import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cs2trader.config import Settings, load_settings
from cs2trader.errors import ConfigError
from cs2trader.ui import api, settings_api
from cs2trader.ui.jobs import JobRunner
from cs2trader.ui.security import (
    BIND_HOST,
    TOKEN_HEADER,
    host_allowed,
    new_token,
    scrub,
    secret_values,
    token_ok,
)

STATIC = Path(__file__).parent / "static"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024
REPORT_PATH = re.compile(r"^/reports/(\d{4}-\d{2}-\d{2})/report\.html$")


class App:
    def __init__(self, settings: Settings | None = None, config_path: Path = Path("cs2.toml"),
                 env_path: Path = Path(".env"), port: int = 0) -> None:
        self.config_path = config_path
        self.env_path = env_path
        self.ctx = api.Ctx(settings or load_settings(config_path), config_path)
        self.token = new_token()
        self.jobs = JobRunner(self.ctx.data_dir)
        self.port = port

    def reload_settings(self) -> None:
        self.ctx = api.Ctx(load_settings(self.config_path), self.config_path)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cs2ui"

        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            # Whatever a payload contains, a secret value never leaves this process.
            body = scrub(body, secret_values(app.env_path))
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, default=str).encode(), "application/json")

        def _guard(self) -> bool:
            if not host_allowed(self.headers.get("Host"), app.port):
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._guard():
                return
            url = urlparse(self.path)
            path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                self._route_get(path, q)
            except Exception as e:  # noqa: BLE001 - a failing page must not kill the server
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})

        def _route_get(self, path: str, q: dict) -> None:
            ctx = app.ctx
            if path == "/":
                page = (STATIC / "index.html").read_text().replace("__TOKEN__", app.token)
                return self._send(200, page.encode(), STATIC_TYPES[".html"])
            if path.startswith("/static/"):
                f = STATIC / path.removeprefix("/static/")
                if f.parent != STATIC or f.suffix not in STATIC_TYPES or not f.is_file():
                    return self._json(404, {"error": "not found"})
                return self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
            if (m := REPORT_PATH.match(path)):
                f = ctx.reports_dir / m.group(1) / "report.html"
                if not f.is_file():
                    return self._json(404, {"error": "not found"})
                return self._send(200, f.read_bytes(), STATIC_TYPES[".html"])
            routes = {
                "/api/overview": lambda: api.overview(ctx),
                "/api/verdict": lambda: api.verdict(ctx, q.get("day")),
                "/api/runs": lambda: api.runs(ctx),
                "/api/market/index": lambda: api.market_index_series(ctx),
                "/api/market/categories": lambda: api.market_categories(ctx, int(q.get("days", 730))),
                "/api/market/search": lambda: api.market_item_search(ctx, q.get("q", "")),
                "/api/market/item": lambda: api.market_item(ctx, q.get("name", "")),
                "/api/collector": lambda: api.collector(ctx, int(q.get("hours", 48))),
                "/api/strategies": lambda: {**api.strategies(),
                                            "defaults": settings_api.load_strategy_defaults(ctx.data_dir)},
                "/api/settings": lambda: settings_api.describe(ctx.settings),
                "/api/jobs": lambda: app.jobs.snapshot(),
            }
            if path in routes:
                return self._json(200, routes[path]())
            if path.startswith("/api/run/"):
                status, payload = api.run_detail(ctx, path.removeprefix("/api/run/"))
                return self._json(status, payload)
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._guard():
                return
            if not token_ok(self.headers.get(TOKEN_HEADER), app.token):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "missing or wrong token"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
            except ValueError as e:
                return self._json(400, {"error": f"bad JSON: {e}"})
            path = urlparse(self.path).path
            try:
                if path == "/api/settings":
                    ok, errors, _ = settings_api.save(app.config_path, app.ctx.settings, body.get("changes", {}))
                    if ok:
                        app.reload_settings()
                    return self._json(200 if ok else 422, {"ok": ok, "errors": errors,
                                                           "note": settings_api.settings_changed_note() if ok else None})
                if path == "/api/strategy-defaults":
                    errors = settings_api.save_strategy_defaults(
                        app.ctx.data_dir, str(body.get("strategy", "")), body.get("params") or {})
                    return self._json(200 if not errors else 422, {"ok": not errors, "errors": errors})
                if path == "/api/jobs":
                    status, payload = app.jobs.start_backtest(body)
                    return self._json(status, payload)
            except ConfigError as e:
                return self._json(422, {"ok": False, "errors": [str(e)]})
            except Exception as e:  # noqa: BLE001 - report it to the client, keep serving
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            self._json(404, {"error": "not found"})

    return Handler


def create_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((BIND_HOST, port), make_handler(app))
    app.port = server.server_address[1]
    return server


def serve(port: int = 8765) -> None:
    app = App()
    server = create_server(app, port)
    print(f"cs2 ui: http://127.0.0.1:{app.port}/   (Ctrl-C to stop; localhost only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_in_thread(app: App) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = create_server(app)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t
