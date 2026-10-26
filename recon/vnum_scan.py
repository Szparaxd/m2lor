"""Szukaj metinow po STRUKTURZE klasy mobow, ale niezaleznie od vtable.
Skanuj kazdy DWORD==8000..8999 (vnum). Hipoteza obj = addr-0x4B0.
Waliduj: vtable@obj to modul-ptr celujacy w modul-ptry; pos@0x264/0x268 ~ sensowna; name@0x354.
Pokaz dist do gracza i vtable (base+off)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
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
        if n <= a: break
        a = n
    return out


def read_name(pm, a, off=0x354):
    try:
        size = pm.read_uint(a+off+16); cap = pm.read_uint(a+off+20)
        if size > 64 or cap > 1_000_000: return ""
        raw = pm.read_bytes(a+off, min(size,15)) if cap < 16 else pm.read_bytes(pm.read_uint(a+off), min(size,48))
    except Exception: return ""
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
hero_vt = base + HERO_VT_OFF
regs = regions(pm.process_handle)
cache = {}
for rb, rs in regs:
    try: cache[(rb, rs)] = pm.read_bytes(rb, rs)
    except Exception: pass

hero = None
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f}) base={base:#x}")


def vt_ok(v):
    if not (mlo <= v < mhi): return False
    try: p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception: return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2


print("\n== obiekty z vnum 8000..8999 @ (addr-0x4B0=obj), walidne vtable ==")
rows = []
for (rb, rs), buf in cache.items():
    n = (len(buf)//4)*4
    u = np.frombuffer(buf[:n], dtype="<u4")
    idx = np.nonzero((u >= 8000) & (u <= 8999))[0]
    for k in idx:
        vnum_addr = rb + int(k)*4
        obj = vnum_addr - 0x4B0
        try: vt = pm.read_uint(obj)
        except Exception: continue
        if not vt_ok(vt): continue
        try:
            x = pm.read_float(obj+0x264); y = pm.read_float(obj+0x268)
        except Exception: continue
        name = read_name(pm, obj)
        d = math.hypot(x-cx, y-cy) if 1000 < abs(x) < 1e7 else -1
        rows.append((d, obj, vt, int(u[k]), x, y, name))
rows.sort(key=lambda r: (r[0] if r[0]>=0 else 9e9))
for d, obj, vt, vnum, x, y, name in rows:
    print(f"  dist={d:8.0f} obj={obj:#x} vtable=base+{vt-base:#x} vnum={vnum} pos=({x:.0f},{y:.0f}) name={name!r}")

print(f"\nrazem: {len(rows)}")
