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
from sheets_mcp.profiles.models import DatedBlockProfile, column_index, column_letter


@dataclass(frozen=True, slots=True)
class Item:
    """One row inside a block: a name plus however many values it carried."""

    name: str
    values: tuple[str, ...]
    # The 1-based sheet row, when this item was read from a sheet. None when it
    # is being written, because it has no row until the write decides one.
    # `update_row` needs it, and recovering it afterwards would mean scanning
    # for a name that may legitimately appear in twenty blocks.
    row: int | None = None


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
        blocks[-1].items.append(
            Item(name=name, values=_drop_trailing_blanks(values), row=offset + 1)
        )

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


@dataclass(frozen=True, slots=True)
class WritePlan:
    """Exactly what a `log_session` call would write, computed before writing it.

    Separated from the write itself so `dry_run` and the real call share one code
    path — a dry run that computed its answer differently would be reassurance
    about nothing.
    """

    mode: str
    a1_range: str
    rows: list[list[str]]
    first_row: int


def plan_session(
    rows: list[list[str]],
    profile: DatedBlockProfile,
    *,
    tab: str,
    target: date,
    items: list[Item],
    mode: str,
) -> WritePlan:
    """Decide where a session goes and what the written cells contain (§8.7).

    Pure: no I/O, so every branch is testable against a fixture. `rows` must
    start at sheet row 1 so the returned range is absolute.

    Appends only. The first written row is always below every populated row in
    the sheet, which is what makes a mistargeted write unable to overwrite
    existing history.
    """
    blocks = scan_blocks(rows, profile)
    last_populated = _last_populated_row(rows)

    resolved = mode
    if mode == "auto":
        resolved = "append-to-existing" if blocks and blocks[-1].date == target else "new-block"

    item_at = column_index(profile.item_column)
    date_at = column_index(profile.date_column)
    value_positions = [column_index(letter) for letter in profile.value_columns]
    width = max([item_at, date_at, *value_positions]) + 1

    body = [_item_row(item, item_at, value_positions, width) for item in items]

    if resolved == "append-to-existing":
        first_row = last_populated + 1
        payload = body
    else:
        # One blank separator row (§8.7 step 3). It is skipped rather than
        # written: writing an empty row is a no-op that still counts as a
        # modification in version history.
        first_row = last_populated + 2
        date_row = [""] * width
        date_row[date_at] = dates.render(target, profile.write_date_format)
        payload = [date_row, *body]

    start = min([item_at, date_at, *value_positions])
    end = max([item_at, date_at, *value_positions])
    last_row = first_row + len(payload) - 1
    a1_range = f"'{tab}'!{column_letter(start)}{first_row}:{column_letter(end)}{last_row}"

    # Trim to the range's first column. Currently a no-op because this profile
    # starts at A, which is exactly why the identical bug in the grid planner
    # survived until it hit a real sheet.
    payload = [row[start:] for row in payload]

    return WritePlan(mode=resolved, a1_range=a1_range, rows=payload, first_row=first_row)


def _item_row(item: Item, item_at: int, value_positions: list[int], width: int) -> list[str]:
    row = [""] * width
    row[item_at] = item.name
    for value, position in zip(item.values, value_positions, strict=False):
        row[position] = value
    return row


def _last_populated_row(rows: list[list[str]]) -> int:
    """The 1-based index of the last row holding anything.

    Google trims trailing empty rows from a read, so this is usually
    `len(rows)` — but not when a range read returns interior padding, and
    getting it wrong by one writes into the last existing row.
    """
    for offset in range(len(rows) - 1, -1, -1):
        if any(cell.strip() for cell in rows[offset]):
            return offset + 1
    return 0


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
