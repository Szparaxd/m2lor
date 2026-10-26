"""M4 - klasyfikacja encji po NAZWIE (offset 0x354). Lista encji: dist, nazwa, race@74, f5c."""
import ctypes
from ctypes import wintypes
import math
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
VT_OFF = 0x3D5000
OFF_X, OFF_Y = 0x264, 0x268
OFF_NAME = 0x354
OFF_RACE = 0x74
OFF_F5C = 0x5C
OFF_VID = 0x2B0
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


def _clean(b):
    s = []
    for ch in b:
        if ch == 0:
            break
        s.append(chr(ch) if 32 <= ch < 127 else ".")
    return "".join(s)


def read_name(pm, o):
    """MSVC std::string (32-bit): buf[16], size@+16, cap@+20. cap<16 -> inline, else ptr w buf[0:4]."""
    try:
        size = pm.read_uint(o + OFF_NAME + 16)
        cap = pm.read_uint(o + OFF_NAME + 20)
    except Exception:
        return ""
    if size > 64 or cap > 1_000_000:
        return ""
    try:
        if cap < 16:
            return _clean(pm.read_bytes(o + OFF_NAME, min(size, 15)))
        ptr = pm.read_uint(o + OFF_NAME)
        if 0x100000 < ptr < 0x7FFF0000:
            return _clean(pm.read_bytes(ptr, min(size, 48)))
    except Exception:
        return ""
    return ""


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base = mod.lpBaseOfDll
vtable = base + VT_OFF
cx = pm.read_float(base + 0x493014); cy = -pm.read_float(base + 0x493018)

ents = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == vtable)[0]:
        o = rb+int(h)*4
        try:
            x = pm.read_float(o+OFF_X); y = pm.read_float(o+OFF_Y)
            name = read_name(pm, o)
            race = pm.read_uint(o+OFF_RACE); f5c = pm.read_uint(o+OFF_F5C); vid = pm.read_uint(o+OFF_VID)
        except Exception: continue
        if math.isfinite(x):
            ents.append((math.hypot(x-cx, y-cy), name, race, f5c, vid, o))
ents.sort()
print(f"center=({cx:.0f},{cy:.0f}) encji={len(ents)}")
from collections import Counter
names = Counter(e[1] for e in ents)
print("Zliczenie nazw:", dict(names))
print("\n20 najblizszych:")
for d, name, race, f5c, vid, o in ents[:20]:
    print(f"  dist={d:7.0f} name={name!r:22} race@74={race:4} f5c={f5c} vid={vid}")
