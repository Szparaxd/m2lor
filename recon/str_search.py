"""Szukaj w CALEJ czytelnej pamieci stringow sciezek zwiazanych z metinem:
'metin','stone','.mse','.gr2','.msm','smoke'. Pokaz adres + kontekst. To ujawni,
jakie modele/efekty metina sa zaladowane i gdzie leza (anchory do struktur)."""
import ctypes
from ctypes import wintypes
import pymem, pymem.process
PROCESS="GameCoreTop.exe"
k32=ctypes.WinDLL("kernel32",use_last_error=True)
class MBI(ctypes.Structure):
    _fields_=[("BaseAddress",ctypes.c_void_p),("AllocationBase",ctypes.c_void_p),("AllocationProtect",wintypes.DWORD),("__a1",wintypes.DWORD),("RegionSize",ctypes.c_size_t),("State",wintypes.DWORD),("Protect",wintypes.DWORD),("Type",wintypes.DWORD),("__a2",wintypes.DWORD)]
k32.VirtualQueryEx.restype=ctypes.c_size_t
k32.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(MBI),ctypes.c_size_t]
READABLE={0x02,0x04,0x08,0x20,0x40,0x80}  # wszystkie czytelne
def regions(h):
    out,a,mbi=[],0,MBI()
    while a<0x7FFFFFFF:
        if not k32.VirtualQueryEx(h,ctypes.c_void_p(a),ctypes.byref(mbi),ctypes.sizeof(mbi)):break
        b,s,p=(mbi.BaseAddress or 0),(mbi.RegionSize or 0),mbi.Protect
        if mbi.State==0x1000 and not(p&0x100) and (p in READABLE) and 0<s<=512*1024*1024: out.append((b,s))
        n=b+s
        if n<=a:break
        a=n
    return out
pm=pymem.Pymem(PROCESS); mod=pymem.process.module_from_name(pm.process_handle,PROCESS); base=mod.lpBaseOfDll
regs=regions(pm.process_handle)
needles=[b"metin",b"stone",b"smoke"]
from collections import Counter
hits=Counter()
examples={}
for rb,rs in regs:
    try:buf=pm.read_bytes(rb,rs)
    except Exception:continue
    low=buf.lower()
    for nd in needles:
        s=0
        while True:
            i=low.find(nd,s)
            if i<0:break
            s=i+1
            # kontekst jako sciezka/string
            st=i
            while st>0 and 32<=buf[st-1]<127 and i-st<64: st-=1
            en=i
            while en<len(buf) and 32<=buf[en]<127 and en-i<64: en+=1
            ctx=buf[st:en].decode("ascii","ignore")
            if len(ctx)<3: continue
            hits[nd]+=1
            key=(nd,ctx)
            if ctx not in [v[1] for v in examples.values()]:
                examples[rb+i]=(nd,ctx,rb+st)
print("liczba trafien:",{k.decode():v for k,v in hits.items()})
print(f"\nunikalne konteksty ({len(examples)}):")
seen=set()
for addr,(nd,ctx,saddr) in sorted(examples.items()):
    if ctx in seen:continue
    seen.add(ctx)
    print(f"  {saddr:#x} [{nd.decode()}] {ctx!r}")
