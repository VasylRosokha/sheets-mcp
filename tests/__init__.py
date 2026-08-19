"""Marks `tests` as a package so `tests.fakes` resolves the same way for
pytest and for mypy. Without it mypy maps the file to a top-level `fakes`
module and stops before checking anything else.
"""
