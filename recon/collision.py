"""Odczyt siatki kolizji (ATTRIBUTE_BLOCK) z pamieci klienta + pathfinding (A*) do omijania przeszkod.

Uklad pamieci (GameCoreTop, z analizy CMapOutdoor::isAttrOn; patrz recon/probe_collision.py):
  CMapOutdoor M: M+0x40 CTerrain* m_pTerrain[9] (3x3 wokol gracza); M+0x70/0x72 countX/Y (short);
                 M+0x74/0x76 curCoordX/Y (short).
  CTerrain  T:  T+ATTR_OFF  BYTE m_abyAttrMap[256*256], index = ly*256 + lx.
  sektor = 25600 jedn. = 256 komorek; komorka = 100 jedn. = 1 px minimapy; ATTRIBUTE_BLOCK = bit0.
Konwencja: iX=wx, iY=wy (wsp. swiata radaru). Offsety SA PER BUILD (tu GameCoreTop) - dla vCore inne.
"""
import heapq
import math

import numpy as np

SECTOR = 25600
CELL = 100
NCELL = 256
BLOCK = 0x01
WATER = 0x02

# Offsety per build (nazwa procesu -> dict). GameCoreTop zweryfikowany na zywo.
BUILD_OFFSETS = {
    "gamecoretop.exe": dict(terrain_arr=0x40, count=0x70, cur=0x74, attr=0x18E1A),
    # vCore: do uzupelnienia osobnym reconem (inny build).
}


class Collision:
    """Czyta siatke przechodniosci z CMapOutdoor. set_mapout() z radaru, refresh() co skan."""

    def __init__(self, pm, proc_name):
        self.pm = pm
        self.off = BUILD_OFFSETS.get(proc_name.lower())
        self.mapout = None
        self.ok = self.off is not None
        self._terr = {}         # (tcx,tcy) -> np.uint8[256,256] (ly,lx) albo None
        self._cur = None        # (curX,curY) gdy cache terenow aktualny
        self.countX = self.countY = 0
        self.curX = self.curY = 0

    def set_mapout(self, mapout):
        if mapout != self.mapout:
            self.mapout = mapout
            self._terr.clear()
            self._cur = None

    def refresh(self):
        """Odczyt pol CMapOutdoor; gdy zmienil sie curCoord (gracz przeszedl sektor) - wyczysc cache."""
        if not self.ok or not self.mapout:
            return False
        m, o = self.mapout, self.off
        try:
            self.countX = self.pm.read_short(m + o["count"])
            self.countY = self.pm.read_short(m + o["count"] + 2)
            cx = self.pm.read_short(m + o["cur"])
            cy = self.pm.read_short(m + o["cur"] + 2)
        except Exception:
            return False
        if (cx, cy) != self._cur:
            self._cur = (cx, cy)
            self._terr.clear()
        self.curX, self.curY = cx, cy
        return True

    def _terrain(self, tcx, tcy):
        """Tablica atrybutow [256,256] danego sektora (z cache); None gdy niezaladowany."""
        key = (tcx, tcy)
        if key in self._terr:
            return self._terr[key]
        arr = None
        num = (tcy - self.curY + 1) * 3 + (tcx - self.curX + 1)
        if 0 <= num < 9:
            try:
                p = self.pm.read_uint(self.mapout + self.off["terrain_arr"] + num * 4)
                if 0x10000 < p < 0x7FFF0000:
                    raw = self.pm.read_bytes(p + self.off["attr"], NCELL * NCELL)
                    arr = np.frombuffer(raw, dtype=np.uint8).reshape(NCELL, NCELL)
            except Exception:
                arr = None
        self._terr[key] = arr
        return arr

    def is_block(self, wx, wy):
        """True gdy pole swiata (wx,wy) zablokowane. Niezaladowane/poza mapa -> False (nie blokuj)."""
        if wx < 0 or wy < 0 or wx > self.countX * SECTOR or wy > self.countY * SECTOR:
            return False
        tcx, tcy = int(wx) // SECTOR, int(wy) // SECTOR
        arr = self._terrain(tcx, tcy)
        if arr is None:
            return False
        lx = (int(wx) - tcx * SECTOR) // CELL
        ly = (int(wy) - tcy * SECTOR) // CELL
        return bool(arr[ly, lx] & BLOCK)

    def _cell_block(self, gx, gy):
        return self.is_block(gx * CELL + 1, gy * CELL + 1)

    def find_path(self, sx, sy, gx, gy, margin=6, max_span=140, inflate=1):
        """A* po komorkach (100 jedn.) od (sx,sy) do (gx,gy). Zwraca liste (wx,wy) centrow komorek
        (wygladzona przez LOS) BEZ komorki startu, albo None gdy brak trasy / cel za daleko."""
        scx, scy = int(sx) // CELL, int(sy) // CELL
        gcx, gcy = int(gx) // CELL, int(gy) // CELL
        minx, maxx = min(scx, gcx) - margin, max(scx, gcx) + margin
        miny, maxy = min(scy, gcy) - margin, max(scy, gcy) + margin
        if (maxx - minx) > max_span or (maxy - miny) > max_span:
            return None
        # siatka blokad w bboxie (+ opcjonalne pogrubienie przeszkod o 'inflate' komorek)
        raw = {}
        for gy_ in range(miny, maxy + 1):
            for gx_ in range(minx, maxx + 1):
                raw[(gx_, gy_)] = self._cell_block(gx_, gy_)
        blk = raw
        if inflate:
            # pogrubienie: komorka = przeszkoda, jesli ktoras sasiednia (do 'inflate') jest przeszkoda
            # (margines bezpieczenstwa, by postac nie scinala rogow scian)
            blk = {}
            for (cx, cy) in raw:
                v = raw[(cx, cy)]
                if not v:
                    for dj in range(-inflate, inflate + 1):
                        for di in range(-inflate, inflate + 1):
                            if raw.get((cx + di, cy + dj)):
                                v = True
                                break
                        if v:
                            break
                blk[(cx, cy)] = v
        start, goal = (scx, scy), (gcx, gcy)
        if blk.get(goal):           # cel na blokadzie -> celuj najblizsza wolna komorke w poblizu
            goal = self._nearest_free(goal, blk)
            if goal is None:
                return None
        # A* 8-kierunkowe
        def h(a, b):
            return math.hypot(a[0] - b[0], a[1] - b[1])
        openh = [(h(start, goal), 0.0, start)]
        came, g = {}, {start: 0.0}
        found = False
        while openh:
            _, gc, cur = heapq.heappop(openh)
            if cur == goal:
                found = True
                break
            if gc > g.get(cur, 1e18):
                continue
            cx, cy = cur
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = cx + di, cy + dj
                if not (minx <= nx <= maxx and miny <= ny <= maxy):
                    continue
                if blk.get((nx, ny)):
                    continue
                if di and dj and (blk.get((cx + di, cy)) or blk.get((cx, cy + dj))):
                    continue  # nie przecinaj rogu przez dwie przeszkody
                ng = gc + (1.4142 if di and dj else 1.0)
                if ng < g.get((nx, ny), 1e18):
                    g[(nx, ny)] = ng
                    came[(nx, ny)] = cur
                    heapq.heappush(openh, (ng + h((nx, ny), goal), ng, (nx, ny)))
        if not found:
            return None
        # odtworz sciezke komorek
        cells = [goal]
        while cells[-1] != start:
            cells.append(came[cells[-1]])
        cells.reverse()
        # wygladzanie LOS: zostaw tylko punkty, gdzie trzeba skrecic
        smooth = [cells[0]]
        i = 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not self._los_clear(cells[i], cells[j], blk):
                j -= 1
            smooth.append(cells[j])
            i = j
        # na wsp. swiata (centra komorek), bez komorki startu
        return [(c[0] * CELL + CELL // 2, c[1] * CELL + CELL // 2) for c in smooth[1:]]

    def _nearest_free(self, cell, blk, r=6):
        for rad in range(1, r + 1):
            for dj in range(-rad, rad + 1):
                for di in range(-rad, rad + 1):
                    c = (cell[0] + di, cell[1] + dj)
                    if c in blk and not blk[c]:
                        return c
        return None

    def _los_clear(self, c0, c1, blk):
        """Bresenham: czy linia komorek c0->c1 jest wolna od blokad."""
        (x0, y0), (x1, y1) = c0, c1
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx - dy
        x, y = x0, y0
        while True:
            if blk.get((x, y)):
                return False
            if (x, y) == (x1, y1):
                return True
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy


# ---- test standalone: znajdz trase w biezacej scenie i pokaz ASCII ----
if __name__ == "__main__":
    import sys
    import ctypes
    from ctypes import wintypes
    import pymem
    import pymem.process
    import rtti
    PROC = sys.argv[1] if len(sys.argv) > 1 else "GameCoreTop.exe"
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WR = {0x04, 0x08, 0x40, 0x80}

    class MBI(ctypes.Structure):
        _fields_ = [("B", ctypes.c_void_p), ("A", ctypes.c_void_p), ("AP", wintypes.DWORD),
                    ("a1", wintypes.DWORD), ("RS", ctypes.c_size_t), ("S", wintypes.DWORD),
                    ("P", wintypes.DWORD), ("T", wintypes.DWORD), ("a2", wintypes.DWORD)]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]

    def regions(h):
        out, a, m = [], 0, MBI()
        while a < 0x7FFFFFFF:
            if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(m), ctypes.sizeof(m)):
                break
            b, s, p = (m.B or 0), (m.RS or 0), m.P
            if m.S == 0x1000 and not (p & 0x100) and not (p & 0x01) and p in WR and 0 < s <= 512 * 1024 * 1024:
                out.append((b, s))
            n = b + s
            if n <= a:
                break
            a = n
        return out
    pm = pymem.Pymem(PROC)
    mod = pymem.process.module_from_name(pm.process_handle, PROC)
    vt = rtti.find_vtables(pm, mod.lpBaseOfDll, mod.SizeOfImage, ["CMapOutdoor", "CGameEventManager"])
    mapout = hero = None
    for rb, rs in regions(pm.process_handle):
        try:
            buf = pm.read_bytes(rb, rs)
        except Exception:
            continue
        u = np.frombuffer(buf[:(len(buf) // 4) * 4], dtype="<u4")
        if mapout is None:
            hh = np.nonzero(u == vt["CMapOutdoor"])[0]
            if len(hh):
                mapout = rb + int(hh[0]) * 4
        if hero is None:
            hh = np.nonzero(u == vt["CGameEventManager"])[0]
            if len(hh):
                hero = rb + int(hh[0]) * 4
        if mapout and hero:
            break
    hx = pm.read_float(hero + 0x8)
    hy = -pm.read_float(hero + 0xC)
    col = Collision(pm, PROC)
    col.set_mapout(mapout)
    col.refresh()
    # cel: 1500 jedn. na wschod + 800 na poludnie (przez cokolwiek jest po drodze)
    gx, gy = hx + 1500, hy + 1500
    path = col.find_path(hx, hy, gx, gy)
    print("hero=(%.0f,%.0f) cel=(%.0f,%.0f)" % (hx, hy, gx, gy))
    print("trasa (%d pkt): %s" % (len(path) if path else -1,
                                  [(int(p[0]), int(p[1])) for p in path] if path else None))
    # ASCII: '#'blok '.'wolne '@'hero 'G'cel '*'trasa
    route = set((int(px) // CELL, int(py) // CELL) for px, py in (path or []))
    cx, cy = int(hx) // CELL, int(hy) // CELL
    gcx, gcy = int(gx) // CELL, int(gy) // CELL
    R = 22
    for gyy in range(min(cy, gcy) - 4, max(cy, gcy) + 5):
        row = []
        for gxx in range(min(cx, gcx) - 4, max(cx, gcx) + 5):
            if (gxx, gyy) == (cx, cy):
                row.append("@")
            elif (gxx, gyy) == (gcx, gcy):
                row.append("G")
            elif (gxx, gyy) in route:
                row.append("*")
            elif col._cell_block(gxx, gyy):
                row.append("#")
            else:
                row.append(".")
        print("".join(row))
