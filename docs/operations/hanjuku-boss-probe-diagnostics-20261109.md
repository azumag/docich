# 半熟英雄: ボス探索候補の回転を診断へ投影する（v545、2026-11-09）

## 背景

v544 で推定ボスセルの探索候補を「細かい輪 → 粗い輪」の9点に変えましたが、
実機でどの候補を使っているかを owner-only 診断から確認できませんでした。
`boss_step_probe`（step → 候補index）は policy にあるのに投影していません。

## 変更（v545 `hanjuku-chart-v545-boss-probe-diagnostics`）

`collect_diagnostics._compact_boss_probes(probes)`:

- `steps`: 候補が割り当てられている step 数
- `indices`: 実際に使われている候補 index（昇順・重複なし・先頭12件）
- 未設定は `None`。step 名・将軍名などの自由文は出しません。

compact policy に `boss_probes` として追加します。

## 確認

- `ops/vm_actions/tests/test_collect_diagnostics.py` に1件追加:
  4 step の割当（0/1/1/不正値）が `{'steps': 4, 'indices': [0, 1]}` に投影され、
  未設定は `None`。
- 同ファイルの boss 関連3件 passed。
- 本番反映後: 第6話ボスで `boss_probes.indices` が 0→1(細かい輪) と進むかを実測する。
