"""API keys. Every /api and /mcp request needs the key, sent any of these ways:

    Authorization: Bearer <key>      (MCP clients, curl, GPT Actions "API key / Bearer")
    X-API-Key: <key>
    ?key=<key>                       (only for clients that can't set headers; keep such URLs private)

The key comes from SILLAGE_API_KEY, else it is generated once and kept in data/api_key.
"""

from __future__ import annotations

import hmac
import os
import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .db import db_path

PUBLIC_PATHS = ("/api/health",)


@lru_cache(maxsize=1)
def api_key() -> str:
    if key := os.environ.get("SILLAGE_API_KEY"):
        return key
    path = Path(db_path()).parent / "api_key"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = "sk-sillage-" + secrets.token_urlsafe(24)
    path.write_text(key)
    path.chmod(0o600)
    return key


def _presented(headers: dict[str, str], query: str) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if headers.get("x-api-key"):
        return headers["x-api-key"].strip()
    for part in query.split("&"):
        if part.startswith("key="):
            from urllib.parse import unquote

            return unquote(part[4:])
    return None


def valid(presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(presented.encode(), api_key().encode())


def require_key(request: Request) -> None:
    """FastAPI dependency for /api routes."""
    if request.url.path in PUBLIC_PATHS:
        return
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not valid(_presented(headers, request.url.query)):
        raise HTTPException(401, "Missing or wrong API key", headers={"WWW-Authenticate": "Bearer"})


class KeyMiddleware:
    """ASGI guard for the mounted MCP app (not a FastAPI route, so dependencies don't apply)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if not valid(_presented(headers, scope.get("query_string", b"").decode())):
                resp = JSONResponse({"error": "Missing or wrong API key"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
