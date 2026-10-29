"""Dziennik diagnostyczny sesji radaru (WALK_DEBUG): dwa pliki w data/logs/:
    <stamp>_<name>_walk.log     - czytelne linie '<s od startu> <tekst>' (Trace.text)
    <stamp>_<name>_trace.jsonl  - rekord JSON na zdarzenie {"t": s, "k": typ, ...pola} (Trace.rec)

Odtworzony: oryginalny modul nigdy nie trafil do repo (radar_map.py importowal go, a pliku nie bylo
w zadnym commicie). Interfejs wg uzycia w radar_map.py: Trace(name).text/.rec/.log_path/.jsonl_path,
ipt, ipts, pack_grid.
"""
import base64
import json
import os
import threading
import time

import numpy as np

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs")


def ipt(p):
    """Punkt -> [int x, int y] (zwarty JSON) albo None."""
    if p is None or p[0] is None or p[1] is None:
        return None
    return [int(round(p[0])), int(round(p[1]))]


def ipts(pts):
    """Lista punktow -> lista [x, y] (None dla pustej/brakujacej)."""
    if not pts:
        return []
    return [ipt(p) for p in pts]


def pack_grid(grid, x0, y0):
    """Siatka blokad (np.bool_ [H, W]) -> pola rekordu: x0/y0 (komorki), w/h, bits (base64 packbits
    wierszami, kolejnosc bitow 'big'). Rozpakowanie: np.unpackbits(..)[:h*w].reshape(h, w)."""
    g = np.asarray(grid, dtype=bool)
    h, w = g.shape
    bits = base64.b64encode(np.packbits(g.ravel()).tobytes()).decode("ascii")
    return {"x0": int(x0), "y0": int(y0), "w": int(w), "h": int(h), "bits": bits}


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return repr(o)


class Trace:
    def __init__(self, name):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))
        self.log_path = os.path.join(LOG_DIR, "%s_%s_walk.log" % (stamp, safe))
        self.jsonl_path = os.path.join(LOG_DIR, "%s_%s_trace.jsonl" % (stamp, safe))
        self.t0 = time.time()
        self._lock = threading.Lock()
        self._log = open(self.log_path, "a", encoding="utf-8", buffering=1)
        self._jsonl = open(self.jsonl_path, "a", encoding="utf-8", buffering=1)

    def _t(self):
        return round(time.time() - self.t0, 3)

    def text(self, line):
        with self._lock:
            self._log.write("%9.3f %s\n" % (self._t(), line))

    def rec(self, kind, **fields):
        row = {"t": self._t(), "k": kind}
        row.update(fields)
        s = json.dumps(row, default=_default, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._jsonl.write(s + "\n")

    def close(self):
        with self._lock:
            for f in (self._log, self._jsonl):
                try:
                    f.close()
                except Exception:
                    pass
