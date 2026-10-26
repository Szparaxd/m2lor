"""Zbadaj kontenery dwoch pol-nazw std::string -> 'Metin Walki' (0x1f83aa58, 0x232dafd0).
Testuj name_off=0x354 (klasa mobow) oraz szeroki back-scan vtable (do 0x1800).
Dla kandydata obj pokaz vtable, vnum@0x4B0, pos@0x264/0x268 i wszystkie floaty ~ gracz."""
import ctypes, math
from ctypes import wintypes
import numpy as np
import pymem, pymem.process

PROCESS = "GameCoreTop.exe"
HERO_VT_OFF = 0x3D6CA0
NAMEPTRS = [0x1f83aa58, 0x232dafd0]
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def rd_u(a):
    return pm.read_uint(a)


pm = pymem.Pymem(PROCESS)
mod = pymem.process.module_from_name(pm.process_handle, PROCESS)
base, mlo, mhi = mod.lpBaseOfDll, mod.lpBaseOfDll, mod.lpBaseOfDll + mod.SizeOfImage

# hero (bez pelnego skanu - uzyj znanego wzorca: znajdz przez male przeszukanie? mamy adres z poprzednich ~0x4c361c0 ale moze sie zmienic)
# szybki: skan tylko kilku regionow? Zamiast tego odczyt z poprzedniego - ale bezpieczniej przeliczyc.
# Uzyjemy wartosci gracza ze stalej sceny:
cx, cy = 29279.1, 35064.3
print(f"gracz (zalozone) world=({cx:.1f},{cy:.1f}) base={base:#x}")


def vt_ok(v):
    if not (mlo <= v < mhi): return False
    try: p = np.frombuffer(pm.read_bytes(v, 16), dtype="<u4")
    except Exception: return False
    return int(np.sum((p >= mlo) & (p < mhi))) >= 2


def read_str(a):
    try:
        size = pm.read_uint(a+16); cap = pm.read_uint(a+20)
        if size > 64 or cap > 1_000_000: return ""
        raw = pm.read_bytes(a, min(size,15)) if cap < 16 else pm.read_bytes(pm.read_uint(a), min(size,48))
    except Exception: return ""
    return "".join(chr(b) if 32<=b<127 else "." for b in raw.split(b"\x00")[0])


for nameptr in NAMEPTRS:
    print(f"\n########## name-field @ {nameptr:#x} (str={read_str(nameptr)!r}) ##########")
    # hipoteza klasy mobow: obj = nameptr - 0x354
    obj = nameptr - 0x354
    vt = pm.read_uint(obj)
    print(f"-- obj=nameptr-0x354={obj:#x} vtable@+0={vt:#x}"
          + (f" base+{vt-base:#x}" if mlo<=vt<mhi else " (NIE modul)"))
    try:
        print(f"   vnum@0x4B0={pm.read_uint(obj+0x4B0)} pos@0x264/0x268=({pm.read_float(obj+0x264):.1f},{pm.read_float(obj+0x268):.1f})")
    except Exception as e: print("   err", e)

    # szeroki back-scan vtable
    WIN = 0x1800
    lo = nameptr - WIN
    blk = pm.read_bytes(lo, WIN+0x40)
    u = np.frombuffer(blk[:(len(blk)//4)*4], dtype="<u4")
    print(f"-- back-scan vtable w {WIN:#x} (od najblizszej) --")
    found = 0
    for j in range(len(u)-1, -1, -1):
        if vt_ok(int(u[j])):
            a = lo + j*4; vt = int(u[j]); noff = nameptr - a
            print(f"   obj={a:#x} vtable=base+{vt-base:#x} name_off={noff:#x}")
            found += 1
            if found >= 10: break
    if not found:
        print("   (brak modul-vtable w oknie)")

    # floaty ~ gracz w szerokim oknie wokol nazwy
    lo2 = nameptr - 0x1000
    blk2 = pm.read_bytes(lo2, 0x1400)
    f = np.frombuffer(blk2[:(len(blk2)//4)*4], dtype="<f4")
    print("-- floaty ~ gracz (<900) w oknie [-0x1000,+0x400] --")
    hits = 0
    for t in range(len(f)-1):
        a, b = float(f[t]), float(f[t+1])
        if abs(a-cx) < 900 and (abs(b-cy) < 900 or abs(-b-cy) < 900):
            yv = b if abs(b-cy)<900 else -b
            ad = lo2 + t*4
            print(f"   @{ad:#x} (name{ad-nameptr:+#x}): ({a:.1f},{yv:.1f}) d={math.hypot(a-cx,yv-cy):.1f}")
            hits += 1
    if not hits: print("   (brak)")
