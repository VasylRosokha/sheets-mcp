"""The exception hierarchy, carrying the §10 error codes.

Every tool converts these into a structured result rather than letting a stack
trace escape. The message text is user-facing copy: Claude relays it more or
less verbatim, and the person reading it on a phone cannot inspect a traceback.

Each message therefore says what went wrong *and* what to do about it. An error
that only states a fact — "permission denied" — costs a round trip to work out
the fix, and the fix is usually one specific action.
"""

from __future__ import annotations


class SheetsMcpError(Exception):
    """Base class. `code` is the §10 error code reported to the caller."""

    code = "INTERNAL_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class ProfileNotFound(SheetsMcpError):
    code = "PROFILE_NOT_FOUND"

    def __init__(self, token: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "(none configured)"
        super().__init__(f"No profile named {token!r}. Available profiles: {listed}.")


class WrongLayout(SheetsMcpError):
    code = "WRONG_LAYOUT"

    def __init__(self, profile: str, layout: str, correct_tool: str) -> None:
        super().__init__(
            f"Profile {profile!r} uses the {layout!r} layout; this tool does not apply to it. "
            f"Use {correct_tool} instead."
        )


class PermissionDenied(SheetsMcpError):
    code = "PERMISSION_DENIED"

    def __init__(self, spreadsheet_id: str, client_email: str | None) -> None:
        # Naming the account turns a dead end into a two-click fix, and it is
        # the one piece of information the user cannot look up for themselves.
        who = client_email or "the service account"
        super().__init__(
            f"No access to spreadsheet {spreadsheet_id}. Open it in Google Sheets, "
            f"click Share, and give {who} the Editor role."
        )


class SpreadsheetNotFound(SheetsMcpError):
    code = "SPREADSHEET_NOT_FOUND"

    def __init__(self, spreadsheet_id: str) -> None:
        super().__init__(
            f"No spreadsheet with id {spreadsheet_id}. Check the id in profiles.yaml "
            "against the URL of the sheet."
        )


class TabNotFound(SheetsMcpError):
    code = "TAB_NOT_FOUND"

    def __init__(self, tab: str, spreadsheet_id: str, available: list[str]) -> None:
        listed = ", ".join(repr(name) for name in available) if available else "(none readable)"
        super().__init__(
            f"Spreadsheet {spreadsheet_id} has no tab named {tab!r}. Tabs present: {listed}. "
            "Tab names are case-sensitive."
        )


class ValidationError(SheetsMcpError):
    code = "VALIDATION_ERROR"


class DateOutOfOrder(SheetsMcpError):
    code = "DATE_OUT_OF_ORDER"

    def __init__(self, requested: str, last_block: str) -> None:
        super().__init__(
            f"Cannot log {requested}: the sheet's most recent block is {last_block}, and blocks "
            "must stay in ascending order. Inserting a session mid-sheet is not supported — add "
            "it by hand if the date really is earlier."
        )


class TooManyValues(SheetsMcpError):
    code = "TOO_MANY_VALUES"

    def __init__(self, item: str, given: int, allowed: int) -> None:
        super().__init__(
            f"{item!r} has {given} values but this profile has room for {allowed}. "
            "Combine them into fewer cells, or widen value_columns in profiles.yaml."
        )


class ColumnNotFound(SheetsMcpError):
    code = "COLUMN_NOT_FOUND"

    def __init__(self, wanted: str, period: str, present: list[str]) -> None:
        listed = ", ".join(repr(label) for label in present) if present else "(none)"
        super().__init__(
            f"No column matching {wanted!r} in the {period!r} block. That block carries: "
            f"{listed}. Labels differ between periods in this sheet, so use one of these."
        )


class PeriodBlockMissing(SheetsMcpError):
    code = "PERIOD_BLOCK_MISSING"

    def __init__(self, period: str, present: list[str]) -> None:
        listed = ", ".join(present) if present else "(none)"
        super().__init__(
            f"No block for {period!r} yet. Existing blocks: {listed}. "
            f"Call create_period_block with period for {period!r} first."
        )


class PeriodExists(SheetsMcpError):
    code = "PERIOD_EXISTS"

    def __init__(self, period: str, row: int) -> None:
        super().__init__(
            f"A block for {period!r} already exists, starting at row {row}. "
            "Write into it with set_grid_value rather than creating a second one."
        )


class DayOutOfRange(SheetsMcpError):
    code = "ROW_NOT_FOUND"

    def __init__(self, day: int, period: str, last_day: int) -> None:
        super().__init__(f"Day {day} is outside the {period!r} block, which runs to day {last_day}.")


class UnparseableCell(SheetsMcpError):
    code = "UNPARSEABLE_CELL"

    def __init__(self, address: str, raw: str) -> None:
        super().__init__(
            f"Cannot read the current value of {address}: {raw!r}. Increment needs to know "
            "what is there before adding to it — fix the cell by hand, or use mode 'set'."
        )


class BadRequest(SheetsMcpError):
    code = "BAD_REQUEST"

    def __init__(self, a1_range: str, detail: str) -> None:
        # Google's own sentence, verbatim. Paraphrasing it is what turned a
        # width mismatch into a bogus TAB_NOT_FOUND.
        super().__init__(f"Google Sheets rejected the request for {a1_range}: {detail}")


class RateLimited(SheetsMcpError):
    code = "RATE_LIMITED"


class UpstreamError(SheetsMcpError):
    code = "UPSTREAM_ERROR"


class CredentialsMissing(SheetsMcpError):
    code = "CREDENTIALS_MISSING"

    def __init__(self) -> None:
        super().__init__(
            "No Google credentials are configured. Set GOOGLE_SERVICE_ACCOUNT_KEY to the "
            "base64-encoded service account JSON key (§5.3)."
        )


class CredentialsInvalid(SheetsMcpError):
    code = "CREDENTIALS_INVALID"


class RegistryMissing(SheetsMcpError):
    code = "REGISTRY_MISSING"

    def __init__(self) -> None:
        super().__init__(
            "No profiles are configured. Create profiles.yaml from profiles.example.yaml, "
            "filling in each spreadsheet id and tab name."
        )
