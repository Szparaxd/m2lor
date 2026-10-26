"""Znajdz offset m_eActorType: STONE=2, ENEMY=0, NPC=1. Metin Cierpienia (race 8001) to STONE.
Dla kazdego offsetu 0x40..0x560 (co 4): wartosc u Cierpienia vs rozklad u wszystkich mobow.
Szukamy offsetu gdzie Cierpienia==2 i mocno koreluje z typem (moby 0/1)."""
import ctypes, math
from ctypes import wintypes
from collections import Counter
import numpy as np
import pymem, pymem.process

PROCESS="GameCoreTop.exe"; HERO_VT_OFF=0x3D6CA0; MOB_VT_OFF=0x3D5000
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
def read_name(a,off=0x354):
    try:
        size=pm.read_uint(a+off+16);cap=pm.read_uint(a+off+20)
        raw=pm.read_bytes(pm.read_uint(a+off),min(max(size,1),48)) if cap>=16 and cap<1_000_000 else pm.read_bytes(a+off,15)
    except Exception:return "?"
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])
pm=pymem.Pymem(PROCESS); mod=pymem.process.module_from_name(pm.process_handle,PROCESS); base=mod.lpBaseOfDll
regs=regions(pm.process_handle); cache={}
for rb,rs in regs:
    try:cache[(rb,rs)]=pm.read_bytes(rb,rs)
    except Exception:pass
objs=[]
cier=None
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+MOB_VT_OFF)[0]:
        o=rb+int(h)*4
        try: win=pm.read_bytes(o,0x580)
        except Exception: continue
        objs.append((o,read_name(o),win))
        if read_name(o)=="Metin Cierpienia": cier=(o,win)
print(f"objs={len(objs)} cier={'OK' if cier else 'MISSING'}")
if not cier:
    import sys; sys.exit()
cwin=np.frombuffer(cier[1],dtype="<u4")
# wartosci u wszystkich obiektow per offset
print("\noffset  cier  rozklad_wartosci(wszystkie, tylko <=12)")
for off in range(0x40,0x580,4):
    cval=int(cwin[off//4])
    if cval!=2: continue
    vals=[]
    for o,name,win in objs:
        w=np.frombuffer(win,dtype="<u4")
        vals.append(int(w[off//4]))
    c=Counter(v for v in vals if v<=12)
    # ile obiektow ma wartosc <=12 (sensowny enum)
    small=sum(1 for v in vals if v<=12)
    print(f"  {off:#06x}  cier={cval}  small={small}/{len(vals)}  {dict(sorted(c.items()))}")
