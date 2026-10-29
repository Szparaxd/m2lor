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
    Panel POD radarem: Pij <% (start), Lecz do % (cel), Odstep s (min. czas), Auto-loot + Zbieraj do (promien) + Natychmiast.
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
import random
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
import autobuff
import autopot
import bottrace
import botapi
import calibrate
import clients
import collision as collision_mod
import guard as guard_mod
import identity
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
# Pewniej: CActorInstance.m_pkHorse (ActorInstance.h) - wskaznik na aktora wierzchowca, NULL pieszo. vCore
# (2026-10-28, na zywo): +0x554 - u wlasnej postaci -> horse_event1, u innego gracza -> lion_3. Per build;
# brak wpisu = heurystyka odleglosci powyzej.
HORSE_PTR_OFFSETS = {"vcore.exe": 0x554}
MAPOUT_NAME = 0x8
WORLD_MIN, WORLD_MAX = 1.0, 10_000_000.0

CANVAS = 720
# Zapamietywanie pozycji+rozmiaru okna miedzy sesjami (data/ui_state.json) + domyslny "wysoki prostokat".
UI_STATE_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "data", "ui_state.json"))
DEFAULT_GEOMETRY = "470x900"   # szerokosc x wysokosc, gdy brak zapisanej geometrii


def _load_ui_state():
    try:
        with open(UI_STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def load_ui_geometry(char=None):
    """Zapisana geometria okna 'WxH+X+Y' (dla postaci `char`, inaczej ostatnia ogolna) albo None.
    Per postac: przy kilku radarach kazdy otwiera sie tam, gdzie zostal zamkniety jego klient."""
    st = _load_ui_state()
    g = (st.get("chars") or {}).get(char) if char else None
    g = g or st.get("geometry")
    return g if isinstance(g, str) and g else None


def save_ui_value(key, val):
    """Zapisz pojedyncza opcje UI (np. wybrane mikstury) do pliku stanu UI (ciche niepowodzenie)."""
    st = _load_ui_state()
    st[key] = val
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
    except Exception:
        pass


def save_ui_geometry(geo, char=None):
    """Zapisz geometrie okna (ogolna + per postac) do pliku stanu UI (ciche niepowodzenie)."""
    st = _load_ui_state()
    st["geometry"] = geo
    if char:
        st.setdefault("chars", {})[char] = geo
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
    except Exception:
        pass


# Trasy auto-chodzenia zapisywane PER MAPA (data/routes/<mapa>.json) - po restarcie radaru / powrocie na mape
# waypointy wracaja same. Zapis przy kazdej zmianie (Ctrl+klik, [C]).
ROUTES_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "routes"))


def load_route(map_name):
    """Zapisane waypointy mapy [(wx, wy)] albo []."""
    try:
        with open(os.path.join(ROUTES_DIR, "%s.json" % map_name), encoding="utf-8") as f:
            return [(float(x), float(y)) for x, y in json.load(f).get("wps", [])]
    except Exception:
        return []


def save_route(map_name, path):
    """Zapisz waypointy mapy (pusta lista = plik z pusta trasa). Ciche niepowodzenie."""
    try:
        os.makedirs(ROUTES_DIR, exist_ok=True)
        tmp = os.path.join(ROUTES_DIR, "%s.json.tmp" % map_name)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"map": map_name, "wps": [[round(x), round(y)] for x, y in path]}, f)
        os.replace(tmp, os.path.join(ROUTES_DIR, "%s.json" % map_name))
    except Exception as e:
        print("[walk] zapis trasy nieudany: %s" % e)


def resume_index(path, hx, hy):
    """Indeks waypointu, do ktorego isc po WZNOWIENIU: trasa jest petla (i -> i+1 -> ... -> 0), wiec bierzemy
    odcinek petli najblizszy postaci i idziemy do jego KONCA - postac rusza dalej po trasie, zamiast wracac
    do punktu 1. Stojac tuz przy waypoincie (<= 250 jedn.) - od razu nastepny."""
    n = len(path)
    if n <= 1:
        return 0
    best = None
    for i in range(n):
        ax, ay = path[i]
        bx, by = path[(i + 1) % n]
        L2 = (bx - ax) ** 2 + (by - ay) ** 2
        t = 0.0 if L2 < 1.0 else max(0.0, min(1.0, ((hx - ax) * (bx - ax) + (hy - ay) * (by - ay)) / L2))
        d = math.hypot(hx - (ax + (bx - ax) * t), hy - (ay + (by - ay) * t))
        if best is None or d < best[0]:
            best = (d, (i + 1) % n)
    idx = best[1]
    if math.hypot(path[idx][0] - hx, path[idx][1] - hy) <= 250.0:
        idx = (idx + 1) % n
    return idx


RESCAN_SEC = 1.5    # okres skanu pamieci w tle (nowe moby/metiny); sam skan ~0.2 s
# Dziennik diagnostyczny ruchu/walki: KAZDA sesja radaru dostaje wlasne pliki w data/logs/ (recon/bottrace.py):
# <stamp>_radar_walk.log (czytelny) + <stamp>_radar_trace.jsonl (pelny slad do odtworzenia: recon/replay.py).
# Wylacz (False), gdy niepotrzebny.
WALK_DEBUG = True
TRACE_ST_EVERY = 0.2      # s: migawka stanu 'st' w sladzie JSONL (gdy chodzenie/farm wlaczone)
TRACE_GRID_EVERY = 3.0    # s: wycinek siatki kolizji wokol postaci w sladzie
TRACE_GRID_R = 50         # komorek (x100 jedn.) w kazda strone od postaci w rekordzie 'grid'
TRACE_MOB_R = 2500.0      # jedn.: aktorzy w tym promieniu trafiaja do migawki 'st'
# Biezaca animacja aktora: CActorInstance.m_pkCurRaceMotionData lezy TUZ ZA m_pkCurRaceData (off "race").
# CRaceMotionData ma WIRTUALNY destruktor: +0 vtable, +4 m_eType, +8 m_eName. Czytamy TYP (EType z
# RaceMotionData.h) - nie zalezy od broni/konia (kombo na koniu = TYPE_COMBO jak pieszo). Zweryfikowane na zywo:
# pary (typ, nazwa) (1,1) WAIT/WAIT, (2,3) MOVE/RUN, (9,7) STANDUP/STAND_UP; walka z metinem na koniu = typ 4.
# (Wczesniejsza wersja brala +4 za m_eName -> COMBO(4) czytane jako "nie atakuje" -> falszywe 'zmiany strony'.)
MOTION_PTR_FROM_RACE = 4
MOTION_TYPE_OFF = 4
MOT_WAIT, MOT_MOVE = 1, 2
MOT_ATTACK = {3, 4, 8}                # TYPE_ATTACK, TYPE_COMBO, TYPE_SKILL - aktor atakuje
MOT_DAMAGE = {5, 6, 9}                # TYPE_DAMAGE, TYPE_KNOCKDOWN, TYPE_STANDUP - aktor dostal
MOT_DEAD = {7}                        # TYPE_DIE
# Walka oparta na DOWODACH: atak oddany klientowi (wpis VID), ale wlasna postac nie gra animacji ataku, a cel
# nie gra 'obrazen' -> klient nie moze uderzyc (np. metin za plotem/namiotem - obiekty mapy nie sa w siatce
# kolizji). Wtedy podchodzimy z INNEJ strony celu (do MELEE_SIDE_TRIES razy), potem porzucamy cel.
MELEE_FIRST_HIT_SEC = 3.5     # s od oddania ataku bez zadnego dowodu -> zmiana strony
MELEE_GAP_SEC = 5.0           # s przerwy w dowodach w trakcie walki -> zmiana strony
MELEE_SIDE_TRIES = 3          # tyle zmian strony, potem porzucenie celu
# Przygotowanie do walki z BOSSEM / SPECJALNYM (opcje w panelu): w odleglosci PREP_RANGE od celu zsiadamy
# z konia ('/unmount' - komenda czatu, jak Ctrl+J) i rzucamy buffy PREP_BUFFS (Szal, Aura Miecza) - dopiero
# potem walka. Po walce (cel padl/porzucony) - znow na konia ('/ride', jak Ctrl+G), jesli zsiadalismy MY.
PREP_CLASSES = ("boss", "king", "special")
PREP_RANGE = 1200.0       # jedn.: tak blisko celu zaczynamy przygotowanie (dalej - dojazd na koniu)
PREP_BUFFS = (3, 4)       # vnum: Szal (Berserk), Aura Miecza - kolejnosc rzucania
PREP_TIMEOUT = 12.0       # s na cale przygotowanie (potem i tak walka)
UNMOUNT_RETRY = 1.5       # s miedzy probami zsiadania
UNMOUNT_TRIES = 3
REMOUNT_DELAY = 2.0       # s po walce (loot) zanim wsiadziemy
REMOUNT_RETRY = 2.5
REMOUNT_TRIES = 2
REPOSITION_TIMEOUT = 5.0      # s na dojscie do nowego punktu podejscia
REPOSITION_REACH = 160.0      # jedn.: tak blisko punktu podejscia = dotarlismy
# Auto-mikstura (recon/autopot.py): HP z CPythonPlayer, "nacisniecie" slotu w oknie gry.
PLAYER_CLASS = "CPythonPlayer"
POTION_KEY = "1"          # slot paska z czerwonymi miksturami (gdy nie uzywamy vnum)
# Histereza: zaczynamy pic ponizej LOW, lejemy az HP dojdzie do HIGH (HIGH >= LOW).
POTION_LOW = 50           # % HP - zacznij pic ponizej (suwak "Pij <", klawisze [ / ])
POTION_HIGH = 75          # % HP - lej az do tego poziomu (suwak "Lecz do")
POTION_COOLDOWN = 0.3     # s - min. odstep miedzy miksturami (suwak "Odstep")
POTION_POLL = 0.07        # s - jak czesto dedykowana petla sprawdza HP (szybka reakcja, nie co klatke)
# Picie "po ludzku". Czerwona mikstura NIE leczy od razu: serwer dodaje VALUE0 z item_proto do
# POINT_HP_RECOVERY i HP rosnie porcjami przez kilka sekund. Stara logika (cd 0.3 s, potem "pierwszy
# wzrost HP = zadzialalo") pila kolejne zanim doszla reszta -> serie 3/s, potem serie po 4 (odcisk
# limitu okna). Teraz liczymy HP "w drodze" (_pot_expect) i nie pijemy, gdy HP + w_drodze >= "Lecz do";
# odstepy losowe jak u czlowieka, po wskrzeszeniu chwila zwloki. Syslog: logs/chNN/<data>/syslog.HH.
POTION_HEAL = {27001: 300, 27051: 300, 27002: 800, 27003: 1200}   # VALUE0 z item_proto (Dump_Proto)
POTION_HEAL_SLOT = 300         # slot paska (nie wiemy, co lezy) - zakladamy M
POTION_RECOVERY_SEC = 4.0      # s - po tylu od ostatniej mikstury "w drodze" uznajemy za doszle/stracone
POTION_GAP = (0.6, 1.2)        # s - losowy odstep miedzy miksturami (max ~1.7/s, regula >=2/s nie lapie)
POTION_GAP_CRIT = (0.35, 0.7)  # s - odstep, gdy HP krytyczne (czlowiek tez wtedy klika szybciej)
POTION_CRIT_PCT = 25           # % HP - ponizej tego odstep krytyczny
POTION_REACT = (0.15, 0.5)     # s - "reakcja" po spadku HP ponizej progu, zanim pierwsza mikstura
POTION_RESPAWN = (1.5, 4.0)    # s - zwloka po wskrzeszeniu (nie pijemy w tej samej sekundzie)
POTION_RATE_MAX = 7            # bezpiecznik: max mikstur w oknie POTION_RATE_WINDOW (<1.5/s)
POTION_RATE_WINDOW = 5.0       # s
POTION_CONFIRM_TIMEOUT = 1.5   # s - brak JAKIEGOKOLWIEK wzrostu HP po wypiciu = mikstura "bez efektu"
POTION_NOGAIN_LIMIT = 3        # tyle mikstur z rzedu bez wzrostu HP -> pauza (zly odczyt / brak mikstur)
POTION_NOGAIN_PAUSE = 10.0     # s
# Auto-loot. Radar WIDZI drop (autopot.read_ground_items: mapa CPythonItem) i zbiera "jak gracz":
# po walce chwila reakcji, podbiega A* do przedmiotu, staje i "klika `" (PickCloseItem = klient bierze
# najblizszy <=300) w losowym rytmie, az nic nie zostanie. Farma/chodzenie czekaja. Cudze dropy
# (wlasciciel != nick) pomijane; przedmiot, ktory nie znika mimo klikania (pelne EQ?), na czarna liste.
# Tryb "Natychmiast" (GameCoreTop): pakiet podniesienia dla WSZYSTKICH VID w promieniu naraz, bez
# podbiegania - test, czy serwer sprawdza zasieg. Build bez czytnika dropow: stare PickCloseItem co 0.5 s.
LOOT_INTERVAL = 0.5       # s - stary tryb (brak czytnika dropow)
LOOT_RADIUS = 1500        # jedn. - domyslny promien "widze drop, ide po niego" (suwak "Zbieraj do")
LOOT_REACH = 200.0        # jedn. - tak blisko przedmiotu stajemy i klikamy (klient bierze <=300)
LOOT_REACT = (0.3, 0.9)   # s - reakcja na drop po walce, zanim ruszymy
LOOT_PRESS = (0.12, 0.28) # s - rytm "klikania `" (czlowiek ~4-7/s)
LOOT_PRESS_PAUSE = (0.08, (0.4, 0.9))   # szansa i dlugosc krotkiej przerwy w klikaniu
LOOT_EXTRA = (0, 2)       # ile "nadmiarowych" klikniec po ostatnim przedmiocie (czlowiek dociska)
LOOT_TRIES = 6            # tyle klikniec w zasiegu, a przedmiot dalej lezy -> czarna lista
LOOT_BLACKLIST = 60.0     # s
LOOT_FAIL_PAUSE = (2, 30.0)  # tyle czarnych list z rzedu -> pauza zbierania (pelne EQ?)
LOOT_INSTANT_EVERY = 1.0  # s - tryby Naraz/High: odstep miedzy paczkami
LOOT_HIGH_TRIES = 3       # High: po tylu paczkach wpis do dziennika (serwer odmawia - za daleko)
# High + podmiana pozycji (DLL '!tpick', GameCoreTop): dalsze dropy zbierane "skokiem" pozycji serwerowej
LOOT_SRV_REACH = 250.0    # jedn. - zasieg podnoszenia liczony przez serwer (log.GET: ~300) z zapasem
LOOT_TP_MAX = 2400.0      # jedn. - najdalszy skok (klasyczny limit serwera Metin2 "move too far" = 25 m)
LOOT_TP_EVERY = 0.35      # s - odstep miedzy skokami (jedno skupisko na skok)
# Auto-chodzenie (funkcja 3): postac idzie po liscie waypointow (wsp. swiata). Ruch natywny przez
# hook per-klatka (vCore): DLL wola NEW_MoveToDestPixelPositionDirection. Pakiety ruchu leca same.
GOTO_POINT_TIMEOUT = 180.0  # s: "podejdz tutaj" - po tym czasie bez dojscia rezygnuj (log)
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
FARM_FIGHT_TIMEOUT = 25.0     # s w walce wrecz BEZ DOWODU TRAFIENIA (od ostatniego; mob) -> cel-widmo, porzuc
FARM_FIGHT_TIMEOUT_BIG = 120.0  # j.w. dla metinow/bossow (dlugo sie bija)
WALK_BIG_FIGHT_MAX = 180.0      # s: w trybie chodzenia (bez farmy) max laczny czas na jednym metinie/bossie
# Obrona: moby, ktore BIJA postac, maja pierwszenstwo przed kazdym innym celem farmy (inaczej postac biegla do
# dalekiego moba/bossa, a najblizsze ja bily). Napastnik = mob w AGGRO_RANGE grajacy animacje ataku albo stojacy
# tuz przy postaci (AGGRO_CLOSE). Animacja ataku jest przerywana -> pamietamy napastnika AGGRO_MEMORY s.
AGGRO_RANGE = 500.0
AGGRO_CLOSE = 250.0
AGGRO_MEMORY = 3.0
# Walka wrecz bez KONTAKTU: VID oddany klientowi, a odleglosc stoi (> MELEE_CONTACT) przez MELEE_STALL_SEC -
# klient nie moze dojsc (blokuje go inny mob). Dziennik: 'walka VID .. d=278' bez zmian przez 11 s. Porzuc cel.
MELEE_CONTACT = 220.0
MELEE_CONTACT_BIG = 400.0     # metiny/bossy maja duzy obrys
MELEE_STALL_SEC = 3.0
COMBAT_EVERY = 0.25       # s: wspolny throttle logiki walki (farm i walka w drodze)
# Masowy atak pakietem (bez animacji, bez ruchu): SendAttackPacket do VID kazdego moba w promieniu.
# Tylko GameCoreTop (pending-call, patrz [[area-attack-packet]]). Serwer waliduje zasieg/cooldown; bez
# ruchu trafiaja tylko moby w serwerowym zasiegu wrecz. Dwa tryby: 'safe' (throttle + log zasiegu),
# 'aggro' (spam co petle, prowokuje detektor ATTACK(SPEED_HACK) serwera przy odstepie <0.1 s).
AREA_ATK_RADIUS = 3000.0  # jedn. swiata: domyslny promien masowego ataku
AREA_ATK_SAFE_EVERY = 0.30  # s miedzy paczkami w trybie realistycznym (>0.1 s = pod progiem speedhacka)
AREA_ATK_MOT = 0          # uMotAttack=0 = cios zwykly (serwer liczy normalne obrazenia broni)
AREA_ATK_MAX = 64         # tyle VID-ow na paczke (limit komendy DLL)
AREA_ATK_KINDS = {"mob", "boss", "king", "special"}  # "potwory" (bez npc/gracza; metiny to kamienie)
# Atak z PRZESKOKIEM pozycji (jak !tpick z lootu): dla mobow DALEJ niz serwerowy zasieg wrecz radar robi
# skok pozycji do skupiska, bije je i wraca - lokalnie postac stoi. Dalekie moby gina bez podchodzenia.
ATTACK_SRV_REACH = 250.0  # jedn.: serwerowy zasieg ataku wrecz (blizej - bij wprost, dalej - przez przeskok)
ATTACK_TP_MAX = 2400.0    # jedn.: najdalszy skok (limit serwera "move too far" ~25 m, jak przy loot-tpick)
ATTACK_HOP_EVERY = 0.35   # s: odstep miedzy skokami w trybie 'safe' (jedno skupisko na krok, jak loot High)
ATTACK_HOP_MAX_AGGRO = 6  # 'aggro': tyle skupisk na tik (szybkie czyszczenie calego obszaru; duzo skokow)
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
# Czerwone mikstury do wyboru w panelu (checkboxy M / Ś / D, zapamietane w ui_state.json "pot_sizes").
# Kolejnosc = priorytet picia: najwieksza zaznaczona, a gdy jej brak w EQ - nastepna mniejsza.
# Vnum z GameCore/Dump_Proto/item_names.txt (27051 = druga "Czerwona Mikstura (M)").
POTION_SIZES = [("D", (27003,)), ("Ś", (27002,)), ("M", (27001, 27051))]
POTION_SIZES_DEFAULT = ["M"]
POTION_VNUMS_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "..", "data", "potion_vnums.txt"))
# Peleryna (np. Peleryna Mestwa) na auto-farmie: co jakis czas, gdy w poblizu jest DOSC mobow,
# "uzyj" peleryne wprost z EQ (net.SendItemUsePacket - ta sama droga co picie mikstury po vnum).
# Tylko GameCoreTop (pending-call przez DLL). Vnum w data/cape_vnums.txt (edytowalny w locie; gdy
# obecnych kilka, uzyta pierwsza z EQ). Okres = przerwa miedzy uzyciami (~czas trwania/odnowienia
# buffa peleryny - dobierz pod serwer). Min. mobow = ile potworow w CAPE_RADIUS, zeby mialo to sens.
CAPE_RADIUS = 3000.0        # jedn. swiata: w tym promieniu liczymy potwory (jak masowy atak)
CAPE_PERIOD = 60.0          # s miedzy uzyciami peleryny (domyslnie; suwak w panelu)
CAPE_MIN_MOBS = 3           # tyle potworow w promieniu, zeby uzyc peleryny (domyslnie; suwak w panelu)
CAPE_KINDS = AREA_ATK_KINDS  # co liczymy jako "potwora" (bez npc/gracza; metiny to kamienie)
CAPE_VNUMS_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "data", "cape_vnums.txt"))

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
    def __init__(self, root, map_name=None, process=DEFAULT_PROCESS, pid=None, headless=False):
        # po PID: przy kilku klientach tego samego exe nazwa wskazalaby zawsze pierwszy (recon/clients.py)
        self.pm = pymem.Pymem(pid) if pid else pymem.Pymem(process)
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

        # tozsamosc postaci (recon/identity.py): nick/klasa/plec/poziom; odswiezana w _rescan_loop
        self.ident = {}
        self._nick_off = None          # offset nicku w CPythonPlayer (sonda raz, potem staly)
        self._db_cls = {}              # nick -> (klasa, plec) z bazy (zapas, 1 zapytanie na nick)
        self._ident_shown = None       # co pokazuje naglowek/tytul (zmiana -> odswiez w tick)
        self._face_img = None
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
        self._pot_unconfirmed = None              # czas mikstury, po ktorej nie bylo jeszcze wzrostu HP
        self._pot_expect = 0                      # HP "w drodze" z wypitych mikstur (leczenie porcjami)
        self._pot_gain = 0                        # suma wzrostow HP od ostatniej mikstury (pomiar leczenia)
        self._pot_heal_seen = None                # zmierzone leczenie 1 mikstury (gdy tabela sie myli)
        self._pot_next_ok = 0.0                   # najwczesniejsze kolejne picie (losowy odstep)
        self._pot_dead = False                    # do wykrycia wskrzeszenia
        self._pot_prev_hp = None                  # HP z poprzedniego kroku (wykrycie wzrostu)
        self._pot_times = []                      # czasy ostatnich mikstur (limit tempa)
        self._pot_nogain = 0                      # mikstury z rzedu bez wzrostu HP
        self._pot_pause_until = 0.0
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
        # auto-buff: odnawianie buffow-umiejetnosci (logika w recon/autobuff.py)
        self.autobuff = autobuff.AutoBuff(self.pm, self.proc_name)
        self._buff_ui_known = None     # zbior vnum, dla ktorego zbudowano checkboxy buffow
        # vnum mikstur -> picie wprost z EQ; pusta lista -> picie slotem paska.
        # Lista = zaznaczone rozmiary M/Ś/D (panel) + dodatkowe vnum z data/potion_vnums.txt.
        sizes = _load_ui_state().get("pot_sizes", POTION_SIZES_DEFAULT)
        self.pot_sizes = {s for s, _ in POTION_SIZES if s in sizes}
        self.pot_vnums_file = []
        self.pot_vnums = []
        self._pot_vnums_mtime = None
        self._reload_pot_vnums()
        # auto-loot (MVP): podnoszenie najblizszego dropu co loot_interval (tylko gdy DLL wykonuje
        # Python = GameCoreTop). Bez detekcji/filtra - prosta wersja na start.
        self.autoloot_on = False
        self.loot_interval = LOOT_INTERVAL
        self._last_loot = 0.0
        self.loot_count = 0
        self.loot_radius = LOOT_RADIUS
        self.loot_mode = "human"          # human = jak gracz, instant = naraz w promieniu, high = wszystko
        self.ground_cfg = autopot.GROUND_ITEM_CLIENTS.get(self.proc_name.lower())
        self.ground = []                  # ostatni odczyt dropow (vid, vnum, wx, wy, owner) - tez do rysowania
        self._looting = False             # faza zbierania: farma/chodzenie czekaja
        self._loot_next = 0.0             # najwczesniejsza kolejna akcja (reakcja / rytm klikania)
        self._loot_tries = {}             # vid -> klikniec w zasiegu bez skutku
        self._loot_black = {}             # vid -> do kiedy pomijany
        self._loot_fails = 0              # czarnych list z rzedu
        self._loot_pause_until = 0.0
        self._loot_extra = 0              # nadmiarowe klikniecia po zebraniu wszystkiego
        self._loot_prev = set()           # VID-y kandydatow z poprzedniego kroku (licznik podniesionych)
        self._tpick_ok = None             # DLL zna '!tpick' (None = jeszcze nie sprawdzono)
        self._tpick_t = 0.0
        # auto-chodzenie (funkcja 3): waypointy w wsp. swiata; petla idzie po nich (tylko vCore/hook).
        # Ctrl+LPM dodaje waypoint, [C] czysci, [G]/checkbox start-stop.
        self.autowalk_on = False
        self.walk_fight = False       # w drodze zatrzymaj sie i zbij metina/bossa w poblizu
        self.path = []                # [(wx, wy)] - waypointy uzytkownika (Ctrl+klik)
        self._goto_point = None       # (wx, wy, t_start) - jednorazowe "podejdz tutaj" z panelu (PPM na mapie)
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
        self._loop_err_t = {}               # tag petli -> czas ostatniego pelnego wpisu bledu (throttle)
        self._loop_err_n = {}               # tag petli -> licznik bledow
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
        self._mob_snap = (0.0, [], {}, {})  # (czas, lista mobow, vid->(wx,wy,is_metin), vid->obj) - 1 skan na krok
        self._engage_cool = {}         # vid -> czas, do kiedy pomijac porzucony (nieosiagalny) cel
        self._aggro = {}               # vid -> czas ostatniego dowodu, ze mob bije postac (_aggressors)
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
        self._tried_angles = []        # katy podejscia (wokol celu) juz probowane
        # masowy atak pakietem (bez animacji/ruchu) - patrz AREA_ATK_* i [[area-attack-packet]]
        self.area_atk_on = False
        self.area_atk_radius = AREA_ATK_RADIUS
        self.area_atk_mode = "safe"    # 'safe' = throttle + log zasiegu; 'aggro' = spam co petle
        self.area_atk_hop = False      # przeskok pozycji do dalekich mobow (jak !tpick) - gina bez podchodzenia
        self._area_hop_next = 0.0      # throttle skokow (ATTACK_HOP_EVERY)
        self._tattack_ok = None        # czy wstrzyknieta DLL zna '!tattack' (cache)
        self._tattack_t = 0.0
        self.area_atk_count = 0        # ile paczek ataku wyslano
        self.area_atk_gone = 0         # ile atakowanych mobow zniklo (przyblizone: smierc/odejscie)
        self._area_atk_last = 0.0      # kiedy ostatnio wyslano paczke (throttle)
        self._area_atk_prev = set()    # VID-y atakowane w poprzednim kroku (wykrycie znikniecia)
        self._area_atk_dist = {}       # vid -> ostatni dystans ataku (max skuteczny = najdalszy, ktory padl)
        self._area_atk_maxhit = 0.0    # najdalszy dystans BEZPOSREDNIEGO ataku, z ktorego mob znikl (=realny zasieg serwera)
        self._area_atk_hopset = set()  # VID-y atakowane ostatnio przez przeskok (by nie liczyc ich do maxhit)
        self._area_atk_logt = 0.0      # throttle czytelnej linii logu (co ~1 s; jsonl leci co paczke)
        self._area_atk_status = ""     # tekst stanu do panelu
        self._attack_ok = None         # czy wstrzyknieta DLL zna '!attack' (cache, jak _tpick_ok)
        self._attack_t = 0.0
        # peleryna na farmie: co jakis czas uzyj, gdy w poblizu dosc mobow (patrz CAPE_* i _cape_step)
        self.cape_on = False
        self.cape_radius = CAPE_RADIUS
        self.cape_period = float(_load_ui_state().get("cape_period", CAPE_PERIOD))
        self.cape_min_mobs = int(_load_ui_state().get("cape_min_mobs", CAPE_MIN_MOBS))
        self.cape_count = 0            # ile razy uzyto peleryny
        self._cape_last = 0.0          # kiedy ostatnio uzyto (throttle okresu)
        self._cape_status = ""         # tekst stanu do panelu
        self.cape_vnums = []           # vnum peleryn z data/cape_vnums.txt (priorytet = kolejnosc)
        self._cape_vnums_mtime = None
        self._reload_cape_vnums()
        # ochrona: wykrywanie graczy/GM w poblizu, wstrzymanie bota, dzwiek (recon/guard.py)
        self.gm_marks = []             # pozycje efektow logo GM (gm.mse) z rescan
        self.guard = guard_mod.Guard(self, _load_ui_state().get("guard"), lambda st: save_ui_value("guard", st))
        # przygotowanie do walki z bossem/specjalnym (zsiadz + buffy) i powrot na konia po walce
        self.prep_dismount = True      # opcja: zsiadaj z konia przed bossem/specjalnym
        self.prep_buffs = True         # opcja: Szal + Aura przed bossem/specjalnym
        self.prep_remount = True       # opcja: po takiej walce wsiadz znow na konia
        self._engaged_cls = ""         # klasa biezacego celu (boss/special/mob/metin)
        self._prep = None              # dict(vid, phase, t0, t, tries) - przygotowanie w toku
        self._prepared_vid = 0         # cel, dla ktorego przygotowanie juz zrobione
        self._we_dismounted = False    # zsiedlismy MY (przed walka) -> po walce wsiadamy
        self._remount = None           # dict(at, t, tries) - wsiadanie po walce w toku
        # walka oparta na dowodach (animacje) + zmiana strony podejscia
        self._metin_objs = {}          # vid -> obj CActorInstance metina (z rescan; animacja celu)
        self._melee_entry_t = 0.0      # kiedy OSTATNIO oddano atak klientowi (wejscie w walke wrecz)
        self._ev_t = 0.0               # ostatni dowod walki (wlasny atak / cel dostaje)
        self._ev_n = 0                 # ile dowodow w tej walce (dziennik)
        self._ev_kind = ""             # ostatni rodzaj dowodu
        self._side_tries = 0           # ile razy zmieniano strone podejscia do biezacego celu
        self._repos = None             # (wx, wy, do_kiedy) punkt podejscia z innej strony, gdy klient nie bije
        self._last_motions = (None, None)   # (wlasna, celu) - zmiany do dziennika
        # slad sesji (JSONL) - rekordy st/grid/plan/goto/ev
        self._trace = None
        self._tr_st_t = 0.0
        self._tr_grid_t = 0.0
        self._tr_path_sig = None

        self.root = root
        # bez okna (panel WWW steruje przez botapi): tk zostaje tylko petla zdarzen i zmiennymi opcji
        self.headless = headless
        if headless:
            root.withdraw()
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
        self.area_atk_var = tk.BooleanVar(value=self.area_atk_on)
        self.area_atk_r_var = tk.IntVar(value=int(self.area_atk_radius))
        self.area_atk_mode_var = tk.StringVar(value=self.area_atk_mode)
        self.area_atk_hop_var = tk.BooleanVar(value=self.area_atk_hop)
        self.cape_var = tk.BooleanVar(value=self.cape_on)
        self.cape_period_var = tk.IntVar(value=int(self.cape_period))
        self.cape_mobs_var = tk.IntVar(value=int(self.cape_min_mobs))
        self.guard_var = tk.BooleanVar(value=self.guard.on)
        self.guard_react_var = tk.StringVar(value=self.guard.react)
        self.guard_stop_var = tk.BooleanVar(value=self.guard.stop)
        self.guard_sound_var = tk.BooleanVar(value=self.guard.sound)
        self.guard_radius_var = tk.IntVar(value=int(self.guard.radius))
        self.guard_resume_var = tk.IntVar(value=int(self.guard.resume))

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

        # naglowek: portret klasy + "Nick - Klasa lv N" (_refresh_identity_ui)
        self._id_label = tk.Label(panel, text="(postac?)", compound="left", padx=6, bg="#0b0f0b",
                                  fg="#ffffff", font=("Consolas", 11, "bold"), anchor="w")
        self._id_label.grid(row=self._panel_row, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 2))
        self._panel_row += 1
        _chk("Auto-mikstura", self.autopot_var, self._on_autopot_check, "#9fe7a6", "#cdebcf")
        _mkscale("Pij <%", self.low_var, 5, 95, 1, self._on_low, "#ffe14d")
        _mkscale("Lecz do %", self.high_var, 5, 99, 1, self._on_high, "#4da6ff")
        _mkscale("Odstep s", self.cd_var, 0.1, 1.0, 0.05, self._on_cd, "#cdebcf")
        # wybor czerwonych mikstur (M / Ś / D) - pije najwieksza zaznaczona, ktora jest w EQ
        tk.Label(panel, text="Mikstury", bg="#0b0f0b", fg="#9fe7a6", font=("Consolas", 9)).grid(
            row=self._panel_row, column=0, sticky="w", padx=6)
        sizes_fr = tk.Frame(panel, bg="#0b0f0b")
        sizes_fr.grid(row=self._panel_row, column=1, sticky="w", padx=(2, 6))
        self._panel_row += 1
        self.pot_size_vars = {}
        for s, _ in reversed(POTION_SIZES):                  # w panelu kolejnosc M, Ś, D
            self.pot_size_vars[s] = tk.BooleanVar(value=s in self.pot_sizes)
            tk.Checkbutton(sizes_fr, text=s, variable=self.pot_size_vars[s], command=self._on_pot_sizes,
                           bg="#0b0f0b", fg="#ff7a7a", selectcolor="#1b2a1b", activebackground="#0b0f0b",
                           activeforeground="#ffb0b0", font=("Consolas", 9)).pack(side="left")
        _chk("Auto-loot", self.autoloot_var, self._on_autoloot_check, "#e9d5a0", "#f5e6bf")
        if self.ground_cfg:
            self.loot_radius_var = tk.IntVar(value=self.loot_radius)
            self.loot_mode_var = tk.StringVar(value=self.loot_mode)
            _mkscale("Zbieraj do", self.loot_radius_var, 300, 4000, 100, self._on_loot_radius, "#e9d5a0")
            # tryb zbierania: Gracz (podbiega, klika) / Naraz (pakiety dla wszystkich w promieniu) /
            # High (pakiety dla WSZYSTKICH widzianych dropow, bez limitu odleglosci i bez przerywania farmy)
            tk.Label(panel, text="Tryb lootu", bg="#0b0f0b", fg="#9fe7a6", font=("Consolas", 9)).grid(
                row=self._panel_row, column=0, sticky="w", padx=6)
            mode_fr = tk.Frame(panel, bg="#0b0f0b")
            mode_fr.grid(row=self._panel_row, column=1, sticky="w", padx=(2, 6))
            self._panel_row += 1
            for val, txt in (("human", "Gracz"), ("instant", "Naraz"), ("high", "High")):
                tk.Radiobutton(mode_fr, text=txt, value=val, variable=self.loot_mode_var,
                               command=self._on_loot_mode, bg="#0b0f0b", fg="#e9d5a0", selectcolor="#1b2a1b",
                               activebackground="#0b0f0b", activeforeground="#f5e6bf",
                               font=("Consolas", 9)).pack(side="left")
        else:
            _mkscale("Loot co s", self.loot_var, 0.2, 2.0, 0.1, self._on_loot_cd, "#e9d5a0")
        _chk("Auto-chodzenie (Ctrl+klik=punkt, C=czysc)", self.autowalk_var,
             self._on_autowalk_check, "#9fd0ff", "#cfe8ff")
        _chk("  W drodze bij metiny/bossy", self.walkfight_var, self._on_walkfight, "#9fd0ff", "#cfe8ff")
        _chk("Auto-farm (okrag wokol postaci)", self.farm_var, self._on_farm_check, "#ff6a6a", "#ffb0b0")
        _mkscale("Promien", self.farmr_var, 500, 6000, 100, self._on_farmr, "#ff6a6a")
        _chk("  Bij metiny (priorytet)", self.farm_metin_var, self._on_farm_metin, "#ffe14d", "#fff0a0")
        _chk("  Bij bossy (priorytet)", self.farm_boss_var, self._on_farm_boss, "#d94dff", "#ecb0ff")
        self.prep_dismount_var = tk.BooleanVar(value=self.prep_dismount)
        self.prep_buffs_var = tk.BooleanVar(value=self.prep_buffs)
        self.prep_remount_var = tk.BooleanVar(value=self.prep_remount)
        _chk("  Boss/specjalny: zsiadz z konia", self.prep_dismount_var, self._on_prep_opts, "#d94dff", "#ecb0ff")
        _chk("  Boss/specjalny: Aura + Szal przed walka", self.prep_buffs_var, self._on_prep_opts,
             "#d94dff", "#ecb0ff")
        _chk("  Po takiej walce wsiadz na konia", self.prep_remount_var, self._on_prep_opts, "#d94dff", "#ecb0ff")
        # masowy atak pakietem: bij wszystkie moby w promieniu bez animacji i bez ruchu postaci
        _chk("Masowy atak pakietem (bez animacji/ruchu)", self.area_atk_var,
             self._on_area_atk_check, "#ff5ea8", "#ffb0d4")
        _mkscale("Zasieg ataku", self.area_atk_r_var, 300, 8000, 100, self._on_area_atk_radius, "#ff5ea8")
        tk.Label(panel, text="Tryb ataku", bg="#0b0f0b", fg="#9fe7a6", font=("Consolas", 9)).grid(
            row=self._panel_row, column=0, sticky="w", padx=6)
        atk_mode_fr = tk.Frame(panel, bg="#0b0f0b")
        atk_mode_fr.grid(row=self._panel_row, column=1, sticky="w", padx=(2, 6))
        self._panel_row += 1
        for val, txt in (("safe", "Realistyczny+test"), ("aggro", "Agresywny")):
            tk.Radiobutton(atk_mode_fr, text=txt, value=val, variable=self.area_atk_mode_var,
                           command=self._on_area_atk_mode, bg="#0b0f0b", fg="#ff5ea8", selectcolor="#1b2a1b",
                           activebackground="#0b0f0b", activeforeground="#ffb0d4",
                           font=("Consolas", 9)).pack(side="left")
        _chk("  Przeskok do dalekich (gina bez podchodzenia)", self.area_atk_hop_var,
             self._on_area_atk_hop, "#ff5ea8", "#ffb0d4")
        # peleryna na farmie: co jakis czas uzyj, gdy w promieniu jest min. tyle potworow
        _chk("Peleryna co jakis czas (na farmie, przy mobach)", self.cape_var,
             self._on_cape_check, "#ffd24d", "#ffe9a6")
        _mkscale("Peleryna co s", self.cape_period_var, 10, 600, 5, self._on_cape_period, "#ffd24d")
        _mkscale("Peleryna min. mobow", self.cape_mobs_var, 1, 30, 1, self._on_cape_mobs, "#ffd24d")
        _chk("Speedhack (test - serwer moze ucinac)", self.speed_var,
             self._on_speed_check, "#ff9f1a", "#ffd79a")
        _mkscale("Ruch x", self.movx_var, 1.0, 3.0, 0.1, self._on_movx, "#ff9f1a")
        _mkscale("Atak x", self.atkx_var, 1.0, 3.0, 0.1, self._on_atkx, "#ff9f1a")
        _chk("Ochrona: wykrywaj graczy i GM", self.guard_var, self._on_guard_opts, "#ff6a6a", "#ffb0b0")
        _chk("  Przy wykryciu zatrzymaj bota", self.guard_stop_var, self._on_guard_opts, "#ff6a6a", "#ffb0b0")
        _chk("  Sygnal dzwiekowy", self.guard_sound_var, self._on_guard_opts, "#ff6a6a", "#ffb0b0")
        self.autobuff_var = tk.BooleanVar(value=False)
        _chk("Auto-buff (umiejetnosci na sobie) [U]", self.autobuff_var, self._on_autobuff_check,
             "#7fe0ff", "#c8f2ff")
        # checkboxy pojedynczych buffow: budowane w tick, gdy poznamy wyuczone skille (_refresh_buff_ui)
        self._buff_frame = tk.Frame(panel, bg="#0b0f0b")
        self._buff_frame.grid(row=self._panel_row, column=0, columnspan=2, sticky="w", padx=(18, 6))
        self._panel_row += 1
        self._buff_vars = {}

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
        self._update_identity()
        geo = load_ui_geometry(self.ident.get("nick"))   # okno tej postaci tam, gdzie je zamknieto
        if geo:
            try:
                root.geometry(geo)
            except Exception:
                pass
        self._refresh_identity_ui()
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
        # API dla panelu WWW (recon/botapi.py): stan + sterowanie bez okna, port w data/workers/<pid>.json
        self.api = botapi.start(self)
        self.tick()

    def _dbg(self, fmt, *args):
        """Linia do dziennika diagnostycznego (WALK_DEBUG): '<czas od startu> <tekst>'. Z obu watkow."""
        if not WALK_DEBUG:
            return
        tr = self._tr()
        if tr is not None:
            try:
                tr.text(fmt % args if args else fmt)
            except Exception:
                pass

    def _on_death_change(self, dead):
        """Zgon/wskrzeszenie postaci (HP z tablicy punktow): log z kontekstem walki + zwolnienie celu.
        Bot nic nie robi az do wskrzeszenia (restart w miescie/na miejscu robi gracz)."""
        hx, hy = self._hero_world()
        tgt = self._engaged_vid
        if dead:
            self._dbg("SMIERC postaci @(%s,%s): cel VID %s (duzy=%s), farm=%s walk=%s atak_pakietem=%s, "
                      "mikstur wypito %d, ostatnia %.1f s temu - wstrzymuje walke/chodzenie/zbieranie",
                      None if hx is None else round(hx), None if hy is None else round(hy), tgt,
                      self._engaged_big, self.farm_on, self.autowalk_on, self.area_atk_on, self.pot_count,
                      time.time() - self._last_pot if self._last_pot else -1)
            try:
                self._clear_autoattack()
                self._end_target()
            except Exception as e:
                self._dbg("SMIERC: blad zwalniania celu: %r", e)
            self._combat_status = "postac martwa - czekam na wskrzeszenie"
        else:
            self._dbg("WSKRZESZENIE @(%s,%s) HP %d/%d - wznawiam", None if hx is None else round(hx),
                      None if hy is None else round(hy), self._hp, self._mhp)
            self._combat_status = ""
        tr = self._tr()
        if tr is not None:
            tr.rec("death" if dead else "revive", hero=bottrace.ipt((hx, hy)) if hx is not None else None,
                   vid=tgt, hp=self._hp, mhp=self._mhp, pots=self.pot_count)

    def _loop_err(self, tag, e):
        """Wyjatek w kroku petli: konsola + walk.log (pelny traceback, max 1x/5 s na tag) + JSONL co raz.
        Wczesniej tylko print(e) - blad 'Trace.rec() got multiple values' nie byl widoczny w dzienniku."""
        import traceback
        tb = traceback.format_exc()
        now = time.time()
        last = self._loop_err_t.get(tag, 0.0)
        cnt = self._loop_err_n.get(tag, 0) + 1
        self._loop_err_n[tag] = cnt
        tr = self._tr()
        if tr is not None:
            try:
                tr.rec("loop_err", tag=tag, err=repr(e), n=cnt)
            except Exception:
                pass
        if now - last >= 5.0:
            self._loop_err_t[tag] = now
            print("[%s] blad petli (#%d): %r" % (tag, cnt, e))
            self._dbg("BLAD petli [%s] #%d: %r" + chr(10) + "%s", tag, cnt, e, tb.rstrip())

    def _tr(self):
        """Slad sesji (tworzony leniwie przy pierwszym zapisie) albo None gdy WALK_DEBUG wylaczony."""
        if not WALK_DEBUG:
            return None
        if self._trace is None:
            with self._dbg_lock:
                if self._trace is None:
                    # osobne pliki per postac (kilka radarow naraz): <stamp>_radar_<nick>_trace.jsonl
                    nick = self.ident.get("nick")
                    t = bottrace.Trace("radar_%s" % nick if nick else "radar_pid%d" % self.pm.process_id)
                    print("[log] dziennik: %s\n[log] slad:    %s" % (t.log_path, t.jsonl_path))
                    t.rec("cfg", exe=self.proc_name, off={k: int(v) for k, v in self.off.items()},
                          farm_off=self.farm_off, speed_off=self.speed_off,
                          coll=getattr(self.collision, "off", None),
                          const={k: v for k, v in globals().items()
                                 if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)})
                    self._trace = t
        return self._trace

    def _goto(self, wx, wy, why, hx=None, hy=None):
        """Wysyla !goto (ruch natywny) + wpis do dziennika: powod i odleglosc celu od postaci."""
        autopot.goto_native(self.pm.process_id, wx, wy)
        self._dbg_last_goto = (time.time(), wx, wy, why)
        tr = self._tr()
        if tr is not None:
            tr.rec("goto", why=why, to=bottrace.ipt((wx, wy)), hero=bottrace.ipt((hx, hy)) if hx is not None else None)
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
        if now - self._tr_st_t >= TRACE_ST_EVERY:
            self._tr_st_t = now
            try:
                self._trace_state(now)
            except Exception as e:
                self._dbg("blad sladu st: %r", e)
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

    def _trace_state(self, now):
        """Rekord 'st' (+ 'grid' co TRACE_GRID_EVERY, 'path' przy zmianie waypointow) - pelny stan do odtworzenia."""
        tr = self._tr()
        hx, hy = self._hero_world()
        if tr is None or hx is None:
            return
        ipt = bottrace.ipt
        path = list(self.path)
        sig = tuple(ipt(p) and tuple(ipt(p)) for p in path)
        if sig != self._tr_path_sig:
            self._tr_path_sig = sig
            tr.rec("path", wps=bottrace.ipts(path))
        if now - self._tr_grid_t >= TRACE_GRID_EVERY and self.collision.ok and self.mapout_obj:
            self._tr_grid_t = now
            cx, cy = int(hx) // 100, int(hy) // 100
            R = TRACE_GRID_R
            g = self.collision.block_grid(cx - R, cy - R, cx + R, cy + R)
            tr.rec("grid", **bottrace.pack_grid(g, cx - R, cy - R))
        atk = None
        if self.farm_off and self.player_obj is not None:
            try:
                atk = self.pm.read_uint(self.player_obj + self.farm_off["autoatk"])
            except Exception:
                atk = None
        actors = []
        for mx, my, vnum, is_pc, o, vid in self._iter_actors(hx, hy):
            if math.hypot(mx - hx, my - hy) <= TRACE_MOB_R:
                actors.append([vid, vnum, "pc" if is_pc else self._classify(vnum, False), int(mx), int(my),
                               self._motion(o)])
        for vnum, nm, mx, my, vid in self.metin_actors:
            if mx is not None and math.hypot(mx - hx, my - hy) <= TRACE_MOB_R:
                actors.append([vid, vnum, "metin", int(mx), int(my), self._motion(self._metin_objs.get(vid))])
        eng = None
        if self._engaged_vid:
            pos = self._mob_snap[2].get(self._engaged_vid)
            idle, lim = self._no_evidence_for(now) if self._in_melee else (None, None)
            eng = dict(vid=self._engaged_vid, big=self._engaged_big, melee=self._in_melee,
                       pos=ipt(pos), d=int(math.hypot(pos[0] - hx, pos[1] - hy)) if pos else None,
                       mot=self._motion(self._mob_snap[3].get(self._engaged_vid)),
                       age=round(now - self._engaged_t0, 2) if self._engaged_t0 else None,
                       melee_age=round(now - self._melee_since, 2) if self._melee_since else None,
                       entry_age=round(now - self._melee_entry_t, 2) if self._melee_entry_t else None,
                       ev_age=round(now - self._ev_t, 2) if self._ev_t else None, ev_n=self._ev_n,
                       ev_kind=self._ev_kind, idle=round(idle, 2) if idle is not None else None,
                       best=int(self._engaged_best) if self._engaged_best < 1e17 else None,
                       sides=self._side_tries, repos=ipt(self._repos) if self._repos else None)
        lg = self._dbg_last_goto
        tr.rec("st", map=self.map.name, hero=ipt((hx, hy)), hp=self._hp, mhp=self._mhp,
               mot=self._motion(self.self_actor), atk=atk, bot=self._bot_vid,
               mode=dict(walk=self.autowalk_on, farm=self.farm_on, wfight=self.walk_fight, loot=self.autoloot_on),
               mounted=(self.mounted[1] if self.mounted and self.mounted[0] else None),
               wp=self.walk_idx, route=bottrace.ipts(self._route), ri=self._route_idx,
               eroute=bottrace.ipts(self._eng_route), eng=eng,
               goto=[round(now - lg[0], 2), ipt((lg[1], lg[2])), lg[3]] if lg else None,
               learned=[[int(x), int(y), round(e - now, 1)] for x, y, e in self._learned if e > now],
               nudge=self._nudge_n, blocker=self._blocker_vid or None,
               farm=dict(c=ipt(self.farm_center), r=int(self.farm_radius)) if self.farm_on else None,
               status=self._combat_status, cool=sorted(v for v, t in self._engage_cool.items()
                                                       if t > now and isinstance(v, int)),
               actors=actors)

    def _trace_plan(self, kind, hx, hy, tx, ty, obst, hard, blobs, route, ms):
        """Rekord 'plan': wejscie A* + wynik + siatka bboxu (do ponownego policzenia offline w replay.py)."""
        tr = self._tr()
        if tr is None:
            return
        grid = None
        if self.collision.ok:
            M = 32
            sx, sy, gx, gy = int(hx) // 100, int(hy) // 100, int(tx) // 100, int(ty) // 100
            x0, x1 = max(min(sx, gx) - M, max(sx, gx) - 220), max(sx, gx) + M
            y0, y1 = max(min(sy, gy) - M, max(sy, gy) - 220), max(sy, gy) + M
            x1, y1 = min(x1, x0 + 260), min(y1, y0 + 260)
            try:
                grid = bottrace.pack_grid(self.collision.block_grid(x0, y0, x1, y1), x0, y0)
            except Exception:
                grid = None
        tr.rec("plan", kind=kind, start=bottrace.ipt((hx, hy)), goal=bottrace.ipt((tx, ty)),
               obst=bottrace.ipts(obst), hard=bottrace.ipts(hard), blobs=bottrace.ipts(blobs),
               route=bottrace.ipts(route) if route else None, ms=round(ms, 1), grid=grid)

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
                self._reload_cape_vnums()
                self._update_identity()
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
            self.pot_vnums_file = load_potion_vnums()
            self._rebuild_pot_vnums()

    def _reload_cape_vnums(self):
        """Przeladowuje vnum peleryn z data/cape_vnums.txt, gdy plik sie zmienil (edycja w locie)."""
        try:
            mtime = os.path.getmtime(CAPE_VNUMS_PATH)
        except OSError:
            mtime = None
        if mtime != self._cape_vnums_mtime:
            self._cape_vnums_mtime = mtime
            self.cape_vnums = load_potion_vnums(CAPE_VNUMS_PATH)   # ten sam format (vnum na linie)
            print("[peleryna] vnum do uzycia: %s"
                  % (self.cape_vnums or "(brak - wpisz w data/cape_vnums.txt)"))

    def _rebuild_pot_vnums(self):
        """Lista vnum do picia: zaznaczone rozmiary (od najwiekszej) + dodatkowe z pliku."""
        vn = [v for s, vs in POTION_SIZES if s in self.pot_sizes for v in vs]
        vn += [v for v in self.pot_vnums_file if v not in vn]
        self.pot_vnums = vn
        print("[mikstura] vnum do picia: %s" % (self.pot_vnums or "(brak - slot paska)"))

    def _on_pot_sizes(self):
        self.pot_sizes = {s for s, _ in POTION_SIZES if self.pot_size_vars[s].get()}
        save_ui_value("pot_sizes", [s for s, _ in POTION_SIZES if s in self.pot_sizes])
        self._rebuild_pot_vnums()

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

    def _on_loot_radius(self, val):
        self.loot_radius = int(float(val))

    def _on_loot_mode(self):
        self.loot_mode = self.loot_mode_var.get()
        self._loot_black.clear()            # nowy tryb = nowa proba (np. High po odmowach w trybie Naraz)
        self._loot_tries.clear()

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

    def _on_area_atk_check(self):
        self.area_atk_on = bool(self.area_atk_var.get())
        if self.area_atk_on:                        # nowa sesja ataku = zeruj liczniki i test zasiegu
            self.area_atk_count = self.area_atk_gone = 0
            self._area_atk_maxhit = 0.0
            self._area_atk_prev = set()
            self._area_atk_dist.clear()
        else:
            self._area_atk_status = ""

    def _on_area_atk_radius(self, val):
        self.area_atk_radius = float(val)

    def _on_area_atk_mode(self):
        self.area_atk_mode = self.area_atk_mode_var.get()

    def _on_area_atk_hop(self):
        self.area_atk_hop = bool(self.area_atk_hop_var.get())

    def _on_cape_check(self):
        self.cape_on = bool(self.cape_var.get())
        if self.cape_on:                            # nowa sesja = pierwsze uzycie od razu, gdy sa moby
            self.cape_count = 0
            self._cape_last = 0.0
        else:
            self._cape_status = ""

    def _on_cape_period(self, val):
        self.cape_period = float(val)
        save_ui_value("cape_period", self.cape_period)

    def _on_cape_mobs(self, val):
        self.cape_min_mobs = int(float(val))
        save_ui_value("cape_min_mobs", self.cape_min_mobs)

    def _on_guard_opts(self, _val=None):
        """Wszystkie opcje ochrony naraz (checkboxy i suwaki czytaja swoje zmienne tk)."""
        self.guard.apply(on=self.guard_var.get(), react=self.guard_react_var.get(), stop=self.guard_stop_var.get(),
                         sound=self.guard_sound_var.get(), radius=self.guard_radius_var.get(),
                         resume=self.guard_resume_var.get())

    def _on_speed_check(self):
        self.speed_on = bool(self.speed_var.get())

    def _on_autobuff_check(self):
        self.autobuff.on = bool(self.autobuff_var.get())

    def _on_buff_toggle(self, vnum):
        if self._buff_vars[vnum].get():
            self.autobuff.enabled.add(vnum)
        else:
            self.autobuff.enabled.discard(vnum)

    def _refresh_buff_ui(self):
        """GUI: po jednym checkboxie na wyuczony buff (lista zalezy od klasy - znana dopiero z pamieci)."""
        known = set(self.autobuff.known)
        if known == self._buff_ui_known:
            return
        self._buff_ui_known = known
        for w in self._buff_frame.winfo_children():
            w.destroy()
        self._buff_vars = {}
        for vnum in sorted(known, key=lambda v: self.autobuff.known[v]):
            var = tk.BooleanVar(value=vnum in self.autobuff.enabled)
            self._buff_vars[vnum] = var
            tk.Checkbutton(self._buff_frame, text=autobuff.BUFFS[vnum][0], variable=var,
                           command=lambda v=vnum: self._on_buff_toggle(v), bg="#0b0f0b", fg="#7fe0ff",
                           selectcolor="#1b2a1b", activebackground="#0b0f0b", activeforeground="#c8f2ff",
                           font=("Consolas", 9)).pack(side="left")

    def _autobuff_step(self):
        """Krok auto-buffa (petla _autopot_loop). Droga wykonania: GameCoreTop - Python, vCore - '!skill'
        z hooka (tylko nowa DLL - stara zostaje w grze do jej restartu)."""
        if time.time() < self.autobuff._next:        # throttle (STEP_EVERY) zanim cokolwiek czytamy
            return
        pid = self.pm.process_id
        use = autobuff.make_use_fn(pid, self.dll_exec,
                                   self.native_drink and autopot.hook_has_skill(pid))
        st = self._mount_check()
        alive = not (self._pts_valid and self._hp <= 0)
        before = (self.autobuff.status, self.autobuff.count)
        self.autobuff.step(self.player_obj, self.self_actor, st[0] if st else None, alive, use)
        if (self.autobuff.status, self.autobuff.count) != before:
            self._dbg("buff: %s uzyto:%d | %s", self.autobuff.status, self.autobuff.count,
                      self.autobuff.summary())

    def _on_movx(self, val):
        self.move_mult = float(val)

    def _on_atkx(self, val):
        self.atk_mult = float(val)

    def _on_prep_opts(self):
        self.prep_dismount = bool(self.prep_dismount_var.get())
        self.prep_buffs = bool(self.prep_buffs_var.get())
        self.prep_remount = bool(self.prep_remount_var.get())

    def _on_walkfight(self):
        self.walk_fight = bool(self.walkfight_var.get())
        self._reset_req = True

    def _on_autowalk_check(self):
        self.autowalk_on = bool(self.autowalk_var.get())
        self._reset_req = True
        if self.autowalk_on:
            # wznow DALEJ po trasie (najblizszy odcinek petli), nie od punktu 1
            hx, hy = self._hero_world()
            self.walk_idx = resume_index(self.path, hx, hy) if (self.path and hx is not None) else 0
            if self.path:
                print("[walk] start od waypointu %d/%d" % (self.walk_idx + 1, len(self.path)))
            self._walk_prog = None
            self._route = []
            self._last_plan = 0.0

    def _on_close(self):
        """Zapisz pozycje i rozmiar okna (by otworzyc sie tam samo), potem zamknij."""
        try:
            save_ui_geometry(self.root.geometry(), self.ident.get("nick"))
        except Exception:
            pass
        self.root.destroy()

    def _point(self, i):
        """Odszyfrowany punkt postaci POINT_i (1=poziom, 5/6=HP, 7/8=PM) albo None."""
        if self.pt_ptr is not None:
            return autopot.read_point_ptr(self.pm, self.base, self.pt_ptr, i)
        if self.points_addr is None:
            return None
        try:
            return autopot.read_points(self.pm, self.points_addr, i + 1)[i]
        except Exception:
            return None

    def _update_identity(self):
        """Nick (CPythonPlayer), klasa+plec (model wlasnego aktora, zapas: baza), poziom. Watek skanu."""
        nick, off = identity.read_nick(self.pm, self.player_obj, self.proc_name, self._nick_off)
        if off is not None and off != self._nick_off:
            print("[id] nick @ CPythonPlayer+0x%X: %s" % (off, nick))
            self._nick_off = off
        cls, sex = identity.class_from_model(self._actor_model(self.self_actor)) if self.self_actor \
            else (None, None)
        if not cls and nick:
            if nick not in self._db_cls:
                self._db_cls[nick] = identity.class_from_db(nick)
            cls, sex = self._db_cls[nick]
        lvl = self._point(1)
        self.ident = dict(pid=self.pm.process_id, exe=self.proc_name, nick=nick, cls=cls, sex=sex,
                          level=lvl if lvl and 0 < lvl < 1000 else None)

    def _refresh_identity_ui(self):
        """Naglowek panelu + tytul okna, gdy tozsamosc sie zmienila (tylko watek GUI)."""
        idn = self.ident
        key = (idn.get("nick"), idn.get("cls"), idn.get("sex"), idn.get("level"))
        if key == self._ident_shown:
            return
        self._ident_shown = key
        txt = identity.label(idn) if idn.get("nick") else "(postac nie w swiecie)"
        fp = identity.face_path(idn.get("cls"), idn.get("sex"))
        try:
            self._face_img = ImageTk.PhotoImage(Image.open(fp)) if fp else None
        except Exception:
            self._face_img = None
        self._id_label.configure(text=txt, image=self._face_img or "")
        self.root.title("%s - Radar (%s, PID %d)" % (txt, self.proc_name, self.pm.process_id))

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
            save_route(self.map.name, self.path)
            print("[walk] waypoint %d: (%.0f, %.0f) - zapisano trase mapy %s" % (len(self.path), wx, wy,
                                                                              self.map.name))
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
        gm_marks = []
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
                if fn and fn.lower().replace("\\", "/").endswith(guard_mod.GM_MARK_SUFFIX):
                    try:                        # logo GM nad glowa (AFFECT_YMIR) - do ochrony
                        gx = self.pm.read_float(o + eff_x)
                        gy = -self.pm.read_float(o + eff_x + 4)
                    except Exception:
                        continue
                    if math.isfinite(gx) and WORLD_MIN < gx < WORLD_MAX:
                        gm_marks.append((gx, gy))
                elif fn and "metinstone" in fn.lower():
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
        metin_actors, metin_objs = [], {}
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
                if vid:
                    metin_objs[vid] = o
        self.metin_actors = metin_actors
        self._metin_objs = metin_objs
        self.effs = effs
        self.metins = metins
        self.gm_marks = gm_marks

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
        old = getattr(self, "map", None)
        self.map = m
        if old is None or old.name != m.name:          # start / teleport: trasa zapisana dla TEJ mapy
            self.path = load_route(m.name)
            hx, hy = self._hero_world()
            self.walk_idx = resume_index(self.path, hx, hy) if (self.path and hx is not None) else 0
            self._route = []
            self._last_plan = 0.0
            print("[walk] mapa %s: trasa %d punktow%s" % (m.name, len(self.path),
                                                         " (z data/routes)" if self.path else ""))

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
        elif k == "u":
            self.autobuff_var.set(not self.autobuff.on)
            self._on_autobuff_check()
        elif k == "g":
            self.autowalk_var.set(not self.autowalk_on)   # start/stop auto-chodzenia (jak checkbox)
            self._on_autowalk_check()
        elif k == "c":
            self.path = []                              # wyczysc waypointy
            self.walk_idx = 0
            self._walk_prog = None
            self._route = []
            save_route(self.map.name, self.path)        # wyczyszczona trasa tez zapamietana dla mapy
            print("[walk] trasa wyczyszczona (mapa %s)" % self.map.name)
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
        if self.headless:                 # nic nie rysujemy; stan konia dla dziennika jak w _draw_mount
            self.mounted = self._mount_check()
            self.root.after(500, self.tick)
            return
        self._refresh_identity_ui()
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
        self._draw_ground(c, hx, hy)                      # przedmioty na ziemi
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
        self._refresh_buff_ui()
        self.root.after(120, self.tick)

    # ---------- auto-mikstura: szybka petla (decyzja) + rysowanie (z cache) ----------
    def _autopot_loop(self):
        """Dedykowana petla: czyta HP i pije co POTION_POLL (szybka reakcja, niezaleznie od rysowania,
        ktore chodzi co 120 ms). Dzieki temu lek leci zaraz po spadku HP, a nie raz na klatke/1.5 s."""
        last_status, last_area, last_area_full, last_area_t, t_end = "", None, None, 0.0, time.time()
        was_dead = False
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
                self._loop_err("mikstura", e)
            try:
                self.guard.step()                   # gracze/GM w poblizu -> hold (czytaja go kroki ponizej)
            except Exception as e:
                self._loop_err("ochrona", e)
            hold = self.guard.hold
            dead = (self._hp <= 0) if self._pts_valid else was_dead   # niewazny odczyt: stan bez zmian
            if dead != was_dead:
                was_dead = dead
                self._on_death_change(dead)
            if dead:                                # martwy: zadnych rozkazow ruchu/ataku/zbierania
                continue
            try:
                if not (hold and self.guard.paused("loot")):
                    self._autoloot_step()
            except Exception as e:
                self._loop_err("loot", e)
            try:
                self._autowalk_step()
            except Exception as e:
                self._loop_err("walk", e)
            try:
                if not (hold and self.guard.paused("speedhack")):
                    self._speed_step()
            except Exception as e:
                self._loop_err("speed", e)
            try:
                self._farm_step()
            except Exception as e:
                self._loop_err("farm", e)
            try:
                if hold and self.area_atk_on and self.guard.paused("atak pakietem"):
                    self._area_atk_status = "STOP: ochrona (%s)" % self.guard.hold_why
                else:
                    self._area_attack_step()
            except Exception as e:
                self._loop_err("atak", e)
            area_key = (self._area_atk_status or "").split(" ", 1)[0]
            if self.area_atk_on and (area_key != last_area or time.time() - last_area_t >= 2.0) \
                    and self._area_atk_status != last_area_full:
                last_area, last_area_t = area_key, time.time()   # powod bezczynnosci/stan ataku pakietem
                last_area_full = self._area_atk_status
                self._dbg("atak pakietem: %s (attack_ok=%s tattack_ok=%s exec=%s)", last_area_full or "-",
                          self._attack_ok, self._tattack_ok, self.dll_exec)
                tr = self._tr()
                if tr is not None:
                    tr.rec("area_status", s=last_area_full, attack_ok=self._attack_ok,
                           tattack_ok=self._tattack_ok, exec=self.dll_exec)
            try:
                if not (hold and self.guard.paused("peleryna")):
                    self._cape_step()
            except Exception as e:
                self._loop_err("peleryna", e)
            try:
                if not (hold and self.guard.paused("buff")):
                    self._autobuff_step()
            except Exception as e:
                self._loop_err("buff", e)
            try:
                if (self.farm_on or self.autowalk_on) and not (hold and self.guard.paused("wsiadanie na konia")):
                    self._remount_step()
            except Exception as e:
                self._loop_err("kon", e)
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
        lst, v2p, v2o = [], {}, dict(self._metin_objs)
        for mx, my, vnum, is_pc, o, vid in self._iter_actors(hx, hy):
            lst.append((mx, my, vnum, is_pc, vid))
            if vid and not is_pc:
                v2p[vid] = (mx, my, False)
                v2o[vid] = o
        for mx, my, mvid in self._metin_targets():
            if mvid:
                v2p[mvid] = (mx, my, True)
        self._mob_snap = (now, lst, v2p, v2o)
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

    def _aggressors(self, hx, hy):
        """{vid: (wx, wy, d)} mobow, ktore bija postac: w AGGRO_RANGE i (animacja ataku albo d <= AGGRO_CLOSE);
        pamiec AGGRO_MEMORY s (animacja ataku jest przerywana). Gracze/NPC/metiny/porzucone pomijane."""
        now = time.time()
        mobs, _ = self._mobs_snapshot(hx, hy)
        v2o = self._mob_snap[3]
        out = {}
        for mx, my, vnum, is_pc, vid in mobs:
            if is_pc or not vid or self._vid_blocked(vid):
                continue
            d = math.hypot(mx - hx, my - hy)
            if d > AGGRO_RANGE * 1.3:
                continue
            if 8000 <= vnum < 9000 or self._classify(vnum, is_pc) == "npc":
                continue
            mot = self._motion(v2o.get(vid))
            if mot in MOT_DEAD:
                self._aggro.pop(vid, None)
                continue
            if d <= AGGRO_RANGE and (d <= AGGRO_CLOSE or self._is_attack_motion(mot)):
                if vid not in self._aggro:
                    self._dbg("napastnik VID %d d=%.0f (anim %s)", vid, d, mot)
                self._aggro[vid] = now
            if now - self._aggro.get(vid, 0.0) <= AGGRO_MEMORY:
                out[vid] = (mx, my, d)
        for vid in [v for v, t in self._aggro.items() if now - t > AGGRO_MEMORY]:
            del self._aggro[vid]
        return out

    def _aggressor_target(self, hx, hy):
        """Najblizszy napastnik jako cel (vid, tx, ty, False, False) albo None."""
        ag = self._aggressors(hx, hy)
        if not ag:
            return None
        vid, (tx, ty, _) = min(ag.items(), key=lambda kv: kv[1][2])
        return (vid, tx, ty, False, False)

    def _farm_pick_target(self, hx, hy):
        """Najlepszy cel farmy wg priorytetu napastnik > metin > metin-z-efektu > boss > special > mob.
        Metiny/specjalne/moby tylko w okregu; bossy tez POZA okregiem (do FARM_BOSS_RANGE od postaci)."""
        bt = self._blocker_target(hx, hy)       # mob, ktory nas zablokowal - najpierw on
        if bt is not None:
            return bt
        at = self._aggressor_target(hx, hy)     # moby bijace postac - zanim pobiegniemy gdziekolwiek dalej
        if at is not None:
            return at
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
        if self._looting:                       # zbieramy drop - nowy cel dopiero po zebraniu
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
        self._defend_switch(hx, hy)
        self._combat_tick(hx, hy, self._farm_pick_target, self._farm_leash)

    def _defend_switch(self, hx, hy):
        """Zamek na cel NIE moze ciagnac postaci do dalekiego moba, gdy inne ja bija: jesli biezacy cel nie jest
        napastnikiem i nie stoimy z nim w walce wrecz (albo bijemy zwyklego moba dalej niz najblizszy napastnik),
        zwolnij zamek BEZ czarnej listy - nastepny wybor (_farm_pick_target) wezmie najblizszego napastnika.
        Metina/bossa w walce wrecz nie przerywamy."""
        vid = self._engaged_vid
        if not vid:
            return
        ag = self._aggressors(hx, hy)
        if not ag or vid in ag:
            return
        if self._in_melee and self._engaged_big:
            return
        pos = self._find_by_vid(vid, hx, hy)
        near = min(a[2] for a in ag.values())
        if self._in_melee and pos is not None and math.hypot(pos[0] - hx, pos[1] - hy) <= near + 50:
            return                              # cel juz przy nas i nie dalej niz napastnik - dobij go
        self._dbg("zmiana celu: VID %d -> napastnik (najblizszy d=%.0f, napastnikow %d)", vid, near, len(ag))
        self._combat_status = "obrona: zmiana celu na napastnika"
        self._clear_autoattack()
        self._end_target()

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
            if pos is not None and self._in_melee:
                if self._melee_evidence(vid, now) == "dead":
                    self._dbg("cel VID %d: animacja smierci -> zabity (walka wrecz %.1fs, dowodow %d)", vid,
                              now - self._melee_since if self._melee_since else 0.0, self._ev_n)
                    self._engage_cool[vid] = now + 10.0
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
                # limit walki liczony od OSTATNIEGO DOWODU trafienia (boss bity od 3 min to nie "walka bez skutku")
                fight_ref = max(self._melee_since, self._ev_t)
                # bez wlasnego aktora (self_actor) nie widac animacji ataku - wtedy nie oceniamy po dowodach
                idle, idle_lim = (self._no_evidence_for(now) if self._in_melee and self.self_actor
                                  else (0.0, 1e9))
                why = None
                if not self._in_melee and not self._repos and now - self._engaged_since > WALK_ENGAGE_TIMEOUT:
                    why = "brak postepu %.0fs" % WALK_ENGAGE_TIMEOUT
                elif self._melee_since and now - fight_ref > fight_lim:
                    why = "walka: %.0fs bez dowodu trafienia" % fight_lim
                elif (self._in_melee and d > (MELEE_CONTACT_BIG if self._engaged_big else MELEE_CONTACT)
                      and now - self._engaged_since > MELEE_STALL_SEC and now - self._ev_t > MELEE_GAP_SEC):
                    # atak oddany klientowi, ale postac nie dochodzi do celu (cos ja blokuje po drodze)
                    why = "klient nie dochodzi (d=%.0f bez zmian %.0fs)" % (d, MELEE_STALL_SEC)
                elif leash_fn is not None and leash_fn(tx, ty, self._engaged_big):
                    why = "uciekl za daleko (d=%.0f)" % d
                elif (self._engaged_big and self.autowalk_on and not self.farm_on and self._engaged_t0
                      and now - self._engaged_t0 > WALK_BIG_FIGHT_MAX):
                    why = "chodzenie: %.0fs na jednym duzym celu - wracam na trase" % (now - self._engaged_t0)
                elif self._in_melee and idle > idle_lim:
                    # klient ma VID, ale ani nie atakuje, ani cel nie dostaje -> nie moze uderzyc z tego miejsca
                    if self._side_tries >= MELEE_SIDE_TRIES:
                        why = "klient nie atakuje (brak dowodu %.1fs, probowano %d stron)" % (idle, self._side_tries)
                    elif not self._start_reposition(hx, hy, tx, ty, vid, idle):
                        why = "klient nie atakuje, brak innego podejscia"
                if why is None:
                    if self._prep_step(hx, hy, vid, tx, ty):   # boss/specjalny: zsiadz + buffy, potem walka
                        return True
                    self._engage(hx, hy, vid, tx, ty, ism)
                    return True
                self._abandon(vid, why)
        if self.guard.hold:
            # ochrona: nowych celow nie bierzemy - tylko moby, ktore nas blokuja albo bija (dobicie walki)
            tgt = self._blocker_target(hx, hy) or self._aggressor_target(hx, hy)
        else:
            tgt = pick_fn(hx, hy)
        if tgt is None:
            self._appr = None
            self._clear_autoattack()
            self._eng_route = []
            self._combat_target = None
            self._combat_status = ("STOP: ochrona (%s)" % self.guard.hold_why) if self.guard.hold else "brak celu"
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
        self._engaged_cls = self._vid_class(vid, ism)
        self._dbg("nowy cel VID %d d=%.0f %s%s", vid, d, self._engaged_cls or ("metin" if ism else "mob"),
                  " (duzy)" if big else "")
        self._engaged_since = now
        self._engaged_best = d
        self._engaged_big = big
        self._engage(hx, hy, vid, tx, ty, ism)
        return True

    def _motion(self, o):
        """Typ biezacej animacji aktora (EType z RaceMotionData.h: 1 wait, 2 move, 3/4/8 atak, 5/6/9 trafiony,
        7 smierc) albo None."""
        if not o:
            return None
        try:
            p = self.pm.read_uint(o + self.off["race"] + MOTION_PTR_FROM_RACE)
            if not 0x10000 < p < 0x7FFF0000:
                return None
            n = self.pm.read_uint(p + MOTION_TYPE_OFF)
        except Exception:
            return None
        return n if n < 16 else None

    @staticmethod
    def _is_attack_motion(n):
        return n in MOT_ATTACK

    def _melee_evidence(self, vid, now):
        """Dowody, ze walka wrecz NAPRAWDE trwa: wlasna postac gra animacje ataku/skilla albo cel gra 'obrazenia'.
        Zwraca 'dead' (cel gra smierc), 'atak' / 'cel dostaje' (dowod) albo None."""
        own = self._motion(self.self_actor)
        tm = self._motion(self._mob_snap[3].get(vid))
        if (own, tm) != self._last_motions:
            self._dbg("animacje: postac %s  cel(VID %d) %s", own, vid, tm)
            self._last_motions = (own, tm)
        if tm in MOT_DEAD:
            return "dead"
        ev = "atak" if self._is_attack_motion(own) else ("cel dostaje" if tm in MOT_DAMAGE else None)
        if ev:
            if self._ev_t < self._melee_entry_t:
                self._dbg("dowod walki VID %d: %s po %.1fs od oddania ataku", vid, ev, now - self._melee_entry_t)
            self._ev_t = now
            self._ev_n += 1
            self._ev_kind = ev
        return ev

    def _no_evidence_for(self, now):
        """(ile s bez dowodu, limit) od oddania ataku klientowi."""
        if self._ev_t < self._melee_entry_t:
            return now - self._melee_entry_t, MELEE_FIRST_HIT_SEC
        return now - self._ev_t, MELEE_GAP_SEC

    def _start_reposition(self, hx, hy, tx, ty, vid, idle):
        """Klient ma VID, a nie bije (cos stoi miedzy postacia a celem - obiekt mapy spoza siatki kolizji).
        Wybierz punkt podejscia po INNEJ stronie celu: kat jak najdalszy od dotychczas probowanych, wolny
        w siatce i z wolna linia do celu. Do tego punktu dojdziemy A*, potem znow oddamy atak."""
        now = time.time()
        self._side_tries += 1
        d = math.hypot(hx - tx, hy - ty)
        cur = math.atan2(hy - ty, hx - tx)
        if not self._tried_angles:
            self._tried_angles = [cur]
        r = max(200.0, min(d, 300.0))
        best = None
        for k in range(16):
            a = cur + k * math.pi / 8
            px, py = tx + math.cos(a) * r, ty + math.sin(a) * r
            if self.collision.ok and (self.collision.is_block(px, py) or not self.collision.line_clear(px, py, hx, hy)
                                      and not self.collision.find_path(hx, hy, px, py)):
                continue
            sep = min(abs(math.atan2(math.sin(a - t), math.cos(a - t))) for t in self._tried_angles)
            if best is None or sep > best[0]:
                best = (sep, a, px, py)
        # miejsce, w ktorym klient nie mogl uderzyc, zapamietaj jako przeszkode (polowa drogi do celu)
        self._learned = [l for l in self._learned if l[2] > now][-11:]
        self._learned.append((hx + (tx - hx) * 0.4, hy + (ty - hy) * 0.4, now + LEARN_TTL))
        self._in_melee = False
        self._clear_autoattack()
        self._eng_route_t = 0.0
        if best is None:
            self._dbg("zmiana strony VID %d: brak wolnego punktu wokol celu", vid)
            self._repos = None
            return False
        _, a, px, py = best
        self._tried_angles.append(a)
        self._repos = (px, py, now + REPOSITION_TIMEOUT)
        self._dbg("zmiana strony VID %d (proba %d/%d): brak dowodu walki %.1fs (d=%.0f, animacja %s) -> "
                  "punkt (%.0f,%.0f) kat %.0f st. (bylo %.0f)", vid, self._side_tries, MELEE_SIDE_TRIES, idle, d,
                  self._motion(self.self_actor), px, py, math.degrees(a), math.degrees(cur))
        return True

    def _vid_class(self, vid, is_metin=False):
        """Klasa celu po VID (boss/king/special/mob/npc/metin) - z biezacego snapshotu."""
        if is_metin or vid in self._metin_objs:
            return "metin"
        for mx, my, vnum, is_pc, v in self._mob_snap[1]:
            if v == vid:
                return self._classify(vnum, is_pc)
        return ""

    def _send_chat(self, text):
        """Komenda czatu w grze ('/unmount', '/ride'). True wyslano, None = brak drogi (stara DLL / brak hooka)."""
        pid = self.pm.process_id
        if self.dll_exec:
            ok = autopot.chat_py(pid, text)
        elif self.native_drink and autopot.hook_has_chat(pid):
            ok = autopot.chat_native(pid, text)
        else:
            return None
        self._dbg("czat: %s (%s)", text, "wyslano" if ok else "BLAD potoku")
        return bool(ok)

    def _prep_step(self, hx, hy, vid, tx, ty):
        """Przed walka z bossem/specjalnym: zsiadz z konia, rzuc Szal i Aure, dopiero potem walka.
        Wolane co tick walki dla zamknietego celu. True = przygotowanie trwa (nie oddawaj jeszcze ataku)."""
        if not (self.prep_dismount or self.prep_buffs) or vid == self._prepared_vid:
            return False
        if self._engaged_cls not in PREP_CLASSES:
            return False
        d = math.hypot(tx - hx, ty - hy)
        p = self._prep
        if p is None:
            if d > PREP_RANGE:
                return False                    # najpierw dojazd (na koniu szybciej)
            p = self._prep = dict(vid=vid, phase="unmount", t0=time.time(), t=0.0, tries=0, buffs=[], skipped=set())
            self._clear_autoattack()
            self._in_melee = False
            mt = self._mount_check()
            self._dbg("PRZYGOTOWANIE VID %d (%s) d=%.0f: kon=%s, zsiadanie=%s, buffy=%s", vid, self._engaged_cls, d,
                      (mt[1] or "tak") if mt and mt[0] else ("nie" if mt else "?"), self.prep_dismount,
                      self.prep_buffs)
        now = time.time()

        def done(why):
            self._dbg("PRZYGOTOWANIE VID %d koniec (%s) po %.1fs: buffy %s", vid, why, now - p["t0"],
                      ", ".join(p["buffs"]) or "-")
            self._prepared_vid = vid
            self._prep = None
            return False
        if now - p["t0"] > PREP_TIMEOUT:
            return done("TIMEOUT %.0fs" % PREP_TIMEOUT)
        mt = self._mount_check()
        mounted = bool(mt and mt[0])
        if p["phase"] == "unmount":
            if not self.prep_dismount or not mounted:
                if p["tries"] and not mounted:
                    self._we_dismounted = True
                    self._dbg("zsiedlismy z konia po %.1fs (prob %d)", now - p["t0"], p["tries"])
                p["phase"] = "buff"
            elif p["tries"] >= UNMOUNT_TRIES:
                if now - p["t"] > UNMOUNT_RETRY:
                    self._dbg("NIE udalo sie zsiasc (%d prob) - walka z konia", p["tries"])
                    p["phase"] = "buff"
            elif now - p["t"] >= UNMOUNT_RETRY:
                r = self._send_chat("/unmount")
                if r is None:
                    self._dbg("zsiadanie niemozliwe: brak drogi wykonania (DLL bez !chat - restart gry)")
                    p["phase"] = "buff"
                else:
                    p["t"], p["tries"] = now, p["tries"] + 1
            if p["phase"] == "unmount":
                self._combat_status = "zsiadam z konia (proba %d/%d)" % (p["tries"], UNMOUNT_TRIES)
                return True
        if p["phase"] == "buff":
            if not self.prep_buffs:
                return done("bez buffow")
            if mounted:
                return done("na koniu - buffy niemozliwe")
            use = autobuff.make_use_fn(self.pm.process_id, self.dll_exec,
                                       self.native_drink and autopot.hook_has_skill(self.pm.process_id))
            for vnum in PREP_BUFFS:
                name = autobuff.BUFFS.get(vnum, (str(vnum), 0))[0]
                r = self.autobuff.cast_now(vnum, use)
                if r is None:
                    return done("brak drogi wykonania skilli (DLL)")
                if r == "rzucam":
                    p["buffs"].append(name)
                    self._dbg("buff przed walka: %s", name)
                if r in ("rzucam", "czekam"):
                    self._combat_status = "buff przed walka: %s" % name
                    return True
                if r != "aktywny" and vnum not in p["skipped"]:     # nie wyuczony / cooldown / odrzucony
                    p["skipped"].add(vnum)
                    self._dbg("buff %s pominiety: %s", name, r)
            return done("gotowe")
        return False

    def _remount_step(self):
        """Po walce z bossem/specjalnym (gdy zsiadalismy) - wsiadz znow na konia ('/ride'), o ile nie walczymy
        juz z kolejnym bossem/specjalnym."""
        r = self._remount
        if r is None:
            return
        now = time.time()
        if now < r["at"]:
            return
        if self._engaged_vid and self._engaged_cls in PREP_CLASSES:
            self._remount = None                # kolejny boss - zostajemy pieszo (przygotowanie zrobi reszte)
            return
        mt = self._mount_check()
        if mt and mt[0]:
            self._dbg("na koniu po walce (prob %d)", r["tries"])
            self._remount = None
            return
        if r["tries"] >= REMOUNT_TRIES:
            if now - r["t"] > REMOUNT_RETRY:
                self._dbg("NIE udalo sie wsiasc na konia (%d prob '/ride') - zostaje pieszo", r["tries"])
                self._remount = None
            return
        if now - r["t"] >= REMOUNT_RETRY:
            if self._send_chat("/ride") is None:
                self._remount = None
                return
            r["t"], r["tries"] = now, r["tries"] + 1

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
        self._melee_entry_t = 0.0
        self._ev_t = 0.0
        self._ev_n = 0
        self._side_tries = 0
        self._repos = None
        self._tried_angles = []
        self._engaged_cls = ""
        self._prep = None
        if self._we_dismounted and self.prep_remount:     # zsiedlismy przed ta walka -> po niej na konia
            self._remount = dict(at=time.time() + REMOUNT_DELAY, t=0.0, tries=0)
        self._we_dismounted = False

    def _abandon(self, vid, why):
        """Porzuc zablokowany cel: czarna lista na WALK_ENGAGE_COOL s + zdejmij nasz wpis auto-ataku."""
        self._engage_cool[vid] = time.time() + WALK_ENGAGE_COOL
        print("[farm] porzucam VID %d: %s" % (vid, why))
        self._dbg("PORZUCAM VID %d: %s (dowodow walki %d, zmian strony %d)", vid, why, self._ev_n, self._side_tries)
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
        self._trace_plan("engage", hx, hy, tx, ty, obst, hard, blobs, route, (time.time() - t0) * 1000)
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
        if vid and self._repos is not None:
            px, py, until = self._repos
            rd = math.hypot(px - hx, py - hy)
            if rd <= REPOSITION_REACH or now > until:
                self._dbg("punkt podejscia VID %d %s (d do punktu %.0f, do celu %.0f) -> oddaje atak", vid,
                          "osiagniety" if rd <= REPOSITION_REACH else "TIMEOUT", rd, d)
                self._repos = None
                self._in_melee = True
                self._melee_entry_t = now
                if not self._melee_since:
                    self._melee_since = now
            else:
                self._combat_target = (tx, ty, False)
                self._combat_status = "zmiana strony VID %d (%d/%d) d=%.0f" % (vid, self._side_tries,
                                                                               MELEE_SIDE_TRIES, rd)
                if self._stuck_nudge(hx, hy, px, py):
                    return
                route = self._engage_path(hx, hy, px, py)
                nxt = self._route_aim(route, 0, hx, hy) if route else (px, py)
                self._goto(nxt[0], nxt[1], "repos", hx, hy)
                self._last_goto = now
                return
        if vid:
            if self._in_melee:
                if d > FARM_MELEE_EXIT:
                    self._dbg("wrecz KONIEC VID %d - cel odszedl d=%.0f", vid, d)
                    self._in_melee = False
            elif d <= FARM_MELEE or (d <= FARM_HANDOFF and now - self._engaged_since > FARM_HANDOFF_SEC):
                self._dbg("wrecz START VID %d d=%.0f (%s)", vid, d,
                          "zasieg" if d <= FARM_MELEE else "oddanie klientowi - brak postepu")
                self._in_melee = True
                self._melee_entry_t = now
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
            idle, lim = self._no_evidence_for(now)
            self._combat_status = "walka VID %d  %.0fs  d=%.0f  %s" % (
                vid, now - self._melee_since, d,
                ("dowod %.1fs temu" % idle) if self._ev_t >= self._melee_entry_t else ("bez dowodu %.1fs" % idle))
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
        gp = self._goto_point                          # "podejdz tutaj": jednorazowy cel ma pierwszenstwo
        if gp is not None:
            path = [(gp[0], gp[1])]
        elif not self.autowalk_on or not path:
            return
        if not (self.native_drink or self.dll_exec):   # vCore: hook; GameCoreTop: pending-call
            if gp is not None:
                self._goto_point_done("brak drogi wykonania (DLL)")
            return
        if self.farm_on or self._looting:              # farm/zbieranie i chodzenie walczylyby o !goto
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        now = time.time()
        if gp is not None:
            if math.hypot(gp[0] - hx, gp[1] - hy) < WALK_REACH:
                self._goto_point_done("dotarlem", hx, hy)
                return
            if now - gp[2] > GOTO_POINT_TIMEOUT:
                self._goto_point_done("limit czasu %.0f s" % GOTO_POINT_TIMEOUT, hx, hy)
                return
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
        if gp is not None:
            tx, ty = path[0]
            adv = len(path)                            # bez przechodzenia po waypointach trasy
        else:
            self.walk_idx %= len(path)
            tx, ty = path[self.walk_idx]
            adv = 0
        if gp is None and self.guard.hold and math.hypot(tx - hx, ty - hy) < WALK_REACH:
            self.guard.paused("trasa: kolejny punkt")   # ochrona: doszlismy do biezacego punktu - stoimy
            return
        # przejdz przez osiagniete waypointy BEZ zatrzymania (od razu kieruj do nastepnego = plynnie)
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
            self._trace_plan("goto_point" if gp is not None else "walk", hx, hy, tx, ty, obst, hard, blobs, p,
                             (time.time() - t0) * 1000)
            self._dbg("plan %s d=%.0f: %s, %.0f ms, przeszkod %d (twardych %d)",
                      "podejdz" if gp is not None else "wp%d" % (self.walk_idx + 1), d,
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
            self._goto(tgt[0], tgt[1], "point" if gp is not None else "walk", hx, hy)
            self._last_goto = now

    def set_goto_point(self, wx, wy):
        """'Podejdz tutaj' z panelu: jednorazowy cel z planowaniem A* (jak waypoint), niezalezny od trasy.
        Ma pierwszenstwo przed trasa auto-chodzenia; po dojsciu/limicie czasu trasa wraca do pracy."""
        hx, hy = self._hero_world()
        self._goto_point = (float(wx), float(wy), time.time())
        self._route = []
        self._last_plan = 0.0
        self._walk_prog = None
        d = math.hypot(wx - hx, wy - hy) if hx is not None else -1
        self._dbg("podejdz START -> (%.0f,%.0f) d=%.0f (farm=%s loot=%s autowalk=%s exec=%s native=%s)",
                  wx, wy, d, self.farm_on, self._looting, self.autowalk_on, self.dll_exec, self.native_drink)
        tr = self._tr()
        if tr is not None:
            tr.rec("goto_point", ev="start", to=bottrace.ipt((wx, wy)),
                   hero=bottrace.ipt((hx, hy)) if hx is not None else None, d=round(d),
                   farm=self.farm_on, autowalk=self.autowalk_on)

    def _goto_point_done(self, why, hx=None, hy=None):
        gp = self._goto_point
        self._goto_point = None
        self._route = []
        self._last_plan = 0.0
        self._walk_prog = None
        if gp is None:
            return
        d = math.hypot(gp[0] - hx, gp[1] - hy) if hx is not None else -1
        self._dbg("podejdz KONIEC: %s; cel (%.0f,%.0f) d=%.0f po %.1f s", why, gp[0], gp[1], d, time.time() - gp[2])
        tr = self._tr()
        if tr is not None:
            tr.rec("goto_point", ev="end", why=why, to=bottrace.ipt(gp[:2]),
                   hero=bottrace.ipt((hx, hy)) if hx is not None else None, d=round(d),
                   sec=round(time.time() - gp[2], 2))

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

    def _draw_ground(self, c, hx, hy):
        """Drop na ziemi: zloty romb = do zebrania, szary = cudzy / pomijany, okrag = promien zbierania."""
        if not self.ground_cfg:
            return
        nick = self.ident.get("nick")
        if self.autoloot_on and not (self.loot_mode == "high" and self.dll_exec):
            cx, cy = self.world_to_canvas(hx, hy, hx, hy)
            ex, _ = self.world_to_canvas(hx + self.loot_radius, hy, hx, hy)
            r = abs(ex - cx)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#5a4a20", dash=(2, 4))
        for vid, vnum, wx, wy, owner in self.ground:
            sx, sy = self.world_to_canvas(wx, wy, hx, hy)
            if not self._in_view(sx, sy):
                continue
            mine = not (owner and nick and owner != nick) and vid not in self._loot_black
            col = ("#ffd24d" if vnum != 1 else "#fff3a0") if mine else "#777"
            c.create_polygon(sx, sy - 4, sx + 4, sy, sx, sy + 4, sx - 4, sy, fill=col, outline="#000")

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
        if self.ground_cfg:
            return self._autoloot_human()
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

    def _area_attack_step(self):
        """Masowy atak pakietem: co paczke wysyla SendAttackPacket do KAZDEGO moba w promieniu -
        BEZ lokalnej animacji i BEZ ruchu postaci. Tylko GameCoreTop (pending-call przez DLL).
        Serwer waliduje zasieg/cooldown: bez ruchu padaja tylko moby w serwerowym zasiegu wrecz.
        Tryb 'safe' = throttle AREA_ATK_SAFE_EVERY + log zasiegu; 'aggro' = co petle (spam)."""
        if not self.area_atk_on:
            return
        if not self.dll_exec:                               # tylko GameCoreTop (pending-call)
            self._area_atk_status = "wymaga GameCoreTop + DLL (brak drogi wykonania)"
            return
        now = time.time()
        every = AREA_ATK_SAFE_EVERY if self.area_atk_mode == "safe" else 0.0
        if now - self._area_atk_last < every:
            return
        if self._attack_ok is None or now - self._attack_t > 10.0:
            self._attack_ok = autopot.has_attack(self.pm.process_id)   # stara DLL nie zna !attack
            self._attack_t = now
        if not self._attack_ok:
            self._area_atk_status = "wstrzyknieta DLL bez !attack - przebuduj (build.bat)"
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        targets = []                                        # (dystans, vid, wx, wy) mobow w promieniu
        for x, y, vnum, is_pc, _o, vid in self._iter_actors(hx, hy):
            if not vid or is_pc:
                continue
            if self._classify(vnum, is_pc) not in AREA_ATK_KINDS:
                continue
            d = math.hypot(x - hx, y - hy)
            if d <= self.area_atk_radius:
                targets.append((d, vid, x, y))
        # metiny: _iter_actors pomija vnum 8xxx, wiec dokladamy je z osobnej listy (maja VID) - bijemy
        # je tak samo (stojac obok = w zasiegu; dalekie przez przeskok). Normalny atak dziala na kamien.
        metin_vids = set()
        for mx, my, mvid in self._metin_targets():
            if not mvid:
                continue
            d = math.hypot(mx - hx, my - hy)
            if d <= self.area_atk_radius:
                targets.append((d, mvid, mx, my))
                metin_vids.add(mvid)
        # test zasiegu: VID atakowany wczesniej, a teraz nieobecny = padl/odszedl. Dla KAZDEGO zgonu
        # logujemy dystans i sposob (direct/hop). maxhit liczymy tylko z BEZPOSREDNICH (hop faluje zasieg)
        # - to daje realny serwerowy zasieg ataku wprost z logow, bez odczytu z panelu.
        live = {t[1] for t in targets}
        for v in self._area_atk_prev - live:
            d0 = self._area_atk_dist.pop(v, None)
            via = "hop" if v in self._area_atk_hopset else "direct"
            self._area_atk_hopset.discard(v)
            if d0 is not None:
                self.area_atk_gone += 1
                if via == "direct" and d0 > self._area_atk_maxhit:
                    self._area_atk_maxhit = d0
                tr = self._tr()
                if tr is not None:
                    tr.rec("area_gone", vid=int(v), d=round(d0), via=via, maxhit=round(self._area_atk_maxhit))
                self._dbg("atak ZGON vid=%d d=%.0f via=%s (maxhit direct=%.0f)", v, d0, via, self._area_atk_maxhit)
        self._area_atk_prev = live
        if not targets:
            self._area_atk_status = "brak mobow w promieniu %.0f" % self.area_atk_radius
            self._area_atk_last = now
            return
        targets.sort()
        for d, v, _x, _y in targets:
            self._area_atk_dist[v] = d                       # ostatni dystans ataku kazdego celu
        pid = self.pm.process_id
        tryb = "test" if self.area_atk_mode == "safe" else "spam"
        # PRZESKOK do dalekich: moby > ATTACK_SRV_REACH bij przez skok pozycji (jak !tpick) - gina bez
        # podchodzenia; blizsze bij wprost. Bez przeskoku: wyslij do wszystkich (dalsze serwer odrzuci -
        # aggruja i same podchodza).
        hop = self.area_atk_hop
        if hop and (self._tattack_ok is None or now - self._tattack_t > 10.0):
            self._tattack_ok = autopot.has_tattack(pid)
            self._tattack_t = now
        if hop and not self._tattack_ok:
            hop = False                                      # stara DLL bez !tattack - zachowaj sie zwyczajnie
        # ZAWSZE: bij wprost wszystko w promieniu (zabija to, co realnie w serwerowym zasiegu teraz;
        # dalsze serwer odrzuci - ale gdy aggruja i podejda, kolejny tick je dobije).
        n_near = sum(1 for d, _v, _x, _y in targets if d <= ATTACK_SRV_REACH)
        n_far = len(targets) - n_near
        n_metin = sum(1 for _d, v, _x, _y in targets if v in metin_vids)
        vids = [v for _d, v, _x, _y in targets[:AREA_ATK_MAX]]
        if autopot.attack_vids(pid, vids, AREA_ATK_MOT):
            self.area_atk_count += 1
        hopped = 0
        hop_d = 0.0
        hops = 0
        if hop:
            # dalekie (poza serwerowym zasiegiem) dobij przez przeskok. 'safe': 1 skupisko co
            # ATTACK_HOP_EVERY (ostroznie, do pomiaru). 'aggro': WIELE skupisk na tik (do ATTACK_HOP_MAX_AGGRO)
            # - czysci caly obszar szybko, kosztem wielu skokow pozycji (bardziej wykrywalne).
            every_hop = ATTACK_HOP_EVERY if self.area_atk_mode == "safe" else 0.0
            max_hops = 1 if self.area_atk_mode == "safe" else ATTACK_HOP_MAX_AGGRO
            far = sorted((d, v, x, y) for d, v, x, y in targets if ATTACK_SRV_REACH < d <= ATTACK_TP_MAX)
            if far and now - self._area_hop_next >= every_hop:
                used = set()
                for d0, v0, cx, cy in far:                   # skupiska od najblizszego; kazdy mob raz
                    if v0 in used:
                        continue
                    cluster = [v for d, v, x, y in far
                               if v not in used and math.hypot(x - cx, y - cy) <= ATTACK_SRV_REACH]
                    if not cluster:
                        continue
                    if autopot.tattack(pid, cx, cy, cluster[:AREA_ATK_MAX], AREA_ATK_MOT):
                        used.update(cluster)
                        self._area_atk_hopset.update(cluster)   # te zgony nie licz do maxhit direct
                        hopped += len(cluster)
                        hop_d = max(hop_d, d0)
                        hops += 1
                    if hops >= max_hops:
                        break
                self._area_hop_next = now
        self._area_atk_status = (
            "bije %d <=%.0f [%s]%s  zniklo:%d  max skuteczny:%.0f"
            % (len(vids), self.area_atk_radius, tryb,
               ("  +przeskok %dx (%d mobow, max d%.0f)" % (hops, hopped, hop_d)) if hop else "",
               self.area_atk_gone, self._area_atk_maxhit))
        # LOG: jsonl co paczke (pelny slad do wnioskow), czytelna linia co ~1 s (bez zalewania)
        dn = [round(d) for d, _v, _x, _y in targets]
        tr = self._tr()
        if tr is not None:
            tr.rec("area_atk", n=len(targets), near=n_near, far=n_far, metin=n_metin, hop=bool(hop),
                   hops=hops, hopped=hopped, hop_d=round(hop_d), gone=self.area_atk_gone,
                   maxhit=round(self._area_atk_maxhit), mode=self.area_atk_mode,
                   radius=round(self.area_atk_radius), reach=round(ATTACK_SRV_REACH),
                   dmin=dn[0], dmax=dn[-1], dists=dn[:40])
        if now - self._area_atk_logt > 1.0:
            self._area_atk_logt = now
            self._dbg("atak: cele=%d near=%d far=%d metin=%d hop=%s hopped=%d gone=%d maxdir=%.0f "
                      "d[%.0f..%.0f] reach=%.0f tryb=%s", len(targets), n_near, n_far, n_metin,
                      hop, hopped, self.area_atk_gone, self._area_atk_maxhit, dn[0], dn[-1],
                      ATTACK_SRV_REACH, self.area_atk_mode)
        self._area_atk_last = now

    def _cape_step(self):
        """Peleryna na auto-farmie: co self.cape_period sekund, gdy w promieniu CAPE_RADIUS jest co
        najmniej self.cape_min_mobs potworow, 'uzyj' peleryne wprost z EQ (net.SendItemUsePacket - ta
        sama droga co picie mikstury po vnum). Tylko GameCoreTop (pending-call przez DLL). Pierwsze
        uzycie po wlaczeniu jest od razu (gdy sa moby); potem nie czesciej niz raz na okres."""
        if not self.cape_on:
            return
        if not (self.farm_on or self.area_atk_on):      # tylko podczas farmienia
            self._cape_status = "czeka na farme/atak"
            return
        if not self.dll_exec:                           # SendItemUsePacket idzie pending-callem (GameCoreTop)
            self._cape_status = "wymaga GameCoreTop + DLL"
            return
        if not self.cape_vnums:
            self._cape_status = "wpisz vnum w data/cape_vnums.txt"
            return
        now = time.time()
        if self._cape_last:
            left = self.cape_period - (now - self._cape_last)
            if left > 0:
                self._cape_status = "odnowa za %.0fs" % left
                return
        hx, hy = self._hero_world()
        if hx is None:
            return
        n = 0                                           # potwory w promieniu (jak masowy atak)
        for x, y, vnum, is_pc, _o, vid in self._iter_actors(hx, hy):
            if is_pc:
                continue
            if self._classify(vnum, is_pc) not in CAPE_KINDS:
                continue
            if math.hypot(x - hx, y - hy) <= self.cape_radius:
                n += 1
        if n < self.cape_min_mobs:
            self._cape_status = "czekam: %d/%d mobow" % (n, self.cape_min_mobs)
            return
        if autopot.drink_by_vnum(self.pm.process_id, self.cape_vnums):
            self.cape_count += 1
            self._cape_last = now
            self._cape_status = "uzyto (%d mobow)  razem:%d" % (n, self.cape_count)

    def _press_pickup(self):
        """Jedno "klikniecie `": klient podnosi najblizszy drop <=300 (GameCoreTop Python, vCore hook)."""
        if self.dll_exec:
            return autopot.pick_close_item(self.pm.process_id)
        if self.native_drink:
            return autopot.pick_close_item_native(self.pm.process_id)
        return False

    def _end_looting(self, now):
        if self._looting:
            self._looting = False
            self._engaged_since = now       # farma: czas zbierania to nie "brak postepu" do celu
            self._um_pos = None
            self._dbg("loot: koniec zbierania (podniesiono lacznie %d)", self.loot_count)

    def _autoloot_high(self, now, nick):
        """Tryb High: co LOOT_INSTANT_EVERY pakiet podniesienia dla KAZDEGO widzianego dropu (klient zna
        przedmioty z ~calego obszaru widocznosci), bez limitu odleglosci, bez podbiegania i bez przerywania
        farmy/walki. Pomijamy tylko cudze dropy. SERWER WALIDUJE ZASIEG (~300 od pozycji postaci po stronie
        serwera) - dalsze odrzuca po cichu; ponawiamy co paczke, az postac znajdzie sie blisko."""
        live = {vid for vid, vnum, wx, wy, owner in self.ground
                if vid not in self._loot_black and not (owner and nick and owner != nick)}
        on_ground = {g[0] for g in self.ground}
        gone = self._loot_prev - on_ground
        if gone:
            self.loot_count += len(gone)
        self._loot_prev = live
        self._loot_tries = {v: n for v, n in self._loot_tries.items() if v in live}
        if not live or now < self._loot_next:
            return
        if self._tpick_ok is None or now - self._tpick_t > 10.0:
            self._tpick_ok = autopot.has_tpick(self.pm.process_id)
            self._tpick_t = now
        hx, hy = self._hero_world()
        if self._tpick_ok and hx is not None:
            # dalsze niz zasieg serwera: jedno skupisko na skok (najblizsze najpierw), reszta zwyklym pakietem
            pos = {vid: (wx, wy) for vid, vnum, wx, wy, owner in self.ground if vid in live}
            far = sorted((math.hypot(x - hx, y - hy), v) for v, (x, y) in pos.items())
            far = [(d, v) for d, v in far if LOOT_SRV_REACH < d <= LOOT_TP_MAX]
            near = [v for v in live if math.hypot(pos[v][0] - hx, pos[v][1] - hy) <= LOOT_SRV_REACH]
            if near:
                autopot.pick_up_vids(self.pm.process_id, sorted(near))
            if far:
                cx, cy = pos[far[0][1]]
                grp = [v for _, v in far if math.hypot(pos[v][0] - cx, pos[v][1] - cy) <= LOOT_SRV_REACH]
                autopot.tpick(self.pm.process_id, cx, cy, grp)
                self._dbg("loot high: skok %.0f -> %d przedm.", far[0][0], len(grp))
            self._loot_next = now + (LOOT_TP_EVERY if far else LOOT_INSTANT_EVERY)
            return
        # BEZ czarnej listy: serwer odmawia tylko z odleglosci (log.GET 2026-10-28: wszystkie podniesienia
        # przy postaci <=~300 od przedmiotu), wiec odrzucony teraz przedmiot moze sie udac, gdy farma podejdzie.
        if autopot.pick_up_vids(self.pm.process_id, sorted(live)):
            for v in live:
                self._loot_tries[v] = self._loot_tries.get(v, 0) + 1
                if self._loot_tries[v] == LOOT_HIGH_TRIES:
                    self._dbg("loot high: VID %d lezy po %d paczkach (serwer: za daleko?) - ponawiam", v,
                              LOOT_HIGH_TRIES)
        self._loot_next = now + LOOT_INSTANT_EVERY

    def _autoloot_human(self):
        """Zbieranie "jak gracz" na podstawie widzianych dropow (patrz komentarz przy LOOT_*)."""
        now = time.time()
        try:
            self.ground = autopot.read_ground_items(self.pm, self.base, self.ground_cfg)
        except Exception:
            self.ground = []
        if not self.autoloot_on or not (self.dll_exec or self.native_drink):
            self._end_looting(now)
            return
        hx, hy = self._hero_world()
        if hx is None:
            return
        nick = self.ident.get("nick")
        self._loot_black = {v: t for v, t in self._loot_black.items() if t > now}
        if self.loot_mode == "high" and self.dll_exec:
            self._end_looting(now)
            return self._autoloot_high(now, nick)
        cand = []
        for vid, vnum, wx, wy, owner in self.ground:
            if vid in self._loot_black or (owner and nick and owner != nick):
                continue                    # czarna lista / cudzy drop (serwer i tak odmowi)
            d = math.hypot(wx - hx, wy - hy)
            if d <= self.loot_radius:
                cand.append((d, vid, wx, wy))
        live = {c[1] for c in cand}
        on_ground = {g[0] for g in self.ground}
        if self._looting:                   # zniknely z ziemi w trakcie zbierania = podniesione
            gone = self._loot_prev - on_ground
            self.loot_count += len(gone)
            if gone:
                self._loot_fails = 0
        self._loot_prev = live
        self._loot_tries = {v: n for v, n in self._loot_tries.items() if v in live}
        if not cand:
            # po ostatnim przedmiocie czlowiek jeszcze raz-dwa "dociska" klawisz
            if self._looting and self._loot_extra > 0 and now >= self._loot_next:
                self._loot_extra -= 1
                self._press_pickup()
                self._loot_next = now + random.uniform(*LOOT_PRESS)
                return
            self._end_looting(now)
            return
        if now < self._loot_pause_until:
            self._end_looting(now)
            return
        # walka ma pierwszenstwo: bijemy cel / ktos nas bije -> zbieramy dopiero po walce
        if (self._engaged_vid and self._in_melee) or (
                (self.farm_on or self.autowalk_on) and self.farm_off and self._aggressors(hx, hy)):
            self._end_looting(now)
            return
        if not self._looting:
            self._looting = True
            self._clear_autoattack()
            self._loot_next = now + random.uniform(*LOOT_REACT)
            self._loot_extra = random.randint(*LOOT_EXTRA)
            self._dbg("loot: widze %d przedmiot(ow), najblizszy d=%.0f", len(cand), min(cand)[0])
        if now < self._loot_next:
            return
        if self.loot_mode == "instant" and self.dll_exec:
            # docelowo: wszystko naraz, bez podbiegania - zasieg/wlasciciela sprawdza tylko serwer
            if autopot.pick_up_vids(self.pm.process_id, sorted(live)):
                for v in live:
                    self._loot_tries[v] = self._loot_tries.get(v, 0) + 1
                    if self._loot_tries[v] >= 3:          # serwer odmawia (zasieg/pelne EQ) -> nie spamuj
                        self._loot_black[v] = now + LOOT_BLACKLIST
            self._loot_next = now + LOOT_INSTANT_EVERY
            return
        d, vid, tx, ty = min(cand)
        if d > LOOT_REACH:                  # podbiegnij (A*, odblokowanie jak przy dochodzeniu do celu)
            self._combat_status = "loot: dojscie d=%.0f (%d w promieniu)" % (d, len(cand))
            if now - self._last_goto < WALK_RESEND or self._stuck_nudge(hx, hy, tx, ty):
                return
            route = self._engage_path(hx, hy, tx, ty)
            nxt = self._route_aim(route, 0, hx, hy) if route else (tx, ty)
            self._goto(nxt[0], nxt[1], "loot", hx, hy)
            self._last_goto = now
            # po dobiegnieciu znow chwila, zanim zaczniemy klikac
            self._loot_next = now + (random.uniform(*LOOT_REACT) * 0.5 if d < LOOT_REACH * 2 else 0.0)
            return
        self._um_pos = None
        in_reach = [c for c in cand if c[0] <= 300]
        n = self._loot_tries[vid] = self._loot_tries.get(vid, 0) + 1
        if n > LOOT_TRIES:                  # klikamy, a lezy dalej: pelne EQ / nie nasz (nieznany wlasciciel)
            self._loot_black[vid] = now + LOOT_BLACKLIST
            self._loot_fails += 1
            self._dbg("loot: VID %d nie schodzi po %d kliknieciach -> pomijam %.0fs", vid, n - 1, LOOT_BLACKLIST)
            if self._loot_fails >= LOOT_FAIL_PAUSE[0]:
                self._loot_fails = 0
                self._loot_pause_until = now + LOOT_FAIL_PAUSE[1]
                print("[loot] %d przedmiotow nie daje sie podniesc - pelne EQ? pauza %.0f s"
                      % (LOOT_FAIL_PAUSE[0], LOOT_FAIL_PAUSE[1]))
            return
        self._combat_status = "loot: klikam (%d w zasiegu)" % len(in_reach)
        self._press_pickup()
        gap = random.uniform(*LOOT_PRESS)
        if random.random() < LOOT_PRESS_PAUSE[0]:
            gap += random.uniform(*LOOT_PRESS_PAUSE[1])
        self._loot_next = now + gap

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
        now = time.time()
        prev_hp, self._pot_prev_hp = self._pot_prev_hp, hp
        # porcje leczenia schodza z "w drodze"; po POTION_RECOVERY_SEC reszte uznajemy za stracona
        if prev_hp is not None and hp > prev_hp:
            self._pot_expect = max(0, self._pot_expect - (hp - prev_hp))
            self._pot_gain += hp - prev_hp
            if self._pot_unconfirmed is not None:
                self._pot_unconfirmed = None
                self._pot_nogain = 0
        if self._pot_expect and now - self._last_pot >= POTION_RECOVERY_SEC:
            self._pot_expect = 0
        if self._pot_unconfirmed is not None and now - self._pot_unconfirmed >= POTION_CONFIRM_TIMEOUT:
            self._pot_unconfirmed = None
            self._pot_nogain += 1
            if self._pot_nogain >= POTION_NOGAIN_LIMIT:
                self._pot_nogain = 0
                self._pot_pause_until = now + POTION_NOGAIN_PAUSE
                print("[mikstura] %d mikstur bez wzrostu HP (%d/%d) - pauza %.0f s"
                      % (POTION_NOGAIN_LIMIT, hp, mhp, POTION_NOGAIN_PAUSE))
                self._dbg("mikstura: %d bez wzrostu HP (%d/%d) - pauza %.0f s (koniec mikstur w EQ?)",
                          POTION_NOGAIN_LIMIT, hp, mhp, POTION_NOGAIN_PAUSE)
        if hp <= 0:                       # martwy - nie pijemy (i tak nie zadziala, marnuje mikstury)
            self._healing = False
            self._pot_dead = True
            self._pot_expect = 0
            return
        if self._pot_dead:                # wskrzeszenie: czlowiek najpierw sie rozglada
            self._pot_dead = False
            self._pot_pause_until = max(self._pot_pause_until, now + random.uniform(*POTION_RESPAWN))
        # histereza: zacznij ponizej low, przestan dopiero po dojsciu do high
        if pct < self.pot_low:
            if not self._healing:         # dopiero zauwazylismy spadek - czas reakcji
                self._pot_next_ok = max(self._pot_next_ok, now + random.uniform(*POTION_REACT))
            self._healing = True
        elif pct >= self.pot_high or hp >= mhp:
            self._healing = False
        if not (self.autopot_on and self._healing):
            return
        if pct >= POTION_CRIT_PCT and 100.0 * (hp + self._pot_expect) / mhp >= self.pot_high:
            return                        # wypite mikstury jeszcze lecza - wystarczy (poza stanem krytycznym)
        if now < self._pot_pause_until or now < self._pot_next_ok:
            return
        if now - self._last_pot < self.pot_cd:
            return
        self._pot_times = [t for t in self._pot_times if now - t < POTION_RATE_WINDOW]
        if len(self._pot_times) >= POTION_RATE_MAX:
            return                        # bezpiecznik tempa
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
            # pomiar: poprzednia mikstura miala pelne okno dla siebie -> jej realne leczenie (np. zaznaczona
            # D, ale w EQ zostaly tylko M - tabela przeszacowalaby i bot pilby za rzadko)
            if self._last_pot and now - self._last_pot >= POTION_RECOVERY_SEC and self._pot_gain > 0:
                self._pot_heal_seen = self._pot_gain
            self._pot_gain = 0
            self._last_pot = now
            # nie wiemy, ktora z zaznaczonych mikstur DLL znalazla w EQ - zakladamy NAJSLABSZA (ostroznie)
            heal = (min(POTION_HEAL.get(v, POTION_HEAL_SLOT) for v in self.pot_vnums)
                    if via == "EQ" else POTION_HEAL_SLOT)
            if self._pot_heal_seen:
                heal = min(heal, self._pot_heal_seen)
            self._pot_expect = min(self._pot_expect + heal, mhp - hp)   # serwer i tak tnie do maxHP
            if self._pot_unconfirmed is None:
                self._pot_unconfirmed = now
            gap = POTION_GAP_CRIT if pct < POTION_CRIT_PCT else POTION_GAP
            self._pot_next_ok = now + random.uniform(*gap)
            self._pot_times.append(now)
            self.pot_count += 1
            print("[mikstura] HP %d/%d (%.0f%%) -> %s" % (hp, mhp, pct, via))
            self._dbg("mikstura #%d: HP %d/%d (%.0f%%) via=%s w_drodze=%d leczenie~%d (zmierzone %s) tempo %d/%.0fs",
                      self.pot_count, hp, mhp, pct, via, self._pot_expect, heal, self._pot_heal_seen or "-",
                      len(self._pot_times), POTION_RATE_WINDOW)
            tr = self._tr()
            if tr is not None:
                tr.rec("pot", hp=hp, mhp=mhp, via=via, expect=self._pot_expect, heal=heal,
                       seen=self._pot_heal_seen, rate=len(self._pot_times), n=self.pot_count)
        elif self.autopot_on:
            self._dbg("mikstura: NIE wypita (exec=%s native=%s vnums=%s hwnd=%s) HP %d/%d",
                      self.dll_exec, self.native_drink, self.pot_vnums, bool(self.hwnd), hp, mhp)

    def _mount_check(self):
        """Czy jedziemy na wierzchowcu: aktor-kon (z rescan) blizej MOUNT_DIST od wlasnej postaci.
        Zwraca (True/False, nazwa_modelu) albo None, gdy nie znamy wlasnego aktora. 2 odczyty na aktora."""
        me = self.self_actor
        if me is None:
            return None
        hoff = HORSE_PTR_OFFSETS.get(self.proc_name.lower())
        if hoff is not None:                         # m_pkHorse: wskaznik na aktora-wierzchowca albo NULL
            try:
                hp = self.pm.read_uint(me + hoff)
                if hp and self.pm.read_uint(hp) == self.vt[ACTOR_CLASS]:
                    m = self._actor_model(hp) or ""
                    return True, m.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0]
                return False, ""
            except Exception:
                pass                                 # nieczytelne -> heurystyka ponizej
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
        if self.ground_cfg:
            mode = {"human": "jak gracz", "instant": "naraz", "high": "HIGH bez limitu"}[self.loot_mode]
            if self.loot_mode != "human" and not self.dll_exec:
                mode = "jak gracz (%s tylko GameCoreTop)" % self.loot_mode
            rad = "wszystko" if (self.loot_mode == "high" and self.dll_exec) else "do %d" % self.loot_radius
            if self.loot_mode == "high" and self._tpick_ok:
                rad += " +skok<=%d" % LOOT_TP_MAX
            loot_txt = "[L]auto-loot %s  na ziemi:%d  %s  %s%s  (%s)" % (
                loot_state, len(self.ground), rad, mode,
                "  ZBIERAM" if self._looting else "", loot_via)
        else:
            loot_txt = "[L]auto-loot %s  co%.1fs (najblizszy <=300, %s)" % (
                loot_state, self.loot_interval, loot_via)
        c.create_text(8, 47, anchor="w", fill=loot_col, text=loot_txt, font=("Consolas", 8))
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
        if self.area_atk_on:                        # masowy atak pakietem - stan obok walki
            fighting = True
            fight_txt += "  ||  ATAK PAKIETEM: " + (self._area_atk_status or "...")
        if self.cape_on:                            # peleryna na farmie - stan obok walki
            fight_txt += "  ||  PELERYNA: " + (self._cape_status or "...")
        c.create_text(8, 75, anchor="w", fill="#ff5ea8" if self.area_atk_on else ("#ff6a6a" if fighting else "#999"),
                      text=fight_txt, font=("Consolas", 8))
        # auto-buff: stan kazdego wyuczonego buffa (aktywny / cd / gotowy)
        ab = self.autobuff
        c.create_rectangle(2, 82, 560, 96, fill="#0b0f0b", outline="")
        c.create_text(8, 89, anchor="w", fill="#7fe0ff" if ab.on else "#999",
                      text="[U]buff %s uzyto:%d  %s" % (ab.status or "WYL", ab.count, ab.summary()),
                      font=("Consolas", 8))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Radar z obrazem mapy (Metin2).")
    p.add_argument("map_name", nargs="?", default=None,
                   help="wymuszona mapa, np. metin2_map_a1 (domyslnie: biezaca mapa gracza)")
    # "-exe" jako alias: bez niego argparse czyta "-exe X" jako "-e xe" + map_name=X
    p.add_argument("-e", "--exe", "-exe", default=None, metavar="EXE",
                   help="nazwa procesu gry (domyslnie: jedyny uruchomiony z %s)" % ", ".join(clients.GAME_EXES))
    p.add_argument("-p", "--pid", type=int, default=None,
                   help="PID klienta - wymagany, gdy dziala kilka klientow (lista: python recon/clients.py)")
    p.add_argument("--headless", action="store_true",
                   help="bez okna radaru - sterowanie tylko z panelu WWW (python recon/hub.py)")
    args = p.parse_args(argv)
    if args.exe and not args.exe.lower().endswith(".exe"):   # "-e vCore" == "-e vCore.exe"
        args.exe += ".exe"
    return args


def main():
    args = parse_args()
    try:
        pid, exe = clients.pick(args.exe, args.pid)
    except LookupError as e:
        print("[!] %s" % e)
        return
    try:
        root = tk.Tk()
    except Exception as e:
        print("Blad tkinter:", e)
        return
    root.title("Metin2 Radar - MAPA (%s, PID %d)" % (exe, pid))
    try:
        RadarMap(root, args.map_name, exe, pid, headless=args.headless)
    except pymem.exception.ProcessNotFound:
        print("[!] Proces %s (PID %d) zniknal. Gra wlaczona?" % (exe, pid))
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
