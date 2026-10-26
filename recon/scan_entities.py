"""
M3 - lista encji wokol gracza (snapshot).

Uzywa znalezionej vtable postaci (0x5e5000, zapisanej jako offset od bazy modulu),
skanuje sterte po tym wskazniku i wypisuje wszystkie znalezione postacie z pozycja
i odlegloscia od gracza, posortowane od najblizszej.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona, postac w swiecie.

Uzycie:
    python recon/scan_entities.py
"""
import ctypes
from ctypes import wintypes
import math
import os
from datetime import datetime

import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"

# pozycja gracza (globalna, z M2)
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C

# vtable postaci i offsety pozycji w obiekcie (z M3)
VT_OFF = 0x3D5000     # vtable = baza_modulu + VT_OFF  (0x5e5000 przy bazie 0x210000)
ENT_OFF_X = 0x268
ENT_OFF_Y = 0x26C

WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0
LIST_RADIUS = 20000.0   # ile wokol gracza listowac
SHOW_NEAREST = 50

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "scan_entities.log")


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- walk po regionach pamieci ---
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
    log(f"[+] baza={hex(base)} vtable={hex(vtable)} | gracz X={px:.1f} Y={py:.1f}")

    entities = []
    for rb, rs in writable_regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        u = np.frombuffer(buf[:n], dtype="<u4")
        hits = np.nonzero(u == vtable)[0]
        for h in hits:
            obj = rb + int(h) * 4
            try:
                ex = pm.read_float(obj + ENT_OFF_X)
                ey = pm.read_float(obj + ENT_OFF_Y)
            except Exception:
                continue
            if math.isfinite(ex) and math.isfinite(ey) and WORLD_MIN < ex < WORLD_MAX and WORLD_MIN < ey < WORLD_MAX:
                d = math.hypot(ex - px, ey - py)
                if d <= LIST_RADIUS:
                    entities.append((d, obj, ex, ey))

    entities.sort()
    log(f"[+] Encji w promieniu {LIST_RADIUS:.0f}: {len(entities)}  (pokazuje {min(SHOW_NEAREST, len(entities))} najblizszych)")
    for d, obj, ex, ey in entities[:SHOW_NEAREST]:
        log(f"    dist={d:8.1f}  X={ex:10.1f} Y={ey:10.1f}  obj={hex(obj)}")


if __name__ == "__main__":
    main()
