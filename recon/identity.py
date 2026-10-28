"""Tozsamosc postaci w kliencie: nick, klasa, plec, poziom (do tytulu okna, panelu, pozniej panelu WWW).

Zrodla (black-box, z pamieci):
- NICK: std::string w CPythonPlayer. GameCoreTop: +0x14 (zweryfikowane). Inne buildy (vCore - pola
  przesuniete) -> sonda: pierwszy std::string w poczatku obiektu, ktory wyglada jak nick.
- KLASA + PLEC: sciezka modelu WLASNEGO aktora (CRaceData, radar._actor_model):
  d:/ymir work/pc/<klasa>/...  -> rasy 0-3 (woj. M, ninja K, sura M, szaman K)
  d:/ymir work/pc2/<klasa>/... -> rasy 4-7 (ta sama klasa, druga plec)
  (sprawdzone w paczkach PC/pc1 = pc/, pc2/pc3 = pc2/).
- Zapas dla klasy: kolumna player.player.job w bazie serwera (po nicku) - tylko gdy modelu brak.
- POZIOM: punkt [1] (POINT_LEVEL) z tablicy punktow (radar juz ja czyta).
"""
import os
import re

NICK_OFFSETS = {"gamecoretop.exe": 0x14}   # znane offsety std::string nicku w CPythonPlayer
NICK_PROBE_SPAN = 0x200                     # sonda: tyle bajtow poczatku CPythonPlayer
NICK_RE = re.compile(r"^[A-Za-z0-9_\-\[\]]{2,24}$")

CLASS_PL = {"warrior": "Wojownik", "assassin": "Ninja", "sura": "Sura", "shaman": "Szaman", "wolfman": "Lykan"}
# job/rasa z bazy (0..7) -> (klasa, plec)
RACES = {0: ("warrior", "m"), 1: ("assassin", "w"), 2: ("sura", "m"), 3: ("shaman", "w"),
         4: ("warrior", "w"), 5: ("assassin", "m"), 6: ("sura", "w"), 7: ("shaman", "m"), 8: ("wolfman", "m")}
_MODEL_RE = re.compile(r"/(pc|pc2|pc3)/(warrior|assassin|sura|shaman|wolfman)/")

FACES_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "faces"))
DB = dict(host="192.168.56.106", port=3306, user="root", password="123", database="player")   # lokalny test


def read_std_string(pm, a, enc="cp1250"):
    """MSVC std::string @a: buf[16] | size@+0x10 | cap@+0x14 (cap>=16 -> buf to wskaznik). None gdy smieci."""
    try:
        size = pm.read_uint(a + 0x10)
        cap = pm.read_uint(a + 0x14)
        if not (0 < size <= cap < 0x1000):
            return None
        src = a if cap < 16 else pm.read_uint(a)
        return pm.read_bytes(src, size).decode(enc)
    except Exception:
        return None


def read_nick(pm, player_obj, proc_name, hint=None):
    """(nick, offset) z CPythonPlayer albo (None, None). hint = offset znaleziony wczesniej (sonda raz)."""
    if not player_obj:
        return None, None
    cands = [o for o in (hint, NICK_OFFSETS.get(proc_name.lower())) if o is not None]
    for off in cands:
        s = read_std_string(pm, player_obj + off)
        if s and NICK_RE.match(s):
            return s, off
    for off in range(0, NICK_PROBE_SPAN, 4):   # inny build: pierwszy std::string wygladajacy jak nick
        s = read_std_string(pm, player_obj + off)
        if s and NICK_RE.match(s):
            return s, off
    return None, None


def class_from_model(model):
    """(klasa, plec) ze sciezki modelu aktora gracza albo (None, None)."""
    m = _MODEL_RE.search(model or "")
    if not m:
        return None, None
    folder, cls = m.groups()
    base = {"warrior": "m", "assassin": "w", "sura": "m", "shaman": "w", "wolfman": "m"}[cls]
    sex = base if folder == "pc" or cls == "wolfman" else ("w" if base == "m" else "m")
    return cls, sex


def class_from_db(nick, timeout=1.0):
    """(klasa, plec) z player.player.job po nicku (lokalny serwer) albo (None, None)."""
    try:
        import pymysql
        con = pymysql.connect(connect_timeout=timeout, read_timeout=timeout, **DB)
        try:
            with con.cursor() as cur:
                cur.execute("SELECT job FROM player WHERE name=%s LIMIT 1", (nick,))
                row = cur.fetchone()
        finally:
            con.close()
        return RACES.get(int(row[0]), (None, None)) if row else (None, None)
    except Exception:
        return None, None


def face_path(cls, sex):
    """Sciezka PNG portretu (data/faces/<klasa>_<m|w>.png; wyciaga recon/extract_faces.py) albo None."""
    if not cls:
        return None
    for name in ("%s_%s.png" % (cls, sex), "%s.png" % cls):
        p = os.path.join(FACES_DIR, name)
        if os.path.isfile(p):
            return p
    return None


def label(ident):
    """'Nick - Wojownik lv 25' (brakujace czesci pomijane)."""
    parts = [ident.get("nick") or "?"]
    if ident.get("cls"):
        parts.append("- " + CLASS_PL.get(ident["cls"], ident["cls"]))
    if ident.get("level"):
        parts.append("lv %d" % ident["level"])
    return " ".join(parts)
