"""Sonda: znajdz PRAWDZIWY offset m_eActorType (TYPE_PC=6) niezaleznie od klasy gracza.

Powod: kolorowanie graczy po vnum-4 dziala dla jednej klasy (sura=bialy), a inne klasy
(szaman/ninja/wojownik) wychodza czerwone -> hipoteza "vnum-4" jest bledna lub niepelna.

Metoda: dla kazdego aktora (CActorInstance) ustalamy GROUND TRUTH z modelu:
  model ".../pc/..." lub ".../pc2/..." = GRACZ, ".../monster/..." = MOB, ".../npc/..." = NPC.
Potem dla kazdego offsetu kandydata (o-0xA0..o+0x40) liczymy rozklad wartosci 0..10 osobno
dla graczy i mobow. Prawdziwy offset m_eActorType: gracze==6, moby==0. Enum nie klamie.

Uruchom:  py -3 recon/probe_actortype.py [GameCoreTop.exe|vCore.exe]
Postac musi byc w grze; najlepiej z widocznymi innymi graczami roznych klas.
"""
import collections
import math
import os
import struct
import sys

import numpy as np
import pymem
import pymem.process

import rtti
import calibrate as cal

PROC = sys.argv[1] if len(sys.argv) > 1 else "GameCoreTop.exe"
HERO_CLASS = "CGameEventManager"
ACTOR_CLASS = "CActorInstance"
HERO_POS = 0x8
PROTO_DIR = r"C:/Metin/Terenzo 2024 - Muchomor Upgrade/GameCore/Dump_Proto"
DEFAULT_OFFSETS = dict(mob_x=0x50C, race=0x420, race_model=0x20, vnum=-0x70,
                       name=-0x1CC, eff_data=0x290, eff_fname=0x44, eff_x=0x280)
TYPES = {0: "ENEMY", 1: "NPC", 2: "STONE", 3: "WARP", 4: "DOOR", 5: "BUILDING",
         6: "PC", 7: "POLY", 8: "HORSE", 9: "GOTO", 10: "OBJECT"}

# --- regiony zapisywalne (jak w radar_map.writable_regions) ---
import ctypes
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
WRITABLE = {0x04, 0x08, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD)]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def regions(h):
    out, a, mbi = [], 0, MBI()
    while a < 0x7FFFFFFF:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WRITABLE and 0 < s <= 512 * 1024 * 1024:
            out.append((b, s))
        n = b + s
        if n <= a:
            break
        a = n
    return out


def read_u32(a):
    try:
        return pm.read_uint(a)
    except Exception:
        return None


def std_string(addr, enc="cp1250"):
    try:
        size = pm.read_uint(addr + 0x10)
        cap = pm.read_uint(addr + 0x14)
        if not (0 < size < 1000 and 0 <= cap < 1_000_000):
            return ""
        raw = pm.read_bytes(pm.read_uint(addr), size) if cap >= 16 else pm.read_bytes(addr, size)
        return raw.decode(enc, "ignore")
    except Exception:
        return ""


pm = pymem.Pymem(PROC)
mod = pymem.process.module_from_name(pm.process_handle, PROC)
base, size = mod.lpBaseOfDll, mod.SizeOfImage
vt = rtti.find_vtables(pm, base, size, [HERO_CLASS, ACTOR_CLASS])
hero_vt, actor_vt = vt[HERO_CLASS], vt[ACTOR_CLASS]
print("proc=%s base=%#x hero_vt=%#x actor_vt=%#x" % (PROC, base, hero_vt, actor_vt))

hero, actors, effs = None, [], []
for rb, rs in regions(pm.process_handle):
    try:
        buf = pm.read_bytes(rb, rs)
    except Exception:
        continue
    u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
    if hero is None:
        hh = np.nonzero(u == hero_vt)[0]
        if len(hh):
            hero = rb + int(hh[0]) * 4
    for h in np.nonzero(u == actor_vt)[0]:
        actors.append(rb + int(h) * 4)

hx = pm.read_float(hero + HERO_POS)
hy = -pm.read_float(hero + HERO_POS + 4)
print("hero=%#x world=(%.0f,%.0f) aktorow=%d" % (hero, hx, hy, len(actors)))

off, report = cal.calibrate(pm, base, size, actors, effs, hx, hy, DEFAULT_OFFSETS, PROTO_DIR)
print("\n--- kalibracja ---")
for r in report:
    print("  " + r)
vnum_off, race_off, rm_off = off["vnum"], off["race"], off["race_model"]


def model_of(o):
    rp = read_u32(o + race_off)
    if not rp or not (0x10000 < rp < 0x7FFF0000):
        return ""
    return std_string(rp + rm_off, "latin-1").lower().replace("\\", "/")


def group_of(model):
    if "/pc/" in model or "/pc2/" in model:
        return "GRACZ"
    if "/monster/" in model:
        return "MOB"
    if "/npc/" in model:
        return "NPC"
    return "?"


# --- zbierz aktorow z pozycja, modelem, vnum, nazwa ---
rows = []
for o in actors:
    x = None
    try:
        x = pm.read_float(o + off["mob_x"])
        y = pm.read_float(o + off["mob_x"] + 4)
    except Exception:
        pass
    vnum = read_u32(o + vnum_off)
    model = model_of(o)
    grp = group_of(model)
    name = std_string(o + off["name"], "cp1250")
    d = math.hypot(x - hx, y - hy) if (x is not None and math.isfinite(x) and 1 < abs(x) < 1e7) else -1
    rows.append((d, o, vnum, grp, name, model))

cnt = collections.Counter(r[3] for r in rows)
print("\ngrupy wg modelu: %s" % dict(cnt))

# --- skan WNETRZA CActorInstance: offsety o+0..o+0x900 ---
# vnum (-0x70) lezy PRZED aktorem (w obiekcie zawierajacym), wiec m_eActorType szukamy
# W SRODKU CActorInstance (dodatnie offsety od o). Kotwica: NPC==1, MOB==0, (GRACZ==6).
print("\n=== KANDYDACI m_eActorType w CActorInstance (offset od o; szukamy NPC=1, MOB=0) ===")
print("offset   GRACZ            MOB                        NPC")
best, npc_anchor = [], []
for delta in range(0, 0x900, 4):
    pg, pmb, pn = collections.Counter(), collections.Counter(), collections.Counter()
    for d, o, vnum, grp, name, model in rows:
        v = read_u32(o + delta)
        if v is None or v > 10:
            continue
        if grp == "GRACZ":
            pg[v] += 1
        elif grp == "MOB":
            pmb[v] += 1
        elif grp == "NPC":
            pn[v] += 1
    nm = sum(pmb.values())
    nn = sum(pn.values())
    mob_zero = nm > 0 and pmb.get(0, 0) == nm
    npc_one = nn > 0 and pn.get(1, 0) == nn
    gr_six = pg.get(6, 0) == sum(pg.values()) and sum(pg.values()) > 0
    tag = ""
    if mob_zero and npc_one:
        npc_anchor.append(delta)
        tag = "  <== NPC=1,MOB=0"
        if gr_six:
            best.append(delta)
            tag += " GRACZ=6 !!!"
    # pokazuj tylko wiersze z jakims sensownym sygnalem (nie 50 pustych)
    if mob_zero and (npc_one or pg):
        print("%#06x  %-15s %-25s %-12s%s" % (
            delta, dict(sorted(pg.items())), dict(sorted(pmb.items())),
            dict(sorted(pn.items())), tag))

print("\nOffsety gdzie NPC=1 i MOB=0: %s" % [hex(d) for d in npc_anchor])
if best:
    print("PRAWDZIWY m_eActorType offset od o: %s" % [hex(d) for d in best])
else:
    print("Brak offsetu z GRACZ=6 (moze brak innych graczy w poblizu - patrz NPC-kotwice wyzej).")

# --- PELNA lista aktorow wg dist: nazwa, vnum, grupa, pelny model ---
print("\n=== WSZYSCY aktorzy wg odleglosci (nazwa / vnum / grupa / model) ===")
for d, o, vnum, grp, name, model in sorted(rows, key=lambda r: (r[0] if r[0] >= 0 else 9e9))[:70]:
    print("  d=%7.0f %#011x [%-5s] vnum=%-6s %-22r %s" % (
        d, o, grp, vnum, name, model or "(brak modelu)"))
