"""Znajdz wszystkie wskazniki (DWORD) celujace w KAZDE wystapienie 'Metin Walki' (naddr).
Dla kazdego wskaznika: ptr_addr to potencjalne pole std::string(name, heap) lub char*; obj = ptr_addr - name_off.
Skanuj name_off w {0x354, inne}: ale nie znamy -> zamiast tego back-scan vtable i floaty.
Dodatkowo: dla kazdego ptr_addr sprawdz, czy ptr_addr-16..-? wyglada jak std::string (cap>=16 -> heap)."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
TARGET = b"Metin Walki"
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


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
hero_vt = base + HERO_VT_OFF
regs = regions(pm.process_handle)
cache = {}
for rb, rs in regs:
    try: cache[(rb, rs)] = pm.read_bytes(rb, rs)
    except Exception: pass

hero = None
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.1f},{cy:.1f}) base={base:#x}")

# wszystkie wystapienia napisu (poczatek bufora)
occ = []
for (rb, rs), buf in cache.items():
    s = 0
    while True:
        i = buf.find(TARGET, s)
        if i < 0: break
        s = i+1
        occ.append(rb+i)
print(f"wystapien napisu: {len(occ)} -> {[hex(o) for o in occ]}")
occ_arr = np.array(occ, dtype=np.uint32)


def vt_ok(v):
    if not (mlo <= v < mhi): return False
    try: p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception: return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2


# skan wskaznikow == ktorys z occ
print("\n== wskazniki na bufory napisu ==")
for (rb, rs), buf in cache.items():
    n = (len(buf)//4)*4
    u = np.frombuffer(buf[:n], dtype="<u4")
    mask = np.isin(u, occ_arr)
    for k in np.nonzero(mask)[0]:
        ptr_addr = rb + int(k)*4
        tgt = int(u[k])
        # czy to std::string heap? size@ptr_addr+16, cap@+20, cap>=16
        try:
            sz = pm.read_uint(ptr_addr+16); cp = pm.read_uint(ptr_addr+20)
        except Exception:
            sz = cp = -1
        tag = ""
        if sz == 11 and cp >= 16: tag = " <-std::string(heap,name) size=11"
        print(f"  ptr@{ptr_addr:#x} -> {tgt:#x}  (@ptr+16 size={sz} cap={cp}){tag}")
        # back-scan vtable w 0x800 jesli to pole nazwy: obj = ptr_addr - name_off
        lo = ptr_addr - 0x800
        try: blk = pm.read_bytes(lo, 0x804)
        except Exception: continue
        uu = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
        for j in range(len(uu)-1, -1, -1):
            if vt_ok(int(uu[j])):
                obj = lo + j*4; vt = int(uu[j]); name_off = ptr_addr - obj
                # szukaj floatow ~ gracz w obj
                try: od = pm.read_bytes(obj, name_off+0x40)
                except Exception: break
                fo = np.frombuffer(od[:(len(od)//4)*4], dtype="<f4")
                poss = []
                for t in range(len(fo)-1):
                    a, b = float(fo[t]), float(fo[t+1])
                    if abs(a-cx) < 900 and (abs(b-cy) < 900 or abs(-b-cy) < 900):
                        yv = b if abs(b-cy)<900 else -b
                        poss.append((t*4, a, yv, math.hypot(a-cx, yv-cy)))
                ps = " ".join(f"[+{o:#x}=({x:.0f},{y:.0f})d{d:.0f}]" for o,x,y,d in poss[:4])
                print(f"      obj={obj:#x} vtable=base+{vt-base:#x} name_off={name_off:#x} pos:{ps or 'brak'}")
                break
