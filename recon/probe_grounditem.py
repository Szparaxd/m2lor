"""Sonda: wykryj PRZEDMIOTY NA ZIEMI (dropy) na zywej grze i potwierdz offsety.

Struktura (Source Client PythonItem.h): CPythonItem::SGroundItemInstance, virtual dtor -> vtable@+0x00:
  +0x00 vtable
  +0x04 DWORD dwVirtualNumber  (vnum itemu; 1 = yang/money)
  +0x08 D3DXVECTOR3 v3EndPosition = (x, -y, z)  -> worldX=+0x08, worldY=-(+0x0C)
Nazwa RTTI (zagniezdzona struct, MSVC): ".?AUSGroundItemInstance@CPythonItem@@" (U=struct).

Uruchom:  py -3 recon/probe_grounditem.py [vCore.exe|GameCoreTop.exe]
MUSZA byc dropy na ziemi (zabij moba albo wyrzuc kilka itemow przy postaci).
"""
import math
import sys

import numpy as np
import pymem
import pymem.process

import rtti

PROC = sys.argv[1] if len(sys.argv) > 1 else "vCore.exe"
HERO_CLASS = "CGameEventManager"
HERO_POS = 0x8

# kandydaci na nazwe RTTI struktury (U=struct, V=class; zagniezdzona i nie)
NAME_CANDIDATES = [
    ".?AUSGroundItemInstance@CPythonItem@@",
    ".?AVSGroundItemInstance@CPythonItem@@",
    ".?AUSGroundItemInstance@@",
    ".?AVSGroundItemInstance@@",
]

# --- regiony zapisywalne ---
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


def resolve_vtables(img, u, base, raw_name):
    """Adresy vtable dla pelnej nazwy RTTI (TD -> COL offset 0 -> vtable). [] gdy brak nazwy."""
    pos = img.find(raw_name.encode() + b"\x00")
    if pos < 0:
        return None
    td = base + pos - 8
    vts = []
    for i in np.nonzero(u == td)[0]:
        col_off = int(i) * 4 - 0xC
        if col_off < 0:
            continue
        if u[col_off // 4] != 0 or u[col_off // 4 + 1] != 0:   # sig=0, offset podobiektu=0
            continue
        col = base + col_off
        vts += [base + int(j) * 4 + 4 for j in np.nonzero(u == col)[0]]
    return vts


pm = pymem.Pymem(PROC)
mod = pymem.process.module_from_name(pm.process_handle, PROC)
base, size = mod.lpBaseOfDll, mod.SizeOfImage
img = rtti.read_image(pm, base, size)
u = np.frombuffer(img[:(len(img) // 4) * 4], dtype="<u4")
print("proc=%s base=%#x" % (PROC, base))

# hero (do policzenia odleglosci)
hero_vt = rtti.find_vtables(pm, base, size, [HERO_CLASS])[HERO_CLASS]
hero = None
for rb, rs in regions(pm.process_handle):
    try:
        buf = pm.read_bytes(rb, rs)
    except Exception:
        continue
    uu = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
    hh = np.nonzero(uu == hero_vt)[0]
    if len(hh):
        hero = rb + int(hh[0]) * 4
        break
hx = pm.read_float(hero + HERO_POS)
hy = -pm.read_float(hero + HERO_POS + 4)
print("hero world=(%.0f,%.0f)" % (hx, hy))

# znajdz vtable struktury dropa
item_vt = None
for nm in NAME_CANDIDATES:
    vts = resolve_vtables(img, u, base, nm)
    if vts is None:
        print("  nazwa %-40s : BRAK w exe" % nm)
        continue
    print("  nazwa %-40s : %d vtable %s" % (nm, len(vts), [hex(v) for v in vts]))
    if len(vts) == 1 and item_vt is None:
        item_vt = vts[0]
if item_vt is None:
    print("\nNie znaleziono jednoznacznej vtable struktury dropa. Jesli wszystkie 'BRAK' - inna nazwa;")
    print("wtedy trzeba przejsc std::map CPythonItem (osobny krok). Konczę.")
    sys.exit()
print("\nitem_vt = %#x" % item_vt)

# skan dropow
drops = []
for rb, rs in regions(pm.process_handle):
    try:
        buf = pm.read_bytes(rb, rs)
    except Exception:
        continue
    uu = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
    for h in np.nonzero(uu == item_vt)[0]:
        o = rb + int(h) * 4
        try:
            vnum = pm.read_uint(o + 0x04)
            x = pm.read_float(o + 0x08)
            y = -pm.read_float(o + 0x0C)
            z = pm.read_float(o + 0x10)
        except Exception:
            continue
        if not (math.isfinite(x) and 1 < abs(x) < 1e7):
            continue
        d = math.hypot(x - hx, y - hy)
        drops.append((d, o, vnum, x, y, z))

print("\nznalezionych dropow: %d" % len(drops))
print("=== dropy wg odleglosci (d w jedn. swiata; <=300 = w zasiegu PickCloseItem) ===")
for d, o, vnum, x, y, z in sorted(drops)[:40]:
    inrange = "  <= W ZASIEGU" if d <= 300 else ""
    print("  d=%8.0f %#011x vnum=%-6d pos=(%.0f,%.0f,%.0f)%s" % (d, o, vnum, x, y, z, inrange))
if not drops:
    print("  (brak) - wyrzuc kilka itemow na ziemie / zabij moba i uruchom ponownie.")
