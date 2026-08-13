#!/usr/bin/env bash
# rsync -> uv sync -> restart -> verify. Run from a laptop checkout:
#   ./deploy/deploy.sh ubuntu@<host>
set -euo pipefail

TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "usage: $0 user@host" >&2; exit 2; }

APP_DIR="${APP_DIR:-/home/ubuntu/sheets-mcp}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== syncing $REPO_ROOT -> $TARGET:$APP_DIR =="
# .env and .venv live on the server and are never overwritten from here: the
# secrets are not in the repo, and the venv is built for the VPS's aarch64.
rsync -az --delete \
	--exclude '.git/' \
	--exclude '.venv/' \
	--exclude '.env' \
	--exclude '__pycache__/' \
	--exclude '.pytest_cache/' \
	--exclude '.mypy_cache/' \
	--exclude '.ruff_cache/' \
	"$REPO_ROOT/" "$TARGET:$APP_DIR/"

echo "== installing dependencies =="
# --frozen: install exactly what uv.lock pins. A deploy is not the place to
# discover that a transitive dependency published a new release this morning.
ssh "$TARGET" "cd $APP_DIR && uv sync --frozen --no-dev"

echo "== restarting =="
ssh "$TARGET" "sudo systemctl restart sheets-mcp"

echo "== verifying =="
ssh "$TARGET" "cd $APP_DIR && ./deploy/verify.sh"
