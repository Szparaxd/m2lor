"""Znajdz klase bliskiego metina 'Metin Walki'.
Napis 11 znakow => std::string INLINE (brak wskaznika). Wiec:
 1) zlokalizuj napis 'Metin Walki\\0' w pamieci,
 2) dla kazdego wystapienia skanuj WSTECZ szukajac waznej vtable (modul-ptr celujacy w modul-ptry),
 3) obj = vtable_addr, name_off = str_addr - obj; sprawdz std::string (size@+16, cap@+20),
 4) znajdz pare floatow ~= pozycja gracza (offset pozycji) oraz vnum 8xxx.
"""
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

# hero center
hero = None
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    hh = np.nonzero(u == hero_vt)[0]
    if len(hh): hero = rb + int(hh[0]) * 4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz (hero@{hero:#x}) world=({cx:.1f},{cy:.1f})")


def is_vtable(v):
    if not (mlo <= v < mhi): return False
    try:
        p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception:
        return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2


# 1) znajdz napisy
hits = []
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    s = 0
    while True:
        i = buf.find(TARGET, s)
        if i < 0: break
        s = i + 1
        end = i + len(TARGET)
        if end < len(buf) and buf[end] == 0:
            hits.append(rb + i)
print(f"wystapien '{TARGET.decode()}\\0': {len(hits)}")

for naddr in hits:
    print(f"\n=== napis @ {naddr:#x} ===")
    # std::string? size@+16 cap@+20 (gdy name buffer zaczyna sie w naddr)
    try:
        sz = pm.read_uint(naddr+16); cp = pm.read_uint(naddr+20)
        print(f"  jako std::string inline: size={sz} cap={cp}  ({'OK' if sz==len(TARGET) and cp==15 else 'nie pasuje'})")
    except Exception:
        pass
    # skan wstecz do 0x1200 bajtow szukajac waznej vtable blisko (pierwsza = najblizszy obj start)
    WIN = 0x1200
    lo = naddr - WIN
    try: blk = pm.read_bytes(lo, WIN + 4)
    except Exception:
        print("  (nie moge odczytac bloku)"); continue
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    # kandydaci vtable (od najblizszej do napisu idac wstecz)
    cands = []
    for j in range(len(u)-1, -1, -1):
        v = int(u[j])
        if is_vtable(v):
            vt_addr = lo + j*4
            cands.append(vt_addr)
            if len(cands) >= 6: break
    for vt_addr in cands:
        obj = vt_addr
        vt = pm.read_uint(obj)
        name_off = naddr - obj
        if name_off <= 0 or name_off > 0x1000: continue
        # sprawdz std::string na name_off
        try:
            sz = pm.read_uint(obj+name_off+16); cp = pm.read_uint(obj+name_off+20)
        except Exception:
            sz = cp = -1
        strok = (sz == len(TARGET))
        print(f"  vtable {vt:#x} (base+{vt-base:#x}) obj={obj:#x} name_off={name_off:#x} str(size={sz},cap={cp}){' <-STR OK' if strok else ''}")
        # szukaj par floatow ~ pozycja gracza w obiekcie (0..0x800)
        try: odata = pm.read_bytes(obj, 0x820)
        except Exception: continue
        f = np.frombuffer(odata[:(len(odata)//4)*4], dtype="<f4")
        ui = np.frombuffer(odata[:(len(odata)//4)*4], dtype="<u4")
        for k in range(len(f)-1):
            a, b = float(f[k]), float(f[k+1])
            if abs(a-cx) < 600 and abs(b-cy) < 600:
                print(f"       pos? off={k*4:#x}: ({a:.1f},{b:.1f}) dist={math.hypot(a-cx,b-cy):.1f}")
            # -Y wariant
            if abs(a-cx) < 600 and abs(-b-cy) < 600:
                print(f"       pos?(-Y) off={k*4:#x}: ({a:.1f},{-b:.1f}) dist={math.hypot(a-cx,-b-cy):.1f}")
        # szukaj vnum 8xxx
        for k in range(len(ui)):
            val = int(ui[k])
            if 8000 <= val <= 8999:
                print(f"       vnum? off={k*4:#x}: {val}")
