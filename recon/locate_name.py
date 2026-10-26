"""Zlokalizuj napis gracza i pokaz kontekst: pobliskie floaty world-range + vtable przed nim."""
import ctypes, sys
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
NAME = (sys.argv[1] if len(sys.argv) > 1 else "GameCoreTop").encode()
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
gX = pm.read_float(base+0x493014)
print(f"global camera-X={gX:.1f}  szukam {NAME!r}")

hits = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    s = 0
    while True:
        i = buf.find(NAME, s)
        if i < 0: break
        s = i+1
        after = buf[i+len(NAME)] if i+len(NAME) < len(buf) else 1
        if after == 0:
            hits.append(rb+i)

print(f"wystapien (z \\0): {len(hits)}")
for naddr in hits[:8]:
    print(f"\n=== nazwa @ {naddr:#x} ===")
    # skan 0x800 przed nazwa: szukaj vtable (modul) i par floatow world-range
    lo = naddr - 0x800
    try: blk = pm.read_bytes(lo, 0x840)
    except Exception:
        print("  (nie moge odczytac bloku)"); continue
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    f = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<f4")
    # vtable = modul-pointer ktorego cel to tez modul-pointery
    for j in range(len(u)):
        v = int(u[j])
        if mlo <= v < mhi:
            try:
                p = np.frombuffer(pm.read_bytes(v, 12), dtype="<u4")
                if int(np.sum((p>=mlo)&(p<mhi)))>=2:
                    off = (lo + j*4) - (naddr - 0x354)  # offset wzgledem hipotetycznego obj (name-0x354)
                    print(f"  vtable {v:#x} base+{v-base:#x} @blok+{j*4:#x} (obj_off~{ (lo+j*4)-(naddr-0x354):#x})")
                    break
            except Exception: pass
    # pary world-range floatow
    found = 0
    for j in range(len(f)-1):
        a, b = float(f[j]), float(f[j+1])
        if 1000 < abs(a) < 100000 and 1000 < abs(b) < 100000:
            addr = lo + j*4
            print(f"  floaty @blok+{j*4:#x} (obj_off {addr-(naddr-0x354):#x}): {a:.1f}, {b:.1f}")
            found += 1
            if found >= 6: break
