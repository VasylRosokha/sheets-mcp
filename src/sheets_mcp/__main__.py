"""Local entrypoint: `python -m sheets_mcp`, or `sheets-mcp` once installed.

Under systemd and on Render the server is started by invoking uvicorn directly
against `sheets_mcp.server:app` (§12.7). This module exists so the same server
can be started by hand — and so `--demo` has somewhere to live.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from sheets_mcp.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="sheets-mcp", description=__doc__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "serve synthetic spreadsheets from memory: no Google account, no "
            "credentials, no authentication. For trying the tools out locally."
        ),
    )
    parser.add_argument("--port", type=int, default=None, help="override PORT")
    args = parser.parse_args()

    if args.demo:
        # Set before the app is imported: server.py resolves Settings at import
        # time, deliberately, so a bad environment fails before the port binds.
        os.environ["SHEETS_MCP_DEMO"] = "1"

    settings = Settings.from_env()
    port = args.port or settings.port

    if settings.demo:
        print(f"\n  sheets-mcp demo — http://127.0.0.1:{port}/mcp")
        print("  Synthetic sheets, in memory. Nothing here touches Google.")
        print("  Changes are lost on restart, so break whatever you like.\n")

    uvicorn.run(
        "sheets_mcp.server:app",
        host="127.0.0.1",
        port=port,
        log_level=settings.log_level,
        workers=1,
    )


if __name__ == "__main__":
    main()
