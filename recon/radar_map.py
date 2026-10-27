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
    Panel POD radarem: Pij <% (start), Lecz do % (cel), Odstep s (min. czas), Auto-loot + Loot co s.
    Okno zapamietuje pozycje i rozmiar (data/ui_state.json) - otwiera sie tam, gdzie zostalo zamkniete.
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
import json
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
import collision as collision_mod
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
COL_PLAYER = "#ffffff"      # inni gracze
# WYKRYWANIE GRACZA: po SCIEZCE MODELU, nie po m_eActorType. W tym kliencie "vnum" (-0x70) lezy
# w obiekcie ZAWIERAJACYM, a nie w CActorInstance, wiec m_eActorType != vnum-4 (sonda na zywo:
# NPC/gracz/moby czytaja 0 w calym oknie -> pole niewiarygodne tu). Pewny sygnal: gracz ma model
# "d:/ymir work/pc/<klasa>/..." (moby: /monster/, NPC: /npc/). Model czytamy z CRaceData (race ->
# race_model), tak samo jak kalibracja vnum/nazwy. Patrz recon/probe_actortype.py.
PC_MODEL_MARKS = ("/pc/", "/pc2/")
MAPOUT_NAME = 0x8
WORLD_MIN, WORLD_MAX = 1.0, 10_000_000.0

CANVAS = 720
# Zapamietywanie pozycji+rozmiaru okna miedzy sesjami (data/ui_state.json) + domyslny "wysoki prostokat".
UI_STATE_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "data", "ui_state.json"))
DEFAULT_GEOMETRY = "470x900"   # szerokosc x wysokosc, gdy brak zapisanej geometrii


def load_ui_geometry():
    """Zapisana geometria okna w formacie 'WxH+X+Y' albo None."""
    try:
        with open(UI_STATE_PATH, encoding="utf-8") as f:
            g = json.load(f).get("geometry")
        return g if isinstance(g, str) and g else None
    except Exception:
        return None


def save_ui_geometry(geo):
    """Zapisz geometrie okna do pliku stanu UI (ciche niepowodzenie)."""
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"geometry": geo}, f)
    except Exception:
        pass


RESCAN_SEC = 1.5    # okres skanu pamieci w tle (nowe moby/metiny); sam skan ~0.2 s
# Auto-mikstura (recon/autopot.py): HP z CPythonPlayer, "nacisniecie" slotu w oknie gry.
PLAYER_CLASS = "CPythonPlayer"
POTION_KEY = "1"          # slot paska z czerwonymi miksturami (gdy nie uzywamy vnum)
# Histereza: zaczynamy pic ponizej LOW, lejemy az HP dojdzie do HIGH (HIGH >= LOW).
POTION_LOW = 50           # % HP - zacznij pic ponizej (suwak "Pij <", klawisze [ / ])
POTION_HIGH = 75          # % HP - lej az do tego poziomu (suwak "Lecz do")
POTION_COOLDOWN = 0.3     # s - min. odstep miedzy miksturami (suwak "Odstep")
POTION_POLL = 0.07        # s - jak czesto dedykowana petla sprawdza HP (szybka reakcja, nie co klatke)
# Auto-loot (MVP): co LOOT_INTERVAL s wolamy player.PickCloseItem() przez potok DLL - klient
# podnosi najblizszy drop w zasiegu (<=300). Dziala tam, gdzie pending-calls sa opruzniane
# (GameCoreTop). Bez detekcji w pamieci i bez filtra vnum - to prosta wersja na start.
LOOT_INTERVAL = 0.5       # s - odstep miedzy probami podniesienia (suwak "Loot co s")
# Auto-chodzenie (funkcja 3): postac idzie po liscie waypointow (wsp. swiata). Ruch natywny przez
# hook per-klatka (vCore): DLL wola NEW_MoveToDestPixelPositionDirection. Pakiety ruchu leca same.
WALK_REACH = 150.0        # jedn. swiata: tak blisko celu = waypoint osiagniety -> nastepny
WALK_RESEND = 0.5         # s: co ile ponawiac !goto do biezacego celu (keep-alive/korekta)
WALK_STUCK_SEC = 1.5      # s: brak postepu tyle -> wejdz w tryb omijania przeszkody
# Omijanie przeszkod bez mapy kolizji (podazanie wzdluz sciany): przy utknieciu kierujemy postac pod
# katem do celu (60/90/120/150 stopni w bok) i trzymamy ten kurs; gdy postac SIE NIE RUSZA, obracamy
# kurs dalej, a gdy wyczerpiemy katy - zmieniamy strone. Konczymy, gdy zblizymy sie do celu bardziej
# niz w chwili utkniecia; poddajemy sie (pomijamy waypoint) po WALK_GIVEUP s lub 2 zmianach strony.
WALK_PLAN_SEC = 1.5       # s: co ile przeliczac trase A* do biezacego waypointu (replan)
WALK_STUCK_REPLAN = 2.0   # s: brak postepu tyle -> wymus replan trasy
# Speedhack (EKSPERYMENT - test czy serwer waliduje; patrz ANALIZA): m_fMovSpd/m_fAtkSpd to mnozniki
# animacji w CActorInstance (1.0=100%). Serwer nadpisuje je przy char_update -> re-aplikujemy co petle.
# Offsety WZGLEDEM wskaznika CActorInstance (ten sam co w self.mobs) - PER BUILD. GameCoreTop zweryf. na zywo.
SPEED_OFFSETS = {
    # Efekt zweryfikowany W GRZE (odwrotnie niz statyczne nazwy subagenta): +0x498 = RUCH, +0x49C = ATAK.
    "gamecoretop.exe": dict(mov=0x498, atk=0x49C),
    # vCore: do uzupelnienia (inny build).
}
# Auto-farm: wpisujemy VID moba do CPythonPlayer.m_dwAutoAttackTargetVID (off "autoatk" wzgl. obiektu
# CPythonPlayer = self.player_obj); klient (__Update_AutoAttack) sam podchodzi, bije i czysci po smierci.
# VID moba czytamy z CActorInstance (off "vid" wzgl. wskaznika z self.mobs). Offsety PER BUILD (do RE).
FARM_OFFSETS = {
    # zweryfikowane na zywo: VID moba = CActorInstance+0x558; auto-attack target = CPythonPlayer+0x50.
    "gamecoretop.exe": dict(vid=0x558, autoatk=0x50),
    # vCore: do uzupelnienia (inny build).
}
FARM_RADIUS = 2500.0      # domyslny promien okregu farmienia (jedn. swiata; ~25 px minimapy)
# Stabilny odczyt HP przez wskaznik singletona CPythonPlayer - dla buildow bez RTTI, gdzie skan
# pamieci zawodzi (np. vCore: tablica 8-bajtowa, dolny dword XOR). RVA/offsety sa SPECYFICZNE DLA
# DANEGO BUILDU exe (znalezione analiza binarki). value(i)=*(base+singleton_rva)+array_off+i*stride ^key.
POINT_PTR_CLIENTS = {
    # UWAGA: vCore przekompilowany (2026-10-27) -> singleton CPythonPlayer przesunięty 0x1B8C664 ->
    # 0x1B8D674 (zweryfikowane na żywo: HP/maxHP/level sensowne). array_off 0x1A668 bez zmian.
    "vcore.exe": dict(singleton_rva=0x1B8D674, array_off=0x1A668, stride=8, key=0xE73AC1DA),
}
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
    """{vnum: (type, rank, level)} z mob_proto.txt; pusty gdy brak pliku (wszystko = mob).
    Kolumny: VNUM=0, RANK=2, TYPE=3, LEVEL=5 (naglowek mob_proto)."""
    proto = {}
    try:
        with open(os.path.join(PROTO_DIR, "mob_proto.txt"), encoding="latin-1") as f:
            next(f)
            for line in f:
                c = line.split("\t")
                if len(c) > 5 and c[0].isdigit():
                    proto[int(c[0])] = (int(c[3] or 0), int(c[2] or 0), int(c[5] or 0))
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
        self.proc_name = process
        mod = pymem.process.module_from_name(self.pm.process_handle, process)
        self.base = mod.lpBaseOfDll
        self.mod_lo = mod.lpBaseOfDll
        self.mod_hi = mod.lpBaseOfDll + mod.SizeOfImage

        self.hero_obj = None
        self.mapout_obj = None    # CMapOutdoor
        self.fixed_map = bool(map_name)   # mapa podana recznie -> bez auto-przelaczania
        self.mobs = []            # [obj]
        self.pc_objs = set()      # aktorzy z modelem gracza (/pc/) - inni gracze; liczone w rescan
        self.metin_actors = []    # [(vnum, nazwa, x, y)] aktorzy metinow (vnum 8xxx) - do etykiet metinow
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
        try:   # preferowane: obiekt gracza po RTTI. Brak (np. vCore) -> HP szukamy skanem ksztaltu
            self.vt.update(rtti.find_vtables(self.pm, self.base, mod.SizeOfImage, [PLAYER_CLASS]))
        except LookupError as e:
            print("[hp] %s - sprobuje znalezc HP skanem bez RTTI" % e)

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
        self._hp_scan_warned = False              # zeby nie spamowac logiem co rescan
        # odczyt HP przez stabilny wskaznik (jesli ten build jest znany) - zamiast skanu
        self.pt_ptr = POINT_PTR_CLIENTS.get(self.proc_name.lower())
        if self.pt_ptr:
            print("[hp] %s: odczyt HP przez wskaznik singletona (bez skanu)" % self.proc_name)
        # picie bez fokusu: wstrzykujemy autopot.dll i gadamy z nim przez potok (recon/autopot.py).
        # Gdy sie nie uda (brak DLL / nie zbudowany) - zostaje stara sciezka SendInput (tylko z fokusem).
        self.pipe = autopot.PipeDrinker(self.pm.process_id)   # potok per-instancja (po PID)
        self.dll_status = self._inject_dll()
        # vnum mikstur -> picie wprost z EQ; pusta lista -> picie slotem paska
        self.pot_vnums = []
        self._pot_vnums_mtime = None
        self._reload_pot_vnums()
        # auto-loot (MVP): podnoszenie najblizszego dropu co loot_interval (tylko gdy DLL wykonuje
        # Python = GameCoreTop). Bez detekcji/filtra - prosta wersja na start.
        self.autoloot_on = False
        self.loot_interval = LOOT_INTERVAL
        self._last_loot = 0.0
        self.loot_count = 0
        # auto-chodzenie (funkcja 3): waypointy w wsp. swiata; petla idzie po nich (tylko vCore/hook).
        # Ctrl+LPM dodaje waypoint, [C] czysci, [G]/checkbox start-stop.
        self.autowalk_on = False
        self.path = []                # [(wx, wy)] - waypointy uzytkownika (Ctrl+klik)
        self.walk_idx = 0
        self._last_goto = 0.0
        self._walk_prog = None        # (czas, dist) - do wykrywania utkniecia (wymusza replan)
        # omijanie przeszkod po REALNEJ siatce kolizji (CMapOutdoor attr) + pathfinding A*
        self.collision = collision_mod.Collision(self.pm, self.proc_name)
        self.show_collision = True    # rysuj zablokowane pola na radarze ([B] przelacza)
        self._route = []              # aktualna trasa A* do biezacego waypointu [(wx,wy)]
        self._route_idx = 0
        self._last_plan = 0.0
        self._coll_cache = (0.0, [])  # (czas, lista (wx,wy) zablokowanych pol w poblizu) - do rysowania
        # speedhack (eksperyment): zapis mnoznikow predkosci do wlasnej postaci (CActorInstance)
        self.speed_off = SPEED_OFFSETS.get(self.proc_name.lower())
        self.speed_on = False
        self.move_mult = 1.0
        self.atk_mult = 1.0
        self.self_actor = None        # wskaznik CActorInstance wlasnej postaci (do zapisu predkosci)
        # auto-farm: okrag wokol punktu startu, bijemy moby w srodku (wpis VID do auto-attack target)
        self.farm_off = FARM_OFFSETS.get(self.proc_name.lower())
        self.farm_on = False
        self.farm_radius = FARM_RADIUS
        self.farm_center = None        # (wx, wy) - srodek okregu (ustawiany przy wlaczeniu)
        self._last_farm = 0.0

        self.root = root
        # Przywroc pozycje i rozmiar okna z poprzedniej sesji (albo domyslny wysoki prostokat).
        geo = load_ui_geometry()
        try:
            root.geometry(geo if geo else DEFAULT_GEOMETRY)
        except Exception:
            root.geometry(DEFAULT_GEOMETRY)
        root.protocol("WM_DELETE_WINDOW", self._on_close)   # zapisz geometrie przy zamknieciu

        # UKLAD: RADAR na gorze (wypelnia), PANEL sterowania POD radarem (wysoki prostokat).
        # Panel: Auto-mikstura (+3 suwaki) i Auto-loot (+suwak) jeden pod drugim (grid, 2 kolumny).
        # Klawisze [H] [ / ] [L] dzialaja dalej i sa zsynchronizowane; zmienne tk to "lustro" -
        # zrodlem prawdy zostaja self.autopot_on / pot_low / pot_high / pot_cd / autoloot_on / loot_interval.
        self.autopot_var = tk.BooleanVar(value=self.autopot_on)
        self.low_var = tk.IntVar(value=self.pot_low)
        self.high_var = tk.IntVar(value=self.pot_high)
        self.cd_var = tk.DoubleVar(value=self.pot_cd)
        self.autoloot_var = tk.BooleanVar(value=self.autoloot_on)
        self.loot_var = tk.DoubleVar(value=self.loot_interval)
        self.autowalk_var = tk.BooleanVar(value=self.autowalk_on)
        self.speed_var = tk.BooleanVar(value=self.speed_on)
        self.movx_var = tk.DoubleVar(value=self.move_mult)
        self.atkx_var = tk.DoubleVar(value=self.atk_mult)
        self.farm_var = tk.BooleanVar(value=self.farm_on)
        self.farmr_var = tk.IntVar(value=int(self.farm_radius))

        self.canvas = tk.Canvas(root, width=CANVAS, height=CANVAS, bg="#0b0f0b", highlightthickness=0)
        panel = tk.Frame(root, bg="#0b0f0b")
        panel.columnconfigure(1, weight=1)                  # suwaki rozciagaja sie na szerokosc okna
        self._panel_row = 0

        def _chk(text, var, cmd, fg, afg):
            tk.Checkbutton(panel, text=text, variable=var, command=cmd, bg="#0b0f0b", fg=fg,
                           selectcolor="#1b2a1b", activebackground="#0b0f0b", activeforeground=afg,
                           font=("Consolas", 9)).grid(row=self._panel_row, column=0, columnspan=2,
                                                       sticky="w", padx=6, pady=(4, 0))
            self._panel_row += 1

        def _mkscale(lbl, var, lo, hi, res, cmd, fg):
            tk.Label(panel, text=lbl, bg="#0b0f0b", fg="#9fe7a6", font=("Consolas", 9)).grid(
                row=self._panel_row, column=0, sticky="w", padx=6)
            tk.Scale(panel, from_=lo, to=hi, resolution=res, orient="horizontal",
                     variable=var, command=cmd, bg="#0b0f0b", fg=fg, troughcolor="#222",
                     highlightthickness=0, font=("Consolas", 8)).grid(
                row=self._panel_row, column=1, sticky="ew", padx=(2, 6))
            self._panel_row += 1

        _chk("Auto-mikstura", self.autopot_var, self._on_autopot_check, "#9fe7a6", "#cdebcf")
        _mkscale("Pij <%", self.low_var, 5, 95, 1, self._on_low, "#ffe14d")
        _mkscale("Lecz do %", self.high_var, 5, 99, 1, self._on_high, "#4da6ff")
        _mkscale("Odstep s", self.cd_var, 0.1, 1.0, 0.05, self._on_cd, "#cdebcf")
        _chk("Auto-loot", self.autoloot_var, self._on_autoloot_check, "#e9d5a0", "#f5e6bf")
        _mkscale("Loot co s", self.loot_var, 0.2, 2.0, 0.1, self._on_loot_cd, "#e9d5a0")
        _chk("Auto-chodzenie (Ctrl+klik=punkt, C=czysc)", self.autowalk_var,
             self._on_autowalk_check, "#9fd0ff", "#cfe8ff")
        _chk("Auto-farm (okrag wokol postaci)", self.farm_var, self._on_farm_check, "#ff6a6a", "#ffb0b0")
        _mkscale("Promien", self.farmr_var, 500, 6000, 100, self._on_farmr, "#ff6a6a")
        _chk("Speedhack (test - serwer moze ucinac)", self.speed_var,
             self._on_speed_check, "#ff9f1a", "#ffd79a")
        _mkscale("Ruch x", self.movx_var, 1.0, 3.0, 0.1, self._on_movx, "#ff9f1a")
        _mkscale("Atak x", self.atkx_var, 1.0, 3.0, 0.1, self._on_atkx, "#ff9f1a")

        # Kolejnosc pakowania wazna: PANEL na dole, canvas wypelnia reszte NAD nim.
        panel.pack(side="bottom", fill="x")
        self.canvas.pack(side="top", fill="both", expand=True)
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

    def _on_autoloot_check(self):
        self.autoloot_on = bool(self.autoloot_var.get())

    def _on_loot_cd(self, val):
        self.loot_interval = float(val)

    def _on_farm_check(self):
        self.farm_on = bool(self.farm_var.get())
        if self.farm_on:
            hx, hy = self._hero_world()
            self.farm_center = (hx, hy) if hx is not None else None   # srodek = pozycja przy wlaczeniu
            self._last_farm = 0.0
        else:
            self.farm_center = None

    def _on_farmr(self, val):
        self.farm_radius = float(val)

    def _on_speed_check(self):
        self.speed_on = bool(self.speed_var.get())

    def _on_movx(self, val):
        self.move_mult = float(val)

    def _on_atkx(self, val):
        self.atk_mult = float(val)

    def _on_autowalk_check(self):
        self.autowalk_on = bool(self.autowalk_var.get())
        if self.autowalk_on:
            self.walk_idx = 0
            self._walk_prog = None
            self._route = []
            self._last_plan = 0.0

    def _on_close(self):
        """Zapisz pozycje i rozmiar okna (by otworzyc sie tam samo), potem zamknij."""
        try:
            save_ui_geometry(self.root.geometry())
        except Exception:
            pass
        self.root.destroy()

    def _inject_dll(self):
        """Wstrzykuje autopot.dll (picie bez fokusu) i sprawdza, czy FAKTYCZNIE wykonuje komendy.
        Niektore klienty (vCore) nie opruzniaja kolejki pending-calls - wtedy picie przez DLL nie
        zadziala i trzeba klawiszem (z fokusem). Ustawia self.dll_exec. Zwraca opis stanu."""
        try:
            ok, msg = autopot.ensure_injected(self.pm.process_id)
        except Exception as e:
            ok, msg = False, str(e)
        print("[dll] %s" % msg)
        self.dll_exec = bool(ok) and autopot.dll_executes(self.pm.process_id)
        # hook per-klatka (vCore): picie bez fokusu natywnym wywolaniem, mimo ze pending-call nie dziala
        self.native_drink = bool(ok) and autopot.hook_ready(self.pm.process_id)
        print("[dll] exec=%s hook(natywne picie bez fokusu)=%s"
              % (self.dll_exec, self.native_drink))
        if not ok:
            return "brak (%s)" % msg
        if self.dll_exec:
            return "wstrzyknieta, exec"
        return "wstrzyknieta, hook" if self.native_drink else "wstrzyknieta, BEZ exec"

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
        """Ctrl+LPM -> dodaj waypoint (auto-chodzenie). Zwykly LPM na mobie -> menu listy."""
        if e.state & 0x0004:                         # Control wcisniety -> dodaj waypoint
            hx, hy = self._hero_world()
            if hx is None:
                return
            wx, wy = self.canvas_to_world(e.x, e.y, hx, hy)
            self.path.append((wx, wy))
            print("[walk] waypoint %d: (%.0f, %.0f)" % (len(self.path), wx, wy))
            return
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

    def _classify(self, vnum, is_pc=False):
        """'player' | 'boss' | 'king' | 'special' | 'npc' | 'mob'.
        Inny gracz (model /pc/) ma pierwszenstwo; dalej lista nad mob_proto."""
        if is_pc:
            return "player"
        mtype, rank = self.proto.get(vnum, (0, 0, 0))[:2]
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

    def _fmt_metin(self, name, vnum):
        """Etykieta metina: nazwa BEZ slowa 'Metin' + poziom z proto. 'Metin Walki' -> 'Walki 10'."""
        nm = (name or "").strip()
        if nm.lower().startswith("metin"):
            nm = nm[5:].lstrip(" -–—")      # usun 'Metin' i separator
        lvl = self.proto.get(vnum, (0, 0, 0))[2]
        if not nm:
            nm = str(vnum) if vnum else "metin"
        return "%s %d" % (nm, lvl) if lvl else nm

    def _metin_label(self, mx, my):
        """Nazwa+poziom dla metina w (mx,my). Gdy na mapie 1 typ metina - ten dla wszystkich;
        gdy wiele - najblizszy aktor metina po pozycji. Brak aktorow -> 'metin'."""
        acts = self.metin_actors
        if not acts:
            return "metin"
        if len(set(a[0] for a in acts)) == 1:
            return self._fmt_metin(acts[0][1], acts[0][0])
        best = None
        for vnum, name, x, y in acts:
            if x is None:
                continue
            d = math.hypot(x - mx, y - my)
            if best is None or d < best[0]:
                best = (d, name, vnum)
        if best is None:
            return self._fmt_metin(acts[0][1], acts[0][0])
        return self._fmt_metin(best[1], best[2])

    def _actor_model(self, o):
        """Sciezka modelu aktora (".../pc|monster|npc/<folder>/x.gr2") z CRaceData; '' gdy brak."""
        try:
            rp = self.pm.read_uint(o + self.off["race"])
        except Exception:
            return ""
        if not (0x10000 < rp < 0x7FFF0000):
            return ""
        return self._std_string(rp + self.off["race_model"]).lower().replace("\\", "/")

    def _actor_is_pc(self, o):
        """True gdy aktor to inny GRACZ (model w /pc/). Pewniejsze niz m_eActorType w tym kliencie."""
        m = self._actor_model(o)
        return bool(m) and any(mark in m for mark in PC_MODEL_MARKS)

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
        # skan tablicy tylko gdy brak RTTI CPythonPlayer I nie mamy wskaznika singletona dla tego buildu
        need_pts_scan = player_vt is None and self.points_addr is None and self.pt_ptr is None
        pts_cand = (None, -1)
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
            if need_pts_scan:
                # najpierw plain (nieszyfrowane, np. vCore), potem XOR-run (inne buildy bez RTTI);
                # trzymamy najLEPSZEGO kandydata ze WSZYSTKICH regionow (najwyzszy wynik gestosci)
                a, rep = autopot.scan_points_plain(u, rb)
                if a is None:
                    a, rep = autopot.scan_points_block(u, rb)
                if a is not None and rep > pts_cand[1]:
                    pts_cand = (a, rep)
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
        if self.mapout_obj:
            self.collision.set_mapout(self.mapout_obj)
        if player is not None and (player != self.player_obj or self.points_addr is None):
            off = autopot.find_points(self.pm, player)
            self.player_obj = player
            self.points_addr = player + off if off is not None else None
            print("[hp] tablica punktow: %s" % ("CPythonPlayer+0x%X" % off if off is not None
                                                 else "nie znaleziona"))
        elif need_pts_scan and pts_cand[0] is not None:
            self.points_addr = pts_cand[0]
            self._hp_scan_warned = False
            print("[hp] tablica punktow (skan bez RTTI): 0x%X (powt. klucza %d)"
                  % (pts_cand[0], pts_cand[1]))
        elif need_pts_scan and not self._hp_scan_warned:
            self._hp_scan_warned = True
            print("[hp] skan bez RTTI nie znalazl tablicy punktow (inny uklad POINT_?). "
                  "Diagnoza z gry: python recon/find_hp.py -e %s --hp <HP> --maxhp <maxHP>"
                  % self.proc_name.replace(".exe", ""))
        self.mobs = mobs
        # inni gracze: aktorzy z modelem /pc/ (liczone tu, raz na skan - nie co klatke)
        self.pc_objs = set(o for o in mobs if self._actor_is_pc(o))
        # wlasna postac (do speedhacka): aktor najblizszy graczowi (ten sam, ktory _iter_mobs odfiltrowuje)
        self.self_actor = None
        hx0, hy0 = self._hero_world()
        if hx0 is not None:
            mob_x = self.off["mob_x"]
            bd, best = 1e18, None
            for o in mobs:
                try:
                    x = self.pm.read_float(o + mob_x)
                    y = self.pm.read_float(o + mob_x + 4)
                except Exception:
                    continue
                dd = math.hypot(x - hx0, y - hy0)
                if dd < bd:
                    bd, best = dd, o
            if best is not None and bd < 100:
                self.self_actor = best
        # aktorzy metinow (vnum 8xxx): zrodlo NAZWY/POZIOMU dla metinow rysowanych z efektow.
        # (ich pozycja bywa niestabilna - sluzy tylko do dopasowania, gdy typow jest kilka)
        metin_actors = []
        for o in mobs:
            try:
                vnum = self.pm.read_uint(o + self.off["vnum"])
            except Exception:
                continue
            if 8000 <= vnum < 9000:
                try:
                    ax = self.pm.read_float(o + self.off["mob_x"])
                    ay = self.pm.read_float(o + self.off["mob_x"] + 4)
                    if not (math.isfinite(ax) and math.isfinite(ay)):
                        ax = ay = None
                except Exception:
                    ax = ay = None
                metin_actors.append((vnum, self._mob_name(o), ax, ay))
        self.metin_actors = metin_actors
        self.effs = effs
        self.metins = metins

    def _iter_mobs(self, hx, hy):
        """Yield (wx, wy, vnum, is_pc, obj) dla aktorow z sensowna pozycja (bez samego gracza).
        is_pc = aktor to inny gracz (model /pc/, z self.pc_objs liczonego w rescan).
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
            is_pc = o in self.pc_objs
            # Gracz NIE podlega filtrowi HIDDEN_TYPES (jego vnum = rasa, bywa w proto).
            if not is_pc and self.proto.get(vnum, (0, 0, 0))[0] in HIDDEN_TYPES:
                continue
            yield x, y, vnum, is_pc, o

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

    def canvas_to_world(self, sx, sy, hx, hy):
        """Odwrotnosc world_to_canvas: punkt na canvasie -> wspolrzedne swiata (do waypointow)."""
        m = self.map
        if self.mode == "follow":
            hix, hiy = m.world_to_image(hx, hy)
            scale = self._follow_scale()
            ix = hix + (sx - self.cw / 2) / scale
            iy = hiy + (sy - self.ch / 2) / scale
        else:
            s = self._full_scale()
            ix = (sx - self.pan_x) / s
            iy = (sy - self.pan_y) / s
        lx = (m.w_px - ix) if m.flip_x else ix       # odwrocenie flipow z world_to_image
        ly = (m.h_px - iy) if m.flip_y else iy
        return lx * map_assets.WORLD_PER_PX + m.bx, ly * map_assets.WORLD_PER_PX + m.by

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
        elif k == "l":
            self.autoloot_on = not self.autoloot_on
            self.autoloot_var.set(self.autoloot_on)     # odbij w checkboxie
        elif k == "g":
            self.autowalk_on = not self.autowalk_on
            self.autowalk_var.set(self.autowalk_on)     # start/stop auto-chodzenia
            if self.autowalk_on:
                self.walk_idx = 0
                self._walk_prog = None
                self._route = []
                self._last_plan = 0.0
        elif k == "c":
            self.path = []                              # wyczysc waypointy
            self.walk_idx = 0
            self._walk_prog = None
            self._route = []
            print("[walk] trasa wyczyszczona")
        elif k == "b":
            self.show_collision = not self.show_collision   # siatka kolizji wl/wyl
        elif k == "v":
            self.farm_on = not self.farm_on                 # auto-farm wl/wyl
            self.farm_var.set(self.farm_on)
            if self.farm_on:
                hx, hy = self._hero_world()
                self.farm_center = (hx, hy) if hx is not None else None
                self._last_farm = 0.0
            else:
                self.farm_center = None
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
            self._on_close()

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
                c.create_text(sx, sy - 10, fill="#ffe14d", text=self._metin_label(mx, my),
                              font=("Consolas", 8))

        # aktorzy: moby (czerwone), NPC (niebieskie), specjalne z listy (wieksze, mietowe),
        # bossy z listy / ranga 4+ (najwieksze, zawsze z nazwa)
        nmob = nnpc = nspec = nboss = nplayer = 0
        bosses = []
        specials = []
        players = []
        hits = []                                        # (sx, sy, vnum, obj) do klikania
        for mx, my, vnum, is_pc, o in self._iter_mobs(hx, hy):
            sx, sy = self.world_to_canvas(mx, my, hx, hy)
            hits.append((sx, sy, vnum, o))
            cls = self._classify(vnum, is_pc)
            if cls == "player":
                nplayer += 1
                players.append((sx, sy, vnum, o))        # rysowani nad mobami, pod bossami
                continue
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
        for sx, sy, vnum, o in players:
            if self._in_view(sx, sy):
                c.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, fill=COL_PLAYER, outline="#000", width=2)
                nm = self._label(o, vnum)
                c.create_text(sx + 1, sy - 10, fill="#000", text=nm, font=("Consolas", 8, "bold"))
                c.create_text(sx, sy - 11, fill=COL_PLAYER, text=nm, font=("Consolas", 8, "bold"))
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
        self._draw_collision(c, hx, hy)                   # siatka kolizji (pod trasa i graczem)
        self._draw_farm(c, hx, hy)                        # okrag auto-farm
        self._draw_path(c, hx, hy)                        # trasa auto-chodzenia (pod graczem)

        # gracz
        sx, sy = self.cw / 2, self.ch / 2
        if self.mode == "full":
            sx, sy = self.world_to_canvas(hx, hy, hx, hy)
        c.create_polygon(sx, sy - 9, sx - 7, sy + 7, sx + 7, sy + 7, fill="#39ff6a", outline="#000")

        lx = (hx - self.map.bx) / map_assets.WORLD_PER_PX
        ly = (hy - self.map.by) / map_assets.WORLD_PER_PX
        info = ("%s  tryb:%s  gracz lok:(%.0f,%.0f) swiat:(%.0f,%.0f)  gracze:%d moby:%d spec:%d npc:%d "
                "bossy:%d metiny:%d  flipY:%s flipX:%s   [F]tryb [+/-]zoom [Y][X]odbij [N]nazwy [R]skan"
                % (self.map.name, self.mode, lx, ly, hx, hy, nplayer, nmob, nspec, nnpc, nboss,
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
            try:
                self._autoloot_step()
            except Exception as e:
                print("[loot] blad petli:", e)
            try:
                self._autowalk_step()
            except Exception as e:
                print("[walk] blad petli:", e)
            try:
                self._speed_step()
            except Exception as e:
                print("[speed] blad petli:", e)
            try:
                self._farm_step()
            except Exception as e:
                print("[farm] blad petli:", e)

    def _farm_step(self):
        """Auto-farm: jesli nie ma aktywnego celu auto-ataku, wybiera najblizszego moba w okregu i wpisuje
        jego VID do CPythonPlayer.m_dwAutoAttackTargetVID - klient sam podchodzi, bije i czysci po smierci."""
        if not self.farm_on or not self.farm_off or self.player_obj is None or self.farm_center is None:
            return
        now = time.time()
        if now - self._last_farm < 0.3:        # nie spamuj co 0.07s
            return
        self._last_farm = now
        va = self.player_obj + self.farm_off["autoatk"]
        try:
            cur = self.pm.read_uint(va)
        except Exception:
            return
        if cur != 0:
            return                              # cel aktywny - klient atakuje, nie przeszkadzamy
        # brak celu -> wybierz najblizszego moba w okregu (od srodka farmy)
        cx, cy = self.farm_center
        hx, hy = self._hero_world()
        if hx is None:
            return
        vid_off = self.farm_off["vid"]
        best, bd = None, 1e18
        for mx, my, vnum, is_pc, o in self._iter_mobs(hx, hy):
            if is_pc or self._classify(vnum, is_pc) in ("npc",):   # tylko moby/bossy, nie gracze/NPC
                continue
            if math.hypot(mx - cx, my - cy) > self.farm_radius:     # poza okregiem
                continue
            d = math.hypot(mx - hx, my - hy)
            if d < bd:
                bd, best = d, o
        if best is None:
            return
        try:
            vid = self.pm.read_uint(best + vid_off)
            if vid:
                self.pm.write_uint(va, vid)     # ustaw cel auto-ataku -> klient atakuje
        except Exception:
            pass

    def _speed_step(self):
        """EKSPERYMENT: zapisuje mnozniki predkosci do wlasnej postaci co petle (serwer je nadpisuje).
        Prawdopodobnie serwer utnie realny efekt (gumka/odrzucone ataki) - to test walidacji (faza obrony)."""
        if not self.speed_on or not self.speed_off or self.self_actor is None:
            return
        try:
            self.pm.write_float(self.self_actor + self.speed_off["mov"], float(self.move_mult))
            self.pm.write_float(self.self_actor + self.speed_off["atk"], float(self.atk_mult))
        except Exception:
            pass

    def _autowalk_step(self):
        """Idzie po waypointach (zapetlonych). Do kazdego liczy trase A* po REALNEJ siatce kolizji
        (CMapOutdoor attr) i podaza po niej, omijajac sciany; replan co WALK_PLAN_SEC lub przy utknieciu.
        Gdy brak trasy (cel nieosiagalny) - jedzie wprost (best effort). vCore: hook; GameCoreTop: pending."""
        if not self.autowalk_on or not self.path:
            return
        if not (self.native_drink or self.dll_exec):   # vCore: hook; GameCoreTop: pending-call
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        now = time.time()
        self.collision.set_mapout(self.mapout_obj)
        self.collision.refresh()
        self.walk_idx %= len(self.path)
        tx, ty = self.path[self.walk_idx]
        d = math.hypot(tx - hx, ty - hy)
        if d < WALK_REACH:                          # waypoint osiagniety -> nastepny (petla)
            self.walk_idx = (self.walk_idx + 1) % len(self.path)
            self._walk_prog = None
            self._route = []
            return

        # detekcja utkniecia: brak postepu -> wymus replan (siatka moze sie zmienic / trasa nieaktualna)
        if self._walk_prog is None or d < self._walk_prog[1] - 20:
            self._walk_prog = (now, d)
        elif now - self._walk_prog[0] > WALK_STUCK_REPLAN:
            self._last_plan = 0.0
            self._walk_prog = (now, d)

        # (re)plan trasy A* po realnej siatce kolizji, okresowo albo gdy brak trasy
        if self.collision.ok and (not self._route or now - self._last_plan > WALK_PLAN_SEC):
            p = self.collision.find_path(hx, hy, tx, ty)
            self._last_plan = now
            self._route = p or []
            self._route_idx = 0

        # wybierz biezacy cel: punkt trasy A* (omija sciany) albo wprost waypoint (gdy brak trasy)
        tgt = (tx, ty)
        if self._route:
            while (self._route_idx < len(self._route) and
                   math.hypot(self._route[self._route_idx][0] - hx,
                              self._route[self._route_idx][1] - hy) < WALK_REACH):
                self._route_idx += 1
            if self._route_idx >= len(self._route):
                self._route = []                    # przeszlismy trase -> replan nastepnym razem
            else:
                tgt = self._route[self._route_idx]

        if now - self._last_goto >= WALK_RESEND:     # ponawiaj biezacy cel (keep-alive/korekta)
            autopot.goto_native(self.pm.process_id, tgt[0], tgt[1])
            self._last_goto = now

    def _draw_collision(self, c, hx, hy):
        """Rysuje zablokowane pola (ATTRIBUTE_BLOCK) wokol gracza jako czerwone kwadraty - jak siatka
        kolizji w grze. Lista pol liczona co ~0.4 s (cache), rysowana co klatke. [B] wl/wyl."""
        if not self.show_collision or not self.collision.ok or self.mapout_obj is None:
            return
        now = time.time()
        cached_t, cells = self._coll_cache
        if now - cached_t > 0.4:
            cells = []
            try:
                self.collision.set_mapout(self.mapout_obj)
                self.collision.refresh()
                cell = collision_mod.CELL
                cx, cy = int(hx) // cell, int(hy) // cell
                R = 24
                for gy in range(cy - R, cy + R + 1):
                    for gx in range(cx - R, cx + R + 1):
                        if self.collision._cell_block(gx, gy):
                            cells.append((gx * cell, gy * cell))      # rog komorki (swiat)
            except Exception:
                cells = []
            self._coll_cache = (now, cells)
        cell = collision_mod.CELL
        drawn = 0
        for wx, wy in cells:
            sx, sy = self.world_to_canvas(wx, wy, hx, hy)
            if not self._in_view(sx, sy):
                continue
            sx2, sy2 = self.world_to_canvas(wx + cell, wy + cell, hx, hy)
            c.create_rectangle(sx, sy, sx2, sy2, outline="", fill="#ff3b3b", stipple="gray25")
            drawn += 1
            if drawn > 1400:                      # zabezpieczenie przed zamuleniem
                break

    def _draw_farm(self, c, hx, hy):
        """Rysuje okrag zasiegu auto-farmu (srodek = punkt wlaczenia)."""
        if not self.farm_on or self.farm_center is None:
            return
        cx, cy = self.farm_center
        sx, sy = self.world_to_canvas(cx, cy, hx, hy)
        scale = self._follow_scale() if self.mode == "follow" else self._full_scale()
        r = self.farm_radius / map_assets.WORLD_PER_PX * scale
        c.create_oval(sx - r, sy - r, sx + r, sy + r, outline="#ff6a6a", width=2)

    def _draw_path(self, c, hx, hy):
        """Rysuje trase auto-chodzenia: linie miedzy waypointami + numerowane punkty; biezacy cel wiekszy."""
        if not self.path:
            return
        pts = [self.world_to_canvas(wx, wy, hx, hy) for wx, wy in self.path]
        col = "#9fd0ff" if self.autowalk_on else "#5a7a9a"
        if len(pts) > 1:
            flat = [v for p in pts for v in p]
            c.create_line(*flat, fill=col, width=2, dash=(4, 3))
        for i, (sx, sy) in enumerate(pts):
            if not self._in_view(sx, sy):
                continue
            cur = (i == self.walk_idx and self.autowalk_on)
            r = 6 if cur else 4
            fill = "#ffffff" if cur else col
            c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=fill, outline="#000")
            c.create_text(sx, sy - r - 6, fill=col, text=str(i + 1), font=("Consolas", 8, "bold"))
        # wyliczona trasa A* (omijanie scian) - cienka pomaranczowa linia od gracza przez punkty trasy
        if self._route and self.autowalk_on:
            rp = [(self.cw / 2, self.ch / 2) if self.mode == "follow"
                  else self.world_to_canvas(hx, hy, hx, hy)]
            rp += [self.world_to_canvas(rx, ry, hx, hy) for rx, ry in self._route]
            flat = [v for p in rp for v in p]
            c.create_line(*flat, fill="#ff9f1a", width=2)

    def _autoloot_step(self):
        """Co loot_interval podnosi najblizszy drop w zasiegu (PickCloseItem).
        GameCoreTop: przez Python (pending-call). vCore: natywnie z hooka per-klatka (!pick, bez fokusu)."""
        if not self.autoloot_on:
            return
        now = time.time()
        if now - self._last_loot < self.loot_interval:
            return
        if self.dll_exec:                                   # GameCoreTop: Python przez pending-call
            ok = autopot.pick_close_item(self.pm.process_id)
        elif self.native_drink:                             # vCore: hook per-klatka, natywne
            ok = autopot.pick_close_item_native(self.pm.process_id)
        else:
            return
        if ok:
            self._last_loot = now
            self.loot_count += 1

    def _autopot_step(self):
        """Jeden krok: odczyt HP, histereza (pij<low, lej do high), ewentualne picie. Zapisuje
        ostatnie HP do cache dla rysowania."""
        if self.pt_ptr is not None:
            # stabilny wskaznik singletona (np. vCore) - bez skanu
            hp = autopot.read_point_ptr(self.pm, self.base, self.pt_ptr, autopot.P_HP)
            mhp = autopot.read_point_ptr(self.pm, self.base, self.pt_ptr, autopot.P_MAX_HP)
            if hp is None or mhp is None or mhp <= 0 or hp < 0 or hp > mhp + 10000:
                self._pts_valid = False     # postac nie w swiecie / chwilowo zle
                return
        else:
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
        if self.dll_exec:                                  # GameCoreTop: Python przez pending-call
            if self.pot_vnums:
                if autopot.drink_by_vnum(self.pm.process_id, self.pot_vnums):   # skan EQ
                    via = "EQ"
            elif self.pipe.drink(slot):                    # slot paska przez DLL
                via = "slot"
        elif self.native_drink:                            # vCore: hook per-klatka, natywne, bez fokusu
            if autopot.drink_native(self.pm.process_id, slot):
                via = "hook"
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
        c.create_rectangle(2, 2, 560, 68, fill="#0b0f0b", outline="")     # tlo dla czytelnosci
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
        mode = "bez fokusu" if (self.dll_exec or self.native_drink) else "wymaga fokusu"
        state = ("WL  uzyto:%d" % self.pot_count) if self.autopot_on else "WYL"
        if self._healing:
            state += " [leje]"
        c.create_text(8, 33, anchor="w", fill="#ffe14d" if self.autopot_on else "#999",
                      text="mikstura %s  pij<%d%% lecz>%d%% co%.2fs  %s  %s  [J]DLL:%s"
                           % (method, self.pot_low, self.pot_high, self.pot_cd, mode, state,
                              self.dll_status),
                      font=("Consolas", 8))
        # auto-loot: GameCoreTop przez Python (dll_exec), vCore natywnie z hooka (native_drink)
        if self.dll_exec or self.native_drink:
            loot_state = ("WL  podniesiono:%d" % self.loot_count) if self.autoloot_on else "WYL"
            loot_col = "#e9d5a0" if self.autoloot_on else "#999"
            loot_via = "hook" if self.native_drink and not self.dll_exec else "python"
        else:
            loot_state = "niedostepny (brak exec/hook)"
            loot_col = "#999"
            loot_via = "-"
        c.create_text(8, 47, anchor="w", fill=loot_col,
                      text="[L]auto-loot %s  co%.1fs (najblizszy <=300, %s)"
                           % (loot_state, self.loot_interval, loot_via),
                      font=("Consolas", 8))
        # auto-chodzenie (vCore: hook; GameCoreTop: pending-call)
        if self.native_drink or self.dll_exec:
            if self.autowalk_on and self.path:
                walk_txt = "WL  wp %d/%d" % (self.walk_idx + 1, len(self.path))
            elif self.autowalk_on:
                walk_txt = "WL (brak trasy - Ctrl+klik)"
            else:
                walk_txt = "WYL  punktow:%d" % len(self.path)
            walk_col = "#9fd0ff" if self.autowalk_on else "#999"
        else:
            walk_txt = "niedostepne (brak exec/hook DLL)"
            walk_col = "#999"
        c.create_text(8, 61, anchor="w", fill=walk_col,
                      text="[G]auto-chodzenie %s  (Ctrl+klik=punkt, C=czysc)" % walk_txt,
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
