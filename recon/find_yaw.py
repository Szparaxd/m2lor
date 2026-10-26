"""
Znajdz KAT KAMERY (yaw) - wartosc float, ktora zmienia sie PLYNNIE i MONOTONICZNIE
tylko gdy obracasz kamera (postac stoi). Potem uzyjemy go do obrotu radaru,
zeby "gora radaru" = "przod na ekranie".

JAK UZYC: stan postacia i NIE RUSZAJ sie. Odpal. Przez caly pomiar (dwie pauzy)
obracaj kamere PLYNNIE w JEDNA strone (prawy przycisk myszy), postac stoi.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.
Uzycie: python recon/find_yaw.py
"""
import ctypes
from ctypes import wintypes
import os
import time
from datetime import datetime

import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
PAUSE = 1.5

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "find_yaw.log")


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


def snap(pm):
    """adres(aligned) -> wartosc float, dla wartosci w zakresie katow [-7, 7] (rad) lub [0,360]/[0,6400]"""
    d = {}
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        f = np.frombuffer(buf[:n], dtype="<f4")
        finite = np.isfinite(f)
        inrange = finite & (np.abs(f) < 6500)
        idx = np.nonzero(inrange)[0]
        for i in idx:
            d[rb + int(i) * 4] = float(f[i])
    return d


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log("[!] Gra wlaczona?"); return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Uruchom JAKO ADMINISTRATOR."); return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    log("=" * 70)
    log(f"[+] Migawka 1. OBRACAJ KAMERE plynnie w jedna strone (postac STOI)...")
    s1 = snap(pm)
    time.sleep(PAUSE)
    s2 = snap(pm)
    time.sleep(PAUSE)
    s3 = snap(pm)
    log(f"[+] 3 migawki ({len(s1)}/{len(s2)}/{len(s3)} kandydatow). Szukam monotonicznych...")

    cand = []
    for a, v1 in s1.items():
        if a in s2 and a in s3:
            v2, v3 = s2[a], s3[a]
            d1, d2 = v2 - v1, v3 - v2
            # monotoniczne, wyrazne, podobne tempo = plynny obrot
            if abs(d1) > 1.0 and abs(d2) > 1.0 and (d1 > 0) == (d2 > 0) and abs(d2) < abs(d1) * 4 + 1:
                cand.append((a, v1, v2, v3, d1, d2))

    cand.sort(key=lambda r: -(abs(r[4]) + abs(r[5])))
    log(f"[+] Kandydaci na yaw (monotoniczni przy obrocie kamery): {len(cand)}")
    for a, v1, v2, v3, d1, d2 in cand[:25]:
        rel = f"base+{hex(a - base)}" if base <= a < base + 0x50d000 else "heap"
        log(f"    addr={hex(a)} ({rel})  {v1:9.3f} -> {v2:9.3f} -> {v3:9.3f}  (d {d1:+.2f},{d2:+.2f})")
    log("[i] Yaw: wartosc w sensownym zakresie (np. 0..360 lub 0..6400), plynnie rosnaca. "
        "Najlepiej kandydat 'base+...' (globalny).")


if __name__ == "__main__":
    main()
