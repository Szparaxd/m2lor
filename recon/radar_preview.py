"""
Podgladowy RADAR (tkinter) - M4/M6 preview.

Rysuje gracza w srodku i encje (moby) wokol, na zywo. Sluzy tez do KALIBRACJI
orientacji wzgledem ekranu gry.

Klawisze:
    Y  - odbij os Y (gora/dol)         X  - odbij os X (lewo/prawo)
    S  - zamien osie (obrot 90st)      + / -  - zoom
    R  - ponowny skan listy encji (po smierci/spawnie mobow)
    ESC - wyjscie

Kolory: zielony trojkat = gracz (srodek). Czerwony = encja f5c==8 (mob),
pomaranczowy (wiekszy) = encja f5c==0 (inne: NPC/metin? - do wskazania wzrokiem).

WAZNE: uruchom z terminala JAKO ADMINISTRATOR, gra wlaczona.

Uzycie:
    python recon/radar_preview.py
"""
import ctypes
from ctypes import wintypes
import math
import tkinter as tk

import numpy as np
import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"
OFF_PLAYER_X = 0x493014
OFF_PLAYER_Y = 0x49301C
VT_OFF = 0x3D5000
ENT_OFF_X = 0x264      # world X (plaszczyzna 2D B)
ENT_OFF_Y = 0x268      # world Y (plaszczyzna 2D B); 0x26c = wysokosc/Z
OFF_F5C = 0x5C
OFF_NAME = 0x354       # std::string nazwa encji
OFF_VNUM = 0x4B0       # vnum/rasa encji: metiny = 8000-8999, moby = 101.. itd.
HERO_VT_OFF = 0x3D6CA0 # vtable obiektu gracza (unikalny, 1 obiekt)
HERO_POS_OFF = 0x8     # pozycja hero: X@+0x8, -Y@+0xC, Z@+0x10 (swiat, niezalezne od kamery)
# metiny = efekt swiecenia CEffectInstance (nie postac!)
EFF_VT_OFF = 0x3CE250  # vtable CEffectInstance
EFF_POS_X = 0x280      # pozycja efektu: X@+0x280, -Y@+0x284
EFF_POS_Y = 0x284
EFF_EFTDATA = 0x290    # -> CEffectData; nazwa pliku efektu @ +0x44 (metin: zawiera 'metinstone')
EFTDATA_FNAME = 0x44
# srodek = hero: obiekt z prawdziwa vtable, ktorego 0x264 ~ globalX; jego 0x268 ma PRZECIWNY znak
WORLD_MIN, WORLD_MAX = 1000.0, 10_000_000.0

W = H = 660
CX = CY = W // 2

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


class Radar:
    def __init__(self, root):
        self.pm = pymem.Pymem(PROCESS)
        mod = pymem.process.module_from_name(self.pm.process_handle, PROCESS)
        self.base = mod.lpBaseOfDll
        self.mod_lo = mod.lpBaseOfDll
        self.mod_hi = mod.lpBaseOfDll + mod.SizeOfImage
        self.vtable = self.base + VT_OFF
        self.vt_cache = {}
        self.hero_obj = None           # obiekt gracza (srodek radaru), znajdowany w rescan()

        self.scale = 300.0 / 10000.0   # px na jednostke swiata
        self.xdir = 1
        self.ydir = -1                 # domyslnie odbijamy Y (negacja z M4)
        self.swap = False
        self.ents = []                 # [(obj, f5c)]
        self.off_x = ENT_OFF_X         # dostrajalne na zywo ([ / ])
        self.off_y = ENT_OFF_Y
        self.rot_deg = 0.0             # obrot radaru (, / .), by dopasowac do ekranu
        self.metins = []              # [(x, y)] pozycje metinow (z efektow)

        self.root = root
        self.canvas = tk.Canvas(root, width=W, height=H, bg="#0b0f0b", highlightthickness=0)
        self.canvas.pack()
        root.bind("<Key>", self.on_key)
        self.rescan()
        self.tick()

    def is_vtable(self, v):
        if v in self.vt_cache:
            return self.vt_cache[v]
        ok = False
        if self.mod_lo <= v < self.mod_hi:
            try:
                ptrs = np.frombuffer(self.pm.read_bytes(v, 16), dtype="<u4")
                ok = int(np.sum((ptrs >= self.mod_lo) & (ptrs < self.mod_hi))) >= 3
            except Exception:
                ok = False
        self.vt_cache[v] = ok
        return ok

    def read_name(self, o):
        """MSVC std::string (32-bit): buf[16], size@+16, cap@+20."""
        try:
            size = self.pm.read_uint(o + OFF_NAME + 16)
            cap = self.pm.read_uint(o + OFF_NAME + 20)
            if size > 64 or cap > 1_000_000:
                return ""
            if cap < 16:
                raw = self.pm.read_bytes(o + OFF_NAME, min(size, 15))
            else:
                ptr = self.pm.read_uint(o + OFF_NAME)
                if not (0x100000 < ptr < 0x7FFF0000):
                    return ""
                raw = self.pm.read_bytes(ptr, min(size, 48))
        except Exception:
            return ""
        out = []
        for ch in raw:
            if ch == 0:
                break
            out.append(chr(ch) if 32 <= ch < 127 else ".")
        return "".join(out)

    def classify(self, obj):
        try:
            vnum = self.pm.read_uint(obj + OFF_VNUM)
        except Exception:
            return "unknown"
        if 1 <= vnum < 30000:
            return "mob"
        return "unknown"

    def _cstr(self, addr, n=80):
        try:
            return self.pm.read_bytes(addr, n).split(b"\x00")[0].decode("ascii", "ignore")
        except Exception:
            return ""

    def _std_string(self, a):
        try:
            cap = self.pm.read_uint(a + 0x14)
            if 16 <= cap < 1_000_000:
                return self._cstr(self.pm.read_uint(a))
            return self._cstr(a, 16)
        except Exception:
            return ""

    def eff_fname(self, o):
        try:
            p = self.pm.read_uint(o + EFF_EFTDATA)
        except Exception:
            return ""
        return self._std_string(p + EFTDATA_FNAME)

    def rescan(self):
        ents = []
        metins = []
        hero_vt = self.base + HERO_VT_OFF
        eff_vt = self.base + EFF_VT_OFF
        hero = None
        for rb, rs in writable_regions(self.pm.process_handle):
            try:
                buf = self.pm.read_bytes(rb, rs)
            except Exception:
                continue
            n = (len(buf) // 4) * 4
            u = np.frombuffer(buf[:n], dtype="<u4")
            for h in np.nonzero(u == self.vtable)[0]:
                obj = rb + int(h) * 4
                ents.append((obj, self.classify(obj)))
            # metiny = efekty 'metinstone'
            for h in np.nonzero(u == eff_vt)[0]:
                o = rb + int(h) * 4
                fn = self.eff_fname(o)
                if fn and "metinstone" in fn.lower():
                    try:
                        mx = self.pm.read_float(o + EFF_POS_X)
                        my = -self.pm.read_float(o + EFF_POS_Y)
                    except Exception:
                        continue
                    if math.isfinite(mx) and WORLD_MIN < mx < WORLD_MAX:
                        metins.append((mx, my))
            if hero is None:
                hh = np.nonzero(u == hero_vt)[0]
                if len(hh):
                    hero = rb + int(hh[0]) * 4
        self.ents = ents
        self.metins = metins
        if hero is not None:
            self.hero_obj = hero

    def on_key(self, e):
        k = e.keysym.lower()
        if k == "y":
            self.ydir *= -1
        elif k == "x":
            self.xdir *= -1
        elif k == "s":
            self.swap = not self.swap
        elif k in ("plus", "equal", "kp_add"):
            self.scale *= 1.25
        elif k in ("minus", "kp_subtract"):
            self.scale /= 1.25
        elif k == "r":
            self.rescan()
        elif k == "bracketleft":
            self.off_x -= 4; self.off_y -= 4
        elif k == "bracketright":
            self.off_x += 4; self.off_y += 4
        elif k in ("comma", "less"):
            self.rot_deg = (self.rot_deg - 15) % 360
        elif k in ("period", "greater"):
            self.rot_deg = (self.rot_deg + 15) % 360
        elif k == "escape":
            self.root.destroy()

    def world_to_screen(self, dx, dy):
        if self.swap:
            dx, dy = dy, dx
        dx *= self.xdir
        dy *= self.ydir
        # obrot radaru (dopasowanie do kamery/ekranu)
        a = math.radians(self.rot_deg)
        ca, sa = math.cos(a), math.sin(a)
        rx = dx * ca - dy * sa
        ry = dx * sa + dy * ca
        return CX + rx * self.scale, CY + ry * self.scale

    def tick(self):
        c = self.canvas
        c.delete("all")
        # pierscienie zasiegu
        for r_world in (2000, 5000, 10000):
            rp = r_world * self.scale
            c.create_oval(CX - rp, CY - rp, CX + rp, CY + rp, outline="#1d2a1d")
        c.create_line(CX, 0, CX, H, fill="#141c14")
        c.create_line(0, CY, W, CY, fill="#141c14")

        # srodek = HERO (obiekt gracza), pozycja swiata @+0x8 (X), -Y @+0xC. Niezalezne od kamery!
        try:
            if self.hero_obj is None:
                raise ValueError
            px = self.pm.read_float(self.hero_obj + HERO_POS_OFF)
            py = -self.pm.read_float(self.hero_obj + HERO_POS_OFF + 4)
        except Exception:
            c.create_text(CX, CY + 20, fill="#ff6a6a", text="szukam gracza... [R]", font=("Consolas", 9))
            self.root.after(150, self.tick)
            return

        shown = 0
        for obj, kind in self.ents:
            try:
                ex = self.pm.read_float(obj + self.off_x)
                ey = self.pm.read_float(obj + self.off_y)
            except Exception:
                continue
            if not (math.isfinite(ex) and WORLD_MIN < ex < WORLD_MAX):
                continue
            sx, sy = self.world_to_screen(ex - px, ey - py)
            if -20 < sx < W + 20 and -20 < sy < H + 20:
                if kind == "metin":
                    c.create_rectangle(sx - 5, sy - 5, sx + 5, sy + 5, fill="#ffe14d", outline="#8a7400")
                elif kind == "mob":
                    c.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="#ff3b3b", outline="")
                else:
                    c.create_oval(sx - 2, sy - 2, sx + 2, sy + 2, fill="#4a5a4a", outline="")
                shown += 1

        # metiny (z efektow 'metinstone') - zolte kwadraty
        for mx, my in self.metins:
            sx, sy = self.world_to_screen(mx - px, my - py)
            if -20 < sx < W + 20 and -20 < sy < H + 20:
                c.create_rectangle(sx - 5, sy - 5, sx + 5, sy + 5, fill="#ffe14d", outline="#8a7400")
                shown += 1

        # gracz: zielony trojkat (wierzcholek do gory = "przod")
        c.create_polygon(CX, CY - 8, CX - 6, CY + 6, CX + 6, CY + 6, fill="#39ff6a", outline="")

        info = (f"encji:{len(self.ents)} widoczne:{shown}  rot:{self.rot_deg:.0f}st  "
                f"X:{'+' if self.xdir>0 else '-'} Y:{'+' if self.ydir>0 else '-'} swap:{self.swap}   "
                f"[,/.]obroc [Y][X][S]odbij [+/-]zoom [R]skan")
        c.create_text(10, H - 12, anchor="w", fill="#9fe7a6", text=info, font=("Consolas", 9))
        c.create_text(CX, 10, anchor="n", fill="#5a7a5a", text="gora ekranu", font=("Consolas", 8))
        c.create_text(W - 10, 10, anchor="ne", fill="#ffe14d", text="■ metin", font=("Consolas", 9))
        c.create_text(W - 10, 26, anchor="ne", fill="#ff3b3b", text="● mob", font=("Consolas", 9))
        c.create_text(W - 10, 42, anchor="ne", fill="#39ff6a", text="▲ Ty", font=("Consolas", 9))

        self.root.after(100, self.tick)


def main():
    try:
        root = tk.Tk()
    except Exception as e:
        print("Blad tkinter:", e)
        return
    root.title("Metin2 Radar (preview)")
    try:
        Radar(root)
    except pymem.exception.ProcessNotFound:
        print("[!] Nie znalazlem procesu. Gra wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
