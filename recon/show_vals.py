"""
M4 fix - pokaz surowe wartosci pol pozycji dla kilku encji + pozycje gracza,
zeby jednoznacznie dobrac offsety X i Y encji.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.
Uzycie: python recon/show_vals.py
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
OFFS = [0x260, 0x264, 0x268, 0x26C, 0x270, 0x274, 0x278, 0x27C, 0x280, 0x284]
DUMP = 0x290
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "show_vals.log")


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
    vtable = base + VT_OFF
    px = pm.read_float(base + OFF_PLAYER_X)
    py = pm.read_float(base + OFF_PLAYER_Y)
    log("=" * 70)
    log(f"[+] GRACZ global:  X={px:.1f}  Y={py:.1f}")
    log("[+] offsety: " + "  ".join(hex(o) for o in OFFS))

    ents = []
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for hh in np.nonzero(u == vtable)[0]:
            obj = rb + int(hh) * 4
            try:
                raw = pm.read_bytes(obj, DUMP)
            except Exception:
                continue
            if len(raw) == DUMP:
                f = np.frombuffer(raw, dtype="<f4")
                ents.append((obj, f))

    log(f"[+] encji: {len(ents)}. Pokazuje 10 pierwszych (wartosci float pod offsetami):")
    for obj, f in ents[:10]:
        vals = "  ".join(f"{f[o // 4]:9.1f}" for o in OFFS)
        log(f"    obj={hex(obj)}  {vals}")


if __name__ == "__main__":
    main()
