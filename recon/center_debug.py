"""Diagnoza srodka: hero vs global vs najblizsze moby (po X)."""
import ctypes
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
VT_OFF = 0x3D5000
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
vtable = base + VT_OFF
gX = pm.read_float(base + 0x493014); gMid = pm.read_float(base + 0x493018); gY = pm.read_float(base + 0x49301C)
print(f"GLOBAL: 0x6a3014(X)={gX:.1f}  0x6a3018(mid)={gMid:.1f}  0x6a301c(Y)={gY:.1f}")

cache = {}
def isvt(v):
    if v in cache: return cache[v]
    ok = False
    if mlo <= v < mhi:
        try:
            p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4"); ok = int(np.sum((p>=mlo)&(p<mhi)))>=3
        except Exception: ok=False
    cache[v]=ok; return ok

hero=None; mobs=[]
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    n=(len(buf)//4)*4; u=np.frombuffer(buf[:n],dtype="<u4"); f=np.frombuffer(buf[:n],dtype="<f4")
    for h in np.nonzero(u==vtable)[0]:
        o=rb+int(h)*4
        try: mobs.append((o, pm.read_float(o+0x264), pm.read_float(o+0x268), pm.read_float(o+0x26C)))
        except Exception: pass
    if hero is None:
        for i in np.nonzero(np.abs(f-gX)<3.0)[0]:
            o=rb+int(i)*4-0x264
            try: vt=pm.read_uint(o)
            except Exception: continue
            if vt!=vtable and isvt(vt):
                hero=(o, pm.read_float(o+0x264), pm.read_float(o+0x268), pm.read_float(o+0x26C), vt); break
print(f"HERO obj={hero[0]:#x} vt=base+{hero[4]-base:#x}  0x264={hero[1]:.1f} 0x268={hero[2]:.1f} 0x26c={hero[3]:.1f}")
# 8 mobow najblizszych w X do hero
mobs.sort(key=lambda m: abs(m[1]-hero[1]))
print("Najblizsze w X moby (obj, 0x264, 0x268, 0x26c, dX):")
for o,x,y,z in mobs[:8]:
    print(f"  {o:#x}  0x264={x:.1f} 0x268={y:.1f} 0x26c={z:.1f}  dX={x-hero[1]:+.1f}")
