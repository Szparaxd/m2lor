"""
M4 fix - znajdz obiekt GRACZA (hero) po jego pozycji = pozycja globalna.
Szukamy obiektu, ktory ma 0x264 ~ globalX ORAZ 0x26c ~ globalY (gracz stoi na swojej pozycji).
Czytamy jego 0x264/0x268/0x26c + vtable -> rozstrzygamy, ktore pole to prawdziwe world-Y
i dostajemy spojny srodek radaru.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.
Uzycie: python recon/find_hero.py
"""
import ctypes
from ctypes import wintypes
import os
from datetime import datetime

import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C
OFF_X = 0x264
OFF_MID = 0x268
OFF_Z = 0x26C
TOL = 8.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "find_hero.log")


def log(m):
    line = f"{datetime.now().strftime('%H:%M:%S')}  {m}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
        if n <= a:
            break
        a = n
    return out


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log("[!] Gra wlaczona?"); return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Uruchom JAKO ADMINISTRATOR."); return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    msize = pymem.process.module_from_name(pm.process_handle, PROCESS).SizeOfImage
    mod_lo, mod_hi = base, base + msize
    px = pm.read_float(base + OFF_PLAYER_X)
    py = pm.read_float(base + OFF_PLAYER_Y)
    log("=" * 70)
    log(f"[+] GRACZ global X={px:.1f} Y={py:.1f}. Szukam obiektow na tej pozycji...")

    hits = []
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        f = np.frombuffer(buf[:n], dtype="<f4")
        # miejsca, gdzie wartosc ~ px (kandydat na obj+0x264)
        idx = np.nonzero(np.abs(f - px) < TOL)[0]
        for i in idx:
            xaddr = rb + int(i) * 4
            obj = xaddr - OFF_X
            try:
                vx = pm.read_float(obj + OFF_X)
                vmid = pm.read_float(obj + OFF_MID)
                vz = pm.read_float(obj + OFF_Z)
                vt = pm.read_uint(obj)
            except Exception:
                continue
            if abs(vz - py) < 40.0:  # 0x26c ~ globalY
                in_mod = mod_lo <= vt < mod_hi
                hits.append((obj, vt, in_mod, vx, vmid, vz))

    log(f"[+] obiektow na pozycji gracza: {len(hits)}")
    for obj, vt, in_mod, vx, vmid, vz in hits[:20]:
        rel = f"base+{hex(vt - base)}" if in_mod else "NIE-modul"
        log(f"    obj={hex(obj)} vtable={hex(vt)} ({rel})  0x264={vx:.1f} 0x268={vmid:.1f} 0x26c={vz:.1f}")
    log("[i] Hero = ten z vtable w module. Jego 0x268 to prawdziwe world-Y gracza "
        "(porownaj z 0x268 mobow, by wybrac plaszczyzne).")


if __name__ == "__main__":
    main()
