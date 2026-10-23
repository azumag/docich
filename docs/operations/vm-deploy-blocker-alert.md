# VM deploy blocker alert（production deploy 停止の独立検知）

docich#2487 の検知経路。`main` の push deploy（`vm-operations.yml`）が失敗したまま
でも、失敗は workflow run の赤い × としてしか残らず、インシデントにならない。
docich#2482 では 5 連続 deploy（約5日）がその状態で放置された。

本検知は、その「deploy が詰まっている」状態そのものを独立に監視する。

## 実体

| 項目 | 値 |
|---|---|
| workflow | `.github/workflows/vm-deploy-blocker-alert.yml` |
| script | `ops/vm_actions/reconcile_refused_alert.py` |
| 実行 | 毎時 `:09` / `:39`（storage monitor の `:17`/`:47`、silence 検知の `:29` と衝突しない） |
| 権限 | `contents: read`, `actions: read`, `issues: write` |
| 実行条件 | `main` / protected / schedule または owner の workflow_dispatch |

## 判定

1. `vm-operations.yml` の `main` push run を新しい順に最大 20 件取得する。
2. 直近 `RECONCILE_ALERT_LOOKBACK_MINUTES`（既定 90 分）以内で、**最新 run が失敗**して
   いれば「deploy が詰まっている」と判定する。より新しい成功 run があれば復旧とみなす
   （失敗は履歴であり現在のブロッカーではない）。
3. 失敗 run の failed-step log から、gateway の**固定 reason enum** のみを抽出する
   （`reconcile_refused` / `reconcile_fetch_failed` / `reconcile_helper_missing` /
   `reconcile_object_missing` / `reconcile_invalid_request` /
   `reconcile_production_only` / `projected_submodule_*` / `operation_rejected`）。
   一致しなければ `deploy_failed` を報告する。log 本文・path・secret は Issue に
   出さない（固定 field のみ）。

## Issue の dedup と復旧

- タイトル `[VM deploy] production deploy blocked` の open Issue を 1 件だけ保つ。
  既にあれば本文を現在の run / reason / SHA で refresh する。
- それ以降の main push deploy が成功すると自動で close する。
- クエリ失敗時は fail-closed（既存の alert state を変更しない）。

## 復旧手順

reason が `reconcile_*` 系なら、VM checkout の live bytes が reviewed lineage と
一致していない。docich#2482 と同じ手順で解消する。

1. 失敗 run の old_root / old_sub / new_sub と、VM live の sha256+mode を比較して
   差分 path を特定する（read-only）。
2. 差分を reviewed bytes へ upstream する（byte-exact PR → soviet_now main merge →
   docich `games/soviet_now` gitlink advance → production deploy）か、reviewed bytes
   へ収束する。
3. 次の push deploy 成功で本 Issue は自動 close される。

恒久対策として、soviet_now 側に projection live-apply gate
（`core/strategy_runtime.sh`、`docs/projection_guard_runbook.md`）があり、
`IMPROVE_LIVE_APPLY_POLICY=enforce_reviewed` で未 review bytes の live 書き込みを
停止できる。

## 既存 alert との役割分担

| alert | 見るもの |
|---|---|
| `[VM deploy] tracked drift alert`（storage monitor） | VM の tracked drift 状態（診断の read-only 出力） |
| `[VM deploy] production baseline alert`（storage monitor） | production status が configured から外れた状態 |
| **`[VM deploy] production deploy blocked`（本検知）** | **main push deploy 自体の失敗と、その reason** |

## 関連

- docich#2482: 5 連続 deploy 失敗のインシデント。
- docich#2487: 再発防止（本検知 + soviet_now projection guard）。
- `docs/operations/vm-monitor-silence-alert.md`: monitor 沈黙の独立検知（同型の設計）。
- soviet_now `docs/projection_guard_runbook.md`: live bytes を reviewed lineage に
  保つ runtime 側の恒久対策。
