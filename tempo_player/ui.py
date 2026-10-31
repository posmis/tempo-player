"""Окно приложения."""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from . import sources
from . import theme as T
from .engine import Engine

F = T.FONT

SPEED_MIN, SPEED_MAX = 0.5, 2.0
SEMI_MIN, SEMI_MAX = -12, 12
SPEED_PRESETS = [0.70, 0.80, 0.90, 1.00, 1.15, 1.25, 1.35]
SEMI_PRESETS = [-5, -3, -2, 0, 2, 3, 5]
MODES = [("vinyl", "Пластинка"), ("tempo", "Только темп"), ("pitch", "Только тон")]


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def semitones_word(n):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "полутон"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "полутона"
    return "полутонов"


class Waveform(tk.Canvas):
    """Волна трека. Клик или перетаскивание — перемотка."""

    BAR, GAP = 3, 2

    def __init__(self, master, on_seek, height=88):
        super().__init__(master, height=height, bg=T.PANEL, highlightthickness=0, cursor="hand2")
        self.on_seek = on_seek
        self.peaks = None
        self.bars = []
        self.progress = 0.0
        self.drag = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def set_peaks(self, peaks):
        self.peaks = peaks
        self.progress = 0.0
        self.redraw()

    def redraw(self):
        self.delete("all")
        self.bars = []
        w, h = self.winfo_width(), self.winfo_height()
        cy = h / 2
        step = self.BAR + self.GAP
        count = max(1, (w - 4) // step)
        if self.peaks is None:
            self.create_line(4, cy, w - 4, cy, fill=T.WAVE_IDLE, width=2)
            self.create_text(w / 2, cy - 18, text="Откройте файл или вставьте ссылку на YouTube",
                             fill=T.MUTED, font=(F, 11))
            return
        n = len(self.peaks)
        for i in range(count):
            a, b = i * n // count, max(i * n // count + 1, (i + 1) * n // count)
            amp = float(self.peaks[a:b].max()) ** 0.8
            bh = max(1.5, amp * (cy - 6))
            x = 4 + i * step + self.BAR / 2
            self.bars.append(self.create_line(x, cy - bh, x, cy + bh, width=self.BAR,
                                              fill=T.WAVE_IDLE, capstyle=tk.ROUND))
        self._paint(self.progress, force=True)

    def _paint(self, frac, force=False):
        if not self.bars:
            return
        old = int(self.progress * len(self.bars))
        new = int(frac * len(self.bars))
        self.progress = frac
        lo, hi = (0, len(self.bars)) if force else (min(old, new), max(old, new) + 1)
        for i in range(lo, min(hi, len(self.bars))):
            self.itemconfigure(self.bars[i], fill=T.ACCENT if i < new else T.WAVE_IDLE)

    def set_progress(self, frac):
        if self.drag is None:
            self._paint(frac)

    def _frac(self, x):
        return min(1.0, max(0.0, (x - 4) / max(1, self.winfo_width() - 8)))

    def _press(self, e):
        if self.bars:
            self.drag = self._frac(e.x)
            self._paint(self.drag)

    def _release(self, e):
        if self.drag is not None:
            self.on_seek(self._frac(e.x))
            self.drag = None


class PlayButton(ctk.CTkFrame):
    """Круглая кнопка play/pause (CTkButton с текстом не бывает идеально круглым)."""

    def __init__(self, master, command, size=56):
        super().__init__(master, width=size, height=size, corner_radius=size // 2,
                         fg_color=T.ACCENT, cursor="hand2")
        self.command = command
        self.playing = None
        self.icon = ctk.CTkLabel(self, text="", text_color=T.BG, fg_color="transparent",
                                 width=1, height=1, cursor="hand2")
        self.icon.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self, self.icon):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self.configure(fg_color=T.ACCENT_HOVER))
            w.bind("<Leave>", lambda e: self.configure(fg_color=T.ACCENT))
        self.set_playing(False)

    def set_playing(self, playing):
        if playing != self.playing:
            self.playing = playing
            self.icon.configure(text="❚❚" if playing else "▶", font=(F, 16 if playing else 20))
            self.icon.place_configure(relx=0.5 if playing else 0.54)


class App(ctk.CTk):
    def __init__(self, path=None):
        T.apply()
        super().__init__(fg_color=T.BG)
        self.title("tempo player")
        self.geometry("800x740")
        self.minsize(660, 720)
        self._set_icon()

        self.engine = Engine()
        self.events = queue.Queue()
        self.busy = False
        self.mode = "vinyl"
        self.speed = 1.0
        self.semi = 0

        self._build()
        self._bind_keys()
        self._set_mode("vinyl")
        if path:
            self.after(100, lambda: self.load_path(path))
        self._tick()

    # ------------------------------------------------------------------ UI
    def _card(self, **pack):
        card = ctk.CTkFrame(self, fg_color=T.PANEL, corner_radius=18)
        card.pack(fill="x", padx=24, **pack)
        return card

    def _build(self):
        # --- шапка
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(20, 12))
        ctk.CTkLabel(head, text="tempo", font=(F, 26, "bold"), text_color=T.TEXT).pack(side="left")
        ctk.CTkLabel(head, text="player", font=(F, 26, "bold"), text_color=T.ACCENT).pack(side="left", padx=(2, 0))
        ctk.CTkButton(head, text="Открыть файл", command=self.open_file, width=130, height=36,
                      corner_radius=12, font=(F, 13), fg_color=T.PANEL_2, hover_color=T.BORDER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER).pack(side="right")

        # --- ссылка на YouTube
        src = self._card(pady=(0, 12))
        self.url = ctk.CTkEntry(src, placeholder_text="Вставьте ссылку на YouTube и нажмите Enter",
                                height=40, corner_radius=12, font=(F, 13), fg_color=T.PANEL_2,
                                border_color=T.BORDER, border_width=1, text_color=T.TEXT,
                                placeholder_text_color=T.MUTED)
        self.url.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=14)
        self.url.bind("<Return>", lambda e: self.load_url())
        self.url_btn = ctk.CTkButton(src, text="Слушать", command=self.load_url, width=110, height=40,
                                     corner_radius=12, font=(F, 13, "bold"), fg_color=T.ACCENT,
                                     hover_color=T.ACCENT_HOVER, text_color=T.BG)
        self.url_btn.pack(side="right", padx=(0, 14), pady=14)

        # --- плеер
        player = self._card(pady=(0, 12))
        top = ctk.CTkFrame(player, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(16, 6))
        self.play_btn = PlayButton(top, command=self.toggle)
        self.play_btn.pack(side="left")
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=14)
        self.track_lbl = ctk.CTkLabel(info, text="Ничего не играет", font=(F, 16, "bold"),
                                      text_color=T.TEXT, anchor="w")
        self.track_lbl.pack(fill="x")
        self.status_lbl = ctk.CTkLabel(info, text="Пробел — пауза  ·  ← → — скорость",
                                       font=(F, 12), text_color=T.MUTED, anchor="w")
        self.status_lbl.pack(fill="x")

        self.wave = Waveform(player, on_seek=self.seek_frac)
        self.wave.pack(fill="x", padx=18, pady=(6, 0))
        times = ctk.CTkFrame(player, fg_color="transparent")
        times.pack(fill="x", padx=20, pady=(2, 12))
        self.pos_lbl = ctk.CTkLabel(times, text="0:00", font=(F, 12), text_color=T.MUTED)
        self.pos_lbl.pack(side="left")
        self.dur_lbl = ctk.CTkLabel(times, text="0:00", font=(F, 12), text_color=T.MUTED)
        self.dur_lbl.pack(side="right")

        # --- скорость
        sp = self._card(pady=(0, 12))
        seg = ctk.CTkFrame(sp, fg_color=T.PANEL_2, corner_radius=14)
        seg.pack(pady=(18, 4))
        self.mode_btns = {}
        for key, label in MODES:
            b = ctk.CTkButton(seg, text=label, width=140, height=34, corner_radius=11, font=(F, 13),
                              command=lambda k=key: self._set_mode(k))
            b.pack(side="left", padx=3, pady=3)
            self.mode_btns[key] = b

        self.value_lbl = ctk.CTkLabel(sp, text="1.00×", font=(F, 50, "bold"), text_color=T.ACCENT)
        self.value_lbl.pack(pady=(6, 0))
        self.caption_lbl = ctk.CTkLabel(sp, text="", font=(F, 13), text_color=T.MUTED)
        self.caption_lbl.pack()

        self.slider = ctk.CTkSlider(sp, height=20, command=self._on_slider, fg_color=T.BORDER,
                                    progress_color=T.ACCENT_DIM, button_color=T.ACCENT,
                                    button_hover_color=T.ACCENT_HOVER, button_length=6)
        self.slider.pack(fill="x", padx=26, pady=(12, 0))
        rng = ctk.CTkFrame(sp, fg_color="transparent")
        rng.pack(fill="x", padx=28)
        self.min_lbl = ctk.CTkLabel(rng, text="", font=(F, 11), text_color=T.MUTED)
        self.min_lbl.pack(side="left")
        self.max_lbl = ctk.CTkLabel(rng, text="", font=(F, 11), text_color=T.MUTED)
        self.max_lbl.pack(side="right")

        self.presets_row = ctk.CTkFrame(sp, fg_color="transparent")
        self.presets_row.pack(pady=(6, 18))
        self.preset_btns = []

        # --- низ: громкость
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=28, pady=(0, 16))
        ctk.CTkLabel(foot, text="1 2 3 — режим  ·  0 — сброс  ·  ↑ ↓ — громкость",
                     font=(F, 11), text_color=T.MUTED).pack(side="left")
        self.vol = ctk.CTkSlider(foot, from_=0, to=1.2, width=160, height=18, command=self._on_volume,
                                 fg_color=T.BORDER, progress_color=T.ACCENT_DIM,
                                 button_color=T.ACCENT, button_hover_color=T.ACCENT_HOVER)
        self.vol.set(self.engine.volume)
        self.vol.pack(side="right")
        ctk.CTkLabel(foot, text="Громкость", font=(F, 12), text_color=T.MUTED).pack(side="right", padx=8)

    def _set_icon(self):
        """Янтарный кружок вместо стандартной (синей) иконки CustomTkinter."""
        s = 32
        img = tk.PhotoImage(width=s, height=s)
        rows = []
        for y in range(s):
            row = []
            for x in range(s):
                d = ((x - 15.5) ** 2 + (y - 15.5) ** 2) ** 0.5
                inside_tri = 11 <= x <= 22 and abs(y - 15.5) <= (22 - x) * 0.6
                row.append(T.BG if d > 15.5 else (T.BG if inside_tri else T.ACCENT))
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        self._icon = img
        self.iconphoto(True, img)
        self._iconbitmap_method_called = True  # чтобы CTk не поставил свою иконку

    # ------------------------------------------------------------ скорость
    def _set_mode(self, mode):
        self.mode = mode
        for key, b in self.mode_btns.items():
            on = key == mode
            b.configure(fg_color=T.ACCENT if on else T.PANEL_2,
                        hover_color=T.ACCENT_HOVER if on else T.BORDER,
                        text_color=T.BG if on else T.TEXT)
        if mode == "pitch":
            self.slider.configure(from_=SEMI_MIN, to=SEMI_MAX, number_of_steps=SEMI_MAX - SEMI_MIN)
            self.slider.set(self.semi)
            self.min_lbl.configure(text=f"{SEMI_MIN}")
            self.max_lbl.configure(text=f"+{SEMI_MAX}")
            values = SEMI_PRESETS
        else:
            self.slider.configure(from_=SPEED_MIN, to=SPEED_MAX,
                                  number_of_steps=round((SPEED_MAX - SPEED_MIN) / 0.01))
            self.slider.set(self.speed)
            self.min_lbl.configure(text=f"{SPEED_MIN:.1f}×")
            self.max_lbl.configure(text=f"{SPEED_MAX:.1f}×")
            values = SPEED_PRESETS

        for b in self.preset_btns:
            b.destroy()
        self.preset_btns = []
        for v in values:
            text = (f"{v:+d}" if v else "0") if mode == "pitch" else f"{v:.2f}×"
            b = ctk.CTkButton(self.presets_row, text=text, width=64, height=30, corner_radius=15,
                              font=(F, 12), border_width=1, command=lambda x=v: self.set_value(x))
            b.value = v
            b.pack(side="left", padx=3)
            self.preset_btns.append(b)
        self._apply()

    def set_value(self, v):
        if self.mode == "pitch":
            self.semi = int(round(min(SEMI_MAX, max(SEMI_MIN, v))))
            self.slider.set(self.semi)
        else:
            self.speed = round(min(SPEED_MAX, max(SPEED_MIN, v)), 2)
            self.slider.set(self.speed)
        self._apply()

    def _on_slider(self, v):
        if self.mode == "pitch":
            self.semi = int(round(v))
        else:
            self.speed = round(float(v), 2)
        self._apply()

    def _apply(self):
        if self.mode == "vinyl":
            tempo, pitch = self.speed, self.speed
        elif self.mode == "tempo":
            tempo, pitch = self.speed, 1.0
        else:
            tempo, pitch = 1.0, 2 ** (self.semi / 12)
        self.engine.set_params(tempo, pitch)

        if self.mode == "pitch":
            v = self.semi
            self.value_lbl.configure(text=f"{v:+d}" if v else "0")
            if v == 0:
                cap = "оригинальный тон"
            else:
                cap = f"{semitones_word(v)} {'выше' if v > 0 else 'ниже'}  ·  темп тот же"
            current, color = v, (T.GOLD if v > 0 else T.TERRACOTTA if v < 0 else T.ACCENT)
        else:
            v = self.speed
            self.value_lbl.configure(text=f"{v:.2f}×")
            faster = "быстрее" if v > 1 else "медленнее"
            if abs(v - 1) < 1e-9:
                cap = "оригинальная скорость"
            elif self.mode == "vinyl":
                cap = f"{faster}  ·  голос {'выше' if v > 1 else 'ниже'}"
            else:
                cap = f"{faster}  ·  тон тот же"
            current, color = v, (T.GOLD if v > 1 else T.TERRACOTTA if v < 1 else T.ACCENT)
        self.value_lbl.configure(text_color=color)
        self.caption_lbl.configure(text=cap)

        for b in self.preset_btns:
            on = abs(b.value - current) < 1e-6
            b.configure(fg_color=T.ACCENT if on else T.PANEL_2,
                        hover_color=T.ACCENT_HOVER if on else T.BORDER,
                        border_color=T.ACCENT if on else T.BORDER,
                        text_color=T.BG if on else T.TEXT)

    def _nudge(self, direction):
        if self.mode == "pitch":
            self.set_value(self.semi + direction)
        else:
            self.set_value(self.speed + 0.05 * direction)

    # ------------------------------------------------------------ клавиши
    def _bind_keys(self):
        def guard(fn):
            def handler(e):
                if isinstance(self.focus_get(), tk.Entry):
                    return None  # печатаем ссылку — горячие клавиши не трогаем
                fn()
                return "break"
            return handler

        self.bind("<space>", guard(self.toggle))
        self.bind("<Left>", guard(lambda: self._nudge(-1)))
        self.bind("<Right>", guard(lambda: self._nudge(+1)))
        self.bind("<Up>", guard(lambda: self._set_volume(self.engine.volume + 0.05)))
        self.bind("<Down>", guard(lambda: self._set_volume(self.engine.volume - 0.05)))
        self.bind("<Key-0>", guard(lambda: self.set_value(0 if self.mode == "pitch" else 1.0)))
        for i, (key, _) in enumerate(MODES, start=1):
            self.bind(f"<Key-{i}>", guard(lambda k=key: self._set_mode(k)))
        # клик мимо поля ввода снимает с него фокус
        self.bind_all("<Button-1>", lambda e: None if isinstance(e.widget, tk.Entry) else self.focus_set(), add="+")

    def _on_volume(self, v):
        self.engine.volume = float(v)

    def _set_volume(self, v):
        v = min(1.2, max(0.0, v))
        self.vol.set(v)
        self.engine.volume = v

    # ------------------------------------------------------------ загрузка
    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[
            ("Аудио", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.opus *.webm *.aiff"),
            ("Все файлы", "*.*")])
        if path:
            self.load_path(path)

    def load_path(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        self._start_job(lambda: (sources.decode(path), name), "Загружаю…")

    def load_url(self):
        url = self.url.get().strip()
        if not url:
            return
        if not sources.looks_like_url(url):
            self._status("Это не похоже на ссылку", error=True)
            return

        def job():
            def progress(frac):
                self.events.put(("status", f"Скачиваю с YouTube…  {int(frac * 100)}%"))
            path, title = sources.download_youtube(url, progress)
            self.events.put(("status", "Подготавливаю звук…"))
            return sources.decode(path), title

        self.focus_set()
        self._start_job(job, "Ищу видео…")

    def _start_job(self, job, status):
        if self.busy:
            return
        self.busy = True
        self.url_btn.configure(state="disabled")
        self._status(status)

        def run():
            try:
                (data, sr), title = job()
                self.events.put(("loaded", data, sr, title))
            except Exception as e:  # noqa: BLE001 — показываем любую ошибку в окне
                self.events.put(("error", str(e) or e.__class__.__name__))

        threading.Thread(target=run, daemon=True).start()

    def _handle(self, ev):
        kind = ev[0]
        if kind == "status":
            self._status(ev[1])
        elif kind == "error":
            self.busy = False
            self.url_btn.configure(state="normal")
            self._status(f"Ошибка: {ev[1][:120]}", error=True)
        elif kind == "loaded":
            self.busy = False
            self.url_btn.configure(state="normal")
            _, data, sr, title = ev
            try:
                self.engine.load(data, sr)
            except Exception as e:  # noqa: BLE001
                self._status(f"Не удалось открыть звуковое устройство: {e}", error=True)
                return
            self.track_lbl.configure(text=title[:70])
            self.dur_lbl.configure(text=fmt_time(self.engine.duration))
            self.wave.set_peaks(self.engine.peaks)
            self._status("Пробел — пауза  ·  ← → — скорость")
            self.engine.playing = True

    def _status(self, text, error=False):
        self.status_lbl.configure(text=text, text_color=T.ERROR if error else T.MUTED)

    # ------------------------------------------------------------ плеер
    def toggle(self):
        if self.engine.loaded:
            self.engine.playing = not self.engine.playing

    def seek_frac(self, frac):
        if self.engine.loaded:
            self.engine.seek(frac * self.engine.duration)

    def _tick(self):
        while not self.events.empty():
            self._handle(self.events.get_nowait())
        e = self.engine
        if e.loaded:
            self.wave.set_progress(e.position / max(e.duration, 1e-9))
            self.pos_lbl.configure(text=fmt_time(e.position))
        self.play_btn.set_playing(e.playing)
        self.after(40, self._tick)

    def destroy(self):
        self.engine.close()
        super().destroy()
