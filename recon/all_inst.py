"""Pelna lista WSZYSTKICH obiektow base+0x3D5000 (CInstanceBase): raw name@0x354, vnum@0x4B0, pos@0x264.
Bez filtrow. Oddzielnie policz te z 'etin' w nazwie lub vnum 8xxx. Cel: 100% pewnosc czy near-metin tam jest."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
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


def read_name(a, off=0x354):
    try:
        size = pm.read_uint(a+off+16); cap = pm.read_uint(a+off+20)
        if cap >= 16:
            if cap > 1_000_000: return "?"
            raw = pm.read_bytes(pm.read_uint(a+off), min(max(size,1),48))
        else:
            raw = pm.read_bytes(a+off, 15)
    except Exception: return "?"
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
hero_vt = base + HERO_VT_OFF
mob_vt = base + MOB_VT_OFF
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

objs = []
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == mob_vt)[0]:
        o = rb+int(h)*4
        try: x=pm.read_float(o+0x264); y=pm.read_float(o+0x268); vnum=pm.read_uint(o+0x4B0)
        except Exception: continue
        name=read_name(o)
        d = math.hypot(x-cx,y-cy) if 1000<abs(x)<1e7 else -1
        objs.append((d,o,x,y,vnum,name))
print(f"CInstanceBase(0x3D5000) razem: {len(objs)}")
print("\n-- te z 'etin' w nazwie lub vnum 8000-8999 --")
for d,o,x,y,vnum,name in objs:
    if "etin" in name or 8000<=vnum<=8999:
        print(f"  dist={d:8.0f} obj={o:#x} vnum={vnum} pos=({x:.0f},{y:.0f}) name={name!r}")
print("\n-- WSZYSTKIE wg dist (name,vnum) --")
for d,o,x,y,vnum,name in sorted(objs,key=lambda m:(m[0] if m[0]>=0 else 9e9)):
    print(f"  dist={d:8.0f} obj={o:#x} vnum={vnum:11d} pos=({x:.0f},{y:.0f}) name={name!r}")
