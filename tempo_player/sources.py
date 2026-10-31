"""Откуда берётся звук: локальные файлы и ссылки на YouTube."""
import os
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


def download_youtube(url, progress=None):
    """Скачивает аудиодорожку в кэш. Возвращает (путь, название)."""
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
            return ydl.prepare_filename(info), info.get("title") or "YouTube"
    except yt_dlp.utils.DownloadError as e:
        # «ERROR: [youtube] abc: Video unavailable; please report…» -> «Video unavailable»
        msg = str(e).replace("ERROR: ", "").split("; please report")[0]
        raise RuntimeError(msg.split(": ", 1)[-1] if msg.startswith("[") else msg) from None
