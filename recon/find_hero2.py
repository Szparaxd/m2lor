"""
Znajdz obiekt HERO w plaszczyznie B (X=0x264, Y=0x268).
Szukamy obiektu z PRAWDZIWA vtable (wskazuje na funkcje w module), ktorego 0x264 ~ globalX.
To hero (lub mob dokladnie na pozycji gracza). Jego 0x268 = world-Y gracza w plaszczyznie B.

Uzycie: python recon/find_hero2.py
"""
import ctypes
from ctypes import wintypes
import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C
MOB_VT_OFF = 0x3D5000
TOL = 3.0

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


def main():
    pm = pymem.Pymem(PROCESS)
    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    msize = pymem.process.module_from_name(pm.process_handle, PROCESS).SizeOfImage
    mod_lo, mod_hi = base, base + msize
    mob_vt = base + MOB_VT_OFF
    px = pm.read_float(base + OFF_PLAYER_X)
    py = pm.read_float(base + OFF_PLAYER_Y)
    print(f"GRACZ global X={px:.1f} Y={py:.1f}  mob_vtable={hex(mob_vt)}")

    vt_cache = {}

    def is_vtable(v):
        if v in vt_cache:
            return vt_cache[v]
        ok = False
        if mod_lo <= v < mod_hi:
            try:
                ptrs = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
                ok = int(np.sum((ptrs >= mod_lo) & (ptrs < mod_hi))) >= 3
            except Exception:
                ok = False
        vt_cache[v] = ok
        return ok

    seen = set()
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        f = np.frombuffer(buf[:n], dtype="<f4")
        idx = np.nonzero(np.abs(f - px) < TOL)[0]
        for i in idx:
            obj = rb + int(i) * 4 - 0x264
            if obj in seen:
                continue
            seen.add(obj)
            try:
                vt = pm.read_uint(obj)
                if not is_vtable(vt):
                    continue
                v264 = pm.read_float(obj + 0x264)
                v268 = pm.read_float(obj + 0x268)
                v26c = pm.read_float(obj + 0x26C)
            except Exception:
                continue
            kind = "MOB" if vt == mob_vt else "???"
            print(f"  obj={hex(obj)} vtable={hex(vt)} base+{hex(vt-base)} [{kind}]  "
                  f"0x264={v264:.1f} 0x268={v268:.1f} 0x26c={v26c:.1f}")


if __name__ == "__main__":
    main()
