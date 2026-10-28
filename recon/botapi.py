"""Cienka warstwa API nad radarem (W2): odczyt stanu bota i sterowanie nim BEZ okna tkinter.

Po co: panel w przegladarce (hub, W3) musi czytac stan i przelaczac funkcje kazdego klienta. Pelny podzial
radar_map.py na logike/widok jest odlozony (rownolegle sesje edytuja ten plik), wiec tu tylko adapter:
- snapshot()  - czyta zwykle pola RadarMap (stan bota juz w nich jest; zmienne tk tylko je odbijaja),
- command()   - ustawia zmienna tk i wola ISTNIEJACY handler _on_* w watku GUI = dokladnie jak klik w panelu
                (handlery maja efekty uboczne, np. farm ustawia srodek okregu - nie dublujemy ich tu).

Serwer HTTP (stdlib) na 127.0.0.1, losowy port, wymagany naglowek X-Bot-Token. Port+token w pliku
data/workers/<pid>.json (hub znajduje po nim workery; plik znika przy wyjsciu radaru).
  GET  /api/snapshot[?entities=1]       stan (+ encje wokol postaci)
  GET  /api/options                     opcje: wartosc + typ + zakres
  POST /api/command {"name","value"}    zmiana opcji (jak w panelu); "buff" -> value {"vnum","on"}
  POST /api/shutdown                    zamknij radar (jak krzyzyk okna)
Recznie:  curl -H "X-Bot-Token: <token>" http://127.0.0.1:<port>/api/snapshot
"""
import atexit
import json
import math
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import identity

WORKERS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "workers"))
ENTITY_R = 6000.0          # jedn.: encje w tym promieniu trafiaja do snapshotu (~zasieg radaru w trybie follow)

# nazwa API -> (typ, pole stanu RadarMap, zmienna tk, handler, (min, max) dla liczb)
# Handler z argumentem (suwaki tk.Scale) dostaje wartosc; bez argumentu czyta zmienna tk sam.
OPTIONS = {
    "autopot":       (bool,  "autopot_on",    "autopot_var",       "_on_autopot_check",  None),
    "pot_low":       (int,   "pot_low",       "low_var",           "_on_low",            (5, 95)),
    "pot_high":      (int,   "pot_high",      "high_var",          "_on_high",           (5, 99)),
    "pot_cd":        (float, "pot_cd",        "cd_var",            "_on_cd",             (0.1, 1.0)),
    "autoloot":      (bool,  "autoloot_on",   "autoloot_var",      "_on_autoloot_check", None),
    "loot_interval": (float, "loot_interval", "loot_var",          "_on_loot_cd",        (0.2, 2.0)),
    "autowalk":      (bool,  "autowalk_on",   "autowalk_var",      "_on_autowalk_check", None),
    "walk_fight":    (bool,  "walk_fight",    "walkfight_var",     "_on_walkfight",      None),
    "farm":          (bool,  "farm_on",       "farm_var",          "_on_farm_check",     None),
    "farm_radius":   (int,   "farm_radius",   "farmr_var",         "_on_farmr",          (500, 6000)),
    "farm_metin":    (bool,  "farm_metin",    "farm_metin_var",    "_on_farm_metin",     None),
    "farm_boss":     (bool,  "farm_boss",     "farm_boss_var",     "_on_farm_boss",      None),
    "prep_dismount": (bool,  "prep_dismount", "prep_dismount_var", "_on_prep_opts",      None),
    "prep_buffs":    (bool,  "prep_buffs",    "prep_buffs_var",    "_on_prep_opts",      None),
    "prep_remount":  (bool,  "prep_remount",  "prep_remount_var",  "_on_prep_opts",      None),
    "speed":         (bool,  "speed_on",      "speed_var",         "_on_speed_check",    None),
    "move_mult":     (float, "move_mult",     "movx_var",          "_on_movx",           (1.0, 3.0)),
    "atk_mult":      (float, "atk_mult",      "atkx_var",          "_on_atkx",           (1.0, 3.0)),
    "autobuff":      (bool,  "autobuff.on",   "autobuff_var",      "_on_autobuff_check", None),
}
_SLIDER_HANDLERS = {"_on_low", "_on_high", "_on_cd", "_on_loot_cd", "_on_farmr", "_on_movx", "_on_atkx"}


def _get(obj, dotted):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


class BotApi:
    def __init__(self, radar):
        self.r = radar

    # ---------- odczyt (dowolny watek; tylko pola i odczyty pamieci) ----------
    def options(self):
        """{nazwa: {value, type, min?, max?}} - tylko opcje istniejace w tej wersji radaru."""
        out = {}
        for name, (typ, attr, var, handler, rng) in OPTIONS.items():
            if not (hasattr(self.r, var) and hasattr(self.r, handler)):
                continue
            try:
                v = typ(_get(self.r, attr))
            except Exception:
                continue
            d = {"value": v, "type": typ.__name__}
            if rng:
                d["min"], d["max"] = rng
            out[name] = d
        if hasattr(self.r, "pot_size_vars") and hasattr(self.r, "pot_sizes"):   # rozmiary mikstur (lista)
            out["pot_sizes"] = {"value": sorted(self.r.pot_sizes), "type": "list",
                                "choices": list(self.r.pot_size_vars)}
        return out

    def buffs(self):
        ab = getattr(self.r, "autobuff", None)
        if ab is None:
            return []
        names = getattr(__import__("autobuff"), "BUFFS", {})
        return [{"vnum": v, "name": names.get(v, (str(v),))[0], "slot": s, "on": v in ab.enabled}
                for v, s in sorted(ab.known.items(), key=lambda kv: kv[1])]

    def snapshot(self, entities=False):
        r = self.r
        hx, hy = r._hero_world()
        idn = dict(r.ident)
        idn["face"] = os.path.basename(identity.face_path(idn.get("cls"), idn.get("sex")) or "") or None
        snap = {
            "t": time.time(),
            "ident": idn,
            "pos": {"x": hx, "y": hy} if hx is not None else None,
            "map": getattr(getattr(r, "map", None), "name", None),
            "hp": {"cur": r._hp, "max": r._mhp, "pct": round(r._pct, 1), "valid": r._pts_valid},
            "sp": {"cur": r._point(7), "max": r._point(8)},
            "options": self.options(),
            "buffs": self.buffs(),
            "status": {
                "dll": str(getattr(r, "dll_status", "")),
                "pot_count": getattr(r, "pot_count", 0),
                "loot_count": getattr(r, "loot_count", 0),
                "combat": getattr(r, "_combat_status", ""),
                "buff": getattr(getattr(r, "autobuff", None), "status", ""),
                "waypoints": len(getattr(r, "path", []) or []),
            },
        }
        if entities and hx is not None:
            snap["entities"] = self.entities(hx, hy)
        return snap

    def entities(self, hx, hy, radius=ENTITY_R):
        """[{x, y, kind, vnum, vid}] aktorow (bez wlasnej postaci) + metiny z efektow, w promieniu."""
        r, out = self.r, []
        for x, y, vnum, is_pc, _o, vid in r._iter_actors(hx, hy):
            if math.hypot(x - hx, y - hy) <= radius:
                out.append({"x": round(x), "y": round(y), "kind": r._classify(vnum, is_pc),
                            "vnum": vnum, "vid": vid})
        for mx, my, _name in list(r.metins):
            if math.hypot(mx - hx, my - hy) <= radius:
                out.append({"x": round(mx), "y": round(my), "kind": "metin", "vnum": 0, "vid": 0})
        return out

    # ---------- zapis (wykonywany w watku GUI, wolajacy czeka na wynik) ----------
    def command(self, name, value, timeout=3.0):
        """Zmien opcje tak, jak zrobilby to klik w panelu. Zwraca nowe options(); ValueError gdy zle dane."""
        if name == "buff":
            fn = self._buff_fn(value)
        elif name == "pot_sizes":
            fn = self._pot_sizes_fn(value)
        elif name in OPTIONS:
            fn = self._option_fn(name, value)
        else:
            raise ValueError("nieznana opcja: %s" % name)
        done, err = threading.Event(), []

        def run():
            try:
                fn()
            except Exception as e:      # blad w handlerze - zwroc go wolajacemu, nie zabijaj petli tk
                err.append(e)
            finally:
                done.set()

        self.r.root.after(0, run)
        if not done.wait(timeout):
            raise TimeoutError("watek GUI nie odpowiedzial w %.1f s" % timeout)
        if err:
            raise err[0]
        return self.options()

    def _option_fn(self, name, value):
        typ, _attr, var, handler, rng = OPTIONS[name]
        if not (hasattr(self.r, var) and hasattr(self.r, handler)):
            raise ValueError("opcja %s niedostepna w tej wersji radaru" % name)
        if typ is bool:
            if not isinstance(value, bool):
                raise ValueError("%s: oczekuje true/false" % name)
            v = value
        else:
            try:
                v = typ(value)
            except (TypeError, ValueError):
                raise ValueError("%s: oczekuje liczby" % name)
            if rng:
                v = min(max(v, rng[0]), rng[1])
        tkvar, h = getattr(self.r, var), getattr(self.r, handler)

        def fn():
            tkvar.set(v)
            h(v) if handler in _SLIDER_HANDLERS else h()
        return fn

    def _buff_fn(self, value):
        try:
            vnum, on = int(value["vnum"]), bool(value["on"])
        except (TypeError, KeyError, ValueError):
            raise ValueError('buff: oczekuje {"vnum": N, "on": true/false}')
        if vnum not in self.r.autobuff.known:
            raise ValueError("buff %d nie jest wyuczony" % vnum)

        def fn():
            var = self.r._buff_vars.get(vnum)      # checkbox moze jeszcze nie istniec (budowany w tick)
            if var is not None:
                var.set(on)
                self.r._on_buff_toggle(vnum)
            elif on:
                self.r.autobuff.enabled.add(vnum)
            else:
                self.r.autobuff.enabled.discard(vnum)
        return fn

    def _pot_sizes_fn(self, value):
        vars_ = getattr(self.r, "pot_size_vars", None)
        if vars_ is None:
            raise ValueError("pot_sizes niedostepne w tej wersji radaru")
        if not isinstance(value, list) or any(s not in vars_ for s in value):
            raise ValueError("pot_sizes: oczekuje listy z %s" % list(vars_))

        def fn():
            for s, var in vars_.items():
                var.set(s in value)
            self.r._on_pot_sizes()
        return fn


# ---------- serwer HTTP ----------
def _handler_cls(api, token):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # bez spamu w konsoli radaru
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            # token w niestandardowym naglowku: strona z przegladarki nie wysle go bez CORS (ktorego nie dajemy)
            if secrets.compare_digest(self.headers.get("X-Bot-Token", ""), token):
                return True
            self._send(401, {"error": "brak/zly X-Bot-Token"})
            return False

        def do_GET(self):
            if not self._authorized():
                return
            u = urlparse(self.path)
            try:
                if u.path == "/api/snapshot":
                    ent = parse_qs(u.query).get("entities", ["0"])[0] == "1"
                    self._send(200, api.snapshot(entities=ent))
                elif u.path == "/api/options":
                    self._send(200, api.options())
                else:
                    self._send(404, {"error": "nie ma %s" % u.path})
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def do_POST(self):
            if not self._authorized():
                return
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                # czyste zamkniecie jak krzyzyk okna: zapis geometrii, potem atexit usuwa plik workera
                self._send(200, {"ok": True})
                api.r.root.after(0, api.r._on_close)
                return
            if path != "/api/command":
                self._send(404, {"error": "nie ma %s" % self.path})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(n) or b"{}")
                self._send(200, {"ok": True, "options": api.command(req.get("name"), req.get("value"))})
            except (ValueError, TimeoutError) as e:
                self._send(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._send(500, {"ok": False, "error": repr(e)})
    return H


def start(radar, port=0):
    """Uruchom API radaru w watku w tle; zapisz data/workers/<pid>.json. Zwraca BotApi (albo None przy bledzie)."""
    api = BotApi(radar)
    token = secrets.token_hex(16)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), _handler_cls(api, token))
    except OSError as e:
        print("[api] nie wystartowalo: %s" % e)
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pid = radar.pm.process_id
    os.makedirs(WORKERS_DIR, exist_ok=True)
    path = os.path.join(WORKERS_DIR, "%d.json" % pid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "exe": radar.proc_name, "radar_pid": os.getpid(),
                   "port": srv.server_address[1], "token": token, "started": time.time()}, f)

    def cleanup():
        try:
            with open(path, encoding="utf-8") as f:
                mine = json.load(f).get("radar_pid") == os.getpid()   # nie kasuj pliku nowszego radaru
            if mine:
                os.remove(path)      # po zamknieciu pliku - Windows nie usuwa otwartego
        except Exception:
            pass
    atexit.register(cleanup)
    api.port, api.token = srv.server_address[1], token
    print("[api] http://127.0.0.1:%d (token w %s)" % (api.port, path))
    return api
