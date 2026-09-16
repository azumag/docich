# NetHack shadow evidence evaluation (P4c)

P4a/P4bで収集したTTYとstructured shadowの比較ログを、source単位で集計するオフライン評価器。

P4cの結果は **automatic promotionではない**。`eligible_for_review=true` は、次の昇格PRを人間が検討するための最低限の証拠閾値を満たした、という意味だけを持つ。

## Input

既定:

```text
<state_dir>/nethack/shadow/comparisons.jsonl
```

P4aが記録する `match / mismatch / stale / invalid` をsource別に集計する。

## Metrics

sourceごとに次を出す。

- total events
- comparable observations
- match / mismatch count
- observation match rate
- map cells compared / mismatched
- map-cell match rate
- stale count / stale rate
- invalid count
- mismatch field breakdown
- eligible_for_review
- readiness reasons

## Default review thresholds

```text
min comparable observations     = 100
min observation match rate      = 0.99
min map-cell match rate         = 0.999
max stale rate                  = 0.01
invalid events                  = 0 required
```

これらは安全性を証明する閾値ではない。sample不足や明白な不一致があるsourceを、policy昇格候補から早期に外すための最低基準。

## CLI

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-shadow-evaluate
```

別ログを評価する場合:

```bash
bin/docich nethack-shadow-evaluate \
  --input /path/to/comparisons.jsonl \
  --min-comparable 500 \
  --min-observation-match-rate 0.995 \
  --min-map-cell-match-rate 0.9999 \
  --max-stale-rate 0.005
```

出力はJSON 1件。常に:

```json
{"policy_effect":"none"}
```

を含む。

## eligible_for_review

`eligible_for_review=true` でも、以下を自動実行しない。

- `[nethack.shadow]` の自動有効化
- structured sourceをpolicy inputへ昇格
- TTY sourceの無効化
- gameplay action surfaceの拡張
- config rewrite / service enable

実際のsource昇格を行う場合は、別PRで明示的に設計・テスト・レビューする。

## Mismatch breakdown

`mismatch_fields` は、たとえば:

```text
message
prompt
player
conditions
vitals.hp
vitals.turn
map_rows.length
map_cells
```

ごとの回数を数える。

単純なoverall rateだけでなく、version差・表示タイミング差・glyph semantics差がどこに集中しているかを見るために使う。

## Malformed / ignored data

- JSONとして壊れている行
- schema_version不正

は `malformed_lines`。

`disabled` 等、P4cの比較評価対象外statusは `ignored_events` として数える。

大きすぎる比較ログは読み込まず失敗させる。既定上限は64MiB。

## NLE系sourceの扱い

NetHack 3.6.x系structured sourceとNetHack 5.0 TTYを比較する場合、version差による不一致は当然起こり得る。

P4cは不一致を「どちらが正しい」と判定しない。sourceごとの一致率とfield breakdownを出すだけ。

したがってNLE系sourceが閾値を超えても、それだけで5.0 runtimeのpolicy sourceへ昇格させない。

## 次

P5ではrun/death/xlogfile/dump/advisory履歴を使うretrospectiveとlessons memoryへ進む。

structured sourceのpolicy昇格を試す場合はP4dとして別PRにし、shadowで十分な実測を取ったsourceだけを明示opt-inの実験対象にする。
