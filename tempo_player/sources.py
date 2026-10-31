"""Откуда берётся звук: локальные файлы и ссылки на YouTube."""
import glob
import json
import os
import re
import shutil
import subprocess
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
    except (RuntimeError, OSError, ValueError, TypeError):
        pass  # soundfile не знает этот формат
    # m4a / webm / opus и всё прочее — через ffmpeg
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", path, "-f", "f32le", "-ac", "2",
         "-ar", str(FFMPEG_SR), "-"],
        capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode(errors="ignore").strip().splitlines()
        raise RuntimeError(err[-1] if err else "не удалось прочитать файл")
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).copy(), FFMPEG_SR


# noinspection HttpUrlsUsage,SpellCheckingInspection
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


def meta(vid):
    """Сведения о скачанном видео: {title, channel, artist, track} (что известно)."""
    m = _titles().get(vid)
    if isinstance(m, str):  # старый формат — только название
        return {"title": m}
    return m or {}


def _remember_title(vid, title, **extra):
    titles = _titles()
    titles[vid] = {"title": title, **{k: v for k, v in extra.items() if v}}
    with open(_TITLES, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False)


def youtube_id(url):
    m = _ID_RE.search(url or "")
    return m.group(1) if m else None


def cached(url):
    """Если трек уже скачан — (путь, название), иначе None. Без обращения к сети."""
    vid = youtube_id(url)
    if not vid:
        return None
    for path in glob.glob(os.path.join(glob.escape(CACHE_DIR), vid + ".*")):
        if not path.endswith((".part", ".ytdl", ".json")):
            return path, meta(vid).get("title", "YouTube")
    return None


# noinspection SpellCheckingInspection
def cached_tracks():
    """Все треки, которые уже лежат в кэше: [{id, url, title, size, mtime}]."""
    out = []
    for path in glob.glob(os.path.join(glob.escape(CACHE_DIR), "*")):
        if not os.path.isfile(path):
            continue
        vid, ext = os.path.splitext(os.path.basename(path))
        # недокачанные (id.webm.part), служебные .json и чужие файлы пропускаем
        if ext in (".part", ".ytdl", ".json", ".tmp"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        st = os.stat(path)
        out.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                    "size": st.st_size, "mtime": st.st_mtime, **{"title": "YouTube", **meta(vid)}})
    return out


def delete_cached(vid):
    for path in glob.glob(os.path.join(glob.escape(CACHE_DIR), glob.escape(vid) + ".*")):
        if not path.endswith(".json"):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------- исполнитель / название
_DASH = re.compile(r"\s+[-–—]\s+")
_JUNK = re.compile(
    r"\s*[(\[](?:official|оф\w*|lyrics?|lyric video|текст|audio|video|music video|mv|hd|hq|4k|"
    r"visuali[sz]er|clip|клип|премьера|premiere)[^)\]]*[)\]]", re.I)


def split_title(title, channel=None, artist=None, track=None, **_):
    """(исполнитель, название). У YouTube исполнителя отдельно обычно нет — угадываем:
    1) поля artist/track (официальная музыка), 2) «Исполнитель - Песня», 3) имя канала."""
    if artist and track:
        return artist, track
    clean = _JUNK.sub("", title or "").strip() or (title or "")
    parts = _DASH.split(clean, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    ch = re.sub(r"(\s*-\s*Topic|VEVO)$", "", channel or "").strip()
    return ch, clean


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
# noinspection SpellCheckingInspection
def download_youtube(url, progress=None):
    """Скачивает аудиодорожку в кэш (или берёт из кэша). Возвращает (путь, название)."""
    hit = cached(url)
    if hit:
        return hit

    import yt_dlp
    from yt_dlp.utils import DownloadError

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
        # noinspection PyTypeChecker
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = dict(ydl.extract_info(url.strip(), download=True))
            if info.get("entries"):  # ссылка на плейлист — берём первое видео
                info = dict(info["entries"][0])
            title = info.get("title") or "YouTube"
            artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
            _remember_title(info["id"], title, channel=info.get("channel") or info.get("uploader"),
                            artist=", ".join(artists), track=info.get("track"))
            return ydl.prepare_filename(info), title
    except DownloadError as e:
        # «ERROR: [сайт] id: Video unavailable; please report…» -> «Video unavailable»
        msg = str(e).replace("ERROR: ", "").split("; please report")[0]
        raise RuntimeError(msg.split(": ", 1)[-1] if msg.startswith("[") else msg) from None
