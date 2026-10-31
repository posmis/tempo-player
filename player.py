"""
Speed Up / Slowed плеер с изменением скорости в реальном времени.

Запуск:  python player.py [путь_к_файлу]
Клавиши: Пробел — play/pause, ← / → — скорость -/+ 0.05, 0 — сброс на 1.0
"""
import sys
import threading

import numpy as np
import soundfile as sf
import tkinter as tk
from tkinter import filedialog, ttk

BLOCK = 1024  # размер аудиоблока (~23 мс при 44.1 кГц) — задержка реакции на ползунок


class Engine:
    """Аудиодвижок: держит трек в памяти и читает его с переменной скоростью."""

    def __init__(self):
        self.data = None          # np.ndarray (frames, 2) float32
        self.sr = 44100
        self.pos = 0.0            # дробная позиция в сэмплах исходника
        self.speed = 1.0          # целевая скорость (меняется из GUI)
        self._cur_speed = 1.0     # фактическая скорость (плавно догоняет целевую)
        self.volume = 1.0
        self.playing = False
        self.lock = threading.Lock()
        self.stream = None

    # ---------- загрузка ----------
    def load(self, path):
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        data = np.ascontiguousarray(data[:, :2])
        self.close()
        with self.lock:
            self.data, self.sr, self.pos, self.playing = data, sr, 0.0, False
        import sounddevice as sd
        self.stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32",
                                      blocksize=BLOCK, callback=self._callback)
        self.stream.start()

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    # ---------- DSP ----------
    def render(self, frames):
        """Возвращает следующие `frames` сэмплов вывода с учётом скорости."""
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            data = self.data
            if data is None or not self.playing:
                return out
            # плавно меняем скорость внутри блока — без щелчков при движении ползунка
            steps = np.linspace(self._cur_speed, self.speed, frames, endpoint=False)
            self._cur_speed = self.speed
            idx = self.pos + np.concatenate(([0.0], np.cumsum(steps[:-1])))
            self.pos += steps.sum()

            n = len(data)
            valid = idx < n - 1
            i0 = idx[valid].astype(np.int64)
            frac = (idx[valid] - i0)[:, None].astype(np.float32)
            # линейная интерполяция между соседними сэмплами
            out[valid] = data[i0] * (1.0 - frac) + data[i0 + 1] * frac
            out *= self.volume

            if self.pos >= n - 1:  # трек закончился
                self.playing = False
                self.pos = 0.0
        return out

    def _callback(self, outdata, frames, time, status):
        outdata[:] = self.render(frames)

    # ---------- управление ----------
    @property
    def duration(self):
        return 0.0 if self.data is None else len(self.data) / self.sr

    @property
    def position_sec(self):
        return self.pos / self.sr

    def seek(self, sec):
        with self.lock:
            if self.data is not None:
                self.pos = float(np.clip(sec * self.sr, 0, len(self.data) - 2))


def fmt(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


class App:
    PRESETS = [("Slowed 0.75", 0.75), ("Slowed 0.85", 0.85), ("Normal", 1.0),
               ("Sped up 1.25", 1.25), ("Nightcore 1.35", 1.35)]

    def __init__(self, root, path=None):
        self.root, self.engine = root, Engine()
        self.dragging = False
        root.title("Speed Up / Slowed Player")
        root.geometry("560x300")
        pad = dict(padx=10, pady=4)

        top = ttk.Frame(root); top.pack(fill="x", **pad)
        ttk.Button(top, text="Открыть…", command=self.open_file).pack(side="left")
        self.play_btn = ttk.Button(top, text="▶ Play", command=self.toggle)
        self.play_btn.pack(side="left", padx=6)
        self.title_lbl = ttk.Label(top, text="Файл не выбран")
        self.title_lbl.pack(side="left", padx=6)

        # позиция
        posf = ttk.Frame(root); posf.pack(fill="x", **pad)
        self.pos_var = tk.DoubleVar()
        self.pos_scale = ttk.Scale(posf, from_=0, to=1, variable=self.pos_var)
        self.pos_scale.pack(side="left", fill="x", expand=True)
        self.pos_scale.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
        self.pos_scale.bind("<ButtonRelease-1>", self.on_seek)
        self.time_lbl = ttk.Label(posf, text="0:00 / 0:00", width=12)
        self.time_lbl.pack(side="left", padx=6)

        # скорость
        sf_ = ttk.Frame(root); sf_.pack(fill="x", **pad)
        ttk.Label(sf_, text="Скорость", width=10).pack(side="left")
        self.speed_var = tk.DoubleVar(value=1.0)
        ttk.Scale(sf_, from_=0.5, to=2.0, variable=self.speed_var,
                  command=self.on_speed).pack(side="left", fill="x", expand=True)
        self.speed_lbl = ttk.Label(sf_, text="1.00x", width=7)
        self.speed_lbl.pack(side="left", padx=6)

        # громкость
        vf = ttk.Frame(root); vf.pack(fill="x", **pad)
        ttk.Label(vf, text="Громкость", width=10).pack(side="left")
        self.vol_var = tk.DoubleVar(value=1.0)
        ttk.Scale(vf, from_=0.0, to=1.5, variable=self.vol_var,
                  command=lambda v: setattr(self.engine, "volume", float(v))
                  ).pack(side="left", fill="x", expand=True)
        ttk.Label(vf, text="", width=7).pack(side="left", padx=6)

        # пресеты
        pf = ttk.Frame(root); pf.pack(fill="x", **pad)
        for name, val in self.PRESETS:
            ttk.Button(pf, text=name, command=lambda v=val: self.set_speed(v)).pack(
                side="left", expand=True, fill="x", padx=2)

        ttk.Label(root, text="Пробел — play/pause   ← → — скорость ±0.05   0 — сброс",
                  foreground="gray").pack(pady=8)

        root.bind("<space>", lambda e: self.toggle())
        root.bind("<Left>", lambda e: self.set_speed(self.speed_var.get() - 0.05))
        root.bind("<Right>", lambda e: self.set_speed(self.speed_var.get() + 0.05))
        root.bind("0", lambda e: self.set_speed(1.0))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        if path:
            self.load(path)
        self.tick()

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[
            ("Аудио", "*.mp3 *.wav *.flac *.ogg *.aiff"), ("Все файлы", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        try:
            self.engine.load(path)
        except Exception as e:
            self.title_lbl.config(text=f"Ошибка: {e}")
            return
        self.title_lbl.config(text=path.replace("\\", "/").split("/")[-1][:45])
        self.pos_scale.config(to=self.engine.duration)
        self.engine.playing = True

    def toggle(self):
        if self.engine.data is not None:
            self.engine.playing = not self.engine.playing

    def set_speed(self, v):
        v = round(min(2.0, max(0.5, v)), 2)
        self.speed_var.set(v)
        self.on_speed(v)

    def on_speed(self, v):
        self.engine.speed = float(v)
        self.speed_lbl.config(text=f"{float(v):.2f}x")

    def on_seek(self, _e):
        self.engine.seek(self.pos_var.get())
        self.dragging = False

    def tick(self):
        e = self.engine
        if not self.dragging:
            self.pos_var.set(e.position_sec)
        self.time_lbl.config(text=f"{fmt(e.position_sec)} / {fmt(e.duration)}")
        self.play_btn.config(text="⏸ Pause" if e.playing else "▶ Play")
        self.root.after(200, self.tick)

    def quit(self):
        self.engine.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()
