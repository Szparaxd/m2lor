"""Znajdz nazwe biezacej mapy w pamieci: szukaj 'metin2_map_' i sciezek 'd:/ymir work/...' map."""
import ctypes
from ctypes import wintypes
from collections import Counter
import pymem, pymem.process
PROCESS="GameCoreTop.exe"
k32=ctypes.WinDLL("kernel32",use_last_error=True); WR={0x04,0x08,0x40,0x80}
class MBI(ctypes.Structure):
    _fields_=[("B",ctypes.c_void_p),("A",ctypes.c_void_p),("AP",wintypes.DWORD),("a1",wintypes.DWORD),("RS",ctypes.c_size_t),("St",wintypes.DWORD),("P",wintypes.DWORD),("T",wintypes.DWORD),("a2",wintypes.DWORD)]
k32.VirtualQueryEx.restype=ctypes.c_size_t; k32.VirtualQueryEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.POINTER(MBI),ctypes.c_size_t]
def regs(h):
    o=[];a=0;m=MBI()
    while a<0x7FFFFFFF:
        if not k32.VirtualQueryEx(h,ctypes.c_void_p(a),ctypes.byref(m),ctypes.sizeof(m)):break
        b,s,p=(m.B or 0),(m.RS or 0),m.P
        if m.St==0x1000 and not(p&0x100) and not(p&1) and p in WR and 0<s<=512*1024*1024:o.append((b,s))
        n=b+s
        if n<=a:break
        a=n
    return o
pm=pymem.Pymem(PROCESS)
cnt=Counter()
for rb,rs in regs(pm.process_handle):
    try:buf=pm.read_bytes(rb,rs)
    except:continue
    s=0
    while True:
        i=buf.find(b"metin2_map_",s)
        if i<0:break
        s=i+1
        end=i
        while end<len(buf) and end<i+40 and (48<=buf[end]<=122 or buf[end] in (0x5f,0x2f,0x5c,0x2e)):end+=1
        cnt[buf[i:end].decode("ascii","ignore")]+=1
for name,c in cnt.most_common(25):
    print(f"{c:6}  {name}")
