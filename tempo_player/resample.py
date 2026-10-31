"""Качественный ресемплер с переменным коэффициентом (windowed sinc).

Линейная интерполяция при ускорении пропускает частоты выше Найквиста —
они «заворачиваются» обратно в слышимый диапазон металлической грязью,
а заодно глушит верха. Здесь каждый выходной сэмпл собирается из 16–34
соседних входных с весами sinc × окно Блэкмана; при ускорении фильтр
сужается (fc = 0.88 / ratio), чтобы срезать то, что иначе завернётся.
"""
import numpy as np

HALF_TAPS = 12  # пересечений нуля sinc с каждой стороны
HISTORY = 40   # сколько сэмплов держим слева от позиции чтения (хватает до ratio 2×)


def kernel_reach(ratio):
    """Сколько входных сэмплов справа нужно фильтру при данной скорости чтения."""
    return int(np.ceil(HALF_TAPS / _cutoff(ratio)))


def _cutoff(ratio):
    # при ускорении срез с запасом: переходная полоса фильтра должна целиком уйти ниже Найквиста
    return 1.0 if ratio <= 1.0 else 0.88 / ratio


def interpolate(buf, pos, ratio):
    """buf: (n, каналы) float32; pos: дробные позиции чтения (m,); ratio: скорость чтения.

    Требует pos >= HISTORY и len(buf) > pos.max() + kernel_reach(ratio).
    """
    fc = _cutoff(ratio)
    width = HALF_TAPS / fc
    k = kernel_reach(ratio)
    base = np.floor(pos).astype(np.int64)
    taps = np.arange(-k + 1, k + 1)
    x = base[:, None] + taps[None, :]                  # (m, 2k) индексы входа
    d = pos[:, None] - x                               # расстояние до каждого отсчёта
    u = np.clip(np.abs(d) / width, 0.0, 1.0)
    window = 0.42 + 0.5 * np.cos(np.pi * u) + 0.08 * np.cos(2 * np.pi * u)
    w = np.sinc(fc * d) * window
    w /= w.sum(axis=1, keepdims=True)                  # ровно 1 на постоянном сигнале
    w = w.astype(np.float32)
    return np.stack([(w * buf[x, c]).sum(axis=1) for c in range(buf.shape[1])], axis=1)
