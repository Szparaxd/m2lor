"""Rozwiaz nazwe pliku .mse dla STACJONARNYCH efektow blisko gracza (kandydaci na metin).
Idz 2 poziomy w glab po wskaznikach; czytaj std::string (inline + wskaznik). Szukaj '.mse'/'effect'.
Porownaj z ruchomymi efektami gracza (dist~2) by odroznic metin od aury gracza."""
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
def ptrok(v):
    if mlo<=v<mhi:return True
    for rb,rs in regs:
        if rb<=v<rb+rs:return True
    return False
def readstr(v,n=120):
    try:
        raw=pm.read_bytes(v,n);t=raw.split(b"\x00")[0]
        s=t.decode("ascii","ignore")
        return s if len(s)>=4 and all(c.isprintable() for c in s) else ""
    except Exception:return ""
def find_mse(obj,maxdepth=3):
    """BFS po wskaznikach z obiektu, szukaj stringa z .mse/effect."""
    seen=set();frontier=[(obj,0)]
    results=[]
    while frontier:
        addr,dep=frontier.pop(0)
        if addr in seen or dep>maxdepth:continue
        seen.add(addr)
        try:raw=pm.read_bytes(addr,0x120)
        except Exception:continue
        # inline string?
        s0=readstr(addr)
        if ".mse" in s0.lower() or "/effect" in s0.lower(): results.append((dep,addr,s0))
        u=np.frombuffer(raw[:0x120],dtype="<u4")
        for v in u:
            v=int(v)
            if not ptrok(v):continue
            s=readstr(v)
            if ".mse" in s.lower() or "/effect" in s.lower():
                results.append((dep+1,v,s))
            elif dep<maxdepth and len(seen)<400:
                frontier.append((v,dep+1))
        if len(results)>=6:break
    return results
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
    if abs(x-cx)<1500 and abs(y-cy)<1500:
        near.append((math.hypot(x-cx,y-cy),o,x,y))
near.sort()
for d,o,x,y in near:
    x0=pm.read_float(o+0x280);time.sleep(0.4);x1=pm.read_float(o+0x280)
    st="STAB" if abs(x1-x0)<0.3 else "ruch"
    print(f"\n### efekt obj={o:#x} dist={d:.0f} pos=({x:.0f},{y:.0f}) [{st}]")
    res=find_mse(o)
    if not res: print("    (brak .mse/effect w zasiegu)")
    for dep,addr,s in res[:6]:
        print(f"    d{dep} {addr:#x}: {s!r}")
