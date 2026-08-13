# deploy/ — self-managed VPS path

**These files are not used by the live deployment.** sheets-mcp runs on Render
(`render.yaml`, spec §12.13), which supplies TLS, the hostname, the process
supervisor, and the network edge. Nothing here is referenced there.

They are kept because the VPS path in spec §12.1–12.12 remains a valid fallback,
and because the problems they solve are real ones that return the moment the
application is hosted on a machine somebody owns:

| File | Solves |
|---|---|
| `sheets-mcp.service` | Process supervision, restart-on-failure, boot ordering |
| `Caddyfile` | TLS issuance and renewal, log redaction of the secret path (§6.2) |
| `deploy.sh` | rsync → `uv sync --frozen` → restart → verify |
| `verify.sh` | The §12.12.1 gate: firewall rule *order*, boot enablement, liveness |

`verify.sh` is worth reading even if the VPS is never used again. It checks that
the iptables ACCEPT rules sit *above* the catch-all REJECT rather than merely
existing, because a rule below the REJECT is listed by `iptables -S`, passes a
naive grep, and still leaves the port shut.
