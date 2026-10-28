"""Ustawienia panelu WWW (data/panel.json): sciezka gry i to, jak hub uruchamia radar.

Wczesniej sciezka do klienta byla wpisana na sztywno w launch.py i autologin.py - teraz zmienia sie ja
w przegladarce (zakladka Ustawienia). Czytane przy kazdym uzyciu, wiec zmiana dziala bez restartu huba.
"""
import json
import os

ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PATH = os.path.join(ROOT_DIR, "data", "panel.json")

DEFAULTS = {
    # pelna sciezka do exe klienta; gra wymaga swojego katalogu jako cwd (packi)
    "game_path": r"C:\Metin\Terenzo 2024 - Muchomor Upgrade\GameCore\Klient Terenzo 2024\GameCoreTop.exe",
    # True = radar z oknem tkinter (stary widok, do diagnostyki); False = bez okna, wszystko w panelu
    "radar_window": False,
}


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {k: data.get(k, v) for k, v in DEFAULTS.items()}


def save(changes):
    """Zapisz zmienione pola (tylko znane klucze, z kontrola typu). Zwraca pelne ustawienia."""
    cur = load()
    for k, v in changes.items():
        if k not in DEFAULTS:
            raise ValueError("nieznane ustawienie: %s" % k)
        if type(v) is not type(DEFAULTS[k]):
            raise ValueError("%s: zly typ" % k)
        if k == "game_path":
            v = v.strip().strip('"')
            if not (v.lower().endswith(".exe") and os.path.isfile(v)):
                raise ValueError("nie ma takiego pliku exe: %s" % v)
        cur[k] = v
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    return cur


def game_path():
    return load()["game_path"]


def game_exe():
    return os.path.basename(game_path())
