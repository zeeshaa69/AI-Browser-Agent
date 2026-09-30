"""Local demo website used for development, tests and the README walkthrough."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - quiet by default
        pass


class DemoServer:
    """Serves the static demo site on 127.0.0.1 in a background thread."""

    def __init__(self, port: int = 0, host: str = "127.0.0.1") -> None:
        handler = functools.partial(_Handler, directory=str(STATIC_DIR))
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> "DemoServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="demo-site", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "DemoServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
