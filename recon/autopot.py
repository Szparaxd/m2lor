"""
Auto-mikstura: odczyt HP gracza z CPythonPlayer + "nacisniecie" slotu mikstury w oknie gry.

USTALONE (black-box, nagranie pamieci w trakcie walki):
- CPythonPlayer (RTTI, singleton) trzyma punkty postaci jako tablice int32 w kolejnosci
  POINT_*: [0]=nic, [1]=poziom, [3]=PD, [4]=wymagane PD, [5]=HP, [6]=max HP, [7]=PM, [8]=max PM...
- W GameCoreTop.exe tablica jest ZASZYFROWANA XOR-em stalym kluczem (u nas 0xE73AC1DA) - dlatego
  zwykle szukanie 11714 w pamieci nic nie dawalo. Indeks [0] to zawsze 0, wiec w pamieci lezy tam
  SAM KLUCZ: key = raw[0]. Dziala tez dla klienta bez szyfrowania (wtedy key = 0).
- Offset tablicy (+0x1036C w GameCoreTop.exe) szukany przy starcie po ksztalcie danych.
- Klawisze (zweryfikowane w zrodle): WM_KEYDOWN idzie tylko do sciezki IME (PostMessage dziala np.
  na 'C'); cyfry paska czyta DirectInput odpytywany raz na klatke, tylko z fokusem -> SendInput z kodem
  skanowania, przytrzymany KEY_HOLD, wylacznie gdy gra jest aktywnym oknem.
"""
import ctypes
import os
import struct
import subprocess
import time
from ctypes import wintypes

import numpy as np

P_LEVEL, P_HP, P_MAX_HP, P_SP, P_MAX_SP = 1, 5, 6, 7, 8

u32 = ctypes.WinDLL("user32", use_last_error=True)
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x100, 0x101, 0x102


P_STATS = (12, 13, 14, 15)          # SIL, WIT, ZR, INT - male dodatnie liczby


def looks_like_points(d):
    """Czy odszyfrowane d[0..16) wyglada jak punkty postaci. Sam poziom+HP+PM nie wystarcza:
    w CPythonPlayer lezaly wczesniej inne dane, ktore przypadkiem pasowaly (poziom 10, HP 266/266)."""
    lvl, hp, mhp, sp, msp = d[P_LEVEL], d[P_HP], d[P_MAX_HP], d[P_SP], d[P_MAX_SP]
    return (d[0] == 0 and 1 <= lvl <= 250 and 0 <= hp <= mhp and 50 <= mhp < 2_000_000
            and 0 <= sp <= msp < 2_000_000 and msp > 0
            and 300 <= d[4] < 2**31 and 0 <= d[3] < d[4]        # wymagane PD: min. 300 (poziom 1)
            and all(1 <= d[i] <= 2000 for i in P_STATS)
            and len(set(d[9:16])) >= 3)                          # nie powtarzajacy sie ogon


MIN_KEY_REPEATS = 64     # z 255 kolejnych pol; prawdziwa tablica: ~200, falszywki: 0-1


def find_points(pm, player_obj, span=0x40000):
    """Offset tablicy punktow w CPythonPlayer albo None.

    Sam ksztalt nie wystarcza: klucz XOR jest dowolny, wiec prawie kazdy kawalek pamieci da sie
    "odszyfrowac" w cos wiarygodnego (zaobserwowane: poziom 10, HP 266/266 pod +0x2178). Prawdziwa
    tablica ma kilkaset punktow, w wiekszosci = 0, czyli w pamieci = SAM KLUCZ powtorzony wiele razy
    zaraz za poczatkiem. Wybieramy kandydata z najwieksza liczba powtorzen (min. MIN_KEY_REPEATS)."""
    try:
        blk = pm.read_bytes(player_obj, span)
    except Exception:
        return None
    v = struct.unpack("<%dI" % (span // 4), blk)
    best, best_rep = None, MIN_KEY_REPEATS - 1
    for i in range(len(v) - 16):
        raw = v[i:i + 16]
        if len(set(raw[1:9])) < 4:         # ciagi powtorzonych wartosci daja falszywe trafienia
            continue
        k = raw[0]
        if not looks_like_points([struct.unpack("<i", struct.pack("<I", x ^ k))[0] for x in raw]):
            continue
        rep = v[i + 1:i + 256].count(k)
        if rep > best_rep:
            best, best_rep = i * 4, rep
    return best


def scan_points_block(u, base):
    """Szuka tablicy punktow w jednym bloku pamieci (u = numpy uint32, base = adres bloku) BEZ
    polegania na RTTI/CPythonPlayer - potrzebne, gdy nie znamy obiektu gracza (np. vCore nie ma
    RTTI CPythonPlayer). Tablica ma charakterystyczny ksztalt: na [0] klucz XOR, potem punkty, a
    setki zerowych pol leza w pamieci jako KLUCZ powtorzony wiele razy pod rzad. Szukamy dlugich
    runow tej samej wartosci (= klucz), a tuz przed nimi weryfikujemy naglowek przez looks_like_points.
    Zwraca (adres, liczba_powtorzen_klucza) najlepszego kandydata albo (None, -1)."""
    n = u.shape[0]
    if n < 300:
        return None, -1
    changes = np.nonzero(u[1:] != u[:-1])[0] + 1          # granice runow jednakowych wartosci
    starts = np.concatenate(([0], changes))
    runs = np.concatenate((changes, [n])) - starts
    best, best_rep = None, MIN_KEY_REPEATS - 1
    for idx in np.nonzero(runs >= MIN_KEY_REPEATS)[0]:
        run_start = int(starts[idx])
        k = np.uint32(u[run_start])
        for p in range(run_start - 1, max(0, run_start - 32) - 1, -1):
            if u[p] != k or p + 16 > n:                   # start tablicy ma memory[0]==klucz (d[0]==0)
                continue
            d = [int(x) for x in (u[p:p + 16] ^ k).view(np.int32)]
            if looks_like_points(d):
                rep = int((u[p + 1:p + 256] == k).sum())
                if rep > best_rep:
                    best, best_rep = p, rep
                break
    return (base + best * 4, best_rep) if best is not None else (None, -1)


def scan_points_plain(u, base):
    """Lokalizuje tablice punktow NIESZYFROWANA (klucz=0), np. w vCore. Wektorowo sprawdza uklad
    POINT_ (ten sam, co u nas: [0]=0, poziom@1, PD@3, wymPD@4, HP@5, maxHP@6, PM@7, maxPM@8,
    staty@12-15). Zwraca (addr, liczba_kandydatow) albo (None, -1). HP czytamy potem read_points
    (klucz=raw[0]=0 -> plain)."""
    n = u.shape[0]
    if n < 48:
        return None, -1
    d = u.view(np.int32)
    m = n - 31

    def c(off):
        return d[off:off + m]

    lvl, exp, nexp = c(1), c(3), c(4)
    hp, mhp, sp, msp = c(5), c(6), c(7), c(8)
    sta, msta, gold = c(9), c(10), c(11)
    s = [c(12), c(13), c(14), c(15)]
    # Scisly wzorzec: HP/PM/stamina maja pary biez./max, do tego PD<wymPD, zloto, 4 rozsadne staty.
    # Luzny wzorzec lapal falszywki (0/96) - te sa "rzadkie", wiec dodatkowo wybieramy NAJGESTSZEGO.
    mask = ((c(0) == 0) & (lvl >= 1) & (lvl <= 120)
            & (hp >= 0) & (hp <= mhp) & (mhp >= 100) & (mhp < 2_000_000)
            & (sp >= 0) & (sp <= msp) & (msp >= 1) & (msp < 2_000_000)
            & (sta >= 0) & (sta <= msta) & (msta >= 1) & (msta < 2_000_000)
            & (gold >= 0) & (gold < 2_000_000_000)
            & (nexp >= 500) & (nexp < 2**31 - 1) & (exp >= 0) & (exp < nexp))
    for st in s:
        mask &= (st >= 1) & (st <= 1000)
    idx = np.nonzero(mask)[0]
    if idx.size == 0:
        return None, -1
    # prawdziwa tablica jest gesto wypelniona - wybierz kandydata z najwieksza liczba niezerowych pol
    best, best_score = None, -1
    for i in idx:
        i = int(i)
        score = int(np.count_nonzero(d[i + 1:i + 24]))
        if score > best_score:
            best, best_score = i, score
    return base + best * 4, best_score


def read_point_ptr(pm, base, cfg, index):
    """Odczyt pojedynczego punktu przez STABILNY wskaznik singletona CPythonPlayer (dla buildow bez
    RTTI, gdzie skan zawodzi - np. vCore). cfg: singleton_rva, array_off, stride, key. Zwraca wartosc
    albo None (singleton==0 = postac jeszcze nie w swiecie). Uklad vCore: tablica 8-bajtowa, dolny
    dword zaszyfrowany XOR; value(i) = *(base+singleton_rva) -> +array_off + i*stride, XOR key."""
    try:
        singleton = pm.read_uint(base + cfg["singleton_rva"])
        if not singleton:
            return None
        raw = pm.read_uint(singleton + cfg["array_off"] + index * cfg["stride"])
        return raw ^ cfg["key"]
    except Exception:
        return None


def read_points(pm, addr, count=9):
    """Odszyfrowane punkty [0..count) spod adresu tablicy (klucz = raw[0])."""
    raw = struct.unpack("<%dI" % count, pm.read_bytes(addr, 4 * count))
    k = raw[0]
    return [struct.unpack("<i", struct.pack("<I", x ^ k))[0] for x in raw]


def game_window(pid):
    """Glowne widoczne okno procesu gry."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h):
            found.append(h)
        return True

    u32.EnumWindows(cb, 0)
    return found[0] if found else None


def post_key(hwnd, ch):
    """Klawisz przez komunikaty okna (dziala w tle). Gra przyjmuje tak np. 'C' (okno postaci),
    ale NIE klawisze cyfr paska - te czyta z wejscia "sprzetowego" (DirectInput)."""
    vk = ord(ch.upper())
    sc = u32.MapVirtualKeyW(vk, 0)
    u32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1 | (sc << 16))
    u32.PostMessageW(hwnd, WM_CHAR, ord(ch), 1 | (sc << 16))
    u32.PostMessageW(hwnd, WM_KEYUP, vk, 1 | (sc << 16) | (0xC0 << 24))


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("ki", _KI), ("pad", ctypes.c_ubyte * 8)]


KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008
# Gra czyta klawiature przez DirectInput ODPYTYWANY raz na klatke (EterLib/Input.cpp: GetDeviceState
# -> porownanie z poprzednia migawka). Nacisniecie+puszczenie wyslane od razu po sobie miesci sie
# miedzy dwiema klatkami i gra go nie widzi - klawisz trzeba przytrzymac przez kilka klatek.
KEY_HOLD = 0.1      # s (~3-6 klatek przy 30-60 FPS)


def _send(sc, flags):
    inp = _INPUT(type=1, ki=_KI(0, sc, flags, 0, 0))        # type 1 = INPUT_KEYBOARD
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def press_key(hwnd, ch, hold=KEY_HOLD):
    """Nacisniecie klawisza jak z klawiatury (SendInput, kod skanowania) - widzi je DirectInput,
    wiec dziala na sloty paska. Trafia do AKTYWNEGO okna (gra i tak czyta klawiature tylko z fokusem:
    DISCL_FOREGROUND), dlatego wysylamy TYLKO gdy gra ma fokus - inaczej '1' wpisaloby sie np. w okno,
    w ktorym akurat piszesz. Zwraca True, gdy wyslano."""
    if u32.GetForegroundWindow() != hwnd:
        return False
    sc = u32.MapVirtualKeyW(ord(ch.upper()), 0)
    _send(sc, KEYEVENTF_SCANCODE)
    time.sleep(hold)
    _send(sc, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)        # puszczamy zawsze, nawet po zmianie fokusu
    return True


# ---------------------------------------------------------------------------
# Picie BEZ FOKUSU: nazwany potok do autopot.dll wstrzyknietego w gre.
#
# Radar (64-bit) jest "mozgiem" - czyta HP i decyduje. Gdy trzeba pic, pisze 1 bajt (indeks slotu
# 0..8) do potoku; DLL (32-bit, w grze) wykonuje player.RequestUseLocalQuickSlot(slot) przez
# Py_AddPendingCall - czyli to samo, co nacisniecie klawisza slotu, ale bez klawiatury i bez fokusu.
# Potok swiadomie przechodzi granice 32/64-bit: to tylko bajty, bez CreateRemoteThread miedzy
# roznymi bitowosciami (co byloby zawodne).
# ---------------------------------------------------------------------------
PIPE_PREFIX = r"\\.\pipe\m2autopot_"      # pelna nazwa = PIPE_PREFIX + PID gry (per-instancja)
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF
_ERROR_FILE_NOT_FOUND = 2     # serwer wlasnie odtwarza instancje potoku (mgnienie oka)
_ERROR_PIPE_BUSY = 231        # instancja istnieje, ale zajeta

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
# WAZNE: uchwyty sa 64-bit w tym procesie - domyslny restype (c_int) obcialby je.
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.WriteFile.restype = wintypes.BOOL
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.WaitNamedPipeW.restype = wintypes.BOOL
k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
k32.CloseHandle.restype = wintypes.BOOL
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _pipe_name(pid):
    return PIPE_PREFIX + str(int(pid))


def _open_pipe(pid):
    """Uchwyt do zapisu w potoku DLL danej GRY (po PID) albo None. Ponawia: serwer ma JEDNA
    instancje i na krotko odtwarza ja miedzy polaczeniami - luka daje FILE_NOT_FOUND / PIPE_BUSY."""
    name = _pipe_name(pid)
    for _ in range(8):
        h = k32.CreateFileW(name, _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if h and h != _INVALID_HANDLE:
            return h
        err = ctypes.get_last_error()
        if err == _ERROR_PIPE_BUSY:
            k32.WaitNamedPipeW(name, 300)
        elif err == _ERROR_FILE_NOT_FOUND:
            time.sleep(0.02)
        else:
            return None                       # potoku nie ma = DLL nie wstrzykniety w ten proces
    return None


def dll_loaded(pid):
    """True, gdy autopot.dll (serwer potoku) dziala w grze o danym PID."""
    h = _open_pipe(pid)
    if h is not None:
        k32.CloseHandle(h)
        return True
    return False


def _send_raw(pid, data):
    """Wysyla surowe bajty jednym otwarciem potoku gry (po PID). True, gdy wszystko zapisane."""
    h = _open_pipe(pid)
    if h is None:
        return False
    n = wintypes.DWORD(0)
    ok = bool(k32.WriteFile(h, data, len(data), ctypes.byref(n), None)) and n.value == len(data)
    k32.CloseHandle(h)
    return ok


def send_command(pid, text):
    """Wysyla skrypt Pythona do wykonania w grze o danym PID. Komenda konczy sie bajtem NUL
    (framing), wiec skrypt moze byc wieloliniowy. True, gdy DLL odebral."""
    return _send_raw(pid, text.encode("utf-8", "replace") + b"\x00")


class PipeDrinker:
    """Picie przez DLL konkretnej gry (po PID): wysyla komende 'uzyj slotu paska'.
    Otwiera-pisze-zamyka za kazdym razem - picie jest rzadkie, nie blokujemy instancji potoku."""

    def __init__(self, pid):
        self.pid = pid
        self.last_ok = False

    def drink(self, slot):
        """Wyslij 'wypij slot N paska' (0..8). True gdy komenda dotarla do DLL."""
        self.last_ok = send_command(
            self.pid, "import player\nplayer.RequestUseLocalQuickSlot(%d)" % int(slot))
        return self.last_ok


# Picie WPROST z ekwipunku: skan po vnum i net.SendItemUsePacket(cell) - bez paska, bez DirectInput.
# Przezywa przesuniecie stacka (gdy mikstury wpadna do innej komorki, skan znajdzie je od nowa).
# net.SendItemUsePacket(cell) i player.GetItemIndex/Count zweryfikowane w Source Client.
_DRINK_BY_VNUM_TEMPLATE = r'''import player, net
_VNS = [__VNS__]
_N = player.INVENTORY_PAGE_SIZE * player.INVENTORY_PAGE_COUNT
_best = None
for _s in range(_N):
    _v = player.GetItemIndex(_s)
    if _v in _VNS and player.GetItemCount(_s) > 0:
        _r = _VNS.index(_v)
        if _best is None or _r < _best[0]:
            _best = (_r, _s)
            if _r == 0:
                break
if _best is not None:
    net.SendItemUsePacket(_best[1])
'''


def drink_by_vnum(pid, vnums):
    """Wypij w grze o danym PID miksture o jednym z podanych vnum, prosto z ekwipunku (ignoruje
    pasek). vnums - lista vnum w kolejnosci priorytetu (pierwszy obecny w EQ wygrywa).
    True gdy komenda dotarla do DLL."""
    if not vnums:
        return False
    vns = ",".join(str(int(v)) for v in vnums)
    return send_command(pid, _DRINK_BY_VNUM_TEMPLATE.replace("__VNS__", vns))


def pick_close_item(pid):
    """Podnies NAJBLIZSZY przedmiot z ziemi (<=300 jedn., 500 na koniu) w grze o danym PID.
    Wola player.PickCloseItem() - klient sam wybiera najblizszy drop w zasiegu i wysyla pakiet
    pickup (SendItemPickUpPacket). Jedno wywolanie = jeden przedmiot; wielokrotnie = kolejne.
    Dziala tam, gdzie pending-calls sa opruzniane (GameCoreTop). True gdy komenda dotarla do DLL."""
    return send_command(pid, "import player\nplayer.PickCloseItem()")


# Przedmioty na ziemi: CPythonItem (singleton, brak RTTI) -> std::map<VID, SGroundItemInstance*>
# m_GroundItemInstanceMap @ obj+4 (MSVC: head; wezel _Left@0 _Parent@4 _Right@8, isnil@+0xD, VID@+0x10,
# wskaznik@+0x14). Instancja: vtable@0, vnum@+4, v3EndPosition x@+8, -y@+0xC. Wlasciciel dropu =
# std::string stOwnership (pusty = wolny). RVA singletona z CPythonPlayer::PickCloseItem
# (mov esi,[sing] przed CPythonItem::GetCloseItem); zweryfikowane statycznie (pefile+capstone).
GROUND_ITEM_CLIENTS = {
    "gamecoretop.exe": dict(sing=0x48E120, owner=0x2F8),  # owner zweryf. na zywo (cap=15 @+0x30C, pusty)
    "vcore.exe": dict(sing=0x1B8E06C, owner=0x2FC),       # owner z wbudowanego "podnies wszystko" vCore
}
_OWNER_CANDS = (0x2FC, 0x2F8, 0x300)
GROUND_MAX = 400          # bezpiecznik przechodzenia drzewa (smieci w pamieci)


def _owner_str(pm, a):
    """stOwnership (MSVC std::string): '' = wolny, nick = czyj, None = to nie jest std::string."""
    try:
        size, cap = pm.read_uint(a + 0x10), pm.read_uint(a + 0x14)
        if cap == 15 and size <= 15:
            return pm.read_bytes(a, size).decode("cp1250", "replace") if size else ""
        if 16 <= cap < 0x1000 and size <= cap:
            return pm.read_bytes(pm.read_uint(a), size).decode("cp1250", "replace")
    except Exception:
        pass
    return None


def read_ground_items(pm, base, cfg):
    """Lista (vid, vnum, wx, wy, owner) przedmiotow na ziemi (wsp. radaru). owner: '' wolny, nick, albo
    None gdy offset wlasciciela nieznany. cfg - wpis GROUND_ITEM_CLIENTS (owner wykrywany i zapamietany)."""
    obj = pm.read_uint(base + cfg["sing"])
    if not obj:
        return []
    head = pm.read_uint(obj + 4)
    if not head or pm.read_uint(obj + 8) > GROUND_MAX:
        return []
    out, stack, seen = [], [pm.read_uint(head + 4)], 0
    while stack and seen < GROUND_MAX:
        n = stack.pop()
        if not n or n == head or pm.read_bytes(n + 0xD, 1)[0]:
            continue
        seen += 1
        stack.append(pm.read_uint(n))
        stack.append(pm.read_uint(n + 8))
        inst = pm.read_uint(n + 0x14)
        if not inst:
            continue
        x, ny = struct.unpack("<2f", pm.read_bytes(inst + 8, 8))
        out.append([pm.read_uint(n + 0x10), pm.read_uint(inst + 4), x, -ny, inst])
    if out and cfg.get("owner") is None and not cfg.get("owner_tried"):
        for off in _OWNER_CANDS:         # offset, pod ktorym WSZYSTKIE instancje maja poprawny std::string
            if all(_owner_str(pm, it[4] + off) is not None for it in out):
                cfg["owner"] = off
                print("[loot] wlasciciel dropu: offset %#x" % off)
                break
        cfg["owner_tried"] = True
    off = cfg.get("owner")
    return [(v, vn, x, y, _owner_str(pm, inst + off) if off else None) for v, vn, x, y, inst in out]


def pick_up_vids(pid, vids):
    """GameCoreTop: wyslij pakiet podniesienia dla KAZDEGO VID naraz (net.SendItemPickUpPacket).
    Omija klienckie 'najblizszy w zasiegu 300' - zasieg i wlasciciela sprawdza juz tylko serwer."""
    if not vids:
        return False
    return send_command(pid, "import net\nfor _v in (%s,):\n    net.SendItemPickUpPacket(_v)"
                        % ",".join(str(int(v)) for v in vids))


def tpick(pid, wx, wy, vids):
    """GameCoreTop: 'skok-podnies-powrot' - DLL w jednej klatce wysyla serwerowi ruch na (wx,wy), pakiety
    podniesienia dla vids i ruch z powrotem. Lokalnie postac stoi. Zasieg ~300 serwer liczy od (wx,wy)."""
    if not vids:
        return False
    return send_command(pid, "!tpick %.0f %.0f %s" % (wx, wy, " ".join(str(int(v)) for v in vids[:32])))


def has_tpick(pid):
    """True, gdy wstrzyknieta DLL zna '!tpick' (znacznik logs/caps_<pid>.ok; stara DLL go nie pisze)."""
    try:
        with open(os.path.join(os.path.dirname(INV_DUMP_PATH), "caps_%d.ok" % pid)) as fh:
            return "tpick" in fh.read()
    except OSError:
        return False


def has_attack(pid):
    """True, gdy wstrzyknieta DLL zna '!attack' (masowy atak pakietem; znacznik caps_<pid>.ok)."""
    try:
        with open(os.path.join(os.path.dirname(INV_DUMP_PATH), "caps_%d.ok" % pid)) as fh:
            return "attack" in fh.read()
    except OSError:
        return False


def has_tattack(pid):
    """True, gdy wstrzyknieta DLL zna '!tattack' (atak z przeskokiem pozycji; znacznik caps_<pid>.ok)."""
    try:
        with open(os.path.join(os.path.dirname(INV_DUMP_PATH), "caps_%d.ok" % pid)) as fh:
            return "tattack" in fh.read()
    except OSError:
        return False


def tattack(pid, wx, wy, vids, mot=0):
    """GameCoreTop: 'przeskok-atak-powrot' - DLL w jednej klatce wysyla serwerowi ruch na (wx,wy),
    pakiety ataku dla vids i ruch z powrotem. Lokalnie postac STOI. Serwer liczy zasieg ataku od (wx,wy),
    wiec dalekie moby mozna uderzyc bez podchodzenia (jak !tpick dla lootu). vids obcinane do 64.
    True gdy komenda dotarla do DLL."""
    if not vids:
        return False
    return send_command(pid, "!tattack %.0f %.0f %d %s" % (wx, wy, int(mot),
                        " ".join(str(int(v)) for v in vids[:64])))


def attack_vids(pid, vids, mot=0):
    """GameCoreTop: wyslij pakiet ataku do KAZDEGO VID (CPythonNetworkStream::SendAttackPacket).
    Bije moby BEZ lokalnej animacji i BEZ ruchu postaci - omija sciezke wejscia OnHit. mot=0 = cios
    zwykly (serwer liczy zwykle obrazenia broni). Zasieg/cooldown waliduje serwer: bez ruchu trafiaja
    tylko moby w serwerowym zasiegu wrecz. vids obcinane do 64 na komende. True gdy komenda dotarla."""
    if not vids:
        return False
    return send_command(pid, "!attack %d %s" % (int(mot),
                        " ".join(str(int(v)) for v in vids[:64])))


def pick_close_item_native(pid):
    """vCore: podnies najblizszy drop NATYWNIE z hooka per-klatka (bez fokusu, bez Pythona).
    Wysyla '!pick' - DLL ustawia flage, hook wola CPythonPlayer::PickCloseItem przy najblizszej klatce
    (ta sama droga co '!drink'). True gdy komenda dotarla do DLL."""
    return send_command(pid, "!pick")


def goto_native(pid, wx, wy):
    """vCore: idz do punktu swiata (wx, wy) w konwencji RADARU - NATYWNIE z hooka per-klatka.
    DLL buduje pixel-pos (x=wx, y=wy, z=biezace - konwencja zgodna z NEW_GetPixelPosition) i wola NEW_MoveToDestPixelPositionDirection
    na glownej postaci. Postac idzie jak po kliknieciu mysza (pakiety ruchu leca same). Jedno
    wywolanie uruchamia marsz do celu; radar ponawia okresowo (korekta/keep-alive)."""
    return send_command(pid, "!goto %.1f %.1f" % (float(wx), float(wy)))


def getpos_native(pid):
    """vCore: kanarek - DLL loguje do dll_debug.txt biezaca pozycje glownej postaci
    (NEW_GetPixelPosition). Do weryfikacji konwencji wspolrzednych przed ruchem."""
    return send_command(pid, "!getpos")


def _project_paths():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # radar-project\
    return (os.path.join(base, "dll", "autopot.dll"),
            os.path.join(base, "injector", "inject.exe"))


def ensure_injected(pid, timeout=10.0):
    """Wstrzykuje autopot.dll do gry o danym PID, jesli jeszcze nie ma. Zwraca (ok, komunikat)."""
    if dll_loaded(pid):
        return True, "juz wstrzykniety"
    dll, inj = _project_paths()
    if not os.path.exists(dll) or not os.path.exists(inj):
        return False, "brak autopot.dll / inject.exe - zbuduj: build.bat"
    try:
        r = subprocess.run([inj, str(pid), dll], capture_output=True, text=True,
                           timeout=timeout, creationflags=0x08000000)   # CREATE_NO_WINDOW
    except Exception as e:
        return False, "inject.exe: %s" % e
    t0 = time.time()
    while time.time() - t0 < 3.0:            # serwer potoku wstaje chwile po LoadLibrary
        if dll_loaded(pid):
            return True, "wstrzyknieto"
        time.sleep(0.1)
    msg = (r.stdout or r.stderr or "").strip().splitlines()
    return False, (msg[-1] if msg else "DLL nie zglosil sie na potoku")


# ---------------------------------------------------------------------------
# Zrzut ekwipunku: DLL wykonuje w grze snippet czytajacy ekwipunek (player.GetItemIndex/Count,
# nazwy z item.GetItemName) i zapisuje do pliku; radar potem go czyta. Nazwy sa w cp1250 (pl).
# ---------------------------------------------------------------------------
INV_DUMP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "logs", "inventory.txt")

# Snippet biegnie w Pythonie 2.7 GRY. __PATH__ podmieniamy na sciezke pliku wyniku.
# Rozmiar ekwipunku liczymy z odslonietych stalych (player.INVENTORY_PAGE_SIZE/COUNT) - odporne
# na uklad tego klienta. '__PATH__' jest w r"..." (surowy), wiec pojedyncze '\' sa literalne.
_DUMP_TEMPLATE = r'''import player
try:
    import item
except Exception:
    item = None
f = open(r"__PATH__", "w")
try:
    N = player.INVENTORY_PAGE_SIZE * player.INVENTORY_PAGE_COUNT
    for s in range(N):
        vn = player.GetItemIndex(s)
        if vn:
            cnt = player.GetItemCount(s)
            nm = ""
            if item is not None:
                try:
                    item.SelectItem(vn)
                    nm = item.GetItemName()
                except Exception:
                    nm = ""
            f.write("%d\t%d\t%d\t%s\n" % (s, vn, cnt, nm))
finally:
    f.close()
'''


def dll_executes(pid, timeout=2.0):
    """Sprawdza, czy DLL FAKTYCZNIE wykonuje komendy (niektore klienty, np. vCore, nie opruzniaja
    kolejki Py_AddPendingCall - komenda dociera, ale nigdy sie nie wykonuje). Wysyla canary piszacy
    plik; True gdy plik powstal. Decyduje, czy picie przez DLL (bez fokusu) ma sens."""
    canary = os.path.join(os.path.dirname(INV_DUMP_PATH), "dll_canary_%d.txt" % pid)
    try:
        if os.path.exists(canary):
            os.remove(canary)
    except OSError:
        pass
    if not send_command(pid, 'open(r"%s","w").write("ok")' % canary):
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(canary):
            try:
                os.remove(canary)
            except OSError:
                pass
            return True
        time.sleep(0.1)
    return False


def hook_ready(pid):
    """True, gdy DLL zainstalowala hook per-klatka (picie bez fokusu natywnym wywolaniem) - marker
    pliku pisany przez DLL dla vCore."""
    mk = os.path.join(os.path.dirname(INV_DUMP_PATH), "hook_%d.ok" % pid)
    return os.path.exists(mk)


def drink_native(pid, slot):
    """Picie BEZ fokusu przez hook (natywne RequestUseLocalQuickSlot na glownym watku). Wysyla
    komende '!drink N' - DLL ustawia flage, hook wykonuje przy najblizszej klatce. slot = indeks
    slotu paska (0..7)."""
    return send_command(pid, "!drink %d" % int(slot))


def hook_has_skill(pid):
    """True, gdy wstrzyknieta DLL zna komende '!skill' (marker hooka zawiera 'skill'). Stara DLL
    zostaje w procesie do restartu gry - wtedy marker to samo 'ok'."""
    mk = os.path.join(os.path.dirname(INV_DUMP_PATH), "hook_%d.ok" % pid)
    try:
        with open(mk) as fh:
            return "skill" in fh.read()
    except OSError:
        return False


def hook_has_chat(pid):
    """True, gdy wstrzyknieta DLL zna komende '!chat' (marker hooka zawiera 'chat')."""
    mk = os.path.join(os.path.dirname(INV_DUMP_PATH), "hook_%d.ok" % pid)
    try:
        with open(mk) as fh:
            return "chat" in fh.read()
    except OSError:
        return False


def chat_native(pid, text):
    """vCore: komenda czatu (np. '/unmount', '/ride') natywnie z hooka: CPythonNetworkStream::SendChatPacket."""
    return send_command(pid, "!chat %s" % text)


def chat_py(pid, text):
    """GameCoreTop: j.w. przez Pythona gry (pending-call): net.SendChatPacket(text)."""
    return send_command(pid, "import net\nnet.SendChatPacket(%r)" % text)


def use_skill_native(pid, slot):
    """vCore: uzyj umiejetnosci ze slotu UMIEJETNOSCI (nie paska) natywnie z hooka per-klatka:
    CPythonPlayer::ClickSkillSlot(slot) = to samo co klik w okno umiejetnosci (animacja, cooldown,
    pakiet). Radar musi sam sprawdzic kon/cooldown/aktywny buff (klik aktywnego TOGGLE go WYLACZA)."""
    return send_command(pid, "!skill %d" % int(slot))


def use_skill_py(pid, slot):
    """GameCoreTop: j.w. przez Pythona gry (pending-call): player.ClickSkillSlot(slot)."""
    return send_command(pid, "import player\nplayer.ClickSkillSlot(%d)" % int(slot))


def dump_inventory(pid, timeout=3.0):
    """Zleca grze o danym PID zrzut ekwipunku i zwraca liste krotek (slot, vnum, count, name).
    None = DLL nie odebral komendy (niewstrzykniety?). [] = puste / brak postaci."""
    try:
        if os.path.exists(INV_DUMP_PATH):
            os.remove(INV_DUMP_PATH)
    except OSError:
        pass
    snippet = _DUMP_TEMPLATE.replace("__PATH__", INV_DUMP_PATH)
    if not send_command(pid, snippet):
        return None
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(INV_DUMP_PATH):
            time.sleep(0.15)                  # daj grze dopisac plik do konca
            break
        time.sleep(0.1)
    if not os.path.exists(INV_DUMP_PATH):
        return []
    out = []
    with open(INV_DUMP_PATH, "rb") as fh:
        for ln in fh.read().splitlines():
            parts = ln.split(b"\t")
            if len(parts) >= 3 and parts[0].isdigit():
                nm = parts[3].decode("cp1250", "replace") if len(parts) > 3 else ""
                out.append((int(parts[0]), int(parts[1]), int(parts[2]), nm))
    return out
