"""Hub panelu WWW (W3): jedna strona w przegladarce dla wszystkich klientow gry.

Laczy dwie listy: uruchomione klienty gry (recon/clients.py) i dzialajace radary (data/workers/<pid>.json,
zapisywane przez recon/botapi.py). Dla klienta z radarem pobiera jego snapshot i przekazuje komendy.
Tylko biblioteka standardowa; przegladarka odpytuje /api/clients co sekunde (lokalnie to wystarcza).

Uruchomienie:  python recon/hub.py [--port 8765]   ->  http://127.0.0.1:8765
Uruchom z terminala JAKO ADMIN (start radaru = wstrzykniecie DLL, ukrywanie okna gry).

API (JSON):
  GET  /api/clients                         lista klientow + snapshot radaru (jesli dziala)
  GET  /api/clients/<pid>/snapshot          snapshot jednej postaci z encjami i nakladkami (widok postaci)
  GET  /maps/<mapa>.png                     obraz minimapy (tlo radaru w przegladarce)
  POST /api/clients/<pid>/command           {"name","value"} -> radar (jak klik w panelu)
  POST /api/clients/<pid>/radar/start       uruchom radar dla klienta (postac musi byc w swiecie)
  POST /api/clients/<pid>/radar/stop        zamknij radar (czysto, przez jego API)
  POST /api/clients/<pid>/window            {"visible": true/false} pokaz/ukryj okno gry
  POST /api/launch                          uruchom nowego klienta (launch.py --new: login recznie, radar sam)
  GET  /api/autologin                       profile autologowania (bez hasel) + stan
  POST /api/clients/<pid>/autologin         {"login","password","channel","slot","enabled","restart","radar"}
  POST /api/clients/<pid>/autologin/enabled {"enabled": true/false}
  POST /api/clients/<pid>/relog             wyloguj postac - autologowanie wejdzie od nowa (np. na inny CH)
  POST /api/autologin/<id>/launch           uruchom klienta dla profilu (bez klienta) i zaloguj
  POST /api/autologin/<id>/remove           usun profil
Bezpieczenstwo: tylko 127.0.0.1, sprawdzany Host (DNS rebinding) i Origin przy POST (inne strony
z przegladarki nie moga sterowac botem).
"""
import argparse
import ctypes
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import autologin
import botapi
import clients

RECON_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(RECON_DIR, ".."))
WEB_DIR = os.path.join(ROOT_DIR, "web")
FACES_DIR = os.path.join(ROOT_DIR, "data", "faces")
MAPS_DIR = os.path.join(ROOT_DIR, "data", "maps")     # cache minimap budowany przez radar (map_assets.py)
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
DEFAULT_PORT = 8765
WORKER_TIMEOUT = 1.5       # s: snapshot z radaru (wolny radar nie blokuje calej listy)
START_GRACE = 60.0         # s: tyle pokazujemy "radar startuje" po kliknieciu (kalibracja trwa kilka s)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
SYNCHRONIZE, STILL_ACTIVE = 0x00100000, 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_HIDE, SW_SHOW = 0, 5


def process_alive(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def game_windows(pid):
    """Okna najwyzszego poziomu procesu z tytulem - TAKZE ukryte (inaczej nie dalo by sie ich pokazac)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.GetWindowTextLengthW(h) > 0 and not u32.GetWindow(h, 4):   # 4 = GW_OWNER
            found.append(h)
        return True

    u32.EnumWindows(cb, 0)
    return found


def read_worker(pid):
    """Plik API radaru dla klienta albo None. Plik po martwym radarze (np. zabity proces) usuwamy."""
    path = os.path.join(botapi.WORKERS_DIR, "%d.json" % pid)
    try:
        with open(path, encoding="utf-8") as f:
            w = json.load(f)
    except (OSError, ValueError):
        return None
    if not process_alive(w.get("radar_pid", 0)):
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return w


def worker_call(w, path, body=None, timeout=WORKER_TIMEOUT):
    rq = urllib.request.Request("http://127.0.0.1:%d%s" % (w["port"], path),
                                data=json.dumps(body).encode() if body is not None else None,
                                headers={"X-Bot-Token": w["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as f:
            return f.status, json.load(f)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": str(e)}


class Hub:
    def __init__(self):
        self._starting = {}            # pid gry -> czas klikniecia "Uruchom radar"
        self._pool = ThreadPoolExecutor(max_workers=8)
        # auto-logowanie: po wejsciu do gry moze sam uruchomic radar (jesli profil tego chce)
        self.autologin = autologin.Manager(
            radar_start=self.radar_start,
            radar_running=lambda pid: read_worker(pid) is not None
            or time.time() - self._starting.get(pid, 0) < START_GRACE)

    def _entry(self, pid, exe):
        e = {"pid": pid, "exe": exe, "window_visible": any(u32.IsWindowVisible(h) for h in game_windows(pid)),
             "radar": "off", "snapshot": None, "autologin": self.autologin.for_pid(pid)}
        w = read_worker(pid)
        if w is None:
            t = self._starting.get(pid)
            if t and time.time() - t < START_GRACE:
                e["radar"] = "starting"
            return e
        self._starting.pop(pid, None)
        try:
            code, snap = worker_call(w, "/api/snapshot")
            e["radar"], e["snapshot"] = ("on", snap) if code == 200 else ("error", None)
        except (OSError, ValueError):
            e["radar"] = "busy"            # radar zyje, ale nie odpowiedzial (np. kalibracja)
        return e

    def list(self):
        found = clients.list_clients()
        return list(self._pool.map(lambda pe: self._entry(*pe), found))

    def _game(self, pid):
        for p, exe in clients.list_clients():
            if p == pid:
                return exe
        raise LookupError("klient o PID %d nie dziala" % pid)

    def snapshot(self, pid):
        """Pelny snapshot jednej postaci (z encjami i nakladkami) - widok postaci odpytuje go kilka razy/s."""
        w = read_worker(pid)
        if w is None:
            raise LookupError("radar dla PID %d nie dziala" % pid)
        return worker_call(w, "/api/snapshot?entities=1")

    def command(self, pid, body):
        self._game(pid)
        w = read_worker(pid)
        if w is None:
            raise LookupError("radar dla PID %d nie dziala" % pid)
        return worker_call(w, "/api/command", body, timeout=5.0)

    def radar_start(self, pid):
        exe = self._game(pid)
        if read_worker(pid):
            return 200, {"ok": True, "info": "radar juz dziala"}
        os.makedirs(LOGS_DIR, exist_ok=True)
        log = open(os.path.join(LOGS_DIR, "radar_%d.txt" % pid), "w", encoding="utf-8")
        # odlaczony od huba: zamkniecie huba nie zamyka radarow
        subprocess.Popen([sys.executable, "-u", os.path.join(RECON_DIR, "radar_map.py"), "-e", exe, "-p", str(pid)],
                         cwd=RECON_DIR, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        self._starting[pid] = time.time()
        return 200, {"ok": True}

    def radar_stop(self, pid):
        w = read_worker(pid)
        if w is None:
            return 200, {"ok": True, "info": "radar nie dziala"}
        self._starting.pop(pid, None)
        return worker_call(w, "/api/shutdown", {})

    def window(self, pid, visible):
        self._game(pid)
        hs = game_windows(pid)
        if not hs:
            raise LookupError("nie znalazlem okna gry PID %d" % pid)
        for h in hs:
            u32.ShowWindow(h, SW_SHOW if visible else SW_HIDE)
        return 200, {"ok": True, "window_visible": bool(visible)}

    def launch(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        log = open(os.path.join(LOGS_DIR, "launch_%s.txt" % time.strftime("%Y%m%d_%H%M%S")), "w", encoding="utf-8")
        subprocess.Popen([sys.executable, "-u", os.path.join(RECON_DIR, "launch.py"), "--new"],
                         cwd=RECON_DIR, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        return 200, {"ok": True}


def make_handler(hub, port):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    allowed_origins = {"http://" + h for h in allowed_hosts}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, base, rel):
            path = os.path.normpath(os.path.join(base, rel))
            if not path.startswith(base + os.sep) or not os.path.isfile(path):
                self._send(404, {"error": "nie ma pliku"})
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")   # po zmianie app.js przegladarka nie trzyma starego
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._send(403, {"error": "zly Host"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            p = unquote(urlparse(self.path).path)
            if p in ("/", "/index.html"):
                self._file(WEB_DIR, "index.html")
            elif p.startswith("/static/"):
                self._file(WEB_DIR, p[len("/static/"):])
            elif p.startswith("/faces/"):
                self._file(FACES_DIR, p[len("/faces/"):])
            elif p.startswith("/maps/") and p.endswith(".png"):
                self._file(MAPS_DIR, p[len("/maps/"):])
            elif p == "/api/autologin":
                self._send(200, {"profiles": hub.autologin.profiles(), "channels": list(autologin.CHANNELS)})
            elif p == "/api/clients":
                self._send(200, {"clients": hub.list(), "t": time.time()})
            elif p.startswith("/api/clients/") and p.endswith("/snapshot"):
                try:
                    code, res = hub.snapshot(int(p.split("/")[3]))
                except (LookupError, ValueError) as e:
                    code, res = 404, {"error": str(e)}
                except OSError as e:
                    code, res = 503, {"error": "radar nie odpowiada: %s" % e}
                self._send(code, res)
            else:
                self._send(404, {"error": "nie ma %s" % p})

        def do_POST(self):
            if not self._host_ok():
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self._send(403, {"error": "obca strona (Origin %s)" % origin})
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                if parts == ["api", "launch"]:
                    code, res = hub.launch()
                elif len(parts) == 4 and parts[:2] == ["api", "autologin"]:
                    if parts[3] == "launch":
                        code, res = 200, hub.autologin.launch(parts[2])
                    elif parts[3] == "remove":
                        hub.autologin.remove(parts[2])
                        code, res = 200, {"ok": True}
                    else:
                        code, res = 404, {"error": "nieznana akcja %s" % parts[3]}
                elif len(parts) >= 4 and parts[:2] == ["api", "clients"]:
                    pid, action = int(parts[2]), "/".join(parts[3:])
                    if action == "command":
                        code, res = hub.command(pid, body)
                    elif action == "radar/start":
                        code, res = hub.radar_start(pid)
                    elif action == "radar/stop":
                        code, res = hub.radar_stop(pid)
                    elif action == "window":
                        code, res = hub.window(pid, bool(body.get("visible")))
                    elif action == "autologin":
                        hub._game(pid)
                        code, res = 200, {"ok": True, "profile": hub.autologin.configure(pid, body)}
                    elif action == "autologin/enabled":
                        code, res = 200, {"ok": True, "profile": hub.autologin.set_enabled(pid, body.get("enabled"))}
                    elif action == "relog":
                        code, res = 200, hub.autologin.relog(pid)
                    else:
                        code, res = 404, {"error": "nieznana akcja %s" % action}
                else:
                    code, res = 404, {"error": "nie ma %s" % self.path}
                self._send(code, res)
            except LookupError as e:
                self._send(404, {"ok": False, "error": str(e)})
            except (ValueError, OSError) as e:
                self._send(400, {"ok": False, "error": str(e)})
    return H


def main():
    ap = argparse.ArgumentParser(description="Panel WWW dla wszystkich klientow gry.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    hub = Hub()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub, args.port))
    srv.daemon_threads = True
    hub.autologin.start()
    print("[hub] panel: http://127.0.0.1:%d  (Ctrl+C konczy; radary dzialaja dalej)" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
