"""
Szukanie vtable klasy po NAZWIE z RTTI (MSVC, 32-bit) - zamiast sztywnych offsetow od bazy.
Nazwa klasy przezywa przekompilowanie exe; adres vtable - nie.

Lancuch RTTI (ten sam, ktory czytamy w druga strone: vtable-4 -> COL -> TD -> nazwa):
    TypeDescriptor (TD):          +0 pVFTable(type_info), +4 spare, +8 nazwa ".?AVKlasa@@\\0"
    CompleteObjectLocator (COL):  +0 signature(0), +4 offset, +8 cdOffset, +0xC pTypeDescriptor
    vtable[-1] = &COL

Odwrotnie: nazwa -> TD = adres_nazwy - 8 -> COL = miejsce, gdzie lezy dword TD, minus 0xC
(z offset == 0, czyli GLOWNA vtable, nie ta z wielodziedziczenia) -> vtable = miejsce, gdzie
lezy dword COL, plus 4. Wszystko szukane wewnatrz obrazu modulu exe.
"""
import numpy as np

PAGE = 0x1000


def read_image(pm, base, size):
    """Caly obraz modulu jako bytes; strony nieczytelne wypelnione zerami."""
    out = bytearray(size)
    for off in range(0, size, PAGE):
        n = min(PAGE, size - off)
        try:
            out[off:off + n] = pm.read_bytes(base + off, n)
        except Exception:
            pass
    return bytes(out)


def find_vtables(pm, base, size, class_names):
    """{nazwa_klasy: adres_vtable} dla klas jak 'CActorInstance'. Brak/niejednoznacznosc -> wyjatek."""
    img = read_image(pm, base, size)
    u = np.frombuffer(img[:(len(img) // 4) * 4], dtype="<u4")
    found = {}
    for cls in class_names:
        name = (".?AV%s@@" % cls).encode() + b"\x00"
        pos = img.find(name)
        if pos < 0:
            raise LookupError("RTTI: brak nazwy %s w exe" % name[:-1].decode())
        td = base + pos - 8
        vts = []
        for i in np.nonzero(u == td)[0]:
            col_off = int(i) * 4 - 0xC
            if col_off < 0:
                continue
            sig, offset = u[col_off // 4], u[col_off // 4 + 1]
            if sig != 0 or offset != 0:       # tylko glowna vtable (offset podobiektu 0)
                continue
            col = base + col_off
            vts += [base + int(j) * 4 + 4 for j in np.nonzero(u == col)[0]]
        if len(vts) != 1:
            raise LookupError("RTTI: %s -> %d kandydatow vtable (oczekiwano 1)" % (cls, len(vts)))
        found[cls] = vts[0]
    return found
