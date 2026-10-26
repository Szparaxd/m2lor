"""Wypisz wszystkie encje 'Metin' z pozycja, odlegloscia i kierunkiem wzgledem gracza (hero)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
VT_OFF = 0x3D5000
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
vtable = base + VT_OFF
hero_vt = base + HERO_VT_OFF

hero = None
metins = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    if hero is None:
        hh = np.nonzero(u==hero_vt)[0]
        if len(hh): hero = rb+int(hh[0])*4
    for h in np.nonzero(u==vtable)[0]:
        o = rb+int(h)*4
        name = read_name(pm, o)
        if "metin" in name.lower():
            try: x=pm.read_float(o+0x264); y=pm.read_float(o+0x268)
            except Exception: continue
            metins.append((o, x, y, name))

cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz (hero) world=({cx:.0f},{cy:.0f})")
print(f"metinow: {len(metins)}")
for o, x, y, name in sorted(metins, key=lambda m: math.hypot(m[1]-cx, m[2]-cy)):
    dx, dy = x-cx, y-cy
    ew = "E" if dx>0 else "W"; ns = "N" if dy>0 else "S"
    print(f"  {name!r:20} dist={math.hypot(dx,dy):7.0f}  {ns}{ew}  pos=({x:.0f},{y:.0f})  obj={o:#x}")
