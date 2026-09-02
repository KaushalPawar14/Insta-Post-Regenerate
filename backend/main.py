"""
Single ASGI entrypoint for the `backend` Vercel Service.

Vercel Services requires a Python service to declare exactly one ASGI/WSGI
`entrypoint` (declared in ../vercel.json as `"entrypoint": "main:app"`) --
unlike the older, non-Services "existing projects" mode, it does not support
multiple standalone `BaseHTTPRequestHandler` files each becoming their own
function. See ../README.md "Runtime coexistence" for the full story of how
this project arrived at this shape.

This file is pure request/response plumbing. Every stage's actual pipeline
logic still lives in its own module (scrape.py, scrape_poll.py, analyze.py,
generate.py) as a plain `run(payload: dict) -> dict` function, untouched by
this restructure -- this file only wires HTTP in front of them.

Routes are declared at their full public path (/api/scrape, etc.) because
Vercel Services routes each request into a service with its original path
intact -- see the Services routing docs.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Awaitable, Callable, Dict  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from _lib import queue  # noqa: E402
from _lib.handler import TerminalError  # noqa: E402

import analyze  # noqa: E402
import generate  # noqa: E402
import scrape  # noqa: E402
import scrape_poll  # noqa: E402

app = FastAPI()

STAGES: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "scrape": scrape.run,
    "scrape_poll": scrape_poll.run,
    "analyze": analyze.run,
    "generate": generate.run,
}


async def _dispatch(endpoint: str, request: Request) -> JSONResponse:
    """
    Shared POST handling for every stage: verify the QStash signature, parse
    the body, run the stage, and translate the result to an HTTP response.

    Every POST is signature-verified as a genuine QStash delivery before any
    work happens, because these endpoints spend money.
    """
    raw_body = (await request.body()).decode("utf-8")

    signature = request.headers.get("upstash-signature") or ""
    if not signature:
        return JSONResponse({"error": "Missing Upstash-Signature header."}, status_code=401)

    try:
        queue.verify(signature, raw_body, endpoint)
    except Exception as exc:  # noqa: BLE001
        print(f"[{endpoint}] signature verification FAILED: {exc}")
        return JSONResponse({"error": "Invalid QStash signature."}, status_code=401)

    try:
        payload = queue.parse_body(raw_body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    try:
        result = STAGES[endpoint](payload) or {}
        return JSONResponse({"ok": True, **result})
    except TerminalError as exc:
        # Return 200 with ok:false for terminal, already-recorded failures so
        # QStash does not retry work that will fail identically. Stage
        # handlers record their own failure state in Postgres before raising
        # TerminalError.
        return JSONResponse({"ok": False, "terminal": True, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/scrape")
async def post_scrape(request: Request) -> JSONResponse:
    return await _dispatch("scrape", request)


@app.post("/api/scrape_poll")
async def post_scrape_poll(request: Request) -> JSONResponse:
    return await _dispatch("scrape_poll", request)


@app.post("/api/analyze")
async def post_analyze(request: Request) -> JSONResponse:
    return await _dispatch("analyze", request)


@app.post("/api/generate")
async def post_generate(request: Request) -> JSONResponse:
    return await _dispatch("generate", request)


@app.get("/api/{endpoint}")
async def health(endpoint: str) -> JSONResponse:
    """Health probe. Never does work and never touches a paid API."""
    if endpoint not in STAGES:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "endpoint": endpoint, "method": "GET"})
