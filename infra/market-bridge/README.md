# market-bridge (docich #603)

Windows kabu / MT5 + Tailscale-limited bridge. PAPER only. No order path.

Status: Step 2 (Linux side) in progress — common readiness, persistent skip/cancel,
guard/recovery implemented with a fake provider. All markets remain disabled.
Design v1 lives in issue #603.

## Layout

- `inventory.example.toml` — schema for Windows + Linux inventory (no secrets).
- `runbooks/` — operational runbooks (boot/session/Tailscale/network/auth/data/backup/monitoring/deploy).
- `inventory/` (gitignored) — collected evidence (no secrets).

## Gates (Step 1)

- No Tailscale ACL/Serve changes.
- No Windows-side changes.
- No existing service changes.
- No secrets in repo/logs/Actions.