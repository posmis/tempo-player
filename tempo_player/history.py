"""История прослушанных видео с YouTube.

Хранится отдельно от кэша (очистка кэша историю не трогает):
Windows: %APPDATA%\\tempo-player\\history.json
"""
import json
import os
import threading
import time

from . import sources

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), "tempo-player")
FILE = os.path.join(DATA_DIR, "history.json")
MAX_ITEMS = 100


class History:
    def __init__(self, path=FILE):
        self.path = path
        self._lock = threading.Lock()
        self.rev = 0  # растёт при каждом изменении — страница понимает, что пора перерисовать
        try:
            with open(path, encoding="utf-8") as f:
                self._items = [i for i in json.load(f) if isinstance(i, dict) and i.get("id")]
        except (OSError, ValueError):
            self._items = []

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self.rev += 1

    def add(self, video_id, title, duration):
        with self._lock:
            self._items = [i for i in self._items if i["id"] != video_id]
            self._items.insert(0, {
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "duration": round(duration, 1),
                "played_at": time.time(),
            })
            del self._items[MAX_ITEMS:]
            self._save()

    def remove(self, video_id):
        with self._lock:
            self._items = [i for i in self._items if i["id"] != video_id]
            self._save()

    def clear(self):
        with self._lock:
            self._items = []
            self._save()

    def items(self):
        with self._lock:
            items = [dict(i) for i in self._items]
        for i in items:
            i["cached"] = sources.cached(i["url"]) is not None
        return items

    def touch(self):
        """Кэш изменился — пометки «в кэше» надо обновить."""
        self.rev += 1
