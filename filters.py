"""
Modulares Filtersystem fuer Sensor-Signale
==========================================
Rohdaten kommen rein, gefilterte Daten gehen raus.
Filter sind stapelbar (Kette) und pro Sensor unabhaengig konfigurierbar.

Verfuegbare Filter:
    raw       - keine Aenderung (Passthrough)
    mean      - arithmetisches Mittel ueber N letzte Werte
    median    - Median ueber N letzte Werte (robust gegen Ausreisser)
    rms       - Root-Mean-Square ueber N letzte Werte
    lowpass   - Exponentieller gleitender Mittelwert (EMA), Parameter alpha (0..1)
    min       - Minimum ueber N letzte Werte
    max       - Maximum ueber N letzte Werte

Kette wird als Liste von Dicts konfiguriert:
    [{"type": "median", "n": 5}, {"type": "lowpass", "alpha": 0.3}]
Die Filter werden in Reihenfolge angewendet.
"""

import statistics
import math
from collections import deque


class Filter:
    def apply(self, value):
        raise NotImplementedError
    def reset(self):
        pass


class RawFilter(Filter):
    def apply(self, value):
        return value


class WindowFilter(Filter):
    """Basisklasse fuer Fenster-basierte Filter."""
    def __init__(self, n=10):
        self.n = max(1, int(n))
        self.buf = deque(maxlen=self.n)

    def apply(self, value):
        if value is None:
            return self._compute() if self.buf else None
        self.buf.append(float(value))
        return self._compute()

    def _compute(self):
        raise NotImplementedError

    def reset(self):
        self.buf.clear()


class MeanFilter(WindowFilter):
    def _compute(self):
        return sum(self.buf) / len(self.buf)


class MedianFilter(WindowFilter):
    def _compute(self):
        return statistics.median(self.buf)


class RmsFilter(WindowFilter):
    def _compute(self):
        return math.sqrt(sum(x * x for x in self.buf) / len(self.buf))


class MinFilter(WindowFilter):
    def _compute(self):
        return min(self.buf)


class MaxFilter(WindowFilter):
    def _compute(self):
        return max(self.buf)


class LowpassFilter(Filter):
    """Exponentieller gleitender Mittelwert (EMA): y = alpha*x + (1-alpha)*y_prev"""
    def __init__(self, alpha=0.3):
        self.alpha = max(0.001, min(1.0, float(alpha)))
        self.prev = None

    def apply(self, value):
        if value is None:
            return self.prev
        if self.prev is None:
            self.prev = float(value)
        else:
            self.prev = self.alpha * float(value) + (1 - self.alpha) * self.prev
        return self.prev

    def reset(self):
        self.prev = None


_REGISTRY = {
    "raw":     lambda cfg: RawFilter(),
    "mean":    lambda cfg: MeanFilter(cfg.get("n", 10)),
    "median":  lambda cfg: MedianFilter(cfg.get("n", 10)),
    "rms":     lambda cfg: RmsFilter(cfg.get("n", 10)),
    "min":     lambda cfg: MinFilter(cfg.get("n", 10)),
    "max":     lambda cfg: MaxFilter(cfg.get("n", 10)),
    "lowpass": lambda cfg: LowpassFilter(cfg.get("alpha", 0.3)),
}

AVAILABLE_FILTERS = list(_REGISTRY.keys())


def build_chain(config):
    """Erstelle Filterkette aus Config-Liste.

    config: Liste von Dicts, z.B.
        [{"type": "median", "n": 5}, {"type": "lowpass", "alpha": 0.3}]
    Returns: FilterChain-Objekt mit .apply(value) Methode.
    """
    if not config:
        return FilterChain([RawFilter()])
    filters = []
    for item in config:
        ftype = item.get("type", "raw")
        factory = _REGISTRY.get(ftype)
        if factory is None:
            continue  # unbekannter Filter: ueberspringen
        filters.append(factory(item))
    if not filters:
        filters = [RawFilter()]
    return FilterChain(filters)


class FilterChain:
    def __init__(self, filters):
        self.filters = filters

    def apply(self, value):
        v = value
        for f in self.filters:
            v = f.apply(v)
        return v

    def reset(self):
        for f in self.filters:
            f.reset()
