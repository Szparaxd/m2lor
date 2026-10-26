"""Znajdz ZYWY obiekt metina vnum=8002 pod DOWOLNA vtable. Szuka DWORD==8002 jako obj+0x4B0,
sprawdza vtable + pozycje blisko gracza. Probuje tez rozne offsety pozycji."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
VNUM_OFF = 0x4B0
TARGET_VNUM = 8002
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

# gracz
hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == base+HERO_VT_OFF)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f}) base={base:#x}")


def isvt(v):
    if not (mlo <= v < mhi): return False
    try:
        p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
        return int(np.sum((p>=mlo)&(p<mhi))) >= 3
    except Exception: return False


def worldpair_near(o, lim=4000):
    """szukaj w obiekcie [o, o+0x600] pary floatow (X,Y) blisko gracza; zwroc (offX, dist) lub None"""
    try: raw = pm.read_bytes(o, 0x600)
    except Exception: return None
    f = np.frombuffer(raw, dtype="<f4")
    best = None
    for i in range(len(f)-3):
        x = float(f[i])
        if abs(x-cx) < lim:
            for dj in (1,2,3):
                if i+dj < len(f):
                    y = float(f[i+dj])
                    if abs(abs(y)-abs(cy)) < lim:
                        d = math.hypot(x-cx, (abs(y) if y<0 else y)-cy)
                        if best is None or d < best[1]:
                            best = (i*4, d, x, y, dj*4)
    return best


print(f"Szukam vnum=={TARGET_VNUM} jako obj+{VNUM_OFF:#x} (dowolna vtable)...")
hits = []
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for i in np.nonzero(u == TARGET_VNUM)[0]:
        a = rb + int(i)*4
        obj = a - VNUM_OFF
        try: vt = pm.read_uint(obj)
        except Exception: continue
        if isvt(vt):
            wp = worldpair_near(obj)
            hits.append((obj, vt, wp))

print(f"kandydatow (vnum 8002 + valid vtable): {len(hits)}")
for obj, vt, wp in hits[:20]:
    info = f"posOff={wp[0]:#x} dist={wp[1]:.0f} val=({wp[2]:.0f},{wp[3]:.0f}) yrel={wp[4]:#x}" if wp else "brak-pozycji-blisko"
    print(f"  obj={obj:#x} vtable=base+{vt-base:#x}  {info}")
