"""Enumeruj obiekty klasy 'actor/graphic' vtable base+0x3d4fb0 (pos X@+0x4, -Y@+0x8, Z@+0xC)
oraz CInstanceBase base+0x3d5000. Wypisz wszystkie wg dist do gracza. Metin stacjonarny obok gracza
powinien byc wsrod actorow blisko, a nie w CInstanceBase. Dodatkowo policz histogram vtable
obiektow majacych pare floatow ~ pozycji gracza (<300)."""
import ctypes, math
from ctypes import wintypes
from collections import Counter
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
ACTOR_VT_OFF = 0x3D4FB0
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
hero_vt = base + HERO_VT_OFF
actor_vt = base + ACTOR_VT_OFF
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

# actor objects
print("\n== actor vtable base+0x3d4fb0 (pos X@+4, -Y@+8) wg dist ==")
acts = []
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == actor_vt)[0]:
        o = rb + int(h)*4
        try:
            x = pm.read_float(o+0x4); y = -pm.read_float(o+0x8); z = pm.read_float(o+0xC)
        except Exception: continue
        d = math.hypot(x-cx, y-cy) if 1000 < abs(x) < 1e7 else -1
        acts.append((d, o, x, y, z))
for d, o, x, y, z in sorted(acts, key=lambda m:(m[0] if m[0]>=0 else 9e9))[:25]:
    print(f"  dist={d:8.1f} obj={o:#x} pos=({x:.0f},{y:.0f},{z:.0f})")
print(f"  (actorow razem: {len(acts)})")

# histogram vtable obiektow z para floatow ~gracz (<300), vtable = DWORD tuz przed (szukamy w [-0x30,0] i w [-0x8])
print("\n== histogram vtable obiektow z pos (X@+4,-Y@+8) ~ gracz <400 ==")
hist = Counter()
samples = {}
for (rb, rs), buf in cache.items():
    n=(len(buf)//4)*4
    f=np.frombuffer(buf[:n],dtype="<f4"); u=np.frombuffer(buf[:n],dtype="<u4")
    ix=np.nonzero(np.abs(f-cx)<400)[0]
    for k in ix:
        if k+1<len(f) and abs(-float(f[k+1])-cy)<400:
            # zaloz pos@+0x4 -> vtable @ (addr-4)
            vt_addr = rb+(int(k)-1)*4
            if vt_addr>=rb:
                v=int(u[k-1]) if k>=1 else 0
                if mlo<=v<mhi:
                    hist[v-base]+=1
                    samples.setdefault(v-base,(rb+int(k)*4,float(f[k]),-float(f[k+1])))
for off,cnt in hist.most_common(15):
    sa=samples[off]
    print(f"  vtable base+{off:#x}: {cnt}x  sample pos=({sa[1]:.0f},{sa[2]:.0f}) @{sa[0]:#x}")
