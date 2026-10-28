"""Launcher: uruchom GRE, poczekaj az postac bedzie W SWIECIE, potem odpal RADAR.

Radar (radar_map.py) przy starcie robi rescan()+calibrate(), a kalibracja WYMAGA bohatera w swiecie
(na ekranie logowania/wyboru postaci jeszcze go nie ma). Dlatego launcher:
  1. uruchamia klienta gry (jesli nie dziala),
  2. czeka, az w pamieci pojawi sie obiekt bohatera z sensowna pozycja = postac weszla w swiat
     (login + wybor postaci robisz recznie - hasla NIE wpisuje za Ciebie),
  3. dopiero wtedy uruchamia radar (ktory wstrzykuje DLL i kalibruje offsety).

Uzycie:  py -3 recon/launch.py            (domyslnie GameCoreTop)
         py -3 recon/launch.py -e vCore.exe --game "C:\\...\\vCore.exe"
         py -3 recon/launch.py -e vCore.exe --game ... --new   (KOLEJNY klient obok dzialajacych)
Radar dostaje PID klienta (-p), wiec przy kilku klientach kazdy radar trzyma sie swojego okna gry.
Uruchom z terminala JAKO ADMIN (radar potrzebuje OpenProcess / wstrzykniecia DLL).
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import numpy as np
import pymem
import pymem.process

import clients
import rtti

HERO_CLASS = "CGameEventManager"   # pozycja gracza: X@+0x8 ; worldY = -(+0xC) (jak w radar_map)
HERO_POS = 0x8
WORLD_MIN, WORLD_MAX = 1.0, 10_000_000.0
DEFAULT_EXE = "GameCoreTop.exe"
DEFAULT_GAME = r"C:\Metin\Terenzo 2024 - Muchomor Upgrade\GameCore\Klient Terenzo 2024\GameCoreTop.exe"
POLL_SEC = 2.0
TIMEOUT_SEC = 600          # ile maks. czekamy na wejscie w swiat (10 min)

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


def in_world(proc_name, pid):
    """True, gdy w procesie gry (PID) jest obiekt bohatera z sensowna pozycja swiata (postac w grze)."""
    try:
        pm = pymem.Pymem(pid)
    except Exception:
        return False                      # proces jeszcze nie wstal
    try:
        mod = pymem.process.module_from_name(pm.process_handle, proc_name)
        base, size = mod.lpBaseOfDll, mod.SizeOfImage
        hero_vt = rtti.find_vtables(pm, base, size, [HERO_CLASS])[HERO_CLASS]
    except Exception:
        return False                      # modul/RTTI jeszcze niegotowe (wczesny start)
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for h in np.nonzero(u == hero_vt)[0]:
            o = rb + int(h) * 4
            try:
                x = pm.read_float(o + HERO_POS)
                y = pm.read_float(o + HERO_POS + 4)
            except Exception:
                continue
            if all(map(lambda v: v == v, (x, y))) and WORLD_MIN < abs(x) < WORLD_MAX:
                return True                # bohater z realna pozycja = jestesmy w swiecie
    return False


def main():
    ap = argparse.ArgumentParser(description="Uruchom gre + radar (radar startuje po wejsciu w swiat).")
    ap.add_argument("-e", "--exe", default=DEFAULT_EXE, help="nazwa procesu gry")
    ap.add_argument("--game", default=DEFAULT_GAME, help="pelna sciezka do exe gry")
    ap.add_argument("--new", action="store_true", help="uruchom KOLEJNEGO klienta, nawet gdy jakis dziala")
    ap.add_argument("-p", "--pid", type=int, default=None, help="podlacz radar do dzialajacego klienta o PID")
    args = ap.parse_args()
    exe = args.exe if args.exe.lower().endswith(".exe") else args.exe + ".exe"

    recon_dir = os.path.dirname(os.path.abspath(__file__))

    # 1) wybierz klienta albo uruchom nowego. Gra WYMAGA swojego katalogu jako cwd (packi).
    #    Nowy klient = PID, ktorego nie bylo przed startem (launcher gry moze podmienic proces).
    before = {p for p, _ in clients.list_clients((exe,))}
    pid = args.pid
    if pid is None and before and not args.new:
        try:
            pid, _ = clients.pick(exe)
        except LookupError as e:
            print("[launch] %s\n[launch] albo --new, by uruchomic kolejnego klienta" % e)
            return 1
        print("[launch] %s juz dziala (PID %d) - nie uruchamiam drugiej instancji" % (exe, pid))
    elif pid is None:
        if not os.path.isfile(args.game):
            print("[launch] BLAD: nie znalazlem gry: %s" % args.game)
            return 1
        print("[launch] uruchamiam gre: %s" % args.game)
        subprocess.Popen([args.game], cwd=os.path.dirname(args.game))

    # 2) czekaj na wejscie w swiat (login + wybor postaci robisz recznie)
    print("[launch] czekam, az postac wejdzie w swiat (zaloguj sie i wybierz postac)...")
    t0 = time.time()
    while time.time() - t0 < TIMEOUT_SEC:
        if pid is None:
            new = sorted({p for p, _ in clients.list_clients((exe,))} - before)
            pid = new[-1] if new else None
            if pid:
                print("[launch] nowy klient: PID %d" % pid)
        if pid and in_world(exe, pid):
            print("[launch] postac w swiecie - uruchamiam radar")
            break
        time.sleep(POLL_SEC)
    else:
        print("[launch] limit czasu - nie wykryto postaci w swiecie. Radar nie uruchomiony.")
        return 1

    # 3) odpal radar (sam wstrzyknie DLL i skalibruje offsety)
    radar = os.path.join(recon_dir, "radar_map.py")
    print("[launch] start: %s -e %s -p %d" % (radar, exe, pid))
    subprocess.Popen([sys.executable, radar, "-e", exe, "-p", str(pid)], cwd=recon_dir)
    print("[launch] gotowe - radar wystartowal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
