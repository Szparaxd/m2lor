"""Zrzut CALEGO ekwipunku gracza i wyswietlenie go w tabeli.

Jak dziala: autopot.dll (wstrzyknieta w gre) wykonuje W PROCESIE GRY snippet Pythona, ktory czyta
ekwipunek przez player.GetItemIndex/GetItemCount (+ nazwy z item.GetItemName) i zapisuje do
logs/inventory.txt. Ten skrypt (64-bit) tylko zleca zrzut przez nazwany potok i pokazuje wynik.
To ta sama droga, ktora bedzie pila wprost z ekwipunku (net.SendItemUsePacket) - najpierw patrzymy,
co gdzie lezy i jaki ma vnum.

WAZNE: wymaga NOWEJ autopot.dll (protokol tekstowy). Jesli gra ma zaladowana starsza wersje,
zrestartuj klienta - skrypt sam wstrzyknie aktualna DLL.

Uruchom (terminal JAKO ADMIN, gra wlaczona i zalogowana postacia):
    cd "C:/Metin/radar-project"; $env:PYTHONIOENCODING="utf-8"; python recon/inv_dump.py [-e EXE]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymem

import autopot


def main():
    p = argparse.ArgumentParser(description="Zrzut ekwipunku (Metin2).")
    p.add_argument("-e", "--exe", "-exe", default="GameCoreTop.exe", metavar="EXE",
                   help="nazwa procesu gry (domyslnie: %(default)s)")
    args = p.parse_args()
    if not args.exe.lower().endswith(".exe"):
        args.exe += ".exe"

    try:
        pm = pymem.Pymem(args.exe)
    except pymem.exception.ProcessNotFound:
        print("[!] Nie znalazlem procesu %s (gra wlaczona? inny exe -> -e NAZWA)" % args.exe)
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Uruchom terminal JAKO ADMINISTRATOR.")
        return

    ok, msg = autopot.ensure_injected(pm.process_id)
    print("[dll] %s" % msg)
    if not ok:
        print("[!] Bez DLL nie zrobie zrzutu. Zbuduj (build.bat) i sprobuj ponownie.")
        return

    inv = autopot.dump_inventory()
    if inv is None:
        print("[!] DLL nie odebral komendy. Czy to NOWA autopot.dll? Zrestartuj klienta gry.")
        return
    if not inv:
        print("Ekwipunek pusty albo gra bez zalogowanej postaci.")
        return

    print("\n slot  vnum      ile  nazwa")
    print(" ----  --------  ----  -----------------------------------")
    for slot, vn, cnt, nm in inv:
        print(" %4d  %8d  %4d  %s" % (slot, vn, cnt, nm))
    print("\nRazem zajetych pol: %d  (plik: %s)" % (len(inv), autopot.INV_DUMP_PATH))


if __name__ == "__main__":
    main()
