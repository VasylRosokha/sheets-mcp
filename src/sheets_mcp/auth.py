"""Request authentication (§6).

Written as raw ASGI rather than Starlette middleware so it can wrap the app
`MCPServer.streamable_http_app()` hands back without reaching inside it.

Two mechanisms, independently switchable:

* **Header** (§6.1) — `x-api-key`, compared in constant time. Preferred.
* **Secret path** (§6.2) — the endpoint is mounted at an unguessable URL, so
  this middleware never sees an unauthorized request in the first place. It is
  enforced by the mount path, not by any code here.

Both may be active at once, in which case a request must satisfy both.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

DEFAULT_HEADER = "x-api-key"


class ApiKeyMiddleware:
    """Reject requests to `protected_path` without a valid API key header."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_key: str,
        protected_path: str,
        header_name: str = DEFAULT_HEADER,
    ) -> None:
        self._app = app
        self._api_key = api_key
        self._protected_path = protected_path
        self._header = header_name.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected(scope["path"]):
            # Lifespan messages, websockets, and public routes such as
            # /health (§12.11) pass straight through.
            await self._app(scope, receive, send)
            return

        if self._authorized(scope.get("headers", ())):
            await self._app(scope, receive, send)
            return

        await _unauthorized(send, self._header.decode("latin-1"))

    def _is_protected(self, path: str) -> bool:
        return path == self._protected_path or path.startswith(f"{self._protected_path}/")

    def _authorized(self, headers: Iterable[tuple[bytes, bytes]]) -> bool:
        for name, value in headers:
            if name.lower() == self._header:
                # compare_digest, not ==, so a wrong key cannot be recovered
                # one byte at a time from response timing.
                return hmac.compare_digest(value.decode("latin-1"), self._api_key)
        return False


async def _unauthorized(send: Send, header_name: str) -> None:
    body = json.dumps(
        {
            "error": "unauthorized",
            "message": f"Missing or invalid {header_name} header.",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
