"""tempo player — запуск: python main.py [файл]

Откроется в браузере. Выключается само, когда закрыта последняя вкладка.
"""
import argparse


def main():
    ap = argparse.ArgumentParser(description="tempo player — slowed / sped up в реальном времени")
    ap.add_argument("file", nargs="?", help="аудиофайл, который открыть сразу")
    ap.add_argument("--port", type=int, default=8765, help="порт (по умолчанию 8765)")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    args = ap.parse_args()

    from tempo_player import web
    web.run(args.file, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
