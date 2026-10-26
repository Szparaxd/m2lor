"""Sprawdz: (1) co jest wlascicielem actor 0x19165ffc (najblizszy nie-hero actor, pos 29553,35267)
- znajdz wskazniki na niego i back-scan do CInstanceBase; pokaz name/vnum wlasciciela.
(2) Czy cached 'Metin Walki' container 0x232dac7c jest martwy (vtable=0) i czy cos go wskazuje.
(3) Pelna lista actorow w dist<400 z ich wlasc. CInstanceBase (name)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
ACTOR_VT_OFF = 0x3D4FB0
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


def read_name(a, off=0x354):
    try:
        size = pm.read_uint(a+off+16); cap = pm.read_uint(a+off+20)
        if cap >= 16:
            if cap > 1_000_000: return "?"
            raw = pm.read_bytes(pm.read_uint(a+off), min(max(size,1),48))
        else:
            raw = pm.read_bytes(a+off, 15)
    except Exception: return "?"
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
hero_vt = base + HERO_VT_OFF; mob_vt = base + MOB_VT_OFF; actor_vt = base + ACTOR_VT_OFF
regs = regions(pm.process_handle)
cache = {}
for rb, rs in regs:
    try: cache[(rb, rs)] = pm.read_bytes(rb, rs)
    except Exception: pass

hero = None
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f}) base={base:#x}")

# map: instances by vtable 0x3D5000 -> addr,name,pos ; actors by vtable 0x3D4FB0 -> addr,pos
insts = {}
actors = {}
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == mob_vt)[0]:
        o = rb+int(h)*4
        insts[o] = o
    for h in np.nonzero(u == actor_vt)[0]:
        o = rb+int(h)*4
        actors[o] = o

def find_ptrs_to(target):
    res = []
    t = np.uint32(target)
    for (rb, rs), buf in cache.items():
        u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
        for k in np.nonzero(u == t)[0]:
            res.append(rb+int(k)*4)
    return res

def owner_inst(ptr_addr):
    # back-scan do 0x1500 szukajac vtable 0x3D5000
    lo = ptr_addr - 0x1500
    try: blk = pm.read_bytes(lo, 0x1504)
    except Exception: return None
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    for j in range(len(u)-1, -1, -1):
        if int(u[j]) == mob_vt:
            return lo + j*4
    return None

for tgt, label in [(0x19165ffc, "actor@0x19165ffc (dist340, 29553,35267)")]:
    print(f"\n### kto wskazuje na {label} ###")
    ptrs = find_ptrs_to(tgt)
    print(f"  wskaznikow: {len(ptrs)}")
    for p in ptrs[:12]:
        oi = owner_inst(p)
        info = ""
        if oi is not None:
            info = f" OWNER inst={oi:#x} name={read_name(oi)!r} vnum={pm.read_uint(oi+0x4B0)} off={p-oi:#x}"
        print(f"    ptr@{p:#x}{info}")

# (2) cached Metin Walki container
print("\n### cached 'Metin Walki' container 0x232dac7c ###")
obj = 0x232dac7c
vt = pm.read_uint(obj)
print(f"  vtable@+0={vt:#x}{' base+'+hex(vt-base) if mlo<=vt<mhi else ' (nie modul)'}  name@0x354={read_name(obj)!r} vnum@0x4B0={pm.read_uint(obj+0x4B0)} pos@0x264=({pm.read_float(obj+0x264):.1f},{pm.read_float(obj+0x268):.1f})")
pp = find_ptrs_to(obj)
print(f"  wskaznikow na ten obiekt: {len(pp)} -> {[hex(x) for x in pp[:10]]}")

# (3) actors dist<500 -> owner instance name
print("\n### actory dist<600 + wlasc. CInstanceBase ###")
rows=[]
for o in actors:
    try: x=pm.read_float(o+0x4); y=-pm.read_float(o+0x8)
    except Exception: continue
    if not (1000<abs(x)<1e7): continue
    d=math.hypot(x-cx,y-cy)
    if d<600: rows.append((d,o,x,y))
for d,o,x,y in sorted(rows):
    ptrs=find_ptrs_to(o)
    owner=None
    for p in ptrs:
        oi=owner_inst(p)
        if oi is not None: owner=(oi,read_name(oi),pm.read_uint(oi+0x4B0)); break
    print(f"  dist={d:6.1f} actor={o:#x} pos=({x:.0f},{y:.0f}) owner={owner}")
