"""Znajdz klase metina: zlokalizuj napis 'Metin ...', znajdz obiekt wskazujacy na niego (0x354),
odczytaj vtable + pozycje. Bliski metin (dist~0) ujawni wlasciwa vtable i offset pozycji."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
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
    hh = np.nonzero(u==hero_vt)[0]
    if len(hh): hero = rb+int(hh[0])*4; break
cx = pm.read_float(hero+0x8); cy = -pm.read_float(hero+0xC)
print(f"gracz world=({cx:.0f},{cy:.0f})")

# 1) znajdz napisy "Metin " -> {addr: name}
name_at = {}
for rb, rs in regs:
    try: buf = pm.read_bytes(rb, rs)
    except Exception: continue
    s = 0
    while True:
        i = buf.find(b"Metin ", s)
        if i < 0: break
        s = i+1
        end = buf.find(b"\x00", i, i+40)
        if end > i:
            name_at[rb+i] = buf[i:end].decode("ascii", "ignore")
print(f"napisow 'Metin ...': {len(name_at)} -> {sorted(set(name_at.values()))[:8]}")
addr_set = set(name_at)

# 2) znajdz wskazniki na te napisy (potencjalne pole nazwy obiektu)
def scan_ptr_to(addrs):
    res = []
    arr = np.array(sorted(addrs), dtype=np.uint64)
    for rb, rs in regs:
        try: buf = pm.read_bytes(rb, rs)
        except Exception: continue
        u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
        mask = np.isin(u, arr.astype(np.uint32))
        for j in np.nonzero(mask)[0]:
            res.append((rb+int(j)*4, int(u[j])))
    return res

ptrs = scan_ptr_to(addr_set)
print(f"wskaznikow na napisy metinow: {len(ptrs)}")
seen = set()
for ptr_addr, straddr in ptrs:
    obj = ptr_addr - 0x354  # zaloz nazwe na 0x354 jak u mobow
    if obj in seen: continue
    seen.add(obj)
    try:
        vt = pm.read_uint(obj)
        x = pm.read_float(obj+0x264); y = pm.read_float(obj+0x268)
    except Exception:
        continue
    inmod = mlo <= vt < mhi
    d = math.hypot(x-cx, y-cy) if (1000 < abs(x) < 1e7) else -1
    print(f"  obj={obj:#x} vt={vt:#x}{' base+'+hex(vt-base) if inmod else ''}  "
          f"name={name_at[straddr]!r:20} 0x264={x:.0f} 0x268={y:.0f} dist={d:.0f}")
