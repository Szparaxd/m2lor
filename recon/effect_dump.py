"""Enumeruj CEffectInstance (vtable base+0x3ce250). Dla kazdego:
- znajdz pozycje swiata (float x ~cx, y ~+/-cy) w pierwszych 0x300 B,
- znajdz wskaznik m_pkEftData -> CEffectData.m_strFileName (sciezka .mse),
by zidentyfikowac efekt (stone/metin smoke/glow) i jego pozycje blisko gracza."""
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
def readstr(v,n=80):
    try:
        raw=pm.read_bytes(v,n);t=raw.split(b"\x00")[0]
        return t.decode("ascii","ignore") if t else ""
    except Exception:return ""
def deref_string(ptr):
    # std::string: maly (inline) lub wskaznik; sprobuj oba
    s=readstr(ptr)
    if len(s)>=4 and s.count("/")>=1: return s
    try:
        p=pm.read_uint(ptr)
        if mlo<=p<mhi or inheap(p):
            s2=readstr(p)
            if len(s2)>=4: return s2
    except Exception:pass
    return s
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
print(f"CEffectInstance (base+{EFF_VT:#x}): {len(effs)}")
out=[]
for o in effs:
    try:raw=pm.read_bytes(o,0x300)
    except Exception:continue
    f=np.frombuffer(raw,dtype="<f4"); u=np.frombuffer(raw,dtype="<u4")
    # pozycja swiata: x ~cx
    posoff=None;px=py=None
    for i in range(1,len(f)-2):
        x=float(f[i])
        if abs(x-cx)<1500:
            for j in (i+1,i+2):
                for sign in (1.0,-1.0):
                    y=sign*float(f[j])
                    if abs(y-cy)<1500:
                        posoff=i*4;px,py=x,y;break
                if posoff is not None:break
        if posoff is not None:break
    # filename: skan wskaznikow -> string z '/'
    fname=""
    for i in range(len(u)):
        v=int(u[i])
        if mlo<=v<mhi or inheap(v):
            s=deref_string(v)
            if "/" in s and (".mse" in s or "effect" in s or ".eff" in s):
                fname=s;break
    if posoff is not None or fname:
        d=math.hypot(px-cx,py-cy) if px is not None else 9e9
        out.append((d,o,posoff,px,py,fname))
out.sort(key=lambda e:e[0])
print(f"\n=== efekty z pozycja/nazwa (sort dist) ===")
for d,o,posoff,px,py,fname in out[:40]:
    ds=f"{d:.0f}" if d<9e8 else "  -"
    ps=f"+{posoff:#x} ({px:.0f},{py:.0f})" if posoff is not None else "poz=?"
    print(f"  dist={ds:>6} obj={o:#x} {ps} eff={fname!r}")
