"""
RADAR Z OBRAZEM MAPY (tkinter + Pillow) - M7.

Tlo = prawdziwa minimapa z plikow klienta (EterPack 'Outdoor' -> mozaika sektorow
minimap.dds, patrz recon/map_assets.py). Na to naniesieni: gracz (zielony trojkat),
moby (czerwone), NPC (niebieskie), specjalne (mietowe) i bossy (fiolet, z nazwa) z wlasnej
listy data/radar_mobs.txt (edytowalna w locie), bossy rangi 4/5 z mob_proto (pomarancz),
metiny (zolte). Typ/ranga z Dump_Proto/mob_proto.txt po vnum aktora.
Wspolrzedne swiata czytane z pamieci (jak radar_preview.py).

NIE modyfikuje radar_preview.py. Wymaga: Pillow (python -m pip install Pillow).

Tryby i klawisze:
    F  - przelacz widok: FOLLOW (wycentrowany na graczu, jak minimapa) <-> FULL (cala mapa)
    + / -   - zoom
    strzalki - przesun (w trybie FULL)
    Y / X   - odbij os Y / X obrazu (kalibracja wzgledem gry)
    N  - pokaz/ukryj nazwy mobow/metinow (z vnum - do uzupelniania data/radar_mobs.txt)
    LPM na mobie - menu: dopisz do listy jako boss / specjalny / zwykly albo usun z listy
    R  - natychmiastowy skan encji (i tak skanuje w tle co RESCAN_SEC)
    K  - ponowna kalibracja offsetow pol (robiona automatycznie przy starcie)
    H  - auto-mikstura wl/wyl
    [ / ]  - prog "Pij <" -/+ 5% (histereza: pij ponizej "Pij<", lej az do "Lecz do")
    Suwaki na gorze: Pij <% (start), Lecz do % (cel), Odstep s (min. czas miedzy miksturami)
    J  - (po)wstrzyknij autopot.dll = picie BEZ fokusu (wymaga build.bat: dll + inject.exe).
         Bez DLL dziala stara sciezka SendInput - tylko gdy gra jest aktywnym oknem.
    data/potion_vnums.txt - wpisz vnum mikstury -> picie WPROST z ekwipunku (skan, ignoruje pasek,
         przezywa przesuniecie stacka); pusty plik -> picie slotem POTION_KEY. Vnum: recon/inv_dump.py.
    Okno radaru mozna dowolnie rozciagac (mapa i znaczniki dopasowuja sie do rozmiaru).
    M  - wymus przebudowe obrazu mapy (bez cache)
    ESC - wyjscie

Uruchom (terminal JAKO ADMIN, gra wlaczona):
    cd "C:/Metin/radar-project" && PYTHONIOENCODING=ascii:ignore python recon/radar_map.py [map_name] [-e EXE]

    map_name      - wymuszona mapa (domyslnie: biezaca, z CMapOutdoor)
    -e/--exe EXE  - nazwa procesu gry (domyslnie GameCoreTop.exe), np. -e vCore.exe
    -h/--help     - pomoc
"""
import argparse
import ctypes
import math
import os
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np
import pymem
import pymem.process

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autopot
import calibrate
import map_assets
import rtti
from PIL import Image, ImageTk

DEFAULT_PROCESS = "GameCoreTop.exe"   # nadpisywane przez -e/--exe
# vtable szukane przy starcie po NAZWIE klasy z RTTI (recon/rtti.py) - przezywa przekompilowanie exe.
# (dla tego exe wychodzi: CGameEventManager base+0x3D6CA0, CActorInstance base+0x3D4FB0,
#  CEffectInstance base+0x3CE250, CMapOutdoor base+0x3D5D40)
HERO_CLASS = "CGameEventManager"   # pozycja gracza: X@+0x8 ; worldY = -(+0xC)
ACTOR_CLASS = "CActorInstance"
EFF_CLASS = "CEffectInstance"
MAPOUT_CLASS = "CMapOutdoor"       # 1 instancja; nazwa BIEZACEJ mapy = std::string @+0x8
HERO_POS = 0x8
# Offsety POL - wartosci domyslne (GameCoreTop.exe); przy starcie nadpisywane przez auto-kalibracje
# (recon/calibrate.py), bo przekompilowany exe (GameCoreTop.exe) ma je przesuniete (+4 / -8).
# Aktor: od POCZATKU CActorInstance (glowna vtable, COL offset 0; vtable podobiektu lezy na +0x2A8).
# vnum/nazwa leza PRZED aktorem - w obiekcie, ktory go zawiera (CInstanceBase?, stride 0x7C8).
DEFAULT_OFFSETS = dict(
    mob_x=0x50C,        # worldX ; worldY = +(mob_x+4)
    race=0x420,         # -> CRaceData (tylko do kalibracji vnum)
    race_model=0x20,    # std::string sciezki modelu w CRaceData
    vnum=-0x70,
    name=-0x1CC,        # std::string, cp1250
    eff_data=0x290,     # CEffectInstance -> CEffectData
    eff_fname=0x44,     # std::string pliku *.mse w CEffectData
    eff_x=0x280,        # metin worldX ; worldY = -(eff_x+4)
)
PROTO_DIR = r"C:/Metin/Terenzo 2024 - Muchomor Upgrade/GameCore/Dump_Proto"
# mob_proto TYPE: 0 potwor, 1 NPC, 2 kamien (metin - z efektow), 3 warp, 4 drzwi, 5 budynek, 9 goto
HIDDEN_TYPES = {2, 3, 4, 5, 9}
# Ranga 2/3 NIE nadaje sie do wyrozniania (na mapie orkow to praktycznie kazdy mob) -> mini-bossy
# i potwory specjalne z wlasnej listy (data/radar_mobs.txt); automatycznie tylko ranga 4/5.
BOSS_RANK = 4       # 4 = BOSS, 5 = KING
MOBLIST_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "..", "data", "radar_mobs.txt"))
MOBLIST_CATS = {"boss", "special", "normal"}
CLICK_RADIUS = 12   # px na radarze: klik dalej od znacznika nic nie robi
COL_MOB, COL_NPC, COL_SPECIAL = "#ff3b3b", "#4da6ff", "#00e5b0"
COL_BOSS, COL_KING = "#d94dff", "#ff9f1a"
MAPOUT_NAME = 0x8
WORLD_MIN, WORLD_MAX = 1.0, 10_000_000.0

CANVAS = 720
RESCAN_SEC = 1.5    # okres skanu pamieci w tle (nowe moby/metiny); sam skan ~0.2 s
# Auto-mikstura (recon/autopot.py): HP z CPythonPlayer, "nacisniecie" slotu w oknie gry.
PLAYER_CLASS = "CPythonPlayer"
POTION_KEY = "1"          # slot paska z czerwonymi miksturami (gdy nie uzywamy vnum)
# Histereza: zaczynamy pic ponizej LOW, lejemy az HP dojdzie do HIGH (HIGH >= LOW).
POTION_LOW = 50           # % HP - zacznij pic ponizej (suwak "Pij <", klawisze [ / ])
POTION_HIGH = 75          # % HP - lej az do tego poziomu (suwak "Lecz do")
POTION_COOLDOWN = 0.3     # s - min. odstep miedzy miksturami (suwak "Odstep")
POTION_POLL = 0.07        # s - jak czesto dedykowana petla sprawdza HP (szybka reakcja, nie co klatke)
# Picie wprost z ekwipunku: vnum mikstur w data/potion_vnums.txt (edytowalny w locie).
# Pusty plik -> picie slotem paska (POTION_KEY). Lista vnum -> skan EQ (net.SendItemUsePacket):
# ignoruje pasek i przezywa przesuniecie stacka. Vnum podejrzysz przez recon/inv_dump.py.
POTION_VNUMS_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "..", "data", "potion_vnums.txt"))

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
WRITABLE = {0x04, 0x08, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD),
    ]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def load_mob_proto():
    """{vnum: (type, rank)} z mob_proto.txt; pusty slownik gdy brak pliku (wszystko = mob)."""
    proto = {}
    try:
        with open(os.path.join(PROTO_DIR, "mob_proto.txt"), encoding="latin-1") as f:
            next(f)
            for line in f:
                c = line.split("\t")
                if len(c) > 3 and c[0].isdigit():
                    proto[int(c[0])] = (int(c[3] or 0), int(c[2] or 0))
    except OSError:
        print("[proto] brak mob_proto.txt - bez klasyfikacji bossow/NPC")
    return proto


def load_mob_list(path=None):
    """{vnum: 'boss'|'special'|'normal'} z wlasnej listy; linie 'vnum kategoria  # komentarz'."""
    path = path or MOBLIST_PATH
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                p = line.split("#", 1)[0].split()
                if not p:
                    continue
                if len(p) >= 2 and p[0].isdigit() and p[1].lower() in MOBLIST_CATS:
                    out[int(p[0])] = p[1].lower()
                else:
                    print("[lista] pomijam linie %d: %r" % (n, line.strip()))
    except OSError:
        print("[lista] brak %s - tylko bossy rangi 4/5" % path)
    return out


def load_potion_vnums(path=None):
    """Lista vnum mikstur do picia (skan EQ); puste gdy brak pliku -> picie slotem paska.
    Format: jeden vnum na linie, komentarze po '#'."""
    path = path or POTION_VNUMS_PATH
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                p = line.split("#", 1)[0].split()
                if p and p[0].isdigit():
                    out.append(int(p[0]))
    except OSError:
        pass
    return out


def set_mob_category(vnum, cat, name="", path=None):
    """Ustawia kategorie vnum w liscie (cat=None -> usuwa). Podmienia istniejaca linie z tym vnum,
    inaczej dopisuje na koncu; komentarze i reszta pliku zostaja bez zmian."""
    path = path or MOBLIST_PATH
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    new = "%-3d %-8s # %s" % (vnum, cat, name) if cat else None
    out, done = [], False
    for line in lines:
        p = line.split("#", 1)[0].split()
        if p and p[0] == str(vnum):
            if new and not done:
                out.append(new)
            done = True
            continue
        out.append(line)
    if new and not done:
        out.append(new)
    with open(path, "w", encoding="utf-8", newline="") as f:   # bez zamiany \n na \r\n
        f.write("\n".join(out) + "\n")


def writable_regions(handle):
    regions, addr, mbi = [], 0, MBI()
    while addr < 0x7FFFFFFF:
        if not k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if (mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01)
                and p in WRITABLE and 0 < s <= 512 * 1024 * 1024):
            regions.append((b, s))
        nxt = b + s
        if nxt <= addr:
            break
        addr = nxt
    return regions


class RadarMap:
    def __init__(self, root, map_name=None, process=DEFAULT_PROCESS):
        self.pm = pymem.Pymem(process)
        mod = pymem.process.module_from_name(self.pm.process_handle, process)
        self.base = mod.lpBaseOfDll
        self.mod_lo = mod.lpBaseOfDll
        self.mod_hi = mod.lpBaseOfDll + mod.SizeOfImage

        self.hero_obj = None
        self.mapout_obj = None    # CMapOutdoor
        self.fixed_map = bool(map_name)   # mapa podana recznie -> bez auto-przelaczania
        self.mobs = []            # [obj]
        self.effs = []            # [obj] wszystkie CEffectInstance (do kalibracji)
        self.metins = []          # [(wx, wy, name)]
        self.off = dict(DEFAULT_OFFSETS)
        self.image_size = mod.SizeOfImage
        self.show_names = False
        self.proto = load_mob_proto()
        self.moblist = {}
        self._moblist_mtime = None
        self._reload_moblist()

        self.vt = rtti.find_vtables(self.pm, self.base, mod.SizeOfImage,
                                    [HERO_CLASS, ACTOR_CLASS, EFF_CLASS, MAPOUT_CLASS])
        print("[rtti] " + ", ".join("%s=base+0x%X" % (k, v - self.base) for k, v in self.vt.items()))
        try:   # opcjonalne - bez CPythonPlayer radar dziala, tylko bez HP/auto-mikstury
            self.vt.update(rtti.find_vtables(self.pm, self.base, mod.SizeOfImage, [PLAYER_CLASS]))
        except LookupError as e:
            print("[hp] %s - auto-mikstura wylaczona" % e)

        # auto-mikstura
        self.player_obj = None
        self.points_addr = None           # odszyfrowywana tablica punktow (HP @[5], max @[6])
        self.hwnd = autopot.game_window(self.pm.process_id)
        self.autopot_on = True
        self.pot_low = POTION_LOW                 # zacznij pic ponizej
        self.pot_high = max(POTION_HIGH, POTION_LOW)   # lej do (>= low)
        self.pot_cd = POTION_COOLDOWN
        self._healing = False                     # stan histerezy (miedzy low a high)
        self._last_pot = 0.0
        self.pot_count = 0
        self._hp = self._mhp = 0                  # ostatnio odczytane HP (do rysowania)
        self._pct = 0.0
        self._pts_valid = False
        # picie bez fokusu: wstrzykujemy autopot.dll i gadamy z nim przez potok (recon/autopot.py).
        # Gdy sie nie uda (brak DLL / nie zbudowany) - zostaje stara sciezka SendInput (tylko z fokusem).
        self.pipe = autopot.PipeDrinker()
        self.dll_status = self._inject_dll()
        # vnum mikstur -> picie wprost z EQ; pusta lista -> picie slotem paska
        self.pot_vnums = []
        self._pot_vnums_mtime = None
        self._reload_pot_vnums()

        self.root = root
        # pasek sterowania auto-miksturą: checkbox + 3 suwaki (pij ponizej / lecz do / odstep).
        # Klawisze [H] [ / ] dzialaja dalej i sa zsynchronizowane; zmienne tk to "lustro" -
        # zrodlem prawdy zostaja self.autopot_on / self.pot_low / self.pot_high / self.pot_cd.
        self.autopot_var = tk.BooleanVar(value=self.autopot_on)
        self.low_var = tk.IntVar(value=self.pot_low)
        self.high_var = tk.IntVar(value=self.pot_high)
        self.cd_var = tk.DoubleVar(value=self.pot_cd)
        bar = tk.Frame(root, bg="#0b0f0b")
        bar.pack(side="top", fill="x")
        tk.Checkbutton(bar, text="Auto-mikstura", variable=self.autopot_var,
                       command=self._on_autopot_check, bg="#0b0f0b", fg="#9fe7a6",
                       selectcolor="#1b2a1b", activebackground="#0b0f0b", activeforeground="#cdebcf",
                       font=("Consolas", 9)).pack(side="left", padx=6, pady=2)

        def _mkscale(lbl, var, lo, hi, res, cmd, fg, length=140):
            tk.Label(bar, text=lbl, bg="#0b0f0b", fg="#9fe7a6", font=("Consolas", 9)).pack(side="left")
            tk.Scale(bar, from_=lo, to=hi, resolution=res, orient="horizontal", length=length,
                     variable=var, command=cmd, bg="#0b0f0b", fg=fg, troughcolor="#222",
                     highlightthickness=0, font=("Consolas", 8)).pack(side="left", padx=2)

        _mkscale("Pij <%", self.low_var, 5, 95, 1, self._on_low, "#ffe14d")
        _mkscale("Lecz do %", self.high_var, 5, 99, 1, self._on_high, "#4da6ff")
        _mkscale("Odstep s", self.cd_var, 0.1, 1.0, 0.05, self._on_cd, "#cdebcf", 110)

        self.canvas = tk.Canvas(root, width=CANVAS, height=CANVAS, bg="#0b0f0b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.cw = self.ch = CANVAS            # biezacy rozmiar canvasa (zmienia sie z oknem)
        self.full_zoom = 1.0                  # zoom trybu FULL wzgledem dopasowania do okna
        self.canvas.bind("<Configure>", self._on_resize)
        root.bind("<Key>", self.on_key)
        self.canvas.bind("<Button-1>", self.on_click)
        self._hits = []           # znaczniki z ostatniej klatki: (sx, sy, vnum, obj)

        # pierwsze czytanie pozycji -> kalibracja offsetow pol -> auto-detekcja mapy
        self.rescan()
        self.calibrate()
        hx, hy = self._hero_world()
        self._set_map(self._load_map(map_name, hx, hy))
        self._tkimg = None

        # widok
        self.mode = "follow"            # 'follow' | 'full'
        self.view_world = 20000.0       # szerokosc okna swiata w trybie follow
        self.pan_x = 0.0
        self.pan_y = 0.0

        # skan w tle: bez niego metiny/moby, ktore pojawily sie po starcie, nigdy nie trafialy
        # na liste (rescan tylko przy starcie i na [R]). rescan podmienia listy na koncu (atomowo).
        threading.Thread(target=self._rescan_loop, daemon=True).start()
        # osobna, szybka petla auto-mikstury (reakcja ~POTION_POLL, niezaleznie od rysowania)
        threading.Thread(target=self._autopot_loop, daemon=True).start()
        self.tick()

    def _rescan_loop(self):
        while True:
            time.sleep(RESCAN_SEC)
            try:
                self.rescan()
                self._reload_moblist()
                self._reload_pot_vnums()
                # teleport/zmiana mapy -> przeladuj tlo (budowa obrazu tez w tle, potem podmiana)
                name = self._current_map_name()
                if name and not self.fixed_map and name != self.map.name:
                    print("[map] zmiana mapy: %s -> %s" % (self.map.name, name))
                    self._set_map(map_assets.build_map_image(name))
            except Exception as e:
                print("[rescan] blad:", e)

    def calibrate(self):
        """Wyznacza offsety pol z zywych danych (patrz recon/calibrate.py) i skanuje ponownie."""
        hx, hy = self._hero_world()
        if hx is None:
            print("[kalibracja] brak gracza - zostaja offsety domyslne ([K] ponawia)")
            return
        off, report = calibrate.calibrate(self.pm, self.base, self.image_size, self.mobs,
                                          self.effs, hx, hy, DEFAULT_OFFSETS, PROTO_DIR)
        self.off = off
        print("[kalibracja]\n  " + "\n  ".join(report))
        self.rescan()

    def _reload_pot_vnums(self):
        """Przeladowuje liste vnum mikstur, gdy plik sie zmienil (edycja w trakcie dzialania)."""
        try:
            mtime = os.path.getmtime(POTION_VNUMS_PATH)
        except OSError:
            mtime = None
        if mtime != self._pot_vnums_mtime:
            self._pot_vnums_mtime = mtime
            self.pot_vnums = load_potion_vnums()
            print("[mikstura] vnum do picia: %s" % (self.pot_vnums or "(brak - slot paska)"))

    def _on_autopot_check(self):
        self.autopot_on = bool(self.autopot_var.get())

    def _on_low(self, val):
        self.pot_low = int(float(val))
        if self.pot_high < self.pot_low:          # cel nie moze byc ponizej progu startu
            self.pot_high = self.pot_low
            self.high_var.set(self.pot_high)

    def _on_high(self, val):
        self.pot_high = int(float(val))
        if self.pot_high < self.pot_low:
            self.pot_low = self.pot_high
            self.low_var.set(self.pot_low)

    def _on_cd(self, val):
        self.pot_cd = float(val)

    def _inject_dll(self):
        """Wstrzykuje autopot.dll (picie bez fokusu) i laczy potok. Zwraca krotki opis stanu."""
        try:
            ok, msg = autopot.ensure_injected(self.pm.process_id)
        except Exception as e:
            ok, msg = False, str(e)
        print("[dll] %s" % msg)
        return "wstrzyknieta" if ok else "brak (%s)" % msg

    def _reload_moblist(self):
        """Wczytuje liste ponownie, gdy plik sie zmienil (edycja w trakcie dzialania radaru)."""
        try:
            mtime = os.path.getmtime(MOBLIST_PATH)
        except OSError:
            mtime = None
        if mtime != self._moblist_mtime:
            self._moblist_mtime = mtime
            self.moblist = load_mob_list()
            print("[lista] wczytano %d wpisow z %s" % (len(self.moblist), MOBLIST_PATH))

    def on_click(self, e):
        """Klik na znacznik moba -> menu: dopisz do listy jako boss/special/normal albo usun."""
        near = [(math.hypot(sx - e.x, sy - e.y), vnum, o) for sx, sy, vnum, o in self._hits]
        near = [h for h in near if h[0] <= CLICK_RADIUS]
        if not near:
            return
        _, vnum, o = min(near, key=lambda h: h[0])
        name = self._mob_name(o) or "?"
        cur = self.moblist.get(vnum)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="%s  (vnum %d)%s" % (name, vnum, "  - na liscie: " + cur if cur else ""),
                      state="disabled")
        m.add_separator()
        if vnum not in self.proto:
            # vnum 0-7 to rasy POSTACI GRACZY - wpis oznaczylby wszystkich graczy tej rasy
            m.add_command(label="Gracz / brak w mob_proto - nie mozna dodac", state="disabled")
            m.tk_popup(e.x_root, e.y_root)
            return
        for cat, label in (("boss", "Boss (fiolet, z nazwa)"), ("special", "Specjalny (mieta)"),
                           ("normal", "Zwykly (wylacz auto-bossa)")):
            m.add_command(label=label, command=lambda c=cat: self._set_list_cat(vnum, c, name))
        if cur:
            m.add_command(label="Usun z listy", command=lambda: self._set_list_cat(vnum, None, name))
        m.tk_popup(e.x_root, e.y_root)

    def _set_list_cat(self, vnum, cat, name):
        set_mob_category(vnum, cat, name)
        print("[lista] %s %d -> %s" % (name, vnum, cat or "usuniety"))
        self._moblist_mtime = None        # wymus przeladowanie nawet przy tym samym mtime
        self._reload_moblist()

    def _classify(self, vnum):
        """'boss' | 'king' | 'special' | 'npc' | 'mob' - lista ma pierwszenstwo nad mob_proto."""
        mtype, rank = self.proto.get(vnum, (0, 0))
        cat = self.moblist.get(vnum)
        if cat == "boss":
            return "boss"
        if cat == "special":
            return "special"
        if mtype == 1:
            return "npc"
        if cat is None and mtype == 0 and rank >= BOSS_RANK:
            return "king"
        return "mob"

    def _label(self, o, vnum):
        """Nazwa nad znacznikiem; z [N] dopisany vnum (do uzupelniania data/radar_mobs.txt)."""
        nm = self._mob_name(o)
        if not nm:
            return str(vnum)
        return "%s %d" % (nm, vnum) if self.show_names else nm

    # ---------- pamiec ----------
    def _hero_world(self):
        if self.hero_obj is None:
            return None, None
        try:
            x = self.pm.read_float(self.hero_obj + HERO_POS)
            y = -self.pm.read_float(self.hero_obj + HERO_POS + 4)
            if math.isfinite(x) and math.isfinite(y):
                return x, y
        except Exception:
            pass
        return None, None

    def _cstr(self, addr, n=48, enc="ascii"):
        try:
            return self.pm.read_bytes(addr, n).split(b"\x00")[0].decode(enc, "ignore")
        except Exception:
            return ""

    def _std_string(self, a):
        try:
            cap = self.pm.read_uint(a + 0x14)
            if 16 <= cap < 1_000_000:
                return self._cstr(self.pm.read_uint(a))
            return self._cstr(a, 16)
        except Exception:
            return ""

    def _eff_fname(self, o):
        try:
            p = self.pm.read_uint(o + self.off["eff_data"])
        except Exception:
            return ""
        return self._std_string(p + self.off["eff_fname"])

    def _mob_name(self, o):
        a = o + self.off["name"]
        try:
            size = self.pm.read_uint(a + 16)
            cap = self.pm.read_uint(a + 20)
            if size > 48 or cap > 1_000_000:
                return ""
            # nazwy w kliencie sa w cp1250 (polskie znaki)
            if cap < 16:
                return self._cstr(a, min(size, 15), "cp1250")
            ptr = self.pm.read_uint(a)
            return self._cstr(ptr, min(size, 40), "cp1250")
        except Exception:
            return ""

    def rescan(self):
        hero, mapout, player, mobs, effs, metins = None, None, None, [], [], []
        player_vt = self.vt.get(PLAYER_CLASS)
        eff_x = self.off["eff_x"]
        hero_vt = self.vt[HERO_CLASS]
        mob_vt = self.vt[ACTOR_CLASS]
        eff_vt = self.vt[EFF_CLASS]
        mapout_vt = self.vt[MAPOUT_CLASS]
        for rb, rs in writable_regions(self.pm.process_handle):
            try:
                buf = self.pm.read_bytes(rb, rs)
            except Exception:
                continue
            u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
            if hero is None:
                hh = np.nonzero(u == hero_vt)[0]
                if len(hh):
                    hero = rb + int(hh[0]) * 4
            if mapout is None:
                hh = np.nonzero(u == mapout_vt)[0]
                if len(hh):
                    mapout = rb + int(hh[0]) * 4
            if player is None and player_vt:
                hh = np.nonzero(u == player_vt)[0]
                if len(hh):
                    player = rb + int(hh[0]) * 4
            for h in np.nonzero(u == mob_vt)[0]:
                mobs.append(rb + int(h) * 4)
            for h in np.nonzero(u == eff_vt)[0]:
                o = rb + int(h) * 4
                effs.append(o)
                fn = self._eff_fname(o)
                if fn and "metinstone" in fn.lower():
                    try:
                        mx = self.pm.read_float(o + eff_x)
                        my = -self.pm.read_float(o + eff_x + 4)
                    except Exception:
                        continue
                    if math.isfinite(mx) and WORLD_MIN < mx < WORLD_MAX:
                        metins.append((mx, my, "metin"))
        self.hero_obj = hero if hero is not None else self.hero_obj
        self.mapout_obj = mapout if mapout is not None else self.mapout_obj
        if player is not None and (player != self.player_obj or self.points_addr is None):
            off = autopot.find_points(self.pm, player)
            self.player_obj = player
            self.points_addr = player + off if off is not None else None
            print("[hp] tablica punktow: %s" % ("CPythonPlayer+0x%X" % off if off is not None
                                                 else "nie znaleziona"))
        self.mobs = mobs
        self.effs = effs
        self.metins = metins

    def _iter_mobs(self, hx, hy):
        """Yield (wx, wy, vnum, obj) dla aktorow z sensowna pozycja (bez samego gracza).
        Pomija niewidzialne typy z mob_proto i kamienie (metiny rysujemy z efektow)."""
        mob_x, vnum_off = self.off["mob_x"], self.off["vnum"]
        for o in self.mobs:
            try:
                x = self.pm.read_float(o + mob_x)
                y = self.pm.read_float(o + mob_x + 4)
                vnum = self.pm.read_uint(o + vnum_off)
            except Exception:
                continue
            if not (math.isfinite(x) and math.isfinite(y) and WORLD_MIN < x < WORLD_MAX):
                continue
            if 8000 <= vnum < 9000 or math.hypot(x - hx, y - hy) < 50:
                continue
            if self.proto.get(vnum, (0, 0))[0] in HIDDEN_TYPES:
                continue
            yield x, y, vnum, o

    # ---------- mapa ----------
    def _current_map_name(self):
        """Nazwa BIEZACEJ mapy z obiektu CMapOutdoor (std::string @+0x8).
        Dawna heurystyka (liczenie napisow atlas/minimap w pamieci) myli sie po teleporcie:
        zasoby starej mapy zostaja w pamieci i np. c1 i c3 maja identyczne liczniki."""
        if self.mapout_obj is None:
            return None
        return self._std_string(self.mapout_obj + MAPOUT_NAME) or None

    def _load_map(self, map_name, hx, hy):
        pack = map_assets._load_pack()
        if not map_name:
            map_name = self._current_map_name() or "metin2_map_t1"
            print("[map] biezaca mapa (CMapOutdoor): %s" % map_name)
        return map_assets.build_map_image(map_name, pack=pack)

    def _set_map(self, m):
        self.map = m

    def _on_resize(self, e):
        self.cw, self.ch = max(50, e.width), max(50, e.height)

    def _in_view(self, sx, sy, pad=20):
        return -pad < sx < self.cw + pad and -pad < sy < self.ch + pad

    # ---------- widok: swiat -> canvas ----------
    def _follow_scale(self):
        """px canvasa na px obrazu mapy; view_world = szerokosc swiata na KROTSZYM boku okna."""
        return min(self.cw, self.ch) / (self.view_world / map_assets.WORLD_PER_PX)

    def _full_scale(self):
        return min(self.cw / self.map.w_px, self.ch / self.map.h_px) * self.full_zoom

    def world_to_canvas(self, wx, wy, hx, hy):
        ix, iy = self.map.world_to_image(wx, wy)     # piksel pelnego obrazu
        if self.mode == "follow":
            hix, hiy = self.map.world_to_image(hx, hy)
            scale = self._follow_scale()
            return self.cw / 2 + (ix - hix) * scale, self.ch / 2 + (iy - hiy) * scale
        else:
            s = self._full_scale()
            return ix * s + self.pan_x, iy * s + self.pan_y

    def _bg_image(self, hx, hy):
        """Zwraca (PhotoImage, x, y) tla dopasowanego do aktualnego widoku."""
        if self.mode == "follow":
            hix, hiy = self.map.world_to_image(hx, hy)
            scale = self._follow_scale()
            hw, hh = self.cw / 2.0 / scale, self.ch / 2.0 / scale   # pol-wymiary w px obrazu
            # resize(box=...) nie przyjmuje ujemnych/poza obrazem -> najpierw crop na calkowitych
            # (poza mapa wypelnia czernia), potem resize z ulamkowym box wzgledem wycinka
            x0, y0 = math.floor(hix - hw), math.floor(hiy - hh)
            x1, y1 = math.ceil(hix + hw) + 1, math.ceil(hiy + hh) + 1
            part = self.map.image.crop((x0, y0, x1, y1))
            box = (hix - hw - x0, hiy - hh - y0, hix + hw - x0, hiy + hh - y0)
            crop = part.resize((self.cw, self.ch), Image.NEAREST, box=box)
            self._tkimg = ImageTk.PhotoImage(crop)
            return self._tkimg, 0, 0
        else:
            s = self._full_scale()
            w = max(1, int(self.map.w_px * s))
            h = max(1, int(self.map.h_px * s))
            im = self.map.image.resize((w, h), Image.BILINEAR)
            self._tkimg = ImageTk.PhotoImage(im)
            return self._tkimg, self.pan_x, self.pan_y

    # ---------- klawisze ----------
    def on_key(self, e):
        k = e.keysym.lower()
        if k == "f":
            self.mode = "full" if self.mode == "follow" else "follow"
        elif k in ("plus", "equal", "kp_add"):
            if self.mode == "follow":
                self.view_world = max(3000.0, self.view_world / 1.25)
            else:
                self.full_zoom *= 1.25
        elif k in ("minus", "kp_subtract"):
            if self.mode == "follow":
                self.view_world = min(self.map.w_px * map_assets.WORLD_PER_PX * 1.5, self.view_world * 1.25)
            else:
                self.full_zoom /= 1.25
        elif k == "y":
            self.map.flip_y = not self.map.flip_y
        elif k == "x":
            self.map.flip_x = not self.map.flip_x
        elif k == "n":
            self.show_names = not self.show_names
        elif k == "h":
            self.autopot_on = not self.autopot_on
            self.autopot_var.set(self.autopot_on)       # odbij w checkboxie
        elif k == "j":
            self.dll_status = self._inject_dll()
        elif k == "bracketleft":
            self.low_var.set(max(5, self.pot_low - 5))   # triggers _on_low (ustawia prog + clamp)
        elif k == "bracketright":
            self.low_var.set(min(95, self.pot_low + 5))
        elif k == "k":
            self.calibrate()
        elif k == "r":
            self.rescan()
        elif k == "m":
            self._set_map(map_assets.build_map_image(self.map.name, use_cache=False))
        elif k == "left":
            self.pan_x += 60
        elif k == "right":
            self.pan_x -= 60
        elif k == "up":
            self.pan_y += 60
        elif k == "down":
            self.pan_y -= 60
        elif k == "escape":
            self.root.destroy()

    # ---------- rysowanie ----------
    def tick(self):
        c = self.canvas
        c.delete("all")
        hx, hy = self._hero_world()
        if hx is None:
            c.create_text(self.cw / 2, self.ch / 2, fill="#ff6a6a",
                          text="szukam gracza... [R]", font=("Consolas", 11))
            self.root.after(200, self.tick)
            return

        img, ix, iy = self._bg_image(hx, hy)
        c.create_image(ix, iy, anchor="nw", image=img)

        # metiny
        for mx, my, _ in self.metins:
            sx, sy = self.world_to_canvas(mx, my, hx, hy)
            if self._in_view(sx, sy):
                c.create_rectangle(sx - 5, sy - 5, sx + 5, sy + 5, fill="#ffe14d", outline="#000")
                if self.show_names:
                    c.create_text(sx, sy - 10, fill="#ffe14d", text="metin", font=("Consolas", 8))

        # aktorzy: moby (czerwone), NPC (niebieskie), specjalne z listy (wieksze, mietowe),
        # bossy z listy / ranga 4+ (najwieksze, zawsze z nazwa)
        nmob = nnpc = nspec = nboss = 0
        bosses = []
        specials = []
        hits = []                                        # (sx, sy, vnum, obj) do klikania
        for mx, my, vnum, o in self._iter_mobs(hx, hy):
            sx, sy = self.world_to_canvas(mx, my, hx, hy)
            hits.append((sx, sy, vnum, o))
            cls = self._classify(vnum)
            if cls in ("boss", "king"):
                nboss += 1
                bosses.append((sx, sy, cls, vnum, o))    # rysowane na koncu, na wierzchu
                continue
            if cls == "special":
                nspec += 1
                specials.append((sx, sy, vnum, o))       # nad zwyklymi mobami, pod bossami
                continue
            if cls == "npc":
                nnpc += 1
                col = COL_NPC
            else:
                nmob += 1
                col = COL_MOB
            if self._in_view(sx, sy):
                c.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill=col, outline="#000")
                if self.show_names:
                    nm = self._label(o, vnum)
                    c.create_text(sx, sy - 9, fill=col, text=nm, font=("Consolas", 8))
        for sx, sy, vnum, o in specials:
            if self._in_view(sx, sy):
                c.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, fill=COL_SPECIAL, outline="#000", width=2)
                if self.show_names:
                    nm = self._label(o, vnum)
                    c.create_text(sx, sy - 11, fill=COL_SPECIAL, text=nm, font=("Consolas", 8))
        for sx, sy, cls, vnum, o in bosses:
            if self._in_view(sx, sy):
                col = COL_KING if cls == "king" else COL_BOSS
                c.create_oval(sx - 8, sy - 8, sx + 8, sy + 8, fill=col, outline="#fff", width=2)
                nm = self._label(o, vnum)
                c.create_text(sx + 1, sy - 15, fill="#000", text=nm, font=("Consolas", 9, "bold"))
                c.create_text(sx, sy - 16, fill=col, text=nm, font=("Consolas", 9, "bold"))
        self._hits = hits

        # gracz
        sx, sy = self.cw / 2, self.ch / 2
        if self.mode == "full":
            sx, sy = self.world_to_canvas(hx, hy, hx, hy)
        c.create_polygon(sx, sy - 9, sx - 7, sy + 7, sx + 7, sy + 7, fill="#39ff6a", outline="#000")

        lx = (hx - self.map.bx) / map_assets.WORLD_PER_PX
        ly = (hy - self.map.by) / map_assets.WORLD_PER_PX
        info = ("%s  tryb:%s  gracz lok:(%.0f,%.0f) swiat:(%.0f,%.0f)  moby:%d spec:%d npc:%d "
                "bossy:%d metiny:%d  flipY:%s flipX:%s   [F]tryb [+/-]zoom [Y][X]odbij [N]nazwy [R]skan"
                % (self.map.name, self.mode, lx, ly, hx, hy, nmob, nspec, nnpc, nboss,
                   len(self.metins), self.map.flip_y, self.map.flip_x))
        c.create_rectangle(0, self.ch - 20, self.cw, self.ch, fill="#0b0f0b", outline="")
        c.create_text(8, self.ch - 10, anchor="w", fill="#9fe7a6", text=info, font=("Consolas", 8))

        self._autopot_draw(c)
        self.root.after(120, self.tick)

    # ---------- auto-mikstura: szybka petla (decyzja) + rysowanie (z cache) ----------
    def _autopot_loop(self):
        """Dedykowana petla: czyta HP i pije co POTION_POLL (szybka reakcja, niezaleznie od rysowania,
        ktore chodzi co 120 ms). Dzieki temu lek leci zaraz po spadku HP, a nie raz na klatke/1.5 s."""
        while True:
            time.sleep(POTION_POLL)
            try:
                self._autopot_step()
            except Exception as e:
                print("[mikstura] blad petli:", e)

    def _autopot_step(self):
        """Jeden krok: odczyt HP, histereza (pij<low, lej do high), ewentualne picie. Zapisuje
        ostatnie HP do cache dla rysowania."""
        if self.points_addr is None:
            self._pts_valid = False
            return
        try:
            pts = autopot.read_points(self.pm, self.points_addr, 16)
        except Exception:
            return
        if not autopot.looks_like_points(pts):
            # np. przelogowanie postaci - porzucamy adres, rescan poszuka tablicy od nowa
            print("[hp] punkty przestaly pasowac - szukam tablicy ponownie")
            self.points_addr = None
            self._pts_valid = False
            return
        hp, mhp = pts[autopot.P_HP], pts[autopot.P_MAX_HP]
        self._hp, self._mhp, self._pct, self._pts_valid = hp, mhp, 100.0 * hp / mhp, True
        pct = self._pct
        # histereza: zacznij ponizej low, przestan dopiero po dojsciu do high
        if hp <= 0:                       # martwy - nie pijemy (i tak nie zadziala, marnuje mikstury)
            self._healing = False
            return
        if pct < self.pot_low:
            self._healing = True
        elif pct >= self.pot_high:
            self._healing = False
        if not (self.autopot_on and self._healing):
            return
        now = time.time()
        if now - self._last_pot < self.pot_cd:
            return
        slot = int(POTION_KEY) - 1 if POTION_KEY.isdigit() else 0   # "1" -> indeks 0
        via = None
        if self.pot_vnums:
            if autopot.drink_by_vnum(self.pot_vnums):      # skan EQ: ignoruje pasek, bez fokusu
                via = "EQ"
        elif self.pipe.drink(slot):                        # slot paska przez DLL, bez fokusu
            via = "slot"
        if via is None and self.hwnd and autopot.press_key(self.hwnd, POTION_KEY):  # zapas: fokus
            via = "klawisz"
        if via:
            self._last_pot = now
            self.pot_count += 1
            print("[mikstura] HP %d/%d (%.0f%%) -> %s" % (hp, mhp, pct, via))

    def _autopot_draw(self, c):
        """Pasek HP z dwiema liniami (pij<low zolta, lecz do high niebieska) + status. Z cache."""
        if not self._pts_valid or self._mhp <= 0:
            return
        hp, mhp, pct = self._hp, self._mhp, self._pct
        w = 180
        c.create_rectangle(2, 2, 560, 41, fill="#0b0f0b", outline="")     # tlo dla czytelnosci
        c.create_rectangle(8, 8, 8 + w, 22, fill="#300", outline="#000")
        c.create_rectangle(8, 8, 8 + w * max(0.0, min(1.0, hp / mhp)), 22, fill="#d22", outline="")
        c.create_line(8 + w * self.pot_low / 100.0, 6, 8 + w * self.pot_low / 100.0, 24,
                      fill="#ffe14d", width=2)                            # pij ponizej
        c.create_line(8 + w * self.pot_high / 100.0, 6, 8 + w * self.pot_high / 100.0, 24,
                      fill="#4da6ff", width=2)                            # lecz do
        c.create_text(8 + w / 2, 15, fill="#fff", text="HP %d/%d (%.0f%%)" % (hp, mhp, pct),
                      font=("Consolas", 8, "bold"))
        method = ("EQ vnum %s" % ",".join(map(str, self.pot_vnums))) if self.pot_vnums \
            else "slot %s" % POTION_KEY
        mode = "bez fokusu" if self.dll_status.startswith("wstrzyk") else "wymaga fokusu"
        state = ("WL  uzyto:%d" % self.pot_count) if self.autopot_on else "WYL"
        if self._healing:
            state += " [leje]"
        c.create_text(8, 33, anchor="w", fill="#ffe14d" if self.autopot_on else "#999",
                      text="mikstura %s  pij<%d%% lecz>%d%% co%.2fs  %s  %s  [J]DLL:%s"
                           % (method, self.pot_low, self.pot_high, self.pot_cd, mode, state,
                              self.dll_status),
                      font=("Consolas", 8))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Radar z obrazem mapy (Metin2).")
    p.add_argument("map_name", nargs="?", default=None,
                   help="wymuszona mapa, np. metin2_map_a1 (domyslnie: biezaca mapa gracza)")
    # "-exe" jako alias: bez niego argparse czyta "-exe X" jako "-e xe" + map_name=X
    p.add_argument("-e", "--exe", "-exe", default=DEFAULT_PROCESS, metavar="EXE",
                   help="nazwa procesu gry (domyslnie: %(default)s)")
    args = p.parse_args(argv)
    if not args.exe.lower().endswith(".exe"):   # "-e vCore" == "-e vCore.exe"
        args.exe += ".exe"
    return args


def main():
    args = parse_args()
    try:
        root = tk.Tk()
    except Exception as e:
        print("Blad tkinter:", e)
        return
    root.title("Metin2 Radar - MAPA (%s)" % args.exe)
    try:
        RadarMap(root, args.map_name, args.exe)
    except pymem.exception.ProcessNotFound:
        print("[!] Nie znalazlem procesu %s. Gra wlaczona? (inna nazwa -> -e NAZWA.exe)" % args.exe)
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return
    except LookupError as e:
        print("[!] %s (inny exe? klasa przemianowana albo RTTI wylaczone)" % e)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
