"""Znajdz hero po STALEJ vtable (base+0x3cfd10), sprawdz world-pos i niezaleznosc od kamery."""
import ctypes, time
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3CFD10
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


def read_name(pm, o):
    try:
        size = pm.read_uint(o+0x354+16); cap = pm.read_uint(o+0x354+20)
        if size > 64 or cap > 1_000_000: return ""
        raw = pm.read_bytes(o+0x354, min(size,15)) if cap < 16 else pm.read_bytes(pm.read_uint(o+0x354), min(size,48))
    except Exception: return ""
    s=[]
    for b in raw:
        if b==0: break
        s.append(chr(b) if 32<=b<127 else ".")
    return "".join(s)


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base = mod.lpBaseOfDll
hero_vt = base + HERO_VT_OFF

heroes = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == hero_vt)[0]:
        o = rb+int(h)*4
        try:
            x = pm.read_float(o+0x264); y = pm.read_float(o+0x268); z = pm.read_float(o+0x26c)
            name = read_name(pm, o)
        except Exception: continue
        heroes.append((o, x, y, z, name))

print(f"obiektow z hero-vtable: {len(heroes)}")
for o, x, y, z, name in heroes[:10]:
    print(f"  obj={o:#x}  0x264={x:.1f} 0x268={y:.1f} 0x26c={z:.1f}  name={name!r}")

if heroes:
    o = heroes[0][0]
    a = (pm.read_float(o+0x264), pm.read_float(o+0x268))
    time.sleep(1.5)
    b = (pm.read_float(o+0x264), pm.read_float(o+0x268))
    print(f"\nKamera-test hero {o:#x}: 0x264 {a[0]:.1f}->{b[0]:.1f} (d{b[0]-a[0]:.1f})  "
          f"0x268 {a[1]:.1f}->{b[1]:.1f} (d{b[1]-a[1]:.1f})")
