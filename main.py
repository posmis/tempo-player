"""tempo player

    python main.py              — веб-версия, откроется в браузере (по умолчанию)
    python main.py --desktop    — отдельное окно (CustomTkinter)
    python main.py song.mp3     — сразу открыть файл (работает в обоих режимах)
"""
import argparse


def main():
    ap = argparse.ArgumentParser(description="tempo player — slowed / sped up в реальном времени")
    ap.add_argument("file", nargs="?", help="аудиофайл, который открыть сразу")
    ap.add_argument("--desktop", action="store_true", help="открыть отдельное окно вместо браузера")
    ap.add_argument("--port", type=int, default=8765, help="порт веб-версии (по умолчанию 8765)")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    args = ap.parse_args()

    if args.desktop:
        from tempo_player.ui import App
        App(args.file).mainloop()
    else:
        from tempo_player import web
        web.run(args.file, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
