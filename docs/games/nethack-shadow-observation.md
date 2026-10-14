# NetHack structured shadow observation (P4a)

P4aでは、現在のNetHack runtimeを置き換えず、外部のstructured sourceが生成した **公開情報だけのsnapshot** をTTY正規化結果と比較する。

shadowはtelemetry専用で、gameplay policy/actionへは入れない。

## Data flow

```text
NetHack 5.0 runtime
  -> TTY capture
  -> normalize_tty
  -> deterministic policy + safety guard
  -> gameplay Action

external structured source
  -> public-only JSON snapshot
  -> strict schema validation
  -> TTY public observationと比較
  -> mismatch metrics / JSONL
  X policy inputではない
```

brain内の順序も、policy決定とsafety guardの **後** にshadow比較を行う。

## Snapshot schema

既定の入力先:

```text
<state_dir>/nethack/shadow/latest.json
```

例:

```json
{
  "schema_version": 1,
  "source": "structured-adapter",
  "captured_at": 1789540000.0,
  "public": {
    "message": "You see here a potion.",
    "map_rows": ["....@...."],
    "player": [4, 0],
    "vitals": {
      "hp": 10,
      "hp_max": 12,
      "power": 4,
      "power_max": 4,
      "ac": 5,
      "experience_level": 2,
      "dungeon_level": 3,
      "gold": 7,
      "turn": 123
    },
    "conditions": [],
    "prompt": "none"
  }
}
```

## Hidden-information leak防止

schemaは未知keyを許可しない。

たとえば以下のようなfieldを追加するとsnapshot全体をinvalid扱いにする。

```text
monster_id
peaceful
true_identity
hidden_map
object_id
rng_seed
```

conditionsもTTY公開契約で既知のvisible conditionだけを許可する。

structured sourceが内部的により多くのengine stateへアクセスできても、Docichへ渡すadapter段階で公開情報だけに落とす。

## Comparison

比較対象:

- message
- prompt
- player座標
- visible conditions
- HP / max HP
- Pw / max Pw
- AC
- Exp
- Dlvl
- gold
- turn
- visible raw map glyphs

差分は「TTYが正しい」「structuredが正しい」と自動判定しない。

```text
match
mismatch
stale
invalid
missing
```

として記録するだけ。

ログ:

```text
<state_dir>/nethack/shadow/comparisons.jsonl
```

`policy_effect = none` を明示する。

## Freshness

structured snapshotは既定5秒以内のみ比較する。

古いsnapshotを現在のTTYと比較して大量の偽mismatchを出さないため、`captured_at` と `max_age_s` を使う。

## 標準設定

```toml
[nethack.shadow]
enabled = false
path = ""
max_age_s = 5.0
max_bytes = 131072
```

既定は無効。mergeだけでstructured sourceを読み始めることはない。

## P4aで行わないこと

- structured snapshotをpolicyへ渡す
- TTYよりstructured sourceを優先する
- monster hostility/identityをengine内部値から取得する
- 未鑑定itemのtrue identityを取得する
- NetHack runtimeを別versionへ置換する
- shadow mismatchを理由にgameを停止する

## 次

P4bでは外部structured source用のadapter processを用意し、同一場面をTTY/structuredの両方から収集して一致率を測る。

十分な一致率とhidden-information leak testを通した後でも、policy sourceを切り替える場合は別PR・明示opt-inとする。
