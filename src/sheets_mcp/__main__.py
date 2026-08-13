"""Local entrypoint: `python -m sheets_mcp`.

Under systemd the unit invokes uvicorn directly against `sheets_mcp.server:app`
(§12.7) — this module exists so the same server can be started by hand while
developing without remembering the uvicorn arguments.
"""

from __future__ import annotations

import uvicorn

from sheets_mcp.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "sheets_mcp.server:app",
        host="127.0.0.1",
        port=settings.port,
        log_level=settings.log_level,
        workers=1,
    )


if __name__ == "__main__":
    main()
