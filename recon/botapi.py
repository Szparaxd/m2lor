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
  trasa: name = waypoint_add {"x","y"} / waypoint_undo / waypoint_clear  (jak Ctrl+klik / klawisz C)
  podazanie: name = follow, value {"leader": "Nick", "distance": N} albo {"leader": null}
  akcje jak klawisze radaru: name = action, value reinject (J) / calibrate (K) / rescan (R) / rebuild_map (M)
Recznie:  curl -H "X-Bot-Token: <token>" http://127.0.0.1:<port>/api/snapshot
"""
import atexit
import json
import math
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import follow
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
    "loot_radius":   (int,   "loot_radius",   "loot_radius_var",   "_on_loot_radius",    (300, 4000)),
    "loot_mode":     (str,   "loot_mode",     "loot_mode_var",     "_on_loot_mode",      None),
    "autowalk":      (bool,  "autowalk_on",   "autowalk_var",      "_on_autowalk_check", None),
    "walk_fight":    (bool,  "walk_fight",    "walkfight_var",     "_on_walkfight",      None),
    "farm":          (bool,  "farm_on",       "farm_var",          "_on_farm_check",     None),
    "farm_radius":   (int,   "farm_radius",   "farmr_var",         "_on_farmr",          (500, 6000)),
    "farm_metin":    (bool,  "farm_metin",    "farm_metin_var",    "_on_farm_metin",     None),
    "farm_boss":     (bool,  "farm_boss",     "farm_boss_var",     "_on_farm_boss",      None),
    "area_atk":      (bool,  "area_atk_on",     "area_atk_var",      "_on_area_atk_check",  None),
    "area_atk_radius": (int, "area_atk_radius", "area_atk_r_var",    "_on_area_atk_radius", (300, 8000)),
    "area_atk_mode": (str,   "area_atk_mode",   "area_atk_mode_var", "_on_area_atk_mode",   None),
    "area_atk_hop":  (bool,  "area_atk_hop",    "area_atk_hop_var",  "_on_area_atk_hop",    None),
    "prep_dismount": (bool,  "prep_dismount", "prep_dismount_var", "_on_prep_opts",      None),
    "prep_buffs":    (bool,  "prep_buffs",    "prep_buffs_var",    "_on_prep_opts",      None),
    "prep_remount":  (bool,  "prep_remount",  "prep_remount_var",  "_on_prep_opts",      None),
    "cape":          (bool,  "cape_on",       "cape_var",          "_on_cape_check",     None),
    "cape_period":   (int,   "cape_period",   "cape_period_var",   "_on_cape_period",    (10, 600)),
    "cape_min_mobs": (int,   "cape_min_mobs", "cape_mobs_var",     "_on_cape_mobs",      (1, 30)),
    "speed":         (bool,  "speed_on",      "speed_var",         "_on_speed_check",    None),
    "move_mult":     (float, "move_mult",     "movx_var",          "_on_movx",           (1.0, 3.0)),
    "atk_mult":      (float, "atk_mult",      "atkx_var",          "_on_atkx",           (1.0, 3.0)),
    "autobuff":      (bool,  "autobuff.on",   "autobuff_var",      "_on_autobuff_check", None),
}
_SLIDER_HANDLERS = {"_on_low", "_on_high", "_on_cd", "_on_loot_cd", "_on_loot_radius", "_on_farmr", "_on_movx",
                    "_on_atkx", "_on_area_atk_radius", "_on_cape_period", "_on_cape_mobs"}
# opcje tekstowe = przelaczniki radiowe w panelu tk: dozwolone wartosci
CHOICES = {"loot_mode": ("human", "instant", "high"), "area_atk_mode": ("safe", "aggro")}
# akcje jednorazowe (klawisze radaru); dluzszy limit - wstrzykniecie DLL / kalibracja trwaja kilka sekund
ACTIONS = ("reinject", "calibrate", "rescan", "rebuild_map")
ACTION_TIMEOUT = 30.0


def _get(obj, dotted):
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


class BotApi:
    def __init__(self, radar):
        self.r = radar
        self.follower = None          # follow.Follower - ustawiany w start() (atrapa w testach go nie ma)

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
            if name in CHOICES:
                d["choices"] = list(CHOICES[name])
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
                "metins": len(getattr(r, "metins", []) or []),
                "pot_mode": "bez fokusu" if (getattr(r, "dll_exec", False) or getattr(r, "native_drink", False))
                            else "wymaga fokusu",
                "pot_vnums": list(getattr(r, "pot_vnums", []) or []),
                "area_atk": getattr(r, "_area_atk_status", ""),
                "area_atk_count": getattr(r, "area_atk_count", 0),
                "cape": getattr(r, "_cape_status", ""),
                "cape_count": getattr(r, "cape_count", 0),
                "buff_count": getattr(getattr(r, "autobuff", None), "count", 0),
                "mount": self._mount(),
            },
            "follow": self.follower.status() if self.follower else None,
        }
        if entities and hx is not None:
            snap["entities"] = self.entities(hx, hy)
            snap["overlay"] = self.overlay()
        return snap

    def _mount(self):
        m = getattr(self.r, "mounted", None)
        return None if m is None else {"on": bool(m[0]), "model": m[1] if m[0] else None}

    def overlay(self):
        """Co radar rysuje poza encjami: waypointy, biezaca trasa A*, okrag farmy, cel walki."""
        r = self.r

        def pts(seq):
            return [[round(x), round(y)] for x, y in (seq or [])]
        fc = getattr(r, "farm_center", None)
        tgt = getattr(r, "_combat_target", None)
        return {
            "path": pts(getattr(r, "path", None)),
            "walk_idx": getattr(r, "walk_idx", 0),
            "route": pts(getattr(r, "_route", None)) or pts(getattr(r, "_eng_route", None)),
            "farm": {"x": round(fc[0]), "y": round(fc[1]), "r": round(r.farm_radius)}
                    if getattr(r, "farm_on", False) and fc else None,
            "target": {"x": round(tgt[0]), "y": round(tgt[1]), "melee": bool(tgt[2])} if tgt else None,
            "ground": [[round(g[2]), round(g[3])] for g in list(getattr(r, "ground", []) or [])],
            "learned": [[round(x), round(y)] for x, y, *_ in list(getattr(r, "_learned", []) or [])],
            "area_r": round(r.area_atk_radius) if getattr(r, "area_atk_on", False) else None,
        }

    def entities(self, hx, hy, radius=ENTITY_R):
        """[{x, y, kind, vnum, vid}] aktorow (bez wlasnej postaci) + metiny z efektow, w promieniu."""
        r, out = self.r, []
        for x, y, vnum, is_pc, o, vid in r._iter_actors(hx, hy):
            if math.hypot(x - hx, y - hy) <= radius:
                kind = r._classify(vnum, is_pc)
                e = {"x": round(x), "y": round(y), "kind": kind, "vnum": vnum, "vid": vid}
                if kind != "mob":                    # nazwy tylko dla rzadkich (odczyt pamieci per encja)
                    try:
                        e["name"] = r._mob_name(o) or None
                    except Exception:
                        pass
                out.append(e)
        for mx, my, name in list(r.metins):
            if math.hypot(mx - hx, my - hy) <= radius:
                out.append({"x": round(mx), "y": round(my), "kind": "metin", "vnum": 0, "vid": 0,
                            "name": name if isinstance(name, str) else None})
        return out

    # ---------- zapis (wykonywany w watku GUI, wolajacy czeka na wynik) ----------
    def command(self, name, value, timeout=3.0):
        """Zmien opcje tak, jak zrobilby to klik w panelu. Zwraca nowe options(); ValueError gdy zle dane."""
        if name == "buff":
            fn = self._buff_fn(value)
        elif name == "pot_sizes":
            fn = self._pot_sizes_fn(value)
        elif name in ("waypoint_add", "waypoint_undo", "waypoint_clear"):
            fn = self._waypoint_fn(name, value)
        elif name == "follow":
            fn = self._follow_fn(value)
        elif name == "action":
            fn = self._action_fn(value)
            timeout = max(timeout, ACTION_TIMEOUT)
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
        elif typ is str:
            if value not in CHOICES.get(name, ()):
                raise ValueError("%s: oczekuje jednej z %s" % (name, list(CHOICES.get(name, ()))))
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

    def _action_fn(self, value):
        """Jednorazowe akcje radaru - te same, co klawisze J/K/R/M w oknie tk."""
        if value not in ACTIONS:
            raise ValueError("action: oczekuje jednej z %s" % list(ACTIONS))
        r = self.r

        def fn():
            if value == "reinject":
                r.dll_status = r._inject_dll()
            elif value == "calibrate":
                r.calibrate()
            elif value == "rescan":
                r.rescan()
            else:
                ma = sys.modules[type(r).__module__].map_assets
                r._set_map(ma.build_map_image(r.map.name, use_cache=False))
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

    def _follow_fn(self, value):
        """Podazanie (recon/follow.py): {"leader": nick, "distance": N} wlacza, {"leader": null} wylacza.
        Na start wylacza farme i auto-chodzenie (inaczej trzy petle sterowalyby jedna postacia)."""
        if not isinstance(value, dict):
            raise ValueError('follow: oczekuje {"leader": "Nick" | null, "distance": N}')
        leader = value.get("leader")
        if leader is not None and (not isinstance(leader, str) or not identity.NICK_RE.match(leader)):
            raise ValueError("follow: zly nick lidera")
        if leader and leader == self.r.ident.get("nick"):
            raise ValueError("follow: postac nie moze isc sama za soba")
        dist = value.get("distance")
        if dist is not None:
            try:
                dist = min(max(int(dist), 200), 3000)
            except (TypeError, ValueError):
                raise ValueError("follow: distance musi byc liczba")
        fol = self.follower

        def fn():
            if not leader:
                fol.stop()
                return
            for opt in ("farm", "autowalk"):
                typ, attr, var, handler, _rng = OPTIONS[opt]
                if hasattr(self.r, var) and getattr(self.r, attr):
                    getattr(self.r, var).set(False)
                    getattr(self.r, handler)()
            fol.start(leader, dist)
        return fn

    def _waypoint_fn(self, name, value):
        """Trasa auto-chodzenia jak w radarze: add = Ctrl+klik, clear = klawisz C; undo = usun ostatni punkt.
        Trase zapisujemy per mapa (save_route), jesli ta wersja radaru to umie."""
        r = self.r
        if not hasattr(r, "path"):
            raise ValueError("trasa niedostepna w tej wersji radaru")
        if name == "waypoint_add":
            try:
                x, y = float(value["x"]), float(value["y"])
            except (TypeError, KeyError, ValueError):
                raise ValueError('waypoint_add: oczekuje {"x": N, "y": N} (wsp. swiata)')
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < 10_000_000 and 0 <= y < 10_000_000):
                raise ValueError("waypoint_add: punkt poza mapa")
        save = getattr(sys.modules.get(type(r).__module__), "save_route", None)

        def fn():
            if name == "waypoint_add":
                r.path = r.path + [(x, y)]          # nowa lista (petla czyta snapshot, nie mutujemy)
            elif name == "waypoint_undo":
                r.path = r.path[:-1]
                r.walk_idx = min(getattr(r, "walk_idx", 0), max(len(r.path) - 1, 0))
            else:
                r.path = []
                r.walk_idx = 0
                for attr, empty in (("_walk_prog", None), ("_route", [])):
                    if hasattr(r, attr):
                        setattr(r, attr, empty)
            if save and getattr(r, "map", None) is not None:
                save(r.map.name, r.path)
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
    # podazanie miedzy klientami: pozycja tej postaci dla innych + sterownik (recon/follow.py)
    follow.Beacon(radar)
    api.follower = follow.Follower(radar)
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
