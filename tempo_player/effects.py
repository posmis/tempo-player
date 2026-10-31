"""Эффекты в реальном времени: бас, lo-fi (приглушение + треск), эхо, реверб.

Параметры меняются из любого потока через set(); в аудиопотоке они
применяются в начале следующего блока (~23 мс).
"""
import copy
import threading

import numpy as np
from pedalboard import Delay, Limiter, LowpassFilter, LowShelfFilter, Reverb

DEFAULTS = {
    "reverb": {"on": False, "size": 0.75, "mix": 0.35},
    "bass": {"on": False, "gain": 6.0},
    "echo": {"on": False, "time": 0.35, "feedback": 0.35, "mix": 0.3},
    "lofi": {"on": False, "tone": 0.5, "crackle": 0.3},
}
LIMITS = {
    ("reverb", "size"): (0.0, 1.0),
    ("reverb", "mix"): (0.0, 1.0),
    ("bass", "gain"): (0.0, 15.0),
    ("echo", "time"): (0.05, 1.0),
    ("echo", "feedback"): (0.0, 0.85),
    ("echo", "mix"): (0.0, 1.0),
    ("lofi", "tone"): (0.0, 1.0),
    ("lofi", "crackle"): (0.0, 1.0),
}
# короткий «щелчок» иглы: быстро затухающий импульс
_CLICK = (np.exp(-np.arange(48) / 6.0) * np.cos(np.arange(48) * 0.9)).astype(np.float32)
_HISS = np.hanning(9).astype(np.float32) / np.hanning(9).sum()


class Effects:
    def __init__(self):
        self.params = copy.deepcopy(DEFAULTS)
        self._lock = threading.Lock()
        self._dirty = True
        self._reset = set()
        self.bass = LowShelfFilter(cutoff_frequency_hz=110, q=0.7)
        self.lowpass = LowpassFilter()
        self.delay = Delay()
        self.reverb = Reverb(damping=0.45, width=1.0)
        self.limiter = Limiter(threshold_db=-1.0, release_ms=120)  # бас не даст перегруза
        self._rng = np.random.default_rng()
        self._click_tail = np.zeros(len(_CLICK) - 1, dtype=np.float32)

    # -------------------------------------------------- управление
    def set(self, name, key, value):
        if name not in self.params or key not in self.params[name]:
            raise ValueError(f"нет параметра {name}.{key}")
        with self._lock:
            if key == "on":
                value = bool(value)
                if value and not self.params[name]["on"]:
                    self._reset.add(name)  # старый «хвост» реверба/эха не нужен
            else:
                lo, hi = LIMITS[(name, key)]
                value = min(hi, max(lo, float(value)))
            self.params[name][key] = value
            self._dirty = True

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.params)

    def _sync(self):
        p = self.params
        self.reverb.room_size = 0.3 + 0.68 * p["reverb"]["size"]
        self.reverb.wet_level = 0.55 * p["reverb"]["mix"]
        self.reverb.dry_level = 1.0 - 0.45 * p["reverb"]["mix"]
        self.bass.gain_db = p["bass"]["gain"]
        self.delay.delay_seconds = p["echo"]["time"]
        self.delay.feedback = p["echo"]["feedback"]
        self.delay.mix = 0.6 * p["echo"]["mix"]
        # приглушение: 0 -> срез 14 кГц (почти незаметно), 1 -> 1.2 кГц («из-за стены»)
        self.lowpass.cutoff_frequency_hz = 14000 * (1200 / 14000) ** p["lofi"]["tone"]
        plugins = {"reverb": self.reverb, "echo": self.delay, "bass": self.bass, "lofi": self.lowpass}
        for name in self._reset:
            plugins[name].reset()
        self._reset.clear()
        self._dirty = False

    # -------------------------------------------------- звук
    def _crackle(self, n, sr, level):
        """Треск пластинки: редкие щелчки разной силы + тихое шипение."""
        x = np.zeros(n, dtype=np.float32)
        k = self._rng.poisson((6 + 45 * level) * n / sr)
        if k:
            pos = self._rng.integers(0, n, k)
            amp = self._rng.exponential(1.0, k) * self._rng.choice((-1.0, 1.0), k)
            np.add.at(x, pos, (amp * 0.06 * level).astype(np.float32))
        y = np.convolve(x, _CLICK)
        y[:len(self._click_tail)] += self._click_tail
        self._click_tail = y[n:].copy()
        hiss = np.convolve(self._rng.standard_normal(n + 8).astype(np.float32), _HISS, "valid")
        return y[:n] + hiss * (0.012 * level)

    def process(self, block, sr):
        """block: (кадры, 2) float32 -> (кадры, 2) float32."""
        with self._lock:
            if self._dirty:
                self._sync()
            p = self.params
            on = {name: p[name]["on"] for name in p}
            crackle = p["lofi"]["crackle"]

        x = np.ascontiguousarray(block.T)
        if on["bass"]:
            x = self.bass.process(x, sr, reset=False)
        if on["lofi"]:
            x = self.lowpass.process(x, sr, reset=False)
        if on["echo"]:
            x = self.delay.process(x, sr, reset=False)
        if on["reverb"]:
            x = self.reverb.process(x, sr, reset=False)
        if on["lofi"] and crackle > 0:
            x = x + self._crackle(x.shape[1], sr, crackle)[None, :]
        x = self.limiter.process(np.ascontiguousarray(x, dtype=np.float32), sr, reset=False)
        return x.T
