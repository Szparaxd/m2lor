"""Test stacjonarnosci wsrod 177 CInstanceBase. Metin stoi, moby lazą. Odczyt pos 2x (1.2s).
Pokaz NIERUCHOME obiekty blisko gracza (dist<2000, dist>5 by wykluczyc hero). Dla kazdego:
vnum@0x4b0, nazwa, oraz pelny dump pol 0x60..0x80 i 0x4a0..0x4d0 (szukamy flagi STONE)."""
import ctypes, math, time
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
hero=None
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    hh=np.nonzero(u==base+HERO_VT_OFF)[0]
    if len(hh):hero=rb+int(hh[0])*4;break
cx=pm.read_float(hero+0x8);cy=-pm.read_float(hero+0xC)
print(f"gracz=({cx:.0f},{cy:.0f})")
chars=[]
for (rb,rs),buf in cache.items():
    u=np.frombuffer(buf[:(len(buf)//4)*4],dtype="<u4")
    for h in np.nonzero(u==base+MOB_VT_OFF)[0]: chars.append(rb+int(h)*4)
def pos(o):
    try:return pm.read_float(o+0x264),pm.read_float(o+0x268)
    except Exception:return None
p1={o:pos(o) for o in chars}
time.sleep(1.2)
p2={o:pos(o) for o in chars}
print(f"chars={len(chars)}  -- stacjonarne blisko gracza --")
res=[]
for o in chars:
    a,b=p1[o],p2[o]
    if not a or not b:continue
    if abs(a[0]-b[0])<0.3 and abs(a[1]-b[1])<0.3:
        d=math.hypot(a[0]-cx,a[1]-cy) if 1000<abs(a[0])<1e7 else -1
        if 5<d<2500:
            res.append((d,o,a))
for d,o,a in sorted(res):
    vnum=pm.read_uint(o+0x4b0); nm=read_name(o)
    print(f"\n  dist={d:.0f} obj={o:#x} vnum={vnum} pos=({a[0]:.0f},{a[1]:.0f}) name={nm!r}")
    try:
        w=np.frombuffer(pm.read_bytes(o+0x60,0x30),dtype="<u4")
        print("    0x60: "+" ".join(f"{v}" if v<100000 else f"{v:#x}" for v in w))
        w2=np.frombuffer(pm.read_bytes(o+0x4a0,0x30),dtype="<u4")
        print("    0x4a0: "+" ".join(f"{v}" if v<100000 else f"{v:#x}" for v in w2))
    except Exception:pass
if not res: print("  BRAK stacjonarnych postaci blisko gracza (poza hero)")
