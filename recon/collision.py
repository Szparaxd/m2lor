"""Odczyt siatki kolizji (ATTRIBUTE_BLOCK) z pamieci klienta + pathfinding (A*) do omijania przeszkod.

Uklad pamieci (GameCoreTop, z analizy CMapOutdoor::isAttrOn; patrz recon/probe_collision.py):
  CMapOutdoor M: M+0x40 CTerrain* m_pTerrain[9] (3x3 wokol gracza); M+0x70/0x72 countX/Y (short);
                 M+0x74/0x76 curCoordX/Y (short).
  CTerrain  T:  T+ATTR_OFF  BYTE m_abyAttrMap[256*256], index = ly*256 + lx.
  sektor = 25600 jedn. = 256 komorek; komorka = 100 jedn. = 1 px minimapy; ATTRIBUTE_BLOCK = bit0.
Konwencja: iX=wx, iY=wy (wsp. swiata radaru). Offsety SA PER BUILD (tu GameCoreTop) - dla vCore inne.

Watki: obiekt jest uzywany z petli bota, z GUI (rysowanie siatki) i ze skanu w tle - stan (cache
terenow, curX/curY) chroni RLock. A* liczy na lokalnej kopii siatki (numpy), juz bez blokady.
"""
import heapq
import math
import struct
import threading
import time

import numpy as np

SECTOR = 25600
CELL = 100
NCELL = 256
BLOCK = 0x01
WATER = 0x02
TERR_SETTLE = 3.0     # s: max czekania na przestawienie m_pTerrain po zmianie cur (wczytywanie sektorow)
NONE_RETRY = 1.0      # s: niezaladowany sektor (None) sprawdzamy ponownie po tym czasie (nie cache'ujemy na stale)
MAX_EXPAND = 20000    # limit rozwinietych wezlow A* (ochrona CPU przy nieosiagalnym celu)
LOS_WINDOW = 40       # wygladzanie LOS: sprawdzaj najwyzej tyle komorek do przodu (zamiast O(n^2) do konca)

# Offsety per build (nazwa procesu -> dict). GameCoreTop zweryfikowany na zywo.
BUILD_OFFSETS = {
    "gamecoretop.exe": dict(terrain_arr=0x40, count=0x70, cur=0x74, attr=0x18E1A),
    # vCore (2026-10-28): ten sam uklad - CMapOutdoor+0x40 = 9x CTerrain*, 0x70/0x74 short; attrMap 0x18E1A
    # potwierdzony ciagloscia szwow miedzy sasiednimi sektorami (43/3072 niezgodnosci vs ~600 dla +-2..16).
    "vcore.exe": dict(terrain_arr=0x40, count=0x70, cur=0x74, attr=0x18E1A),
}


class Collision:
    """Czyta siatke przechodniosci z CMapOutdoor. set_mapout() z radaru, refresh() co skan."""

    def __init__(self, pm, proc_name):
        self.pm = pm
        self.off = BUILD_OFFSETS.get(proc_name.lower())
        self.mapout = None
        self.ok = self.off is not None
        self._lock = threading.RLock()
        self._terr = {}         # (tcx,tcy) -> (np.uint8[256,256] (ly,lx) albo None, czas odczytu)
        self._cur = None        # (curX,curY) gdy cache terenow aktualny
        self._ptrs = None       # m_pTerrain[9] z ostatniego refresh() (zmiana -> cache nieaktualny)
        self._settle_until = 0.0  # do tej chwili cur juz nowy, a wskazniki stare -> tereny niepewne
        self.countX = self.countY = 0
        self.curX = self.curY = 0

    def set_mapout(self, mapout):
        with self._lock:
            if mapout != self.mapout:
                self.mapout = mapout
                self._terr.clear()
                self._cur = None
                self._ptrs = None

    def refresh(self):
        """Odczyt pol CMapOutdoor; gdy zmienil sie curCoord (gracz przeszedl sektor) - wyczysc cache."""
        with self._lock:
            if not self.ok or not self.mapout:
                return False
            m, o = self.mapout, self.off
            # JEDEN odczyt: m_pTerrain[9] + count + cur (spojna migawka zamiast osobnych odczytow)
            lo = o["terrain_arr"]
            try:
                raw = self.pm.read_bytes(m + lo, o["cur"] + 4 - lo)
            except Exception:
                return False
            ptrs = struct.unpack_from("<9I", raw, 0)
            countX, countY = struct.unpack_from("<2h", raw, o["count"] - lo)
            cx, cy = struct.unpack_from("<2h", raw, o["cur"] - lo)
            self.countX, self.countY = countX, countY
            # curX/curY PRZED czyszczeniem cache: _terrain liczy slot 3x3 wzgledem nich
            self.curX, self.curY = cx, cy
            # Klient (CMapOutdoor::Update) najpierw ustawia NOWY cur, potem wczytuje sektory, a dopiero na
            # koncu AssignTerrainPtr przestawia m_pTerrain[9]. W tym oknie slot wyliczony z nowego cur wskazuje
            # teren SASIEDNIEGO sektora - wczesniej trafial do cache na stale (zly sektor -> A* przez wode).
            # Dlatego cache czyscimy przy zmianie cur ORAZ przy kazdej zmianie wskaznikow, a gdy cur sie zmienil,
            # a wskazniki jeszcze nie - nie ufamy terenom (None) do ich przestawienia (max TERR_SETTLE s).
            if (cx, cy) != self._cur:
                if self._cur is not None and ptrs == self._ptrs:
                    self._settle_until = time.time() + TERR_SETTLE
                self._cur = (cx, cy)
                self._terr.clear()
            elif ptrs != self._ptrs:
                self._settle_until = 0.0
                self._terr.clear()
            self._ptrs = ptrs
            return True

    def _terrain(self, tcx, tcy):
        """Tablica atrybutow [256,256] danego sektora (z cache); None gdy niezaladowany.
        None NIE jest trzymane na stale: sektor dopiero wczytywany przez klienta sprawdzamy znow po NONE_RETRY."""
        with self._lock:
            key = (tcx, tcy)
            hit = self._terr.get(key)
            if hit is not None and (hit[0] is not None or time.time() - hit[1] < NONE_RETRY):
                return hit[0]
            arr = None
            num = (tcy - self.curY + 1) * 3 + (tcx - self.curX + 1)
            settling = time.time() < self._settle_until      # cur nowy, wskazniki stare -> nie zgaduj
            if 0 <= tcx - self.curX + 1 < 3 and 0 <= num < 9 and self._ptrs and not settling:
                try:
                    p = self._ptrs[num]                     # z migawki refresh(), spojnej z curX/curY
                    if 0x10000 < p < 0x7FFF0000:
                        raw = self.pm.read_bytes(p + self.off["attr"], NCELL * NCELL)
                        arr = np.frombuffer(raw, dtype=np.uint8).reshape(NCELL, NCELL)
                except Exception:
                    arr = None
            self._terr[key] = (arr, time.time())
            return arr

    def is_block(self, wx, wy):
        """True gdy pole swiata (wx,wy) zablokowane. Niezaladowane/poza mapa -> False (nie blokuj)."""
        if wx < 0 or wy < 0 or wx >= self.countX * SECTOR or wy >= self.countY * SECTOR:
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

    def block_grid(self, minx, miny, maxx, maxy):
        """Siatka blokad komorek [miny..maxy, minx..maxx] jako np.bool_ (indeks [gy-miny, gx-minx]).
        Skladana WYCINKAMI z tablic sektorow (numpy), zamiast pytac o kazda komorke osobno."""
        H, W = maxy - miny + 1, maxx - minx + 1
        grid = np.zeros((H, W), dtype=bool)
        with self._lock:
            cX, cY = self.countX, self.countY
            for tcy in range(miny // NCELL, maxy // NCELL + 1):
                if not (0 <= tcy < cY):
                    continue
                for tcx in range(minx // NCELL, maxx // NCELL + 1):
                    if not (0 <= tcx < cX):
                        continue
                    arr = self._terrain(tcx, tcy)
                    if arr is None:
                        continue
                    # czesc wspolna bboxu z sektorem (wsp. komorek globalnych)
                    x0, x1 = max(minx, tcx * NCELL), min(maxx, tcx * NCELL + NCELL - 1)
                    y0, y1 = max(miny, tcy * NCELL), min(maxy, tcy * NCELL + NCELL - 1)
                    sub = arr[y0 - tcy * NCELL:y1 - tcy * NCELL + 1, x0 - tcx * NCELL:x1 - tcx * NCELL + 1]
                    grid[y0 - miny:y1 - miny + 1, x0 - minx:x1 - minx + 1] = (sub & BLOCK) != 0
        return grid

    @staticmethod
    def _dilate(raw, r):
        """Pogrubienie przeszkod o r komorek (kwadrat (2r+1)^2) - przesuniecia numpy zamiast petli."""
        H, W = raw.shape
        pad = np.pad(raw, r, constant_values=False)
        out = np.zeros_like(raw)
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                out |= pad[r + dj:r + dj + H, r + di:r + di + W]
        return out

    def find_path(self, sx, sy, gx, gy, margin=6, max_span=140, inflate=1, extra_blocked=None,
                  wide_margin=30, hard_blocked=None, hard_blobs=None):
        """A* po komorkach (100 jedn.) od (sx,sy) do (gx,gy). Zwraca liste (wx,wy) centrow komorek
        (wygladzona przez LOS) BEZ komorki startu, albo None gdy brak trasy.
        Cel dalszy niz max_span -> trasa do punktu POSREDNIEGO na odcinku (wolajacy po dojsciu planuje dalej).
        Najpierw waski bbox (margin); gdy trasy brak - druga proba z wide_margin (objazd dluzszej sciany).
        extra_blocked: lista (wx,wy) PUNKTOW swiata traktowanych jako przeszkoda (np. metiny) - oprocz mapy.
        hard_blocked: jak extra_blocked, ale NIE zwalniane przy starcie (przeszkody tuz PRZED postacia: mob
        z przodu). Tuz przy starcie (<= 2 komorki) bez pogrubienia. Gdy przez nie brak trasy - proba bez nich.
        hard_blobs: jak hard_blocked, ale ZAWSZE pogrubione (zapamietane miejsca utkniec - nieznany rozmiar)."""
        scx, scy = int(sx) // CELL, int(sy) // CELL
        gcx, gcy = int(gx) // CELL, int(gy) // CELL
        lim = max_span - 2 * margin
        ddx, ddy = gcx - scx, gcy - scy
        if max(abs(ddx), abs(ddy)) > lim:               # za daleko -> posredni cel w zasiegu bboxu
            f = lim / float(max(abs(ddx), abs(ddy)))
            gcx, gcy = scx + int(ddx * f), scy + int(ddy * f)
        hard = (hard_blocked or [], hard_blobs or [])
        tries = [(m, hard) for m in ((margin, wide_margin) if wide_margin > margin else (margin,))]
        # zamurowani twardymi -> luzujemy po kolei: bez zapamietanych plam (moby zostaja), potem bez niczego
        if hard_blobs and hard_blocked:
            tries.append((margin, (hard_blocked, [])))
        if hard_blocked or hard_blobs:
            tries.append((margin, ([], [])))
        for m, hd in tries:
            r = self._find_path_cells(scx, scy, gcx, gcy, m, inflate, extra_blocked, hd)
            if r is not None:
                return r
        # cel (lub punkt posredni na linii prostej) nieosiagalny - np. wypadl w wodzie/skale za mostem:
        # trasa CZESCIOWA do osiagalnego miejsca najblizszego celu (szeroki bbox, moby zostaja przeszkoda).
        # Lepsze niz "brak trasy" -> jazda na wprost w urwisko.
        return self._find_path_cells(scx, scy, gcx, gcy, wide_margin, inflate, extra_blocked,
                                     (hard_blocked or [], []), partial=True)

    def _find_path_cells(self, scx, scy, gcx, gcy, margin, inflate, extra_blocked, hard=([], []), partial=False):
        """A* w bboxie start/cel + margin (komorki). Wynik jak find_path.
        partial: gdy celu nie da sie osiagnac - trasa do osiagalnej komorki NAJBLIZSZEJ celowi (o ile blizej niz start)."""
        minx, maxx = min(scx, gcx) - margin, max(scx, gcx) + margin
        miny, maxy = min(scy, gcy) - margin, max(scy, gcy) + margin
        W = maxx - minx + 1
        raw = self.block_grid(minx, miny, maxx, maxy)
        wall = raw.copy()                                # same sciany (bez encji) - do odblokowania startu
        # dodatkowe przeszkody-encje (metiny/moby): komorki ich zajmujace (poza celem i okolica startu)
        if extra_blocked:
            for ex, ey in extra_blocked:
                cx_, cy_ = int(ex) // CELL - minx, int(ey) // CELL - miny
                if 0 <= cx_ < W and 0 <= cy_ < raw.shape[0]:
                    raw[cy_, cx_] = True
        blk = self._dilate(raw, inflate) if inflate else raw
        lsx, lsy, lgx, lgy = scx - minx, scy - miny, gcx - minx, gcy - miny
        # okolica STARTU: encje (moby tuz przy postaci) nie blokuja w promieniu inflate+2 - tam tylko
        # (pogrubione) sciany; bezposrednio 3x3 wokol startu - same sciany bez pogrubienia. Inaczej
        # tlum/sciana zamurowalyby wszystkie wyjscia -> brak trasy -> utkniecie
        r = inflate + 2
        wall_d = self._dilate(wall, inflate) if inflate else wall
        y0, y1, x0, x1 = max(0, lsy - r), lsy + r + 1, max(0, lsx - r), lsx + r + 1
        blk[y0:y1, x0:x1] = wall_d[y0:y1, x0:x1]
        blk[lsy - 1:lsy + 2, lsx - 1:lsx + 2] = wall[lsy - 1:lsy + 2, lsx - 1:lsx + 2]
        hard_pts, blob_pts = hard
        if hard_pts or blob_pts:                         # twarde: nie zwalniane przy starcie
            near = []                                    # wektory (start -> mob) mobow tuz przy starcie
            for ex, ey, blob in [(x, y, False) for x, y in hard_pts] + [(x, y, True) for x, y in blob_pts]:
                cx_, cy_ = int(ex) // CELL - minx, int(ey) // CELL - miny
                if not (0 <= cx_ < W and 0 <= cy_ < blk.shape[0]):
                    continue
                if not blob and max(abs(cx_ - lsx), abs(cy_ - lsy)) <= 2:
                    near.append(((ex - (scx * CELL + CELL // 2)), (ey - (scy * CELL + CELL // 2))))
                blk[max(0, cy_ - inflate):cy_ + inflate + 1, max(0, cx_ - inflate):cx_ + inflate + 1] = True
            # mob tuz przy postaci (pogrubiony) zamknalby ja ze wszystkich stron -> trasy brak -> jazda na wprost
            # prosto w niego. Odblokuj sasiadow startu po stronie PRZECIWNEJ do takich mobow (droga ucieczki).
            if near:
                for dj in (-1, 0, 1):
                    for di in (-1, 0, 1):
                        y_, x_ = lsy + dj, lsx + di
                        if (di or dj) and not wall[y_, x_] and all(di * vx + dj * vy <= 0 for vx, vy in near):
                            blk[y_, x_] = False
            blk[lsy, lsx] = wall[lsy, lsx]               # sama komorka startu zawsze jak mapa
        blk[lgy, lgx] = blk[lgy, lgx] and wall[lgy, lgx]   # komorki celu nie blokuja encje
        grid = blk.tolist()                              # listy Pythona: szybszy dostep w petli A*
        start, goal = (lsx, lsy), (lgx, lgy)
        if grid[lgy][lgx]:          # cel na blokadzie -> celuj najblizsza wolna komorke w poblizu
            free = self._nearest_free(goal, grid)
            if free is None and not partial:
                return None
            goal = free or goal          # partial: zablokowany cel zostaje tylko kierunkiem (heurystyka)
        H = len(grid)

        def h(a, b):
            return math.hypot(a[0] - b[0], a[1] - b[1])
        openh = [(h(start, goal), 0.0, start)]
        came, g = {}, {start: 0.0}
        found = False
        expanded = 0
        best, best_h = start, h(start, goal)             # partial: osiagalna komorka najblizej celu
        while openh:
            _, gc, cur = heapq.heappop(openh)
            if cur == goal:
                found = True
                break
            if gc > g.get(cur, 1e18):
                continue
            expanded += 1
            if expanded > MAX_EXPAND:
                break
            if partial:
                hc = h(cur, goal)
                if hc < best_h:
                    best, best_h = cur, hc
            cx, cy = cur
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = cx + di, cy + dj
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                if grid[ny][nx]:
                    continue
                if di and dj and (grid[cy][cx + di] or grid[cy + dj][cx]):
                    continue  # nie przecinaj rogu przez dwie przeszkody
                ng = gc + (1.4142 if di and dj else 1.0)
                if ng < g.get((nx, ny), 1e18):
                    g[(nx, ny)] = ng
                    came[(nx, ny)] = cur
                    heapq.heappush(openh, (ng + h((nx, ny), goal), ng, (nx, ny)))
        if not found:
            if not (partial and best != start and best_h < h(start, goal) - 3):
                return None
            goal = best
        # odtworz sciezke komorek
        cells = [goal]
        while cells[-1] != start:
            cells.append(came[cells[-1]])
        cells.reverse()
        # wygladzanie LOS: zostaw tylko punkty, gdzie trzeba skrecic (okno LOS_WINDOW - ograniczony koszt)
        smooth = [cells[0]]
        i = 0
        while i < len(cells) - 1:
            j = min(len(cells) - 1, i + LOS_WINDOW)
            while j > i + 1 and not self._los_clear(cells[i], cells[j], grid):
                j -= 1
            smooth.append(cells[j])
            i = j
        # na wsp. swiata (centra komorek), bez komorki startu
        return [((c[0] + minx) * CELL + CELL // 2, (c[1] + miny) * CELL + CELL // 2) for c in smooth[1:]]

    @staticmethod
    def _nearest_free(cell, grid, r=15):
        H, W = len(grid), len(grid[0])
        for rad in range(1, r + 1):
            for dj in range(-rad, rad + 1):
                for di in range(-rad, rad + 1):
                    x, y = cell[0] + di, cell[1] + dj
                    if 0 <= x < W and 0 <= y < H and not grid[y][x]:
                        return (x, y)
        return None

    def line_clear(self, x0, y0, x1, y1):
        """LOS w WSPOLRZEDNYCH SWIATA (nie komorkach): probkuje odcinek co pol komorki wprost z mapy.
        True gdy zadne probkowane pole nie jest zablokowane. Uzywane do celowania z wyprzedzeniem
        (aim) - mozna celowac dalej do przodu tylko gdy droga na wprost jest wolna."""
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(d / (CELL * 0.5)))
        for i in range(n + 1):
            t = i / n
            if self.is_block(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
                return False
        return True

    @staticmethod
    def _los_clear(c0, c1, grid):
        """Bresenham: czy linia komorek c0->c1 jest wolna od blokad (grid[y][x]).
        Krok po przekatnej jak w A*: nie wolno przeciac rogu miedzy dwiema przeszkodami."""
        (x0, y0), (x1, y1) = c0, c1
        H, W = len(grid), len(grid[0])
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx - dy
        x, y = x0, y0
        while True:
            if not (0 <= x < W and 0 <= y < H) or grid[y][x]:
                return False
            if (x, y) == (x1, y1):
                return True
            e2 = 2 * err
            px, py = x, y
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
            if x != px and y != py and (grid[py][x] or grid[y][px]):
                return False


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
