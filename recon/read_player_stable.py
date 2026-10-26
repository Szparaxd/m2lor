"""
M2 - docelowy czytnik pozycji gracza (STABILNY).

Roznice wzgledem read_player.py:
  * adresy liczone jako BAZA_MODULU + offset  -> przezywa restart gry i ASLR
    (pymem odczytuje biezaca baze modulu przy kazdym uruchomieniu),
  * LOGUJE do pliku  logs/read_player_stable.log  (wiec nie trzeba przeklejac wynikow),
  * pokazuje WCISNIETE KLAWISZE / przyciski myszy obok wspolrzednych
    (widac dokladnie: stoisz / biegniesz WSAD / klikasz mysza).

WAZNE: terminal JAKO ADMINISTRATOR, gra wlaczona, postac w swiecie.

Offsety znalezione w M1/M2 (module-relative, GameCoreTop.exe):
    X = +0x493014,  Y = +0x49301c,  Z(?) = +0x493018

Uzycie:
    python recon/read_player_stable.py
    python recon/read_player_stable.py 0x493014 0x49301c 0x493018   # wlasne offsety
"""
import ctypes
import os
import sys
import time
from datetime import datetime

import pymem
import pymem.process

PROCESS = "GameCoreTop.exe"

# offsety od bazy modulu (domyslne - z naszego reverse engineeringu)
OFF_X = 0x493014
OFF_Y = 0x49301C
OFF_Z = 0x493018

# --- log do pliku ---
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "read_player_stable.log")


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- stan klawiszy przez GetAsyncKeyState ---
user32 = ctypes.WinDLL("user32", use_last_error=True)
KEYS = [
    ("LMB", 0x01), ("RMB", 0x02),
    ("W", 0x57), ("A", 0x41), ("S", 0x53), ("D", 0x44),
    ("UP", 0x26), ("LEFT", 0x25), ("DOWN", 0x28), ("RIGHT", 0x27),
    ("SPACE", 0x20), ("SHIFT", 0x10), ("CTRL", 0x11),
]


def pressed_keys():
    out = []
    for name, vk in KEYS:
        if user32.GetAsyncKeyState(vk) & 0x8000:
            out.append(name)
    return out


def main():
    if len(sys.argv) >= 4:
        off_x = int(sys.argv[1], 16)
        off_y = int(sys.argv[2], 16)
        off_z = int(sys.argv[3], 16)
    else:
        off_x, off_y, off_z = OFF_X, OFF_Y, OFF_Z

    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        log(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return
    except pymem.exception.CouldNotOpenProcess:
        log("[!] Nie moge otworzyc procesu. Uruchom terminal JAKO ADMINISTRATOR.")
        return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    addr_x, addr_y, addr_z = base + off_x, base + off_y, base + off_z

    log("=" * 60)
    log(f"[+] START. baza modulu = {hex(base)}")
    log(f"[+] X @ {hex(addr_x)} (base+{hex(off_x)}), "
        f"Y @ {hex(addr_y)} (base+{hex(off_y)}), Z @ {hex(addr_z)} (base+{hex(off_z)})")
    log("[+] Biegaj/klikaj postacia. Ctrl+C aby przerwac.")

    last_log = 0.0
    last_keys = None
    try:
        while True:
            x = pm.read_float(addr_x)
            y = pm.read_float(addr_y)
            z = pm.read_float(addr_z)

            if not (-1.0e7 < x < 1.0e7) or not (-1.0e7 < y < 1.0e7):
                log("[!] Wartosci wygladaja na smieci - zly offset albo inny build klienta.")
                break

            keys = pressed_keys()
            keys_str = "+".join(keys) if keys else "-"
            now = time.time()

            # loguj co ~0.4s LUB gdy zmienil sie zestaw klawiszy
            if now - last_log > 0.4 or keys != last_keys:
                log(f"X={x:11.2f}  Y={y:11.2f}  Z={z:9.2f}  klawisze=[{keys_str}]")
                last_log = now
                last_keys = keys

            time.sleep(0.05)
    except KeyboardInterrupt:
        log("[+] Koniec.")
    except Exception as e:
        log(f"[!] Blad odczytu: {e}")


if __name__ == "__main__":
    main()
