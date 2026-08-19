"""Test-wide fixtures.

`profiles.yaml` refers to its spreadsheet ids through `${...}` placeholders, so
the registry does not load without them. The values are irrelevant here — the
Sheets client is always a double and never looks at an id — but they have to be
*present*, because an unset placeholder is a boot failure by design.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _spreadsheet_ids() -> None:
    import os

    os.environ.setdefault("TRAINING_SPREADSHEET_ID", "test-training-spreadsheet-id")
    os.environ.setdefault("STUDY_SPREADSHEET_ID", "test-study-spreadsheet-id")
