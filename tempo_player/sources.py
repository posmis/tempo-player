"""Откуда берётся звук: локальные файлы и ссылки на YouTube."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf

CACHE_DIR = os.path.join(tempfile.gettempdir(), "tempo-player")
FFMPEG_SR = 44100
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # не мигать консолью на Windows


def ffmpeg_exe():
    import imageio_ffmpeg  # ставится через pip вместе с готовым ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode(path):
    """Читает аудиофайл -> (float32 массив [кадры, каналы], частота)."""
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        if len(data):
            return data, sr
    except Exception:
        pass
    # m4a / webm / opus и всё прочее — через ffmpeg
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", path, "-f", "f32le", "-ac", "2",
         "-ar", str(FFMPEG_SR), "-"],
        capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode(errors="ignore").strip().splitlines()
        raise RuntimeError(err[-1] if err else "не удалось прочитать файл")
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).copy(), FFMPEG_SR


def looks_like_url(text):
    t = text.strip().lower()
    return t.startswith(("http://", "https://", "www.", "youtu", "m.youtube", "music.youtube"))


# ---------------------------------------------------------------- кэш
_TITLES = os.path.join(CACHE_DIR, "titles.json")
_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def _titles():
    try:
        with open(_TITLES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _remember_title(video_id, title):
    titles = _titles()
    titles[video_id] = title
    with open(_TITLES, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False)


def cached(url):
    """Если трек уже скачан — (путь, название), иначе None. Без обращения к сети."""
    m = _ID_RE.search(url)
    if not m:
        return None
    vid = m.group(1)
    for path in glob.glob(os.path.join(glob.escape(CACHE_DIR), vid + ".*")):
        if not path.endswith((".part", ".ytdl", ".json")):
            return path, _titles().get(vid, "YouTube")
    return None


def cache_size():
    """Размер кэша в байтах."""
    total = 0
    for root, _, files in os.walk(CACHE_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def clear_cache():
    shutil.rmtree(CACHE_DIR, ignore_errors=True)


# ---------------------------------------------------------------- загрузка
def download_youtube(url, progress=None):
    """Скачивает аудиодорожку в кэш (или берёт из кэша). Возвращает (путь, название)."""
    hit = cached(url)
    if hit:
        return hit

    import yt_dlp

    os.makedirs(CACHE_DIR, exist_ok=True)

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(CACHE_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "ffmpeg_location": ffmpeg_exe(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
            if "entries" in info:
                info = info["entries"][0]
            title = info.get("title") or "YouTube"
            _remember_title(info["id"], title)
            return ydl.prepare_filename(info), title
    except yt_dlp.utils.DownloadError as e:
        # «ERROR: [youtube] abc: Video unavailable; please report…» -> «Video unavailable»
        msg = str(e).replace("ERROR: ", "").split("; please report")[0]
        raise RuntimeError(msg.split(": ", 1)[-1] if msg.startswith("[") else msg) from None
