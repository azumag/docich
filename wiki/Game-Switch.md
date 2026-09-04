# ゲーム切替機構

`display` / `audio` / `stream` を止めずに `game` (と `agent`) window だけを安全に入れ替えるための
トランザクション機構。issue [#47](https://github.com/azumag/docich/issues/47) の Design v2 を
実装したもので、全フェーズが main に統合済みである (PR #48–#61)。一次情報は
`src/docich/game_switch.py` と issue #47。本ページは要約であり、食い違いがあればコード側が正しい。

使い方の日常的な部分は [[日常運用|Operations]] も参照。

## 設計の骨子

- **配信を止めない**: 切替対象は `game` / `agent` window のみ。`display` / `audio` / `stream`
  は常駐したまま ([[アーキテクチャ|Architecture]] の基本方針どおり)。
- **状態の正本は 1 つ**: canonical (`run/game_switch.json`) に phase・generation・receipt を持ち、
  残りの 3 層 (互換 mirror / 実プロセス / agent fence) と食い違っていないかを `status` で
  個別に見られる。canonical が無ければ legacy 扱いの gate が働く (後述)。
- **失敗したら戻す**: 切替は prepare → quiesce → start → probe の順に進め、どこかで失敗したら
  旧ゲームを復元して `rolled_back` で終わる。commit 後の失敗は `recovery_required` になり、
  `docich recover` で収束させる。
- **冪等**: 同一 `request_id` の再送は同じ結果 (receipt) を返す。異なる payload での再利用は
  `request_conflict` で拒否する。
- **競合は排除**: 排他ロック (`run/locks/game-switch.lock`) 下で canonical を transaction 的に
  更新する。並行要求は `busy` になる。

## コマンド

| コマンド | 動作 |
|---|---|
| `docich up` / `down` | 基盤 (display/audio/stream) の起動 / 全停止 |
| `docich start <game>` | 起動。同名が稼働中なら no-op 成功、別ゲーム稼働中は switch へ誘導 |
| `docich switch <game>` | 切替 (旧停止 → 新起動、失敗時は旧ゲームを自動復元) |
| `docich stop` | 現在のゲームを停止 (canonical は idle へ) |
| `docich restart` | 現在のゲームを再起動 (watchdog 復旧用) |
| `docich rotate [--dry-run]` | `[rotation] games` を順に切り替える (時間割ローテーション) |
| `docich recover [--timeout SEC]` | 中断した切替を復旧する (crash/failed 後の再開) |
| `docich migrate-legacy` | 旧 runtime (固定 window/session) を停止して移行する |
| `docich status [--json]` | 4 層の状態を表示する (下記) |

**終了コード契約**: `succeeded`=0 / `busy`・`in_progress`・`rolled_back`=1 / `failed` 等=2。
ユーザー向けエラー (`CliError`) も 2 で終わる。

## canonical と 4 層の状態モデル

| 層 | 実体 | 役割 |
|---|---|---|
| canonical | `run/game_switch.json` (schema v2) | phase・generation・receipt・履歴の正本。全更新はロック下の transaction |
| mirror | `run/current_game` | 旧 CLI との互換用 mirror。canonical から復元される |
| actual | tmux window / プロセス | 実物。`status` が突合して不一致を警告する |
| agent fence | agent window の identity | agent が「今どの generation の何に向いているか」の契約 (下記) |

phase は次の 12 値:
`idle` / `validating` / `preparing` / `quiescing` / `starting` / `probing` / `committing` /
`ready` / `stopping` / `rolling_back` / `failed` / `recovery_required`。
通常の起動・切替は `idle` → `validating` → `preparing` → `quiescing` → `starting` → `probing` →
`committing` → `ready` と進む。停止処理では `stopping` を経由する。失敗時は `rolling_back`
(receipt は `rolled_back`) または `failed`、commit 後に安全な収束が必要な場合は
`recovery_required` になる。

receipt は終端状態 3 値 (`succeeded` / `failed` / `rolled_back`) を持ち、request 単位で
`run/game-switch/requests/` に残る。各成功起動は generation を進め、receipt に記録する。

`docich status --json` の例 (アイドル時):

```json
{"schema_version": 1, "session_alive": false,
 "windows": {"display": false, "audio": false, "stream": false, "game": false, "agent": false},
 "canonical": {"present": false, "phase": "idle", "next_generation": 1, "active": null, ...},
 "mirror": {"game": null, "matches_canonical": null},
 "actual": {"active": null, ...}, "agent_fence": {"tuple": null, "agent_window_present": null}}
```

## 冪等性と request 契約

- `start` / `stop` / `switch` / `restart` / `rotate` / `down` は `--request-id UUID` を取り、
  同一 request の再送は同じ receipt を返す (at-least-once で安全)。`recover` は既存状態を
  reconcile する復旧操作で、CLI の `--request-id` は取らない。
- 同一 `request_id` の別 operation は原則 `request_conflict`。ただし
  `caller=start` / `recorded=switch` で同一 target・同一 payload の場合は sanctioned alias として
  許容する (start の誘導経路のため)。
- 排他ロックを取れない場合は `busy` (終了コード 1)。エラーコードは
  `invalid_game` / `already_active` / `no_active_game` / `prepare_failed` / `quiesce_failed` /
  `start_failed` / `probe_failed` / `readiness_timeout` / `rollback_failed` / `state_corrupt` /
  `timeout` / `internal` 等を receipt に持つ。

## agent fence

agent window には稼働中ランタイムの identity (game・generation 等の tuple) を渡す。brain 側
worker は行動注入前に fence を検証し、identity が active と一致しない (切替が起きた等) 場合は
`FenceLost` (exit 75) で即終了する。古い世代の agent が新しいゲームへ誤入力するのを防ぐ。
fence の実装は `src/docich/agent/fence.py`。

## イベントログ

state_dir 下 `logs/game_switch.log` に JSON 行で追記する (phase 遷移・receipt・エラー)。
`detail` は書き込み時にマスクされ (URL 全体・argv/command・Authorization 等の認証情報・
`key=value` 形式・長いトークン)、機密がログへ流れない。receipt の canonical 表現は生のまま。

## legacy からの移行

coordinator 導入前の旧 runtime は「固定名の `game` / `agent` window + `docich-game` session +
`run/current_game`」で管理していた。移行は:

1. `docich migrate-legacy` — 旧 runtime を停止し、互換 mirror を消去する。
2. 以降は start / switch 等が canonical を作って coordinator 世界で動く。

canonical が未作成の状態で legacy 痕跡 (mirror や確認不能な tmux 状態) があると、
`down` / `start` / `stop` / `switch` / `restart` / `recover` / `rotate` (非 dry-run) は
`migrate-legacy` を指示して fail-closed する
(`recover` が idle canonical を勝手に作って legacy を移行済みに見せかける経路を塞ぐため)。
canonical 作成後は legacy footprint は operator 管理扱いで許可される。

## recover (crash / 中断後の復旧)

`docich recover [--timeout SEC]` は coordinator の recovery フローを実行する:

- 中断 canonical の rollback 収束 (`recovery_required` / `rolling_back` 等の後始末)
- dangling receipt の reconcile
- retiring window の再試行・実物との突合、mirror 修復

idle なら「復旧は不要でした」で成功 (終了コード 0)。canonical が破損している等で安全に復旧
できない場合は traceback ではなくユーザー向けエラー (終了コード 2) で fail-closed する。
watchdog からの自動復旧は `docich restart` (こちらはゲームの再起動) の役割分担。

## 既知の制限

- **replace モードのみ実装** (旧を止めてから新を起動)。parallel 切替 (新を先に起動してから
  旧を止める) は issue #47 の設計にはあるが未実装。
- browser / retroarch アダプタは実機 smoke 未検証 (chromium・ROM の無い環境で検証したため)。
  `cli` (nethack) は実機検証済み。
- VM (Oracle ARM) への反映は 2026-09-04 済み。`doctor` / `status --json` が正常動作することを
  実機確認している (詳細: `handoff.md`)。
