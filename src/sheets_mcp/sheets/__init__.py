"""Authenticated access to the Google Sheets API."""

from sheets_mcp.sheets.client import SheetsClient, SpreadsheetInfo, build_client

__all__ = ["SheetsClient", "SpreadsheetInfo", "build_client"]
