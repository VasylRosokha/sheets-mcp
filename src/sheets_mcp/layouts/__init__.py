"""Layout-specific readers.

Each module here knows how to find structure in one physical sheet shape (§7.1)
from a raw block of cell values. They do no I/O: a grid of strings goes in, a
described structure comes out, which is what makes them testable against
fixtures rather than against a live spreadsheet.
"""

from sheets_mcp.layouts.dated_block import Block, scan_blocks
from sheets_mcp.layouts.grid import Period, scan_periods

__all__ = ["Block", "Period", "scan_blocks", "scan_periods"]
