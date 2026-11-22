# 月次確認の地図明け渡しに「試行なし」の上限を入れる (v1356)

## 症状（本番 g1288 ch10）

月次監査が **出撃ゼロ月** と **3ヶ月野営放置** を連続して記録していた。

| 月 | 記録 | シグナル |
|---|---|---|
| 93-2 | `zero_sortie` | `monthly_scan_hold`, `battle_grind` |
| 93-1〜93-4 | `camp_abandoned` | `no_source`, `monthly_scan_hold` |

野営の例: ナストリーニ（HP 66/66、野営3ヶ月、城候補ピランドリア＝ボス城ではない）。

## 実測

```
month_sorties     = {"launches": 0, "scope": [10, "93-4"]}
post_month_status = {"stage": "await_map", "steps": 15, "names": [], "tasks": [],
                     "floor_yields": 3, "complete": false}
```

## 原因（コード）

`flows/post_month_status.py` の「出撃ゼロの月はマップを出撃計画へ明け渡す」
（docich#4642）は、`next_order(mem)` が **ある** ことだけを条件にしていた。
しかし出撃元の将軍が全員野営・敗走中だと、その指示は **開始できない**。
明け渡しを繰り返すあいだロスター巡回が走らないため、帰還タスクが積まれず、
野営将軍は野営のまま → 出撃元が無いまま月が終わる、という循環になる
（`floor_yields` の上限 48 は「開始できない指示」を止められない）。

## 変更

- `SORTIE_FLOOR_IDLE_YIELDS = 3` を追加。
- 出撃の試行が流れている（`flow.sortie_attempt` / `flow.launch_probe` /
  `flow.deploy_holds` のいずれか）間は従来どおり明け渡す。
- 試行が無いままの明け渡しが 3 回に達したら、**マップを月次確認へ戻す**。
  巡回が走り、帰還・修理・募集のタスクで出撃元を作る。
- 戻したことは `post_month_sortie_floor_reclaimed` として 1 回だけ記録する。

## 変えないこと

- 明け渡しの総上限 `SORTIE_FLOOR_YIELDS = 48` と、初回の明け渡し（ログ含む）。
- ボス波・築城などの既存の延期条件。

## 検証

- `tests/test_hanjuku_post_month_status.py` に 2 件追加:
  - 試行なしの明け渡しが `SORTIE_FLOOR_IDLE_YIELDS` 回で止まり、次のマップ観測で
    月次確認が X を押す（`post_month_sortie_floor_reclaimed` を記録）。
  - 試行が流れている間は上限を超えて明け渡し続け、idle カウンタは凍結する。
- 同ファイル **113 passed**。
