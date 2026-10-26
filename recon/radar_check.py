"""Test: hero-srodek + plaszczyzna B. ASCII scatter encji wzgledem gracza (@)."""
import ctypes
from ctypes import wintypes
import math
import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
OFF_PLAYER_X = 0x493014
VT_OFF = 0x3D5000
OFF_X = 0x264
OFF_Y = 0x268
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
        if n <= a:
            break
        a = n
    return out


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base = mod.lpBaseOfDll
vtable = base + VT_OFF
cx = pm.read_float(base + 0x493014)          # global X
cy = -pm.read_float(base + 0x493018)         # -global mid = world Y w plaszczyznie mobow

mobs = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    n = (len(buf)//4)*4
    u = np.frombuffer(buf[:n], dtype="<u4")
    for h in np.nonzero(u == vtable)[0]:
        o = rb+int(h)*4
        try: mobs.append((pm.read_float(o+OFF_X), pm.read_float(o+OFF_Y)))
        except Exception: pass

print(f"center=({cx:.0f},{cy:.0f}) mobs={len(mobs)}")
near = [(x-cx, y-cy) for x, y in mobs if abs(x-cx) < 12000 and abs(y-cy) < 12000]
print(f"w promieniu 12000: {len(near)}")
W, Hh = 70, 30
grid = [[" "]*W for _ in range(Hh)]
R = 12000.0
for dx, dy in near:
    gx = int((dx/R/2+0.5)*(W-1)); gy = int((-dy/R/2+0.5)*(Hh-1))
    if 0 <= gx < W and 0 <= gy < Hh: grid[gy][gx] = "#"
grid[Hh//2][W//2] = "@"
for row in grid: print("".join(row))
