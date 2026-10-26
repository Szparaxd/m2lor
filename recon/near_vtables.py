"""Wszystkie obiekty z pozycja (0x264/0x268) blisko gracza, pogrupowane po vtable.
Metin powinien byc obca (nie-mob) vtable obok gracza."""
import ctypes, math
from ctypes import wintypes
from collections import defaultdict
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


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
regs = regions(pm.process_handle)
hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == base+HERO_VT_OFF)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f})")

vtcache = {}
def isvt(v):
    if v in vtcache: return vtcache[v]
    ok = False
    if mlo <= v < mhi:
        try:
            p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
            ok = int(np.sum((p>=mlo)&(p<mhi))) >= 3
        except Exception: ok=False
    vtcache[v]=ok; return ok

by_vt = defaultdict(list)
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    n=(len(buf)//4)*4
    f = np.frombuffer(buf[:n], dtype="<f4")
    # pozycje X blisko gracza
    xmask = np.abs(f - cx) < 1500
    for i in np.nonzero(xmask)[0]:
        if i+1 >= len(f): continue
        y = float(f[i+1])
        if abs(y - cy) < 1500:
            obj = rb + int(i)*4 - 0x264
            try: vt = pm.read_uint(obj)
            except Exception: continue
            if isvt(vt):
                d = math.hypot(float(f[i])-cx, y-cy)
                by_vt[vt].append((obj, float(f[i]), y, d))

print(f"vtable z obiektami blisko gracza (pozycja @0x264/0x268 w promieniu 1500):")
for vt, lst in sorted(by_vt.items(), key=lambda kv: len(kv[1]), reverse=True):
    rel = f"base+{vt-base:#x}"
    tag = " [MOB]" if vt==base+MOB_VT_OFF else (" [HERO]" if vt==base+HERO_VT_OFF else " <== INNA KLASA")
    obj0 = min(lst, key=lambda e: e[3])
    try: vnum = pm.read_uint(obj0[0]+0x4B0)
    except Exception: vnum = -1
    print(f"  {rel}{tag}  obiektow={len(lst)}  najblizszy dist={obj0[3]:.0f} obj={obj0[0]:#x} vnum@4B0={vnum}")
