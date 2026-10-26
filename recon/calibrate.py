"""
Auto-kalibracja offsetow POL na zywych danych - uzupelnienie rtti.py.

RTTI znajduje vtable po nazwie klasy, ale gdy ktos doda pole w klasie (np. we wspolnej klasie
bazowej), przesuwaja sie offsety pol (zaobserwowane: przekompilowany GameCoreTop.exe -> +4 w aktorze
i efekcie, -8 przy nazwie). Zamiast wpisywac nowe liczby, wyznaczamy je przy starcie tymi samymi
metodami, ktorymi znalezlismy je recznie:

  mob_x       - aktor GRACZA ma dokladnie pozycje z CGameEventManager (X, +Y)
  race        - pole wskazujace na obiekt z vtable CRaceData (RTTI)
  race_model  - std::string *.gr2 w CRaceData (sciezka modelu .../monster/<FOLDER>/...)
  vnum        - u32 przed aktorem, ktorego FOLDER z mob_proto zgadza sie z modelem
  name        - std::string przed aktorem rowny nazwie z mob_names.txt dla tego vnum
  eff_data / eff_fname - wskaznik w efekcie -> std::string z plikiem *.mse
  eff_x       - 0x10 przed eff_data (X, -Y, Z, 1.0); potwierdzane efektami z pozycja przy graczu

Dla kazdego pola wybierany jest offset z najwieksza liczba zgodnych obiektow; remis -> wartosc
domyslna, jesli jest wsrod remisujacych, inaczej najblizsza zera. Brak danych -> domyslna.
"""
import collections
import math
import os
import struct

import rtti

SAMPLE = 40          # ile obiektow badamy przy drozszych testach (odczyty przez ReadProcessMemory)
SPAN = 0x1000        # zakres szukania offsetow


def _pick(counter, default, min_hits=1):
    if not counter:
        return default, 0
    best = max(counter.values())
    if best < min_hits:
        return default, best
    cands = [k for k, n in counter.items() if n == best]
    if default in cands:
        return default, best
    return min(cands, key=lambda k: (abs(k) if isinstance(k, int) else k)), best


def _read(pm, addr, n):
    try:
        return pm.read_bytes(addr, n)
    except Exception:
        return None


def _std_string_in(pm, blk, i, maxlen=200, enc="latin-1"):
    """std::string (MSVC x86: buf[16]|ptr, size@+0x10, cap@+0x14) lezacy w blk na pozycji i."""
    if i + 0x18 > len(blk):
        return None
    size, cap = struct.unpack_from("<II", blk, i + 0x10)
    if not (0 < size <= cap < 1_000_000) or size > maxlen:
        return None
    if cap < 16:
        raw = blk[i:i + size]
    else:
        raw = _read(pm, struct.unpack_from("<I", blk, i)[0], size)
        if raw is None:
            return None
    return raw.decode(enc, "ignore")


def load_proto_tables(proto_dir):
    """(vnum->folder, vnum->nazwa) z Dump_Proto; puste przy braku plikow."""
    folder, names = {}, {}
    try:
        with open(os.path.join(proto_dir, "mob_proto.txt"), encoding="latin-1") as f:
            next(f)
            for line in f:
                c = line.split("\t")
                if len(c) > 12 and c[0].isdigit():
                    folder[int(c[0])] = c[12].lower()
        with open(os.path.join(proto_dir, "mob_names.txt"), encoding="cp1250") as f:
            next(f)
            for line in f:
                a, _, b = line.rstrip("\r\n").partition("\t")
                if a.isdigit():
                    names[int(a)] = b.strip()
    except OSError:
        pass
    return folder, names


def calibrate(pm, base, size, actors, effs, hx, hy, defaults, proto_dir):
    """Zwraca (offsety, raport). offsety = dict jak defaults; raport = lista linii tekstu."""
    off = dict(defaults)
    report = []

    def note(key, val, hits, how):
        off[key] = val
        d = defaults[key]
        fmt = (lambda v: "%s0x%X" % ("-" if v < 0 else "", abs(v)))
        mark = "" if val == d else "  (domyslnie %s)" % fmt(d)
        report.append("%-10s = %-7s zgodnych %3d  [%s]%s" % (key, fmt(val), hits, how, mark))

    # --- mob_x: aktor gracza (i wierzchowca) stoi dokladnie w pozycji gracza ---
    c = collections.Counter()
    for o in actors:
        blk = _read(pm, o, SPAN)
        if blk is None:
            continue
        f = struct.unpack("<%df" % (SPAN // 4), blk)
        for i in range(len(f) - 1):
            if abs(f[i] - hx) < 50 and abs(f[i + 1] - hy) < 50:
                c[i * 4] += 1
    note("mob_x", *_pick(c, defaults["mob_x"]), how="aktor gracza")

    # --- race: wskaznik na CRaceData; race_model: *.gr2 w CRaceData ---
    try:
        race_vt = rtti.find_vtables(pm, base, size, ["CRaceData"])["CRaceData"]
    except LookupError:
        race_vt = None
    c, models_at = collections.Counter(), {}
    if race_vt:
        for o in actors[:SAMPLE * 2]:
            blk = _read(pm, o, SPAN)
            if blk is None:
                continue
            for i in range(0, SPAN, 4):
                p = struct.unpack_from("<I", blk, i)[0]
                if 0x10000 < p < 0x7FFF0000 and p not in models_at:
                    v = _read(pm, p, 4)
                    models_at[p] = v is not None and struct.unpack("<I", v)[0] == race_vt
                if models_at.get(p):
                    c[i] += 1
    note("race", *_pick(c, defaults["race"], min_hits=3), how="RTTI CRaceData")
    c = collections.Counter()
    race_objs = []
    for o in actors:
        b = _read(pm, o + off["race"], 4)
        if b:
            race_objs.append((o, struct.unpack("<I", b)[0]))
    for _, p in race_objs[:SAMPLE]:
        blk = _read(pm, p, 0x118)
        if blk is None:
            continue
        for i in range(0, 0x100, 4):
            s = _std_string_in(pm, blk, i)
            if s and s.lower().endswith(".gr2"):
                c[i] += 1
    note("race_model", *_pick(c, defaults["race_model"], min_hits=3), how="*.gr2")

    # --- vnum: FOLDER(vnum) zgodny ze sciezka modelu; name: zgodna z mob_names ---
    folder, names = load_proto_tables(proto_dir)
    models = {}
    for o, p in race_objs:
        blk = _read(pm, p + off["race_model"], 0x18)
        m = _std_string_in(pm, blk, 0) if blk else None
        if m and ("/monster/" in m.lower().replace("\\", "/") or "/npc/" in m.lower().replace("\\", "/")):
            models[o] = m.lower().replace("\\", "/")
    c = collections.Counter()
    for o, m in models.items():
        blk = _read(pm, o - SPAN, SPAN)
        if blk is None:
            continue
        for i in range(0, SPAN, 4):
            fo = folder.get(struct.unpack_from("<I", blk, i)[0])
            if fo and ("/%s/" % fo) in m:
                c[i - SPAN] += 1
    note("vnum", *_pick(c, defaults["vnum"], min_hits=3), how="vnum~model")
    c = collections.Counter()
    for o in list(models)[:SAMPLE]:
        b = _read(pm, o + off["vnum"], 4)
        want = names.get(struct.unpack("<I", b)[0]) if b else None
        blk = _read(pm, o - SPAN, SPAN + 0x18) if want else None
        if blk is None:
            continue
        wlen = len(want.encode("cp1250", "ignore"))
        for i in range(0, SPAN, 4):
            if struct.unpack_from("<I", blk, i + 0x10)[0] != wlen:
                continue
            if _std_string_in(pm, blk, i, enc="cp1250") == want:
                c[i - SPAN] += 1
    note("name", *_pick(c, defaults["name"], min_hits=3), how="mob_names")

    # --- efekty: wskaznik -> std::string *.mse ; pozycja przy graczu ---
    c = collections.Counter()
    for o in effs[:SAMPLE]:
        blk = _read(pm, o, 0x400)
        if blk is None:
            continue
        for po in range(0, 0x400, 4):
            p = struct.unpack_from("<I", blk, po)[0]
            if not 0x10000 < p < 0x7FFF0000:
                continue
            pb = _read(pm, p, 0x118)
            if pb is None:
                continue
            for so in range(0, 0x100, 4):
                s = _std_string_in(pm, pb, so)
                if s and s.lower().endswith(".mse"):
                    c[(po, so)] += 1
                    break
    (po, so), hits = _pick(c, (defaults["eff_data"], defaults["eff_fname"]), min_hits=3)
    note("eff_data", po, hits, how="*.mse")
    note("eff_fname", so, hits, how="*.mse")
    # eff_x: pozycja lezy w stalej odleglosci przed eff_data (X, -Y, Z, 1.0, eff_data - w obu exe
    # 0x10). Efekty z wlasna pozycja w swiecie przy graczu sa rzadkie (zalezy od ekwipunku postaci:
    # swiecenie zbroi/broni; metin w poblizu) - tylko potwierdzaja, domyslnie liczymy od eff_data.
    eff_x_default = off["eff_data"] - (defaults["eff_data"] - defaults["eff_x"])
    c = collections.Counter()
    for o in effs:
        blk = _read(pm, o, 0x800)
        if blk is None:
            continue
        f = struct.unpack("<%df" % 0x200, blk)
        for i in range(len(f) - 1):
            if math.isfinite(f[i]) and abs(f[i] - hx) < 3000 and abs(-f[i + 1] - hy) < 3000:
                c[i * 4] += 1
    val, hits = _pick(c, eff_x_default)
    note("eff_x", val, hits, how="efekty przy graczu" if hits else "eff_data-0x10")
    return off, report
