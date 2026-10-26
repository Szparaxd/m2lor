"""Szczegoly 4 efektow blisko gracza: wszystkie wskazniki (modul/heap) -> string (0 i 1 deref),
pelna sciezka .mse. Test stacjonarnosci pozycji @0x280 (1s). Identyfikacja metina."""
import ctypes, math, time
from ctypes import wintypes
import numpy as np
import pymem, pymem.process
PROCESS="GameCoreTop.exe"; HERO_VT_OFF=0x3D6CA0; EFF_VT=0x3ce250
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
regs=regions(pm.process_handle); cache={}
for rb,rs in regs:
    try:cache[(rb,rs)]=pm.read_bytes(rb,rs)
    except Exception:pass
def inheap(v):
    for rb,rs in regs:
        if rb<=v<rb+rs:return True
    return False
def readstr(v,n=96):
    try:
        raw=pm.read_bytes(v,n);t=raw.split(b"\x00")[0]
        s=t.decode("ascii","ignore")
        return s if len(s)>=3 and sum(c.isprintable() for c in s)==len(s) else ""
    except Exception:return ""
hero=None
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    hh=np.nonzero(u==base+HERO_VT_OFF)[0]
    if len(hh):hero=rb+int(hh[0])*4;break
cx=pm.read_float(hero+0x8);cy=-pm.read_float(hero+0xC)
print(f"gracz=({cx:.0f},{cy:.0f})")
effs=[]
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+EFF_VT)[0]: effs.append(rb+int(h)*4)
near=[]
for o in effs:
    try:x=pm.read_float(o+0x280);y=-pm.read_float(o+0x284)
    except Exception:continue
    # y moze byc +/-; sprawdz tez bez negacji
    for yy in (y,-y):
        if abs(x-cx)<1500 and abs(yy-cy)<1500:
            near.append((math.hypot(x-cx,yy-cy),o,x,yy));break
near.sort()
print(f"efektow blisko (+0x280): {len(near)}")
for d,o,x,y in near:
    print(f"\n### efekt obj={o:#x} dist={d:.0f} pos=({x:.0f},{y:.0f})")
    # stacjonarnosc
    x0=pm.read_float(o+0x280); z0=pm.read_float(o+0x288)
    time.sleep(1.0)
    x1=pm.read_float(o+0x280)
    print(f"    stacjonarnosc @0x280: x {x0:.2f}->{x1:.2f}  {'STABILNA' if abs(x1-x0)<0.3 else 'ZMIENNA'}")
    # wszystkie stringi z wskaznikow
    try:raw=pm.read_bytes(o,0x300)
    except Exception:continue
    u=np.frombuffer(raw,dtype="<u4")
    found=[]
    for i in range(len(u)):
        v=int(u[i])
        if not(mlo<=v<mhi or inheap(v)):continue
        s=readstr(v)
        if s and ("/" in s or "." in s or s.isalpha()):
            found.append((i*4,0,s))
        else:
            try:
                p=pm.read_uint(v)
                if mlo<=p<mhi or inheap(p):
                    s2=readstr(p)
                    if s2 and "/" in s2: found.append((i*4,1,s2))
            except Exception:pass
    for off,dr,s in found[:25]:
        print(f"    +{off:#05x} (deref{dr}) {s!r}")
