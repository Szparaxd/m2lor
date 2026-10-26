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
PIPE_NAME = r"\\.\pipe\m2autopot"
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


def _open_pipe():
    """Uchwyt do zapisu w potoku DLL albo None. Ponawia: serwer ma JEDNA instancje i na krotko
    odtwarza ja miedzy polaczeniami - trafienie w te luke daje FILE_NOT_FOUND / PIPE_BUSY."""
    for _ in range(8):
        h = k32.CreateFileW(PIPE_NAME, _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if h and h != _INVALID_HANDLE:
            return h
        err = ctypes.get_last_error()
        if err == _ERROR_PIPE_BUSY:
            k32.WaitNamedPipeW(PIPE_NAME, 300)
        elif err == _ERROR_FILE_NOT_FOUND:
            time.sleep(0.02)
        else:
            return None                       # potoku nie ma = DLL nie wstrzykniety
    return None


def dll_loaded():
    """True, gdy autopot.dll (serwer potoku) dziala w grze."""
    h = _open_pipe()
    if h is not None:
        k32.CloseHandle(h)
        return True
    return False


class PipeDrinker:
    """Wysyla indeks slotu (0..8) jako 1 bajt do DLL. Otwiera-pisze-zamyka za kazdym razem:
    picie jest rzadkie (cooldown), a tak nie blokujemy na stale jedynej instancji potoku
    (dzieki czemu dll_loaded()/[J] zawsze maja sie do czego podlaczyc)."""

    def __init__(self):
        self.last_ok = False

    def drink(self, slot):
        """Wyslij 'wypij slot'. True gdy bajt dotarl do DLL."""
        h = _open_pipe()
        if h is None:
            self.last_ok = False
            return False
        n = wintypes.DWORD(0)
        ok = bool(k32.WriteFile(h, bytes([slot & 0xFF]), 1, ctypes.byref(n), None)) and n.value == 1
        k32.CloseHandle(h)
        self.last_ok = ok
        return ok


def _project_paths():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # radar-project\
    return (os.path.join(base, "dll", "autopot.dll"),
            os.path.join(base, "injector", "inject.exe"))


def ensure_injected(pid, timeout=10.0):
    """Wstrzykuje autopot.dll do gry, jesli jeszcze nie ma. Zwraca (ok, komunikat)."""
    if dll_loaded():
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
        if dll_loaded():
            return True, "wstrzyknieto"
        time.sleep(0.1)
    msg = (r.stdout or r.stderr or "").strip().splitlines()
    return False, (msg[-1] if msg else "DLL nie zglosil sie na potoku")
