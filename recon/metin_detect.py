"""WYKRYWANIE METINOW przez efekt swiecenia (ROZWIAZANIE).
Metin = CEffectInstance (vtable base+0x3ce250) z m_pkEftData(+0x290)->CEffectData.m_strFileName(+0x44)
zawierajacym 'metinstone' (d:/ymir work/effect/background/metinstone_*.mse).
Pozycja swiata: X=+0x280, Y=-(+0x284), Z=+0x288 (translacja m_matGlobal). Weryfikacja: dist, stacjon."""
import ctypes, math, time
from ctypes import wintypes
import numpy as np
import pymem, pymem.process
PROCESS="GameCoreTop.exe"; HERO_VT_OFF=0x3D6CA0; EFF_VT=0x3ce250
POS_X,POS_Y,POS_Z=0x280,0x284,0x288; EFTDATA=0x290; FNAME=0x44
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
def readstr(v,n=120):
    try:
        t=pm.read_bytes(v,n).split(b"\x00")[0];s=t.decode("ascii","ignore")
        return s if all(c.isprintable() for c in s) else ""
    except Exception:return ""
def std_string_at(a):
    try:
        cap=pm.read_uint(a+0x14)
        return readstr(pm.read_uint(a)) if 16<=cap<1_000_000 else readstr(a,16)
    except Exception:return ""
def eff_fname(o):
    try:p=pm.read_uint(o+EFTDATA)
    except Exception:return ""
    if not(mlo<=p<mhi):
        ok=any(rb<=p<rb+rs for rb,rs in regs)
        if not ok:return ""
    return std_string_at(p+FNAME)
hero=None
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    hh=np.nonzero(u==base+HERO_VT_OFF)[0]
    if len(hh):hero=rb+int(hh[0])*4;break
cx=pm.read_float(hero+0x8);cy=-pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f})  base={base:#x}")
effs=[]
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+EFF_VT)[0]: effs.append(rb+int(h)*4)
rows=[]
for o in effs:
    fn=eff_fname(o)
    if not fn:continue
    try:
        x=pm.read_float(o+POS_X); y=-pm.read_float(o+POS_Y); z=pm.read_float(o+POS_Z)
    except Exception:continue
    if not(1000<abs(x)<1e7):continue
    d=math.hypot(x-cx,y-cy)
    rows.append((d,o,x,y,z,fn))
rows.sort()
metins=[r for r in rows if "metinstone" in r[5].lower()]
print(f"\nCEffectInstance razem: {len(effs)}  z nazwa pliku: {len(rows)}  METINY: {len(metins)}")
print("\n=== WSZYSTKIE efekty z pozycja (sort dist) ===")
for d,o,x,y,z,fn in rows:
    tag=" <== METIN" if "metinstone" in fn.lower() else ""
    print(f"  dist={d:7.0f} obj={o:#x} pos=({x:.0f},{y:.0f},{z:.0f}) {fn}{tag}")
print("\n=== METINY (weryfikacja stacjonarnosci 1.0s) ===")
snap={o:pm.read_float(o+POS_X) for _,o,*_ in metins}
time.sleep(1.0)
for d,o,x,y,z,fn in metins:
    x1=pm.read_float(o+POS_X)
    print(f"  METIN dist={d:.0f} pos=({x:.0f},{y:.0f}) X:{snap[o]:.2f}->{x1:.2f} {'STABILNA' if abs(x1-snap[o])<0.5 else 'ZMIENNA'} | {fn}")
