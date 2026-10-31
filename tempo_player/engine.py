"""Аудиодвижок: темп и высота тона меняются независимо и в реальном времени.

Цепочка:  исходник -> WSOLA (темп / pitch) -> ресемплер (pitch) -> звуковая карта
 * «Пластинка»:    tempo = pitch = speed  -> WSOLA работает 1:1, всё делает ресемплер
 * «Только темп»:  tempo = speed, pitch = 1
 * «Только тон»:   tempo = 1, pitch = 2^(полутоны/12)
"""
import threading

import numpy as np
from .effects import Effects
from .stretch import WSOLA

BLOCK = 1024
WAVE_BINS = 1200
SCOPE_N = 4096      # сколько последних сэмплов вывода держим для спектра
SPEC_BANDS = 56


class Engine:
    def __init__(self):
        self.src = None
        self.sr = 44100
        self.peaks = None
        self.tempo = 1.0
        self.pitch = 1.0
        self.volume = 0.6 ** 3  # громкость — множитель амплитуды; ползунок = корень кубический
        self.playing = False
        self.lock = threading.Lock()
        self.stream = None
        self.stretch = WSOLA()
        self.fx = Effects()
        self._buf = np.zeros((0, 2), dtype=np.float32)
        self._rp = 0.0
        self._cur_pitch = 1.0
        self._cur_vol = self.volume
        self._scope = np.zeros(SCOPE_N, dtype=np.float32)
        self._scope_win = np.hanning(SCOPE_N).astype(np.float32)

    # ---------------- загрузка ----------------
    def load(self, data, sr):
        """Принимает уже декодированный звук и открывает аудиовыход."""
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        data = np.ascontiguousarray(data[:, :2])
        peaks = self._peaks(data)

        self.close()
        with self.lock:
            self.src, self.sr, self.peaks = data, sr, peaks
            self.stretch.set_source(data)
            self._buf = np.zeros((0, 2), dtype=np.float32)
            self._rp = 0.0
            self.playing = False

        import sounddevice as sd
        self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32",
                                      blocksize=BLOCK, latency=0.08,
                                      callback=self._callback)
        self.stream.start()

    @staticmethod
    def _peaks(data):
        mono = np.abs(data).max(axis=1)
        n = len(mono) // WAVE_BINS * WAVE_BINS
        if n == 0:
            return np.zeros(WAVE_BINS, dtype=np.float32)
        p = mono[:n].reshape(WAVE_BINS, -1).max(axis=1)
        return p / (p.max() or 1.0)

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    # ---------------- DSP ----------------
    def render(self, frames):
        out = self._render(frames)
        self._push_scope(out.mean(axis=1))
        return out

    def _push_scope(self, mono):
        n = len(mono)
        if n >= SCOPE_N:
            self._scope = mono[-SCOPE_N:].astype(np.float32)
        else:
            sc = np.empty_like(self._scope)
            sc[:-n] = self._scope[n:]
            sc[-n:] = mono
            self._scope = sc  # подмена ссылки целиком — читатель всегда видит целый массив

    def spectrum(self, bands=SPEC_BANDS):
        """Уровни частотных полос 0..1 (логарифмическая шкала 40 Гц – 16 кГц)."""
        x = self._scope * self._scope_win
        mag = np.abs(np.fft.rfft(x)) * (2.0 / self._scope_win.sum())
        freqs = np.fft.rfftfreq(SCOPE_N, 1.0 / self.sr)
        edges = np.geomspace(40.0, min(16000.0, self.sr * 0.45), bands + 1)
        idx = np.searchsorted(freqs, edges)
        out = np.empty(bands, dtype=np.float32)
        for i in range(bands):
            a = idx[i]
            b = max(idx[i + 1], a + 1)
            out[i] = mag[a:b].max()
        centers = np.sqrt(edges[:-1] * edges[1:])
        db = 20 * np.log10(out + 1e-9) + 3.0 * np.log2(centers / 1000.0)  # музыка тише на высоких — выравниваем
        return np.clip((db + 66.0) / 56.0, 0.0, 1.0)

    def _render(self, frames):
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            if self.src is None or not self.playing:
                return out
            p0, p1 = self._cur_pitch, self.pitch
            self._cur_pitch = p1
            steps = np.linspace(p0, p1, frames, endpoint=False)
            idx = self._rp + np.concatenate(([0.0], np.cumsum(steps[:-1])))

            # WSOLA должна выдать звук в темпе tempo/pitch — ресемплер потом
            # ускорит его в pitch раз, и в сумме получится нужный темп
            self.stretch.factor = self.tempo / p1
            need = int(idx[-1]) + 2
            chunks, have = [self._buf], len(self._buf)
            while have < need:
                c = self.stretch.step()
                chunks.append(c)
                have += len(c)
            buf = np.concatenate(chunks) if len(chunks) > 1 else self._buf

            i0 = idx.astype(np.int64)
            f = (idx - i0)[:, None].astype(np.float32)
            out[:] = buf[i0] * (1.0 - f) + buf[i0 + 1] * f

            adv = self._rp + steps.sum()
            drop = int(adv)
            self._buf = buf[drop:]
            self._rp = adv - drop

            vol = np.linspace(self._cur_vol, self.volume, frames,
                              endpoint=False, dtype=np.float32)[:, None]
            self._cur_vol = self.volume
            out *= vol

            if self.stretch.done:
                self.playing = False
                self._seek(0.0)
            sr = self.sr
        return self.fx.process(out, sr)

    def _callback(self, outdata, frames, time, status):
        outdata[:] = self.render(frames)

    # ---------------- управление ----------------
    def set_params(self, tempo, pitch):
        self.tempo, self.pitch = float(tempo), float(pitch)

    @property
    def loaded(self):
        return self.src is not None

    @property
    def duration(self):
        return 0.0 if self.src is None else len(self.src) / self.sr

    @property
    def position(self):
        return 0.0 if self.src is None else min(self.stretch.apos / self.sr, self.duration)

    def _seek(self, sec):
        pos = float(np.clip(sec * self.sr, 0, len(self.src) - 1))
        self.stretch.reset(pos)
        self._buf = np.zeros((0, 2), dtype=np.float32)
        self._rp = 0.0

    def seek(self, sec):
        with self.lock:
            if self.src is not None:
                self._seek(sec)
