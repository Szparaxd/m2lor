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
import struct
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
# WIERZCHOWIEC: na koniu klient tworzy OSOBNEGO aktora konia (np. "d:/ymir work/npc/horse2/horse2_normal.gr2",
# VID 0), ktory stoi w pozycji postaci (0-50 jedn.). Po zsiadaniu odbiega i znika - nie zostaje widmo
# (sprawdzone na zywo). Jedziemy = aktor-wierzchowiec blizej MOUNT_DIST od wlasnej postaci.
MOUNT_MODEL_MARKS = ("/horse", "/mount")
MOUNT_DIST = 70.0
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
# Dziennik diagnostyczny ruchu/walki (zatrzymania postaci, !goto, replan, kroki w bok, przerwy petli).
# Plik nadpisywany przy kazdym starcie radaru. Wylacz (False), gdy niepotrzebny.
WALK_DEBUG = True
WALK_DEBUG_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "data", "walk_debug.log"))
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
WALK_REACH = 250.0        # jedn. swiata: tak blisko celu/wezla = idz dalej (wiekszy = plynniej, mniej stopow)
WALK_RESEND = 0.3         # s: co ile ponawiac !goto do biezacego celu (czesciej = plynniej)
WALK_PLAN_RETRY = 1.0     # s: po NIEUDANYM A* (brak trasy) ponow dopiero po tym czasie (nie co tick)
WALK_LOOKAHEAD = 1200.0   # jedn.: celuj wezel trasy tak daleko DO PRZODU (konne luki zamiast krazenia)
# Kon jedzie ~1100-1300 jedn./s, a !goto ponawiamy co WALK_RESEND (~0.35 s = ~400 jedn.). Cel blizszy niz to
# postac osiaga PRZED nastepnym !goto i staje (dziennik: STOP z 'cel d=169'). Dlatego cel ruchu przedluzamy
# wzdluz kierunku do co najmniej WALK_MIN_AIM (gdy droga na wprost wolna).
WALK_MIN_AIM = 900.0
WALK_PLAN_SEC = 2.5       # s: co ile przeliczac trase A* do biezacego waypointu (rzadziej = mniej szarpania)
WALK_STUCK_REPLAN = 1.5   # s: brak postepu tyle -> wymus replan trasy
WALK_FIGHT_RANGE = 6000.0 # jedn.: w drodze zatrzymaj sie na metina/bossa/specjalnego w tym promieniu
# Zamek na cel: raz zaczety cel bijemy do konca. Ale gdy przez ENGAGE_TIMEOUT s NIE ZBLIZAMY sie do niego
# (nieosiagalny - za sciana / wbici) -> porzucamy i na ENGAGE_COOL s ignorujemy ten VID (nie wybieramy go
# od razu ponownie), zeby nie krazyc w kolko wokol jednego nieosiagalnego celu.
WALK_ENGAGE_TIMEOUT = 7.0 # s: tyle bez postepu do celu (poza zasiegiem) -> porzuc cel
WALK_ENGAGE_COOL = 12.0   # s: tak dlugo pomijamy porzucony VID przy wyborze nowego celu
# Odblokowanie (unstick): gdy postac realnie stoi mimo wysylanego ruchu (np. wbita w ruszajacego sie moba),
# wysylamy krotki krok w BOK (naprzemiennie), zeby sie uwolnic. Dziala i dla chodzenia, i dla dochodzenia.
WALK_MOVE_EPS = 30.0      # jedn.: mniejszy ruch od ostatniego sprawdzenia = "stoi" (kon po walce rusza ~110 jedn./s -
                          # przy 50 jedn./0.5 s to byla granica i dawala falszywe utkniecia)
UNSTICK_GRACE = 1.2       # s po koncu walki bez wykrywania utkniecia (postac konczy animacje ataku i sie rozpedza;
                          # dziennik: 44 z 79 'utkniec' zaraz po walce -> zbedne objazdy i lancuchy bicia mobow)
WALK_UNSTICK_SEC = 0.5    # s: tyle bez ruchu -> utkniecie (1. raz: zapamietaj przeszkode + nowa trasa; potem krok w bok)
# Przeszkody spoza siatki kolizji (moby, drzewa/skaly - obiekty mapy NIE sa w attr): miejsce przed postacia,
# w ktorym utknelismy, zapamietujemy jako przeszkode na LEARN_TTL s i omijamy w A*.
LEARN_TTL = 10.0
LEARN_AHEAD = 150.0      # jedn.: przeszkoda = punkt tyle przed postacia w kierunku ruchu
FRONT_MOB_R = 450.0      # moby blizej niz to i PRZED postacia sa twarda przeszkoda (nie zwalniane przy starcie)
# OSTATECZNOSC: gdy postac utknie PONOWNIE mimo proby objazdu, a przed nia (BLOCKER_R) stoi mob - to on
# blokuje (przez potwory nie da sie przejechac). Bijemy wtedy JEDNEGO - tego w kierunku jazdy - i jedziemy
# dalej (najwyzej BLOCKER_SEC s). Dziennik pokazal, ze okno "bij wszystko w poblizu" zabijalo 2-4 moby za razem.
BLOCKER_R = 300.0
MOB_CLEAR_R = 160.0      # celowanie na wprost: odcinek nie moze mijac moba blizej niz to
BLOCKER_CONTACT = 180.0  # mob blizej = w kontakcie z postacia (bity w pierwszej kolejnosci)
BLOCKER_SEC = 6.0
WALK_NUDGE_HOLD = 0.5     # s: tyle trzymamy krok w bok (bez nadpisywania go zwyklym !goto)
# Speedhack (EKSPERYMENT - test czy serwer waliduje; patrz ANALIZA): m_fMovSpd/m_fAtkSpd to mnozniki
# animacji w CActorInstance (1.0=100%). Serwer nadpisuje je przy char_update -> re-aplikujemy co petle.
# Offsety WZGLEDEM wskaznika CActorInstance (ten sam co w self.mobs) - PER BUILD. GameCoreTop zweryf. na zywo.
SPEED_OFFSETS = {
    # Efekt zweryfikowany W GRZE (odwrotnie niz statyczne nazwy subagenta): +0x498 = RUCH, +0x49C = ATAK.
    "gamecoretop.exe": dict(mov=0x498, atk=0x49C),
    # vCore (2026-10-28, sonda na zywo): pola aktora przesuniete o +4 (jak pozycja 0x510) - wlasna postac
    # +0x49C=1.64 (odpowiednik GameCoreTop +0x498), moby 0.7/1.0/1.3 na +0x49C.
    "vcore.exe": dict(mov=0x49C, atk=0x4A0),
}
# Auto-farm: wpisujemy VID moba do CPythonPlayer.m_dwAutoAttackTargetVID (off "autoatk" wzgl. obiektu
# CPythonPlayer = self.player_obj); klient (__Update_AutoAttack) sam podchodzi, bije i czysci po smierci.
# VID moba czytamy z CActorInstance (off "vid" wzgl. wskaznika z self.mobs). Offsety PER BUILD (do RE).
FARM_OFFSETS = {
    # zweryfikowane na zywo: VID moba = CActorInstance+0x558; auto-attack target = CPythonPlayer+0x50.
    "gamecoretop.exe": dict(vid=0x558, autoatk=0x50),
    # vCore (2026-10-28): VID aktora +0x55C (sekwencyjne VID-y na zywo, +4 jak pozycja); autoatk +0x50
    # (statycznie: __Update_AutoAttack @RVA 0x825B0 'cmp dword [ebx+0x50],0'). CPythonPlayer bez RTTI ->
    # obiekt gracza bierzemy ze wskaznika singletona (POINT_PTR_CLIENTS singleton_rva).
    "vcore.exe": dict(vid=0x55C, autoatk=0x50),
}
FARM_RADIUS = 2500.0      # domyslny promien okregu farmienia (jedn. swiata; ~25 px minimapy)
FARM_MELEE = 300.0        # jedn. swiata: tak blisko celu = oddaj atak klientowi (dalej - dochodzimy A*)
FARM_BOSS_RANGE = 10000.0 # jedn.: bossy scigamy nawet POZA okregiem farmy (rzadkie, cenne), do tego zasiegu
FARM_MELEE_EXIT = 480.0   # histereza: z trybu walki wychodzimy dopiero dalej niz to (cofniecie/odrzut moba)
FARM_HANDOFF = 650.0      # tak blisko, ale bez postepu FARM_HANDOFF_SEC (duzy boss/metin) -> oddaj klientowi
FARM_HANDOFF_SEC = 2.0
FARM_FIGHT_TIMEOUT = 25.0     # s w walce wrecz bez smierci celu (mob) -> cel-widmo/niezabijalny, porzuc
FARM_FIGHT_TIMEOUT_BIG = 120.0  # j.w. dla metinow/bossow (dlugo sie bija)
# Walka wrecz bez KONTAKTU: VID oddany klientowi, a odleglosc stoi (> MELEE_CONTACT) przez MELEE_STALL_SEC -
# klient nie moze dojsc (blokuje go inny mob). Dziennik: 'walka VID .. d=278' bez zmian przez 11 s. Porzuc cel.
MELEE_CONTACT = 220.0
MELEE_CONTACT_BIG = 400.0     # metiny/bossy maja duzy obrys
MELEE_STALL_SEC = 3.0
COMBAT_EVERY = 0.25       # s: wspolny throttle logiki walki (farm i walka w drodze)
ENGAGE_REPLAN = 1.0       # s: trasa dojscia do celu liczona najwyzej co tyle...
ENGAGE_REPLAN_MOVE = 200.0  # ...albo gdy cel przesunal sie o wiecej niz to
OBST_MOB_MIN = 250.0      # moby blizej postaci nie sa przeszkoda (ruszaja sie / to one nas bija)
OBST_MOB_MAX = 3000.0     # ani dalsze niz to (i tak sie przemieszcza zanim dojdziemy)
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
        self._actors = ([], set())  # (mobs, pc_objs) podmieniane RAZEM jedna krotka (spojny odczyt z watkow)
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
        self.walk_fight = False       # w drodze zatrzymaj sie i zbij metina/bossa w poblizu
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
        self._um_pos = None           # (wx,wy) do wykrywania braku ruchu (unstick: krok w bok)
        self._um_t = 0.0
        self._um_side = 1
        self._nudge_until = 0.0       # do kiedy trzymamy krok w bok (nie nadpisujemy go !goto)
        self._nudge_n = 0             # ile krokow w bok pod rzad bez realnego ruchu (eskalacja -> replan)
        self._um_grace_until = 0.0    # do kiedy nie wykrywamy utkniecia (po koncu walki)
        self._learned = []            # [(wx, wy, wygasa)] przeszkody wykryte przez utkniecie (spoza siatki)
        self._coll_cache = (0.0, [])  # (czas, lista (wx,wy) zablokowanych pol w poblizu) - do rysowania
        # speedhack (eksperyment): zapis mnoznikow predkosci do wlasnej postaci (CActorInstance)
        self.speed_off = SPEED_OFFSETS.get(self.proc_name.lower())
        self.speed_on = False
        self.move_mult = 1.0
        self.atk_mult = 1.0
        self.self_actor = None        # wskaznik CActorInstance wlasnej postaci (do zapisu predkosci)
        self._dbg_lock = threading.Lock()   # dziennik diagnostyczny (WALK_DEBUG)
        self._dbg_f = None
        self._dbg_t0 = 0.0
        self._dbg_last_goto = None   # (czas, wx, wy, powod) ostatniego !goto
        self._dbg_mv = None          # stan detektora zatrzyman
        self._dbg_hp = None          # ostatnie HP (zdarzenia obrazen)
        self._dbg_atk = None         # ostatnia wartosc pola auto-ataku klienta
        self._dbg_st_t = 0.0         # throttle linii stanu 'ST'
        self._dbg_prev = None        # (czas, x, y) do predkosci w linii 'ST'
        self._mount_cands = []        # [(obj, nazwa_modelu)] aktorzy-wierzchowce w poblizu (z rescan)
        self.mounted = None           # (True/False, nazwa_modelu) albo None gdy nie wiadomo - z _mount_check
        # auto-farm: okrag wokol punktu startu, bijemy moby w srodku (wpis VID do auto-attack target)
        self.farm_off = FARM_OFFSETS.get(self.proc_name.lower())
        self.farm_on = False
        self.farm_radius = FARM_RADIUS
        self.farm_center = None        # (wx, wy) - srodek okregu (ustawiany przy wlaczeniu)
        self._last_farm = 0.0
        self.farm_metin = True         # bij metiny (najwyzszy priorytet)
        self.farm_boss = True          # bij bossy z priorytetem (nad zwyklymi mobami)
        self._engaged_vid = 0          # "zamek na cel": VID celu w trakcie walki (konczymy go, nie zmieniamy)
        self._engaged_since = 0.0      # kiedy zaczeto biezacy cel (timeout porzucenia)
        self._engaged_best = 1e18      # najlepszy (min) dystans do celu (wykrycie braku postepu)
        self._mob_snap = (0.0, [], {}) # (czas, lista mobow, dict vid->(wx,wy,is_metin)) - 1 skan na krok
        self._engage_cool = {}         # vid -> czas, do kiedy pomijac porzucony (nieosiagalny) cel
        self._engaged_big = False      # biezacy cel to metin/boss (dluzszy limit walki)
        self._engaged_t0 = 0.0         # kiedy wybrano biezacy cel (dziennik)
        self._in_melee = False         # histereza zasiegu: True = atak oddany klientowi (wpis VID)
        self._melee_since = 0.0        # od kiedy w walce wrecz (limit FARM_FIGHT_TIMEOUT)
        self._bot_vid = 0              # VID wpisany PRZEZ BOTA do auto-ataku (czyscimy tylko wlasny wpis)
        self._eng_route = []           # trasa A* dojscia do celu (cache + rysowanie)
        self._eng_route_t = 0.0
        self._eng_route_goal = None
        self._appr = None              # podejscie do metina widocznego tylko z efektu: (klucz, od_kiedy, best_d)
        self._reset_req = False        # GUI prosi o reset walki -> wykonuje petla (bez wyscigu watkow)
        self._last_combat = 0.0        # throttle walki w drodze (COMBAT_EVERY)
        self._walk_fighting = False    # wynik ostatniego ticku walki w drodze
        self._blockers_until = 0.0     # do kiedy bijemy moba blokujacego postac (po utknieciu przy mobie)
        self._blocker_vid = 0          # VID tego JEDNEGO moba (0 = brak)
        self._combat_target = None     # (wx, wy, wrecz) biezacego celu - do rysowania w GUI
        self._combat_status = ""       # tekst stanu walki do panelu

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
        self.walkfight_var = tk.BooleanVar(value=self.walk_fight)
        self.speed_var = tk.BooleanVar(value=self.speed_on)
        self.movx_var = tk.DoubleVar(value=self.move_mult)
        self.atkx_var = tk.DoubleVar(value=self.atk_mult)
        self.farm_var = tk.BooleanVar(value=self.farm_on)
        self.farmr_var = tk.IntVar(value=int(self.farm_radius))
        self.farm_metin_var = tk.BooleanVar(value=self.farm_metin)
        self.farm_boss_var = tk.BooleanVar(value=self.farm_boss)

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
        _chk("  W drodze bij metiny/bossy", self.walkfight_var, self._on_walkfight, "#9fd0ff", "#cfe8ff")
        _chk("Auto-farm (okrag wokol postaci)", self.farm_var, self._on_farm_check, "#ff6a6a", "#ffb0b0")
        _mkscale("Promien", self.farmr_var, 500, 6000, 100, self._on_farmr, "#ff6a6a")
        _chk("  Bij metiny (priorytet)", self.farm_metin_var, self._on_farm_metin, "#ffe14d", "#fff0a0")
        _chk("  Bij bossy (priorytet)", self.farm_boss_var, self._on_farm_boss, "#d94dff", "#ecb0ff")
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

    def _dbg(self, fmt, *args):
        """Linia do dziennika diagnostycznego (WALK_DEBUG): '<czas od startu> <tekst>'. Z obu watkow."""
        if not WALK_DEBUG:
            return
        try:
            with self._dbg_lock:
                if self._dbg_f is None:
                    self._dbg_t0 = time.time()
                    self._dbg_f = open(WALK_DEBUG_PATH, "w", encoding="utf-8", buffering=1)
                self._dbg_f.write("%8.3f %s\n" % (time.time() - self._dbg_t0, fmt % args if args else fmt))
        except Exception:
            pass

    def _goto(self, wx, wy, why, hx=None, hy=None):
        """Wysyla !goto (ruch natywny) + wpis do dziennika: powod i odleglosc celu od postaci."""
        autopot.goto_native(self.pm.process_id, wx, wy)
        self._dbg_last_goto = (time.time(), wx, wy, why)
        if hx is not None:
            self._dbg("goto %-6s d=%5.0f -> (%.0f,%.0f)", why, math.hypot(wx - hx, wy - hy), wx, wy)

    def _dbg_motion(self):
        """Wykrywa ZATRZYMANIA postaci (gdy chodzenie/farm wlaczone) i zapisuje kontekst: ile temu i jak
        daleko byl ostatni !goto. Wolane co tick petli (1 odczyt pozycji)."""
        if not WALK_DEBUG or not (self.autowalk_on or self.farm_on):
            self._dbg_mv = None
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        now = time.time()
        if self._dbg_mv is None:
            self._dbg_mv = [now, hx, hy, True, now]       # [t, x, y, w_ruchu, od_kiedy_stan]
            return
        t, x, y, moving, since = self._dbg_mv
        if now - t < 0.2:
            return
        v = math.hypot(hx - x, hy - y) / (now - t)
        if moving and v < 60:
            lg = self._dbg_last_goto
            ctx = ("ostatni goto %s %.2fs temu, cel d=%.0f" % (lg[3], now - lg[0], math.hypot(lg[1] - hx, lg[2] - hy))
                   if lg else "brak goto")
            near = min(((math.hypot(m[0] - hx, m[1] - hy), m[2]) for m in self._mob_snap[1] if not m[3]),
                       default=None)
            self._dbg("STOP po %.1fs ruchu @(%.0f,%.0f); %s; najblizszy mob: %s; walka: %s", now - since, hx, hy,
                      ctx, ("vnum %d d=%.0f" % (near[1], near[0])) if near else "-", self._combat_status or "-")
            moving, since = False, now
        elif not moving and v >= 60:
            self._dbg("RUSZ po %.2fs postoju (v=%.0f)", now - since, v)
            moving, since = True, now
        self._dbg_mv = [now, hx, hy, moving, since]

    def _dbg_state(self):
        """Szczegolowy dziennik (WALK_DEBUG), gdy chodzenie/farm wlaczone:
          - co tick: zdarzenia HP (obrazenia/leczenie) i zmiany pola auto-ataku klienta (kto ustawil VID),
          - co 0.5 s: linia 'ST' - pozycja, predkosc, HP, auto-atak, ostatni !goto, trasa, odblokowanie, walka
            oraz 4 najblizsze moby (vid, vnum, klasa, odleglosc, kat wzgledem kierunku jazdy)."""
        if not WALK_DEBUG or not (self.autowalk_on or self.farm_on):
            return
        now = time.time()
        hp = self._hp
        if self._dbg_hp is not None and hp != self._dbg_hp and self._mhp:
            self._dbg("HP %+d -> %d/%d%s", hp - self._dbg_hp, hp, self._mhp,
                      "  (OBRYWAM)" if hp < self._dbg_hp else "")
        self._dbg_hp = hp
        atk = None
        if self.farm_off and self.player_obj is not None:
            try:
                atk = self.pm.read_uint(self.player_obj + self.farm_off["autoatk"])
            except Exception:
                atk = None
        if atk != self._dbg_atk:
            who = "bot" if atk and atk == self._bot_vid else ("klient/gracz" if atk else "wyczyszczone")
            self._dbg("AUTOATAK %s -> %s (%s)", self._dbg_atk, atk, who)
            self._dbg_atk = atk
        if now - self._dbg_st_t < 0.5:
            return
        self._dbg_st_t = now
        hx, hy = self._hero_world()
        if hx is None:
            return
        mv = self._dbg_mv
        v = 0.0
        if self._dbg_prev is not None and now > self._dbg_prev[0]:
            v = math.hypot(hx - self._dbg_prev[1], hy - self._dbg_prev[2]) / (now - self._dbg_prev[0])
        self._dbg_prev = (now, hx, hy)
        lg = self._dbg_last_goto
        dx = dy = 0.0
        gtxt = "-"
        if lg:
            gd = math.hypot(lg[1] - hx, lg[2] - hy)
            if gd > 1:
                dx, dy = (lg[1] - hx) / gd, (lg[2] - hy) / gd
            gtxt = "%s d=%.0f %.2fs" % (lg[3], gd, now - lg[0])
        mobs = []
        for mx, my, vnum, is_pc, vid in self._mobs_snapshot(hx, hy)[0]:   # swiezy (cache 0.12 s)
            if is_pc:
                continue
            d = math.hypot(mx - hx, my - hy)
            if d < 1200:
                mobs.append((d, vid, vnum, mx, my))
        mobs.sort()
        mtxt = []
        for d, vid, vnum, mx, my in mobs[:4]:
            ang = "?"
            if d > 1 and (dx or dy):
                c = ((mx - hx) * dx + (my - hy) * dy) / d
                ang = "%.0f" % math.degrees(math.acos(max(-1.0, min(1.0, c))))
            mtxt.append("%d/%d/%s d=%.0f k=%s" % (vid, vnum, self._classify(vnum, False)[:4], d, ang))
        route = self._route
        st = self._combat_target
        self._dbg("ST (%.0f,%.0f) v=%.0f %s HP %d/%d atk=%s bot=%s | goto %s | trasa %d/%d plan %.1fs | "
                  "utk n=%d zapam=%d blok=%s | cel=%s%s | kon=%s | moby(snap %.1fs): %s",
                  hx, hy, v, "JADE" if (mv and mv[3]) else "STOJE", self._hp, self._mhp, atk, self._bot_vid or "-",
                  gtxt, self._route_idx if route else 0, len(route), now - self._last_plan if self._last_plan else -1,
                  self._nudge_n, len(self._learned), self._blocker_vid or "-",
                  self._engaged_vid or "-", (" wrecz" if self._in_melee else " dojscie") if st else "",
                  (self.mounted[1] or "nie") if self.mounted and self.mounted[0] else "nie",
                  now - self._mob_snap[0], "; ".join(mtxt) or "brak w 1200")

    def _rescan_loop(self):
        while True:
            time.sleep(RESCAN_SEC)
            try:
                t0 = time.time()
                self.rescan()
                dt = time.time() - t0
                if dt > 0.15:
                    self._dbg("rescan %.0f ms (aktorow %d)", dt * 1000, len(self.mobs))
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
        self._reset_req = True                     # reset walki wykona petla (bez wyscigu z jej watkiem)
        if self.farm_on:
            hx, hy = self._hero_world()
            self.farm_center = (hx, hy) if hx is not None else None   # srodek = pozycja przy wlaczeniu
            self._last_farm = 0.0
        else:
            self.farm_center = None

    def _on_farmr(self, val):
        self.farm_radius = float(val)

    def _on_farm_metin(self):
        self.farm_metin = bool(self.farm_metin_var.get())

    def _on_farm_boss(self):
        self.farm_boss = bool(self.farm_boss_var.get())

    def _on_speed_check(self):
        self.speed_on = bool(self.speed_var.get())

    def _on_movx(self, val):
        self.move_mult = float(val)

    def _on_atkx(self, val):
        self.atk_mult = float(val)

    def _on_walkfight(self):
        self.walk_fight = bool(self.walkfight_var.get())
        self._reset_req = True

    def _on_autowalk_check(self):
        self.autowalk_on = bool(self.autowalk_var.get())
        self._reset_req = True
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
            self.path = self.path + [(wx, wy)]       # nowa lista (petla czyta snapshot, nie mutujemy)
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
        for vnum, name, x, y, _vid in acts:
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
            self.collision.refresh()        # tez gdy samo farmienie (bez chodzenia/siatki na ekranie)
        if player is None and player_vt is None and self.pt_ptr is not None:
            # build bez RTTI CPythonPlayer (vCore): obiekt gracza = *(base+singleton_rva). Potrzebny do
            # auto-farmu (pole auto-ataku). HP i tak idzie przez read_point_ptr - tu tylko player_obj.
            try:
                sp = self.pm.read_uint(self.base + self.pt_ptr["singleton_rva"])
            except Exception:
                sp = 0
            self.player_obj = sp or None
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
        # inni gracze: aktorzy z modelem /pc/ (liczone tu, raz na skan - nie co klatke).
        # mobs i pc_objs podmieniamy RAZEM (krotka): inaczej przez chwile nowy gracz nie bylby w pc_objs
        # i farm moglby wpisac jego VID do auto-ataku.
        models = {o: self._actor_model(o) for o in mobs}      # 1 odczyt modelu na aktora na skan
        pcs = set(o for o, m in models.items() if m and any(mark in m for mark in PC_MODEL_MARKS))
        self._actors = (mobs, pcs)
        self.mobs = mobs
        self.pc_objs = pcs
        # wlasna postac (do speedhacka i wykrycia konia): aktor z modelem GRACZA najblizszy pozycji gracza.
        # Na koniu w tym samym miejscu stoi tez aktor konia - dlatego tylko modele /pc/ (inaczej trafialby kon).
        # Wierzchowce: aktorzy z modelem konia w poblizu (czy jedzie NA nim - sprawdza _mount_check co klatke).
        self_actor, mounts = None, []
        hx0, hy0 = self._hero_world()
        if hx0 is not None:
            mob_x = self.off["mob_x"]
            bd = 1e18
            for o in mobs:
                m = models[o]
                is_pc, is_mount = o in pcs, any(mark in m for mark in MOUNT_MODEL_MARKS)
                if not (is_pc or is_mount):
                    continue
                try:
                    x = self.pm.read_float(o + mob_x)
                    y = self.pm.read_float(o + mob_x + 4)
                except Exception:
                    continue
                dd = math.hypot(x - hx0, y - hy0)
                if is_pc and dd < bd:
                    bd, self_actor = dd, o
                elif is_mount and dd < 600:
                    mounts.append((o, m.rsplit("/", 1)[-1].split(".")[0]))
            if bd >= 150:
                self_actor = None
        self.self_actor = self_actor
        self._mount_cands = mounts
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
                vid = 0
                if self.farm_off:               # VID metina (do auto-farmu); offset per build
                    try:
                        vid = self.pm.read_uint(o + self.farm_off["vid"])
                    except Exception:
                        vid = 0
                metin_actors.append((vnum, self._mob_name(o), ax, ay, vid))
        self.metin_actors = metin_actors
        self.effs = effs
        self.metins = metins

    def _iter_actors(self, hx, hy):
        """Yield (wx, wy, vnum, is_pc, obj, vid) dla aktorow z sensowna pozycja (bez samego gracza).
        JEDEN ReadProcessMemory na aktora: blok obejmujacy vnum, pozycje i VID (zamiast 4 odczytow).
        is_pc = aktor to inny gracz (model /pc/, z pc_objs liczonego w rescan); vid=0 gdy brak offsetu.
        Pomija niewidzialne typy z mob_proto i kamienie (metiny rysujemy z efektow)."""
        mob_x, vnum_off = self.off["mob_x"], self.off["vnum"]
        vid_off = self.farm_off["vid"] if self.farm_off else None
        offs = [mob_x, mob_x + 4, vnum_off] + ([vid_off] if vid_off is not None else [])
        lo, hi = min(offs), max(offs) + 4
        mobs, pcs = self._actors
        for o in mobs:
            try:
                b = self.pm.read_bytes(o + lo, hi - lo)
            except Exception:
                continue
            x, y = struct.unpack_from("<ff", b, mob_x - lo)
            vnum = struct.unpack_from("<I", b, vnum_off - lo)[0]
            vid = struct.unpack_from("<I", b, vid_off - lo)[0] if vid_off is not None else 0
            if not (math.isfinite(x) and math.isfinite(y) and WORLD_MIN < x < WORLD_MAX):
                continue
            if 8000 <= vnum < 9000 or math.hypot(x - hx, y - hy) < 50:
                continue
            is_pc = o in pcs
            # Gracz NIE podlega filtrowi HIDDEN_TYPES (jego vnum = rasa, bywa w proto).
            if not is_pc and self.proto.get(vnum, (0, 0, 0))[0] in HIDDEN_TYPES:
                continue
            yield x, y, vnum, is_pc, o, vid

    def _iter_mobs(self, hx, hy):
        """Yield (wx, wy, vnum, is_pc, obj) - jak _iter_actors, bez VID (rysowanie)."""
        for x, y, vnum, is_pc, o, _vid in self._iter_actors(hx, hy):
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

    def _view_center(self, hx, hy, scale):
        """Srodek widoku FOLLOW (px obrazu mapy): gracz, ale widok nie wychodzi poza mape. Gdy okno
        pokazuje wiecej niz mapa w danej osi (mocne oddalenie) - mapa wycentrowana w oknie. Dzieki temu
        stojac przy krawedzi i oddalajac widac (i mozna kliknac) caly drugi koniec mapy."""
        hix, hiy = self.map.world_to_image(hx, hy)
        hw, hh = self.cw / 2.0 / scale, self.ch / 2.0 / scale   # pol-wymiary okna w px obrazu

        def clamp(v, half, size):
            if 2 * half >= size:
                return size / 2.0
            return min(max(v, half), size - half)
        return clamp(hix, hw, self.map.w_px), clamp(hiy, hh, self.map.h_px)

    def world_to_canvas(self, wx, wy, hx, hy):
        ix, iy = self.map.world_to_image(wx, wy)     # piksel pelnego obrazu
        if self.mode == "follow":
            scale = self._follow_scale()
            hix, hiy = self._view_center(hx, hy, scale)
            return self.cw / 2 + (ix - hix) * scale, self.ch / 2 + (iy - hiy) * scale
        else:
            s = self._full_scale()
            return ix * s + self.pan_x, iy * s + self.pan_y

    def canvas_to_world(self, sx, sy, hx, hy):
        """Odwrotnosc world_to_canvas: punkt na canvasie -> wspolrzedne swiata (do waypointow)."""
        m = self.map
        if self.mode == "follow":
            scale = self._follow_scale()
            hix, hiy = self._view_center(hx, hy, scale)
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
            scale = self._follow_scale()
            hix, hiy = self._view_center(hx, hy, scale)
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
                self.view_world = min(max(self.map.w_px, self.map.h_px) * map_assets.WORLD_PER_PX * 1.5,
                                      self.view_world * 1.25)
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
            self.autowalk_var.set(not self.autowalk_on)   # start/stop auto-chodzenia (jak checkbox)
            self._on_autowalk_check()
        elif k == "c":
            self.path = []                              # wyczysc waypointy
            self.walk_idx = 0
            self._walk_prog = None
            self._route = []
            print("[walk] trasa wyczyszczona")
        elif k == "b":
            self.show_collision = not self.show_collision   # siatka kolizji wl/wyl
        elif k == "v":
            self.farm_var.set(not self.farm_on)             # auto-farm wl/wyl (jak checkbox)
            self._on_farm_check()
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
        self._draw_learned(c, hx, hy)                     # zapamietane miejsca utkniec (fioletowe X)

        # gracz
        sx, sy = self.world_to_canvas(hx, hy, hx, hy)     # FOLLOW: przy krawedzi mapy gracz poza srodkiem
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
        self._draw_mount(c)                               # ikona konia obok paska HP
        self.root.after(120, self.tick)

    # ---------- auto-mikstura: szybka petla (decyzja) + rysowanie (z cache) ----------
    def _autopot_loop(self):
        """Dedykowana petla: czyta HP i pije co POTION_POLL (szybka reakcja, niezaleznie od rysowania,
        ktore chodzi co 120 ms). Dzieki temu lek leci zaraz po spadku HP, a nie raz na klatke/1.5 s."""
        last_status, t_end = "", time.time()
        while True:
            time.sleep(POTION_POLL)
            t_start = time.time()
            if t_start - t_end > 0.15:              # petla nie dostala CPU (GIL: rescan/GUI) - diagnoza
                self._dbg("przerwa petli %.0f ms", (t_start - t_end) * 1000)
            try:
                self._dbg_motion()
                self._dbg_state()
            except Exception as e:
                self._dbg("blad dziennika: %r", e)
            if self._combat_status != last_status:
                last_status = self._combat_status
                self._dbg("walka: %s", last_status or "-")
            if self._reset_req:                     # prosba z GUI (wl/wyl farmy/chodzenia) - wykonujemy tutaj
                self._reset_req = False
                try:
                    self._reset_combat()
                except Exception as e:
                    print("[farm] blad resetu:", e)
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
            t_end = time.time()
            if t_end - t_start > 0.15:
                self._dbg("krok petli %.0f ms", (t_end - t_start) * 1000)

    def _metin_targets(self):
        """Metiny do bicia: (wx, wy, vid) wprost z aktorow-metinow (vnum 8xxx) - maja VID i pozycje.
        (Prostsze i pewniejsze niz parowanie z efektami; klient i tak dochodzi do metina po VID.)"""
        return [(x, y, vid) for (vnum, nm, x, y, vid) in self.metin_actors if x is not None and vid]

    def _fx_metins_without_actor(self):
        """Metiny widoczne TYLKO z efektu (brak aktora z pozycja w poblizu, np. dalekie / jeszcze nie
        wczytane): [(wx, wy)]. Do nich tylko PODCHODZIMY (bez VID), az pojawi sie aktor."""
        acts = [(x, y) for (vnum, nm, x, y, vid) in self.metin_actors if x is not None]
        return [(mx, my) for mx, my, _ in self.metins
                if not any(math.hypot(mx - ax, my - ay) < 500 for ax, ay in acts)]

    @staticmethod
    def _fx_key(wx, wy):
        """Klucz metina-z-efektu (brak VID) - do czarnej listy porzuconych podejsc."""
        return ("fx", int(wx) // 300, int(wy) // 300)

    def _mobs_snapshot(self, hx, hy):
        """JEDEN skan aktorow na krok (cache ~0.12 s). Zwraca (lista, dict):
          lista = [(wx, wy, vnum, is_pc, vid), ...]  (vid=0 gdy brak offsetu),
          dict  = {vid: (wx, wy, is_metin)}  (moby + metiny, do szybkiego _find_by_vid).
        Wszyscy konsumenci w jednym kroku (walka/wybor celu/przeszkody) biora ten sam snapshot -
        zamiast 3-4x skanowac pamiec. Mniej odczytow = plynniejsza petla (kon mniej dryfuje)."""
        now = time.time()
        if now - self._mob_snap[0] < 0.12:
            return self._mob_snap[1], self._mob_snap[2]
        lst, v2p = [], {}
        for mx, my, vnum, is_pc, o, vid in self._iter_actors(hx, hy):
            lst.append((mx, my, vnum, is_pc, vid))
            if vid and not is_pc:
                v2p[vid] = (mx, my, False)
        for mx, my, mvid in self._metin_targets():
            if mvid:
                v2p[mvid] = (mx, my, True)
        self._mob_snap = (now, lst, v2p)
        return lst, v2p

    def _vid_blocked(self, vid):
        """True gdy VID zostal niedawno porzucony jako nieosiagalny - pomijamy go przy wyborze celu,
        zeby nie krazyc w kolko wokol tego samego celu za sciana. Wpis wygasa po WALK_ENGAGE_COOL s."""
        t = self._engage_cool.get(vid)
        if t is None:
            return False
        if time.time() > t:
            del self._engage_cool[vid]
            return False
        return True

    def _route_aim(self, route, idx, hx, hy):
        """Cel ruchu z WYPRZEDZENIEM. Zamiast celowac w najblizszy wezel trasy, celuje w najdalszy wezel,
        ktory (a) jest w promieniu WALK_LOOKAHEAD i (b) ma wolna linie wzroku z biezacej pozycji.
        Kon ma promien skretu - celujac dalej do przodu robi lagodne luki zamiast krazenia wokol bliskiego
        punktu; LOS pilnuje, by nie scinac rogu w sciane."""
        if not route:
            return None
        n = len(route)
        j = min(idx, n - 1)
        aim = route[j]
        while j + 1 < n:
            nx, ny = route[j + 1]
            if math.hypot(nx - hx, ny - hy) > WALK_LOOKAHEAD:
                break
            if not self._segment_clear(hx, hy, nx, ny):
                break
            aim = route[j + 1]
            j += 1
        return aim

    def _collect_targets(self, hx, hy, accept):
        """Wspolny wybor celu. accept(cls, wx, wy) -> priorytet (mniejszy = wazniejszy) albo None (pomin);
        cls: 'metin' (aktor z VID), 'metin_fx' (tylko efekt, bez VID), 'boss'/'king'/'special'/'mob'.
        Zwraca (vid, tx, ty, is_metin, big) najlepszego (priorytet, potem odleglosc) albo None.
        vid == 0 -> metin z efektu: tylko podejscie. big = metin/boss (dluzszy limit walki)."""
        cands = []                              # (priorytet, dist_do_gracza, vid, tx, ty, is_metin)
        mobs, _ = self._mobs_snapshot(hx, hy)
        for mx, my, vid in self._metin_targets():
            if self._vid_blocked(vid):
                continue
            pr = accept("metin", mx, my)
            if pr is not None:
                cands.append((pr, math.hypot(mx - hx, my - hy), vid, mx, my, True))
        for mx, my in self._fx_metins_without_actor():
            if self._vid_blocked(self._fx_key(mx, my)):
                continue
            pr = accept("metin_fx", mx, my)
            if pr is not None:
                cands.append((pr, math.hypot(mx - hx, my - hy), 0, mx, my, True))
        for mx, my, vnum, is_pc, vid in mobs:
            if is_pc or not vid or self._vid_blocked(vid):
                continue
            cls = self._classify(vnum, is_pc)
            if cls == "npc":
                continue
            pr = accept(cls, mx, my)
            if pr is not None:
                cands.append((pr, math.hypot(mx - hx, my - hy), vid, mx, my, False))
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]))
        pr, _, vid, tx, ty, ism = cands[0]
        return vid, tx, ty, ism, pr <= 1

    def _farm_pick_target(self, hx, hy):
        """Najlepszy cel farmy wg priorytetu metin > metin-z-efektu > boss > special > mob.
        Metiny/specjalne/moby tylko w okregu; bossy tez POZA okregiem (do FARM_BOSS_RANGE od postaci)."""
        bt = self._blocker_target(hx, hy)       # mob, ktory nas zablokowal - najpierw on
        if bt is not None:
            return bt
        cx, cy = self.farm_center
        R = self.farm_radius

        def accept(cls, mx, my):
            in_circle = math.hypot(mx - cx, my - cy) <= R
            if cls in ("metin", "metin_fx"):
                return (0 if cls == "metin" else 0.5) if self.farm_metin and in_circle else None
            if cls in ("boss", "king") and self.farm_boss:
                return 1 if math.hypot(mx - hx, my - hy) <= FARM_BOSS_RANGE else None
            if not in_circle:
                return None
            return 2 if cls == "special" else 3
        return self._collect_targets(hx, hy, accept)

    def _farm_leash(self, tx, ty, big):
        """True gdy zablokowany cel farmy uciekl za daleko od okregu (porzuc)."""
        cx, cy = self.farm_center
        lim = FARM_BOSS_RANGE * 1.2 if big else self.farm_radius * 1.5
        return math.hypot(tx - cx, ty - cy) > lim

    def _nearby_fight_target(self, hx, hy, R):
        """Metin/boss/specjalny w promieniu R od postaci (walka w drodze): jak _collect_targets.
        Priorytet: metin > metin-z-efektu > boss > specjalny. Zwykle moby pomijane."""
        PRI = {"metin": 0, "metin_fx": 0.5, "boss": 1, "king": 1, "special": 2}

        def accept(cls, mx, my):
            return PRI.get(cls) if math.hypot(mx - hx, my - hy) <= R else None
        return self._collect_targets(hx, hy, accept)

    def _farm_step(self):
        """Auto-farm: wybiera cel (metin>boss>special>mob; bossy tez poza okregiem). ZAMEK NA CEL: raz zaczety
        cel bije do konca (nie przerywa na rzecz wyzszego priorytetu). W zasiegu oddaje atak klientowi,
        poza zasiegiem prowadzi A* (omijajac metiny+moby) z odblokowaniem."""
        if not self.farm_on or not self.farm_off or self.player_obj is None:
            return
        now = time.time()
        if now - self._last_farm < COMBAT_EVERY:
            return
        self._last_farm = now
        hx, hy = self._hero_world()
        if hx is None:
            return
        if self.farm_center is None:            # wlaczono, gdy gracza nie bylo widac -> srodek teraz
            self.farm_center = (hx, hy)
        self._combat_tick(hx, hy, self._farm_pick_target, self._farm_leash)

    def _find_by_vid(self, vid, hx, hy):
        """Biezaca pozycja celu po VID: (wx,wy,is_metin) albo None gdy zniknal/padl (ze snapshotu)."""
        _, v2p = self._mobs_snapshot(hx, hy)
        return v2p.get(vid)

    def _combat_tick(self, hx, hy, pick_fn, leash_fn=None):
        """Wspolna logika walki z ZAMKIEM NA CEL. Zwraca True gdy walczymy (nie przerywac chodzenia).
        Zablokowany cel bijemy do konca, chyba ze:
          - padl/zniknal (brak VID w snapshocie),
          - przez WALK_ENGAGE_TIMEOUT s nie zblizamy sie do niego (za sciana / wbici),
          - w walce wrecz trwa dluzej niz FARM_FIGHT_TIMEOUT(_BIG) (cel-widmo z puli aktorow / niezabijalny),
          - uciekl poza smycz (leash_fn).
        Porzucony VID na WALK_ENGAGE_COOL s trafia na czarna liste. Nowy cel wybiera pick_fn."""
        now = time.time()
        if self._engaged_vid:
            vid = self._engaged_vid
            pos = self._find_by_vid(vid, hx, hy)
            if pos is not None and self._in_melee and self._bot_vid == vid and now - self._melee_since > 0.5:
                # klient czysci auto-atak po zabiciu celu - a aktor (zwl. metin) wisi w pamieci jeszcze ~2 s
                # animacji smierci. Dziennik: AUTOATAK -> 0, a 'padl' dopiero 1.7-2.5 s pozniej. Nie czekamy.
                try:
                    cleared = self.pm.read_uint(self.player_obj + self.farm_off["autoatk"]) == 0
                except Exception:
                    cleared = False
                if cleared:
                    self._dbg("cel VID %d: klient wyczyscil auto-atak -> zabity (walka wrecz %.1fs)", vid,
                              now - self._melee_since)
                    self._engage_cool[vid] = now + 10.0  # trup jeszcze w pamieci - nie wybieraj go znowu
                    self._bot_vid = 0
                    pos = None
            if pos is None:
                self._dbg("cel VID %d padl/zniknal (walka wrecz %.1fs, od wyboru %.1fs)", vid,
                          (now - self._melee_since) if self._melee_since else 0.0,
                          now - self._engaged_t0 if self._engaged_t0 else 0.0)
                self._end_target()              # cel padl/zniknal (klient sam czysci auto-atak)
            else:
                tx, ty, ism = pos
                d = math.hypot(tx - hx, ty - hy)
                if d < self._engaged_best - 30:      # realny postep -> odswiez licznik bez-postepu
                    self._engaged_best = d
                    self._engaged_since = now
                fight_lim = FARM_FIGHT_TIMEOUT_BIG if self._engaged_big else FARM_FIGHT_TIMEOUT
                why = None
                if not self._in_melee and now - self._engaged_since > WALK_ENGAGE_TIMEOUT:
                    why = "brak postepu %.0fs" % WALK_ENGAGE_TIMEOUT
                elif self._melee_since and now - self._melee_since > fight_lim:
                    why = "walka > %.0fs bez skutku" % fight_lim
                elif (self._in_melee and d > (MELEE_CONTACT_BIG if self._engaged_big else MELEE_CONTACT)
                      and now - self._engaged_since > MELEE_STALL_SEC):
                    # atak oddany klientowi, ale postac nie dochodzi do celu (cos ja blokuje po drodze)
                    why = "klient nie dochodzi (d=%.0f bez zmian %.0fs)" % (d, MELEE_STALL_SEC)
                elif leash_fn is not None and leash_fn(tx, ty, self._engaged_big):
                    why = "uciekl za daleko"
                if why is None:
                    self._engage(hx, hy, vid, tx, ty, ism)
                    return True
                self._abandon(vid, why)
        tgt = pick_fn(hx, hy)
        if tgt is None:
            self._appr = None
            self._clear_autoattack()
            self._eng_route = []
            self._combat_target = None
            self._combat_status = "brak celu"
            return False
        vid, tx, ty, ism, big = tgt
        d = math.hypot(tx - hx, ty - hy)
        if not vid:
            # metin widoczny tylko z efektu: podchodzimy BEZ zamka; gdy wczyta sie aktor z VID, nastepny
            # wybor zwroci juz prawdziwy cel. Bez postepu przez WALK_ENGAGE_TIMEOUT -> czarna lista.
            key = self._fx_key(tx, ty)
            if self._appr is None or self._appr[0] != key:
                self._appr = [key, now, d]
            elif d < self._appr[2] - 30:
                self._appr[1], self._appr[2] = now, d
            elif now - self._appr[1] > WALK_ENGAGE_TIMEOUT:
                self._engage_cool[key] = now + WALK_ENGAGE_COOL
                self._appr = None
                print("[farm] porzucam podejscie do metina (efekt) @(%.0f,%.0f)" % (tx, ty))
                return False
            self._engage(hx, hy, 0, tx, ty, True)
            self._combat_status = "podejscie do metina (efekt) d=%.0f" % d
            return True
        self._appr = None
        self._end_target()
        self._engaged_vid = vid                  # nowy zamek: ten cel konczymy; zaczynamy liczyc postep
        self._engaged_t0 = now
        self._dbg("nowy cel VID %d d=%.0f %s%s", vid, d, "metin" if ism else "mob", " (duzy)" if big else "")
        self._engaged_since = now
        self._engaged_best = d
        self._engaged_big = big
        self._engage(hx, hy, vid, tx, ty, ism)
        return True

    def _end_target(self):
        """Wyczysc stan biezacego celu (bez dotykania auto-ataku w kliencie)."""
        if self._engaged_vid:
            self._um_grace_until = time.time() + UNSTICK_GRACE   # po walce postac rusza powoli - nie 'utknela'
        self._engaged_vid = 0
        self._engaged_since = 0.0
        self._engaged_best = 1e18
        self._engaged_big = False
        self._in_melee = False
        self._melee_since = 0.0
        self._eng_route = []
        self._eng_route_goal = None
        self._combat_target = None

    def _abandon(self, vid, why):
        """Porzuc zablokowany cel: czarna lista na WALK_ENGAGE_COOL s + zdejmij nasz wpis auto-ataku."""
        self._engage_cool[vid] = time.time() + WALK_ENGAGE_COOL
        print("[farm] porzucam VID %d: %s" % (vid, why))
        self._combat_status = "porzucono VID %d (%s)" % (vid, why)
        self._clear_autoattack()
        self._end_target()

    def _reset_combat(self):
        """Zwolnij zamek na cel + nasz wpis auto-ataku + stan odblokowania (nudge). Wolane Z PETLI
        (GUI ustawia tylko _reset_req), by stary cel/licznik utkniecia nie przenosil sie miedzy sesjami."""
        self._clear_autoattack()
        self._end_target()
        self._appr = None
        self._um_pos = None
        self._nudge_until = 0.0
        self._nudge_n = 0
        self._walk_fighting = False
        self._combat_status = ""

    def _clear_autoattack(self):
        """Zdejmij VID z auto-ataku klienta - TYLKO gdy to nasz wpis (nie kasuj recznego ataku gracza)."""
        if not self._bot_vid:
            return
        try:
            va = self.player_obj + self.farm_off["autoatk"]
            if self.pm.read_uint(va) == self._bot_vid:
                self.pm.write_uint(va, 0)
        except Exception:
            pass
        self._bot_vid = 0

    def _entity_obstacles(self, hx, hy, include_mobs, exclude=None, exclude_r=200.0):
        """Pozycje encji jako przeszkody dla A*: metiny (zawsze) + moby (gdy include_mobs).
        Moby tylko w pasie OBST_MOB_MIN..OBST_MOB_MAX od postaci (bliskie nas bija/ruszaja sie, dalekie
        i tak zmienia miejsce); inni gracze pomijani. exclude: punkt (wx,wy) celu - wokol niego bez przeszkod."""
        obs = [(mx, my) for mx, my, _ in self.metins]
        if include_mobs:
            mobs, _ = self._mobs_snapshot(hx, hy)
            for mx, my, vnum, is_pc, vid in mobs:
                if is_pc:
                    continue
                if OBST_MOB_MIN <= math.hypot(mx - hx, my - hy) <= OBST_MOB_MAX:
                    obs.append((mx, my))
        if exclude is not None:
            ex, ey = exclude
            obs = [(x, y) for (x, y) in obs if math.hypot(x - ex, y - ey) > exclude_r]
        return obs

    def _hard_obstacles(self, hx, hy, ax, ay, exclude=None, exclude_r=350.0):
        """Twarde przeszkody dla A* (nie zwalniane przy starcie): zapamietane miejsca utkniec (niewygasle)
        + moby blizej FRONT_MOB_R, ktore stoja PRZED postacia (w kierunku (ax,ay)). exclude: cel ataku."""
        now = time.time()
        self._learned = [l for l in self._learned if l[2] > now]
        # miejsce utkniecia = plama ~5x5 komorek (po pogrubieniu w A*): nie znamy rozmiaru przeszkody
        blobs = [(x + ox, y + oy) for x, y, _ in self._learned
                 for ox, oy in ((0, 0), (100, 0), (-100, 0), (0, 100), (0, -100))]
        hard = []
        d = math.hypot(ax - hx, ay - hy) or 1.0
        dx, dy = (ax - hx) / d, (ay - hy) / d
        mobs, _ = self._mobs_snapshot(hx, hy)
        for mx, my, vnum, is_pc, vid in mobs:
            md = math.hypot(mx - hx, my - hy)
            if is_pc or not (1.0 < md < FRONT_MOB_R):
                continue
            if ((mx - hx) * dx + (my - hy) * dy) / md > 0.3:     # w stozku ~70 st. przed postacia
                hard.append((mx, my))
        if exclude is not None:
            ex, ey = exclude
            hard = [(x, y) for (x, y) in hard if math.hypot(x - ex, y - ey) > exclude_r]
            blobs = [(x, y) for (x, y) in blobs if math.hypot(x - ex, y - ey) > exclude_r]
        return hard, blobs

    def _extend_aim(self, hx, hy, tgt):
        """Cel ruchu nie blizej niz WALK_MIN_AIM: przedluz odcinek postac->tgt, gdy droga na wprost wolna
        (siatka + zapamietane przeszkody). Kon nie dojezdza wtedy do celu przed kolejnym !goto i nie staje."""
        d = math.hypot(tgt[0] - hx, tgt[1] - hy)
        if d < 1.0 or d >= WALK_MIN_AIM:
            return tgt
        ex = hx + (tgt[0] - hx) / d * WALK_MIN_AIM
        ey = hy + (tgt[1] - hy) / d * WALK_MIN_AIM
        return (ex, ey) if self._segment_clear(hx, hy, ex, ey) else tgt

    def _segment_clear(self, x0, y0, x1, y1, clear_r=250.0):
        """Droga na wprost wolna: siatka kolizji (line_clear) ORAZ odcinek nie przechodzi blizej clear_r od
        zapamietanego miejsca utkniecia (inaczej celowanie z wyprzedzeniem scinaloby objazd przeszkody)."""
        if self.collision.ok and not self.collision.line_clear(x0, y0, x1, y1):
            return False
        L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
        now = time.time()

        def near_seg(px, py, r):
            t = 0.0 if L2 < 1.0 else max(0.0, min(1.0, ((px - x0) * (x1 - x0) + (py - y0) * (y1 - y0)) / L2))
            return math.hypot(px - (x0 + (x1 - x0) * t), py - (y0 + (y1 - y0) * t)) < r
        for lx, ly, exp in self._learned:
            if exp > now and near_seg(lx, ly, clear_r):
                return False
        # moby na odcinku (poza tymi tuz przy starcie - te omija A*/odblokowanie): przez potwora nie przejedziemy
        for mx, my, vnum, is_pc, vid in self._mobs_snapshot(x0, y0)[0]:
            if not is_pc and math.hypot(mx - x0, my - y0) > 120 and near_seg(mx, my, MOB_CLEAR_R):
                return False
        return True

    def _blocker_check(self, hx, hy, dx, dy):
        """Wolane przy PONOWNYM utknieciu: wybiera JEDNEGO moba blokujacego - z VID, blizej BLOCKER_R,
        najbardziej w kierunku jazdy (dx,dy); za postacia (kat > ~70 st.) nie blokuje. Walka w drodze / farm
        wybiora go przez _blocker_target. True = obsluzone (bijemy)."""
        if not self.farm_off or self.player_obj is None:
            return False
        mobs, _ = self._mobs_snapshot(hx, hy)
        cands = []                                      # (d, front, vid, vnum)
        for mx, my, vnum, is_pc, vid in mobs:
            d = math.hypot(mx - hx, my - hy)
            if is_pc or not vid or not (1.0 < d < BLOCKER_R) or self._vid_blocked(vid):
                continue
            if self._classify(vnum, False) == "npc":
                continue
            cands.append((d, ((mx - hx) * dx + (my - hy) * dy) / d, vid, vnum))
        # 1) mob w KONTAKCIE (< BLOCKER_CONTACT) - najblizszy, z dowolnej strony (bije nas / trzyma);
        # 2) inaczej najblizszy PRZED postacia. (Wybor "najbardziej na wprost" bral moba z 278 jedn., a klient
        #    nie mogl do niego dojsc przez tego tuz przy postaci - 11 s stania.)
        contact = [c for c in cands if c[0] < BLOCKER_CONTACT]
        front = [c for c in cands if c[1] > 0.3]
        pick = min(contact) if contact else (min(front) if front else None)
        self._dbg("blokujacy - kandydaci: %s -> %s",
                  ", ".join("vid %d vnum %d d=%.0f przod=%.1f" % (c[2], c[3], c[0], c[1]) for c in sorted(cands))
                  or "brak", ("VID %d" % pick[2]) if pick else "nikt")
        if pick is None:
            return False
        best = (pick[1], pick[2], pick[0])
        now = time.time()
        self._blocker_vid = best[1]
        self._blockers_until = now + BLOCKER_SEC
        if self._engaged_vid and not self._in_melee:    # szlismy do dalszego celu - najpierw blokujacy
            self._end_target()
        self._last_combat = self._last_farm = 0.0       # walka rusza w nastepnym ticku
        self._nudge_n = 0                               # po zabiciu znow najpierw objazd
        self._um_t, self._um_pos = now, (hx, hy)
        self._dbg("utkniecie @(%.0f,%.0f) mimo objazdu -> bije blokujacego VID %d (d=%.0f)", hx, hy, best[1], best[2])
        return True

    def _blocker_target(self, hx, hy):
        """Mob blokujacy postac (jeden, z _blocker_check) dopoki zyje i trwa okno: (vid, tx, ty, False, False)
        albo None. Padl/zniknal/porzucony/koniec okna -> zapominamy go i jedziemy dalej."""
        vid = self._blocker_vid
        if not vid:
            return None
        _, v2p = self._mobs_snapshot(hx, hy)
        pos = v2p.get(vid)
        if time.time() >= self._blockers_until or pos is None or self._vid_blocked(vid):
            self._blocker_vid = 0
            self._blockers_until = 0.0
            return None
        return (vid, pos[0], pos[1], False, False)

    def _stuck_nudge(self, hx, hy, tx, ty):
        """Gdy postac realnie STOI mimo wysylanego ruchu (WALK_UNSTICK_SEC): zapamietuje miejsce przed nia
        jako przeszkode (_learned) i wymusza nowa trase. Za 1. razem tylko to; przy kolejnym utknieciu dodatkowo
        krok w BOK (wolna strona wg siatki, obie zablokowane -> cofniecie), trzymany WALK_NUDGE_HOLD s.
        Zwraca True gdy wolajacy ma w tym ticku nic nie wysylac."""
        now = time.time()
        if now < self._nudge_until:
            return True
        if now < self._um_grace_until:                  # tuz po walce: rozpedzanie to nie utkniecie
            self._um_pos, self._um_t = (hx, hy), now
            return False
        if self._um_pos is None or math.hypot(hx - self._um_pos[0], hy - self._um_pos[1]) > WALK_MOVE_EPS:
            if self._um_pos is not None:
                self._nudge_n = 0                       # realny ruch -> licznik eskalacji od zera
            self._um_pos = (hx, hy)
            self._um_t = now
            return False
        if now - self._um_t < WALK_UNSTICK_SEC:
            return False
        d = math.hypot(tx - hx, ty - hy) or 1.0
        dx, dy = (tx - hx) / d, (ty - hy) / d
        # 2.+ utkniecie (objazd nie pomogl) i mob przed postacia - to on blokuje: zabij go (ostatecznosc)
        if self._nudge_n >= 1 and self._blocker_check(hx, hy, dx, dy):
            return True
        # utkniecie: blokuje nas cos spoza siatki kolizji (drzewo/skala) - zapamietaj to miejsce przed postacia
        # jako przeszkode (LEARN_TTL s) i wymus nowa trase, ktora je ominie. To samo miejsce -> odnow wpis
        # (zamiast mnozyc punkty, ktore zamurowalyby postac ze wszystkich stron)
        lx, ly = hx + dx * LEARN_AHEAD, hy + dy * LEARN_AHEAD
        self._learned = [l for l in self._learned if l[2] > now and math.hypot(l[0] - lx, l[1] - ly) > 120][-11:]
        self._learned.append((lx, ly, now + LEARN_TTL))
        self._last_plan = 0.0
        self._eng_route_t = 0.0
        self._dbg("utkniecie @(%.0f,%.0f) cel d=%.0f -> przeszkoda zapamietana (proba %d)",
                  hx, hy, d, self._nudge_n + 1)
        if self._nudge_n == 0:                          # 1. raz: sama nowa trasa (bez kroku w bok)
            self._nudge_n = 1
            self._last_goto = 0.0                       # nowa trasa leci od razu w nastepnym ticku
            self._um_t = now
            self._um_pos = (hx, hy)
            return True
        aim = None
        for side in (self._um_side, -self._um_side):
            px, py = -dy * side, dx * side
            ax = hx + px * 500 + dx * 200               # w bok + lekko do przodu
            ay = hy + py * 500 + dy * 200
            if not self.collision.ok or self.collision.line_clear(hx, hy, ax, ay):
                aim = (ax, ay)
                self._um_side = -side                   # nastepnym razem najpierw druga strona
                break
        if aim is None:                                 # obie strony w scianie -> cofnij sie
            aim = (hx - dx * 400, hy - dy * 400)
        self._goto(aim[0], aim[1], "nudge", hx, hy)
        self._nudge_until = now + WALK_NUDGE_HOLD
        self._nudge_n += 1
        self._last_goto = now
        self._um_t = now + WALK_NUDGE_HOLD
        self._um_pos = (hx, hy)
        return True

    def _engage_path(self, hx, hy, tx, ty):
        """Trasa A* dojscia do celu z CACHE: liczona najwyzej co ENGAGE_REPLAN s albo gdy cel przesunal
        sie o > ENGAGE_REPLAN_MOVE (takze nieudana - nie ponawiamy co tick). Osiagniete wezly odcinane."""
        if not self.collision.ok:
            return None
        now = time.time()
        g = self._eng_route_goal
        if (g is not None and math.hypot(tx - g[0], ty - g[1]) < ENGAGE_REPLAN_MOVE
                and now - self._eng_route_t < ENGAGE_REPLAN):
            r = self._eng_route
            while len(r) > 1 and math.hypot(r[0][0] - hx, r[0][1] - hy) < 150:
                r = r[1:]
            self._eng_route = r
            return r or None
        self.collision.refresh()            # aktualny cur/m_pTerrain (inaczej mapowanie sektorow sprzed skanu)
        obst = self._entity_obstacles(hx, hy, include_mobs=True, exclude=(tx, ty), exclude_r=350.0)
        t0 = time.time()
        hard, blobs = self._hard_obstacles(hx, hy, tx, ty, exclude=(tx, ty), exclude_r=350.0)
        route = self.collision.find_path(hx, hy, tx, ty, extra_blocked=obst, hard_blocked=hard, hard_blobs=blobs)
        self._dbg("plan cel d=%.0f: %s, %.0f ms, przeszkod %d", math.hypot(tx - hx, ty - hy),
                  ("%d wezlow" % len(route)) if route else "BRAK TRASY", (time.time() - t0) * 1000, len(obst))
        self._eng_route = route or []
        self._eng_route_t = now
        self._eng_route_goal = (tx, ty)
        return route

    def _engage(self, hx, hy, vid, tx, ty, is_metin):
        """Zajmij sie celem. Walka wrecz (atak oddany klientowi przez wpis VID) z HISTEREZA: wejscie przy
        d <= FARM_MELEE (albo d <= FARM_HANDOFF bez postepu FARM_HANDOFF_SEC - duzy boss/metin), wyjscie
        dopiero przy d > FARM_MELEE_EXIT. Poza walka: zdejmij nasz VID i dojdz A* (metiny+moby jako
        przeszkody, cel wykluczony) z odblokowaniem. vid == 0 -> samo podejscie (metin z efektu)."""
        now = time.time()
        d = math.hypot(tx - hx, ty - hy)
        if vid:
            if self._in_melee:
                if d > FARM_MELEE_EXIT:
                    self._dbg("wrecz KONIEC VID %d - cel odszedl d=%.0f", vid, d)
                    self._in_melee = False
            elif d <= FARM_MELEE or (d <= FARM_HANDOFF and now - self._engaged_since > FARM_HANDOFF_SEC):
                self._dbg("wrecz START VID %d d=%.0f (%s)", vid, d,
                          "zasieg" if d <= FARM_MELEE else "oddanie klientowi - brak postepu")
                self._in_melee = True
                if not self._melee_since:               # limit walki liczony od PIERWSZEGO wejscia
                    self._melee_since = now
        self._combat_target = (tx, ty, bool(vid and self._in_melee))
        if vid and self._in_melee:
            try:
                va = self.player_obj + self.farm_off["autoatk"]
                if self.pm.read_uint(va) != vid:
                    self.pm.write_uint(va, vid)
                self._bot_vid = vid
            except Exception:
                pass
            self._um_pos = None                         # w walce nie liczymy "utkniecia"
            self._nudge_n = 0
            self._eng_route = []
            self._combat_status = "walka VID %d  %.0fs  d=%.0f" % (vid, now - self._melee_since, d)
            return
        self._clear_autoattack()                        # nie pozwol klientowi podchodzic (bumpuje przeszkody)
        if vid:
            self._combat_status = "dojscie VID %d  d=%.0f" % (vid, d)
        if not (self.native_drink or self.dll_exec):
            return
        if self._stuck_nudge(hx, hy, tx, ty):           # wbity w moba -> krok w bok
            return
        route = self._engage_path(hx, hy, tx, ty)
        nxt = self._route_aim(route, 0, hx, hy) if route else (tx, ty)  # celuj z wyprzedzeniem (konne luki)
        self._goto(nxt[0], nxt[1], "engage", hx, hy)
        self._last_goto = now

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
        Gdy brak trasy (cel nieosiagalny) - jedzie wprost (best effort), A* ponawia co WALK_PLAN_RETRY.
        Przy wlaczonej farmie chodzenie czeka (farma trzyma postac w okregu). vCore: hook; GameCoreTop: pending."""
        path = self.path                               # snapshot: GUI podmienia liste (nie mutuje jej)
        if not self.autowalk_on or not path:
            return
        if not (self.native_drink or self.dll_exec):   # vCore: hook; GameCoreTop: pending-call
            return
        if self.farm_on:                               # farm i chodzenie naraz walczylyby o !goto
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        now = time.time()
        self.collision.set_mapout(self.mapout_obj)
        self.collision.refresh()
        # opcja: w drodze zatrzymaj sie i zbij metina/bossa/specjalnego w poblizu (zamek na cel).
        # Logika walki co COMBAT_EVERY (nie co tick petli) - miedzy tickami trzymamy ostatni wynik.
        # Niezaleznie od opcji: moby, ktore nas zablokowaly (_blockers_until), bijemy zawsze - inaczej stoimy.
        if ((self.walk_fight or now < self._blockers_until or self._walk_fighting)
                and self.farm_off and self.player_obj is not None):
            if now - self._last_combat >= COMBAT_EVERY:
                self._last_combat = now
                self._walk_fighting = self._combat_tick(
                    hx, hy, lambda hx, hy: self._blocker_target(hx, hy) or (
                        self._nearby_fight_target(hx, hy, WALK_FIGHT_RANGE) if self.walk_fight else None),
                    lambda tx, ty, big: math.hypot(tx - hx, ty - hy) > WALK_FIGHT_RANGE * 1.5)
            if self._walk_fighting:
                self._walk_prog = None          # walczymy -> nie progresuj waypointa
                self._route = []                # po walce trasa od nowa (stara zostala za plecami)
                self._last_plan = 0.0
                return
        self.walk_idx %= len(path)
        tx, ty = path[self.walk_idx]
        # przejdz przez osiagniete waypointy BEZ zatrzymania (od razu kieruj do nastepnego = plynnie)
        adv = 0
        while math.hypot(tx - hx, ty - hy) < WALK_REACH and adv < len(path):
            self.walk_idx = (self.walk_idx + 1) % len(path)
            tx, ty = path[self.walk_idx]
            self._walk_prog = None
            self._route = []
            self._last_plan = 0.0
            adv += 1
        d = math.hypot(tx - hx, ty - hy)
        if d < WALK_REACH:                           # jedyny/wszystkie punkty tuz obok - nic do roboty
            return

        # detekcja utkniecia: brak postepu -> wymus replan (siatka moze sie zmienic / trasa nieaktualna)
        if self._walk_prog is None or d < self._walk_prog[1] - 20:
            self._walk_prog = (now, d)
        elif now - self._walk_prog[0] > WALK_STUCK_REPLAN:
            self._last_plan = 0.0
            self._walk_prog = (now, d)

        # (re)plan trasy A* po realnej siatce kolizji + METINY I MOBY jako przeszkody: okresowo, a gdy
        # trasy brak - po WALK_PLAN_RETRY od ostatniej proby (nieudany A* nie leci co 70 ms)
        age = now - self._last_plan
        if self.collision.ok and ((not self._route and age > WALK_PLAN_RETRY) or age > WALK_PLAN_SEC):
            obst = self._entity_obstacles(hx, hy, include_mobs=True)
            t0 = time.time()
            hard, blobs = self._hard_obstacles(hx, hy, tx, ty)
            p = self.collision.find_path(hx, hy, tx, ty, extra_blocked=obst, hard_blocked=hard, hard_blobs=blobs)
            self._dbg("plan wp%d d=%.0f: %s, %.0f ms, przeszkod %d (twardych %d)", self.walk_idx + 1, d,
                      ("%d wezlow" % len(p)) if p else "BRAK TRASY", (time.time() - t0) * 1000, len(obst),
                      len(hard) + len(blobs))
            self._last_plan = now
            self._route = p or []
            self._route_idx = 0

        # wybierz biezacy cel: punkt trasy A* (omija sciany) albo wprost waypoint (gdy brak trasy).
        # Trasa moze konczyc sie w punkcie POSREDNIM (daleki waypoint) - po dojsciu planujemy dalej.
        tgt = (tx, ty)
        route = self._route
        if route:
            ri = self._route_idx
            while ri < len(route) and math.hypot(route[ri][0] - hx, route[ri][1] - hy) < WALK_REACH:
                ri += 1
            self._route_idx = ri
            if ri >= len(route):
                self._route = []                    # przeszlismy trase -> replan od razu
                self._last_plan = 0.0
            else:
                tgt = self._route_aim(route, ri, hx, hy)  # celuj z wyprzedzeniem (luki)
        tgt = self._extend_aim(hx, hy, tgt)          # nie blizej niz WALK_MIN_AIM (kon nie staje miedzy !goto)

        if self._stuck_nudge(hx, hy, tgt[0], tgt[1]):   # wbity w moba/przeszkode -> krok w bok (trzymany)
            return
        if now - self._last_goto >= WALK_RESEND:     # ponawiaj biezacy cel (keep-alive/korekta)
            self._goto(tgt[0], tgt[1], "walk", hx, hy)
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
                grid = self.collision.block_grid(cx - R, cy - R, cx + R, cy + R)
                for j, i in zip(*np.nonzero(grid)):
                    cells.append(((cx - R + int(i)) * cell, (cy - R + int(j)) * cell))  # rog komorki (swiat)
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
        """Rysuje okrag zasiegu auto-farmu (srodek = punkt wlaczenia) oraz biezacy cel walki:
        linia od postaci + pierscien (zolty = walka wrecz, pomaranczowy = dojscie) i trase dojscia A*."""
        if self.farm_on and self.farm_center is not None:
            cx, cy = self.farm_center
            sx, sy = self.world_to_canvas(cx, cy, hx, hy)
            scale = self._follow_scale() if self.mode == "follow" else self._full_scale()
            r = self.farm_radius / map_assets.WORLD_PER_PX * scale
            c.create_oval(sx - r, sy - r, sx + r, sy + r, outline="#ff6a6a", width=2)
        tgt = self._combat_target                 # krotka podmieniana przez petle - jeden odczyt
        if tgt is None or not (self.farm_on or (self.autowalk_on and self.walk_fight)):
            return
        hsx, hsy = self.world_to_canvas(hx, hy, hx, hy)
        route = self._eng_route
        if route:
            rp = [(hsx, hsy)] + [self.world_to_canvas(rx, ry, hx, hy) for rx, ry in route]
            c.create_line(*[v for p in rp for v in p], fill="#ff9f1a", width=2, dash=(3, 2))
        tx, ty, melee = tgt
        tsx, tsy = self.world_to_canvas(tx, ty, hx, hy)
        col = "#ffe14d" if melee else "#ff9f1a"
        c.create_line(hsx, hsy, tsx, tsy, fill=col, width=1)
        c.create_oval(tsx - 11, tsy - 11, tsx + 11, tsy + 11, outline=col, width=2)

    def _draw_learned(self, c, hx, hy):
        """Zapamietane przeszkody (miejsca utkniec, spoza siatki kolizji) - fioletowe X, do wygasniecia."""
        now = time.time()
        for lx, ly, exp in self._learned:
            if exp <= now:
                continue
            sx, sy = self.world_to_canvas(lx, ly, hx, hy)
            if self._in_view(sx, sy):
                c.create_line(sx - 5, sy - 5, sx + 5, sy + 5, fill="#c86bff", width=2)
                c.create_line(sx - 5, sy + 5, sx + 5, sy - 5, fill="#c86bff", width=2)

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
            rp = [self.world_to_canvas(hx, hy, hx, hy)]
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

    def _mount_check(self):
        """Czy jedziemy na wierzchowcu: aktor-kon (z rescan) blizej MOUNT_DIST od wlasnej postaci.
        Zwraca (True/False, nazwa_modelu) albo None, gdy nie znamy wlasnego aktora. 2 odczyty na aktora."""
        me = self.self_actor
        if me is None:
            return None
        mob_x = self.off["mob_x"]
        try:
            mx = self.pm.read_float(me + mob_x)
            my = self.pm.read_float(me + mob_x + 4)
        except Exception:
            return None
        for o, name in self._mount_cands:
            try:
                x = self.pm.read_float(o + mob_x)
                y = self.pm.read_float(o + mob_x + 4)
            except Exception:
                continue
            if math.hypot(x - mx, y - my) < MOUNT_DIST:
                return True, name
        return False, ""

    def _draw_mount(self, c):
        """Ikona konia obok paska HP: zlota = na koniu (z nazwa modelu), szara = pieszo, '?' = nie wiadomo."""
        st = self._mount_check()
        self.mounted = st
        x, y = 196, 15
        if st is None:
            col, txt = "#777", "? kon (brak wlasnego aktora)"
        elif st[0]:
            col, txt = "#ffcf4d", "NA KONIU (%s)" % st[1]
        else:
            col, txt = "#8a8a8a", "pieszo"
        c.create_text(x, y, anchor="w", fill=col, text="♞", font=("Segoe UI Symbol", 14, "bold"))
        c.create_text(x + 20, y, anchor="w", fill=col, text=txt, font=("Consolas", 8, "bold"))

    def _autopot_draw(self, c):
        """Pasek HP z dwiema liniami (pij<low zolta, lecz do high niebieska) + status. Z cache."""
        if not self._pts_valid or self._mhp <= 0:
            return
        hp, mhp, pct = self._hp, self._mhp, self._pct
        w = 180
        c.create_rectangle(2, 2, 560, 82, fill="#0b0f0b", outline="")     # tlo dla czytelnosci
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
                if self.farm_on:
                    walk_txt += "  (pauza: farm wlaczony)"
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
        # stan walki (farm / walka w drodze): cel, faza, porzucenia
        fighting = self.farm_on or (self.autowalk_on and self.walk_fight)
        if self.farm_on:
            fight_txt = "[V]farm WL  " + (self._combat_status or "...")
        elif self.autowalk_on and self.walk_fight:
            fight_txt = "walka w drodze  " + (self._combat_status or "...")
        else:
            fight_txt = "[V]farm WYL"
        c.create_text(8, 75, anchor="w", fill="#ff6a6a" if fighting else "#999",
                      text=fight_txt, font=("Consolas", 8))


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
