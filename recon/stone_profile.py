"""Porownaj Metin Cierpienia (znany STONE) vs Wilk vs bliskie puste obiekty vnum=0.
Szukamy: (a) co odroznia stone od moba (pole typu), (b) czy ktorys bliski pusty obiekt
ma sygnature stone/metina. Dump DWORDow + interpretacja wskaznikow modulowych/heapowych."""
import ctypes, math
from ctypes import wintypes
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
pm=pymem.Pymem(PROCESS); mod=pymem.process.module_from_name(pm.process_handle,PROCESS)
base,mlo,mhi=mod.lpBaseOfDll,mod.lpBaseOfDll,mod.lpBaseOfDll+mod.SizeOfImage
regs=regions(pm.process_handle)
# mapa regionow do klasyfikacji wskaznikow
def whatptr(v):
    if mlo<=v<mhi: return f"MOD+{v-base:#x}"
    for rb,rs in regs:
        if rb<=v<rb+rs: return "heap"
    return ""
def read_name(a,off=0x354):
    try:
        size=pm.read_uint(a+off+16);cap=pm.read_uint(a+off+20)
        raw=pm.read_bytes(pm.read_uint(a+off),min(max(size,1),48)) if cap>=16 and cap<1_000_000 else pm.read_bytes(a+off,15)
    except Exception:return "?"
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])
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
objs=[]
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+MOB_VT_OFF)[0]:
        o=rb+int(h)*4
        try:x=pm.read_float(o+0x264);y=pm.read_float(o+0x268);vnum=pm.read_uint(o+0x4B0)
        except Exception:continue
        d=math.hypot(x-cx,y-cy) if 1000<abs(x)<1e7 else -1
        objs.append((d,o,x,y,vnum,read_name(o)))

cier=[o for o in objs if o[5]=="Metin Cierpienia"]
wilk=[o for o in objs if o[5]=="Alfa Wilk"]
empt=sorted([o for o in objs if o[5]=="" and 0<o[0]<1200],key=lambda m:m[0])
picks=[("CIERPIENIA",cier[0])] if cier else []
if wilk: picks.append(("WILK",wilk[0]))
for i,e in enumerate(empt[:4]): picks.append((f"EMPTY#{i} d={e[0]:.0f}",e))

def dump(tag,rec):
    d,o,x,y,vnum,name=rec
    print(f"\n### {tag}  obj={o:#x} dist={d:.0f} pos=({x:.0f},{y:.0f}) vnum={vnum} name={name!r}")
    try: raw=pm.read_bytes(o,0x580)
    except Exception: print("  read fail"); return
    w=np.frombuffer(raw,dtype="<u4")
    for off in range(0,0x580,4):
        v=int(w[off//4])
        if v==0: continue
        wp=whatptr(v)
        extra=""
        if wp:
            # jesli heap i wskazuje na string?
            try:
                s=pm.read_bytes(v,24); txt="".join(chr(b) if 32<=b<127 else "" for b in s[:20])
                if len(txt)>=4 and sum(c.isalpha() for c in txt)>=3: extra=f' -> "{txt}"'
            except Exception: pass
            fl=np.frombuffer(raw[off:off+4],dtype="<f4")[0]
            print(f"  +{off:#05x}: {v:#010x} [{wp}]{extra}")
        else:
            fl=np.frombuffer(raw[off:off+4],dtype="<f4")[0]
            fs=f" f={fl:.1f}" if 1e-3<abs(fl)<1e7 else ""
            if v<=12 or (v<70000):
                print(f"  +{off:#05x}: {v:<11}{fs}")
for tag,rec in picks: dump(tag,rec)
