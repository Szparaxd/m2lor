"""
M4 - profilowanie encji, zeby przypisac typy do wartosci pol.

Wypisuje:
  * encje "wyjatkowe" (off 0x5c != 8) = podejrzani o bycie metinem/NPC,
  * kilka "zwyklych" (off 0x5c == 8) = moby,
z wartosciami pol kandydatow + pozycja, odleglosc i KIERUNEK wzgledem gracza
(W=zachod/mniejsze X, E=wschod, N=polnoc/wieksze Y, S=poludnie).

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona, to samo miejsce (metin + wilki).

Uzycie:
    python recon/profile_entities.py
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
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C
VT_OFF = 0x3D5000
ENT_OFF_X = 0x268
ENT_OFF_Y = 0x26C
DUMP_SIZE = 0x400
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

# offsety do pokazania (z analizy dump_entity_fields)
FIELDS = {
    "vid@2b0": 0x2B0,
    "f5c": 0x5C,
    "race@74": 0x74,
    "f8": 0x08,
    "f24": 0x24,
    "f154": 0x154,
    "f158": 0x158,
    "f23c": 0x23C,
}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "profile_entities.log")


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


def direction(dx, dy):
    ew = "W" if dx < 0 else "E"
    ns = "N" if dy > 0 else "S"
    return f"{ns}{ew}"


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
    log(f"[+] gracz X={px:.0f} Y={py:.0f}")

    ents = []
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
            fields = {name: int(dw[off // 4]) for name, off in FIELDS.items()}
            d = math.hypot(ex - px, ey - py)
            ents.append((obj, ex, ey, d, fields))

    specials = [e for e in ents if e[4]["f5c"] != 8]
    normals = [e for e in ents if e[4]["f5c"] == 8]
    specials.sort(key=lambda e: e[3])
    normals.sort(key=lambda e: e[3])

    def show(e):
        obj, ex, ey, d, f = e
        fs = " ".join(f"{k}={f[k]}" for k in FIELDS)
        log(f"    {direction(ex - px, ey - py)} dist={d:7.1f} pos=({ex:.0f},{ey:.0f}) {fs}")

    log(f"[+] WYJATKOWE (f5c != 8): {len(specials)}")
    for e in specials:
        show(e)
    log(f"[+] ZWYKLE (f5c == 8), 6 najblizszych z {len(normals)}:")
    for e in normals[:6]:
        show(e)


if __name__ == "__main__":
    main()
