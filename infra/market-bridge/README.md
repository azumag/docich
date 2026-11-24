# market-bridge (docich #603 / #581)

Windows kabu / MT5 + Tailscale-limited bridge. PAPER only. No order path.

Status: Step 3 **code side only** (DSH 2026-11-22 judgment). The Windows bridge
and Linux provider are implemented as importable modules plus fake/contract
tests. All markets remain disabled; no Windows host, Tailscale ACL/Serve,
production service, credential or deploy is touched by this change.

## Layout

- `inventory.example.toml` — schema for Windows + Linux inventory (no secrets).
- `runbooks/` — operational runbooks (boot/session/Tailscale/network/auth/data/backup/monitoring/deploy).
- `inventory/` (gitignored) — collected evidence (no secrets).
- `bridge.example.toml` — non-secret structure of the per-market bridge/provider config.
- `mbridge/common/` — wire contract, bridge auth, hard deadlines, atomic publish.
- `mbridge/windows/` — loopback HTTP bridge (`bridge.py`), read-only kabu/MT5
  adapters (`adapters.py`) and the isolated MT5 initialize child (`mt5_child.py`).
- `mbridge/linux/` — authenticated deadline-bounded client (`client.py`) and the
  fail-closed provider (`provider.py`) with health lease and atomic publish.

The Windows terminal SDK (`kabu`/`MetaTrader5`) is always injected or launched
in a separate child; importing these modules never requires a vendor SDK.

## Minimal API (§3)

Only these routes exist, all requiring the market-scoped bridge key:

`GET /v1/health` (liveness only) · `GET /v1/readiness` · `GET /v1/snapshot` ·
`GET /v1/scanner` (stocks only) · `PUT /v1/subscriptions` (quote subscription
only, max 50). No order, cancel, account, proxy or arbitrary URL route exists.

## Gates (unchanged)

- All markets disabled; no `enabled`/selector change, no unit replacement or deploy.
- No Tailscale ACL/Serve changes, no Windows-side installation, no real data.
- No broker credential in repo/logs/Actions; only synthetic keys in tests.
- Windows inventory execution and evidence remain an owner task (Step 3 later half).
