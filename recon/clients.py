"""Uruchomione klienty gry: lista (PID, exe, tytul okna) i wybor JEDNEGO klienta do podlaczenia.

Po co: pymem.Pymem("vCore.exe") lapie PIERWSZY proces o tej nazwie - przy dwoch oknach gry dwa radary
podlaczylyby sie do tego samego klienta. Dlatego radar/launcher podlaczaja sie po PID.

Uzycie z konsoli:  python recon/clients.py      -> lista klientow (PID, exe, tytul okna)
"""
import ctypes
from ctypes import wintypes

import pymem.process

GAME_EXES = ("GameCoreTop.exe", "vCore.exe")   # znane buildy klienta

u32 = ctypes.WinDLL("user32", use_last_error=True)


def list_clients(exes=GAME_EXES):
    """[(pid, exe)] uruchomionych procesow gry (exe w kanonicznej pisowni z GAME_EXES), rosnaco po PID."""
    want = {e.lower(): e for e in exes}
    out = []
    for p in pymem.process.list_processes():
        name = p.szExeFile.decode("mbcs", "replace") if isinstance(p.szExeFile, bytes) else p.szExeFile
        if name.lower() in want:
            out.append((int(p.th32ProcessID), want[name.lower()]))
    return sorted(out)


def window_title(pid):
    """Tytul glownego (widocznego) okna procesu albo ''."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        wp = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(wp))
        if wp.value == pid and u32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(h, buf, 256)
            if buf.value:
                found.append(buf.value)
                return False
        return True

    u32.EnumWindows(cb, 0)
    return found[0] if found else ""


def describe(clients):
    return "\n".join("  PID %-6d %-16s %s" % (pid, exe, window_title(pid)) for pid, exe in clients)


def pick(exe=None, pid=None):
    """Wybierz klienta: (pid, exe). pid -> ten proces; exe -> jedyny proces tej nazwy; nic -> jedyny
    klient ze znanych exe. Gdy pasuje 0 albo >1 klientow -> LookupError z lista (uzyj -p PID)."""
    exes = GAME_EXES if exe is None else (exe,)
    clients = list_clients(exes)
    if pid is not None:
        for p, e in list_clients(GAME_EXES + ((exe,) if exe else ())):
            if p == pid:
                return p, e
        raise LookupError("brak procesu gry o PID %d. Uruchomione klienty:\n%s"
                          % (pid, describe(list_clients()) or "  (brak)"))
    if len(clients) == 1:
        return clients[0]
    if not clients:
        raise LookupError("nie znalazlem procesu gry (%s). Gra wlaczona?" % ", ".join(exes))
    raise LookupError("dziala %d klientow - wskaz ktory: -p PID\n%s" % (len(clients), describe(clients)))


if __name__ == "__main__":
    cl = list_clients()
    print(describe(cl) if cl else "brak uruchomionych klientow (%s)" % ", ".join(GAME_EXES))
