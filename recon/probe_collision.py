"""Sonda: odczyt SIATKI KOLIZJI (ATTRIBUTE_BLOCK) z pamieci klienta i podglad wokol gracza.

Uklad (GameCoreTop, z analizy isAttrOn):
  CMapOutdoor M: M+0x40 CTerrain* m_pTerrain[9] (3x3 wokol gracza); M+0x70/0x72 countX/Y (short);
                 M+0x74/0x76 curCoordX/Y (short).
  CTerrain  T:  T+0x18E1A BYTE m_abyAttrMap[256*256], index = ly*256 + lx.
  sektor = 25600 jedn. = 256 komorek; komorka = 100 jedn. = 1 px minimapy. BLOCK = bit0.
Algorytm is_block(wx,wy): tcx=wx//25600; num=(tcy-curY+1)*3+(tcx-curX+1) (0..8); T=m_pTerrain[num];
  lx=(wx-tcx*25600)//100; ly=(wy-tcy*25600)//100; attr=T[0x18E1A+ly*256+lx]; blocked=attr&1.

Uruchom: py -3 recon/probe_collision.py [GameCoreTop.exe]  (postac w swiecie).
"""
import sys
import pymem
import pymem.process
import ctypes
from ctypes import wintypes
import numpy as np
import rtti

PROC = sys.argv[1] if len(sys.argv) > 1 else "GameCoreTop.exe"
SECTOR = 25600
CELL = 100
ATTR_OFF = 0x18E1A
BLOCK = 0x01

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
WR = {0x04, 0x08, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [("B", ctypes.c_void_p), ("A", ctypes.c_void_p), ("AP", wintypes.DWORD),
                ("a1", wintypes.DWORD), ("RS", ctypes.c_size_t), ("S", wintypes.DWORD),
                ("P", wintypes.DWORD), ("T", wintypes.DWORD), ("a2", wintypes.DWORD)]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def regions(h):
    out, a, m = [], 0, MBI()
    while a < 0x7FFFFFFF:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(m), ctypes.sizeof(m)):
            break
        b, s, p = (m.B or 0), (m.RS or 0), m.P
        if m.S == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WR and 0 < s <= 512 * 1024 * 1024:
            out.append((b, s))
        n = b + s
        if n <= a:
            break
        a = n
    return out


pm = pymem.Pymem(PROC)
mod = pymem.process.module_from_name(pm.process_handle, PROC)
base, size = mod.lpBaseOfDll, mod.SizeOfImage
vt = rtti.find_vtables(pm, base, size, ["CMapOutdoor", "CGameEventManager"])
map_vt, hero_vt = vt["CMapOutdoor"], vt["CGameEventManager"]
mapout = hero = None
for rb, rs in regions(pm.process_handle):
    try:
        buf = pm.read_bytes(rb, rs)
    except Exception:
        continue
    u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
    if mapout is None:
        h = np.nonzero(u == map_vt)[0]
        if len(h):
            mapout = rb + int(h[0]) * 4
    if hero is None:
        h = np.nonzero(u == hero_vt)[0]
        if len(h):
            hero = rb + int(h[0]) * 4
    if mapout and hero:
        break
hx = pm.read_float(hero + 0x8)
hy = -pm.read_float(hero + 0xC)
countX = pm.read_short(mapout + 0x70)
countY = pm.read_short(mapout + 0x72)
curX = pm.read_short(mapout + 0x74)
curY = pm.read_short(mapout + 0x76)
print("proc=%s base=%#x" % (PROC, base))
print("CMapOutdoor=%#x hero world=(%.0f,%.0f)" % (mapout, hx, hy))
print("countX=%d countY=%d curCoord=(%d,%d)" % (countX, countY, curX, curY))


def terrain_ptr(tcx, tcy):
    num = (tcy - curY + 1) * 3 + (tcx - curX + 1)
    if num < 0 or num >= 9:
        return None
    try:
        p = pm.read_uint(mapout + 0x40 + num * 4)
    except Exception:
        return None
    return p if 0x10000 < p < 0x7FFF0000 else None


_tcache = {}


def is_block(wx, wy):
    if wx < 0 or wy < 0:
        return None            # poza/wolne
    if wx > countX * SECTOR or wy > countY * SECTOR:
        return None
    tcx, tcy = int(wx) // SECTOR, int(wy) // SECTOR
    T = _tcache.get((tcx, tcy), 0)
    if T == 0:
        T = terrain_ptr(tcx, tcy)
        _tcache[(tcx, tcy)] = T
    if not T:
        return None            # sektor niezaladowany
    lx = (int(wx) - tcx * SECTOR) // CELL
    ly = (int(wy) - tcy * SECTOR) // CELL
    try:
        attr = pm.read_uchar(T + ATTR_OFF + ly * 256 + lx)
    except Exception:
        return None
    return bool(attr & BLOCK)


print("\nKomorka pod graczem: blocked=%s (powinno byc False)" % is_block(hx, hy))

# podglad: siatka 41x41 komorek (po 100 jedn.) wokol gracza; '#'=blok, '.'=wolne, ' '=niezaladowane, '@'=gracz
R = 20
print("\nMapa kolizji wokol gracza (%d..%d komorek, 1 znak = 100 jedn.):" % (-R, R))
cx, cy = int(hx) // CELL, int(hy) // CELL
nblk = nfree = nunk = 0
for gy in range(cy - R, cy + R + 1):
    row = []
    for gx in range(cx - R, cx + R + 1):
        if gx == cx and gy == cy:
            row.append("@")
            continue
        b = is_block(gx * CELL + 1, gy * CELL + 1)
        if b is None:
            row.append(" "); nunk += 1
        elif b:
            row.append("#"); nblk += 1
        else:
            row.append("."); nfree += 1
    print("".join(row))
print("\nblok=%d wolne=%d niezaladowane=%d" % (nblk, nfree, nunk))
