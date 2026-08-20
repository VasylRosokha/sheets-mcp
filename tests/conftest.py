"""Test-wide setup.

These are set at import rather than in a fixture, because conftest is imported
before the test modules are, and `sheets_mcp.server` resolves its configuration
at *its* import — deliberately, so a bad environment fails before uvicorn binds
the port. A fixture would run too late for any module that imports the server.

`profiles.yaml` refers to its spreadsheet ids through `${...}` placeholders, so
the registry does not load without them. The values are irrelevant here — the
Sheets backend is always a double and never looks at an id — but they have to be
*present*, because an unset placeholder is a boot failure by design.
"""

from __future__ import annotations

import os

os.environ.setdefault("TRAINING_SPREADSHEET_ID", "test-training-spreadsheet-id")
os.environ.setdefault("STUDY_SPREADSHEET_ID", "test-study-spreadsheet-id")
# Importing the server module builds Settings, which refuses to start with no
# authentication configured. Any key will do; nothing here serves a request.
os.environ.setdefault("MCP_API_KEY", "test-key")
