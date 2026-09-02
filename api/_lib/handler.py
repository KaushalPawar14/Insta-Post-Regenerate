"""
Thin `BaseHTTPRequestHandler` scaffolding for the Vercel Python functions.

`BaseHTTPRequestHandler` is used rather than FastAPI/Flask on purpose: Vercel's
Python *framework preset* detection takes precedence over file-based `/api`
functions, so having a web framework in requirements.txt would hijack routing
away from the Next.js app. See requirements.txt.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict

from . import queue


class QStashHandlerBase(BaseHTTPRequestHandler):
    """
    Subclasses set `endpoint` and implement `run(payload) -> dict`.

    Every POST is signature-verified as a genuine QStash delivery before any
    work happens, because these endpoints spend money.
    """

    endpoint: str = ""

    # --- to implement in subclasses ---
    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    # --- plumbing ---
    def do_GET(self) -> None:
        """Health check. Never does work and never touches paid APIs."""
        self._json(200, {"ok": True, "endpoint": self.endpoint, "method": "GET"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""

        signature = self.headers.get("upstash-signature") or ""
        if not signature:
            self._json(401, {"error": "Missing Upstash-Signature header."})
            return

        try:
            queue.verify(signature, raw_body, self.endpoint)
        except Exception as exc:  # noqa: BLE001
            print(f"[{self.endpoint}] signature verification FAILED: {exc}")
            self._json(401, {"error": "Invalid QStash signature."})
            return

        try:
            payload = queue.parse_body(raw_body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        try:
            result = self.run(payload) or {}
            self._json(200, {"ok": True, **result})
        except Exception as exc:  # noqa: BLE001
            # Return 200 with ok:false for terminal, already-recorded failures
            # so QStash does not retry work that will fail identically. Stage
            # handlers record their own failure state in Postgres before
            # raising TerminalError.
            traceback.print_exc()
            if isinstance(exc, TerminalError):
                self._json(200, {"ok": False, "terminal": True, "error": str(exc)})
            else:
                self._json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.endpoint}] {fmt % args}")

    def _json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TerminalError(Exception):
    """
    A failure that retrying cannot fix (bad URL, post not found, content
    policy refusal). The stage records it in Postgres, then raises this so
    QStash stops rather than burning retries -- and, for paid stages, money.
    """


def make_handler(endpoint: str, run: Callable[[Dict[str, Any]], Dict[str, Any]]):
    """Build a concrete handler class for a stage function."""

    class _Handler(QStashHandlerBase):
        pass

    _Handler.endpoint = endpoint
    _Handler.run = lambda self, payload: run(payload)  # type: ignore[assignment]
    return _Handler
