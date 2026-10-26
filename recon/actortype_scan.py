"""Hipoteza (ze zrodel GameLib/ActorInstance.h): m_eActorType (DWORD) tuz PRZED m_eRace(vnum@0x4B0).
Enum: ENEMY=0,NPC=1,STONE=2,WARP=3,DOOR=4,BUILDING=5,PC=6,POLY=7,HORSE=8,GOTO=9,OBJECT=10.
=> m_eActorType powinien byc @0x4AC. METIN = wartosc 2 (TYPE_STONE).
Dla wszystkich 177 CInstanceBase: dump okna 0x4A0..0x4BC + vnum, sort po dist. Flaguj STONE."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
TYPES = {0:"ENEMY",1:"NPC",2:"STONE",3:"WARP",4:"DOOR",5:"BUILDING",6:"PC",7:"POLY",8:"HORSE",9:"GOTO",10:"OBJECT"}
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
        if mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WRITABLE and 0 < s <= 512*1024*1024:
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
base = mod.lpBaseOfDll
regs = regions(pm.process_handle)
cache = {}
for rb, rs in regs:
    try: cache[(rb, rs)] = pm.read_bytes(rb, rs)
    except Exception: pass

hero = None
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == base+HERO_VT_OFF)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f}) base={base:#x}")

objs = []
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == base+MOB_VT_OFF)[0]:
        o = rb+int(h)*4
        try:
            x=pm.read_float(o+0x264); y=pm.read_float(o+0x268); vnum=pm.read_uint(o+0x4B0)
            at=pm.read_uint(o+0x4AC)
            win=pm.read_bytes(o+0x4A0, 0x20)
        except Exception: continue
        d = math.hypot(x-cx,y-cy) if 1000<abs(x)<1e7 else -1
        objs.append((d,o,x,y,vnum,at,read_name(o),win))

stones=[o for o in objs if o[5]==2]
print(f"\nrazem={len(objs)}  TYPE_STONE(@0x4AC==2): {len(stones)}")
print("\n=== TYPE_STONE (metiny) wg dist ===")
for d,o,x,y,vnum,at,name,win in sorted(stones,key=lambda m:(m[0] if m[0]>=0 else 9e9)):
    print(f"  dist={d:8.0f} obj={o:#x} type={TYPES.get(at,at)} vnum={vnum} pos=({x:.0f},{y:.0f}) name={name!r}")

print("\n=== rozklad wartosci @0x4AC po wszystkich ===")
from collections import Counter
cc=Counter(o[5] for o in objs)
for k,v in sorted(cc.items()): print(f"  0x4AC={k} ({TYPES.get(k,'?')}): {v}")

print("\n=== 12 najblizszych obiektow: okno 0x4A0..0x4C0 (DWORDY) ===")
for d,o,x,y,vnum,at,name,win in sorted(objs,key=lambda m:(m[0] if m[0]>=0 else 9e9))[:12]:
    dw=np.frombuffer(win,dtype="<u4")
    s=" ".join(f"{v:>5}" if v<100000 else f"{v:#010x}" for v in dw)
    print(f"  d={d:7.0f} {o:#x} type@4AC={TYPES.get(at,at)} vnum={vnum} name={name!r}")
    print(f"      0x4A0: {s}")
