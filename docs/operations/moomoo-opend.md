# Moomoo OpenD for the stock PAPER provider

The stock PAPER runtime remains paper-only and provider-neutral. Moomoo/OpenD is
used only as a read-only JP market-data source. `docich` never creates a trading
context, unlocks trading, or sends an order through Moomoo.

## Why OpenD is operator-managed

The Python `moomoo` package is only the client SDK. A local OpenD process must
also be logged in before quote APIs are available. OpenD 10.10+ performs an
interactive login on first use and can remember that login for later unattended
starts. The password/remembered-login material must therefore stay on the VM and
must not pass through GitHub Actions, repository files, issues, or logs.

Download the current **Ubuntu command-line OpenD** package from the official
Moomoo API documentation. Do not pin an unofficial mirror or a guessed download
URL in this repository.

Official documentation:

- https://openapi.moomoo.com/moomoo-api-doc/jp/opend/opend-cmd.html
- https://openapi.moomoo.com/moomoo-api-doc/jp/opend/opend-intro.html

## Fixed paths

The managed unit expects these operator-owned files:

```text
$HOME/.local/share/docich/moomoo-opend/OpenD
$HOME/.local/share/docich/moomoo-opend/Appdata.dat
$HOME/.config/docich/moomoo-opend/OpenD.xml
```

`OpenD` must be executable. `Appdata.dat` must be present and non-empty.
`OpenD.xml` must be owned by the service user, must not be a symlink, and must
have mode `0400` or `0600`.

The service overrides the network-facing settings on the command line:

```text
api_ip=127.0.0.1
api_port=11111
lang=en
console=0
login_by_remember=1
```

It never binds OpenD to `0.0.0.0`.

## One-time interactive login

After extracting the official package and preparing the private config, perform
one interactive login **on the VM itself**. Do not put the password in a GitHub
secret merely to automate this bootstrap.

Example (paths only; no credential values):

```bash
cd "$HOME/.local/share/docich/moomoo-opend"
./OpenD \
  -cfg_file="$HOME/.config/docich/moomoo-opend/OpenD.xml" \
  -api_ip=127.0.0.1 \
  -api_port=11111 \
  -lang=en
```

Complete the provider's interactive authentication and choose its remembered
login option. Stop that foreground process after the remembered login has been
successfully established. Subsequent managed starts use `-login_by_remember=1`.

## Managed provider lifecycle

The owner-only **Stock market-data provider** workflow remains the control
surface. On `enable` or `restart`, the reviewed unit installer is run first and
the pinned Python quote SDK is provisioned into the existing `.venv-trading`.
The stock collector then requires `docich-moomoo-opend.service`.

OpenD startup is fail-closed:

1. external binary/Appdata/config paths are checked;
2. config ownership and `0400`/`0600` mode are checked without printing or
   reading its contents;
3. the pinned `moomoo` SDK must import successfully;
4. OpenD is started on loopback only;
5. a sanitized Moomoo quote-protocol handshake must reach OpenD within 30s.

If any step fails, the stock market-data collector is not considered started.
Disabling the stock provider stops the dedicated OpenD process; restarting the
provider restarts OpenD before the collector.

## Promotion gate

Starting the provider does **not** enable the stock PAPER worker/corner.
`config/market-paper.toml` stays fail-closed until production evidence exists.
During JP market hours, require the existing sanitized provider probe to report
`realtime_ready`, then run a one-hour observation/soak and verify candidate
churn, quote freshness, worker continuity, market close handling, and the 10:00
entry cutoff. Only after those checks should stock PAPER activation be proposed
as a separate reviewed change.

Outside JP market hours, an authenticated provider can legitimately be reachable
without `realtime_ready`; do not reinterpret a closed market as proof of fresh
realtime quotes.
