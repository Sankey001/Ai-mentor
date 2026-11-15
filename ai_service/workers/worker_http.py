"""Small JSON-over-HTTP server shared by the voice and face workers.

Standard library only, so it runs inside each model's own Python environment.
The server handles one request at a time, which keeps GPU work in order.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

MAX_BODY_BYTES = 64 * 1024

Route = Callable[[dict], dict]


def check_path(path: object, roots: list[str], must_exist: bool = True) -> str:
    """Return the real path if it lies inside one of ``roots``; raise ValueError otherwise."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    real = os.path.realpath(path)
    for root in roots:
        real_root = os.path.realpath(root)
        if os.path.commonpath([real_root, real]) == real_root:
            if must_exist and not os.path.isfile(real):
                raise ValueError(f"file not found: {os.path.basename(real)}")
            return real
    raise ValueError("path is outside the allowed folders")


def split_text(text: str, max_chars: int = 280) -> list[str]:
    """Split text into chunks of whole sentences no longer than ``max_chars``."""
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?।])\s+", " ".join(text.split())):
        words = sentence.split()
        while words:
            piece = words.pop(0)
            while words and len(piece) + 1 + len(words[0]) <= max_chars:
                piece += " " + words.pop(0)
            if current and len(current) + 1 + len(piece) <= max_chars:
                current += " " + piece
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def make_server(port: int, routes: dict[str, Route], name: str) -> HTTPServer:
    """Create a server on 127.0.0.1 that answers GET /health and POST routes."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path == "/health":
                self._reply(200, {"ok": True, "worker": name})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, MAX_BODY_BYTES + 1))
            if length > MAX_BODY_BYTES:
                self._reply(413, {"error": "request too large"})
                return
            route = routes.get(self.path)
            if route is None:
                self._reply(404, {"error": "not found"})
                return
            try:
                payload = json.loads(body or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                self._reply(200, route(payload))
            except ValueError as error:
                self._reply(400, {"error": str(error)})
            except Exception as error:  # the worker keeps running after a failed job
                print(f"[{name}] request failed: {error!r}", flush=True)
                self._reply(500, {"error": type(error).__name__})

        def log_message(self, *_args) -> None:
            pass

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(port: int, routes: dict[str, Route], name: str) -> None:
    server = make_server(port, routes, name)
    print(f"[{name}] ready on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
