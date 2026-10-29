"""Ochrona: wykrywanie innych graczy i GM w poblizu postaci, wstrzymanie bota i sygnal dzwiekowy.

Wykrywanie (co STEP_EVERY s, w petli radaru _autopot_loop):
- gracz = aktor z modelem /pc/ (RadarMap._iter_actors, is_pc), nazwa z CInstanceBase (_mob_name);
- GM = gracz z logo GM nad glowa: serwer daje GM-owi AFF_YMIR (char.cpp, GM level > LOW_WIZARD), klient
  rysuje wtedy efekt EFFECT_AFFECT+0 = localeInfo.FN_GM_MARK ("<locale>/effect/gm.mse") na kosci Bip01.
  Radar zbiera pozycje takich efektow w rescan (gm_marks); efekt blizej niz GM_MARK_MATCH od gracza = GM.
  Zapas: nazwa z prefiksem w nawiasie ("[GM]Nick", "[SA]...") - zwykly nick nie moze miec '['.
  GM niewidzialny (AFFECT_INVISIBILITY / tryb obserwatora) NIE ma logo ani aktora - tego nie wykryjemy.
- swoje postacie (beacony data/beacons z innych radarow, swiezsze niz OWN_FRESH) i nicki z
  data/guard_ignore.txt sa pomijane - inaczej boty zatrzymywalyby sie na siebie nawzajem.
GM liczy sie w calym zasiegu widzenia klienta; zwykly gracz tylko w promieniu `radius`.

Wstrzymanie (hold), gdy `stop` i jest zagrozenie (react: "all" = gracz lub GM, "gm" = tylko GM):
radar nie wybiera nowych celow, nie zbiera, nie uzywa ataku pakietem/peleryny/buffow/speedhacka, nie
podaza za liderem i nie idzie do kolejnego punktu trasy. Zostaje: bieg do biezacego celu (podejdz tutaj,
biezacy punkt trasy, zablokowany cel walki), dobicie zablokowanego celu i mobow, ktore bija postac
(stanie w miejscu pod atakiem wyglada gorzej) oraz auto-mikstura. Koniec: po `resume` s bez zagrozenia
(0 = tylko recznie: release()).

Wiadomosci (tylko GameCoreTop - DLL wykonuje Pythona gry): radar podmienia w kliencie metody
game.GameWindow.OnRecvWhisper / OnRecvWhisperSystemMessage / BINARY_SetTipMessage / BINARY_SetBigMessage
na wersje, ktore dopisuja linie do logs/msg_<pid>.txt i wolaja oryginal (MSG_HOOK). Radar czyta plik od konca:
szept = dzwiek + dzwonek w panelu (unread); szept typu GM (WHISPER_TYPE_GM = 5) = jak wykrycie GM (hold).
Zwykly czat lokalny/krzyk klient obsluguje w C++ (CPythonChat::AppendChat) - Python go nie widzi.
"""
import json
import math
import os
import re
import threading
import time

try:
    import winsound
except ImportError:          # testy poza Windows
    winsound = None

import autopot
import follow

STEP_EVERY = 0.5             # s: skan graczy (odczyt aktorow /pc/ + nazwy; tani)
GM_MARK_MATCH = 250.0        # jedn.: logo GM (efekt na Bip01) tak blisko gracza = to on jest GM
GM_NAME_RE = re.compile(r"^\s*\[[^\]]{1,10}\]")
GM_MARK_SUFFIX = "/effect/gm.mse"
OWN_FRESH = 30.0             # s: beacon innego radaru swiezszy niz to = nasza postac
OWN_EVERY = 5.0              # s: odswiezanie listy wlasnych postaci
IGNORE_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data",
                                            "guard_ignore.txt"))
SOUND_REPEAT_GM = 10.0       # s: przy GM w zasiegu dzwiek powtarzany co tyle
SOUND_REPEAT_PLAYER = 60.0   # s: przy graczu (bez GM) - rzadziej
LOG_EVERY = 5.0              # s: czytelna linia z lista zagrozen (JSONL leci przy kazdej zmianie)
REACT_CHOICES = ("all", "gm")
DEFAULTS = dict(on=True, react="all", stop=True, sound=True, radius=5000, resume=60, msg=True, msg_sound=True)
RADIUS_RANGE = (500, 10000)
RESUME_RANGE = (0, 600)
# (Hz, ms) - GM: wysoki, natarczywy; gracz: dwa krotkie
SOUND_GM = ((1500, 220), (900, 220), (1500, 220), (900, 220), (1500, 400))
SOUND_PLAYER = ((880, 150), (0, 80), (880, 150))
SOUND_MSG = ((1200, 90), (0, 60), (1600, 140))

LOGS_DIR = os.path.dirname(autopot.INV_DUMP_PATH)
MSG_INSTALL_EVERY = 60.0     # s: ponowne (idempotentne) podpiecie - na wypadek relogu/przeladowania modulu
MSG_CONFIRM_SEC = 5.0        # s: tyle czekamy na marker po wyslaniu podpiecia
MSG_MAX_BYTES = 1 << 20      # plik wiadomosci wiekszy -> przycinamy (radar przeczytal juz wszystko)
WHISPER_TYPE_GM = 5          # PythonChat.h
ALERTS_MAX = 30
# Python 2.7 klienta (exec przez DLL w __main__). Wrapper dopisuje "rodzaj<TAB>czas<TAB>argumenty..." i wola oryginal.
MSG_HOOK = r'''import sys, time
def _m2lor_msg_install(path, mark):
	g = sys.modules.get("game")
	if g is None or not hasattr(g, "GameWindow"):
		open(mark, "w").write("nogame %.0f" % time.time())
		return
	def wrap(fn, kind):
		def h(self, *a):
			try:
				f = open(path, "ab")
				f.write("%s\t%.3f\t%s\n" % (kind, time.time(), "\t".join([str(x).replace("\t", " ").replace("\n", " ") for x in a])))
				f.close()
			except Exception:
				pass
			return fn(self, *a)
		h._m2lor = 1
		return h
	n = 0
	for name, kind in (("OnRecvWhisper", "whisper"), ("OnRecvWhisperSystemMessage", "wsys"), ("BINARY_SetTipMessage", "notice"), ("BINARY_SetBigMessage", "big")):
		fn = getattr(g.GameWindow, name, None)
		if fn is not None and not getattr(fn, "_m2lor", 0):
			setattr(g.GameWindow, name, wrap(fn, kind))
			n += 1
	open(mark, "w").write("ok %d %.0f" % (n, time.time()))
_m2lor_msg_install(r"__PATH__", r"__MARK__")
'''


def load_ignore(path=IGNORE_PATH):
    """Nicki z pliku (jeden na linie, '#' = komentarz) -> zbior malymi literami."""
    out = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                nm = line.split("#", 1)[0].strip()
                if nm:
                    out.add(nm.lower())
    except OSError:
        pass
    return out


def own_nicks(now=None):
    """Nicki postaci z dzialajacymi radarami (beacony follow.Beacon) -> zbior malymi literami."""
    now = now or time.time()
    out = set()
    try:
        names = os.listdir(follow.BEACON_DIR)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(follow.BEACON_DIR, fn), encoding="utf-8") as f:
                b = json.load(f)
        except (OSError, ValueError):
            continue
        if b.get("nick") and now - float(b.get("t", 0)) <= OWN_FRESH:
            out.add(str(b["nick"]).lower())
    return out


def play(pattern):
    """Odtworz sekwencje (Hz, ms) w watku w tle (winsound.Beep blokuje). Hz=0 = pauza."""
    def run():
        try:
            for hz, ms in pattern:
                if hz and winsound is not None:
                    winsound.Beep(int(hz), int(ms))
                else:
                    time.sleep(ms / 1000.0)
        except Exception:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
    threading.Thread(target=run, daemon=True).start()


class Guard:
    """Stan i logika ochrony jednej postaci. step() z petli radaru; hold czytaja kroki bota."""

    def __init__(self, radar, state=None, save_fn=None):
        self.r = radar
        st = dict(DEFAULTS)
        st.update({k: v for k, v in (state or {}).items() if k in DEFAULTS})
        self.on = bool(st["on"])
        self.react = st["react"] if st["react"] in REACT_CHOICES else DEFAULTS["react"]
        self.stop = bool(st["stop"])
        self.sound = bool(st["sound"])
        self.radius = float(min(max(float(st["radius"]), RADIUS_RANGE[0]), RADIUS_RANGE[1]))
        self.resume = int(min(max(int(st["resume"]), RESUME_RANGE[0]), RESUME_RANGE[1]))
        self.msg = bool(st["msg"])
        self.msg_sound = bool(st["msg_sound"])
        self._save = save_fn
        self.hold = False            # bot wstrzymany (czytaja kroki radaru i Follower)
        self.hold_since = 0.0
        self.hold_why = ""
        self.threats = []            # biezace zagrozenia: dict(name, gm, via, d, x, y, vid, vnum)
        self.status = ""
        self.count = 0               # ile razy cos wykryto (nowe wejscie w zasieg)
        self._seen = {}              # key -> dict(t0, name, gm, dmin)
        self._own, self._own_t = set(), 0.0
        self._ign, self._ign_mtime = set(), -1.0   # -1: pierwszy odczyt zawsze trafia do dziennika
        self._logged_own = set()     # swoje postacie zalogowane juz raz (bez spamu)
        self._last_step = 0.0
        self._last_threat = 0.0      # kiedy ostatnio bylo zagrozenie wywolujace reakcje
        self._sound_t = 0.0
        self._log_t = 0.0
        self._paused_logged = set()  # kroki, ktorych wstrzymanie juz zalogowano w tym hold
        # wiadomosci + dzwonek w panelu
        self.alerts = []             # ostatnie zdarzenia do panelu: dict(t, kind, name, text)
        self.unread = 0              # nieprzeczytane (szepty + wykryci GM) - dzwonek; zeruje ack()
        self.msg_status = ""
        self.msg_count = 0
        self._msg_sent = 0.0         # kiedy ostatnio wyslano podpiecie
        self._msg_ok = False         # marker potwierdzil podpiecie w tej sesji radaru
        self._msg_off = None         # ile bajtow pliku wiadomosci juz przeczytano (None = jeszcze nie)

    # ---------- ustawienia ----------
    def settings(self):
        return dict(on=self.on, react=self.react, stop=self.stop, sound=self.sound,
                    radius=int(self.radius), resume=self.resume, msg=self.msg, msg_sound=self.msg_sound)

    def apply(self, **kw):
        """Zmiana opcji z panelu; zapis do ui_state (wspolne dla wszystkich postaci)."""
        before = self.settings()
        for k, v in kw.items():
            if k == "on":
                self.on = bool(v)
            elif k == "react" and v in REACT_CHOICES:
                self.react = v
            elif k == "stop":
                self.stop = bool(v)
            elif k == "sound":
                self.sound = bool(v)
            elif k == "radius":
                self.radius = float(min(max(float(v), RADIUS_RANGE[0]), RADIUS_RANGE[1]))
            elif k == "resume":
                self.resume = int(min(max(int(float(v)), RESUME_RANGE[0]), RESUME_RANGE[1]))
            elif k == "msg":
                self.msg = bool(v)
            elif k == "msg_sound":
                self.msg_sound = bool(v)
        after = self.settings()
        if after != before:
            self.r._dbg("ochrona: opcje %s -> %s", before, after)
            if self._save:
                self._save(after)
        if self.hold and (not self.on or not self.stop):
            self.release("opcja %s wylaczona" % ("ochrony" if not self.on else "zatrzymania"))
        elif self.hold and self.react == "gm" and not any(t["gm"] for t in self.threats):
            self.release("reakcja tylko na GM, a w poblizu sami gracze")

    # ---------- wykrywanie ----------
    def _refresh_lists(self, now):
        if now - self._own_t >= OWN_EVERY:
            self._own_t = now
            own = own_nicks(now)
            nick = (self.r.ident or {}).get("nick")
            if nick:
                own.add(nick.lower())
            if own != self._own:
                self.r._dbg("ochrona: swoje postacie (pomijane): %s", sorted(own) or "-")
            self._own = own
        try:
            mt = os.path.getmtime(IGNORE_PATH)
        except OSError:
            mt = None
        if mt != self._ign_mtime:
            self._ign_mtime = mt
            self._ign = load_ignore()
            self.r._dbg("ochrona: lista ignorowanych (%s): %s", IGNORE_PATH, sorted(self._ign) or "-")

    def scan(self, hx, hy):
        """Lista zagrozen wokol (hx, hy) - bez efektow ubocznych poza dziennikiem 'swoich'."""
        r = self.r
        marks = list(getattr(r, "gm_marks", []) or [])
        out = {}
        for x, y, vnum, is_pc, o, vid in r._iter_actors(hx, hy):
            if not is_pc:
                continue
            d = math.hypot(x - hx, y - hy)
            name = r._mob_name(o) or ""
            low = name.lower()
            if low and (low in self._own or low in self._ign):
                if low not in self._logged_own:
                    self._logged_own.add(low)
                    r._dbg("ochrona: pomijam %s (%s) d=%.0f", name,
                           "swoja postac" if low in self._own else "guard_ignore.txt", d)
                continue
            mark_d = min((math.hypot(mx - x, my - y) for mx, my in marks), default=None)
            via = None
            if mark_d is not None and mark_d <= GM_MARK_MATCH:
                via = "logo GM (%.0f)" % mark_d
            elif GM_NAME_RE.match(name):
                via = "prefiks nicku"
            gm = via is not None
            if not gm and d > self.radius:
                continue
            key = low or "vid%d" % vid
            out[key] = dict(name=name or "?", gm=gm, via=via or "gracz", d=round(d), x=round(x), y=round(y),
                            vid=vid, vnum=vnum)
        return out

    def step(self, now=None):
        now = now or time.time()
        if now - self._last_step < STEP_EVERY:
            return
        self._last_step = now
        r = self.r
        try:
            self._msg_step(now)
        except Exception as e:
            r._dbg("ochrona: blad wiadomosci: %r", e)
        if not self.on:
            if self.threats or self._seen:
                self.threats, self._seen = [], {}
            self.status = "wylaczona"
            return
        hx, hy = r._hero_world()
        if hx is None:
            return
        self._refresh_lists(now)
        cur = self.scan(hx, hy)
        tr = r._tr()
        # wejscia / wyjscia z zasiegu
        new_react = []
        for key, t in cur.items():
            s = self._seen.get(key)
            if s is None:
                self._seen[key] = dict(t0=now, name=t["name"], gm=t["gm"], dmin=t["d"])
                self.count += 1
                r._dbg("ochrona: WYKRYTO %s %s d=%.0f @(%.0f,%.0f) vid=%d vnum=%d via=%s (promien %.0f, "
                       "logo GM na mapie: %d)", "GM" if t["gm"] else "gracz", t["name"], t["d"], t["x"], t["y"],
                       t["vid"], t["vnum"], t["via"], self.radius, len(getattr(r, "gm_marks", []) or []))
                if tr is not None:
                    tr.rec("guard", ev="seen", hero=[round(hx), round(hy)], **t)
                if t["gm"] or self.react == "all":
                    new_react.append(t)
                if t["gm"]:
                    self._alert("gm", t["name"], "GM w poblizu (d=%d, %s)" % (t["d"], t["via"]), now)
            else:
                s["dmin"] = min(s["dmin"], t["d"])
                if t["gm"] and not s["gm"]:          # logo GM pojawilo sie pozniej (np. wyszedl z ukrycia)
                    s["gm"] = True
                    r._dbg("ochrona: %s to GM (%s) d=%.0f", t["name"], t["via"], t["d"])
                    if tr is not None:
                        tr.rec("guard", ev="gm", **t)
                    new_react.append(t)
                    self._alert("gm", t["name"], "GM w poblizu (d=%d, %s)" % (t["d"], t["via"]), now)
        for key in [k for k in self._seen if k not in cur]:
            s = self._seen.pop(key)
            r._dbg("ochrona: odszedl %s %s (w zasiegu %.0f s, najblizej %.0f)", "GM" if s["gm"] else "gracz",
                   s["name"], now - s["t0"], s["dmin"])
            if tr is not None:
                tr.rec("guard", ev="left", name=s["name"], gm=s["gm"], sec=round(now - s["t0"], 1), dmin=s["dmin"])
        self.threats = sorted(cur.values(), key=lambda t: (not t["gm"], t["d"]))
        react = [t for t in self.threats if t["gm"] or self.react == "all"]
        if self.threats and now - self._log_t >= LOG_EVERY:
            self._log_t = now
            r._dbg("ochrona: w poblizu %s; hold=%s", ", ".join(
                "%s%s d=%d" % ("[GM] " if t["gm"] else "", t["name"], t["d"]) for t in self.threats), self.hold)

        # dzwiek: nowe zagrozenie od razu, potem powtarzanie (GM czesciej)
        if self.sound and react:
            gm = any(t["gm"] for t in react)
            every = SOUND_REPEAT_GM if gm else SOUND_REPEAT_PLAYER
            if new_react or now - self._sound_t >= every:
                self._sound_t = now
                play(SOUND_GM if gm else SOUND_PLAYER)
                r._dbg("ochrona: sygnal dzwiekowy (%s, nowych %d)", "GM" if gm else "gracz", len(new_react))
                if tr is not None:
                    tr.rec("guard", ev="sound", gm=gm, new=len(new_react))

        # wstrzymanie / wznowienie
        if react:
            self._last_threat = now
            if self.stop and not self.hold:
                t = react[0]
                self._start_hold("%s %s d=%d" % ("GM" if t["gm"] else "gracz", t["name"], t["d"]), now)
        elif self.hold and self.resume > 0 and now - self._last_threat >= self.resume:
            self.release("nikogo w poblizu od %d s" % self.resume)

        self.status = self._status_text(now, react)

    def _status_text(self, now, react):
        if self.hold:
            if react:
                return "STOP: %s" % self.hold_why
            if self.resume > 0:
                return "STOP: wznowienie za %.0f s" % max(0.0, self.resume - (now - self._last_threat))
            return "STOP: wznow recznie"
        if self.threats:
            t = self.threats[0]
            return "%s %s d=%d (%s)" % ("GM" if t["gm"] else "gracz", t["name"], t["d"],
                                       "bez reakcji - tylko GM" if not react else "zatrzymanie wylaczone")
        return "czysto"

    # ---------- wstrzymanie ----------
    def _start_hold(self, why, now):
        r = self.r
        self.hold, self.hold_since, self.hold_why = True, now, why
        self._paused_logged = set()
        fol = getattr(getattr(r, "api", None), "follower", None)
        paused = [n for n, on in (("loot", getattr(r, "autoloot_on", False) or bool(getattr(r, "ground_cfg", None))),
                                  ("atak pakietem", getattr(r, "area_atk_on", False)),
                                  ("peleryna", getattr(r, "cape_on", False)),
                                  ("buff", getattr(getattr(r, "autobuff", None), "on", False)),
                                  ("speedhack", getattr(r, "speed_on", False)),
                                  ("farma (nowe cele)", getattr(r, "farm_on", False)),
                                  ("trasa (kolejne punkty)", getattr(r, "autowalk_on", False)),
                                  ("podazanie", bool(fol and fol.leader))) if on]
        gp = getattr(r, "_goto_point", None)
        r._dbg("ochrona: STOP (%s). Wstrzymane: %s. Dokanczam: cel walki VID %s (%s), podejdz=%s, wp%d",
               why, ", ".join(paused) or "-", getattr(r, "_engaged_vid", 0) or "-",
               getattr(r, "_engaged_cls", "") or "-",
               "(%.0f,%.0f)" % gp[:2] if gp else "-", getattr(r, "walk_idx", 0) + 1)
        tr = r._tr()
        if tr is not None:
            tr.rec("guard", ev="hold", why=why, paused=paused, engaged=getattr(r, "_engaged_vid", 0),
                   goto=[round(gp[0]), round(gp[1])] if gp else None)
        if getattr(r, "_looting", False):
            r._end_looting(now)

    def release(self, why):
        if not self.hold:
            return
        r = self.r
        sec = time.time() - self.hold_since
        self.hold, self.hold_why = False, ""
        r._dbg("ochrona: WZNOWIENIE (%s) po %.0f s wstrzymania", why, sec)
        tr = r._tr()
        if tr is not None:
            tr.rec("guard", ev="release", why=why, sec=round(sec, 1))
        r._route, r._last_plan, r._walk_prog = [], 0.0, None   # trasa od nowa (postac stala)

    def paused(self, what):
        """True gdy krok `what` ma czekac; pierwsze wstrzymanie kroku w danym hold trafia do dziennika."""
        if not self.hold:
            return False
        if what not in self._paused_logged:
            self._paused_logged.add(what)
            self.r._dbg("ochrona: krok '%s' wstrzymany (%s)", what, self.hold_why)
        return True

    # ---------- wiadomosci ----------
    def _alert(self, kind, name, text, now=None):
        self.alerts.append(dict(t=now or time.time(), kind=kind, name=name, text=text))
        del self.alerts[:-ALERTS_MAX]
        self.unread += 1

    def ack(self):
        """Panel: dzwonek przeczytany."""
        if self.unread:
            self.r._dbg("ochrona: dzwonek przeczytany (%d)", self.unread)
        self.unread = 0

    def _msg_paths(self):
        pid = self.r.pm.process_id
        return (os.path.join(LOGS_DIR, "msg_%d.txt" % pid), os.path.join(LOGS_DIR, "msg_%d.ok" % pid))

    def _msg_step(self, now):
        r = self.r
        if not self.msg:
            self.msg_status = "wylaczone"
            return
        if not getattr(r, "dll_exec", False):
            self.msg_status = "niedostepne (wymaga GameCoreTop + DLL)"
            return
        path, mark = self._msg_paths()
        if now - self._msg_sent >= (MSG_INSTALL_EVERY if self._msg_ok else MSG_CONFIRM_SEC * 2):
            self._msg_sent = now
            code = MSG_HOOK.replace("__PATH__", path).replace("__MARK__", mark)
            sent = autopot.send_command(r.pm.process_id, code)
            if not self._msg_ok:
                r._dbg("ochrona: podpinam wiadomosci w kliencie (wyslano=%s) -> %s", sent, path)
        if not self._msg_ok:
            try:
                st = os.stat(mark)
                with open(mark, encoding="ascii", errors="replace") as f:
                    txt = f.read().strip()
            except OSError:
                st, txt = None, ""
            if st is not None and st.st_mtime >= self._msg_sent - 1 and txt.startswith("ok"):
                self._msg_ok = True
                r._dbg("ochrona: wiadomosci podpiete (%s)", txt)
            elif st is not None and txt.startswith("nogame"):
                self.msg_status = "czekam na wejscie do gry"
                return
            else:
                self.msg_status = "podpinam..."
                if now - self._msg_sent > MSG_CONFIRM_SEC:
                    self.msg_status = "brak potwierdzenia z gry (DLL nie wykonuje?)"
                return
        self.msg_status = "dziala"
        self._msg_read(path, now)

    def _msg_read(self, path, now):
        r = self.r
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if self._msg_off is None:            # stare wiadomosci (sprzed startu radaru) pomijamy
            self._msg_off = size
            r._dbg("ochrona: plik wiadomosci %s, pomijam %d B sprzed startu", path, size)
            return
        if size < self._msg_off:              # plik przyciety/odtworzony
            self._msg_off = 0
        if size == self._msg_off:
            return
        with open(path, "rb") as f:
            f.seek(self._msg_off)
            data = f.read()
        end = data.rfind(b"\n") + 1           # tylko pelne linie (gra moze dopisywac)
        self._msg_off += end
        for raw in data[:end].splitlines():
            self._on_msg(raw.decode("cp1250", "replace").split("\t"), now)
        if self._msg_off > MSG_MAX_BYTES:
            try:
                open(path, "wb").close()
                self._msg_off = 0
            except OSError:
                pass

    def _on_msg(self, parts, now):
        r = self.r
        kind = parts[0] if parts else ""
        args = parts[2:]
        tr = r._tr()
        if kind == "whisper" and len(args) >= 3:
            try:
                mode = int(args[0])
            except ValueError:
                mode = -1
            name, line = args[1], "\t".join(args[2:])
            if line.startswith(name + " : "):    # klient dokleja "Nick : " do tresci szeptu
                line = line[len(name) + 3:]
            gm = mode == WHISPER_TYPE_GM
            self.msg_count += 1
            r._dbg("ochrona: SZEPT od %s%s (mode=%d): %s", name, " [GM]" if gm else "", mode, line)
            if tr is not None:
                tr.rec("guard", ev="whisper", name=name, mode=mode, gm=gm, text=line)
            self._alert("gm_whisper" if gm else "whisper", name, line, now)
            if gm and self.on:
                self._last_threat = now
                if self.stop and not self.hold:
                    self._start_hold("szept od GM %s" % name, now)
            if gm and self.sound:
                play(SOUND_GM)
                self._sound_t = now
            elif self.msg_sound:
                play(SOUND_MSG)
        elif kind in ("notice", "big") and args:
            text = "\t".join(args)
            r._dbg("ochrona: ogloszenie (%s): %s", kind, text)
            if tr is not None:
                tr.rec("guard", ev="notice", big=kind == "big", text=text)
            self.alerts.append(dict(t=now, kind="notice", name="", text=text))   # bez dzwonka (dla wszystkich)
            del self.alerts[:-ALERTS_MAX]
        elif kind == "wsys":
            r._dbg("ochrona: szept systemowy: %s", "\t".join(args))
        else:
            r._dbg("ochrona: nieznana linia wiadomosci: %r", parts)

    def test_sound(self):
        play(SOUND_GM)
        self.r._dbg("ochrona: test dzwieku")

    def summary(self):
        now = time.time()
        return {"hold": self.hold, "why": self.hold_why, "status": self.status, "count": self.count,
                "since": round(now - self.hold_since) if self.hold else None,
                "unread": self.unread, "alerts": [dict(a, ago=round(now - a["t"])) for a in self.alerts[-10:]],
                "msg": self.msg_status, "msg_count": self.msg_count,
                "threats": [{k: t[k] for k in ("name", "gm", "via", "d")} for t in self.threats]}
