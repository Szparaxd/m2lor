"""
Zasoby mapy dla radar_map.py: z klienta (EterPack 'Outdoor') buduje pelny obraz
minimapy danej mapy (mozaika sektorow minimap.dds) + przeliczenie swiat->piksel.

POTWIERDZONE:
- Mapy klienta leza w pack/Outdoor.(eix|epk), sciezki np. 'metin2_map_t1/setting.txt',
  'metin2_map_t1/<SSSTTT>/minimap.dds' gdzie SSSTTT = '%03d%03d'%(sektorX, sektorY).
- setting.txt: CellScale 200, MapSize <sx> <sy> (sektory), BasePosition <bx> <by>.
- 1 sektor = 128 komorek * CellScale(200) = 25600 jednostek swiata.
- minimap.dds = 256x256 px (DXT1) na sektor  ->  1 px = 100 jednostek swiata.
- Obraz skladany: sektor (sx,sy) -> kafel na (sx*256, sy*256)  (sy rosnie W DOL, jak wy).
- swiat->piksel:  px = wx/100 ;  py = wy/100   (wspolrzedne LOKALNE mapy, bez BasePosition).
  Wsp. swiata gracza/mobow/metinow: X = hero+0x8, Y = -(hero+0xC)  (patrz notes M1/M3/M4).

Cache: zbudowany PNG zapisujemy w radar-project/data/maps/<map>.png (+ .json z meta),
aby kolejne uruchomienia byly natychmiastowe.
"""
import io
import json
import os

from eterpack import EterPack

PACK_DIR = r"C:/Metin/Terenzo 2024 - Muchomor Upgrade/GameCore/Klient Terenzo 2024/pack"
TILE_PX = 256
SECTOR_WORLD = 25600.0
WORLD_PER_PX = 100.0  # 25600 / 256

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", "data", "maps"))


def parse_setting(txt):
    """Zwraca dict z MapSize (sx,sy) i BasePosition (bx,by) z setting.txt."""
    sx = sy = 1
    bx = by = 0.0
    cell = 200.0
    for line in txt.splitlines():
        p = line.split()
        if not p:
            continue
        k = p[0].lower()
        if k == "mapsize" and len(p) >= 3:
            sx, sy = int(p[1]), int(p[2])
        elif k == "baseposition" and len(p) >= 3:
            bx, by = float(p[1]), float(p[2])
        elif k == "cellscale" and len(p) >= 2:
            cell = float(p[1])
    return {"sx": sx, "sy": sy, "bx": bx, "by": by, "cell": cell}


class MapImage:
    """Pelny obraz minimapy mapy + przeliczenie swiat<->piksel."""

    def __init__(self, name, pil_image, meta):
        self.name = name
        self.image = pil_image            # PIL.Image RGBA, (W_px, H_px)
        self.w_px, self.h_px = pil_image.size
        # Klient renderuje w LOKALNYCH wspolrzednych mapy (0-bazowych) — pozycje gracza/mobow
        # czytane z pamieci sa lokalne. Globalny BasePosition z setting.txt jest SERWEROWY,
        # wiec dla nakladki uzywamy bazy 0 (nie odejmujemy meta['bx']/['by']).
        self.bx = 0.0
        self.by = 0.0
        self.base_global = (meta["bx"], meta["by"])  # zachowane tylko informacyjnie
        self.sx = meta["sx"]
        self.sy = meta["sy"]
        # odbicie osi, dostrajalne na zywo z radaru (wy rosnie w dol obrazu -> bez odbicia)
        self.flip_y = False
        self.flip_x = False

    def world_to_image(self, wx, wy):
        lx = (wx - self.bx) / WORLD_PER_PX
        ly = (wy - self.by) / WORLD_PER_PX
        px = (self.w_px - lx) if self.flip_x else lx
        py = (self.h_px - ly) if self.flip_y else ly
        return px, py


def _load_pack():
    return EterPack(os.path.join(PACK_DIR, "Outdoor"))


def build_map_image(name, pack=None, use_cache=True):
    """Buduje (lub wczytuje z cache) obraz minimapy mapy 'name' (np. 'metin2_map_t1')."""
    from PIL import Image

    os.makedirs(CACHE_DIR, exist_ok=True)
    png_path = os.path.join(CACHE_DIR, name.replace("/", "_") + ".png")
    meta_path = os.path.join(CACHE_DIR, name.replace("/", "_") + ".json")

    if use_cache and os.path.exists(png_path) and os.path.exists(meta_path):
        with open(meta_path, "r") as f:
            meta = json.load(f)
        img = Image.open(png_path).convert("RGBA")
        return MapImage(name, img, meta)

    if pack is None:
        pack = _load_pack()
    meta = parse_setting(pack.get(name + "/setting.txt").decode("latin-1"))
    sx, sy = meta["sx"], meta["sy"]
    canvas = Image.new("RGBA", (sx * TILE_PX, sy * TILE_PX), (10, 14, 10, 255))
    placed = 0
    for ix in range(sx):
        for iy in range(sy):
            folder = "%03d%03d" % (ix, iy)
            key = "%s/%s/minimap.dds" % (name, folder)
            if key.lower().replace("\\", "/") not in pack.entries:
                continue
            try:
                dds = pack.get(key)
                tile = Image.open(io.BytesIO(dds)).convert("RGBA")
            except Exception:
                continue
            left = ix * TILE_PX
            # wiekszy iy -> NIZEJ (zweryfikowane szwami: blad 5.1 vs 31.6 przy odwrotnej kolejnosci)
            top = iy * TILE_PX
            canvas.paste(tile, (left, top))
            placed += 1
    canvas.save(png_path)
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    print("[map] zbudowano %s: %dx%d px, sektorow %d/%d -> %s"
          % (name, canvas.width, canvas.height, placed, sx * sy, png_path))
    return MapImage(name, canvas, meta)


def map_registry(pack=None):
    """Rejestr {map_name: meta} z setting.txt wszystkich map w Outdoor (do auto-detekcji)."""
    if pack is None:
        pack = _load_pack()
    reg = {}
    suffix = "/setting.txt"
    for n in pack.names:
        n2 = n.replace("\\", "/")
        if n2.lower().endswith(suffix):
            mapname = n2[:-len(suffix)]          # np. 'metin2_map_t1' lub 'season1/metin2_map_x'
            try:
                reg[mapname] = parse_setting(pack.get(mapname + "/setting.txt").decode("latin-1"))
            except Exception:
                pass
    return reg


def detect_map(wx, wy, pack=None):
    """Najlepsze dopasowanie mapy po zawieraniu (wx,wy) w [base, base+size*25600].
    UWAGA: wiele map ma BasePosition 0 0 -> moze byc niejednoznaczne; zwraca liste kandydatow."""
    reg = map_registry(pack)
    hits = []
    for name, m in reg.items():
        x0, y0 = m["bx"], m["by"]
        x1 = x0 + m["sx"] * SECTOR_WORLD
        y1 = y0 + m["sy"] * SECTOR_WORLD
        if x0 <= wx < x1 and y0 <= wy < y1:
            area = m["sx"] * m["sy"]
            hits.append((area, name, m))
    hits.sort()   # mniejsza mapa = ciasniejsze dopasowanie = bardziej prawdopodobne
    return [(n, m) for _, n, m in hits]


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "metin2_map_t1"
    mi = build_map_image(name, use_cache=False)
    print("OK", mi.name, mi.image.size, "base", mi.bx, mi.by, "size", mi.sx, mi.sy)
