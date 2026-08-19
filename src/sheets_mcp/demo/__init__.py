"""The `--demo` server: every tool, no Google account, no credentials.

Screenshots prove a thing worked once. This lets someone clone the repository,
run one command, point a client at localhost and use all nine tools against a
spreadsheet that behaves like the real ones — including the parts that make them
hard.
"""

from sheets_mcp.demo.data import build_backend, registry_yaml

__all__ = ["build_backend", "registry_yaml"]
