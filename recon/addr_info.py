"""
M2 / krok 1 - klasyfikacja adresu.
Mowi, czy podany adres lezy WEWNATRZ modulu GameCoreTop.exe (adres statyczny, module-relative)
czy poza nim (sterta -> trzeba pointer scan).

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona, postac w swiecie.

Uzycie:
    python recon/addr_info.py <addr_hex>
Przyklad:
    python recon/addr_info.py 0x6a3014
"""
import sys

import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"


def main():
    if len(sys.argv) < 2:
        print("Uzycie: python recon/addr_info.py <addr_hex>")
        return
    addr = int(sys.argv[1], 16)

    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        print("[!] Nie moge otworzyc procesu. Uruchom terminal JAKO ADMINISTRATOR.")
        return

    module = pymem.process.module_from_name(pm.process_handle, PROCESS)
    base = module.lpBaseOfDll
    size = module.SizeOfImage
    end = base + size

    print(f"[+] {PROCESS}")
    print(f"    baza modulu : {hex(base)}")
    print(f"    rozmiar     : {hex(size)} ({size/1024/1024:.2f} MB)")
    print(f"    zakres      : {hex(base)} .. {hex(end)}")
    print(f"    badany adres: {hex(addr)}")
    print(f"    wartosc tam : {pm.read_float(addr):.3f}")

    if base <= addr < end:
        off = addr - base
        print(f"[+] WEWNATRZ modulu. To adres STATYCZNY.")
        print(f"    offset od bazy = {hex(off)}  ->  stabilny dostep: baza_modulu + {hex(off)}")
        print(f"    (po restarcie gry: baza sie zmieni, offset zostanie)")
    else:
        print(f"[+] POZA modulem (sterta/alokacja dynamiczna).")
        print(f"    Potrzebny pointer scan, by znalezc lancuch od statycznej bazy.")


if __name__ == "__main__":
    main()
