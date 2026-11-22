# コーナー異常の自動Issue（corner health monitor）

コーナーの異常終了と自動ローテーションの停止・詰まりを、owner-only の
read-only diagnostics から検知し、固定タイトルの Issue を自動で作成・更新・
クローズする。監視は `.github/workflows/vm-corner-monitor.yml` が10分ごとに
実行し、判定と Issue の重複排除は `ops/vm_actions/corner_health.py` が持つ。

これは復旧経路ではない。コーナーの停止・再起動・予約の解除・台帳の編集は
一切行わない。復旧は既存の `corner-rotation recover` / `recover-failed` /
各コーナーの正規 stop/recover を使う。runtime health の WARN アラート
（`vm-storage-monitor.yml`）は従来どおり別に動く。

## 検知するもの

| 種別 | 条件 | 主な根拠 |
| --- | --- | --- |
| コーナー不正終了 | コーナーstateが `failed` のまま（5分の猶予後）。`recovery_required=true` は期間を問わない | `corners.<corner>.status` / `recovery_required` |
| 失敗の記録 | ローテーションの `failed_corners` に直近の失敗がある。`released-unverified` は24時間、`recovery-required` は現在の latch 中だけ（最大7日） | `corners.corner_rotation.failed_corners` |
| 要復旧ラッチ | `corner_rotation.status == recovery_required` | `corner_rotation.status` / `error_kind` |
| 待機の詰まり | `waiting_overdue=true`（同じ reason のまま `next_due_at` を10分以上超過） | #4333 で diagnostics に投影済みの signal |
| 解放済みownerの未退役 | `excluded.<corner> == released-owner-unretired` | #2352 / #2354 / #3682 |
| タイマー停止 | `corner_rotation_timer.active=false` かつ `enabled!=false`、最終tickから10分超（時刻不明も対象） | `corners.corner_rotation_timer` |
| tick停止 | `ready` / `waiting` かつ予約なしで最終tickから2時間超 | `last_seen_at` |
| 予約のowner不在 | `pending` の owner が `none/absent/unknown` で15分超 | `pending_owner` / `pending_age_sec` |
| 手動予約のowner不在 | `manual_pending` の owner が `none/absent/unknown` で6時間超 | `manual_pending_owner` / `manual_pending_age_sec` |

`waiting_overdue` は pending / manual_pending がある間は使わない。長いコーナーの
実行中に通常の待機を異常と誤認しないため。`timer.active=false` でも
`enabled=false`（明示的な無効化）は対象外にする。

## 判定の境界

- 失敗直後の1tickで解消する一時的な失敗を通知しないよう、state の `failed` は
  5分の猶予後に判定する。`recovery_required=true` は即時。
- 古い `failed` state が残留しても、7日を超えていれば `recovery_required` 以外は
  一覧から外す。監視導入前から残っている失敗で永久に Issue を開き続けないため。
  失敗時刻が欠落した state は経過を判定できないため対象に残す。
- ローテーション監査の `released-unverified` は「失敗を記録して継続した」記録なので
  24時間だけ一覧に残す。
- `recovery-required` の監査行は latch が解消した後も ledger に残るため、現在の
  `corner_rotation.status == recovery_required` の間だけ現在の異常として扱う
  （最大7日）。latch が解消していれば履歴として扱い、state の `failed` だけで判断する。
- 同じコーナーについて state の `failed` と監査行がある場合、state の `game` が
  監査行の corner id と一致すれば1行に統合する（`nethack_corner` と `nethack` など）。
  一致しない場合は別の根拠として本文に併記する。監査行が state の失敗より古い場合は、
  その理由コードで現在の失敗を上書きしない。

## Issue のライフサイクル

- タイトルは固定:
  - `[corner] 不正終了アラート`
  - `[corner rotation] ローテーション異常アラート`
- 異常があるとき: 同じタイトルの open Issue を本文更新する。無ければ1件作成する。
- 異常が無いとき: 同じタイトルの open Issue を固定コメント付きで全て close する。
- タイトル検索は完全一致で行い、同じタイトルの Issue を増やさない。
- 本文は診断スナップショットが変わったときだけ更新する。検知時刻・固定enum・安全な
  corner id・経過秒だけを載せ、生のstate本文・例外本文・path・seed・
  credential は載せない。
- owner が手動で close しても、異常が続く限り次回の実行で再作成される。

## 失敗時の挙動

- diagnostics の取得・JSON解析・`corners` / `corner_rotation` の欠落、
  または Issue 一覧の照会失敗では **exit 2** で終了し、Issue の状態を一切
  変更しない。判断できないときに既存アラートを閉じて異常を隠さない。
- Issue の作成・更新・close に失敗した場合は **exit 3**。状態は次回実行で収束させる。
- workflow は診断の生データ・stderr・pathをログやIssueへ出さない。

## 限界

- 対象は diagnostics が `corners` に固定投影するコーナーstateのみ。puyo / trpg など
  未投影のコーナーは対象外で、必要なら diagnostics 側の投影追加を別途行う。
- GitHub Actions の `schedule` 自体が遅延・停止すると検知も遅れる。この監視は
  「気づき」の経路であり、配信の復旧手段ではない。
- 10分間隔のため、5分未満で自己回復する失敗は通知しない（意図した猶予）。
- `corner_rotation` が diagnostics の hard ceiling で丸ごと落ちた場合は
  「判断不能」として何も変更しない（既存Issueは開いたまま）。
- この監視自体の沈黙は `vm-monitor-silence-alert.yml` の対象外。runtime health
  アラートと併用し、必要なら別途 follow-up とする。

## 検証

ローカル:

```console
python3 -m unittest ops.vm_actions.tests.test_corner_health -v
```

owner は main で `workflow_dispatch` を実行して、次を確認できる。

- 異常が無ければ既存の2タイトルの Issue が close される。
- 異常があれば同じタイトルの Issue が1件だけ開き、本文に固定fieldだけが出る。
- 診断が取得できない run では Issue が変わらない。
