"""Czysty test STACJONARNOSCI + RTTI. Zbierz nie-mobowe pary (x,y) blisko gracza, odczytaj
2x (0.8s), zostaw tylko NIERUCHOME (metin stoi, moby/czastki sie ruszaja). Dla kazdej
nieruchomej: wsteczny vtable -> RTTI nazwa klasy + offset pozycji. To powinno wskazac metin."""
import ctypes, math, time
from ctypes import wintypes
from collections import defaultdict
import numpy as np
import pymem, pymem.process

PROCESS="GameCoreTop.exe"; HERO_VT_OFF=0x3D6CA0; MOB_VT_OFF=0x3D5000
RING_LO,RING_HI=25.0,1600.0
k32=ctypes.WinDLL("kernel32",use_last_error=True); WRITABLE={0x04,0x08,0x40,0x80}
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_void_p),("AllocationBase",ctypes.c_void_p),("AllocationProtect",wintypes.DWORD),("__a1",wintypes.DWORD),("RegionSize",ctypes.c_size_t),("State",wintypes.DWORD),("Protect",wintypes.DWORD),("Type",wintypes.DWORD),("__a2",wintypes.DWORD)]
k32.VirtualQueryEx.restype=ctypes.c_size_t
k32.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(MBI),ctypes.c_size_t]
def regions(h):
    out,a,mbi=[],0,MBI()
    while a<0x7FFFFFFF:
        if not k32.VirtualQueryEx(h,ctypes.c_void_p(a),ctypes.byref(mbi),ctypes.sizeof(mbi)):break
        b,s,p=(mbi.BaseAddress or 0),(mbi.RegionSize or 0),mbi.Protect
        if mbi.State==0x1000 and not(p&0x100) and not(p&0x01) and p in WRITABLE and 0<s<=512*1024*1024: out.append((b,s))
        n=b+s
        if n<=a:break
        a=n
    return out
pm=pymem.Pymem(PROCESS); mod=pymem.process.module_from_name(pm.process_handle,PROCESS)
base,mlo,mhi=mod.lpBaseOfDll,mod.lpBaseOfDll,mod.lpBaseOfDll+mod.SizeOfImage
def rtti_name(vt):
    try:
        col=pm.read_uint(vt-4)
        if not(mlo<=col<mhi):return None
        td=pm.read_uint(col+0xC)
        if not(mlo<=td<mhi):return None
        return pm.read_bytes(td+8,80).split(b"\x00")[0].decode("ascii","ignore")
    except Exception:return None
def is_vtable(ptr):
    if not(mlo<=ptr<mhi):return False
    try:p=np.frombuffer(pm.read_bytes(ptr,16),dtype="<u4");return int(np.sum((p>=mlo)&(p<mhi)))>=3
    except Exception:return False
regs=regions(pm.process_handle); cache={}
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
        for off in range(0x25c,0x290,4): mob_pos.add(o+off)
cands=[]
for (rb,rs),buf in cache.items():
    n=(len(buf)//4)*4;f=np.frombuffer(buf[:n],dtype="<f4")
    with np.errstate(invalid="ignore"):xi=np.nonzero(np.abs(f-cx)<RING_HI)[0]
    for i in xi:
        ax=rb+int(i)*4;xv=float(f[i])
        for d in (1,-1,2,3):
            j=i+d
            if j<0 or j>=len(f):continue
            for sign in (1.0,-1.0):
                yy=sign*float(f[j]);dist=math.hypot(xv-cx,yy-cy)
                if RING_LO<=dist<=RING_HI and ax not in mob_pos:
                    cands.append((dist,ax,rb+j*4,xv,float(f[j]),sign));break
            else:continue
            break
seen=set();uniq=[]
for c in sorted(cands,key=lambda e:e[0]):
    if c[1] in seen:continue
    seen.add(c[1]);uniq.append(c)
print(f"kandydatow: {len(uniq)}  -- test stacjonarnosci 0.8s --")
time.sleep(0.8)
stat=[]
for dist,ax,ay,x0,y0,sign in uniq:
    try:x1=pm.read_float(ax);y1=pm.read_float(ay)
    except Exception:continue
    if abs(x1-x0)<0.3 and abs(y1-y0)<0.3:
        stat.append((dist,ax,ay,x0,y0,sign))
print(f"NIERUCHOMYCH: {len(stat)}")
# RTTI klasyfikacja
groups=defaultdict(list)
for dist,ax,ay,x0,y0,sign in stat:
    try:raw=pm.read_bytes(ax-0x400,0x404)
    except Exception:continue
    u=np.frombuffer(raw,dtype="<u4");hits=np.nonzero((u>=mlo)&(u<mhi))[0]
    best=None
    for j in hits[::-1]:
        ptr=int(u[j])
        if is_vtable(ptr):best=(0x400-j*4,ptr);break
    key=best if best else (None,0)
    off,vt=key
    nm=rtti_name(vt) if vt else "(brak vtable w -0x400)"
    groups[(vt,off,nm)].append((dist,ax,x0,sign*y0))
print("\n=== NIERUCHOME nie-mobowe struktury (vtable, posOff, RTTI) ===")
for (vt,off,nm),lst in sorted(groups.items(),key=lambda kv:min(e[0] for e in kv[1])):
    b=min(lst,key=lambda e:e[0])
    rel=f"base+{vt-base:#x}" if vt else "-"
    print(f"  n={len(lst):3d} dist={b[0]:6.0f} posOff=+{off:#x} pos=({b[2]:.0f},{b[3]:.0f}) {rel} {nm}")
