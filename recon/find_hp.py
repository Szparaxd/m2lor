"""Diagnostyka HP: znajduje tablice punktow gracza w pamieci i wypisuje DUZO logow.
Po to, gdy radar nie potrafi znalezc HP (np. vCore - brak RTTI CPythonPlayer, inny uklad POINT_).

Dwa tryby:

1) KSZTALT (bez argumentow) - jak radar: szuka dlugich runow tej samej wartosci (= klucz XOR na
   zerowych polach) i probuje zweryfikowac naglowek. Wypisuje kandydatow i KTORE warunki
   looks_like_points przechodza/odpadaja - widac, czemu radar odrzuca.

2) PARA HP (--hp H --maxhp M, odczytane z gry) - najpewniejszy. Szuka dwoch SASIEDNICH dwordow,
   ktorych XOR == H^M. Przy szyfrowaniu XOR stalym kluczem: (H^k)^(M^k) == H^M - KLUCZ SIE SKRACA,
   wiec pare (HP,maxHP) znajdziemy niezaleznie od klucza i ukladu. Potem liczymy klucz i
   rozszyfrowujemy okolice -> widac prawdziwy uklad POINT_ (gdzie jest poziom, HP, staty...).

Uruchom (terminal JAKO ADMIN, gra wlaczona, postac zalogowana):
    cd "C:/Metin/radar-project"; python recon/find_hp.py -e vCore
    cd "C:/Metin/radar-project"; python recon/find_hp.py -e vCore --hp 11714 --maxhp 20000
"""
import argparse
import ctypes
import os
import struct
import sys
from ctypes import wintypes

import numpy as np
import pymem

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autopot

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
WRITABLE = {0x04, 0x08, 0x40, 0x80}


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
    while addr < 0x7FFFFFFF:
        if not k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, s, p = (mbi.BaseAddress or 0), (mbi.RegionSize or 0), mbi.Protect
        if (mbi.State == 0x1000 and not (p & 0x100) and not (p & 0x01)
                and p in WRITABLE and 0 < s <= 512 * 1024 * 1024):
            regions.append((b, s))
        nxt = b + s
        if nxt <= addr:
            break
        addr = nxt
    return regions


def dec(vals, key):
    """Lista uint32 -> lista int32 po XOR kluczem."""
    return [struct.unpack("<i", struct.pack("<I", int(x) ^ key))[0] for x in vals]


def explain_points(d):
    """Zwraca (ile_ok, liste_opisow) dla warunkow looks_like_points - widac co pasuje, co nie."""
    L, HP, MHP, SP, MSP = autopot.P_LEVEL, autopot.P_HP, autopot.P_MAX_HP, autopot.P_SP, autopot.P_MAX_SP
    stats = autopot.P_STATS
    checks = [
        ("d[0]==0 (klucz)",            d[0] == 0),
        ("1<=poziom<=250",             1 <= d[L] <= 250),
        ("0<=HP<=maxHP",               0 <= d[HP] <= d[MHP]),
        ("50<=maxHP<2e6",              50 <= d[MHP] < 2_000_000),
        ("0<=PM<=maxPM<2e6, maxPM>0",  0 <= d[SP] <= d[MSP] < 2_000_000 and d[MSP] > 0),
        ("300<=wymPD<2^31",            300 <= d[4] < 2**31),
        ("0<=PD<wymPD",                0 <= d[3] < d[4]),
        ("staty 1..2000 (idx12-15)",   all(1 <= d[i] <= 2000 for i in stats)),
        ("ogon d[9:16] >=3 roznych",   len(set(d[9:16])) >= 3),
    ]
    ok = sum(1 for _, v in checks if v)
    return ok, checks


def mode_pair(pm, regions, hp, mhp):
    """Szuka dwordow X, Y odleglych o 'gap' (1..3), gdzie X^Y == hp^mhp. Przy szyfrowaniu stalym
    kluczem klucz sie skraca, wiec dziala tez bez znajomosci klucza. Dla vCore (bez szyfrowania)
    prawdziwa para ma KLUCZ=0. Zbiera trafienia i wypisuje okolice; key=0 na gorze."""
    target = (hp ^ mhp) & 0xFFFFFFFF
    print("\n=== TRYB PARA HP: szukam X,Y (odstep 1-3) z X^Y == %#010x (= %d ^ %d) ==="
          % (target, hp, mhp))
    hits = []       # (key, gap, order, addr, window_decoded, base_rel)
    for rb, rs in regions:
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        if u.size < 4:
            continue
        for gap in (1, 2, 3):
            idxs = np.nonzero((u[:-gap] ^ u[gap:]) == np.uint32(target))[0]
            for i in idxs:
                i = int(i)
                a = int(u[i])
                # a moze byc HP albo maxHP; klucz wylicza sie z zalozenia, ktore to
                for order, va in (("HP,maxHP", hp), ("maxHP,HP", mhp)):
                    key = a ^ va
                    lo = max(0, i - 20)
                    window = dec(u[lo:i + 24], key)
                    hits.append((key, gap, order, rb + i * 4, window, lo - i))
    if not hits:
        print("Brak trafien. Czy HP/maxHP sa DOKLADNE i aktualne (po buffach/zdjeciu HP+)?")
        print("Sprobuj tez: python recon/find_hp.py -e <exe> --hp H --maxhp M --shape")
        return 0
    # key=0 najpierw (vCore nieszyfrowany), potem mniejsze klucze; ogranicz wydruk
    hits.sort(key=lambda h: (h[0] != 0, h[0]))
    print("Trafien: %d. Najbardziej prawdopodobne (key=0 = brak szyfrowania):" % len(hits))
    seen = set()
    shown = 0
    for key, gap, order, addr, window, base in hits:
        if addr in seen:        # ta sama para pod dwoma 'order' - pokaz raz
            continue
        seen.add(addr)
        print("\n[HIT] addr=%#010x  gap=%d  zalozenie=%s  KLUCZ=%#010x" % (addr, gap, order, key))
        for j, val in enumerate(window):
            rel = base + j
            if -16 <= rel <= 18:
                mark = "  <== X (%s)" % order.split(",")[0] if rel == 0 else (
                    "  <== Y (+%d)" % gap if rel == gap else "")
                print("    rel %+3d : %12d%s" % (rel, val, mark))
        shown += 1
        if shown >= 10:
            print("\n(...wiecej trafien pominieto; patrz plik logu)")
            break
    return len(hits)


def mode_shape(pm, regions, min_run):
    print("\n=== TRYB KSZTALT: szukam runow klucza (>=%d) i weryfikuje naglowek ===" % min_run)
    cands = []
    for rb, rs in regions:
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        n = u.shape[0]
        if n < 300:
            continue
        changes = np.nonzero(u[1:] != u[:-1])[0] + 1
        starts = np.concatenate(([0], changes))
        runs = np.concatenate((changes, [n])) - starts
        for idx in np.nonzero(runs >= min_run)[0]:
            run_start = int(starts[idx])
            k = int(u[run_start])
            for p in range(run_start - 1, max(0, run_start - 32) - 1, -1):
                if int(u[p]) != k or p + 16 > n:
                    continue
                d = dec(u[p:p + 16], k)
                ok, checks = explain_points(d)
                rep = int((u[p + 1:p + 256] == np.uint32(k)).sum())
                cands.append((ok, rep, rb + p * 4, k, d, checks))
                break
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    print("Kandydatow: %d. Najlepsze (wg liczby spelnionych warunkow, potem powt. klucza):" % len(cands))
    for ok, rep, addr, k, d, checks in cands[:8]:
        print("\n  addr=%#010x klucz=%#010x  warunki_OK=%d/9  powt.klucza=%d" % (addr, k, ok, rep))
        print("    pierwsze 16 (odszyfrowane): %s" % d)
        for name, val in checks:
            if not val:
                print("    [X] %s" % name)
    if not cands:
        print("Zero kandydatow - moze inne szyfrowanie/brak dlugiego runu. Uzyj --hp/--maxhp.")


class _Tee:
    """Pisze jednoczesnie na ekran i do pliku - zeby nie trzeba bylo przeklejac wynikow."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "logs", "find_hp.txt")


def main():
    ap = argparse.ArgumentParser(description="Diagnostyka HP (Metin2).")
    ap.add_argument("-e", "--exe", "-exe", default="GameCoreTop.exe")
    ap.add_argument("--hp", type=int, default=None, help="aktualne HP z gry")
    ap.add_argument("--maxhp", type=int, default=None, help="aktualne maxHP z gry")
    ap.add_argument("--min-run", type=int, default=16, help="min. dlugosc runu klucza (tryb ksztalt)")
    ap.add_argument("--shape", action="store_true", help="wymus tryb ksztalt nawet z --hp")
    args = ap.parse_args()
    if not args.exe.lower().endswith(".exe"):
        args.exe += ".exe"

    logf = open(LOG_PATH, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, logf)
    try:
        print("[wynik zapisywany do %s]" % LOG_PATH)
        try:
            pm = pymem.Pymem(args.exe)
        except pymem.exception.ProcessNotFound:
            print("[!] Nie ma procesu %s (gra wlaczona? -e NAZWA)" % args.exe)
            return
        except pymem.exception.CouldNotOpenProcess:
            print("[!] Uruchom terminal JAKO ADMINISTRATOR.")
            return
        regions = writable_regions(pm.process_handle)
        total = sum(s for _, s in regions)
        print("Proces %s (PID %d): %d regionow zapisywalnych, razem %.1f MB"
              % (args.exe, pm.process_id, len(regions), total / 1048576.0))
        if args.hp is not None and args.maxhp is not None:
            mode_pair(pm, regions, args.hp, args.maxhp)
            if args.shape:
                mode_shape(pm, regions, args.min_run)
        else:
            print("(bez --hp/--maxhp -> tylko tryb ksztalt; dla pewnosci podaj HP i maxHP z gry)")
            mode_shape(pm, regions, args.min_run)
    finally:
        sys.stdout = sys.__stdout__
        logf.close()
    print("[gotowe] pelny wynik w %s" % LOG_PATH)


if __name__ == "__main__":
    main()
