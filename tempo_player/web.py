"""Веб-версия: локальный сервер + страница в браузере.

Звук играет Python (тот же движок), браузер — только пульт управления.
Сервер слушает только 127.0.0.1, снаружи к нему не подключиться.
"""
import json
import os
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from . import sources
from .controller import Controller

STATIC = os.path.join(os.path.dirname(__file__), "static")
UPLOAD_DIR = os.path.join(sources.CACHE_DIR, "uploads")
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 ГБ


class Presence:
    """Следит, открыта ли страница. Закрыли последнюю вкладку — выключаемся.

    Каждая вкладка шлёт свой X-Client с каждым запросом, а при закрытии —
    /api/bye. Обновление страницы (F5) успевает переподключиться за GRACE секунд.
    Если браузер упал и «пока» не прислал — выключаемся после STALE секунд тишины
    (свёрнутая вкладка может опрашивать сервер всего раз в минуту).
    """

    GRACE = 2.5
    STALE = 180

    def __init__(self, on_empty):
        self.on_empty = on_empty
        self.clients = {}
        self.seen_any = False
        self.empty_since = None
        self.lock = threading.Lock()
        threading.Thread(target=self._watch, daemon=True).start()

    def touch(self, cid):
        with self.lock:
            self.clients[cid] = time.monotonic()
            self.seen_any = True
            self.empty_since = None

    def bye(self, cid):
        with self.lock:
            self.clients.pop(cid, None)

    def _watch(self):
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            with self.lock:
                self.clients = {c: t for c, t in self.clients.items() if now - t < self.STALE}
                if not self.seen_any or self.clients:
                    self.empty_since = None
                    continue
                self.empty_since = self.empty_since or now
                if now - self.empty_since < self.GRACE:
                    continue
            self.on_empty()
            return


def make_handler(ctl, server_ref, presence):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # не засоряем консоль запросами

        # -------------------------------------------------- защита
        def _allowed(self):
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            # это свой компьютер (127.0.0.1), https тут не бывает
            # noinspection HttpUrlsUsage
            return origin is None or origin in {f"http://{h}" for h in hosts}

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        # -------------------------------------------------- GET
        def _touch(self):
            cid = self.headers.get("X-Client")
            if cid:
                presence.touch(cid[:64])

        def do_GET(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            self._touch()
            path = self.path.split("?")[0]
            if path == "/":
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, ctl.state())
            if path == "/api/spectrum":
                bands = ctl.engine.spectrum() if ctl.engine.playing else []
                return self._send(200, {"bands": [round(float(b), 3) for b in bands]})
            if path == "/api/library":
                return self._send(200, {"rev": ctl.history.rev, "items": ctl.library()})
            if path == "/api/peaks":
                peaks = ctl.engine.peaks
                data = [] if peaks is None else [round(float(p), 3) for p in peaks]
                return self._send(200, {"track_id": ctl.track_id, "peaks": data})
            self._send(404, {"error": "not found"})

        # -------------------------------------------------- POST
        def do_POST(self):
            if not self._allowed():
                return self._send(403, {"error": "forbidden"})
            self._touch()
            path = self.path.split("?")[0]

            if path == "/api/bye":  # вкладку закрыли (navigator.sendBeacon)
                n = int(self.headers.get("Content-Length") or 0)
                presence.bye(self.rfile.read(min(n, 64)).decode(errors="ignore"))
                return self._send(200, {"ok": True})

            if path == "/api/upload":
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_UPLOAD:
                    return self._send(413, {"error": "too large"})
                name = unquote(self.headers.get("X-Filename") or "track")
                name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(name))[:120] or "track"
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                dst = os.path.join(UPLOAD_DIR, name)
                with open(dst, "wb") as f:
                    left = n
                    while left:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                ok = ctl.load_path(dst, delete_after=True)
                return self._send(200, {"ok": ok})

            body = self._json_body()
            if path == "/api/url":
                return self._send(200, {"ok": ctl.load_url(body.get("url", ""))})
            if path != "/api/control":
                return self._send(404, {"error": "not found"})

            action, value = body.get("action"), body.get("value")
            try:
                if action == "toggle":
                    ctl.toggle()
                elif action == "seek":
                    ctl.seek(value)
                elif action == "mode":
                    ctl.set_mode(value)
                elif action == "speed":
                    ctl.set_speed(value)
                elif action == "semi":
                    ctl.set_semi(value)
                elif action == "volume":
                    ctl.set_volume(value)
                elif action == "fx":
                    ctl.set_fx(value["name"], value["key"], value["val"])
                elif action == "cache_remove":
                    ctl.remove_cached(str(value))
                elif action == "clear_cache":
                    ctl.clear_cache()
                elif action == "quit":
                    self._send(200, {"ok": True})
                    threading.Thread(target=server_ref[0].shutdown, daemon=True).start()
                    return None
                else:
                    return self._send(400, {"error": "unknown action"})
            except (TypeError, ValueError, KeyError):
                return self._send(400, {"error": "bad value"})
            self._send(200, ctl.state())

    return Handler


def run(path=None, port=8765, open_browser=True):
    ctl = Controller()
    server_ref = [None]

    def on_empty():
        print("Страница закрыта — выключаюсь")
        server_ref[0].shutdown()

    handler = make_handler(ctl, server_ref, Presence(on_empty))

    server = None
    for p in range(port, port + 20):  # если порт занят — берём следующий
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"Не удалось занять порт {port}–{port + 19}")
    server.daemon_threads = True
    server_ref[0] = server

    # noinspection HttpUrlsUsage
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"tempo player: {url}")
    print("Остановить: закройте вкладку, кнопка «Выключить» или Stop в PyCharm")
    if path:
        ctl.load_path(path)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.close()
        server.server_close()
        print("tempo player остановлен")
