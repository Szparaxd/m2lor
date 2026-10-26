"""Znajdz obiekty z nazwa 'Metin Walki' inline @0x354; pokaz vtable, pozycje, vnum.
Zywy metin: poprawna vtable + pozycja blisko gracza."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
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
regs = regions(pm.process_handle)
hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == base+HERO_VT_OFF)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f})")

needles = [b"Metin Walki", b"Metin Cierpienia"]
for needle in needles:
    print(f"\n=== {needle.decode()} ===")
    seen = set()
    for rb, rs in regs:
        try: buf = pm.read_bytes(rb, rs)
        except Exception: continue
        s = 0
        while True:
            i = buf.find(needle, s)
            if i < 0: break
            s = i+1
            naddr = rb + i
            obj = naddr - 0x354
            if obj in seen: continue
            seen.add(obj)
            try:
                vt = pm.read_uint(obj)
                x = pm.read_float(obj+0x264); y = pm.read_float(obj+0x268)
                vnum = pm.read_uint(obj+0x4B0)
            except Exception:
                continue
            rel = f"base+{vt-base:#x}" if mlo <= vt < mhi else f"{vt:#x}(NIE-modul)"
            d = math.hypot(x-cx, y-cy) if 1000 < abs(x) < 1e7 else -1
            tag = ""
            if vt == base+MOB_VT_OFF: tag = " [MOB-VT]"
            elif mlo <= vt < mhi: tag = " [INNA-MODUL-VT!]"
            print(f"  obj={obj:#x} vtable={rel}{tag}  pos=({x:.0f},{y:.0f}) dist={d:.0f} vnum={vnum}")
