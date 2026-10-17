"""A deliberately tiny HTTP service used by the Saarathi example. Standard library only."""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

Handler = Callable[[], tuple[int, dict[str, object]]]


def version() -> tuple[int, dict[str, object]]:
    return 200, {"version": "0.1.0"}


ROUTES: dict[str, Handler] = {"/version": version}


def dispatch(path: str) -> tuple[int, dict[str, object]]:
    handler = ROUTES.get(path)
    if handler is None:
        return 404, {"error": "not found"}
    return handler()


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        status, body = dispatch(self.path)
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), RequestHandler).serve_forever()
