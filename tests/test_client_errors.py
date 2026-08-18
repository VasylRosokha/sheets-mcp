from __future__ import annotations

import json
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from sheets_mcp.errors import BadRequest, PermissionDenied, SpreadsheetNotFound, TabNotFound
from sheets_mcp.sheets.client import SheetsClient


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "Bad Request"


def http_error(status: int, message: str) -> HttpError:
    content = json.dumps({"error": {"code": status, "message": message, "status": "INVALID_ARGUMENT"}})
    return HttpError(FakeResponse(status), content.encode("utf-8"))  # type: ignore[arg-type]


def translate(exc: HttpError, a1_range: str | None = "'Лист1'!B242:F274") -> Exception:
    # _translate needs no credentials, but it does read client_email to name the
    # account in a PermissionDenied message. Constructing a real client would
    # need a real key, so the one attribute is supplied directly.
    client = SheetsClient.__new__(SheetsClient)
    client.client_email = "sheets-mcp@example.iam.gserviceaccount.com"
    return SheetsClient._translate(client, exc, "sheet-id", a1_range)  # noqa: SLF001


def test_a_width_mismatch_reports_googles_own_sentence() -> None:
    """The 400 that was misreported as TAB_NOT_FOUND against a tab that existed.

    Blaming the tab sent the diagnosis toward sharing and scopes, neither of
    which was involved. The API's own message names the real problem.
    """
    message = "Requested writing within range ['Лист1'!B242:F274], but tried writing a row with 6 columns."
    result = translate(http_error(400, message))
    assert isinstance(result, BadRequest)
    assert "6 columns" in result.message
    assert "TAB_NOT_FOUND" not in result.code


def test_an_unparseable_range_still_reports_a_missing_tab() -> None:
    result = translate(http_error(400, "Unable to parse range: 'Sheet9'!A1:B2"))
    assert isinstance(result, TabNotFound)


@pytest.mark.parametrize(("status", "expected"), [(403, PermissionDenied), (404, SpreadsheetNotFound)])
def test_permission_and_missing_spreadsheet_are_unchanged(status: int, expected: Any) -> None:
    assert isinstance(translate(http_error(status, "no")), expected)
