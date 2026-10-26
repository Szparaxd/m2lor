"""Dla kandydatow par (x,y) blisko gracza: znajdz wsteczny wskaznik modulowy (vtable),
rozwiaz NAZWE KLASY przez MSVC RTTI, i sprawdz STACJONARNOSC (2 odczyty) by odsiac ruchome
moby/czastki. Metin = obiekt STACJONARNY, nie-mob, z sensowna nazwa klasy."""
import ctypes, math, time
from ctypes import wintypes
from collections import defaultdict
import numpy as np
import pymem, pymem.process

PROCESS="GameCoreTop.exe"; HERO_VT_OFF=0x3D6CA0; MOB_VT_OFF=0x3D5000
RING_LO,RING_HI=30.0,1500.0
k32=ctypes.WinDLL("kernel32",use_last_error=True); WRITABLE={0x04,0x08,0x40,0x80}
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_void_p),("AllocationBase",ctypes.c_void_p),("AllocationProtect",wintypes.DWORD),("__a1",wintypes.DWORD),("RegionSize",ctypes.c_size_t),("State",wintypes.DWORD),("Protect",wintypes.DWORD),("Type",wintypes.DWORD),("__a2",wintypes.DWORD)]
k32.VirtualQueryEx.restype=ctypes.c_size_t
k32.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(MBI),ctypes.c_size_t]
def regions(h,readonly=False):
    out,a,mbi=[],0,MBI()
    while a<0x7FFFFFFF:
        if not k32.VirtualQueryEx(h,ctypes.c_void_p(a),ctypes.byref(mbi),ctypes.sizeof(mbi)):break
        b,s,p=(mbi.BaseAddress or 0),(mbi.RegionSize or 0),mbi.Protect
        ok = p in WRITABLE if not readonly else (p in WRITABLE or p==0x02 or p==0x20)
        if mbi.State==0x1000 and not(p&0x100) and not(p&0x01) and ok and 0<s<=512*1024*1024: out.append((b,s))
        n=b+s
        if n<=a:break
        a=n
    return out
pm=pymem.Pymem(PROCESS); mod=pymem.process.module_from_name(pm.process_handle,PROCESS)
base,mlo,mhi=mod.lpBaseOfDll,mod.lpBaseOfDll,mod.lpBaseOfDll+mod.SizeOfImage

def rtti_name(vt):
    try:
        col=pm.read_uint(vt-4)
        if not(mlo<=col<mhi): return None
        td=pm.read_uint(col+0xC)
        if not(mlo<=td<mhi): return None
        raw=pm.read_bytes(td+8,64)
        s=raw.split(b"\x00")[0].decode("ascii","ignore")
        return s if s.startswith(".?A") else None
    except Exception: return None

def is_vtable(ptr):
    if not(mlo<=ptr<mhi):return False
    try:
        p=np.frombuffer(pm.read_bytes(ptr,16),dtype="<u4"); return int(np.sum((p>=mlo)&(p<mhi)))>=3
    except Exception:return False

regs=regions(pm.process_handle)
cache={}
for rb,rs in regs:
    try:cache[(rb,rs)]=pm.read_bytes(rb,rs)
    except Exception:pass
hero=None
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    hh=np.nonzero(u==base+HERO_VT_OFF)[0]
    if len(hh):hero=rb+int(hh[0])*4;break
cx=pm.read_float(hero+0x8);cy=-pm.read_float(hero+0xC)
print(f"gracz=({cx:.0f},{cy:.0f}) base={base:#x}")

mob_pos=set()
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+MOB_VT_OFF)[0]:
        o=rb+int(h)*4
        for off in (0x260,0x264,0x268,0x26c,0x270,0x27c): mob_pos.add(o+off)

# zbierz kandydatow (ax, ay, xv, yv, sign)
cands=[]
for (rb,rs),buf in cache.items():
    n=(len(buf)//4)*4; f=np.frombuffer(buf[:n],dtype="<f4")
    with np.errstate(invalid="ignore"): xi=np.nonzero(np.abs(f-cx)<RING_HI)[0]
    for i in xi:
        ax=rb+int(i)*4; xv=float(f[i])
        for d in (1,-1,2,3):
            j=i+d
            if j<0 or j>=len(f):continue
            for sign in (1.0,-1.0):
                yy=sign*float(f[j]); dist=math.hypot(xv-cx,yy-cy)
                if RING_LO<=dist<=RING_HI:
                    if ax in mob_pos:continue
                    cands.append((dist,ax,xv,rb+j*4,float(f[j]),sign)); break
# dedup
seen=set();uniq=[]
for c in sorted(cands,key=lambda e:e[0]):
    if c[1] in seen:continue
    seen.add(c[1]);uniq.append(c)
print(f"kandydatow: {len(uniq)}")

# dla kazdego: najblizszy wsteczny VTABLE (do -0x400), RTTI
groups=defaultdict(list)
for dist,ax,xv,ay,yv,sign in uniq:
    try: raw=pm.read_bytes(ax-0x400,0x404)
    except Exception: continue
    u=np.frombuffer(raw,dtype="<u4")
    hits=np.nonzero((u>=mlo)&(u<mhi))[0]
    best=None
    for j in hits[::-1]:
        ptr=int(u[j])
        if is_vtable(ptr):
            off=0x400-j*4; best=(off,ptr); break
    if not best: continue
    off,vt=best
    name=rtti_name(vt) or f"vt=base+{vt-base:#x}"
    if vt==base+MOB_VT_OFF: continue  # mob
    groups[(vt,off,name)].append((dist,ax,xv,yv,sign))

print(f"\n=== grupy nie-mobowych struktur z pozycja blisko gracza (vtable, pos_offset, RTTI) ===")
rows=sorted(groups.items(), key=lambda kv: min(e[0] for e in kv[1]))
# stacjonarnosc: zapamietaj najblizszy obiekt kazdej grupy, odczyt #1
snap={}
for (vt,off,name),lst in rows:
    best=min(lst,key=lambda e:e[0]); snap[(vt,off,name)]=(best[1],best[2],best[3])
time.sleep(0.7)
for (vt,off,name),lst in rows[:30]:
    best=min(lst,key=lambda e:e[0])
    ax,x0,y0=snap[(vt,off,name)]
    try: x1=pm.read_float(ax); y1=pm.read_float(ax+ (snap_off:=0)) # placeholder
    except Exception: x1=None
    try:
        x1=pm.read_float(best[1]);
        # y address = ax + (ay-ax) approx; recompute via stored
    except Exception: x1=None
    stat = "STAC" if (x1 is not None and abs(x1-x0)<0.5) else "ruch"
    rel=f"base+{vt-base:#x}"
    print(f"  n={len(lst):4d} dist={best[0]:6.0f} posOff=+{off:#x} vt={rel} [{stat}] {name}")
