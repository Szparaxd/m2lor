"""Precyzyjnie: pozycja @0x280 = translacja m_matGlobal (_41/_42). matGlobal ~ +0x250..0x290.
m_pkEftData tuz po matGlobal (~+0x290). Czytaj wskazniki +0x288..+0x2a4, podazaj do CEffectData,
czytaj jego std::string (filename) z poczatku obiektu (skan +0x0..+0x40, inline+wskaznik)."""
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
        t=pm.read_bytes(v,n).split(b"\x00")[0];s=t.decode("ascii","ignore")
        return s if len(s)>=3 and all(c.isprintable() for c in s) else ""
    except Exception:return ""
def std_string_at(a):
    """MSVC std::string @ a: cap@a+0x14; cap<16 inline@a else wskaznik@a."""
    try:
        cap=pm.read_uint(a+0x14)
        if cap>=16 and cap<1_000_000:
            return readstr(pm.read_uint(a))
        return readstr(a,16)
    except Exception:return ""
def eftdata_fname(p):
    # skan poczatku CEffectData po std::string z .mse
    for off in range(0,0x60,4):
        s=std_string_at(p+off)
        if ".mse" in s.lower() or "/effect" in s.lower():
            return (off,s)
    return None
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
    if abs(x-cx)<1500 and abs(y-cy)<1500: near.append((math.hypot(x-cx,y-cy),o,x,y))
near.sort()
print(f"efektow blisko: {len(near)}\n")
for d,o,x,y in near:
    print(f"### obj={o:#x} dist={d:.0f} pos=({x:.0f},{y:.0f})")
    for po in range(0x288,0x2a8,4):
        try:p=pm.read_uint(o+po)
        except Exception:continue
        if not ptrok(p):continue
        fn=eftdata_fname(p)
        if fn:
            print(f"    m_pkEftData@+{po:#x} -> {p:#x}  fname(+{fn[0]:#x})={fn[1]!r}")
            break
    else:
        # fallback: pokaz wszystkie wskazniki i ich pierwszy string
        for po in range(0x288,0x2a8,4):
            try:p=pm.read_uint(o+po)
            except Exception:continue
            if ptrok(p):
                print(f"    +{po:#x}->{p:#x} str0={readstr(p)!r} ss={std_string_at(p)!r}")
