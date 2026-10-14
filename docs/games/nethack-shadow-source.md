# NetHack structured shadow source (P4b)

P4aの `latest.json` を生成するための外部structured-source接続境界。

P4bでもpolicy sourceはTTYのまま。producerはゲーム操作を行わず、外部commandの出力をstrict schemaで検証してshadowファイルへ保存するだけ。

## Flow

```text
external structured adapter
        |
        | stdout JSON
        v
CommandShadowSource
        |
        | size / timeout / exit / schema validation
        v
ShadowPublicSnapshot
        |
        | canonical re-serialize + atomic write
        v
<state_dir>/nethack/shadow/latest.json
        |
        v
P4a shadow comparator
        X gameplay policy/action
```

外部commandが出したraw JSONをそのまま保存しない。`parse_shadow_snapshot()` を通したdataclassから再シリアライズするため、未知fieldやhidden情報はpublishされない。

## External command contract

stdin:

```json
{
  "schema_version": 1,
  "request": "public_shadow_snapshot",
  "game": "nethack",
  "constraints": [
    "public-visible fields only",
    "no hidden map",
    "no true unidentified item identity",
    "no monster internal identity/peacefulness"
  ]
}
```

stdoutはP4a `ShadowPublicSnapshot` schemaのJSON 1件。

失敗条件:

- timeout
- process launch failure
- non-zero exit
- stdout非text
- response size超過
- JSON不正
- schema不正
- hidden/unknown field混入

失敗時は既存 `latest.json` を上書きしない。

## Config

標準は無効。

```toml
[nethack.shadow_source]
enabled = false
command = []
interval_s = 1.0
timeout_s = 2.0
max_response_bytes = 131072
```

外部adapterを検証環境で明示的に有効化する場合だけcommandを設定する。

```toml
[nethack.shadow_source]
enabled = true
command = ["python3", "brains/nethack/public_shadow_adapter.py"]
interval_s = 1.0
timeout_s = 2.0
max_response_bytes = 131072
```

## Manual check

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-shadow-source --once
```

有効時に成功すると `latest.json` と `source-status.json` が更新される。

```text
<state_dir>/nethack/shadow/latest.json
<state_dir>/nethack/shadow/source-status.json
```

## Daemon

systemd template:

```text
scripts/systemd/docich-nethack-shadow-source.service
```

templateをmergeしただけではinstall/enableされない。

## Runtime isolation

producerは以下を行わない。

- tmux capture/send-keys
- GameSwitchStore mutation
- NetHack save操作
- agent Action生成
- policy sourceの切替

外部adapter自身が別プロセス/別環境を使う場合も、Docichへ渡せるのはP4a public schemaのみ。

## NLE系adapterについて

NLE系はDocich本番NetHack 5.0.0とversion差があるため、まず別環境のstructured sourceとして接続し、同一公開状態の整合率を測る用途に限定する。

version差でglyphやstatus semanticsがずれる場合は `mismatch` として可視化し、TTY policyへ混ぜない。

## 次

P4cでは比較ログを集計し、source別のfield一致率・map cell一致率・stale/error率を出す評価器を追加する。

十分な実測が得られるまではstructured sourceをpolicyへ昇格しない。
