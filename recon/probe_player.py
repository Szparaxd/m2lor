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
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymem

import autopot

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "player_probe.txt")

SNIPPET = r'''import player
f = open(r"__PATH__", "w")
try:
    try:
        f.write("MODULE dir: " + ",".join(sorted(dir(player))) + "\n")
    except Exception as e:
        f.write("dir ERR %s\n" % e)
    for n in sorted(dir(player)):
        try:
            v = getattr(player, n)
            if isinstance(v, int):
                f.write("CONST %s = %d\n" % (n, v))
        except Exception:
            pass
    for i in range(0, 41):
        try:
            f.write("GetStatus[%d] = %r\n" % (i, player.GetStatus(i)))
        except Exception as e:
            f.write("GetStatus[%d] = ERR %s\n" % (i, e))
finally:
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
        print("[!] Brak wyniku - snippet sie nie wykonal (player niedostepny?).")
        return
    time.sleep(0.1)
    with open(OUT, encoding="utf-8", errors="replace") as f:
        print(f.read())
    print("[i] Plik: %s" % OUT)


if __name__ == "__main__":
    main()
