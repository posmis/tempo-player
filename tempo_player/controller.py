"""Состояние плеера без привязки к интерфейсу: режим, скорость, загрузка треков.

Используется веб-версией; все методы можно вызывать из любых потоков.
"""
import os
import threading
import time

from . import sources
from .history import History
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
        self.status = None  # {"key": ..., "args": {...}} — текст переводит страница
        self.error = False
        self.busy = False
        self.track_id = 0
        self.current_id = None  # id видео YouTube, которое сейчас загружено
        self.author = ""
        self.history = History()
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
        self.engine.volume = min(1.0, max(0.0, float(v)))

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

        return self._start(job, "loading", video_id=None)

    def load_url(self, url):
        url = (url or "").strip()
        if not sources.looks_like_url(url):
            self._set_status("not_url", error=True)
            return False

        def job():
            if sources.cached(url):
                self._set_status("from_cache")

            def progress(frac):
                self._set_status("downloading", pct=int(frac * 100))

            path, title = sources.download_youtube(url, progress)
            self._set_status("preparing")
            return sources.decode(path), title

        vid = sources.video_id(url)
        return self._start(job, "searching", video_id=vid)

    def _start(self, job, status, video_id=None):
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
                meta = sources.meta(video_id) if video_id else {}
                self.author, self.title = sources.split_title(**{**meta, "title": title})
                self.current_id = video_id
                self.track_id += 1
                if video_id:
                    self.history.add(video_id, title, len(data) / sr)  # полное название, как на YouTube
                self._set_status(None)
                self.engine.playing = True
            except Exception as e:  # noqa: BLE001 — любую ошибку показываем пользователю
                self._set_status("error", error=True, msg=str(e)[:160] or e.__class__.__name__)
            finally:
                self.busy = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def _set_status(self, key, error=False, **args):
        self.status = {"key": key, "args": args} if key else None
        self.error = error

    # ------------------------------------------------------------ кэш
    def clear_cache(self):
        if self.busy:
            return False
        sources.clear_cache()
        self.history.clear()
        self._set_status("cache_cleared")
        return True

    def remove_cached(self, video_id):
        if self.busy:
            return False  # вдруг этот файл сейчас скачивается
        sources.delete_cached(video_id)
        self.history.remove(video_id)
        return True

    def library(self):
        """Треки в кэше, свежие сверху. Подробности — из истории прослушивания."""
        meta = self.history.by_id()
        items = []
        for f in sources.cached_tracks():
            h = meta.get(f["id"], {})
            author, name = sources.split_title(**{**f, "title": f["title"] if f["title"] != "YouTube" else h.get("title", "YouTube")})
            items.append({
                "id": f["id"], "url": f["url"],
                "author": author, "name": name,
                "duration": h.get("duration"),
                "size_mb": round(f["size"] / 1024 / 1024, 1),
                "played_at": h.get("played_at") or f["mtime"],
            })
        items.sort(key=lambda i: i["played_at"], reverse=True)
        return items

    # ------------------------------------------------------------ эффекты
    def set_fx(self, name, key, value):
        self.engine.fx.set(name, key, value)

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
            "author": self.author,
            "status": self.status,
            "error": self.error,
            "busy": self.busy,
            "track_id": self.track_id,
            "cache_mb": round(sources.cache_size() / 1024 / 1024, 1),
            "current_id": self.current_id,
            "library_rev": self.history.rev,
            "fx": self.engine.fx.snapshot(),
        }

    def close(self):
        self.engine.close()
