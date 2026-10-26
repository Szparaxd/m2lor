"""Potwierdz tozsamosc gracza (hero@0x3D6CA0): nazwa@0x7F8, pos. I policz zywe metiny (vtable 0x3D5000 + vnum 8xxx)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0; MOB_VT_OFF = 0x3D5000
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
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)): break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WRITABLE and 0 < s <= 512*1024*1024:
            out.append((b, s))
        n = b + s
        if n <= a: break
        a = n
    return out
def rstr(a):
    try:
        size=pm.read_uint(a+16); cap=pm.read_uint(a+20)
        raw = pm.read_bytes(pm.read_uint(a), min(size,48)) if cap>=16 else pm.read_bytes(a, 15)
    except Exception: return "?"
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])
pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base = mod.lpBaseOfDll; hero_vt=base+HERO_VT_OFF; mob_vt=base+MOB_VT_OFF
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u==hero_vt)[0]
    if len(hh):
        hero=rb+int(hh[0])*4
        print(f"HERO @ {hero:#x} name@0x7F8={rstr(hero+0x7F8)!r} pos=({pm.read_float(hero+8):.1f},{-pm.read_float(hero+0xC):.1f})")
        break
