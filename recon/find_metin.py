"""
M4 - identyfikacja metina przez nieruchomosc.

Metin stoi w miejscu, moby chodza. Robimy 2 migawki w odstepie PAUSE sekund,
dopasowujemy encje po VID (off 0x2b0) i pokazujemy te, ktore NIE zmienily pozycji
= nieruchome (metin / NPC), posortowane wg odleglosci, z wartosciami pol.

JAK UZYC: stan tuz przy kamieniu metin (najlepiej bez mobow dokladnie na nim),
potem uruchom. Najblizsza nieruchoma encja to metin -> jej race@74 = rasa metina.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.

Uzycie:
    python recon/find_metin.py
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
ENT_OFF_X = 0x268
ENT_OFF_Y = 0x26C
OFF_VID = 0x2B0
DUMP_SIZE = 0x400
PAUSE = 2.5
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0
FIELDS = {"vid": 0x2B0, "f5c": 0x5C, "race@74": 0x74, "f154": 0x154, "f158": 0x158}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "find_metin.log")


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE = {0x04, 0x08, 0x40, 0x80}
MAX_USER = 0x7FFFFFFF
MAX_REGION = 512 * 1024 * 1024


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD), ("__a1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("__a2", wintypes.DWORD),
    ]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]


def writable_regions(handle):
    regions, addr, mbi = [], 0, MBI()
    while addr < MAX_USER:
        if not k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if (mbi.State == MEM_COMMIT and not (p & PAGE_GUARD) and not (p & PAGE_NOACCESS)
                and p in WRITABLE and 0 < s <= MAX_REGION):
            regions.append((b, s))
        nxt = b + s
        if nxt <= addr:
            break
        addr = nxt
    return regions


def snapshot(pm, vtable):
    """zwraca dict vid -> (ex, ey, fields)"""
    out = {}
    for rb, rs in writable_regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        nn = (len(buf) // 4) * 4
        u = np.frombuffer(buf[:nn], dtype="<u4")
        for h in np.nonzero(u == vtable)[0]:
            obj = rb + int(h) * 4
            try:
                raw = pm.read_bytes(obj, DUMP_SIZE)
                ex = pm.read_float(obj + ENT_OFF_X)
                ey = pm.read_float(obj + ENT_OFF_Y)
            except Exception:
                continue
            if len(raw) != DUMP_SIZE or not (math.isfinite(ex) and WORLD_MIN < ex < WORLD_MAX):
                continue
            dw = np.frombuffer(raw, dtype="<u4")
            vid = int(dw[OFF_VID // 4])
            fields = {k: int(dw[o // 4]) for k, o in FIELDS.items()}
            out[vid] = (ex, ey, fields)
    return out


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log(f"[!] Nie znalazlem procesu {PROCESS}. Gra wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    vtable = base + VT_OFF
    px = pm.read_float(base + OFF_PLAYER_X)
    py = pm.read_float(base + OFF_PLAYER_Y)
    log("=" * 70)
    log(f"[+] gracz X={px:.0f} Y={py:.0f}. Migawka 1...")
    s1 = snapshot(pm, vtable)
    log(f"[+] s1: {len(s1)} encji. Czekam {PAUSE}s (moby maja sie ruszyc)...")
    time.sleep(PAUSE)
    s2 = snapshot(pm, vtable)
    log(f"[+] s2: {len(s2)} encji. Szukam nieruchomych...")

    stationary = []
    for vid, (ex, ey, f) in s2.items():
        if vid in s1:
            ox, oy, _ = s1[vid]
            moved = math.hypot(ex - ox, ey - oy)
            if moved < 2.0:  # nie drgnelo
                d = math.hypot(ex - px, ey - py)
                stationary.append((d, vid, ex, ey, moved, f))
    stationary.sort()
    log(f"[+] NIERUCHOME encje: {len(stationary)} (najblizsze pierwsze)")
    for d, vid, ex, ey, moved, f in stationary[:15]:
        ew = "W" if ex < px else "E"
        ns = "N" if ey > py else "S"
        fs = " ".join(f"{k}={f[k]}" for k in FIELDS)
        log(f"    {ns}{ew} dist={d:7.1f} pos=({ex:.0f},{ey:.0f}) {fs}")


if __name__ == "__main__":
    main()
