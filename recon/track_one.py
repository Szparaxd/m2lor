"""
M4 fix - rozstrzygniecie: czy 0x264/0x26c to swiat czy widok (kamera)?
Sledzi JEDNEGO moba (najblizszego) przez obrot kamery o 180 st (postac stoi).
Pola, ktore sie NIE zmienily = swiat. Pola world-range, ktore sie zmienily = widok/kamera.

JAK UZYC: stan postacia, NIE RUSZAJ sie. Odpal. W czasie pauzy obroc kamere o ~180 st
(prawy przycisk myszy), postac ma stac w miejscu.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.
Uzycie: python recon/track_one.py
"""
import ctypes
from ctypes import wintypes
import math
import os
import time
from datetime import datetime

import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C
VT_OFF = 0x3D5000
OFF_VID = 0x2B0
OFF_X = 0x264
OFF_Y = 0x26C
DUMP = 0x2C0
PAUSE = 5.0
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "track_one.log")


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


def find_nearest(pm, vtable, px, py):
    best = None
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for hh in np.nonzero(u == vtable)[0]:
            obj = rb + int(hh) * 4
            try:
                ex = pm.read_float(obj + OFF_X); ey = pm.read_float(obj + OFF_Y)
                vid = pm.read_uint(obj + OFF_VID)
            except Exception:
                continue
            if math.isfinite(ex) and WORLD_MIN < ex < WORLD_MAX:
                d = math.hypot(ex - px, ey - py)
                if d > 200 and (best is None or d < best[0]):   # pomijamy ew. kopie na graczu
                    best = (d, obj, vid)
    return best


def obj_by_vid(pm, vtable, vid):
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for hh in np.nonzero(u == vtable)[0]:
            obj = rb + int(hh) * 4
            try:
                if pm.read_uint(obj + OFF_VID) == vid:
                    return obj
            except Exception:
                continue
    return None


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log("[!] Gra wlaczona?"); return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Uruchom JAKO ADMINISTRATOR."); return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    vtable = base + VT_OFF
    px = pm.read_float(base + OFF_PLAYER_X); py = pm.read_float(base + OFF_PLAYER_Y)
    near = find_nearest(pm, vtable, px, py)
    if not near:
        log("[!] Brak moba."); return
    d, obj, vid = near
    raw1 = np.frombuffer(pm.read_bytes(obj, DUMP), dtype="<f4").copy()
    log("=" * 70)
    log(f"[+] sledze moba vid={vid} obj={hex(obj)} dist={d:.0f}. "
        f"OBROC KAMERE o 180 st (postac STOI) przez {PAUSE}s...")
    time.sleep(PAUSE)
    obj2 = obj_by_vid(pm, vtable, vid) or obj
    raw2 = np.frombuffer(pm.read_bytes(obj2, DUMP), dtype="<f4").copy()

    log(f"[+] obj po obrocie: {hex(obj2)}. Offsety world-range i ich zmiana:")
    for c in range(DUMP // 4):
        v1, v2 = float(raw1[c]), float(raw2[c])
        if (WORLD_MIN < abs(v1) < WORLD_MAX) or (WORLD_MIN < abs(v2) < WORLD_MAX):
            ch = abs(v2 - v1)
            tag = "  <-- NIEZMIENNE (swiat?)" if ch < 2.0 else ("  <<< zmienilo sie (kamera?)" if ch > 20 else "")
            log(f"    off={hex(c*4)}  przed={v1:11.1f}  po={v2:11.1f}  d={ch:8.1f}{tag}")


if __name__ == "__main__":
    main()
