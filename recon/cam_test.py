"""Test zaleznosci od kamery: 2 odczyty w odstepie (kamera auto-obraca sie).
Porownuje global, hero i mob. Co sie zmienia = zalezne od kamery."""
import ctypes, time
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

cache = {}
def isvt(v):
    if v in cache: return cache[v]
    ok = False
    if mlo <= v < mhi:
        try:
            p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4"); ok = int(np.sum((p>=mlo)&(p<mhi)))>=3
        except Exception: ok=False
    cache[v]=ok; return ok

gX = pm.read_float(base+0x493014)
# znajdz hero i pierwszy mob
hero=None; mob=None
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    n=(len(buf)//4)*4; u=np.frombuffer(buf[:n],dtype="<u4"); f=np.frombuffer(buf[:n],dtype="<f4")
    if mob is None:
        hits=np.nonzero(u==vtable)[0]
        if len(hits): mob = rb+int(hits[0])*4
    if hero is None:
        for i in np.nonzero(np.abs(f-gX)<3.0)[0]:
            o=rb+int(i)*4-0x264
            try: vt=pm.read_uint(o)
            except Exception: continue
            if vt!=vtable and isvt(vt): hero=o; break
    if mob is not None and hero is not None: break

def rd():
    return {
        "global 0x6a3014": pm.read_float(base+0x493014),
        "global 0x6a3018": pm.read_float(base+0x493018),
        "global 0x6a301c": pm.read_float(base+0x49301c),
        "hero 0x264": pm.read_float(hero+0x264) if hero else 0,
        "hero 0x268": pm.read_float(hero+0x268) if hero else 0,
        "hero 0x26c": pm.read_float(hero+0x26c) if hero else 0,
        "mob 0x264": pm.read_float(mob+0x264) if mob else 0,
        "mob 0x268": pm.read_float(mob+0x268) if mob else 0,
    }

print(f"hero={hex(hero) if hero else None}  mob={hex(mob) if mob else None}  (kamera ma sie obracac)")
a = rd(); time.sleep(1.5); b = rd()
print(f"{'pole':22} {'odczyt1':>12} {'odczyt2':>12} {'zmiana':>10}")
for k in a:
    print(f"{k:22} {a[k]:12.1f} {b[k]:12.1f} {b[k]-a[k]:10.1f}  {'<-- ZMIENIA' if abs(b[k]-a[k])>2 else ''}")
