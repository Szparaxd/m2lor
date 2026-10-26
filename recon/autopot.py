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
for _s in range(_N):
    if player.GetItemIndex(_s) in _VNS and player.GetItemCount(_s) > 0:
        net.SendItemUsePacket(_s)
        break
'''


def drink_by_vnum(pid, vnums):
    """Wypij w grze o danym PID pierwsza miksture o jednym z podanych vnum, prosto z ekwipunku
    (ignoruje pasek). vnums - lista vnum. True gdy komenda dotarla do DLL."""
    if not vnums:
        return False
    vns = ",".join(str(int(v)) for v in vnums)
    return send_command(pid, _DRINK_BY_VNUM_TEMPLATE.replace("__VNS__", vns))


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
