"""Zbadaj obiekt wokol inline-std::string 'Metin Walki' (size=11,cap=15).
Szeroki skan wstecz na vtable, floaty ~ gracz, vnum 8xxx. Pokaz tez liste mobow dla porownania."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
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
mob_vt = base + MOB_VT_OFF
regs = regions(pm.process_handle)

hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f})")


def is_vtable(v):
    if not (mlo <= v < mhi): return False
    try:
        p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception:
        return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2


# znajdz inline-std::string wystapienia (size=11,cap=15)
targets = []
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    s = 0
    while True:
        i = buf.find(TARGET, s)
        if i < 0: break
        s = i + 1
        a = rb + i
        # size@+16==11, cap@+20==15
        try:
            if i+24 <= len(buf):
                sz = int.from_bytes(buf[i+16:i+20], "little")
                cp = int.from_bytes(buf[i+20:i+24], "little")
                if sz == 11 and cp == 15:
                    targets.append(a)
        except Exception: pass
print(f"inline-string kandydatow: {len(targets)} -> {[hex(t) for t in targets]}")

for naddr in targets:
    print(f"\n=== inline name @ {naddr:#x} ===")
    WIN = 0x900
    lo = naddr - WIN
    blk = pm.read_bytes(lo, WIN + 0x40)
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    # wszystkie vtable kandydaci w oknie, od najblizszego wstecz
    cands = []
    for j in range(len(u)-1, -1, -1):
        if is_vtable(int(u[j])):
            cands.append(lo + j*4)
    print(f"  vtable kandydatow w oknie {WIN:#x} przed napisem: {len(cands)}")
    for vt_addr in cands[:8]:
        name_off = naddr - vt_addr
        vt = pm.read_uint(vt_addr)
        print(f"    obj={vt_addr:#x} vtable={vt:#x}(base+{vt-base:#x}) name_off={name_off:#x}")
    # najblizszy sensowny obiekt: wez pierwszy cand jako obj
    if cands:
        obj = cands[0]
        name_off = naddr - obj
        print(f"  -- analiza obj={obj:#x} (name_off={name_off:#x}) --")
        odata = pm.read_bytes(obj, name_off + 0x40)
        f = np.frombuffer(odata[:(len(odata)//4)*4], dtype="<f4")
        ui = np.frombuffer(odata[:(len(odata)//4)*4], dtype="<u4")
        for k in range(len(f)-1):
            a, b = float(f[k]), float(f[k+1])
            if abs(a-cx) < 800 and abs(b-cy) < 800:
                print(f"     pos(+Y)? off={k*4:#x}: ({a:.1f},{b:.1f}) d={math.hypot(a-cx,b-cy):.1f}")
            if abs(a-cx) < 800 and abs(-b-cy) < 800:
                print(f"     pos(-Y)? off={k*4:#x}: ({a:.1f},{-b:.1f}) d={math.hypot(a-cx,-b-cy):.1f}")
        for k in range(len(ui)):
            if 8000 <= int(ui[k]) <= 8999:
                print(f"     vnum? off={k*4:#x}: {int(ui[k])}")
