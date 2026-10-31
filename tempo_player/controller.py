"""Состояние плеера без привязки к интерфейсу: режим, скорость, загрузка треков.

Используется веб-версией; все методы можно вызывать из любых потоков.
"""
import os
import threading
import time

from . import sources
from .engine import Engine

SPEED_MIN, SPEED_MAX = 0.5, 2.0
SEMI_MIN, SEMI_MAX = -12, 12
MODES = ("vinyl", "tempo", "pitch")


class Controller:
    def __init__(self):
        self.engine = Engine()
        self.mode = "vinyl"
        self.speed = 1.0
        self.semi = 0
        self.title = ""
        self.status = ""
        self.error = False
        self.busy = False
        self.track_id = 0
        self._lock = threading.Lock()
        self._apply()

    # ------------------------------------------------------------ скорость
    def set_mode(self, mode):
        if mode in MODES:
            self.mode = mode
            self._apply()

    def set_speed(self, v):
        self.speed = round(min(SPEED_MAX, max(SPEED_MIN, float(v))), 2)
        self._apply()

    def set_semi(self, v):
        self.semi = int(round(min(SEMI_MAX, max(SEMI_MIN, float(v)))))
        self._apply()

    def set_volume(self, v):
        self.engine.volume = min(1.2, max(0.0, float(v)))

    def _apply(self):
        if self.mode == "vinyl":
            self.engine.set_params(self.speed, self.speed)
        elif self.mode == "tempo":
            self.engine.set_params(self.speed, 1.0)
        else:
            self.engine.set_params(1.0, 2 ** (self.semi / 12))

    # ------------------------------------------------------------ плеер
    def toggle(self):
        if self.engine.loaded:
            self.engine.playing = not self.engine.playing

    def seek(self, sec):
        self.engine.seek(float(sec))

    # ------------------------------------------------------------ загрузка
    def load_path(self, path, title=None, delete_after=False):
        name = title or os.path.splitext(os.path.basename(path))[0]

        def job():
            try:
                return sources.decode(path), name
            finally:
                if delete_after:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        return self._start(job, "Загружаю…")

    def load_url(self, url):
        url = (url or "").strip()
        if not sources.looks_like_url(url):
            self._set_status("Это не похоже на ссылку", error=True)
            return False

        def job():
            if sources.cached(url):
                self._set_status("Из кэша, без скачивания…")

            def progress(frac):
                self._set_status(f"Скачиваю с YouTube…  {int(frac * 100)}%")

            path, title = sources.download_youtube(url, progress)
            self._set_status("Подготавливаю звук…")
            return sources.decode(path), title

        return self._start(job, "Ищу видео…")

    def _start(self, job, status):
        with self._lock:
            if self.busy:
                return False
            self.busy = True
        self._set_status(status)

        def run():
            try:
                (data, sr), title = job()
                self.engine.load(data, sr)
                self.title = title
                self.track_id += 1
                self._set_status("")
                self.engine.playing = True
            except Exception as e:  # noqa: BLE001 — любую ошибку показываем пользователю
                self._set_status(f"Ошибка: {str(e)[:160] or e.__class__.__name__}", error=True)
            finally:
                self.busy = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def _set_status(self, text, error=False):
        self.status, self.error = text, error

    # ------------------------------------------------------------ кэш
    def clear_cache(self):
        if self.busy:
            return False
        sources.clear_cache()
        self._set_status("Кэш очищен")
        return True

    # ------------------------------------------------------------ снимок
    def state(self):
        e = self.engine
        return {
            "t": time.time(),
            "loaded": e.loaded,
            "playing": e.playing,
            "position": e.position,
            "duration": e.duration,
            "rate": e.tempo,
            "mode": self.mode,
            "speed": self.speed,
            "semi": self.semi,
            "volume": e.volume,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "busy": self.busy,
            "track_id": self.track_id,
            "cache_mb": round(sources.cache_size() / 1024 / 1024, 1),
        }

    def close(self):
        self.engine.close()
