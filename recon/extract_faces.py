"""Portrety klas z paczki klienta (ETC) -> data/faces/*.png (do radaru i panelu WWW).

Klient opisuje ikony plikami .sub ("subImage"): nazwa atlasu DDS (wzgledem katalogu .sub) + prostokat
left/top/right/bottom. Bierzemy dwa zestawy:
  ui/intro/public_intro/face/face_<klasa>_<m|w>_01.sub  -> data/faces/<klasa>_<m|w>.png  (z plcia)
  ui/game/windows/face_<klasa>.sub                      -> data/faces/<klasa>.png        (okno postaci)

Uzycie:  python recon/extract_faces.py [sciezka_do_katalogu_pack]
"""
import io
import os
import re
import sys

from PIL import Image, ImageDraw

import eterpack
from identity import FACES_DIR

DEFAULT_PACK = r"C:\Metin\Terenzo 2024 - Muchomor Upgrade\GameCore\Klient Terenzo 2024\pack"
UI_DIR = "d:/ymir work/ui/"
_SUB_RE =re.compile(r'image\s+"([^"]+)".*?left\s+(\d+).*?top\s+(\d+).*?right\s+(\d+).*?bottom\s+(\d+)', re.S)


def sub_crop(ep, sub_name, atlases):
    """Wytnij obraz opisany plikiem .sub (atlasy DDS cache'owane w `atlases`)."""
    m = _SUB_RE.search(ep.get(sub_name).decode("latin-1"))
    if not m:
        raise ValueError("nie rozumiem %s" % sub_name)
    img, l, t, r, b = m.group(1), *map(int, m.groups()[1:])
    # atlas lezy w katalogu UI ("d:/ymir work/ui/<img>"), nie obok .sub; zapasowo - obok .sub
    path = next((p for p in (UI_DIR + img.lower(), sub_name.rsplit("/", 1)[0] + "/" + img.lower())
                 if p in atlases or ep.list(p)), None)
    if path is None:
        raise KeyError("brak atlasu %s" % img)
    if path not in atlases:
        atlases[path] = Image.open(io.BytesIO(ep.get(path))).convert("RGBA")
    return atlases[path].crop((l, t, r, b))


def main():
    pack_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PACK
    ep = eterpack.EterPack(os.path.join(pack_dir, "ETC"))
    os.makedirs(FACES_DIR, exist_ok=True)
    atlases, n = {}, 0
    for name in ep.list("face"):
        m = (re.search(r"public_intro/face/face_(\w+?)_([mw])_01\.sub$", name)
             or re.search(r"game/windows/face_(warrior|assassin|sura|shaman|wolfman)\.sub$", name))
        if not m:
            continue
        out = "_".join(m.groups()) + ".png"
        try:
            img = sub_crop(ep, name, atlases)
            if "public_intro" in name:   # okragla ramka na bialym tle -> rogi przezroczyste
                mask = Image.new("L", img.size, 0)
                ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
                img.putalpha(mask)
            img.save(os.path.join(FACES_DIR, out))
            n += 1
            print("  %-22s <- %s" % (out, name))
        except Exception as e:
            print("  [!] %s: %s" % (name, e))
    print("zapisano %d portretow do %s" % (n, FACES_DIR))


if __name__ == "__main__":
    main()
