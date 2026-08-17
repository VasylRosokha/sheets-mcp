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
