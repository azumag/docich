# 半熟英雄: bot の記憶ファイルが自分の読込上限を超えて詰まるのを直す（v675、2026-11-11）

## 症状（実測 20:19–20:27）

`hanjuku_bot.json`（bot の記憶そのもの）が **265,143 バイト**まで育ち、`BOT_STATE_LIMIT`
（256 KiB = 262,144 バイト）を超えました。以降の観測はすべて次のエラーで落ちました。

```
hanjuku-bot: policy_error AdapterError('RetroArch runtime record is not a bounded regular record')
  at retroarch_boundary.py:26:read_record < bot.py:675:main
```

- `agent22.out` の `警告: brain が異常終了` は **200 回**、その間 `0 件のアクション`
- 入力が止まったため画面が凍結し、`unchanged_seconds` 300 秒で `screen_stalled` をラッチ
- bot は「読み込みに成功したときだけ書き戻す」ため、**記録が自分で縮められない膠着**でした
  （復帰は手作業で `hanjuku_bot.json` を剪定 → `docich hanjuku-clear-terminal` のみ）

## 原因

1. 記憶の書き戻しにサイズ上限が無く、無制限に育つ（観測キャッシュ・退役した指示・履歴）。
2. 読み込みは上限超過をそのままエラーにするため、上限を超えた瞬間に自己修復できなくなる。

## 修正（v675 `hanjuku-chart-v675-state-budget`）

`src/docich/hanjuku/state_budget.py` を追加し、brain の読み書きに挟みます。

- **`load_state`**: 上限超過の記録も**1回だけ**は読む（通常ファイル・シンボリックリンク不可・
  4 MiB の復旧上限）。読めた時点で次の書き戻しが圧縮するため、膠着から抜けられます。
- **`compact_state`**: 書き戻し前に、読込上限の 3/4 を目標に次の順で削ります。
  1. 観測キャッシュ（`obs_cost` / `post_month_status_last` / `general_statuses`）
  2. 退役した指示（`failed` / `done`）のうち、**どこからも参照されていない** step id だけ
     （`world.sorties` / `world.launched_orders` / `world.launched` / `plan.order_context` /
     `plan.sortie_general` / `policy.flow` を参照集合として確認）
  3. 追記型履歴（`sortie_history`）を新しい 20 件に

現在の月・進行中の指示・行軍中の部隊・従軍将軍は削りません。呼び出し元の state は変更せず、
コピーを圧縮して書き戻します。

## 確認

- `tests/test_hanjuku_state_budget.py`（9ケース）: 小さい記録はそのまま／キャッシュが先に落ちる／
  生きている指示・参照済みの退役指示は残る／履歴は新しい側を残す／上限超過の記録も1回は読める／
  復旧上限を超える記録とシンボリックリンクは拒否／不在は空状態。
- 半熟スイートをCIで実行。
- 本番反映後: `hanjuku_bot.json` が上限未満に保たれ、`policy_error` が出ないことを実測する。

## 残件

- 記憶を膨らませている主因は第9話の機械出撃の空回り（指示の作成→取り消しが毎観測）で、
  issue #3944 として分離しました。本修正はその成長を止めるものではなく、上限内に収めます。
