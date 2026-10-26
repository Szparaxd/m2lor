"""KIERUNEK #1: szeroki skan PAR floatow (X,Y) blisko gracza BEZ zalozenia o offsecie 0x264
ani o vtable. Metin stoi ~100-800 od gracza, ale NIE jako mob. Szukamy dowolnej pary floatow
(x,y) gdzie x~cx i y~cy (lub -cy). Dla kazdego trafienia: delta offsetu x->y, oraz czy w
poblizu (wstecz, do -0x400) jest WSKAZNIK MODULOWY (kandydat na vtable) i na jakim offsecie.
Wyklucz pozycje mobow (0x264 obiektu z mob-vtable). Cel: znalezc strukture obiektu mapy/metina."""
import ctypes, math
from ctypes import wintypes
from collections import Counter
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
RING_LO, RING_HI = 30.0, 2000.0   # pierscien dystansu wokol gracza
TOL = 2.0                          # tolerancja dopasowania osi (float jednostki)
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
        if mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WRITABLE and 0 < s <= 512*1024*1024:
            out.append((b, s))
        n = b + s
        if n <= a: break
        a = n
    return out


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
regs = regions(pm.process_handle)
cache = {}
for rb, rs in regs:
    try: cache[(rb, rs)] = pm.read_bytes(rb, rs)
    except Exception: pass

# hero
hero = None
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == base+HERO_VT_OFF)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f}) base={base:#x} regs={len(regs)}")

# adresy pol pozycji mobow (zeby je wykluczyc): obj+0x264 oraz obj+0x268
mob_pos_addr = set()
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == base+MOB_VT_OFF)[0]:
        o = rb+int(h)*4
        for off in (0x264, 0x268, 0x26c, 0x270, 0x27c):
            mob_pos_addr.add(o+off)
print(f"mobow (mob-vt): {sum(1 for _ in mob_pos_addr)//5}  wykluczonych pol: {len(mob_pos_addr)}")


def nearest_mod_ptr(addr, back=0x600):
    """szukaj wstecz od addr najblizszego wskaznika modulowego (kandydat vtable); zwroc (offset, ptr)."""
    lo = addr - back
    try:
        raw = pm.read_bytes(lo, back+4)
    except Exception:
        return None
    u = np.frombuffer(raw, dtype="<u4")
    hits = np.nonzero((u >= mlo) & (u < mhi))[0]
    if not len(hits):
        return None
    # najblizszy przed addr
    j = int(hits[-1])
    ptr = int(u[j])
    off = (back) - j*4  # ile bajtow przed addr
    return (off, ptr)


def is_vtable(ptr):
    if not (mlo <= ptr < mhi): return False
    try:
        p = np.frombuffer(pm.read_bytes(ptr, 16), dtype="<u4")
        return int(np.sum((p >= mlo) & (p < mhi))) >= 3
    except Exception:
        return False


cands = []
for (rb, rs), buf in cache.items():
    n = (len(buf)//4)*4
    f = np.frombuffer(buf[:n], dtype="<f4")
    with np.errstate(invalid="ignore"):
        xmask = np.abs(f - cx) < RING_HI
    xi = np.nonzero(xmask)[0]
    for i in xi:
        ax = rb + int(i)*4
        xv = float(f[i])
        # szukaj Y w sasiednich floatach
        for d in (1, 2, 3, -1, -2):
            j = i + d
            if j < 0 or j >= len(f): continue
            yv = float(f[j])
            for sign in (1.0, -1.0):
                yy = sign*yv
                dist = math.hypot(xv-cx, yy-cy)
                if RING_LO <= dist <= RING_HI:
                    ay = rb + j*4
                    if ax in mob_pos_addr or ay in mob_pos_addr:
                        continue
                    cands.append((dist, ax, xv, ay, yv, d, sign))
                    break

# dedup po (ax zaokraglone do 4) i sortuj
seen = set(); uniq = []
for c in sorted(cands, key=lambda e: e[0]):
    key = c[1]
    if key in seen: continue
    seen.add(key); uniq.append(c)

print(f"\nkandydatow par (x,y) w pierscieniu {RING_LO:.0f}-{RING_HI:.0f} (bez pol mobow): {len(uniq)}")
# statystyka delty x->y
dc = Counter((c[5], c[6]) for c in uniq)
print("rozklad (delta_floatow, znak_Y):", dict(dc))

print(f"\n-- 40 najblizszych kandydatow --")
print(f"{'dist':>7} {'addrX':>10} {'x':>9} {'y':>9} {'dlt':>3} {'sgnY':>4}  vtable?")
for dist, ax, xv, ay, yv, d, sign in uniq[:40]:
    mp = nearest_mod_ptr(ax)
    tag = ""
    if mp:
        off, ptr = mp
        vt = is_vtable(ptr)
        rel = f"base+{ptr-base:#x}"
        tag = f"ptr@-{off:#x}={rel}{' VTABLE' if vt else ''}"
        if vt and ptr == base+MOB_VT_OFF: tag += " [MOB]"
        if vt and ptr == base+HERO_VT_OFF: tag += " [HERO]"
    print(f"{dist:7.0f} {ax:#10x} {xv:9.0f} {sign*yv:9.0f} {d:3d} {sign:+.0f}  {tag}")
