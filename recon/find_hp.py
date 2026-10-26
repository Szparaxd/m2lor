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


def f32bits(x):
    """Bity float32 danej liczby jako uint32 (do szukania HP trzymanego jako float)."""
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def mode_pair(pm, regions, hp, mhp):
    """Szuka HP obok maxHP (odstep 1-3), próbujac obu kodowan: int32 i float32. Dla vCore
    (nieszyfrowane) to pewny sposob - o ile HP != maxHP. Wypisuje okolice w obu interpretacjach."""
    print("\n=== TRYB PARA HP (int+float, okno +-8, tez wymieszane): HP=%d  maxHP=%d ===" % (hp, mhp))
    if hp == mhp:
        print("HP == maxHP -> nie da sie odroznic pary. Daj sie TRAFIC (HP < maxHP) i podaj --hp aktualne.")
        return 0
    enc_hp = [("int", hp & 0xFFFFFFFF), ("float", f32bits(hp))]
    enc_mhp = [("int", mhp & 0xFFFFFFFF), ("float", f32bits(mhp))]
    hits = 0
    seen = set()
    for rb, rs in regions:
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        n = u.size
        # pozycje HP (w dowolnym kodowaniu)
        hp_pos = {}
        for lbl, V in enc_hp:
            for i in np.nonzero(u == np.uint32(V))[0]:
                hp_pos[int(i)] = lbl
        for i, lbl in hp_pos.items():
            lo = max(0, i - 8)
            hi = min(n, i + 9)
            for lbl2, V2 in enc_mhp:
                for j in range(lo, hi):
                    if j != i and int(u[j]) == V2:
                        addr = rb + i * 4
                        if addr in seen:
                            continue
                        seen.add(addr)
                        hits += 1
                        print("\n[HIT] HP@%#010x (%s)  maxHP %+d dword (%s)" % (addr, lbl, j - i, lbl2))
                        for kk in range(max(0, i - 16), min(n, i + 20)):
                            raw = int(u[kk])
                            iv = struct.unpack("<i", struct.pack("<I", raw))[0]
                            fv = struct.unpack("<f", struct.pack("<I", raw))[0]
                            fvs = (" float=%.3f" % fv) if 1e-3 < abs(fv) < 1e9 else ""
                            mark = "  <== HP" if kk == i else ("  <== maxHP" if kk == j else "")
                            print("   %+3d: int=%12d%s%s" % (kk - i, iv, fvs, mark))
                        if hits >= 15:
                            print("\n(limit trafien - patrz plik)")
                            return hits
    if not hits:
        print("Brak pary HP/maxHP (int ani float, okno +-8). Czy HP i maxHP sa DOKLADNE i ROZNE?")
    return hits


def _dump_anchor_neighbourhoods(pm, pos, occ, labelof, span=16, limit=24):
    """Wypisuje okolice (+-span dwordow) wystapien najbardziej wyroznikowej OBECNEJ kotwicy -
    zebym mogl recznie rozpoznac tablice postaci (ta z poziomem/statami/PM obok)."""
    present = sorted((occ[a], a) for a in pos if occ[a] > 0)   # najmniej liczna = najbardziej unikatowa
    if not present:
        return
    _, a = present[0]
    print("\n--- Okolice wystapien kotwicy %d (%s), do %d sztuk ---"
          % (a, labelof.get(a, "?"), limit))
    for n, addr in enumerate(pos[a][:limit]):
        try:
            raw = pm.read_bytes(addr - span * 4, (2 * span + 1) * 4)
        except Exception:
            continue
        u = np.frombuffer(raw[:(len(raw) // 4) * 4], dtype="<u4")
        vals = dec(u, 0)
        print("\n[#%d @%#010x]" % (n + 1, addr))
        for j, val in enumerate(vals):
            rel = j - span
            tag = ("  <== %s" % labelof[val]) if (val in labelof and val != 0) else ""
            print("   %+3d: %12d%s" % (rel, val, tag))


def mode_known(pm, regions, known):
    """Lokalizuje tablice punktow po KILKU znanych wartosciach z karty postaci (vCore nieszyfrowany).
    Kotwiczy na najbardziej wyroznikowej liczbie (np. maxEXP), a potem w oknie +-48 liczy, ile innych
    znanych wartosci tam lezy. Okno z najwieksza liczba trafien = tablica punktow. Wypisuje je z
    indeksami wzglednymi i etykietami -> widac prawdziwy uklad POINT_ tego klienta."""
    items = [(l, v) for l, v in known.items() if v is not None]
    vals = set(v for _, v in items)
    labelof = {}
    for l, v in items:
        labelof.setdefault(v, l)
    # kotwice = wartosci wyroznikowe (probujemy KILKU, bo np. wymPD moze nie byc trzymane w strukturze)
    anchor_vals = sorted({v for _, v in items if abs(v) > 1000}, reverse=True)
    if not anchor_vals:
        anchor_vals = sorted({v for _, v in items if abs(v) > 100}, reverse=True)
    print("\n=== TRYB ZNANE WARTOSCI: kotwice=%s; znane=%s ==="
          % (anchor_vals, sorted(vals)))
    occ = {a: 0 for a in anchor_vals}
    pos = {a: [] for a in anchor_vals}        # adresy wystapien (do zrzutu okolic), limit na kotwice
    results = []
    for rb, rs in regions:
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for a in anchor_vals:
            idxs = np.nonzero(u == np.uint32(a & 0xFFFFFFFF))[0]
            occ[a] += len(idxs)
            for i in idxs:
                i = int(i)
                if len(pos[a]) < 60:
                    pos[a].append(rb + i * 4)
                lo = max(0, i - 48)
                win = dec(u[lo:min(u.size, i + 48)], 0)
                cnt = len(vals & set(win))
                if cnt >= 3:
                    results.append((cnt, a, rb + i * 4, lo - i, win))
    print("Ile razy kotwice wystepuja w pamieci (0 = NIE trzymane jako zwykly int32):")
    for a in anchor_vals:
        print("  %-10d %-8s : %d razy" % (a, "(%s)" % labelof.get(a, "?"), occ[a]))
    if not results:
        print("\nBrak okna z >=3 znanymi wartosciami blisko siebie.")
        print("-> wymPD/EXP moga byc z tabeli lub 64-bit; ale HP jest obecne - pokazuje okolice.")
        _dump_anchor_neighbourhoods(pm, pos, occ, labelof)
        print("\n-> Jesli zadne okno nie wyglada na tablice postaci: zejdz HP PONIZEJ maxHP w grze")
        print("   i odpal z --hp <aktualne> --maxhp 3886 (tryb par wskaze HP/maxHP bez pudla).")
        return
    results.sort(key=lambda r: r[0], reverse=True)
    print("\nOkien-kandydatow: %d. Najlepsze (po liczbie trafien):" % len(results))
    seen = set()
    for cnt, a, addr, base, win in results:
        if addr in seen:
            continue
        seen.add(addr)
        print("\n[OKNO] kotwica=%d  addr=%#010x  trafien znanych=%d/%d" % (a, addr, cnt, len(vals)))
        for j, val in enumerate(win):
            rel = base + j
            if -24 <= rel <= 28:
                tag = ("  <== %s" % labelof[val]) if (val in labelof and val != 0) else ""
                print("    rel %+3d : %12d%s" % (rel, val, tag))
        if len(seen) >= 6:
            break


SNAP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "logs", "hp_snap.txt")


def mode_snap(pm, regions, value):
    """Zapisuje adresy wszystkich dwordow == value (int) oraz == float(value) do pliku.
    Potem (po zdjeciu zbroi) --diff sprawdzi, ktore sie zmienily na nowa wartosc = prawdziwe maxHP."""
    vi = value & 0xFFFFFFFF
    vf = f32bits(value)
    print("\n=== SNAPSHOT: zapisuje adresy z wartoscia %d (int) i %d jako float ===" % (value, value))
    rows = []
    for rb, rs in regions:
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        for enc, V in (("int", vi), ("float", vf)):
            for i in np.nonzero(u == np.uint32(V))[0]:
                rows.append((rb + int(i) * 4, enc))
    with open(SNAP_PATH, "w", encoding="utf-8") as f:
        f.write("pid %d value %d\n" % (pm.process_id, value))
        for addr, enc in rows:
            f.write("%d %s\n" % (addr, enc))
    print("Zapisano %d adresow (int+float) do %s" % (len(rows), SNAP_PATH))
    print("TERAZ: zdejmij zbroje w grze, odczytaj nowe maxHP, i odpal:")
    print("    python recon/find_hp.py -e <exe> --diff <noweMaxHP>")


def mode_diff(pm, new_value):
    """Czyta snapshot i pokazuje adresy, ktore zmienily sie na new_value (w tym samym kodowaniu).
    To prawdziwe pole maxHP (ew. HP). Wypisuje okolice (int+float), zeby odczytac uklad."""
    try:
        with open(SNAP_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        print("[!] Brak %s - najpierw zrob --snap <maxHP> z zalozona zbroja." % SNAP_PATH)
        return
    hdr = lines[0].split()
    snap_pid = int(hdr[1])
    snap_val = int(hdr[3])
    if snap_pid != pm.process_id:
        print("[!] Snapshot byl dla PID %d, a teraz PID %d - gra zrestartowana, adresy nieaktualne."
              % (snap_pid, pm.process_id))
        print("    Zrob --snap jeszcze raz (z zalozona zbroja).")
        return
    ni = new_value & 0xFFFFFFFF
    nf = f32bits(new_value)
    print("\n=== DIFF: szukam adresow zmienionych z %d na %d (int lub float) ===" % (snap_val, new_value))
    hits = 0
    for line in lines[1:]:
        addr_s, enc = line.split()
        addr = int(addr_s)
        try:
            raw = struct.unpack("<I", pm.read_bytes(addr, 4))[0]
        except Exception:
            continue
        want = ni if enc == "int" else nf
        if raw == want:
            hits += 1
            print("\n[ZMIENIONE %s] addr=%#010x : %d -> %d" % (enc, addr, snap_val, new_value))
            try:
                win = np.frombuffer(pm.read_bytes(addr - 64, 128), dtype="<u4")
            except Exception:
                continue
            for k in range(win.size):
                rel = k - 16
                r = int(win[k])
                iv = struct.unpack("<i", struct.pack("<I", r))[0]
                fv = struct.unpack("<f", struct.pack("<I", r))[0]
                fvs = (" float=%.3f" % fv) if 1e-3 < abs(fv) < 1e9 else ""
                mark = "  <== TU" if rel == 0 else ""
                print("   %+3d: int=%12d%s%s" % (rel, iv, fvs, mark))
            if hits >= 12:
                print("\n(limit - patrz plik)")
                break
    if not hits:
        print("Zero zmienionych na %d. Czy nowe maxHP == %d? (zdjeta zbroja, odczytane z gry)"
              % (new_value, new_value))
        print("Mozesz tez sprobowac wartosc +-kilka, albo HP zamiast maxHP.")


TRACK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "logs", "hp_track.txt")


def _read_dword(pm, addr):
    try:
        return struct.unpack("<I", pm.read_bytes(addr, 4))[0]
    except Exception:
        return None


def mode_track(pm, regions, value, reset=False):
    """Iteracyjne zawezanie (jak CE 'exact value' kilka razy). Odporne na regeneracje HP i na int/float.
    Runda 1: zapisz WSZYSTKIE adresy == value (int lub float). Kolejne rundy (ZMIEN HP miedzy nimi!):
    zostaw tylko te, ktore maja nowa podana wartosc. Po 2-3 rundach zostaje prawdziwe HP."""
    prev = None
    if not reset and os.path.exists(TRACK_PATH):
        with open(TRACK_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
        if lines and lines[0].startswith("pid "):
            pid = int(lines[0].split()[1])
            if pid == pm.process_id:
                prev = [(int(a), e) for a, e in (ln.split() for ln in lines[1:])]
            else:
                print("[i] Snapshot byl dla innego PID - zaczynam od nowa.")
    vi = value & 0xFFFFFFFF
    vf = f32bits(value)
    if prev is None:
        cand = []
        for rb, rs in regions:
            try:
                buf = pm.read_bytes(rb, rs)
            except Exception:
                continue
            u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
            for enc, V in (("int", vi), ("float", vf)):
                for i in np.nonzero(u == np.uint32(V))[0]:
                    cand.append((rb + int(i) * 4, enc))
        _save_track(pm, cand)
        print("\n=== TRACK runda 1: %d kandydatow == %d (int+float) ===" % (len(cand), value))
        print("TERAZ zmien HP (daj sie trafic albo poczekaj na regen), odczytaj NOWE HP i odpal:")
        print("    python recon/find_hp.py -e <exe> --track <noweHP>")
        return
    keep = []
    for addr, enc in prev:
        raw = _read_dword(pm, addr)
        if raw is None:
            continue
        if raw == (vi if enc == "int" else vf):
            keep.append((addr, enc))
    _save_track(pm, keep)
    print("\n=== TRACK kolejna runda: z %d -> %d kandydatow (== %d) ===" % (len(prev), len(keep), value))
    if not keep:
        print("Zero - najpewniej HP nie bylo dokladnie %d w chwili odczytu (regen!) albo reset." % value)
        print("Zacznij od nowa: --track <HP> --track-reset, najlepiej gdy HP chwilowo stabilne.")
        return
    if len(keep) <= 16:
        print("Prawie gotowe - okolice kandydatow (int+float):")
        for addr, enc in keep:
            print("\n[KAND %s] addr=%#010x" % (enc, addr))
            try:
                win = np.frombuffer(pm.read_bytes(addr - 48, 112), dtype="<u4")
            except Exception:
                continue
            for k in range(win.size):
                raw = int(win[k])
                iv = struct.unpack("<i", struct.pack("<I", raw))[0]
                fv = struct.unpack("<f", struct.pack("<I", raw))[0]
                fvs = (" float=%.3f" % fv) if 1e-3 < abs(fv) < 1e9 else ""
                mark = "  <== HP" if k == 12 else ""
                print("   %+3d: int=%12d%s%s" % (k - 12, iv, fvs, mark))
    else:
        print("Za duzo - zmien HP jeszcze raz i odpal --track <noweHP> (powtarzaj do <=16).")


def _save_track(pm, cand):
    with open(TRACK_PATH, "w", encoding="utf-8") as f:
        f.write("pid %d\n" % pm.process_id)
        for addr, enc in cand:
            f.write("%d %s\n" % (addr, enc))


def mode_watch(pm, seconds=20.0, interval=0.7, lo=50, hi=50000):
    """Monitor na zywo: badz BITY (HP maleje), nie lecz sie. Probkuje pamiec co 'interval' i zostawia
    adresy, ktore sie NIE zwiekszyly (int i float) - na koncu te z NETTO spadkiem to kandydaci na HP.
    Nie trzeba znac dokladnego HP. Zakres [lo,hi] odsiewa wszechobecne male liczby."""
    import time as _t
    print("\n=== WATCH: dostawaj obrazenia (HP maleje), NIE lecz sie. %.0fs, co %.1fs; zakres %d..%d ==="
          % (seconds, interval, lo, hi))
    tracked = {}   # base -> dict(size, idx, enc, first, prev)  (enc: 0=int, 1=float)
    for rb, rs in writable_regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        iv = u.view(np.int32)
        fv = u.view(np.float32)
        im = (iv >= lo) & (iv <= hi)
        fm = np.isfinite(fv) & (fv >= lo) & (fv <= hi)
        idx_i = np.nonzero(im)[0]
        idx_f = np.nonzero(fm)[0]
        if idx_i.size + idx_f.size == 0:
            continue
        idx = np.concatenate([idx_i, idx_f]).astype(np.int32)
        enc = np.concatenate([np.zeros(idx_i.size, np.uint8), np.ones(idx_f.size, np.uint8)])
        num = np.concatenate([iv[idx_i].astype(np.float32), fv[idx_f].astype(np.float32)])
        tracked[rb] = dict(size=rs, idx=idx, enc=enc, first=num.copy(), prev=num)
    total = sum(t["idx"].size for t in tracked.values())
    print("Start: %d kandydatow w %d regionach. Zaczynaj dostawac obrazenia..." % (total, len(tracked)))
    t_end = _t.time() + seconds
    sample = 0
    while _t.time() < t_end:
        _t.sleep(interval)
        sample += 1
        for rb in list(tracked.keys()):
            t = tracked[rb]
            try:
                buf = pm.read_bytes(rb, t["size"])
            except Exception:
                del tracked[rb]
                continue
            u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
            if u.size <= int(t["idx"].max()):      # region sie skurczyl - porzuc
                del tracked[rb]
                continue
            iv = u.view(np.int32)
            fv = u.view(np.float32)
            idx = t["idx"]
            newnum = np.where(t["enc"] == 0, iv[idx].astype(np.float32), fv[idx].astype(np.float32))
            keep = newnum <= t["prev"]             # nie wzroslo (rowne dopuszczamy - regen/brak ciosu)
            if not keep.all():
                t["idx"] = idx[keep]
                t["enc"] = t["enc"][keep]
                t["first"] = t["first"][keep]
                newnum = newnum[keep]
            t["prev"] = newnum
            if t["idx"].size == 0:
                del tracked[rb]
        total = sum(t["idx"].size for t in tracked.values())
        print("  probka %d: %d kandydatow" % (sample, total))
        if total == 0:
            break
    results = []
    for rb, t in tracked.items():
        net = t["first"] - t["prev"]
        for n in np.nonzero(net > 0)[0]:
            n = int(n)
            results.append((rb + int(t["idx"][n]) * 4, "float" if t["enc"][n] else "int",
                            float(t["first"][n]), float(t["prev"][n])))
    results.sort(key=lambda r: r[2] - r[3], reverse=True)       # najwiekszy spadek na gorze
    print("\n=== Kandydatow z NETTO spadkiem: %d (najwiekszy spadek pierwszy) ===" % len(results))
    for addr, enc, f, l in results[:25]:
        print("\n[SPADL %s] addr=%#010x  %.0f -> %.0f" % (enc, addr, f, l))
        try:
            win = np.frombuffer(pm.read_bytes(addr - 48, 112), dtype="<u4")
        except Exception:
            continue
        for k in range(win.size):
            raw = int(win[k])
            ivv = struct.unpack("<i", struct.pack("<I", raw))[0]
            fvv = struct.unpack("<f", struct.pack("<I", raw))[0]
            fvs = (" float=%.3f" % fvv) if 1e-3 < abs(fvv) < 1e9 else ""
            mark = "  <== TU" if k == 12 else ""
            print("   %+3d: int=%12d%s%s" % (k - 12, ivv, fvs, mark))
    if not results:
        print("Zero. Moze HP roslo (lecenie/regen szybszy niz ciosy) albo poza zakresem - zwieksz --watch-hi.")


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
    ap.add_argument("--level", type=int, default=None, help="poziom")
    ap.add_argument("--exp", type=int, default=None, help="aktualne PD (EXP)")
    ap.add_argument("--maxexp", type=int, default=None, help="wymagane PD (EXP do nast. poziomu)")
    ap.add_argument("--sp", type=int, default=None, help="aktualne PM/mana")
    ap.add_argument("--maxsp", type=int, default=None, help="maks. PM/mana")
    ap.add_argument("--snap", type=int, default=None, metavar="MAXHP",
                    help="skan roznicowy krok 1: zapisz adresy z ta wartoscia (zbroja ZALOZONA)")
    ap.add_argument("--diff", type=int, default=None, metavar="NOWE_MAXHP",
                    help="skan roznicowy krok 2: pokaz adresy zmienione na te wartosc (zbroja ZDJETA)")
    ap.add_argument("--track", type=int, default=None, metavar="HP",
                    help="iteracyjne zawezanie: podawaj aktualne HP kilka razy (zmieniaj HP miedzy)")
    ap.add_argument("--track-reset", action="store_true", help="zacznij zawezanie od nowa")
    ap.add_argument("--watch", nargs="?", const=20, type=int, default=None, metavar="SEK",
                    help="monitor na zywo: dostawaj obrazenia, zostawia malejace adresy (domyslnie 20s)")
    ap.add_argument("--watch-lo", type=int, default=50, help="dolny prog wartosci w --watch")
    ap.add_argument("--watch-hi", type=int, default=50000, help="gorny prog wartosci w --watch")
    ap.add_argument("--min-run", type=int, default=16, help="min. dlugosc runu klucza (tryb ksztalt)")
    ap.add_argument("--shape", action="store_true", help="wymus tryb ksztalt")
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
        # skan roznicowy (zdejmowanie zbroi) ma pierwszenstwo
        if args.snap is not None:
            mode_snap(pm, regions, args.snap)
            return
        if args.diff is not None:
            mode_diff(pm, args.diff)
            return
        if args.track is not None:
            mode_track(pm, regions, args.track, reset=args.track_reset)
            return
        if args.watch is not None:
            hi = args.watch_hi if args.maxhp is None else max(args.watch_hi, args.maxhp + 100)
            mode_watch(pm, seconds=args.watch, lo=args.watch_lo, hi=hi)
            return

        known = {"poziom": args.level, "PD": args.exp, "wymPD": args.maxexp,
                 "HP": args.hp, "maxHP": args.maxhp, "PM": args.sp, "maxPM": args.maxsp}
        n_known = sum(1 for v in known.values() if v is not None)
        did = False
        # 1) najpewniejszy: para HP/maxHP (int+float), gdy HP != maxHP
        if args.hp is not None and args.maxhp is not None and args.hp != args.maxhp:
            mode_pair(pm, regions, args.hp, args.maxhp)
            did = True
        # 2) gdy HP==maxHP lub brak: kilka znanych wartosci
        elif n_known >= 3 and (args.exp is not None or args.maxexp is not None or args.level is not None):
            mode_known(pm, regions, known)
            did = True
        # 3) ostatecznosc: ksztalt
        if args.shape or not did:
            if not did:
                print("(za malo danych - podaj np. --hp --maxhp (rozne) albo --level/--exp; wlaczam ksztalt)")
            mode_shape(pm, regions, args.min_run)
    finally:
        sys.stdout = sys.__stdout__
        logf.close()
    print("[gotowe] pelny wynik w %s" % LOG_PATH)


if __name__ == "__main__":
    main()
