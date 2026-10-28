"""Podazanie za postacia z INNEGO klienta (W5). Dziala w procesie radaru postaci podazajacej.

Klient gry nie ma followa, wiec to wlasna logika (notes/ANALIZA-skille-follow-kon.md, sekcja 2):
- Beacon: kazdy radar co BEACON_EVERY s zapisuje data/beacons/<nick>.json {nick, map, x, y, t}.
  Lider jest znany po NICKU (VID zmienia sie po teleporcie), pozycje znamy takze poza zasiegiem widzenia.
- Follower: co TICK s czyta beacon lidera i prowadzi postac trasa A* radaru (_engage_path/_route_aim/_goto).
  Histereza: rusza, gdy lider dalej niz distance + START_EXTRA; staje, gdy blizej niz distance.
  Odblokowanie wlasne (krok w bok) - NIE _stuck_nudge radaru, bo ten przy 2. utknieciu atakuje moba.
Wylaczone: konie, portale/teleporty za liderem, asysta w walce (kolejne kroki).
"""
import json
import math
import os
import threading
import time

BEACON_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "beacons"))
BEACON_EVERY = 0.2       # s: zapis pozycji
BEACON_STALE = 3.0       # s: starszy sygnal = lider niedostepny (radar lidera zamkniety / zawieszony)
TICK = 0.25              # s: krok sterownika
DEFAULT_DISTANCE = 400   # jedn. swiata: tak blisko lidera stajemy
START_EXTRA = 300        # rusz dopiero, gdy lider dalej niz distance + to (bez szarpania w miejscu)
AIM_SHORT = 0.6          # cel ruchu = punkt na odcinku do lidera, distance*AIM_SHORT przed nim
RESEND = 0.3             # s: ponowienie !goto (jak WALK_RESEND radaru)
STUCK_SEC = 1.5          # s bez ruchu (< STUCK_EPS) mimo marszu = utkniecie
STUCK_EPS = 50.0
SIDESTEP = 500.0         # jedn.: krok w bok przy utknieciu
SIDESTEP_HOLD = 0.8      # s
TELEPORT_JUMP = 3000.0   # skok wlasnej pozycji = teleport -> zapomnij trase


def _safe_name(nick):
    return "".join(ch for ch in nick if ch.isalnum() or ch in "_-[]")


def read_beacon(nick):
    try:
        with open(os.path.join(BEACON_DIR, "%s.json" % _safe_name(nick)), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Beacon:
    """Watek: pozycja tej postaci dla innych radarow (data/beacons/<nick>.json)."""

    def __init__(self, radar):
        self.r = radar
        self._path = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(BEACON_EVERY)
            try:
                self._write()
            except Exception:
                pass                     # beacon nigdy nie przerywa radaru

    def _write(self):
        nick = self.r.ident.get("nick")
        hx, hy = self.r._hero_world()
        if not nick or hx is None:
            return
        os.makedirs(BEACON_DIR, exist_ok=True)
        path = os.path.join(BEACON_DIR, "%s.json" % _safe_name(nick))
        tmp = path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"nick": nick, "pid": self.r.pm.process_id, "map": getattr(self.r.map, "name", None),
                       "x": round(hx), "y": round(hy), "t": time.time()}, f)
        os.replace(tmp, path)
        self._path = path


class Follower:
    """Sterownik podazania. start(nick, distance) / stop(); stan w status() (do API/panelu)."""

    def __init__(self, radar):
        self.r = radar
        self.leader = None
        self.distance = DEFAULT_DISTANCE
        self.state = "wylaczone"
        self._moving = False
        self._last_goto = 0.0
        self._last_aim = None
        self._um_pos, self._um_t = None, 0.0
        self._side = 1
        self._side_until = 0.0
        self._last_pos = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- sterowanie (watek API) ----------
    def start(self, leader, distance=None):
        with self._lock:
            self.leader = leader
            if distance is not None:
                self.distance = int(distance)
            self._moving = False
            self._last_aim = None
            self._um_pos = None
            self.state = "czekam na sygnal lidera"

    def stop(self):
        with self._lock:
            was_moving = self._moving
            self.leader = None
            self._moving = False
            self.state = "wylaczone"
        if was_moving:
            self._halt()

    def status(self):
        return {"leader": self.leader, "distance": self.distance, "state": self.state}

    # ---------- petla ----------
    def _loop(self):
        while True:
            time.sleep(TICK)
            try:
                with self._lock:
                    if self.leader:
                        self._tick()
            except Exception as e:      # blad jednego ticku nie zabija sterownika
                self.state = "blad: %s" % e

    def _halt(self):
        hx, hy = self.r._hero_world()
        if hx is not None:
            self.r._goto(hx, hy, "fstop", hx, hy)    # !goto na wlasna pozycje = stop

    def _tick(self):
        r = self.r
        hx, hy = r._hero_world()
        if hx is None:
            self.state = "postac poza swiatem"
            return
        if self._last_pos and math.hypot(hx - self._last_pos[0], hy - self._last_pos[1]) > TELEPORT_JUMP:
            r._eng_route_t = 0.0             # teleport: trasa sprzed skoku jest bez sensu
        self._last_pos = (hx, hy)

        b = read_beacon(self.leader)
        if not b or time.time() - b.get("t", 0) > BEACON_STALE:
            self.state = "brak sygnalu lidera (radar lidera nie dziala?)"
            self._stop_if_moving()
            return
        my_map = getattr(r.map, "name", None)
        if b.get("map") != my_map:
            self.state = "lider na innej mapie (%s)" % b.get("map")
            self._stop_if_moving()
            return

        lx, ly = float(b["x"]), float(b["y"])
        d = math.hypot(lx - hx, ly - hy)
        if self._moving and d < self.distance:
            self._moving = False
            self._halt()
        elif not self._moving and d > self.distance + START_EXTRA:
            self._moving = True
            self._um_pos, self._um_t = (hx, hy), time.time()
        if not self._moving:
            self.state = "przy liderze (%.0f)" % d
            return
        self.state = "ide za liderem (%.0f)" % d
        self._walk(hx, hy, lx, ly, d)

    def _stop_if_moving(self):
        if self._moving:
            self._moving = False
            self._halt()

    def _walk(self, hx, hy, lx, ly, d):
        r = self.r
        now = time.time()
        if now < self._side_until:           # trwa krok w bok po utknieciu
            return
        # utkniecie: stoimy mimo marszu -> krok w bok (wolna strona) i nowa trasa
        if self._um_pos is None or math.hypot(hx - self._um_pos[0], hy - self._um_pos[1]) > STUCK_EPS:
            self._um_pos, self._um_t = (hx, hy), now
        elif now - self._um_t > STUCK_SEC:
            ux, uy = (lx - hx) / d, (ly - hy) / d
            for side in (self._side, -self._side):
                ax, ay = hx - uy * side * SIDESTEP + ux * 150, hy + ux * side * SIDESTEP + uy * 150
                if not r.collision.ok or r.collision.line_clear(hx, hy, ax, ay):
                    break
            else:
                ax, ay = hx - ux * 300, hy - uy * 300      # obie strony w scianie: cofnij sie
            self._side = -self._side
            r._goto(ax, ay, "fside", hx, hy)
            r._eng_route_t = 0.0
            self._side_until = now + SIDESTEP_HOLD
            self._um_pos, self._um_t = (hx, hy), now + SIDESTEP_HOLD
            self.state = "utknalem - obchodze"
            return
        # cel: punkt przed liderem (od naszej strony), trasa A* radaru z omijaniem przeszkod
        k = max(d - self.distance * AIM_SHORT, 0.0) / d
        gx, gy = hx + (lx - hx) * k, hy + (ly - hy) * k
        route = r._engage_path(hx, hy, gx, gy)
        aim = r._route_aim(route, 0, hx, hy) if route else (gx, gy)
        moved = self._last_aim is None or math.hypot(aim[0] - self._last_aim[0], aim[1] - self._last_aim[1]) > 150
        if moved or now - self._last_goto >= RESEND:
            r._goto(aim[0], aim[1], "follow", hx, hy)
            self._last_goto, self._last_aim = now, aim
