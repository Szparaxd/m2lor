"""
M1 / Zadanie 2 - skaner wartosci float w pamieci procesu (mini-Cheat-Engine w Pythonie).

Robi dokladnie to, co CE przy szukaniu pozycji:
  1) 'first' - migawka WSZYSTKICH wartosci float w zapisywalnej pamieci procesu,
     przefiltrowanych do rozsadnego zakresu (kandydaci na wspolrzedne).
  2) biegasz postacia w grze,
  3) 'inc' / 'dec' / 'same' / 'changed' - zostawia tylko te adresy, ktore
     odpowiednio wzrosly / zmalaly / nie zmienily sie / zmienily sie od ostatniej migawki.
Powtarzasz 2-3, az zostanie kilka adresow = X, Y (ew. Z).

WAZNE: uruchom terminal JAKO ADMINISTRATOR (bez tego OpenProcess odmawia).

Uzycie (interaktywnie):
    python recon/scan_floats.py

Komendy w petli:
    first              - pierwsza migawka (kandydaci w zakresie)
    inc / dec          - zostaw adresy ktore wzrosly / zmalaly
    same / changed     - zostaw adresy bez zmian / ze zmiana
    count              - ile zostalo kandydatow
    show [N]           - pokaz do N kandydatow (adres = wartosc), domyslnie 40
    read <hex>         - odczytaj pojedynczy float spod adresu
    watch <hex> [..]   - podgladaj na zywo kilka adresow (Ctrl+C konczy)
    range <lo> <hi>    - ustaw zakres filtra wartosci (domyslnie 100 .. 1e7)
    reset              - wyczysc kandydatow (bez nowej migawki)
    quit               - wyjscie
"""
import ctypes
from ctypes import wintypes
import math
import sys
import time

import numpy as np
import pymem

PROCESS = "GameCoreTop.exe"

# --- Windows API: VirtualQueryEx do chodzenia po regionach pamieci ---
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE_PROTECT = {0x04, 0x08, 0x40, 0x80}  # RW, WriteCopy, ExecRW, ExecWriteCopy
MAX_USER_ADDR = 0x7FFFFFFF  # cel jest 32-bitowy
MAX_REGION = 512 * 1024 * 1024  # pomijaj absurdalnie duze regiony


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]


def writable_regions(handle):
    """Zwraca liste (base, size) zapisywalnych, zacommitowanych regionow pamieci."""
    regions = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while addr < MAX_USER_ADDR:
        ok = k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ok:
            break
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        prot = mbi.Protect
        if (
            mbi.State == MEM_COMMIT
            and not (prot & PAGE_GUARD)
            and not (prot & PAGE_NOACCESS)
            and prot in WRITABLE_PROTECT
            and 0 < size <= MAX_REGION
        ):
            regions.append((base, size))
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt
    return regions


class Scanner:
    def __init__(self, pm):
        self.pm = pm
        self.handle = pm.process_handle
        self.lo = 100.0
        self.hi = 1.0e7
        self.addrs = np.empty(0, dtype=np.int64)  # posortowane adresy kandydatow
        self.vals = np.empty(0, dtype="<f4")       # ostatnia znana wartosc pod kazdym adresem

    def _read_region(self, base, size):
        try:
            buf = self.pm.read_bytes(base, size)
        except Exception:
            return None
        n = (len(buf) // 4) * 4
        if n == 0:
            return None
        return np.frombuffer(buf[:n], dtype="<f4")

    def first_scan(self):
        all_addrs = []
        all_vals = []
        regions = writable_regions(self.handle)
        for base, size in regions:
            a = self._read_region(base, size)
            if a is None:
                continue
            mask = np.isfinite(a) & (a > self.lo) & (a < self.hi)
            idx = np.nonzero(mask)[0]
            if idx.size == 0:
                continue
            all_addrs.append(base + idx.astype(np.int64) * 4)
            all_vals.append(a[idx])
        if all_addrs:
            self.addrs = np.concatenate(all_addrs)
            self.vals = np.concatenate(all_vals).astype("<f4")
        else:
            self.addrs = np.empty(0, dtype=np.int64)
            self.vals = np.empty(0, dtype="<f4")
        print(f"[+] Pierwsza migawka: {self.addrs.size} kandydatow "
              f"(zakres {self.lo} .. {self.hi}, {len(regions)} regionow).")

    def _current_values(self):
        """Odczytaj biezace wartosci pod wszystkimi kandydatami (bulk, po regionach)."""
        cur = np.full(self.addrs.size, np.nan, dtype="<f4")
        if self.addrs.size == 0:
            return cur
        for base, size in writable_regions(self.handle):
            lo_i = np.searchsorted(self.addrs, base, "left")
            hi_i = np.searchsorted(self.addrs, base + size, "left")
            if hi_i <= lo_i:
                continue
            a = self._read_region(base, size)
            if a is None:
                continue
            sub = self.addrs[lo_i:hi_i]
            idx = (sub - base) // 4
            valid = idx < a.size
            cur[lo_i:hi_i][valid] = a[idx[valid]]
        return cur

    def refine(self, mode):
        if self.addrs.size == 0:
            print("[!] Brak kandydatow. Najpierw 'first'.")
            return
        cur = self._current_values()
        finite = np.isfinite(cur)
        if mode == "inc":
            keep = finite & (cur > self.vals)
        elif mode == "dec":
            keep = finite & (cur < self.vals)
        elif mode == "same":
            keep = finite & (cur == self.vals)
        elif mode == "changed":
            keep = finite & (cur != self.vals)
        else:
            print(f"[!] Nieznany tryb: {mode}")
            return
        self.addrs = self.addrs[keep]
        self.vals = cur[keep]
        print(f"[+] Po '{mode}': {self.addrs.size} kandydatow.")

    def show(self, n=40):
        if self.addrs.size == 0:
            print("[!] Brak kandydatow.")
            return
        cur = self._current_values()
        count = min(n, self.addrs.size)
        print(f"[+] Pokazuje {count} z {self.addrs.size}:")
        for i in range(count):
            print(f"    {hex(int(self.addrs[i]))}  =  {float(cur[i]):.3f}")
        if self.addrs.size > count:
            print(f"    ... (jeszcze {self.addrs.size - count})")

    def read_one(self, addr):
        try:
            print(f"    {hex(addr)}  =  {self.pm.read_float(addr):.3f}")
        except Exception as e:
            print(f"[!] Blad odczytu {hex(addr)}: {e}")

    def watch(self, addrs):
        print("[+] Podglad na zywo. Ctrl+C aby przerwac.")
        try:
            while True:
                parts = []
                for a in addrs:
                    try:
                        parts.append(f"{hex(a)}={self.pm.read_float(a):10.2f}")
                    except Exception:
                        parts.append(f"{hex(a)}=ERR")
                print("  ".join(parts))
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n[+] Koniec podgladu.")


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Nie moge otworzyc procesu. Uruchom terminal JAKO ADMINISTRATOR.")
        return

    print(f"[+] Podpieto do {PROCESS} (PID {pm.process_id}). Wpisz 'first' aby zaczac, 'quit' aby wyjsc.")
    sc = Scanner(pm)

    while True:
        try:
            line = input("scan> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "first":
            sc.first_scan()
        elif cmd in ("inc", "dec", "same", "changed"):
            sc.refine(cmd)
        elif cmd == "count":
            print(f"[+] Kandydatow: {sc.addrs.size}")
        elif cmd == "show":
            sc.show(int(parts[1]) if len(parts) > 1 else 40)
        elif cmd == "read" and len(parts) > 1:
            sc.read_one(int(parts[1], 16))
        elif cmd == "watch" and len(parts) > 1:
            sc.watch([int(p, 16) for p in parts[1:]])
        elif cmd == "range" and len(parts) > 2:
            sc.lo, sc.hi = float(parts[1]), float(parts[2])
            print(f"[+] Zakres filtra: {sc.lo} .. {sc.hi} (zadziala przy nastepnym 'first').")
        elif cmd == "reset":
            sc.addrs = np.empty(0, dtype=np.int64)
            sc.vals = np.empty(0, dtype="<f4")
            print("[+] Wyczyszczono kandydatow.")
        else:
            print("[!] Nieznana komenda. Dostepne: first inc dec same changed count show read watch range reset quit")


if __name__ == "__main__":
    main()
