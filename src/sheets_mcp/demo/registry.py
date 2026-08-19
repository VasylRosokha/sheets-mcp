"""The demo profile registry.

A YAML string rather than a packaged file: it goes through exactly the same
loader and the same validation as a real `profiles.yaml`, so a mistake here
fails at boot the way a user's would, and there is no package-data wiring to get
wrong in a wheel.

Kept close to the real registry in shape and deliberately different in content —
English labels and month names, so a reviewer can read the sheet and check the
answers. The quirks it has to survive are structural, not linguistic; the real
one is Ukrainian, which the casefolding in `resolve` and `resolve_column` exists
for and which the test suite covers.
"""

from __future__ import annotations

registry_yaml = """
profiles:
  - name: training
    aliases: [workout, gym, log]
    layout: dated-block
    description: >
      Training log (demo data). Each session is a dated block: a date row
      followed by one row per exercise. Set notation is free-form text,
      typically weight x reps x sets (80x8x3) for weighted exercises, or
      reps x sets (10x3) for bodyweight ones. Written verbatim, never parsed.
    spreadsheet_id: demo-training-spreadsheet
    tab: Log
    date_column: A
    item_column: A
    value_columns: [B, C, D, E, F, G]
    # Both appear in the fixture, as they do in a sheet a person has kept for
    # years. Only the four-digit form is ever written back.
    date_formats: ["DD.MM.YYYY", "DD.MM.YY"]
    write_date_format: "DD.MM.YYYY"
    block_separator: blank-row

  - name: study
    aliases: [habits, learning]
    layout: grid
    description: >
      Habit grid (demo data). One block per month, one row per day of that
      month, columns are activities. Durations are written as hours - 1h, 1.5h -
      rounded to the half hour. Older blocks use tally marks ("|" is an hour,
      "-" is thirty minutes); those are still read, and nothing rewrites them.
      There is no block for the current month yet.
    spreadsheet_id: demo-study-spreadsheet
    tab: Habits
    day_column: B
    period_header_offset: -2
    label_row_offset: -1
    value_type: duration-hours
    duration_step: 0.5
    write_canonical: true
    period_names:
      [January, February, March, April, May, June,
       July, August, September, October, November, December]
    columns:
      - key: programming
        label: Programming
        aliases: [code, coding, dev]
      - key: reading
        label: Reading
      - key: english
        label: English
    note: >
      Column labels are NOT stable across months in this fixture, on purpose.
      Reading was replaced by Crypto, and English by "x". The label row must be
      read per block, never assumed from configuration.
"""
