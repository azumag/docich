# OANDA Practice provider for FX PAPER

The FX corner remains `mode="paper"`. OANDA is used only for read-only practice
pricing; fills and positions exist only in docich's local PAPER ledger. The
collector has no OANDA order/trade mutation path.

## Operator-managed credentials

Create this file directly on the production VM as the service user:

```text
$HOME/.config/docich/oanda-practice.env
```

It must be a regular, non-symlink file owned by the service user with mode
`0600`, and contain exactly one non-empty value for each key:

```text
DOCICH_OANDA_ACCOUNT_ID=...
DOCICH_OANDA_TOKEN=...
```

Do not paste either value into GitHub Actions, Issues, pull requests, repository
files, command-line arguments, or chat transcripts. The owner-only provider
workflow does not receive these values; systemd loads them from the VM-local
file.

## Provider startup contract

`docich-market-data-fx.service` performs three fail-closed stages:

1. validate the external credential file without printing its values;
2. start the OANDA-practice pricing-only resident collector for `USD_JPY` and
   `EUR_JPY`;
3. wait up to 30 seconds for evidence produced after this service start.

During an open FX week, startup succeeds only after the collector publishes a
fresh provider-health record and a fresh quote file satisfying the reviewed
contract:

- provider `oanda-practice`;
- source `oanda-practice-pricing`;
- `live_order_capability=false`;
- JPY-quoted configured pairs only;
- `tradeable=true`;
- quote age at most 15 seconds;
- positive, non-crossed bid/ask and positive liquidity.

When the FX week is closed, a fresh provider-health record with `status=closed`
is accepted so the resident service can remain installed without a restart loop.
A pre-existing health/quote file from before the current service start never
satisfies startup readiness.

The readiness helper emits no quote values, account identifier, token, provider
body, URL, or error detail. Success/failure is represented only by exit status.

## Owner operation

Use the owner-only **FX market-data provider** workflow with `enable`, `restart`,
or `disable`. Provider operations remain independent from the FX PAPER
worker/corner/improvement timers.

A successful provider start while the FX market is open proves the read-only
pricing path is live. It does **not** enable the FX PAPER show.

## Promotion gate

Keep `config/market-paper.toml` at `fx.enabled=false` until production evidence
shows the resident provider continuously publishing fresh quotes and the PAPER
runtime accepts those quotes without stale/schema/risk failures. Then perform a
separate reviewed activation change and a soak run. Real/live OANDA execution is
not part of this promotion path.
