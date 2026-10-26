"""
Czytnik EterPack (format klienta Terenzo 2024).

Potwierdzone ze zrodel (Source Client/Client/EterBase/{lzo,tea}.cpp,
EterPack/EterPack.cpp) oraz empirycznie z plikow pack/*.eix/*.epk:

- .eix (indeks) i kazdy plik w .epk to CLZObject:
    THeader (16 B, little-endian): dwFourCC='MCOZ', dwEncryptSize, dwCompressedSize, dwRealSize
    po naglowku: strumien danych.
  Jesli dwEncryptSize != 0  -> dane sa zaszyfrowane TEA (classic, 32 rundy, DELTA 0x9E3779B9,
    blok 8B, tryb ECB), klucz = s_adwEterPackKey. Po odszyfrowaniu pierwsze 4 B == 'MCOZ' (wew.),
    dalej LZO1X skompresowane (dwCompressedSize bajtow) -> dwRealSize bajtow.
  Jesli dwEncryptSize == 0  -> brak szyfrowania; po THeader jest wew. 'MCOZ'(4B) + LZO1X.
- Indeks po dekompresji: [DWORD fourcc][DWORD ver=2][long count] + count * TEterPackIndex(192 B).
  TEterPackIndex (#pragma pack(4)): long id; char filename[161]; DWORD filename_crc;
    long real_data_size; long data_size; DWORD data_crc; long data_position; char compressed_type;
  -> rozmiar 192 B (potwierdzone: (dwRealSize-12)/192 == count calkowite).

Klucz TEA = s_adwEterPackKey z EterPack.cpp (== [xtea:1] z EPack32.cfg dla fourcc MCOZ):
    45129401, 92367215, 681285731, 1710201
"""
import struct

ETERPACK_KEY = (45129401, 92367215, 681285731, 1710201)
# Klucze TEA per typ pliku (compressed_type w indeksie) = [xtea:N] z EPack32.cfg.
# Potwierdzone empirycznie: indeks (.eix) i ctype=1 -> klucz1; ctype=2 (.txt/.py/.msm) -> klucz2.
# Pliki moga miec enc=0 (brak szyfrowania, tylko LZO) - wtedy klucz nieuzywany (np. minimap.dds).
TYPE_KEYS = {
    1: (45129401, 92367215, 681285731, 1710201),
    2: (0x04b4b822, 0x1f6eb264, 0x0018eaae, 0x1cfbf6a6),
    3: (0x0002a4a1, 0x045415aa, 0x185a8be7, 0x01aad6ab),
    4: (0x00497446, 0x00004a0b, 0x00086eb7, 0x0068189d),
}
MCOZ = 0x5A4F434D  # 'MCOZ' jako DWORD little-endian (bajty 'M','C','O','Z')
DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF


def _tea_decode(y, z, key):
    s = (DELTA * 32) & MASK
    for _ in range(32):
        z = (z - (((((y << 4) & MASK) ^ (y >> 5)) + y & MASK) ^ (s + key[(s >> 11) & 3] & MASK))) & MASK
        s = (s - DELTA) & MASK
        y = (y - (((((z << 4) & MASK) ^ (z >> 5)) + z & MASK) ^ (s + key[s & 3] & MASK))) & MASK
    return y, z


def tea_decrypt(data, key):
    """TEA ECB, blokami 8B (2 DWORD). data dlugosci wielokrotnosci 8."""
    out = bytearray(len(data))
    n = len(data) // 8
    for i in range(n):
        y, z = struct.unpack_from("<II", data, i * 8)
        y, z = _tea_decode(y, z, key)
        struct.pack_into("<II", out, i * 8, y, z)
    return bytes(out)


def lzo1x_decompress(s, dst_len):
    """Czysty Python port minilzo lzo1x_decompress (niesejf). s=bytes skompresowane."""
    s = bytes(s)
    o = bytearray()
    ip = 0

    def copy_match(pos, length):
        # kopiowanie z obsluga nakladania (overlap)
        for _ in range(length):
            o.append(o[pos]); pos += 1

    t = 0
    label = "start"

    # --- inicjalizacja ---
    if s[ip] > 17:
        t = s[ip] - 17; ip += 1
        if t < 4:
            label = "match_next"
        else:
            o += s[ip:ip + t]; ip += t
            label = "first_literal_run"
    else:
        label = "top"

    while True:
        if label == "top":
            t = s[ip]; ip += 1
            if t >= 16:
                label = "match"
            else:
                if t == 0:
                    while s[ip] == 0:
                        t += 255; ip += 1
                    t += 15 + s[ip]; ip += 1
                n = t + 3
                o += s[ip:ip + n]; ip += n
                label = "first_literal_run"

        if label == "first_literal_run":
            t = s[ip]; ip += 1
            if t >= 16:
                label = "match"
            else:
                pos = len(o) - (1 + 0x0800) - (t >> 2) - (s[ip] << 2); ip += 1
                copy_match(pos, 3)
                label = "match_done"

        # --- petla dopasowan ---
        if label in ("match", "match_done", "match_next"):
            while True:
                if label == "match":
                    if t >= 64:
                        pos = len(o) - 1 - ((t >> 2) & 7) - (s[ip] << 3); ip += 1
                        t = (t >> 5) - 1
                        o.append(o[pos]); o.append(o[pos + 1]); copy_match(pos + 2, t)
                        label = "match_done"
                    elif t >= 32:
                        t &= 31
                        if t == 0:
                            while s[ip] == 0:
                                t += 255; ip += 1
                            t += 31 + s[ip]; ip += 1
                        pos = len(o) - 1 - ((s[ip] >> 2) + (s[ip + 1] << 6)); ip += 2
                        o.append(o[pos]); o.append(o[pos + 1]); copy_match(pos + 2, t)
                        label = "match_done"
                    elif t >= 16:
                        pos = len(o) - ((t & 8) << 11)
                        t &= 7
                        if t == 0:
                            while s[ip] == 0:
                                t += 255; ip += 1
                            t += 7 + s[ip]; ip += 1
                        pos -= (s[ip] >> 2) + (s[ip + 1] << 6); ip += 2
                        if pos == len(o):
                            label = "eof"; break
                        pos -= 0x4000
                        o.append(o[pos]); o.append(o[pos + 1]); copy_match(pos + 2, t)
                        label = "match_done"
                    else:
                        pos = len(o) - 1 - (t >> 2) - (s[ip] << 2); ip += 1
                        o.append(o[pos]); o.append(o[pos + 1])
                        label = "match_done"

                if label == "match_done":
                    t = s[ip - 2] & 3
                    if t == 0:
                        label = "top"; break
                    label = "match_next"

                if label == "match_next":
                    o.append(s[ip]); ip += 1
                    if t > 1:
                        o.append(s[ip]); ip += 1
                        if t > 2:
                            o.append(s[ip]); ip += 1
                    t = s[ip]; ip += 1
                    label = "match"

            if label == "eof":
                break
        else:
            # nie powinno sie zdarzyc
            break

    if dst_len is not None and len(o) != dst_len:
        raise ValueError("LZO: rozmiar wyjscia %d != oczekiwany %d" % (len(o), dst_len))
    return bytes(o)


def clz_decompress(blob, key=ETERPACK_KEY):
    """Rozpakuj CLZObject (THeader + [TEA] + LZO) z bajtow blob. Zwraca surowe dane."""
    fourcc, enc, comp, real = struct.unpack_from("<IIII", blob, 0)
    if fourcc != MCOZ:
        raise ValueError("zly fourcc CLZObject: %#x" % fourcc)
    body = blob[16:]
    if enc:
        dec = tea_decrypt(body[:enc], key)
        if struct.unpack_from("<I", dec, 0)[0] != MCOZ:
            raise ValueError("TEA: zly klucz (wew. fourcc != MCOZ)")
        return lzo1x_decompress(dec[4:], real)
    else:
        # po THeader: wew. fourcc (4B) + LZO
        if struct.unpack_from("<I", body, 0)[0] != MCOZ:
            raise ValueError("brak wew. fourcc MCOZ (nieszyfrowane)")
        return lzo1x_decompress(body[4:], real)


class EterPack:
    IDX_STRIDE = 192

    def __init__(self, base_path):
        """base_path np. '.../pack/Zone' (bez rozszerzenia)."""
        self.base = base_path
        with open(base_path + ".eix", "rb") as f:
            eix = f.read()
        raw = clz_decompress(eix)
        fourcc, ver, count = struct.unpack_from("<IIi", raw, 0)
        self.count = count
        self.entries = {}   # lower filename -> (data_position, data_size, real_size, comp_type)
        self.names = []
        off = 12
        for i in range(count):
            e = raw[off:off + self.IDX_STRIDE]
            name = e[4:4 + 161].split(b"\x00")[0].decode("latin-1")
            real_data_size, data_size = struct.unpack_from("<ii", e, 168)
            data_position = struct.unpack_from("<i", e, 184)[0]
            comp_type = e[188]
            key = name.lower().replace("\\", "/")
            self.entries[key] = (data_position, data_size, real_data_size, comp_type)
            self.names.append(name)
            off += self.IDX_STRIDE
        self._epk = base_path + ".epk"

    def list(self, substr=None):
        if substr is None:
            return list(self.names)
        s = substr.lower()
        return [n for n in self.names if s in n.lower()]

    def get(self, filename):
        """Zwraca surowe bajty pliku (zdekompresowane jesli CLZObject)."""
        key = filename.lower().replace("\\", "/")
        if key not in self.entries:
            raise KeyError(filename)
        pos, dsize, rsize, ctype = self.entries[key]
        with open(self._epk, "rb") as f:
            f.seek(pos)
            blob = f.read(dsize)
        if len(blob) >= 4 and struct.unpack_from("<I", blob, 0)[0] == MCOZ:
            return clz_decompress(blob, TYPE_KEYS.get(ctype, ETERPACK_KEY))
        return blob


if __name__ == "__main__":
    import sys
    pack = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:/Metin/Terenzo 2024 - Muchomor Upgrade/GameCore/Klient Terenzo 2024/pack/Zone"
    ep = EterPack(pack)
    print("plikow w indeksie:", ep.count)
    q = sys.argv[2] if len(sys.argv) > 2 else "minimap"
    hits = ep.list(q)
    print("pasuje do %r: %d" % (q, len(hits)))
    for n in hits[:40]:
        print("  ", n)
