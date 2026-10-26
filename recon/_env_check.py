"""
M1 / Zadanie 1 - sprawdzenie srodowiska.
Potwierdza, ze pymem potrafi podpiac sie do procesu gry i odczytac adres bazowy modulu.
Uruchom przy WLACZONEJ grze (postac w swiecie):  python recon/_env_check.py
"""
import pymem

PROCESS = "GameCoreTop.exe"


def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    print(f"[+] Podpieto do {PROCESS} (PID {pm.process_id})")
    print(f"[+] Adres bazowy modulu: {hex(base)}")


if __name__ == "__main__":
    main()
