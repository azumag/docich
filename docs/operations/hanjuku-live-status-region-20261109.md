# 半熟英雄: ライブ状況が宣言領域（world）の sorties を読めていなかった（v566、2026-11-09）

## 症状（オーナー報告 15:52）

「ミニマップに出撃チャート表示されなくなってるような？」

## 原因

宣言キー（`sorties` / `garrison` / `boss_victories` など）は状態領域 `world` へ移され、
policy のトップレベルから消えます（docich#3193 の明示移行）。

- `ops/vm_actions/collect_diagnostics.py` は移行後の `world.sorties` も読むよう
  フォールバック済みでした。
- **`ops/vm_actions/hanjuku_live_status.py` はトップレベルのキーしか読んでおらず**、
  出撃(sorties)・在城(garrison)・ボス勝利(boss_victories)が常に空になっていました。
  その結果、ライブ状況（＝配信カードの元データ）から**出撃チャートの行が消えて**いました。

実測: `hanjuku_bot.json` の `policy` は `sorties` をトップレベルに持たず、
`policy.world.sorties` に3件持っていました（15:52 時点）。

## 修正（v566 `hanjuku-chart-v566-live-status-region`）

`hanjuku_live_status._region(policy, key)` を追加し、`march` / `boss_victories` /
`sorties` / `garrison` を**トップレベル → `world` の順**で読みます
（両方ある場合はトップレベル優先、従来の挙動を維持）。

## 確認

- `ops/vm_actions/tests/test_hanjuku_live_status.py` に1件追加: フラット形式と
  `world` 形式で同じ `boss_sorties` / `generals_home` / `boss_wins` になる。
- 同ファイル10件 passed。
- 本番反映後: 配信のライブ状況に出撃行が戻ることを実測する。
