"""
M3 - znajdz liste encji wokol gracza metoda "skan po vtable".

Idea:
  * Kazdy obiekt tej samej klasy C++ (tu: postac w grze) zaczyna sie od wskaznika
    na te sama vtable (staly adres w module).
  * Znajdujemy ten wskaznik + offset pozycji w obiekcie, analizujac kopie pozycji
    GRACZA lezace na stercie (szukamy wspolnego wzorca: ten sam wskaznik-w-modul
    w tej samej odleglosci przed polem X).
  * Potem skanujemy CALA sterte po tym vtable -> kazde trafienie to poczatek obiektu
    postaci. Czytamy jego pozycje -> lista wszystkich encji.

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona, postac w swiecie.
Najlepiej stan w miejscu, gdzie wokol jest kilka mobow / kamieni metin.

Uzycie:
    python recon/find_actors.py
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
OFF_X = 0x493014          # pozycja gracza (globalna, z M2) - do odniesienia
OFF_Y = 0x49301C
RADIUS = 8000.0           # szukamy encji w tym promieniu wokol gracza (jednostki swiata)
BACK_WINDOW = 0x3000      # ile bajtow wstecz szukamy wskaznika vtable
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "find_actors.log")


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
    regions = []
    addr = 0
    mbi = MBI()
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


def read_region_bytes(pm, base, size):
    try:
        buf = pm.read_bytes(base, size)
    except Exception:
        return None
    return buf


def is_vtable(pm, val, mod_lo, mod_hi):
    """Prawdziwa vtable wskazuje na tablice adresow FUNKCJI (wskaznikow w modul).
    Sprawdzamy, czy pierwsze 4 wpisy to wskazniki w modul."""
    try:
        raw = pm.read_bytes(val, 16)
    except Exception:
        return False
    ptrs = np.frombuffer(raw, dtype="<u4")
    good = int(np.sum((ptrs >= mod_lo) & (ptrs < mod_hi)))
    return good >= 3


def main():
    open(LOG_PATH, "a").close()
    log("=" * 70)
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log(f"[!] Nie znalazlem procesu {PROCESS}. Gra wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    size = pymem.process.module_from_name(pm.process_handle, PROCESS).SizeOfImage
    mod_lo, mod_hi = base, base + size

    px = pm.read_float(base + OFF_X)
    py = pm.read_float(base + OFF_Y)
    log(f"[+] baza modulu {hex(base)} .. {hex(mod_hi)} | gracz X={px:.1f} Y={py:.1f}")

    regions = writable_regions(pm.process_handle)
    log(f"[+] zapisywalnych regionow: {len(regions)}")

    # --- krok 1: znajdz na stercie bloki (X,Y) w promieniu RADIUS wokol gracza ---
    # (lapie gracza ORAZ pobliskie moby/metiny -> duzo prawdziwych postaci do analizy)
    xlo, xhi = px - RADIUS, px + RADIUS
    ylo, yhi = py - RADIUS, py + RADIUS
    blocks = []  # (x_addr, y_rel_bytes)
    for rb, rs in regions:
        buf = read_region_bytes(pm, rb, rs)
        if buf is None:
            continue
        n = (len(buf) // 4) * 4
        f = np.frombuffer(buf[:n], dtype="<f4")
        xmask = (f > xlo) & (f < xhi)
        xhit = np.nonzero(xmask)[0]
        for i in xhit:
            for dy in (1, 2, 3):  # Y na +4 / +8 / +12
                if i + dy < f.size:
                    yv = f[i + dy]
                    if ylo < yv < yhi:
                        blocks.append((rb + int(i) * 4, dy * 4))
                        break
    log(f"[+] blokow pozycji (X,Y) w promieniu {RADIUS:.0f}: {len(blocks)}")
    if not blocks:
        log("[!] Nie znalazlem blokow pozycji. Zwieksz POS_TOL albo sprawdz offsety.")
        return

    # --- krok 2: dla kazdego bloku szukaj wstecz wskaznika w modul; tally (vtable, dist) ---
    tally = Counter()
    yrel_for = {}
    for xaddr, yrel in blocks:
        start = xaddr - BACK_WINDOW
        buf = read_region_bytes(pm, start, BACK_WINDOW + 4)
        if buf is None:
            continue
        d = np.frombuffer(buf[:((len(buf) // 4) * 4)], dtype="<u4")
        for j in range(d.size):
            val = int(d[j])
            if mod_lo <= val < mod_hi:
                ptr_addr = start + j * 4
                dist = xaddr - ptr_addr
                if 0 < dist <= BACK_WINDOW:
                    tally[(val, dist)] += 1
                    yrel_for[(val, dist)] = yrel

    if not tally:
        log("[!] Brak wskaznikow w modul przed blokami. Trzeba inaczej.")
        return

    # --- agregacja per wartosc vtable: najlepszy offset, ile roznych offsetow (sygnatura tablicy) ---
    from collections import defaultdict
    best_by_val = {}            # val -> (xoff, yoff, count)
    offsets_by_val = defaultdict(set)
    for (val, dist), cnt in tally.items():
        offsets_by_val[val].add(dist)
        if val not in best_by_val or cnt > best_by_val[val][2]:
            best_by_val[val] = (dist, dist + yrel_for[(val, dist)], cnt)

    # kandydaci: tylko prawdziwe vtable (wskazuja na funkcje), posortowani po count malejaco
    cand = [v for v in best_by_val if is_vtable(pm, v, mod_lo, mod_hi)]
    cand.sort(key=lambda v: -best_by_val[v][2])
    cand = cand[:8]
    log(f"[+] Kandydatow-vtable (po filtrze is_vtable): {len(cand)}")

    # --- jeden przebieg po stercie: zbierz adresy trafien dla wszystkich kandydatow ---
    hits_by_val = {v: [] for v in cand}
    cand_set = set(cand)
    for rb, rs in regions:
        buf = read_region_bytes(pm, rb, rs)
        if buf is None:
            continue
        n = (len(buf) // 4) * 4
        u = np.frombuffer(buf[:n], dtype="<u4")
        for v in cand:
            idx = np.nonzero(u == v)[0]
            if idx.size:
                base_arr = rb + idx.astype(np.int64) * 4
                hits_by_val[v].extend(base_arr.tolist())

    # --- diagnostyka per kandydat: liczba, odstepy adresow, probka pozycji ---
    log("[+] DIAGNOSTYKA kandydatow:")
    for v in cand:
        xoff, yoff, cnt = best_by_val[v]
        n_off = len(offsets_by_val[v])
        addrs = sorted(hits_by_val[v])
        gaps = [addrs[i + 1] - addrs[i] for i in range(len(addrs) - 1)]
        med_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
        # probka pozycji (pierwsze kilkanascie obiektow w swiatowym zakresie)
        sample = []
        for A in addrs:
            try:
                ex = pm.read_float(A + xoff)
                ey = pm.read_float(A + yoff)
            except Exception:
                continue
            if math.isfinite(ex) and WORLD_MIN < ex < WORLD_MAX and math.isfinite(ey) and WORLD_MIN < ey < WORLD_MAX:
                sample.append((A, ex, ey))
            if len(sample) >= 12:
                break
        log(f"  --- vtable={hex(v)} xoff={hex(xoff)} yoff={hex(yoff)} "
            f"count_tally={cnt} n_offsetow={n_off} n_trafien={len(addrs)} median_gap={hex(med_gap)}")
        for A, ex, ey in sample:
            log(f"        obj={hex(A)}  X={ex:10.1f} Y={ey:10.1f} dist={math.hypot(ex - px, ey - py):8.1f}")

    log("[i] Szukamy kandydata: MALO roznych offsetow (1-3), DUZY median_gap, rozne pozycje wokol gracza.")


if __name__ == "__main__":
    main()
