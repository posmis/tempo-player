"""Тёплая тёмная палитра. Ни одного синего пикселя."""
import customtkinter as ctk

BG = "#15110e"           # фон окна — почти чёрный, с коричневым подтоном
PANEL = "#201914"        # карточки
PANEL_2 = "#2a211b"      # поля, неактивные кнопки
BORDER = "#3b2f27"       # линии, дорожка слайдера
TEXT = "#f2e7d8"         # основной текст — тёплый кремовый
MUTED = "#9a887a"        # второстепенный текст
ACCENT = "#e8a25a"       # янтарь
ACCENT_HOVER = "#f3b877"
ACCENT_DIM = "#b8763c"   # заполненная часть слайдера
TERRACOTTA = "#d0694c"   # «медленнее»
GOLD = "#e9c46f"         # «быстрее»
WAVE_IDLE = "#4a3a2f"    # ещё не сыгранная часть волны
ERROR = "#e07a6a"

FONT = "Segoe UI"


def _is_bluish(color):
    if not (isinstance(color, str) and color.startswith("#") and len(color) == 7):
        return False
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return b > r + 15 and b >= g


def _warm(node):
    for key, val in node.items():
        if isinstance(val, dict):
            _warm(val)
        elif isinstance(val, list):
            node[key] = [ACCENT if _is_bluish(c) else c for c in val]
        elif _is_bluish(val):
            node[key] = ACCENT


def apply():
    """Тёмный режим + перекраска всех синих цветов стандартной темы в янтарь."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    _warm(ctk.ThemeManager.theme)
