#!/usr/bin/env bash
# Post-deploy and post-reboot gate (§12.12.1).
#
# Everything here passes from *inside* the box even when Oracle's Security List
# blocks the world, so it proves persistence and process health — not
# reachability. Run the external check separately; see the closing message.
set -euo pipefail

fail() { echo "FAIL: $1" >&2; exit 1; }

APP_DIR="${APP_DIR:-/home/ubuntu/sheets-mcp}"

echo "== persistence =="
# Presence is not enough. Oracle's chain ends in a catch-all REJECT, and an
# ACCEPT inserted below it is listed by `iptables -S` while the port stays
# shut — the one firewall mistake that survives a naive check. Compare
# positions: `iptables -S` prints the chain in evaluation order.
RULES="$(sudo iptables -S INPUT)"
REJECT_AT="$(printf '%s\n' "$RULES" | grep -n -- '-j \(REJECT\|DROP\)' | head -1 | cut -d: -f1 || true)"
for port in 80 443; do
	accept_at="$(printf '%s\n' "$RULES" | grep -n -- "--dport $port -j ACCEPT" | head -1 | cut -d: -f1 || true)"
	[ -n "$accept_at" ] || fail "iptables $port ACCEPT rule missing"
	if [ -n "$REJECT_AT" ] && [ "$accept_at" -gt "$REJECT_AT" ]; then
		fail "iptables $port ACCEPT is below the catch-all REJECT (line $accept_at > $REJECT_AT) — port still closed"
	fi
done
[ -f /etc/iptables/rules.v4 ] || fail "iptables rules not persisted to disk (run: sudo netfilter-persistent save)"
[ "$(systemctl is-enabled sheets-mcp)" = "enabled" ] || fail "sheets-mcp not enabled at boot"
[ "$(systemctl is-enabled caddy)" = "enabled" ] || fail "caddy not enabled at boot"

echo "== running =="
systemctl is-active --quiet sheets-mcp || fail "sheets-mcp not running"
systemctl is-active --quiet caddy || fail "caddy not running"

echo "== reachable =="
[ -x "$APP_DIR/.venv/bin/uvicorn" ] || fail "venv missing or not synced (run: uv sync)"
curl -fsS "http://127.0.0.1:${PORT:-8787}/health" >/dev/null || fail "app not responding locally"

echo "== config =="
# An empty value is worse than a missing one: it reads as "configured" to a
# careless parser while authenticating nobody.
grep -qE '^MCP_(API_KEY|SECRET_PATH)=.+' "$APP_DIR/.env" || fail "no auth configured in .env (§6)"
[ "$(stat -c '%a' "$APP_DIR/.env")" = "600" ] || fail ".env is not mode 600"

echo "OK (local checks passed — run the external check separately)"
echo "    curl -sS https://sheets-mcp.<name>.duckdns.org/health   # from your laptop, not the VPS"
