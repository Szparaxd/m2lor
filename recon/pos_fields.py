"""
M4 fix - znajdz prawdziwa pozycje SWIATA encji (m_x, m_y), odrozniajac ja od pozycji
widoku/renderu (ktora obraca sie z kamera).

Test: stoisz NIERUCHOMO i KRECISZ KAMERA. Pola "widoku" sie zmienia, pola "swiata" NIE.
Dla kazdego offsetu liczymy: ile encji ma tam wartosc w zakresie swiata, rozrzut (std)
oraz ile encji ZMIENILO wartosc po obrocie kamery. Prawdziwe m_x/m_y:
  duzo wartosci world-range, DUZY rozrzut, MALO zmian przy obrocie kamery.

JAK UZYC: stan postacia w miejscu (NIE ruszaj sie). Odpal skrypt. W czasie pauzy
tylko KREC KAMERA (prawy przycisk myszy / obrot), postac stoi.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.

Uzycie:
    python recon/pos_fields.py
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
SCAN_LO = 0x240       # przeszukiwany zakres offsetow w obiekcie
SCAN_HI = 0x300
DUMP_SIZE = 0x320
PAUSE = 3.0
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "pos_fields.log")


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
    """vid -> float-array obiektu (DUMP_SIZE/4 floatow)"""
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
            except Exception:
                continue
            if len(raw) != DUMP_SIZE:
                continue
            vid = int(np.frombuffer(raw, dtype="<u4")[OFF_VID // 4])
            out[vid] = np.frombuffer(raw, dtype="<f4")
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
    log(f"[+] gracz=({px:.0f},{py:.0f}). Migawka 1. STOJ i KREC KAMERA przez {PAUSE}s...")
    s1 = snapshot(pm, vtable)
    time.sleep(PAUSE)
    s2 = snapshot(pm, vtable)
    common = [v for v in s2 if v in s1]
    log(f"[+] encji wspolnych: {len(common)}")
    if len(common) < 5:
        log("[!] Za malo encji.")
        return

    # macierze floatow [n, ncol]
    M1 = np.vstack([s1[v] for v in common])
    M2 = np.vstack([s2[v] for v in common])

    log(f"[+] Analiza offsetow {hex(SCAN_LO)}..{hex(SCAN_HI)} "
        f"(szukamy: world-range, duzy std, MALO zmian po obrocie kamery):")
    rows = []
    for off in range(SCAN_LO, SCAN_HI, 4):
        c = off // 4
        col2 = M2[:, c]
        inrange = (col2 > 10000) & (col2 < 60000)
        n_wr = int(np.sum(inrange))
        if n_wr < len(common) * 0.5:
            continue
        vals = col2[inrange]
        std = float(np.std(vals))
        changed = int(np.sum(np.abs(M2[:, c] - M1[:, c]) > 1.0))
        rows.append((off, n_wr, std, changed))

    # sort: najpierw malo zmian, potem duzy std
    rows.sort(key=lambda r: (r[3], -r[2]))
    for off, n_wr, std, changed in rows:
        tag = "  <-- KANDYDAT (swiat)" if changed <= len(common) * 0.1 and std > 300 else ""
        log(f"    off={hex(off)}  world_range={n_wr}/{len(common)}  std={std:8.0f}  "
            f"zmienionych_po_obrocie={changed}{tag}")


if __name__ == "__main__":
    main()
