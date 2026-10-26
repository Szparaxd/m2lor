"""Znajdz pole VNUM/typ: offset gdzie encje o tej samej nazwie maja te sama wartosc.
Potem pokaz wartosc dla 'Metin ...' vs moby."""
import ctypes
from ctypes import wintypes
from collections import defaultdict
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
VT_OFF = 0x3D5000
DUMP = 0x900
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
WRITABLE = {0x04, 0x08, 0x40, 0x80}


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD)]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def regions(h):
    out, a, mbi = [], 0, MBI()
    while a < 0x7FFFFFFF:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WRITABLE and 0 < s <= 512 * 1024 * 1024:
            out.append((b, s))
        n = b + s
        if n <= a: break
        a = n
    return out


def read_name(pm, o):
    try:
        size = pm.read_uint(o+0x354+16); cap = pm.read_uint(o+0x354+20)
        if size == 0 or size > 64 or cap > 1_000_000: return ""
        raw = pm.read_bytes(o+0x354, min(size,15)) if cap < 16 else pm.read_bytes(pm.read_uint(o+0x354), min(size,48))
    except Exception: return ""
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


pm = pymem.Pymem(PROCESS)
base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
vtable = base + VT_OFF

named = []  # (name, uint-array)
for rb, rs in regions(pm.process_handle):
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u==vtable)[0]:
        o = rb+int(h)*4
        nm = read_name(pm, o)
        if nm:
            try: blk = np.frombuffer(pm.read_bytes(o, DUMP), dtype="<u4")
            except Exception: continue
            named.append((nm, blk))

print(f"nazwanych encji: {len(named)}; roznych nazw: {len(set(n for n,_ in named))}")

# dla kazdego offsetu: ile nazw ma SPOJNA wartosc (wszystkie instancje tej nazwy == ta sama)
best = []
ncol = DUMP//4
for c in range(ncol):
    by_name = defaultdict(set)
    for nm, blk in named:
        by_name[nm].add(int(blk[c]))
    consistent = sum(1 for s in by_name.values() if len(s) == 1)
    distinct_vals = len(set(next(iter(s)) for s in by_name.values() if len(s) == 1))
    # dobry typ: duzo spojnych nazw, wartosci to male inty
    vals = [next(iter(s)) for s in by_name.values() if len(s)==1]
    if vals and consistent >= len(by_name)*0.8 and all(v < 100000 for v in vals) and distinct_vals >= 3:
        best.append((consistent, distinct_vals, c))

best.sort(reverse=True)
print("Najlepsze offsety (spojnych_nazw, roznych_wartosci, offset):")
for consistent, dv, c in best[:12]:
    print(f"  off={c*4:#x}  spojnych={consistent}/{len(set(n for n,_ in named))}  roznych_wart={dv}")

# dla najlepszego offsetu pokaz nazwa->wartosc
if best:
    c = best[0][2]
    print(f"\nMapa nazwa->wartosc dla off={c*4:#x}:")
    seen = {}
    for nm, blk in named:
        seen.setdefault(nm, int(blk[c]))
    for nm, v in sorted(seen.items(), key=lambda kv: kv[1]):
        mark = "  <== METIN" if "metin" in nm.lower() else ""
        print(f"  {v:8}  {nm!r}{mark}")
