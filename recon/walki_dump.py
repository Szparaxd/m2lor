"""Surowy dump wokol inline 'Metin Walki'. Wypisz wszystkie modul-ptry i floaty world-range
w oknie [-0x1000,+0x100]. Zidentyfikuj vtable i pozycje. Rowniez sprawdz czy to klasa mobow (name_off 0x354)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
TARGET = b"Metin Walki\x00"
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
hero_vt = base + HERO_VT_OFF
regs = regions(pm.process_handle)

hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f})  base={base:#x}")

# znajdz inline string
naddr = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    i = buf.find(TARGET)
    if i >= 0:
        a = rb + i
        sz = int.from_bytes(buf[i+16:i+20], "little"); cp = int.from_bytes(buf[i+20:i+24], "little")
        if sz == 11 and cp == 15:
            naddr = a; break
print(f"inline name @ {naddr:#x}")

lo = naddr - 0x1000
blk = pm.read_bytes(lo, 0x1100)
u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
f = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<f4")

print("\n-- modul-ptry (potencjalne vtable) w oknie [-0x1000,+0x100] --")
for j in range(len(u)):
    v = int(u[j])
    if mlo <= v < mhi:
        addr = lo + j*4
        off_from_name = addr - naddr
        # czy cel wyglada jak vtable?
        try:
            p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
            modcnt = int(np.sum((p >= mlo) & (p < mhi)))
        except Exception:
            modcnt = -1
        print(f"  @{addr:#x} (name{off_from_name:+#x}) -> {v:#x} base+{v-base:#x}  targetModPtrs={modcnt}")

print("\n-- floaty world-range (abs 1000..100000) w oknie --")
for j in range(len(f)):
    a = float(f[j])
    if 1000 < abs(a) < 100000:
        addr = lo + j*4
        near = ""
        if abs(a-cx) < 1500: near += " ~=X"
        if abs(a-cy) < 1500: near += " ~=Y"
        if abs(-a-cy) < 1500: near += " ~=-Y"
        print(f"  @{addr:#x} (name{addr-naddr:+#x}): {a:.1f}{near}")

# sprawdz hipoteze klasy mobow: obj = naddr - 0x354
obj_mob = naddr - 0x354
vt_mob = pm.read_uint(obj_mob)
print(f"\n-- hipoteza mob-class: obj=naddr-0x354={obj_mob:#x} vtable@+0={vt_mob:#x} base+{vt_mob-base:#x if mlo<=vt_mob<mhi else 0}")
try:
    print(f"   pos@0x264/0x268: {pm.read_float(obj_mob+0x264):.1f},{pm.read_float(obj_mob+0x268):.1f}  vnum@0x4B0={pm.read_uint(obj_mob+0x4B0)}")
except Exception as e:
    print("   blad", e)
