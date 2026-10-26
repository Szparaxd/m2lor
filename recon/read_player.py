"""
M1 / Zadanie 3 - odczyt pozycji gracza na zywo.
Czyta X/Y (ew. Z) spod adresow znalezionych skanerem i wypisuje je w petli.

WAZNE: uruchom terminal JAKO ADMINISTRATOR, gra wlaczona, postac w swiecie.

Uzycie:
    python recon/read_player.py <addr_X_hex> <addr_Y_hex> [addr_Z_hex]
Przyklad (adresy z tej sesji):
    python recon/read_player.py 0x6a3014 0x6a301c
    python recon/read_player.py 0x6a3014 0x6a301c 0x6a3018
"""
import sys
import time

import pymem

PROCESS = "GameCoreTop.exe"


def parse_addr(s):
    return int(s, 16)


def main():
    if len(sys.argv) < 3:
        print("Uzycie: python read_player.py <addr_X_hex> <addr_Y_hex> [addr_Z_hex]")
        print("Przyklad: python read_player.py 0x6a3014 0x6a301c")
        return

    addr_x = parse_addr(sys.argv[1])
    addr_y = parse_addr(sys.argv[2])
    addr_z = parse_addr(sys.argv[3]) if len(sys.argv) > 3 else None

    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Nie moge otworzyc procesu. Uruchom terminal JAKO ADMINISTRATOR.")
        return

    print("[+] Czytam pozycje. Biegaj postacia. Ctrl+C aby przerwac.")
    try:
        while True:
            x = pm.read_float(addr_x)
            y = pm.read_float(addr_y)

            # sanity-check: wylap adres z nieaktualnej sesji gry (smieciowe wartosci)
            if not (-1.0e7 < x < 1.0e7) or not (-1.0e7 < y < 1.0e7):
                print("[!] Wartosci wygladaja na smieci — adres pewnie z innej sesji gry. "
                      "Znajdz adres ponownie skanerem.")
                break

            if addr_z is not None:
                z = pm.read_float(addr_z)
                print(f"X={x:12.2f}  Y={y:12.2f}  Z={z:10.2f}")
            else:
                print(f"X={x:12.2f}  Y={y:12.2f}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[+] Koniec.")
    except Exception as e:
        print(f"[!] Blad odczytu: {e} (czy adres jest z TEJ sesji gry?)")


if __name__ == "__main__":
    main()
