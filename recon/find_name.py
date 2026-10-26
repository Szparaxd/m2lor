"""
M4 - znajdz pole NAZWY w obiekcie encji. Dla kilku najblizszych encji skanuje obiekt
w poszukiwaniu ciagow ASCII (inline) oraz wskaznikow na ciagi (string ptr).

Postac stoi przy metinie -> najblizsza encja to metin. Zobaczymy nazwy.
Uzycie: python recon/find_name.py
"""
import ctypes
from ctypes import wintypes
import math
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
VT_OFF = 0x3D5000
OFF_X, OFF_Y = 0x264, 0x268
SCAN = 0x600
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


def ascii_runs(data, minlen=3):
    out, cur, start = [], [], 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not cur: start = i
            cur.append(chr(b))
        else:
            if len(cur) >= minlen:
                out.append((start, "".join(cur)))
            cur = []
    if len(cur) >= minlen:
        out.append((start, "".join(cur)))
    return out


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
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
        except Exception: continue
        if math.isfinite(x):
            ents.append((math.hypot(x-cx, y-cy), o))
ents.sort()
print(f"center=({cx:.0f},{cy:.0f}) encji={len(ents)}. 6 najblizszych:")
for d, o in ents[:6]:
    print(f"\n--- obj={o:#x} dist={d:.0f} ---")
    try: raw = pm.read_bytes(o, SCAN)
    except Exception: continue
    # inline ASCII
    for off, s in ascii_runs(raw, 3):
        print(f"    inline off={off:#x}: {s!r}")
    # wskazniki na ciagi (heap/module), czytaj do 32 bajtow
    dw = np.frombuffer(raw, dtype="<u4")
    for c in range(SCAN//4):
        p = int(dw[c])
        if 0x100000 < p < 0x7FFF0000:
            try:
                sb = pm.read_bytes(p, 24)
            except Exception:
                continue
            r = ascii_runs(sb, 3)
            if r and r[0][0] == 0:
                print(f"    ptr@{c*4:#x} -> {r[0][1]!r}")
