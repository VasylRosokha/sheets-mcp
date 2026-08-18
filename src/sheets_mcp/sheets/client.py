"""Authenticated Google Sheets client (§5).

The service account key arrives base64-encoded in an environment variable and
is decoded into memory. It is never written to disk: a file would need a path,
permissions, a backup policy, and a way to be excluded from every archive, and
the only thing it would buy is the ability to forget it exists.

`google-api-python-client` is synchronous, so every call here runs in a worker
thread. Blocking the event loop would stall the whole server, and this process
handles a Streamable HTTP transport that has to stay responsive.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import random
from dataclasses import dataclass
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from sheets_mcp.errors import (
    BadRequest,
    CredentialsInvalid,
    CredentialsMissing,
    PermissionDenied,
    RateLimited,
    SpreadsheetNotFound,
    TabNotFound,
    UpstreamError,
)
from sheets_mcp.logging import get_logger

log = get_logger(__name__)

# Read/write on spreadsheets explicitly shared with the account. Not
# `drive.readonly`, not `spreadsheets.readonly`: the first would widen reach to
# every file in the Drive, and the second could not write. Sharing is what
# limits scope here (§5.2), so the scope itself stays narrow in kind.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

_MAX_ATTEMPTS = 3
_BASE_DELAY = 0.2


@dataclass(frozen=True, slots=True)
class SpreadsheetInfo:
    """The metadata a describe call needs, without a second round trip."""

    title: str
    tabs: tuple[str, ...]


class SheetsClient:
    """Thin async wrapper over the Sheets v4 API."""

    def __init__(self, credentials: service_account.Credentials) -> None:
        self._credentials = credentials
        self.client_email: str | None = getattr(credentials, "service_account_email", None)
        # cache_discovery=False: the default file cache is unwritable in a
        # read-only container and logs a warning on every construction.
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        """Title and tab names. One call, because both are wanted together."""

        def call() -> dict[str, Any]:
            request = self._service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                # Without a field mask this returns every cell of every sheet.
                fields="properties.title,sheets.properties.title",
            )
            return dict(request.execute())

        payload = await self._run(call, spreadsheet_id=spreadsheet_id)
        title = str(payload.get("properties", {}).get("title", ""))
        tabs = tuple(str(sheet["properties"]["title"]) for sheet in payload.get("sheets", []))
        return SpreadsheetInfo(title=title, tabs=tabs)

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        """Read a range, returned as rows of strings.

        Google truncates trailing empty cells, so rows come back ragged — a row
        of three values and a row of one are both returned at their own length.
        Callers pad; this layer does not invent columns it did not receive.
        """

        def call() -> dict[str, Any]:
            request = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=a1_range,
                    # Formatted, not raw: the sheet's own rendering of a date is
                    # what a human sees and what §7.5's mixed formats describe.
                    valueRenderOption="FORMATTED_VALUE",
                )
            )
            return dict(request.execute())

        payload = await self._run(call, spreadsheet_id=spreadsheet_id, a1_range=a1_range)
        rows = payload.get("values", [])
        return [[str(cell) for cell in row] for row in rows]

    async def write_range(
        self, spreadsheet_id: str, a1_range: str, values: list[list[str]]
    ) -> dict[str, Any]:
        """Write values into an explicit range.

        `RAW`, not `USER_ENTERED`. The values this server writes are free-form
        text that Sheets would otherwise try to interpret: `8x3` survives either
        way, but a tally cell of `-` and a date of `16.08.2026` do not
        necessarily, and §7.3 promises verbatim writing. RAW also means a value
        beginning with `=` is stored as text rather than evaluated as a formula,
        which matters when the text ultimately comes from a chat message.
        """

        def call() -> dict[str, Any]:
            request = (
                self._service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=a1_range,
                    valueInputOption="RAW",
                    body={"values": values},
                )
            )
            return dict(request.execute())

        return await self._run(call, spreadsheet_id=spreadsheet_id, a1_range=a1_range)

    async def _run(
        self,
        call: Any,
        *,
        spreadsheet_id: str,
        a1_range: str | None = None,
    ) -> dict[str, Any]:
        """Execute a blocking API call with the §10.1 retry policy.

        Retries only 429 and 5xx. A 4xx other than 429 is a bug in the request,
        and retrying it turns a clear failure into a slow one.
        """
        last: HttpError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await asyncio.to_thread(call)
            except HttpError as exc:
                status = exc.resp.status
                if status not in (429, 500, 502, 503, 504):
                    raise self._translate(exc, spreadsheet_id, a1_range) from exc
                last = exc
                if attempt == _MAX_ATTEMPTS:
                    break
                # Jittered backoff: a fixed delay would synchronise retries if
                # several calls were ever rate-limited together.
                delay = _BASE_DELAY * (2 ** (attempt - 1)) * (1 + random.random())
                log.warning("sheets_retry", attempt=attempt, status=status, delay=round(delay, 3))
                await asyncio.sleep(delay)

        assert last is not None
        if last.resp.status == 429:
            raise RateLimited(
                "Google rate-limited this request after 3 attempts. Wait a moment and try again."
            ) from last
        raise UpstreamError(
            f"Google Sheets returned {last.resp.status} on 3 attempts. This is usually transient."
        ) from last

    def _translate(self, exc: HttpError, spreadsheet_id: str, a1_range: str | None) -> Exception:
        status = exc.resp.status
        if status in (401, 403):
            return PermissionDenied(spreadsheet_id, self.client_email)
        if status == 404:
            return SpreadsheetNotFound(spreadsheet_id)
        if status == 400 and a1_range is not None:
            # Only "unable to parse range" actually means a missing tab. This
            # used to blame the tab for *every* 400, which hid a real defect:
            # a write whose rows were one column wider than its range failed
            # with TAB_NOT_FOUND naming a tab that plainly existed and had been
            # read successfully seconds earlier. An error that misattributes
            # the cause is worse than one that says nothing.
            detail = _detail(exc)
            if "unable to parse range" in detail.casefold():
                tab = a1_range.split("!", 1)[0].strip("'")
                return TabNotFound(tab, spreadsheet_id, [])
            return BadRequest(a1_range, detail)
        return UpstreamError(f"Google Sheets returned {status}: {exc.reason}")


def build_client(raw_key: str | None) -> SheetsClient:
    """Construct a client from the base64 service-account key (§5.3)."""
    if raw_key is None or not raw_key.strip():
        raise CredentialsMissing

    try:
        decoded = base64.b64decode(raw_key.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialsInvalid(
            "GOOGLE_SERVICE_ACCOUNT_KEY is not valid base64. Produce it with "
            "`base64 -w0 key.json` — the -w0 matters, since line breaks corrupt it."
        ) from exc

    try:
        info = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise CredentialsInvalid(
            "GOOGLE_SERVICE_ACCOUNT_KEY decodes to something that is not JSON. "
            "Check that the whole key file was encoded, not a fragment."
        ) from exc

    try:
        # google-auth ships py.typed but leaves this constructor unannotated, so
        # --strict treats the whole call as untyped. The result is annotated on
        # the way out, which is the part that matters to callers.
        credentials: service_account.Credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=list(SCOPES)
        )
    except (GoogleAuthError, ValueError, KeyError) as exc:
        raise CredentialsInvalid(
            f"The service account key is missing required fields: {exc}. Download a fresh "
            "JSON key from the service account's Keys tab."
        ) from exc

    return SheetsClient(credentials)


def _detail(exc: HttpError) -> str:
    """The API's own explanation, which is where the useful text lives.

    `HttpError.reason` is often just "Bad Request"; the sentence that names the
    actual problem sits in the JSON body.
    """
    try:
        payload = json.loads(exc.content.decode("utf-8"))
        message = payload.get("error", {}).get("message")
        if isinstance(message, str) and message:
            return message
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    return str(exc.reason)
