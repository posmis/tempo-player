"""Потоковый time-stretch (WSOLA): меняет темп без изменения высоты тона.

Звук режется на перекрывающиеся окна по 2048 сэмплов и склеивается обратно
с шагом 1024. Чтобы ускорить/замедлить, окна берутся из исходника с другим
шагом, а точное место каждого окна подбирается (в пределах ±tol) так, чтобы
оно максимально совпадало с продолжением предыдущего — тогда склейка неслышна.
"""
import numpy as np


class WSOLA:
    def __init__(self, frame=2048, tol=512, decim=4):
        self.N = frame
        self.H = frame // 2
        self.tol = tol
        self.D = decim
        # периодическое окно Ханна: при перекрытии 50% сумма окон ровно 1
        self.win = np.hanning(frame + 1)[:-1].astype(np.float32)[:, None]
        self.factor = 1.0  # >1 быстрее, <1 медленнее
        self.src = None
        self.apos = 0.0

    def set_source(self, src, pos=0.0):
        self.src = src
        self.reset(pos)

    def reset(self, pos=0.0):
        self.apos = float(pos)
        self.prev = None
        self.ola = np.zeros((self.N, self.src.shape[1]), dtype=np.float32)

    @property
    def done(self):
        return self.apos >= len(self.src)

    def _get(self, start, length):
        n = len(self.src)
        if 0 <= start and start + length <= n:
            return self.src[start:start + length]
        out = np.zeros((length, self.src.shape[1]), dtype=np.float32)
        a, b = max(start, 0), min(start + length, n)
        if b > a:
            out[a - start:b - start] = self.src[a:b]
        return out

    def _search(self, ideal):
        if self.prev is None:
            return ideal
        natural = self.prev + self.H
        if natural == ideal:  # скорость 1.0 — идеальное продолжение, искать нечего
            return ideal
        N, tol, D = self.N, self.tol, self.D
        tmpl = self._get(natural, N).sum(axis=1)
        lo = ideal - tol
        region = self._get(lo, N + 2 * tol).sum(axis=1)
        # грубый поиск на прореженном сигнале…
        c = np.correlate(region[::D], tmpl[::D], mode="valid")
        k = int(np.argmax(c)) * D
        # …и уточнение с точностью до сэмпла
        best, best_v = k, -np.inf
        for j in range(max(0, k - D + 1), min(2 * tol, k + D - 1) + 1):
            v = float(np.dot(region[j:j + N], tmpl))
            if v > best_v:
                best, best_v = j, v
        return lo + best

    def step(self):
        """Выдаёт следующие H сэмплов растянутого/сжатого звука."""
        start = self._search(int(round(self.apos)))
        self.ola += self._get(start, self.N) * self.win
        out = self.ola[:self.H].copy()
        self.ola[:self.H] = self.ola[self.H:]
        self.ola[self.H:] = 0.0
        self.prev = start
        self.apos += self.H * self.factor
        return out
