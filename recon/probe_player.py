"""Sonda modulu `player` w grze: wypisuje stale (POINT_*) i GetStatus(0..40) do pliku.
Cel: potwierdzic, ze HP da sie czytac przez wbudowane API klienta (player.GetStatus) zamiast
skanowac pamiec - dziala tez tam, gdzie brak RTTI/offsetow (np. vCore). Pomysl z subagenta Opus.

Wykonuje w grze (przez autopot.dll) snippet, ktory zapisuje wynik do logs/player_probe.txt;
ten skrypt go odczytuje. Uruchom DWA razy - raz na pelnym HP, raz po oberwaniu - zeby zobaczyc,
ktory indeks GetStatus sie zmienia (to biezace HP).

Uruchom (ADMIN, gra wlaczona, postac zalogowana):
    cd "C:/Metin/radar-project"; python recon/probe_player.py -e vCore
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymem

import autopot


def exe_dir(pm):
    """Katalog pliku exe gry (do znalezienia syserr.txt)."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD)]
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    if k.QueryFullProcessImageNameW(pm.process_handle, 0, buf, ctypes.byref(size)):
        return os.path.dirname(buf.value)
    return None


def tail_syserr(pm, n=25):
    """Ostatnie linie syserr.txt klienta (tam gra wypisuje bledy Pythona)."""
    d = exe_dir(pm)
    if not d:
        return
    p = os.path.join(d, "syserr.txt")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        print("[syserr] brak %s" % p)
        return
    print("\n--- syserr.txt (ostatnie %d linii) %s ---" % (n, p))
    for ln in lines[-n:]:
        print("  " + ln)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "player_probe.txt")

# WAZNE: najpierw otwieramy plik (canary), potem probujemy player - zeby ZAWSZE dostac wynik,
# nawet gdy import/GetStatus rzuci wyjatek (wtedy zapiszemy traceback).
SNIPPET = r'''f = open(r"__PATH__", "w")
f.write("canary: snippet wykonany\n")
f.flush()
try:
    import player
    f.write("player: zaimportowany\n")
    try:
        f.write("dir: " + ",".join(sorted(dir(player))) + "\n")
    except Exception as e:
        f.write("dir ERR %s\n" % e)
    for n in sorted(dir(player)):
        try:
            v = getattr(player, n)
            if isinstance(v, int):
                f.write("CONST %s = %d\n" % (n, v))
        except Exception:
            pass
    if hasattr(player, "GetStatus"):
        for i in range(0, 41):
            try:
                f.write("GetStatus[%d] = %r\n" % (i, player.GetStatus(i)))
            except Exception as e:
                f.write("GetStatus[%d] = ERR %s\n" % (i, e))
    else:
        f.write("UWAGA: player nie ma GetStatus\n")
except Exception:
    import traceback
    f.write("EXC:\n" + traceback.format_exc())
f.close()
'''


def main():
    ap = argparse.ArgumentParser(description="Sonda modulu player (Metin2).")
    ap.add_argument("-e", "--exe", "-exe", default="GameCoreTop.exe")
    args = ap.parse_args()
    if not args.exe.lower().endswith(".exe"):
        args.exe += ".exe"
    try:
        pm = pymem.Pymem(args.exe)
    except pymem.exception.ProcessNotFound:
        print("[!] Nie ma procesu %s (gra wlaczona? -e NAZWA)" % args.exe)
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return

    ok, msg = autopot.ensure_injected(pm.process_id)
    print("[dll] %s" % msg)
    if not ok:
        print("[!] Bez DLL nie wykonam sondy.")
        return

    try:
        if os.path.exists(OUT):
            os.remove(OUT)
    except OSError:
        pass
    if not autopot.send_command(pm.process_id, SNIPPET.replace("__PATH__", OUT)):
        print("[!] DLL nie odebral komendy (nowa autopot.dll? restart klienta).")
        return
    t0 = time.time()
    while time.time() - t0 < 3.0 and not os.path.exists(OUT):
        time.sleep(0.1)
    if not os.path.exists(OUT):
        print("[!] Brak pliku wyniku - nawet canary nie powstal.")
        print("    Mozliwe: DLL nie wykonuje komend tekstowych (stary protokol?) albo open() rzucil.")
        tail_syserr(pm)
        return
    time.sleep(0.1)
    with open(OUT, encoding="utf-8", errors="replace") as f:
        print(f.read())
    print("[i] Plik: %s" % OUT)
    tail_syserr(pm)


if __name__ == "__main__":
    main()
