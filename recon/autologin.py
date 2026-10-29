"""Auto-logowanie klienta gry (GameCoreTop): login -> wybor kanalu -> wybor postaci -> gra, takze po rozlaczeniu.

USTALONE (skrypty klienta pack/root + Source Client):
- Jedyna instancja networkModule.MainStream (net.SetHandler) trzyma biezace okno fazy: curPhaseWindow =
  LogoWindow / LoginWindow / SelectCharacterWindow / LoadingWindow / GameWindow. Z Pythona gry siegamy po nia
  przez gc (MainStream nie jest nigdzie globalny - to lokalna zmienna RunApp() w prototype.py).
- Kanal = adres serwera gry: serverInfo.REGION_DICT[0][1]["channel"][CH-1] (ip, tcp_port; CH1..CH4 = 29000..29300),
  auth: serverInfo.REGION_AUTH_SERVER_DICT[0][1]. Okno wyboru kanalu robi tylko stream.SetConnectInfo(...) +
  net.SetMarkServer/net.SetServerInfo - robimy to samo, potem LoginWindow.Connect(id, pwd) (jak klik "Zaloguj").
- Bledy logowania (WRONGPWD, ALREADY, ...) przychodza do LoginWindow.OnLoginFailure(error) - podpinamy sie
  na instancji, zeby panel pokazal powod i nie ponawial zlego hasla w kolko.
- Wybor postaci: SelectCharacterWindow.SelectSlot(slot) + StartGame() (jak klik "Start"; po 3 s okno samo
  wola net.DirectEnter). Slot pusty = net.GetAccountCharacterSlotDataInteger(slot, ..._ID) == 0.
- Rozlaczenie: C++ CPythonNetworkStream::OnRemoteDisconnect wola MainStream.SetLoginPhase -> wraca okno
  logowania, wiec ta sama petla loguje ponownie. net.LogOutGame() = "Wyloguj" (przelogowanie na inny CH).

Komendy ida przez DLL (autopot.send_command, Py_AddPendingCall) - dziala juz na ekranie logowania.
Dane konta: haslo w data/autologin.json zaszyfrowane DPAPI (tylko ten uzytkownik Windows go odczyta),
do gry ida jako liczby (bajty) PO naglowku dluzszym niz 60 znakow (DLL loguje 60 pierwszych znakow komendy), nic
z danymi konta nie trafia do printow / logow huba. Wynik komendy gra zapisuje do logs/autologin_<pid>.txt
(tylko faza, kanal, nazwy postaci, kod bledu).
"""
import ast
import base64
import ctypes
import json
import os
import secrets
import subprocess
import threading
import time
from ctypes import wintypes

import autopot
import clients
import settings

RECON_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(RECON_DIR, ".."))
STORE_PATH = os.path.join(ROOT_DIR, "data", "autologin.json")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")

CHANNELS = (1, 2, 3, 4)
SLOTS = (0, 1, 2, 3)               # pid1..pid4 konta (w panelu: postac 1..4)
TICK = 1.5                         # s: co tyle petla sprawdza kazdy klient
STATUS_TIMEOUT = 2.5               # s: na odpowiedz gry (pending-call wykonuje sie w najblizszej klatce)
LOGIN_RETRY = 12.0                 # s: odstep miedzy probami logowania (serwer odpowiada w ~1 s)
ALREADY_RETRY = 20.0               # s: "konto juz zalogowane" - serwer wyrzuca stara sesje, ponow pozniej
START_RETRY = 15.0                 # s: StartGame bez wejscia do gry - ponow
INJECT_DELAY = 4.0                 # s od pojawienia sie okna gry do wstrzykniecia DLL (gra konczy start)
RESTART_COOLDOWN = 30.0            # s: miedzy kolejnymi uruchomieniami klienta dla jednego konta
NEW_PID_TIMEOUT = 40.0             # s: tyle czekamy, az nowy proces gry sie pojawi
FATAL_ERRORS = {"WRONGPWD", "NOID", "BLOCK", "BLKLOGIN", "NOTAVAIL", "BESAMEKEY", "WEBBLK"}   # ponawianie nic nie da

# Naglowek komend z danymi konta: dluzszy niz 60 znakow, ktore DLL wpisuje do logs/dll_debug.txt.
_SAFE_HEAD = "# autologin: komenda panelu (dane konta za naglowkiem, poza logiem DLL)\n"
assert len(_SAFE_HEAD) > 64

# Wspolny poczatek kazdego skryptu w grze: MainStream przez gc, zapis wyniku jako repr(dict) do pliku.
_PRELUDE = r'''
import gc, net, app, __main__
def _al_stream():
    for _o in gc.get_objects():
        if type(_o).__name__ == "MainStream":
            return _o
    return None
def _al_out(_d):
    _f = open(r"__OUT__", "w")
    _f.write(repr(_d))
    _f.close()
_al_ms = _al_stream()
_al_w = _al_ms and (_al_ms.curPhaseWindow or None)
_al_res = {"phase": _al_w and type(_al_w).__name__ or "", "err": getattr(__main__, "_al_err", None),
           "server": "", "connected": 0}
'''

_STATUS = r'''
try:
    _al_res["server"] = net.GetServerInfo()
    _al_res["connected"] = int(net.IsConnect())
    if _al_res["phase"] == "SelectCharacterWindow":
        _al_res["started"] = int(getattr(_al_w, "startIndex", -1) != -1)
        _al_res["loaded"] = int(getattr(_al_w, "isLoad", 0))
        _al_res["slots"] = [net.GetAccountCharacterSlotDataInteger(_i, net.ACCOUNT_CHARACTER_SLOT_ID) and
                            net.GetAccountCharacterSlotDataString(_i, net.ACCOUNT_CHARACTER_SLOT_NAME) or ""
                            for _i in range(4)]
    elif _al_res["phase"] == "GameWindow":
        import player
        _al_res["name"] = player.GetName()
except Exception, _e:
    _al_res["exc"] = type(_e).__name__
_al_out(_al_res)
'''

# __ID__/__PWD__ = bajty cp1250 jako liczby (Python gry nie ma kodeka "hex"), __CH__ = 1..4, __SLOT__ = 0..3
_LOGIN = r'''
try:
    if _al_res["phase"] != "LoginWindow":
        _al_res["skip"] = 1
    else:
        import serverInfo
        _srv_id = 1 if 1 in serverInfo.REGION_DICT[0] else sorted(serverInfo.REGION_DICT[0].keys())[0]
        _srv = serverInfo.REGION_DICT[0][_srv_id]
        _ch = _srv["channel"][__CH__ - 1]
        try:
            _au = serverInfo.REGION_AUTH_SERVER_DICT[0][_srv_id]
            _aip, _aport = _au["ip"], _au["port"]
        except Exception:
            _aip, _aport = 0, 0
        try:
            _mk = serverInfo.MARKADDR_DICT[_srv_id * 10]
            net.SetMarkServer(_mk["ip"], _mk["tcp_port"])
            app.SetGuildMarkPath(_mk["mark"])
            app.SetGuildSymbolPath(_mk["symbol_path"])
        except Exception:
            pass
        net.SetServerInfo(("%s, %s" % (_srv["name"], _ch["name"])).strip())
        try:
            _al_w.serverInfo.SetText("%s, %s" % (_srv["name"], _ch["name"]))
        except Exception:
            pass
        _al_ms.SetConnectInfo(_ch["ip"], _ch["tcp_port"], _aip, _aport)
        _al_ms.SetCharacterSlot(__SLOT__)
        _al_ms.isAutoSelect = 0
        if not getattr(_al_w, "_al_hooked", 0):
            _al_orig = _al_w.OnLoginFailure
            def _al_fail(error, _o=_al_orig):
                __main__._al_err = error
                _o(error)
            _al_w.OnLoginFailure = _al_fail
            _al_w._al_hooked = 1
        __main__._al_err = None
        _al_res["err"] = None
        _al_w.Connect("".join(map(chr, [__ID__])), "".join(map(chr, [__PWD__])))
        _al_res["sent"] = 1
except Exception, _e:
    _al_res["exc"] = type(_e).__name__
_al_out(_al_res)
'''

_SELECT = r'''
try:
    if _al_res["phase"] != "SelectCharacterWindow":
        _al_res["skip"] = 1
    elif not net.GetAccountCharacterSlotDataInteger(__SLOT__, net.ACCOUNT_CHARACTER_SLOT_ID):
        _al_res["empty"] = 1
    else:
        _al_w.SelectSlot(__SLOT__)
        _al_w.StartGame()
        _al_res["sent"] = 1
except Exception, _e:
    _al_res["exc"] = type(_e).__name__
_al_out(_al_res)
'''

_LOGOUT = r'''
try:
    if _al_res["phase"] == "GameWindow":
        net.LogOutGame()
        _al_res["sent"] = 1
    elif _al_res["phase"] == "SelectCharacterWindow":
        _al_ms.SetLoginPhase()
        _al_res["sent"] = 1
except Exception, _e:
    _al_res["exc"] = type(_e).__name__
_al_out(_al_res)
'''


# ---------------------------------------------------------------------------
# DPAPI: haslo w pliku zaszyfrowane kluczem biezacego uzytkownika Windows
# ---------------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.LocalFree.argtypes = [ctypes.c_void_p]


def _dpapi(data, protect):
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):   # 0x1 = UI_FORBIDDEN
        raise OSError("DPAPI: blad %d" % ctypes.get_last_error())
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        _k32.LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))


def protect(text):
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(blob):
    return _dpapi(base64.b64decode(blob), False).decode("utf-8")


# ---------------------------------------------------------------------------
# Komendy do gry
# ---------------------------------------------------------------------------
def _out_path(pid):
    return os.path.join(LOGS_DIR, "autologin_%d.txt" % pid)


def _decode(v):
    """Napisy z gry (Python 2, cp1250) przychodza w repr jako bajty -> str."""
    if isinstance(v, str):
        try:
            return v.encode("latin-1").decode("cp1250")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return v
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


def run_in_game(pid, body, timeout=STATUS_TIMEOUT, head=""):
    """Wykonaj skrypt (prelude + body) w grze i zwroc dict wyniku albo None (brak DLL / brak odpowiedzi)."""
    out = _out_path(pid)
    try:
        os.remove(out)
    except OSError:
        pass
    script = head + _PRELUDE.replace("__OUT__", out) + body
    if not autopot.send_command(pid, script):
        return None
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.1)
        try:
            with open(out, encoding="latin-1") as f:
                res = ast.literal_eval(f.read())
            return {k: _decode(v) for k, v in res.items()}
        except (OSError, ValueError, SyntaxError):
            continue                        # jeszcze nie ma / gra wlasnie pisze
    return None


def game_status(pid):
    return run_in_game(pid, _STATUS)


def game_login(pid, login, password, channel, slot):
    hx = lambda s: ",".join(str(b) for b in s.encode("cp1250", "replace"))
    body = (_LOGIN.replace("__ID__", hx(login)).replace("__PWD__", hx(password))
            .replace("__CH__", str(int(channel))).replace("__SLOT__", str(int(slot))))
    return run_in_game(pid, body, head=_SAFE_HEAD)


def game_select(pid, slot):
    return run_in_game(pid, _SELECT.replace("__SLOT__", str(int(slot))))


def game_logout(pid):
    return run_in_game(pid, _LOGOUT)


def channel_of(server_info):
    """'GameCore.top, |cffFFFFFF|hCH-2' -> 2 (albo None)."""
    s = server_info or ""
    i = s.rfind("CH-")
    return int(s[i + 3]) if i >= 0 and s[i + 3:i + 4].isdigit() else None


# ---------------------------------------------------------------------------
# Profile kont + petla w watku huba
# ---------------------------------------------------------------------------
def public(p):
    """Profil bez hasla - to idzie do przegladarki."""
    d = {k: p.get(k) for k in ("id", "login", "channel", "slot", "enabled", "restart", "radar", "pid")}
    d["nickname"] = p.get("nickname") or p.get("login", "")
    d["has_password"] = bool(p.get("pwd"))
    return d


class Manager:
    """Profile autologowania (data/autologin.json) i petla, ktora prowadzi klienty przez login/kanal/postac.

    radar_start(pid) / radar_running(pid) - funkcje huba (start radaru po wejsciu do gry, jesli profil chce)."""

    def __init__(self, radar_start=None, radar_running=None, log=print):
        self._lock = threading.RLock()
        self._profiles = self._load()
        self._state = {}                    # id profilu -> stan petli (czasy prob, status dla panelu)
        self._radar_start, self._radar_running, self._log = radar_start, radar_running, log
        self._stop = threading.Event()
        self._thread = None

    # --- trwalosc ---
    def _load(self):
        try:
            with open(STORE_PATH, encoding="utf-8") as f:
                return json.load(f).get("profiles", [])
        except (OSError, ValueError):
            return []

    def _save(self):
        os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
        tmp = STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"profiles": self._profiles}, f, indent=1)
        os.replace(tmp, STORE_PATH)

    def _find(self, pid=None, id_=None):
        for p in self._profiles:
            if (pid is not None and p.get("pid") == pid) or (id_ is not None and p["id"] == id_):
                return p
        return None

    # --- API dla huba ---
    def profiles(self):
        with self._lock:
            return [dict(public(p), status=self._st(p)["status"]) for p in self._profiles]

    def for_pid(self, pid):
        with self._lock:
            p = self._find(pid=pid)
            return dict(public(p), status=self._st(p)["status"]) if p else None

    def configure(self, pid, body):
        """Zgodny adapter dla konfiguracji konta na dzialajacym kliencie."""
        if not isinstance(body, dict) or ("login" in body and not isinstance(body["login"], str)):
            raise ValueError("nieprawidlowe dane konta")
        with self._lock:
            p = self._find(pid=pid)
            if p is None:
                try:
                    slot = int(body.get("slot", 0))
                except (ValueError, TypeError):
                    raise ValueError("postac musi byc 1-4") from None
                p = next((q for q in self._profiles if q["login"] == body.get("login", "").strip()
                          and q["slot"] == slot), None)
            return self.save_profile(body, p["id"] if p else None, pid=pid)

    def save_profile(self, body, profile_id=None, pid=None):
        """Zapis bez uruchomionej gry. Puste haslo zachowuje haslo tego samego loginu."""
        if not isinstance(body, dict):
            raise ValueError("nieprawidlowe dane konta")
        with self._lock:
            p = self._find(id_=profile_id) if profile_id else None
            if profile_id and p is None:
                raise LookupError("brak zapisanego konta")
            updated = dict(p or {"id": secrets.token_hex(4), "pid": None,
                                 "enabled": True, "restart": False, "radar": True, "channel": 1, "slot": 0})
            for key in ("login", "nickname", "password"):
                if key in body and not isinstance(body[key], str):
                    raise ValueError("%s: podaj tekst" % key)
            login = body.get("login", updated.get("login", "")).strip()
            if not login:
                raise ValueError("podaj login konta")
            if len(login) > 64 or len(body.get("nickname", "")) > 80:
                raise ValueError("login lub nazwa konta sa za dlugie")
            for key, choices in (("channel", CHANNELS), ("slot", SLOTS)):
                value = body.get(key, updated[key])
                if isinstance(value, bool) or not isinstance(value, (int, str)):
                    raise ValueError("kanal i postac musza byc liczbami 1-4")
                try:
                    value = int(value)
                except ValueError:
                    raise ValueError("kanal i postac musza byc liczbami 1-4") from None
                if value not in choices:
                    raise ValueError("kanal lub postac poza zakresem 1-4")
                updated[key] = value
            for key in ("enabled", "restart", "radar"):
                if key in body:
                    if type(body[key]) is not bool:
                        raise ValueError("%s: wybierz tak lub nie" % key)
                    updated[key] = body[key]
            if body.get("password"):
                updated["pwd"] = protect(body["password"])
            elif updated.get("login") != login or not updated.get("pwd"):
                raise ValueError("podaj haslo konta (takze po zmianie loginu)")
            updated["login"] = login
            updated["nickname"] = body.get("nickname", updated.get("nickname", "")).strip()
            if pid is not None:
                updated["pid"] = pid
            if any(q is not p and q["login"] == login and q["slot"] == updated["slot"] for q in self._profiles):
                raise ValueError("to konto i postac sa juz zapisane - edytuj istniejacy profil")
            old_profiles = [(q, dict(q)) for q in self._profiles]
            if p is None:
                p = updated
                self._profiles.append(p)
            else:
                p.update(updated)
            if pid is not None:
                for q in self._profiles:
                    if q is not p and q.get("pid") == pid:
                        q["pid"] = None
            try:
                self._save()
            except OSError:
                self._profiles = [q for q, _ in old_profiles]
                for q, old in old_profiles:
                    q.clear()
                    q.update(old)
                raise
            self._state.pop(p["id"], None)
            self._log("[autologin] zapisano profil %s (PID %s), CH%d, postac %d"
                      % (p["id"], p.get("pid"), p["channel"], p["slot"] + 1))
            return public(p)

    def set_enabled(self, pid_or_id, enabled):
        with self._lock:
            p = self._find(pid=pid_or_id) or self._find(id_=pid_or_id)
            if p is None:
                raise LookupError("brak profilu autologowania")
            p["enabled"] = bool(enabled)
            self._state.pop(p["id"], None)
            self._save()
            return public(p)

    def remove(self, pid_or_id):
        with self._lock:
            p = self._find(pid=pid_or_id) or self._find(id_=pid_or_id)
            if p is None:
                raise LookupError("brak profilu autologowania")
            self._profiles.remove(p)
            self._state.pop(p["id"], None)
            self._save()

    def relog(self, pid):
        """Wyloguj postac (z gry albo z wyboru postaci) - petla zaloguje ja od nowa na ustawiony kanal."""
        with self._lock:
            p = self._find(pid=pid)
            if p is None or not p.get("enabled"):
                raise LookupError("klient nie ma wlaczonego autologowania")
            self._state.pop(p["id"], None)
        res = game_logout(pid)
        if res is None:
            raise OSError("gra nie odpowiada (DLL?)")
        if not res.get("sent"):
            raise ValueError("postac nie jest w grze ani na wyborze postaci (faza %s)" % (res.get("phase") or "?"))
        return {"ok": True}

    def launch(self, profile_id):
        """Uruchom nowego klienta dla profilu (petla przypnie nowy PID i zaloguje)."""
        with self._lock:
            p = self._find(id_=profile_id)
            if p is None:
                raise LookupError("brak profilu autologowania")
            st = self._st(p)
            if st.get("want_launch") or st["status"]["state"] == "launch":
                return {"ok": True}
            alive = {pid for pid, _ in clients.list_clients(("GameCoreTop.exe",))}
            running = p.get("pid") in alive
            if not running and not os.path.isfile(settings.game_path()):
                raise ValueError("Nie ma pliku gry. Popraw sciezke w Ustawieniach gry.")
            st.clear()                       # ponow takze po WRONGPWD na istniejacym kliencie
            p["enabled"] = True
            if not running:
                st["want_launch"] = True
            self._status(p, "wait" if running else "launch", "ponawiam logowanie" if running else "uruchamiam gre")
            self._save()
        return {"ok": True}

    # --- petla ---
    def start(self):
        self._thread = threading.Thread(target=self._run, name="autologin", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _st(self, p):
        st = self._state.setdefault(p["id"], {})
        st.setdefault("status", {"state": "idle", "text": "czeka"})
        return st

    def _status(self, p, state, text):
        st = self._st(p)
        if st["status"].get("text") != text:
            self._log("[autologin] profil %s (PID %s): %s" % (p["id"], p.get("pid"), text))
        st["status"] = {"state": state, "text": text, "t": time.time()}

    def _run(self):
        while not self._stop.is_set():
            t0 = time.time()
            with self._lock:
                todo = [p for p in self._profiles if p.get("enabled")]
            alive = {pid for pid, _ in clients.list_clients(("GameCoreTop.exe",))}
            for p in todo:
                try:
                    self._step(p, alive)
                except Exception as e:            # jedna awaria nie zatrzymuje petli dla innych klientow
                    self._status(p, "error", "blad petli: %s" % type(e).__name__)
            self._stop.wait(max(0.2, TICK - (time.time() - t0)))

    def _step(self, p, alive):
        st, now, pid = self._st(p), time.time(), p.get("pid")
        if pid not in alive:
            manual = st.get("want_launch", False)
            if manual or (p.get("restart") and (pid is not None or st.get("launched"))):
                if manual or now - st.get("launched", 0) > RESTART_COOLDOWN:
                    st.pop("want_launch", None)
                    self._status(p, "launch", "uruchamiam klienta gry")
                    new = self._launch_client(alive)
                    st["launched"] = time.time()
                    with self._lock:
                        p["pid"] = new
                        self._save()
                    if new:
                        for k in [k for k in st if k not in ("status", "launched")]:
                            del st[k]         # nowy proces = nowe proby logowania
                        self._status(p, "wait", "nowy klient PID %d - czekam na ekran logowania" % new)
                    else:
                        self._status(p, "error", "nowy klient nie wystartowal")
            elif st["status"]["state"] != "error":
                self._status(p, "off", "Gra jest wylaczona. Kliknij Uruchom i zaloguj.")
            return
        if not autopot.dll_loaded(pid):
            st.setdefault("seen", now)
            if now - st["seen"] < INJECT_DELAY:
                self._status(p, "wait", "klient startuje")
                return
            ok, msg = autopot.ensure_injected(pid)
            if not ok:
                self._status(p, "error", "DLL: %s" % msg)
                return
        s = game_status(pid)
        if s is None:
            self._status(p, "wait", "gra nie odpowiada (ekran ladowania?)")
            return
        # kod bledu zostaje w grze do nastepnej proby - liczy sie tylko po naszej probie w tym stanie petli
        # (po zapisaniu nowych danych konta stan jest czysty, wiec stare WRONGPWD nie blokuje)
        phase, err = s.get("phase"), s.get("err") if st.get("login_t") else None
        if phase != st.get("phase"):
            st["phase"], st["phase_t"] = phase, now
        if phase == "LoginWindow":
            if err in FATAL_ERRORS:
                self._status(p, "error", "logowanie odrzucone: %s - popraw dane konta" % err)
                return
            wait = ALREADY_RETRY if err == "ALREADY" else LOGIN_RETRY
            if now - st.get("login_t", 0) < wait:
                if err:
                    self._status(p, "login", "serwer: %s, ponowie" % err)
                elif not st.get("login_fail"):
                    self._status(p, "login", "logowanie na CH%d..." % p["channel"])
                return
            st["login_t"] = now
            r = game_login(pid, p["login"], unprotect(p["pwd"]), p["channel"], p["slot"])
            st["login_fail"] = not (r and r.get("sent"))
            if not st["login_fail"]:
                self._status(p, "login", "logowanie na CH%d..." % p["channel"])
            else:
                self._status(p, "error", "nie udalo sie wyslac logowania (%s)" % ((r or {}).get("exc") or "brak odpowiedzi"))
        elif phase == "SelectCharacterWindow":
            if not s.get("loaded") or now - st["phase_t"] < 1.0:
                self._status(p, "select", "wybor postaci")
                return
            if now - st.get("select_t", 0) < START_RETRY:
                return                    # StartGame wyslany, okno samo wola net.DirectEnter po 3 s
            st["select_t"] = now
            r = game_select(pid, p["slot"])
            if r and r.get("empty"):
                names = [n for n in s.get("slots", []) if n]
                self._status(p, "error", "slot postaci %d jest pusty (na koncie: %s)"
                             % (p["slot"] + 1, ", ".join(names) or "brak postaci"))
            elif r and r.get("sent"):
                name = (s.get("slots") or [""] * 4)[p["slot"]]
                self._status(p, "select", "wchodze postacia %s" % (name or p["slot"] + 1))
            else:
                self._status(p, "error", "wybor postaci nie powiodl sie (%s)" % ((r or {}).get("exc") or "brak odpowiedzi"))
        elif phase == "GameWindow":
            ch = channel_of(s.get("server"))
            self._status(p, "ingame", "w grze%s%s" % (" jako %s" % s["name"] if s.get("name") else "",
                                                        ", CH%d" % ch if ch else ""))
            if p.get("radar") and self._radar_start and not (self._radar_running and self._radar_running(pid)):
                if now - st.get("radar_t", 0) > 90:
                    st["radar_t"] = now
                    try:
                        self._radar_start(pid)
                    except Exception as e:
                        self._log("[autologin] start radaru PID %d: %s" % (pid, e))
        elif phase == "LoadingWindow":
            self._status(p, "loading", "ladowanie mapy")
        elif phase in ("SelectEmpireWindow", "ReselectEmpireWindow", "CreateCharacterWindow"):
            self._status(p, "error", "konto wymaga recznej akcji (%s)" % phase)
        else:
            # LogoWindow albo chwila miedzy oknami (curPhaseWindow=0 w trakcie zmiany fazy)
            self._status(p, "wait", "czekam na ekran logowania" if phase else "zmiana ekranu gry")

    def _launch_client(self, alive):
        """Uruchom GameCoreTop (sciezka z ustawien panelu) i zwroc PID nowego procesu (albo None)."""
        game = settings.game_path()
        if not os.path.isfile(game):
            return None
        # Inny profil mogl juz wystartowac w tej samej iteracji _run; PID nie rosna monotonicznie.
        alive = {pid for pid, _ in clients.list_clients(("GameCoreTop.exe",))}
        subprocess.Popen([game], cwd=os.path.dirname(game))
        t0 = time.time()
        while time.time() - t0 < NEW_PID_TIMEOUT and not self._stop.is_set():
            new = sorted({pid for pid, _ in clients.list_clients(("GameCoreTop.exe",))} - alive)
            if new:
                return new[-1]
            time.sleep(0.5)
        return None
