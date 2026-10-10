"""Small ASGI request-body limits used before framework body parsing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


MAX_JSON_REQUEST_BYTES = 8 * 1024 * 1024
# Image-edit handlers enforce a 40 MiB aggregate file limit. Leave room for
# multipart boundaries and field headers while still bounding parser memory.
MAX_MULTIPART_REQUEST_BYTES = 48 * 1024 * 1024


class RequestBodyTooLarge(Exception):
    """Raised while an application is reading a request over its limit."""


def _header(scope: dict[str, Any], name: bytes) -> bytes:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value
    return b""


def request_body_limit(scope: dict[str, Any]) -> int | None:
    """Return a limit for request bodies that would otherwise be unbounded."""
    if scope.get("type") != "http" or scope.get("method", "GET").upper() in {"GET", "HEAD", "OPTIONS"}:
        return None
    content_type = _header(scope, b"content-type").split(b";", 1)[0].strip().lower()
    if content_type == b"application/json" or content_type.endswith(b"+json"):
        return MAX_JSON_REQUEST_BYTES
    if content_type == b"multipart/form-data":
        return MAX_MULTIPART_REQUEST_BYTES
    return None


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before Starlette/FastAPI parses them.

    The Content-Length check handles the common case without reading any body.
    The wrapped receive function also enforces the limit for chunked requests or
    clients that omit Content-Length.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        limit = request_body_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = _header(scope, b"content-length")
        try:
            declared_length = int(content_length) if content_length else None
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > limit:
            await self._send_rejection(send)
            return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._send_rejection(send)
            else:
                raise

    @staticmethod
    async def _send_rejection(send) -> None:
        body = b'{"detail":"request body too large"}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
