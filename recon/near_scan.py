"""(A) Lista WSZYSTKICH obiektow mob-vtable (base+0x3D5000) wg odleglosci od gracza: vnum,name.
(B) Globalny skan pozycji: pary floatow ~ gracz w calej pamieci zapisywalnej -> znajdz obiekty blisko,
    wyluskaj ich vtable (skan wstecz) aby wykryc osobna klase bliskiego metina."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
MOB_VT_OFF = 0x3D5000
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


def read_name(pm, o, off=0x354):
    try:
        size = pm.read_uint(o+off+16); cap = pm.read_uint(o+off+20)
        if size > 64 or cap > 1_000_000: return ""
        raw = pm.read_bytes(o+off, min(size,15)) if cap < 16 else pm.read_bytes(pm.read_uint(o+off), min(size,48))
    except Exception: return ""
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage
hero_vt = base + HERO_VT_OFF
mob_vt = base + MOB_VT_OFF
regs = regions(pm.process_handle)

# read all regions once
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
print(f"gracz world=({cx:.1f},{cy:.1f})  base={base:#x}")

# (A) all mob-vtable objects
print("\n===== (A) obiekty mob-vtable (base+0x3D5000) wg dist =====")
mobs = []
for (rb, rs), buf in cache.items():
    u = np.frombuffer(buf[:(len(buf)//4)*4], dtype="<u4")
    for h in np.nonzero(u == mob_vt)[0]:
        o = rb + int(h)*4
        try:
            x = pm.read_float(o+0x264); y = pm.read_float(o+0x268); vnum = pm.read_uint(o+0x4B0)
        except Exception: continue
        name = read_name(pm, o)
        d = math.hypot(x-cx, y-cy) if 1000 < abs(x) < 1e7 else -1
        mobs.append((d, o, x, y, vnum, name))
for d, o, x, y, vnum, name in sorted(mobs, key=lambda m: (m[0] if m[0]>=0 else 9e9))[:20]:
    print(f"  dist={d:8.0f} obj={o:#x} vnum={vnum:5d} pos=({x:.0f},{y:.0f}) name={name!r}")

# (B) global position search
print("\n===== (B) globalny skan par floatow ~ gracz (<=700) =====")
R = 700
cand = []
for (rb, rs), buf in cache.items():
    n = (len(buf)//4)*4
    f = np.frombuffer(buf[:n], dtype="<f4")
    # X near cx
    ix = np.nonzero(np.abs(f - cx) < R)[0]
    for k in ix:
        if k+1 < len(f):
            y = float(f[k+1])
            if abs(y-cy) < R:
                cand.append((rb+int(k)*4, float(f[k]), y, '+Y'))
            elif abs(-y-cy) < R:
                cand.append((rb+int(k)*4, float(f[k]), -y, '-Y'))
print(f"  kandydatow pozycji: {len(cand)}")
# dla kazdego: skan wstecz na vtable (do 0x900), aby znalezc obj/klase
def is_vtable(v):
    if not (mlo <= v < mhi): return False
    try: p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception: return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2

seen_obj = set()
for posaddr, x, y, yv in cand:
    # skan wstecz
    lo = posaddr - 0x900
    try: blk = pm.read_bytes(lo, 0x904)
    except Exception: continue
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    best = None
    for j in range(len(u)-1, -1, -1):
        if is_vtable(int(u[j])):
            best = lo + j*4; break
    if best is None:
        print(f"  pos@{posaddr:#x}=({x:.0f},{y:.0f}){yv} d={math.hypot(x-cx,y-cy):.0f}  [brak vtable w 0x900]")
        continue
    if best in seen_obj: continue
    seen_obj.add(best)
    vt = pm.read_uint(best)
    posoff = posaddr - best
    name = read_name(pm, best, 0x354)
    print(f"  obj={best:#x} vtable=base+{vt-base:#x} pos_off={posoff:#x}=({x:.0f},{y:.0f}){yv} d={math.hypot(x-cx,y-cy):.0f} name@0x354={name!r}")
