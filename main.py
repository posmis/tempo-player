"""tempo player — запуск: python main.py [файл]"""
import sys

from tempo_player.ui import App

if __name__ == "__main__":
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
