"""
M4 - analiza pol obiektu encji, zeby znalezc pole TYPU/RASY i VID.

Dla kazdej encji (znalezionej po vtable) czyta blok DUMP_SIZE bajtow i analizuje
kolumny (offsety) po wszystkich encjach:
  * "kategoryczne" (malo roznych wartosci) -> kandydaci na typ / rase / vnum
     (metin bedzie wartoscia o bardzo malej liczbie wystapien),
  * "unikalne" (kazda encja inna wartosc)  -> kandydat na VID.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona; stan tam, gdzie wokol sa moby I metin.

Uzycie:
    python recon/dump_entity_fields.py
"""
import ctypes
from ctypes import wintypes
from collections import Counter
import math
import os
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
DUMP_SIZE = 0x400          # ile bajtow obiektu analizujemy (0x400 = 256 dwordow)
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "dump_entity_fields.log")


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
        base, size, prot = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if (mbi.State == MEM_COMMIT and not (prot & PAGE_GUARD) and not (prot & PAGE_NOACCESS)
                and prot in WRITABLE and 0 < size <= MAX_REGION):
            regions.append((base, size))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return regions


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
    log("=" * 60)
    log(f"[+] baza={hex(base)} vtable={hex(vtable)} gracz X={px:.0f} Y={py:.0f}")

    # zbierz obiekty encji + ich bloki pamieci
    objs, blocks, pos = [], [], []
    for rb, rs in writable_regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        u = np.frombuffer(buf[:n], dtype="<u4")
        for h in np.nonzero(u == vtable)[0]:
            obj = rb + int(h) * 4
            try:
                raw = pm.read_bytes(obj, DUMP_SIZE)
                ex = pm.read_float(obj + ENT_OFF_X)
                ey = pm.read_float(obj + ENT_OFF_Y)
            except Exception:
                continue
            if len(raw) == DUMP_SIZE and math.isfinite(ex) and WORLD_MIN < ex < WORLD_MAX:
                objs.append(obj)
                blocks.append(np.frombuffer(raw, dtype="<u4"))
                pos.append((ex, ey))

    n = len(objs)
    log(f"[+] encji do analizy: {n}")
    if n < 5:
        log("[!] Za malo encji.")
        return

    M = np.vstack(blocks)  # [n, DUMP_SIZE/4]
    ncol = M.shape[1]

    # --- kolumny "kategoryczne": malo roznych wartosci, wartosci int-podobne ---
    log("[+] KANDYDACI na pole TYPU/RASY (malo roznych wartosci):")
    cat_cols = []
    for c in range(ncol):
        col = M[:, c]
        distinct = np.unique(col)
        nd = distinct.size
        # pomijamy pola pozycji (float) i stale (nd==1) i chaos (nd za duze)
        if 2 <= nd <= 40:
            vmin, vmax = int(col.min()), int(col.max())
            if 0 <= vmin and vmax < 5_000_000:  # wartosci jak vnum/typ
                cat_cols.append((nd, c))
    cat_cols.sort()
    for nd, c in cat_cols[:25]:
        col = M[:, c]
        hist = Counter(col.tolist()).most_common(8)
        hist_str = "  ".join(f"{v}:{cnt}" for v, cnt in hist)
        log(f"    off={hex(c*4)}  roznych={nd}   {hist_str}")

    # --- kolumny "unikalne": kandydat na VID ---
    log("[+] KANDYDACI na VID (prawie kazda encja inna wartosc):")
    for c in range(ncol):
        col = M[:, c]
        nd = np.unique(col).size
        if nd >= n * 0.9:
            vmin, vmax = int(col.min()), int(col.max())
            if 0 < vmin and vmax < 50_000_000:  # VID to umiarkowany int, nie wskaznik
                log(f"    off={hex(c*4)}  roznych={nd}/{n}  min={vmin} max={vmax}")


if __name__ == "__main__":
    main()
