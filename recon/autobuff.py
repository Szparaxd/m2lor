"""Auto-buff: odnawianie buffow-umiejetnosci na sobie.

UWAGA: oryginalny modul nigdy nie trafil do repo (radar_map.py importowal go od ed4d228, a pliku
nie bylo w zadnym commicie) - bez niego radar padal na starcie (ModuleNotFoundError) i panel byl
pusty. To jest odtworzenie INTERFEJSU uzywanego przez radar_map.py / botapi.py:

    BUFFS[vnum] = (nazwa, cooldown_s)
    AutoBuff(pm, proc_name): .on .enabled .known {vnum: slot} .status .count ._next
                             .step(...)  .cast_now(vnum, use)  .summary()
    make_use_fn(pid, dll_exec, native) -> use(slot) albo None (brak drogi wykonania)

Odczyt wyuczonych umiejetnosci z pamieci NIE jest jeszcze odtworzony: `known` zostaje puste, wiec
auto-buff i buffy przed walka nic nie rzucaja (status mowi dlaczego). Gdy `known` zostanie
wypelnione (vnum -> slot okna umiejetnosci), step/cast_now juz rzucaja i pilnuja cooldownu.
"""
import time

import autopot

STEP_EVERY = 1.0          # s - throttle kroku (radar sprawdza _next przed czytaniem pamieci)
RECAST_GAP = 2.0          # s - min. odstep miedzy dowolnymi dwoma rzutami (animacja)

# vnum -> (nazwa, cooldown w s). Buffy rzucane na siebie, wg klas.
BUFFS = {
    3:   ("Szal", 60),
    4:   ("Aura Miecza", 60),
    19:  ("Silne Cialo", 60),
    34:  ("Kamuflaz", 60),
    63:  ("Zaczarowane Ostrze", 60),
    64:  ("Strach", 60),
    65:  ("Zaczarowana Zbroja", 60),
    94:  ("Blogoslawienstwo", 60),
    95:  ("Odbicie", 60),
    96:  ("Pomoc Smoka", 60),
    110: ("Zwinnosc", 60),
    111: ("Zwiekszenie Ataku", 60),
}


def _log(fmt, *a):
    print("[buff] " + (fmt % a if a else fmt), flush=True)


def make_use_fn(pid, dll_exec, native):
    """Droga wykonania umiejetnosci: vCore - '!skill' z hooka (native), GameCoreTop - Python gry
    (dll_exec). None = brak drogi (stara DLL / niewstrzyknieta)."""
    if native:
        return lambda slot: autopot.use_skill_native(pid, slot)
    if dll_exec:
        return lambda slot: autopot.use_skill_py(pid, slot)
    return None


class AutoBuff:
    def __init__(self, pm, proc_name):
        self.pm = pm
        self.proc_name = proc_name
        self.on = False
        self.enabled = set()      # vnum-y wlaczone przez uzytkownika
        self.known = {}           # vnum -> slot okna umiejetnosci (wyuczone przez postac)
        self.status = ""
        self.count = 0            # ile rzutow wyslano
        self._next = 0.0
        self._last_cast = {}      # vnum -> czas ostatniego rzutu
        self._last_any = 0.0
        _log("modul zastepczy: odczyt wyuczonych umiejetnosci niedostepny - known puste")

    def _ready(self, vnum, now):
        cd = BUFFS.get(vnum, (str(vnum), 60))[1]
        return now - self._last_cast.get(vnum, 0.0) >= cd

    def _cast(self, vnum, use, now):
        slot = self.known[vnum]
        ok = bool(use(slot))
        _log("rzut %s vnum=%d slot=%d wyslano=%s", BUFFS.get(vnum, (str(vnum),))[0], vnum, slot, ok)
        if ok:
            self._last_cast[vnum] = now
            self._last_any = now
            self.count += 1
        return ok

    def cast_now(self, vnum, use):
        """Rzut jednego buffa teraz (przygotowanie do walki). Zwraca: None (brak drogi wykonania),
        'nie wyuczony', 'aktywny' (cooldown po naszym rzucie = buff trwa), 'czekam' (animacja
        poprzedniego), 'rzucam', 'odrzucony'."""
        if use is None:
            return None
        if vnum not in self.known:
            return "nie wyuczony"
        now = time.time()
        if not self._ready(vnum, now):
            return "aktywny"
        if now - self._last_any < RECAST_GAP:
            return "czekam"
        return "rzucam" if self._cast(vnum, use, now) else "odrzucony"

    def step(self, player_obj, self_actor, mounted, alive, use):
        now = time.time()
        self._next = now + STEP_EVERY
        if not self.on:
            self.status = ""
            return
        if use is None:
            self.status = "brak drogi wykonania (DLL)"
        elif not self.known:
            self.status = "brak wyuczonych buffow (odczyt niedostepny)"
        elif not alive:
            self.status = "martwy"
        elif mounted:
            self.status = "na koniu"
        else:
            todo = [v for v in sorted(self.enabled) if v in self.known and self._ready(v, now)]
            if not todo:
                self.status = "aktywne"
            elif now - self._last_any < RECAST_GAP:
                self.status = "czekam"
            else:
                self.status = "rzucam %s" % BUFFS.get(todo[0], (str(todo[0]),))[0]
                self._cast(todo[0], use, now)

    def summary(self):
        if not self.known:
            return "-"
        now = time.time()
        parts = []
        for vnum in sorted(self.known, key=lambda v: self.known[v]):
            name = BUFFS.get(vnum, (str(vnum), 60))[0]
            cd = BUFFS.get(vnum, (str(vnum), 60))[1]
            left = cd - (now - self._last_cast.get(vnum, 0.0))
            mark = "WYL" if vnum not in self.enabled else ("%ds" % left if left > 0 else "gotowy")
            parts.append("%s:%s" % (name, mark))
        return " ".join(parts)
