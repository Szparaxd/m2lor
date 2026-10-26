"""
M4 - znajdz ENCJE GRACZA przez korelacje ruchu + ustal zaleznosc ukladow wspolrzednych.

Problem: pozycja gracza z globalu (M1/M2) rozni sie od pozycji encji (zwlaszcza na Y).
Rozwiazanie: gracz tez jest encja. Mierzymy, jak zmienia sie globalna pozycja gracza,
i szukamy encji, ktora poruszyla sie TAK SAMO -> to encja gracza. Potem porownujemy
jej surowa pozycje (off 0x268/0x26c) z globalna -> znamy transformacje (offset / znak Y).

JAK UZYC: kliknij mysza DALEKI punkt na mapie, zeby postac sama tam biegla,
i W TRAKCIE biegu odpal skrypt (postac ma sie ruszac przez caly pomiar).

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona.

Uzycie:
    python recon/find_player_entity.py
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
PAUSE = 2.0
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0
MOVE_MIN = 30.0        # minimalny ruch, zeby uznac encje za "ruszajaca sie"
MATCH_TOL = 40.0       # tolerancja dopasowania wektora ruchu

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "find_player_entity.log")


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
            vid = int(np.frombuffer(raw, dtype="<u4")[OFF_VID // 4])
            out[vid] = (ex, ey, obj)
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

    gx1 = pm.read_float(base + OFF_PLAYER_X); gy1 = pm.read_float(base + OFF_PLAYER_Y)
    s1 = snapshot(pm, vtable)
    log("=" * 70)
    log(f"[+] global1=({gx1:.0f},{gy1:.0f}) encji={len(s1)}. Postac ma BIEC. Czekam {PAUSE}s...")
    time.sleep(PAUSE)
    gx2 = pm.read_float(base + OFF_PLAYER_X); gy2 = pm.read_float(base + OFF_PLAYER_Y)
    s2 = snapshot(pm, vtable)

    gdx, gdy = gx2 - gx1, gy2 - gy1
    gmove = math.hypot(gdx, gdy)
    log(f"[+] global2=({gx2:.0f},{gy2:.0f}) ruch gracza=({gdx:+.0f},{gdy:+.0f}) |{gmove:.0f}|")
    if gmove < MOVE_MIN:
        log("[!] Gracz sie nie poruszyl. Kliknij daleki punkt i odpal w trakcie biegu.")
        return

    # kandydaci: encje z podobnym |dx| i |dy| (uwzgledniamy mozliwa negacje Y)
    log("[+] Encje z ruchem pasujacym do gracza (kandydat na encje gracza):")
    found = []
    for vid, (ex2, ey2, obj) in s2.items():
        if vid not in s1:
            continue
        ex1, ey1, _ = s1[vid]
        edx, edy = ex2 - ex1, ey2 - ey1
        if math.hypot(edx, edy) < MOVE_MIN:
            continue
        dx_ok = abs(edx - gdx) < MATCH_TOL
        dy_same = abs(edy - gdy) < MATCH_TOL
        dy_neg = abs(edy + gdy) < MATCH_TOL
        if dx_ok and (dy_same or dy_neg):
            found.append((vid, ex2, ey2, edx, edy, "Y-ten-sam" if dy_same else "Y-NEGOWANE"))

    for vid, ex2, ey2, edx, edy, ymode in found:
        log(f"    vid={vid} pos_encji=({ex2:.0f},{ey2:.0f}) ruch=({edx:+.0f},{edy:+.0f}) {ymode}")
        log(f"        global gracza=({gx2:.0f},{gy2:.0f})  => roznica X={gx2-ex2:+.0f} Y={gy2-ey2:+.0f}")
    if not found:
        log("[!] Nie znalazlem encji gracza. Mozliwe, ze gracz ma inna vtable (hero osobno).")


if __name__ == "__main__":
    main()
