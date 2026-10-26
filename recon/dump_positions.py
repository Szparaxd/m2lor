"""
Zrzut pozycji wszystkich encji + analiza: ktora para offsetow daje rozklad 2D (nie linie).
Rysuje tekstowy scatter dla wariantu A (0x264,0x26c) i B (0x264,0x268).

WAZNE: terminal JAKO ADMINISTRATOR (lub proces z prawami), gra wlaczona, postac stoi.
Uzycie: python recon/dump_positions.py
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
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "dump_positions.log")
CSV_PATH = os.path.join(LOG_DIR, "positions.csv")


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


def scatter(points, px, py, w=70, h=28):
    """tekstowy wykres: '@'=gracz, '#'=encja"""
    if not points:
        return ["(brak)"]
    xs = [p[0] for p in points] + [px]
    ys = [p[1] for p in points] + [py]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmax - xmin < 1:
        xmax = xmin + 1
    if ymax - ymin < 1:
        ymax = ymin + 1
    grid = [[" "] * w for _ in range(h)]

    def put(x, y, ch):
        cx = int((x - xmin) / (xmax - xmin) * (w - 1))
        cy = int((y - ymin) / (ymax - ymin) * (h - 1))
        cy = h - 1 - cy
        grid[cy][cx] = ch
    for x, y in points:
        put(x, y, "#")
    put(px, py, "@")
    return ["".join(r) for r in grid]


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log("[!] Gra wlaczona?"); return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Brak praw do procesu (uruchom JAKO ADMINISTRATOR)."); return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    vtable = base + VT_OFF
    px = pm.read_float(base + OFF_PLAYER_X)
    py = pm.read_float(base + OFF_PLAYER_Y)
    log("=" * 70)
    log(f"[+] gracz global=({px:.1f},{py:.1f})")

    rowsA, rowsB = [], []
    csv = ["obj,o264,o268,o26c,o270,o274,o278"]
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        n = (len(buf) // 4) * 4
        u = np.frombuffer(buf[:n], dtype="<u4")
        for hh in np.nonzero(u == vtable)[0]:
            obj = rb + int(hh) * 4
            try:
                f264 = pm.read_float(obj + 0x264); f268 = pm.read_float(obj + 0x268)
                f26c = pm.read_float(obj + 0x26C); f270 = pm.read_float(obj + 0x270)
                f274 = pm.read_float(obj + 0x274); f278 = pm.read_float(obj + 0x278)
            except Exception:
                continue
            if not (math.isfinite(f264) and WORLD_MIN < f264 < WORLD_MAX):
                continue
            csv.append(f"{hex(obj)},{f264:.1f},{f268:.1f},{f26c:.1f},{f270:.1f},{f274:.1f},{f278:.1f}")
            rowsA.append((f264, f26c))
            rowsB.append((f264, f268))

    with open(CSV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(csv))

    def stats(rows, name):
        xs = np.array([r[0] for r in rows]); ys = np.array([r[1] for r in rows])
        log(f"[{name}] n={len(rows)}  X[{xs.min():.0f}..{xs.max():.0f} std{xs.std():.0f}]  "
            f"Y[{ys.min():.0f}..{ys.max():.0f} std{ys.std():.0f}]  ratio_std={xs.std()/max(ys.std(),1):.1f}")

    log(f"[+] encji: {len(rowsA)}. CSV -> {CSV_PATH}")
    stats(rowsA, "A 0x264/0x26c")
    stats(rowsB, "B 0x264/0x268")
    log("--- SCATTER A (0x264 X, 0x26c Y) ---")
    for line in scatter(rowsA, px, py):
        log(line)
    log("--- SCATTER B (0x264 X, 0x268 Y) ---  (gracz @ moze byc poza kadrem)")
    bx = [r[0] for r in rowsB]; by = [r[1] for r in rowsB]
    for line in scatter(rowsB, float(np.median(bx)), float(np.median(by))):
        log(line)


if __name__ == "__main__":
    main()
