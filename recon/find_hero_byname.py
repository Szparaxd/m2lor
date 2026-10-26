"""Znajdz obiekt gracza po nazwie (inline w 0x354). Odczytaj world-pos + test kamery."""
import ctypes, sys, time
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
NAME = sys.argv[1] if len(sys.argv) > 1 else "GameCoreTop"
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
needle = NAME.encode("ascii", "ignore")
print(f"Szukam nazwy {NAME!r} -> obiekt gracza (addr nazwy - 0x354)")
cands = []
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    start = 0
    while True:
        i = buf.find(needle, start)
        if i < 0: break
        start = i + 1
        # nazwa konczy sie zerem?
        if i+len(needle) < len(buf) and buf[i+len(needle)] == 0:
            naddr = rb + i
            obj = naddr - 0x354
            try:
                x = pm.read_float(obj+0x264); y = pm.read_float(obj+0x268); z = pm.read_float(obj+0x26c)
                vt = pm.read_uint(obj)
            except Exception:
                continue
            if 1000 < abs(x) < 10_000_000:
                cands.append((obj, vt, x, y, z))

print(f"kandydatow (nazwa inline @0x354 + world-pos): {len(cands)}")
for obj, vt, x, y, z in cands[:10]:
    print(f"  obj={obj:#x} vtable={vt:#x} base+{vt-pm.base_address:#x}  0x264={x:.1f} 0x268={y:.1f} 0x26c={z:.1f}")

if cands:
    o = cands[0][0]
    a = (pm.read_float(o+0x264), pm.read_float(o+0x268))
    time.sleep(1.5)
    b = (pm.read_float(o+0x264), pm.read_float(o+0x268))
    print(f"\nKamera-test {o:#x}: 0x264 {a[0]:.1f}->{b[0]:.1f} (d{b[0]-a[0]:.1f})  "
          f"0x268 {a[1]:.1f}->{b[1]:.1f} (d{b[1]-a[1]:.1f})")
