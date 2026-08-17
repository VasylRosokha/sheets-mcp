"""Reading the `dated-block` layout (§7.1).

A block is a row whose date column parses as a date, followed by item rows,
terminated by a blank row or the next date. There is no header row, so the only
structural signal available is whether column A parses as a date — which is why
`date_formats` in the profile is load-bearing rather than decorative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sheets_mcp import dates
from sheets_mcp.profiles.models import DatedBlockProfile, column_index


@dataclass(frozen=True, slots=True)
class Item:
    """One row inside a block: a name plus however many values it carried."""

    name: str
    values: tuple[str, ...]


@dataclass(slots=True)
class Block:
    """One dated session."""

    date: date
    raw_date: str
    row: int  # 1-based sheet row of the date row, for later writes
    items: list[Item] = field(default_factory=list)


def scan_blocks(rows: list[list[str]], profile: DatedBlockProfile) -> list[Block]:
    """Find every block in a range read from the top of the sheet.

    `rows` must start at sheet row 1, because block positions are reported as
    absolute row numbers for writes to use later. Google truncates trailing
    empty cells, so rows arrive ragged and every access has to tolerate a short
    row rather than assuming a rectangle.
    """
    date_at = column_index(profile.date_column)
    item_at = column_index(profile.item_column)
    value_positions = [column_index(letter) for letter in profile.value_columns]

    blocks: list[Block] = []
    for offset, row in enumerate(rows):
        first = _cell(row, date_at)
        parsed = dates.parse(first, profile.date_formats)

        if parsed is not None:
            blocks.append(Block(date=parsed, raw_date=first.strip(), row=offset + 1))
            continue

        if not blocks:
            # Anything above the first date row is a title or a stray note.
            continue

        name = _cell(row, item_at).strip()
        if not name:
            # A blank row closes the current block. The next date opens the
            # next one, so nothing needs to be tracked here.
            continue

        values = tuple(_cell(row, position).strip() for position in value_positions)
        blocks[-1].items.append(Item(name=name, values=_drop_trailing_blanks(values)))

    return blocks


def recent_item_names(blocks: list[Block], *, block_count: int = 10) -> list[str]:
    """Distinct item names from the most recent blocks, most recent first.

    This is the field that stops the model inventing a near-duplicate spelling
    of an exercise it has already used (§8.2). Order matters: the newest naming
    is the convention most worth copying.
    """
    seen: dict[str, None] = {}
    for block in reversed(blocks[-block_count:]):
        for item in block.items:
            seen.setdefault(item.name, None)
    return list(seen)


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _drop_trailing_blanks(values: tuple[str, ...]) -> tuple[str, ...]:
    """Trim empty trailing values so a one-value row does not report six.

    Interior blanks are preserved: in `90x4x3, , 40x20` the gap is a real gap,
    and collapsing it would shift values into the wrong columns on read-back.
    """
    end = len(values)
    while end > 0 and not values[end - 1]:
        end -= 1
    return values[:end]
