from __future__ import annotations

from typing import Any

import pytest

from sheets_mcp.auth import ApiKeyMiddleware

API_KEY = "0123456789abcdef"
MCP_PATH = "/mcp/9f2c1a7e"


class RecordingApp:
    """Stand-in for the MCP app, recording whether it was reached."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def call(
    middleware: ApiKeyMiddleware,
    path: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope: dict[str, Any] = {"type": scope_type, "path": path, "headers": headers or []}
    await middleware(scope, receive, send)

    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.fixture
def downstream() -> RecordingApp:
    return RecordingApp()


@pytest.fixture
def middleware(downstream: RecordingApp) -> ApiKeyMiddleware:
    return ApiKeyMiddleware(downstream, api_key=API_KEY, protected_path=MCP_PATH)


async def test_valid_key_reaches_the_app(middleware: ApiKeyMiddleware, downstream: RecordingApp) -> None:
    status, _ = await call(middleware, MCP_PATH, headers=[(b"x-api-key", API_KEY.encode())])
    assert status == 200
    assert len(downstream.calls) == 1


async def test_missing_header_is_rejected(middleware: ApiKeyMiddleware, downstream: RecordingApp) -> None:
    status, body = await call(middleware, MCP_PATH)
    assert status == 401
    assert b"unauthorized" in body
    assert downstream.calls == []


async def test_wrong_key_is_rejected(middleware: ApiKeyMiddleware, downstream: RecordingApp) -> None:
    status, _ = await call(middleware, MCP_PATH, headers=[(b"x-api-key", b"wrong")])
    assert status == 401
    assert downstream.calls == []


async def test_header_name_is_case_insensitive(middleware: ApiKeyMiddleware) -> None:
    # HTTP/2 lowercases header names; HTTP/1.1 clients may not.
    status, _ = await call(middleware, MCP_PATH, headers=[(b"X-Api-Key", API_KEY.encode())])
    assert status == 200


async def test_health_is_public(middleware: ApiKeyMiddleware, downstream: RecordingApp) -> None:
    # Caddy and the uptime monitor have no API key (§12.11).
    status, _ = await call(middleware, "/health")
    assert status == 200
    assert len(downstream.calls) == 1


async def test_subpaths_of_the_mcp_endpoint_are_protected(middleware: ApiKeyMiddleware) -> None:
    status, _ = await call(middleware, f"{MCP_PATH}/messages")
    assert status == 401


async def test_prefix_lookalike_is_not_protected(middleware: ApiKeyMiddleware) -> None:
    # "/mcp/9f2c1a7e-public" must not be treated as inside the endpoint.
    status, _ = await call(middleware, f"{MCP_PATH}-public")
    assert status == 200


async def test_lifespan_passes_through(downstream: RecordingApp) -> None:
    # A middleware that swallows lifespan messages stops the session manager
    # from ever starting, and every request then fails at runtime.
    middleware = ApiKeyMiddleware(downstream, api_key=API_KEY, protected_path=MCP_PATH)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await middleware({"type": "lifespan", "path": "", "headers": []}, receive, send)
    assert len(downstream.calls) == 1
