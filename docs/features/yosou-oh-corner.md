# 予想王コーナー — チャネルポイント予想の的中率ランキング（設計案）

- Status: **PR-1（オフライン中核）／PR-2a（半熟英雄の記録フック）／PR-2b（Soren の記録フック）／
  PR-3（`yoso` adapter・view・catalog登録）／PR-4（読み上げ・eventOverlay・コーナー紹介）実装済み**。
  **PR-5（不成立キャンセルの参加実績と5指標ランキングの中核、§2.1 / §3.1、Issue #933 追記）実装済み**。
  production catalog は **2026-10-14 オーナー承認で `enabled = true`**、**2026-10-15 オーナー判断で
  `manual_only` を解除して24時間 rolling rotation の自動抽選に組み入れ**（手動予約も従来どおり可能）、
  **2026-10-15 オーナー判断で `audio_enabled = true`**
  （コーナー紹介と上位3名の読み上げ）。有効化・自動抽選・読み上げは
  `config/docich.soren-live.toml` の当該行の変更で行う（§4参照）。TwiCa 前景への表示は
  **2026-10-15 のオーナー判断で対象外**（§4.1。docich 側は共通 eventOverlay までを担当する）。
  **PR-6（Soren 側の不成立キャンセル記録、§2.1 / §8-2。soviet_now PR ＋ docich gitlink）実装済み**。
  5指標ボードの画面表示（1枚の表・18秒切替）は #2435 / #2436 で main 済み。読み上げの部門対応は
  対象外（§8-5 (c) 不採用）。
- 関連 Issue: [azumag/docich#933](https://github.com/azumag/docich/issues/933)（番組コーナー（24時間中ローテーション）に追加）、
  [azumag/docich#9](https://github.com/azumag/docich/issues/9)（自動チャネルポイント予想）
- 関連実装: `src/docich/prediction_leaderboard.py`（記録・集計・ランキングの契約）、
  `src/docich/soren_predictions.py`（Soren handoff の読み取りと記録 tick）、
  `src/docich/yoso_view.py`（読み取り専用 960×540 view）、
  `src/docich/yoso_corner.py`（GameSwitch slot 上の有界な表示 lifecycle）、
  `src/docich/adapters/yoso_program.py`（`yosou-view` program adapter）、
  `src/docich/hanjuku_prediction_api.py` / `src/docich/hanjuku_predictions.py`（半熟英雄の予想）、
  `games/soviet_now/twitch_predictions.sh` / `games/soviet_now/lib/prediction_round.py`（Soren の12試合予想）、
  `src/docich/corner_catalog.py` / `corner_rotation.py` / `corner_adapters.py`（24時間ローテーション）

## 0. 何を作るか

チャネルポイント予想（このチャンネルでは「サナエトークン」）の結果を記録しておき、
**予想的中率のランキング**を番組ローテーションの1コーナーとして表示する。

- 「予想王」= 対象期間で最も的中率が高い視聴者。
- 半熟英雄（`どこまで到達できるか`）と Soren（`12ゲーム中に建国できる？`）の結果を
  共通の台帳形式で蓄積し、1枚のランキングへ集約する。
- 表示は 960×540 を基準にした読み取り専用ビュー（既存 weather / paper と同じ合成 view 方式）＋読み上げ。
  配信では weather と同じく**ディスプレイ全画面**（本番 1280×720）に提示し、960×540 のステージを
  縦横比維持で拡大する（§3「全画面表示」、#2237）。

本ドキュメントは #933 の「統計の記録仕様とランキング表示の仕様を定義し、コーナー追加案として起案」に答える。

## 1. データ源の調査（一次情報）

Twitch Helix `GET /helix/predictions` の outcome に **`top_predictors`** がある。

出典（2026-10-08 確認）: <https://dev.twitch.tv/docs/api/reference/#get-predictions>

| フィールド | 型 | 説明（要約） |
| --- | --- | --- |
| `outcomes[].top_predictors` | Object[] | その outcome の上位予想者（最も多く賭けた視聴者）。該当が無ければ `null`。 |
| `top_predictors[].user_id` / `user_name` / `user_login` | String | 視聴者の id・表示名・ログイン名。 |
| `top_predictors[].channel_points_used` | Integer | その視聴者が賭けたチャネルポイント。 |
| `top_predictors[].channel_points_won` | Integer | その視聴者が獲得したチャネルポイント。未確定（`RESOLVED` 以前）は `null`／0。 |
| `winning_outcome_id` | String | `status = RESOLVED` 以外は `null`。 |

**重要な制約**: 視聴者単位のデータは Twitch が公開する **top predictors だけ** で、
全参加者の一覧は取得できない。したがって本コーナーのランキングは
「**上位予想者**のランキング」であり、全賭け参加者の完全な的中率ではない。
この制約は仕様として画面にも明記する（「上位予想者のみ集計」）。

- 公式リファレンス（2026-10-17 再確認）は `top_predictors` を status で限定していない
  （`channel_points_won` だけが未確定時に `null`）。したがって **不成立キャンセルでも、
  row が上位予想者を持っていれば参加の証拠として記録できる**（§2.1）。
  ただし Twitch が返す範囲は `RESOLVED` の row と同じ「上位予想者」であり、全参加者ではない。
- 上位予想者が返らない row（未確定・取得不能・キャンセルで消えた等）からは
  **視聴者・回数・ポイントを推測しない**。その round は記録できる範囲だけを残す（§2.1）。

既存クライアントは Helix 応答の生 dict をそのまま返すため、追加取得は不要。
ただし `src/docich/hanjuku_prediction_api.py` は `top_predictors` を検証しないので、
**記録時に `prediction_leaderboard.py` 側で検証**する。

現行2系統の予想:

- 半熟英雄: docich `hanjuku_predictions.py` が作成・精算する。`client.end(...)`（PATCH）の応答に
  `top_predictors` が含まれ得る。
- Soren（12ゲーム建国）: `games/soviet_now/twitch_predictions.sh` が PATCH で resolve する。
  応答本文を Soren 側の bounded な引き渡しファイル（`tmp/state/prediction_resolved.jsonl`）へ
  追記し、docich の rotation timer が台帳へ記録する（PR-2b）。

## 2. 統計の記録仕様

- 台帳: `<state_dir>/prediction_leaderboard.json`、`schema = 2`。実装は `prediction_leaderboard.py`。
  `schema = 1`（`RESOLVED` の的中率のみ）の既存台帳は読み込み時に schema 2 へ正規化する
  （全 round を `RESOLVED`・`refunded = 0` として読み、書き出しは schema 2）。
- 追記単位: `rounds[<prediction_id>]` に、**確定した予想を1回だけ**記録する。
  - `prediction id` をキーにした冪等追記。再送・再精算・再起動の tick が二重計上しない。
  - `ACTIVE` / `LOCKED` は記録しない（未確定のため）。
  - `CANCELED` は **不成立キャンセル（`no-contest`）だけ** を参加実績として記録する（§2.1）。
    それ以外のキャンセル（中断・既知結果・結果不明など）は記録しない。
- **賭けが成立していない予想は `CANCELED` にして全額返却する**（2026-10-15 オーナー判断）:
  精算の直前（半熟英雄は `LOCKED` 後、Soren は `resolve` の直前）に `GET /helix/predictions` の
  `outcomes[].users` / `channel_points` を見て、賭けが入った選択肢が1つ以下（0件を含む）なら
  勝者を出さず `CANCELED` にして投票済みポイントを全額返却する。1択しか賭けが無い予想は
  賭けが成立しておらず、その1択が外れた場合に視聴者のポイントだけが失われるため。
  件数が読めないとき（フィールド欠落・取得失敗・応答不正）は**現行どおり `RESOLVED` へ進む**
  （settle を新しい理由で止めない）。半熟英雄は `hanjuku_predictions._no_contest`、
  Soren は `twitch_predictions.sh` の `_contested_outcome_count` が同じ規則を実装する。
- 1 round の内容:
  `{ corner, title, ended_at, status, winning_outcome_id, cancel_reason?, outcomes[{id,title,users,points}], predictors[] }`
  - `status` = `RESOLVED` または `CANCELED`。`cancel_reason` は `CANCELED` のときだけ入り、
    値は固定語彙 `no-contest` のみ（`prediction_leaderboard.CANCEL_REASONS`）。
  - `CANCELED` の round は `winning_outcome_id = null`、`cancel_reason` 必須。理由の無い
    キャンセルは保存を拒否する（無言のゼロとして残さない）。
- 1 predictor の内容:
  `{ user_id, user_login, user_name, outcome_id, used, won, refunded, hit }`
  - `used` = `channel_points_used`、`won` = `channel_points_won`。
  - `hit` = 「その視聴者が `winning_outcome` を選んだか」（`outcome_id == winning_outcome_id`）。
    `CANCELED` の round では勝敗が確定しないため **`null`**。
  - `refunded` = 返金された元本。`CANCELED` では `used` と同額、`RESOLVED` では 0。
- 記録タイミング: 精算確定後に `RESOLVED` を観測した tick、および不成立 `CANCELED` を
  確定・再観測した tick。書き込みは予想所有者が行う。半熟英雄は docich の予想 tick が
  直接記録し、Soren は resolve 時に自身の handoff ファイルへ追記して docich の
  rotation timer（`soren_predictions.tick`）が記録する。
- 書き込み経路: `prediction_leaderboard.record_settled(state_dir, row, corner=..., cancel_reason=...)`
  が唯一の入口（`record_resolved` は resolved handoff 用の互換名で、内部でこれへ委譲する）。
  `<state_dir>/locks/prediction-leaderboard.json.lock` の排他 lock の下で read-modify-write し、
  `save()` が temp file + `os.replace` で atomic に置換する。同じ `prediction id` の再送は no-op。
  壊れた台帳・壊れた row は例外で拒否し、既存の台帳を上書きしない（fail-closed）。
- 記録に失敗したとき: 半熟英雄の tick は固定コード `leaderboard`（台帳の read/write 失敗）または
  `invalid_response`（row が契約違反）で error mode に入り、台帳書き込みの失敗は同じ prediction id を
  次の観測で再試行する。`invalid_response` の row は再試行せず記録しない。
- 保持: 最大 `MAX_ROUNDS = 2000` 件。超過時は `(ended_at, id)` の古い順に間引く。
- 入力上限: 台帳 512 KiB、1 outcome あたり predictor 100 件、文字列 128 文字。
  保存も schema 移行後の最終 UTF-8 バイト列（末尾改行込み）を 512 KiB で検査し、
  超過時は元ファイルをバイト単位で保ったまま明示的に拒否する。サイズを理由に参加履歴を
  追加で間引かない。拒否後も元台帳は読めるが、追記は容量の扱いを別途判断するまで再試行になる。
- 検証: `hit` を誤記録しないよう、壊れた resolved row は **fail-closed**（例外）で記録しない。
  非 resolved の row は静かにスキップする。
- プライバシー: 保存・表示する視聴者情報は Twitch が公開する `id / login / 表示名` と
  賭けポイントのみ。トークン・IP などは保存しない。診断（`stats`）へは **件数のみ**を出し、
  視聴者名は出さない。

### 2.1 不成立キャンセルの参加実績（Issue #933 追記、2026-10-17）

「対立する投票が無い」不成立キャンセルでも、**証拠のある参加は残す**。勝敗が確定して
いないので的中・外れには数えず、返金は元本返却として扱い、利益・損失には二重計上しない。

- 記録の根拠: 所有者が確定・再観測した `CANCELED` の row。`top_predictors` があれば
  視聴者ごとの `used` / `refunded` を残し、無ければ round と outcome の件数だけを残す。
  **row に無い視聴者・回数・ポイントを作らない**。
- 記録経路（半熟英雄）: 不成立と確定して `CANCELED` を PATCH した直後、および
  既に `CANCELED` の row を再観測した時に `hanjuku_predictions._record_canceled` が
  `cancel_reason = "no-contest"` で記録する。PATCH 応答に `top_predictors` が無いときは
  権威ある `LOCKED` の row を `CANCELED` として再利用し、それも無ければ同じ id を1回 GET する。
- cancel 前に、所有する予想 ID・outcome ID・タイトル・不成立条件と台帳 row 契約を検証した
  `LOCKED` snapshot を `<state_dir>/hanjuku_prediction_cancel_<予想IDのSHA-256>.json` へ
  `CANCELED` の記帳候補として保存する（この時点では台帳へ記帳しない）。上限は改行込み
  UTF-8 256 KiB。0600・一時ファイル・fsync・原子的置換を使い、保存失敗・上限超過時は
  cancel へ進まない。既存の予想状態は16 KiBのままで、snapshot を埋め込まない。
- 再起動後も同一予想 ID の snapshot だけを再検証して再利用し、CANCELED の PATCH/GET が
  参加者や件数を省略しても既知の参加を失わない。別 ID・別 outcome・壊れた snapshot は
  空の参加として記帳せず拒否する。LOCKED の再試行応答が参加者を省略しても既存証拠を上書きしない。
  snapshot は台帳記録の成功（同一 ID が記録済みの冪等 no-op を含む）後だけ削除する。
  削除失敗時も再試行でき、再記帳しない。row 契約違反等で記録しなかった証拠は保持する。
- 記録に失敗したとき（`leaderboard`）: round を terminal にせず次の観測・再試行 tick で
  再記録する。同じ `prediction id` は一度しか記録しない（再起動・再読取でも二重計上が無い）。
- round 契約違反（`invalid_response`）: 記録せず、その round を terminal にして再試行しない
  （`RESOLVED` の契約違反と同じ扱い）。
- 記録経路（Soren、PR-6・2026-10-20 オーナー承認）: Soren の `twitch_predictions.sh` が
  不成立（`_contested_outcome_count <= 1`）と判定して `CANCELED` に返却したとき、
  PATCH 応答 row に固定理由 `no-contest` を付けて既存の
  `<soren_root>/tmp/state/prediction_resolved.jsonl` へ追記する
  （`tools/prediction_resolved.py` の `--cancel-reason`。語彙は `CANCEL_REASONS` と同一）。
  docich 側は `soren_predictions.read_handoff` が**理由付きの `CANCELED` row だけ**を受け取り、
  `record_settled(corner='soren', cancel_reason='no-contest')` で1回だけ記録する。
  理由の無い `CANCELED` row、および理由を持つ `RESOLVED` row は handoff 全体を拒否する
  （fail-closed・部分記録なし）。手動 `cancel` コマンドは不成立返金ではないため記録しない。
  Soren 側の記録失敗は cancel 自体を失敗させない（警告のみ。参加実績は次の不成立キャンセルから）。
- 対象外: 不成立以外のキャンセル（中断・既知結果・結果不明など）。§8-2 のとおり
  `no-contest` のみが対象で、他の理由は今後も記録しない。
- まだ約束しないこと: 既存データに参加者・投票量・正解の証拠が無い round は復元しない。
  欠損を 0 や未参加で埋めない。

## 3. ランキング表示の仕様

集計は `user_id` 単位（表示名・ログイン名が変わっても同一人物として合算）。

- `rounds` = 登場回数、`hits` = 的中回数、`hit_rate = hits / rounds`、
  `used` / `won` = 合計賭け／獲得ポイント、`net = won - used`。
- **最小参加数 `min_rounds`（既定 3）** 未満は圏外。一発屋を王にしない。
- 並び順: `hit_rate` 降順 → `hits` 降順 → `net` 降順 → `user_login` 昇順（同率の決定的順序）。
- 表示: 上位 `limit`（既定 10）件。`順位 / 表示名 / 的中率(%) / 参加数 / 獲得ポイント`。
- 集計期間: 台帳全体（全期間）。将来「直近 N 日」窓を追加できる。
- 更新: 新しい予想が `RESOLVED` になるたび再計算。進行中の予想があるときは前回確定分を表示する。
- コーナー紹介: corner 開始直後に、固定の紹介文を overlay と読み上げの両方へ1件ずつ出す
  （`yoso_narration.INTRO_TEXT` / `INTRO_OVERLAY_BODY`。他コーナーの `ANNOUNCE_TEXT` と同じ役割）。
  内容は「チャンネルポイント予想の的中率ランキングのコーナーである」ことと、
  上位予想者だけを集計しているという但し書き。
- 読み上げ: 上の紹介文と、上位 3 件の実況の**2発話**。視聴者の呼称は既存契約に合わせ「同志◯◯」。
  実装は `yoso_narration.narration_text` / `INTRO_TEXT`（各 上限300字・1発話）で、
  `audio_enabled` の opt-in があるときだけ共有 audio worker へ enqueue する
  （context は `yosou:intro` / `yosou:ranking`、speaker は既定）。audio worker 側は
  120秒以内の同一文面を重複として落とすため、同じ紹介文の連続発話は保証しない。
- overlay: corner 開始直後に `prediction` category の event を**2件**積む
  （紹介 `yosou:intro:<request>` と ranking `yosou:ranking:<request>`。
  `yoso_narration.overlay_body`、上位5件＋件数、本文は `OVERLAY_BODY_LIMIT` 以内）。
  共有キューは `source_id` ごとに最新1件だけを残すため、紹介と ranking は
  `source_id` を分ける（同じだと ranking が紹介を上書きする）。
- 終了条件: `duration_minutes`（既定2分・1〜14分）は**上限**で、読み上げを入れた run は
  自分の発話が共有 audio queue から消えた時点で `end_reason = "audio-complete"` として早期終了する
  （天気予報 corner の音声完了と同じ考え方。2026-10-15 オーナー判断）。
  queue が混んで発話が上限まで残る run は `end_reason = "duration"` で終わる。
  判定は `yoso_narration.voice_items_in_flight()`（自分の `yosou:intro` / `yosou:ranking` の
  `.txt` / `.playing` マーカーだけを数える）で、他 source のコメントや他 corner は数えない。
- 開始境界（#2236）: 共有 audio queue が完全に静か（`comment_*.txt` / `comment_*.playing` が
  0件）で、Soren の予想が in-flight なら確定境界が来るまで開始しない。待ちは
  `rotation_wait_minutes`（既定10分）で有界で、超えるとその回は `expired` で飛ぶ。
  再生中のコメントの後ろにイントロが割り込まないようにするための、**入り**の条件
  （終了条件の `audio-complete` とは別）。詳細は `docs/corner-rotation.md` の
  「コーナー入りの境界」。
- 翻訳対象外: view は常に日本語表示で、翻訳すると集計の意味が変わる。
  `yoso_view.HTML` は `<html lang="ja" translate="no">` と
  `<meta name="google" content="notranslate">` を持ち、日本語UIのブラウザが出す
  翻訳コントロール（日本語 / English）を出さない（2026-10-15 オーナー判断）。
- 全画面表示（#2237、2026-10-15 オーナー判断）: 天気予報と同じ**配信ネイティブの情報画面**として扱い、
  `display.width/height` が既知なら `(0, 0, width, height)`（本番 1280×720）の全画面で提示する
  （`yoso_program.YosoProgramViewAdapter._viewer_command`。weather の前例 = PR #1874）。
  `--window-size` も同寸法にし、960×540 のステージは view 自身の `fit()` が縦横比維持で拡大する。
  ステータス枠・通知枠の位置・サイズは変更せず、コーナー終了時は既存 GameSwitch の復帰経路
  （`audio-complete` 早期終了でも同じ）で元画面へ戻る。**枠を隠す/戻す専用の経路は追加しない**
  （復帰の二重化を作らない）。`display.width/height` が無い環境では従来の `viewport` へ
  fallback する。

### 3.1 5指標ボード（Issue #933 追記、2026-10-17）

「予想的中率」だけでなく、次の5指標それぞれを独立した順位として集計する。
実装は `prediction_leaderboard.boards(ledger, min_rounds=..., min_participations=..., limit=...)`
が返す5つのリスト（`METRICS = ("hit_rate", "staked", "won", "lost", "plays")`）。

| 部門（key） | 意味 | 集計対象 | 既定の最低数 | 並び順（同率の決定的順序） |
| --- | --- | --- | --- | --- |
| 予想的中率（`hit_rate`） | `hits / rounds` | 勝敗が確定した `RESOLVED` の round | `min_rounds`（既定 3） | 的中率降順 → 的中数降順 → `net` 降順 → login 昇順 |
| 賭けたポイント数（`staked`） | `Σ used` | 全 round（キャンセル含む） | `min_participations`（既定 1） | `used` 降順 → 参加数降順 → login 昇順 |
| 得たポイント数（`won`） | `Σ won` | 全 round | `min_participations` | `won` 降順 → 参加数降順 → login 昇順。`won > 0` のみ |
| 失ったポイント数（`lost`） | `Σ (used - refunded)`（`hit == false` の round のみ） | `RESOLVED` の外れ | `min_participations` | `lost` 降順 → 参加数降順 → login 昇順。`lost > 0` のみ |
| 賭けた回数（`plays`） | `participations` | 全 round（キャンセル含む） | `min_participations` | 参加数降順 → `rounds` 降順 → login 昇順 |

- 不成立キャンセルは `participations`（賭けた回数・賭けたポイント・参加数）には入るが、
  `rounds`（的中率の分母）・`won`・`lost` には入らない。返金 `refunded` は元本返却で、
  `won` にも `lost` にも計上しない。したがって 100 ポイント賭けて全額返金された回は
  「参加 1 回・累計投票 100・返金 100」で、このキャンセルによる利益と損失はともに 0。
- `net = won - used + refunded`。`RESOLVED` の round だけを見る的中率ボードでは
  `refunded = 0` なので従来どおり `won - used`。
- `min_rounds` は的中率ボード専用。勝敗を判定しない部門へ流用しない（Issue #933 追記の指摘）。
  部門ごとの最低数は `min_rounds` / `min_participations` で独立に指定できる。
- 得た・失ったポイントの部門は 0 の行を並べても意味がないため `> 0` の視聴者だけを載せる。
- 表示: `yoso_view` の `/api/yosou` は5部門（`boards`）を返し、画面は**1枚の表を18秒ごとに部門替わりへ切り替える**（§8-5 の (a)、2026-10-17 オーナー判断）。
  表の列は 順位 / 表示名 / 部門の記録 / 的中率 / 純増 で、部門ごとに「記録」列の意味と最低数表示だけが変わる。
  5部門 × 18秒 = 90秒で、コーナー上限2分に収まる。読み上げは従来どおり的中率ボードのみ。集計の意味は本節が正本。
- 休止表示の解除は**取得成功した `render` だけ**が行う（PR #2435 P2）。部門切替の `draw()` は
  古い `lastData` を描き直すだけで、取得状態を変えない。成功 → 失敗 → 部門切替では休止表示を
  維持し、次の成功でのみ解除する（`tests/test_yoso_view.py` が実JSを node で実行して固定）。

## 4. コーナー追加案（番組ローテーションへの接続）

- 新 adapter **`yoso`**（game id `yosou-view`）。`WeatherCornerAdapter` / `TsuitateCornerAdapter` と
  同列の**合成 view**（GameSwitch の program slot に乗る読み取り専用ビュー）。
- ビューは台帳を **read-only** で読み、`予想王` ランキング表を 960×540 のステージへ描画する。
  配信では `display.width/height` の全画面 `(0, 0)` に提示し、コーナー中だけステータス枠・通知枠を
  覆う（枠自体の位置・サイズは動かさない。#2237）。
  既存ゲーム本体・配信 encoder・Soren の予想 worker は変更しない。
- catalog（起案時は**手動予約のみ**で提案していたが、2026-10-15 のオーナー判断で自動抽選にも入る）:

  ```toml
  # 予想王: 予想的中率ランキング。自動抽選＋手動予約。
  { id = "yosou-oh", adapter = "yoso", game = "yosou-view", enabled = true }
  ```

- 実行は他コーナーと同じ program slot 排他。表示時間は上限つき（既定 2 分、
  catalog の `duration_minutes` で 1〜14 分）で自動終了し、既存の GameSwitch 復帰経路へ戻す。
  手動 stop は開始前・表示中・復帰中のどこでも同じ bounded な restore 経路を通る。
- 配備済みの catalog 登録（**2026-10-14 有効化 / 2026-10-15 自動抽選へ追加・読み上げ有効化**）:

  ```toml
  # 予想王: チャネルポイント予想の的中率ランキング（上位予想者のみ集計）。
  # 2026-10-14 オーナー承認で有効化（enabled = true）。
  # 2026-10-15 オーナー判断で manual_only を解除し、24時間 rolling rotation の
  # 自動抽選に組み入れる（WebUI からの手動予約も従来どおり可能）。
  # 2026-10-15 オーナー判断で読み上げを有効化（audio_enabled = true）。
  { id = "yosou-oh", adapter = "yoso", game = "yosou-view", enabled = true, audio_enabled = true }
  ```

- **有効化・抽選の契約**: `enabled = false` の行は rotation の適格判定で
  `disabled-or-paused` になるため、WebUI の手動予約も `hanjuku_not_eligible` で拒否される。
  自動抽選に入れるには `enabled = true` かつ `manual_only` を付けない（`manual_only = true` の行は
  適格判定で `manual-only` として除外され、手動予約だけを受け付ける）。
  視聴者の表示名を配信へ出す扱いはオーナー判断（§5）。
- 本番反映は `branch → PR → protected main → owner-only gateway` の正規フローで行う。

### 4.1 TwiCa 前景への表示は対象外（2026-10-15 オーナー判断）

**予想と TwiCa にデータ上の接点は無い。** TwiCa 前景は
`SOREN_DIRECT_TWICA_OVERLAY_URL`（`/overlay/...`）の1ページが
**配信者のカードガチャ履歴**（`GET /api/overlay/<streamerId>/events` と
`/realtime-config`）を購読し、カード排出演出を RGBA で合成する仕組みで、
予想を扱うテーブルも投入経路も持たない（`azumag/twica` を検索しても prediction 系は無い）。
`src/docich/twica_*.py` 側も URL をブラウザで開くだけで、任意のパネルを送る API を持たない。

一方で予想王は、Twitch Helix `top_predictors` → docich の台帳 → `yosou-view`
（GameSwitch のゲーム領域 960×540 全面に ranking 表）＋ 共通 eventOverlay の
`prediction` category 告知までを担当しており、**コーナーとしての表示は完結している**。
TwiCa 前景に同じ ranking を前景演出として重ねる必然性は無い。

したがって 2026-10-15 のオーナー判断で **TwiCa 連携は対象外**とする。
将来 TwiCa のカード風前景でも予想王を出したくなった場合は、`azumag/twica` 側に
「予想を扱う API / イベント種別と描画」を新設する別リポジトリの設計・PR として起票する
（`docs/features/doushi-card-corner.md` が TwiCa 側 API 新設を前提にしているのと同じ扱い）。
docich 単独で TwiCa の描画や配信フィードを追加しない。

## 5. プライバシー・同意

- 2026-10-14 オーナー判断: 配信で扱う情報であり、視聴者の表示名（Twitch が公開する
  `id / login / 表示名`）を配信画面へ出すための個別 opt-in / privacy 同意は不要。
  別途の同意取得は行わず、閲覧者への個別 opt-out 手段も設けない。
- 表示名は既存の表示契約どおり制御文字を除去し24文字へ丸める（`yoso_view`）。
  荒らし・不適切名への追加のフィルタや通報経路は設けない（オーナー判断）。
- トークン・IP 等の非公開情報は引き続き保存しない。診断へは件数のみを出し、
  視聴者名は出さない（§2）。
- オーナーの明示承認なしに production catalog を有効化しない。この判断は上記の
  2026-10-14 承認に対応する。

## 6. MVP

- [x] 台帳の検証・記録（冪等）・ランキング集計の**オフライン中核**（`prediction_leaderboard.py`、PR-1）
- [x] 予想精算時の記録フック（半熟英雄 `hanjuku_predictions.py`、PR-2a）
- [x] 予想精算時の記録フック（Soren `twitch_predictions.sh` ＋ `soren_predictions.py`、PR-2b）
- [x] `yoso` adapter ＋ 読み取り専用 view（`yoso_view.py` / `adapters/yoso_program.py` / `yoso_corner.py`、PR-3）
- [x] corner catalog 登録（2026-10-14 オーナー承認で `enabled = true`、2026-10-15 オーナー判断で `manual_only` を解除して自動抽選へ。PR-3＋有効化PR＋自動抽選PR）
- [x] 読み上げ原稿（`yoso_narration.py`、`audio_enabled` opt-in、PR-4）
- [x] overlay 表示（共通 eventOverlay の `prediction` category、PR-4）
- [x] TwiCa 前景への表示: 2026-10-15 オーナー判断で**対象外**（§4.1）
- [x] 不成立キャンセルの参加実績（`no-contest`）と5指標ボードのオフライン中核（`prediction_leaderboard.py`、PR-5。§2.1 / §3.1）
- [x] 半熟英雄の不成立キャンセル記録フック（`hanjuku_predictions._record_canceled`、PR-5）
- [x] 5指標ボードの view への表示（§3.1、1枚の表を18秒で部門切替。§8-5）。読み上げは的中率のみ
- [x] Soren 側の不成立キャンセル記録（soviet_now の handoff 拡張 + docich reader。PR-6。§8-2）
- [ ] 台帳に実データを入れて ranking を実測する（初回は「確定した予想がまだありません」）
- [ ] 不成立キャンセル・5指標ボードの実データでの受入（記録が本番で積まれてから）

## 7. 実装ステップ（PR 分割案）

1. **PR-1**: オフライン中核 `prediction_leaderboard.py` ＋ `tests/test_prediction_leaderboard.py` ＋ 本設計 doc。
2. **PR-2**: 半熟英雄 / Soren の予想精算で `RESOLVED` を観測したときに台帳へ記録する配線
   （共有 lock・atomic write・fail-closed）。予想本体の作成・精算ロジックは変えない。
   - **PR-2a（実装済み・半熟英雄）**: `prediction_leaderboard.record_resolved()`（共有 lock・atomic・
     冪等）を追加し、`hanjuku_predictions._advance()` の①リモートで `RESOLVED` を観測した経路と
     ②`RESOLVED` PATCH 成功直後に記録する。PATCH 応答に `top_predictors` が無いときだけ同じ id を
     1回 GET して権威ある row を使う。台帳の read/write 失敗は `leaderboard`、row の契約違反は
     `invalid_response` として error mode に入り、前者は同じ id を再試行する。診断の固定語彙にも
     `leaderboard` を追加。回帰: `tests/test_prediction_leaderboard.py` / `tests/test_hanjuku_predictions.py`。
     **終端理由の契約（2026-10-14 修正）**: 永続化する `result.reason` の受理集合は
     `hanjuku_run.TERMINAL_REASONS`（`game_over` / `screen_stalled` / `input_stalled`）と
     同一でなければならない。`_result()` は `hanjuku_run.terminal()` が返した
     `terminal_reason` をそのまま durable record へ書くため、`hanjuku_predictions` 側に
     より狭い集合（旧: `{game_over, screen_stalled}`）を置くと **自分が書いた記録を
     次の読み取りで拒否**し、round を永続化する前に `_advance()` が例外を上げて
     `error=invalid_state` の 300 秒 retry を永久に回す（`input_stalled` 終端の round が
     settlement も cancel もできなくなる）。回帰:
     `tests/test_hanjuku_predictions.py::test_input_stalled_terminal_survives_the_durable_reread`。
   - **PR-2b（実装済み・Soren）**: Soren の `twitch_predictions.sh` が resolve 成功時に
     応答 row を `tools/prediction_resolved.py` で `<soren_root>/tmp/state/prediction_resolved.jsonl`
     へ bounded（最大50行）に追記する（soviet_now PR、docich gitlink 更新）。
     docich 側は `src/docich/soren_predictions.py` が rotation timer からこのファイルを読み、
     変更が無い tick は cursor で飛ばし、`record_resolved(corner='soren')` で冪等に記録する。
     symlink・oversize・契約違反の行は記録せず固定コード（`handoff-*` / `leaderboard`）を返し、
     台帳書き込みに失敗した tick は cursor を進めないので同じ row を再試行する。
     診断は `prediction_leaderboard` の件数（rounds/viewers）と `soren_predictions` の
     存在・行数のみを投影する。回帰: `tests/test_soren_predictions.py`、
     `ops/vm_actions/tests/test_collect_diagnostics.py`、soviet_now `tests/test_prediction_resolved.py`。
3. **PR-3（実装済み）**: `yoso` adapter ＋ view ＋ catalog 登録（既定無効）。
   - `src/docich/yoso_view.py`: 台帳を read-only で読み、`予想王` ランキングを 960×540 へ描画する
     loopback限定 view（`/broadcast` と `/api/yosou`。台帳不正時は 503 で休止表示）。
   - `src/docich/adapters/yoso_program.py`: `yosou-view` の program adapter（readiness は
     runtime identity 付きの `/api/yosou`）。`src/docich/adapters/__init__.py` に登録。
   - `src/docich/yoso_corner.py`: GameSwitch slot 上の有界 lifecycle（既定2分、停止要求で即 restore、
     operator移動では上書きしない、`reconcile_failed_start` は terminal rollback のみ確定）。
   - `src/docich/corner_adapters.py` / `corner_catalog.py`: `yoso` adapter と identity 検証
     （`id=yosou-oh`、`game=yosou-view`、`duration_minutes` 1〜14、weather専用設定を拒否）。
   - `src/docich/webui.py`: 既存の durable manual queue 経由で予約・停止（専用 runner は追加しない）。
     `ops/vm_actions/collect_diagnostics.py` は `yoso_corner.json` の状態のみを投影する。
   - 回帰: `tests/test_yoso_view.py` / `tests/test_yoso_corner.py` / `tests/test_webui.py`（`yosou`）。
4. **PR-4（読み上げ・コーナー紹介まで実装済み）**: 読み上げ・overlay、有効化と VM 反映。
   - `src/docich/yoso_narration.py`: 台帳のサニタイズ済み board から、固定のコーナー紹介
     （`INTRO_TEXT` / `INTRO_OVERLAY_BODY`）と ranking 原稿を組み立てる。ranking の読み上げは
     上位3件を「第n位、同志◯◯、的中率◯◯パーセント、◯回中◯回的中、純増◯ポイント」の形で
     1発話（上限300字）。overlay は `overlay_queue.append_event` の既存 `prediction` category へ
     紹介と ranking の2件を bounded に出す（`source_id = yosou:{intro,ranking}:<request_id>`。
     共有キューは `source_id` ごとに最新1件だけを残すため分ける）。
   - 発話は catalog の `audio_enabled` を明示したときだけ。共有 audio worker の既定音声
     （`speaker=""`）へ `context="yosou:intro"` の紹介 → `context="yosou:ranking"` の ranking の
     順で enqueue する（各1件、合計2件）。独自の再生・再試行・バックログは持たない。
   - 呼び出しは corner が active になった直後の1回だけ。`announce_request_id` を副作用の前に
     保存するため、crash / resume で同じコーナーを二度読むことはない（at-most-once）。
     sink 失敗は `overlay_announcements` / `narration` の件数カウンタに残し（overlay は最大2件、
     narration は `enqueued`/`delivery_failed`/`skipped` の合計が最大2件）、corner は
     失敗させない（診断は件数のみで、例外本文・視聴者名は出さない）。
   - **TwiCa 前景への表示は docich 単独では実装せず、2026-10-15 のオーナー判断で対象外**
     （§4.1。TwiCa 側の正本・配信フィードが必要で、予想と TwiCa にデータ接点が無い）。
     docich 側は共通 eventOverlay までを担当する。
   - 有効化と VM 反映はオーナー承認後。2026-10-14 に `enabled = true`、2026-10-15 に
     `audio_enabled = true`（コーナー紹介と上位3名の読み上げ）で配備済み。
5. **PR-5（不成立キャンセルの参加実績と5指標ボードの中核・実装済み、Issue #933 追記）**:
   - `src/docich/prediction_leaderboard.py` を **schema 2** へ拡張し、`CANCELED`（`no-contest`）の
     参加実績・返金と5指標ボード（§3.1）を実装。schema 1 の既存台帳は読み込み時に正規化する。
   - `src/docich/hanjuku_predictions.py`: 不成立キャンセルの確定直後と、既に `CANCELED` の row を
     再観測した時に `_record_canceled` が `cancel_reason="no-contest"` で1回だけ記録する。
     台帳 I/O 失敗は round を terminal にせず再試行し、row 契約違反は記録せず再試行しない。
   - `ops/vm_actions/collect_diagnostics.py`: 台帳の schema 1 / 2 の両方を読む（投影は件数のみ）。
   - 回帰: `tests/test_prediction_leaderboard.py` / `tests/test_hanjuku_predictions.py` /
     `ops/vm_actions/tests/test_collect_diagnostics.py`。
   - 残り: 5指標の view / 読み上げ表示（§8-5）、Soren 側の不成立キャンセル記録（§8-2）。
6. **PR-6（Soren 側の不成立キャンセル記録・実装済み、2026-10-20 オーナー承認）**:
   - soviet_now: `tools/prediction_resolved.py` に `settled_row()`／`--cancel-reason no-contest`
     を追加し、`twitch_predictions.sh` の不成立 cancel が PATCH 応答 row を理由付きで
     既存 handoff へ追記する（手動 cancel は対象外）。
   - docich: `soren_predictions.read_handoff` が理由付き `CANCELED` row を受理し、
     `record_settled(corner='soren', cancel_reason=...)` で冪等記録する。理由なし
     `CANCELED`・理由付き `RESOLVED` は handoff 全体を拒否する。
   - `games/soviet_now` の gitlink を soviet_now 側のマージコミットへ更新する。
   - 回帰: soviet_now `tests/test_prediction_resolved.py` / `tests/test_prediction_no_contest.py`、
     docich `tests/test_soren_predictions.py`。

## 8. 未確定・オーナー判断が要る点

1. 対象予想: 半熟英雄のみ / Soren のみ / **両方**（本案は両方を想定）。
2. `top_predictors` の件数上限（Twitch 非公開。`MAX_PREDICTORS` は安全上限で 100）。
3. `min_rounds`（既定 3）・`limit`（既定 10）・コーナー表示時間（例: 2 分）。
4. 集計期間: 全期間 or 直近 N 日。
5. ~~opt-out / 不適切名の扱い~~ → **2026-10-14 オーナー判断で解決**（配信で扱う情報であり
   個別の opt-in / opt-out は設けない。制御文字除去と24文字上限のみ。§5）。
6. 既存予想への遡及（Helix は `id` 指定で過去予想も取得できるが、初回のみ 25 件/ページ）。

### 8.1 Issue #933 追記（2026-10-17）の残りの判断

- **8-1（解決・本案）** 部門ごとの定義: 賭けたポイント = `Σ used`、得たポイント = `Σ won`、
  失ったポイント = 外れた `used - refunded`。返金は元本返却として `won` / `lost` に入れない（§3.1）。
- **8-2（解決・2026-10-20 オーナー承認）** 不成立以外のキャンセル（結果不明・中断等）は
  参加実績に含めない。対象は `no-contest` のみ。Soren 側の `no-contest` 記録も
  PR-6 で実装済み（soviet_now の handoff が理由付き `CANCELED` row を運ぶ。§2.1 / §6）。
- **8-3（解決・2026-10-20 オーナー判断）** キャンセルを独立したゲーム結果で正誤評価しない。
  `CANCELED` だけを根拠に的中扱いしない現状を維持する（§2.1 のとおり実装済み）。
- **8-4（解決・本案）** キャンセル参加者を含む5指標をどこまで正確に算出できるか。
  データ源は引き続き `outcomes[].top_predictors`（上位予想者のみ）で、全参加者ではない。
  上位予想者が返らない round は round と outcome の件数だけを残し、視聴者を推測しない（§1 / §2.1）。
  これ以上の網羅には EventSub（`channel.prediction.progress`）の購読など新規経路が要るため未着手。
- **8-5（解決・本案 / 2026-10-17 オーナー判断 (a)）** 5指標ボードの表示: **1枚の表を18秒ごとに部門替わりへ
  切り替える**。表は 順位 / 表示名 / 部門の記録 / 的中率 / 純増 の5列で、部門ごとに記録列と最低数の
  表示だけを変える（5部門×18秒=90秒でコーナー上限2分内）。部門ごとにコーナーを分けない。
  読み上げは従来どおり的中率ボードのみ（(c) は採らない）。実装は `yoso_view`（§3.1）。
- **8-6（解決・本案）** 部門ごとの最低数: 的中率は `min_rounds`（既定 3）、
  参加・ポイント部門は `min_participations`（既定 1）。`min_rounds` を流用しない（§3.1）。
- **8-7（解決・本案 / Issue #2216）** 半熟英雄の予想 round が `error=invalid_state` で停滞したときの
  遠隔復旧経路。実測では WebUI の `POST /api/predictions/action`（`cancel` / `sync`）は
  *Soren 側* `twitch_predictions.sh` と `tmp/state/current_prediction.json` を操作する経路で、
  半熟の durable ledger `hanjuku_predictions.json` は操作しない。したがって半熟の round を
  止める経路は WebUI には無く、owner-only operator に**固定 operation**
  `cancel-prediction` / `sync-prediction`（`check-` 前置きで事前確認）を追加した。
  実装は `ops/vm_actions/control_hanjuku_prediction.sh` →
  `src/docich/hanjuku_prediction_control.py`、workflow は
  `.github/workflows/corner-rotation-operator.yml`。連続失敗の可視化は診断へ実装済み
  （`docs/operations/runtime-diagnostics.md`）。詳細は §8-7.1。

#### 8-7.1 owner-only の予想 round 制御（2026-10-20）

半熟の予想 round が「未検証の記録から settlement を作らない」fail-closed で止まったとき、
オーナーは WebUI ではなく canonical operator workflow から復旧できる。

- 固定 operation は4つ。`check-cancel-prediction` / `cancel-prediction` /
  `check-sync-prediction` / `sync-prediction`。`expected_reservation` も
  `outcome_index` も取らず、**prediction id・outcome index・任意コマンドを入力にしない**。
- `cancel` は台帳が所有する round だけを remote で `CANCELED` にして返金し、ローカル所有を
  解除する。不成立（`no-contest`）と証明できたときだけ参加実績を台帳へ記録する。
- `sync` は台帳が所有する round を remote の権威状態へ合わせる。remote が既に
  `RESOLVED` / `CANCELED` ならその結果を記録して所有を解除し、生きていれば実 id を再取り込みし、
  remote に存在しなければ stale な所有だけを捨てる。**target / middle / 選択肢は変更しない。**
- どちらも既に記録された round の照合のみを行う。新しい round を作らず、勝者を選ばず、
  他コーナーの賭けに触れない。`check-` は同じ判定を読み取り専用で行い、対象が無ければ非0で拒否する。
  **所有台帳を検証できない場合も `actionable=False` で拒否する**（apply 側も同じ台帳を
  `ledger_unreadable` で拒否するため、check だけ成功扱いにしない）。
- **round を消さず終端 tombstone（`status=CANCELED`）で残す。** 既存の `_advance` は
  「同じ run が所有する終端 round があれば二つ目の賭けを開かない」ため、`round=None` にすると
  そのガードが消え、同じ ready/active の run で次の tick が新しい予想を POST してしまう。
  tombstone を残せば同一 run では再作成せず、**別 runtime の後続 run だけ**が通常どおり作成できる。
- **記帳失敗を握り潰さない。** 不成立キャンセルの参加実績記帳が `leaderboard` で失敗したときは、
  round を終端にせず未解決のまま残し（`mode=settling`、`outcome=canceled_record_pending`）、
  取消前 snapshot も保持する。次の tick が remote の `CANCELED` を再観測して同じ prediction id を
  1回だけ記帳する（未解決 round も二つ目の賭けを止める）。通常 tick の
  `_save_cancel_snapshot` → PATCH → 記帳 → 証拠削除と同じ順序で、不可逆取消の前に証拠を保存する。
- **不正な durable result を tombstone に残さない。** `_load` は DONE の row でも
  `_checked_result` を呼ぶため、malformed な `result` を残したまま終端化すると次tickも
  後続runtimeも `invalid_state` で止まる。operator は元の不正証拠を
  `hanjuku_prediction_recovered_result_<予想IDのSHA-256>.json`（0600・原子的置換・64 KiB上限）へ
  退避してから row の `result` を空にし、通常 loader が受理する tombstone へ収束させる。
  証拠を保存できないときは `evidence_unpreservable` / `evidence_unavailable` で拒否し、
  黙って捨てない。
- **CANCELED 応答の shape に記帳根拠を依存させない。** CANCELED 応答が `users` /
  `channel_points` を省いても、取消前に LOCKED の counts から保存した no-contest snapshot を
  記帳根拠として引き継ぐ。証明できた source だけで `_record_canceled` を呼び、記帳できて
  いない間は round を DONE にしない（snapshot を孤児にしない）。
- **remote の「不在」と「所有権不一致」を区別する。** 同じ prediction id を返すが title や
  outcome id が一致しない場合は `remote_mismatch` で fail-closed し、ローカル所有を消さない。
  GET が実際に対象 id を返さなかった場合だけ不在として扱う。
- **本番 profile を明示的に束縛する。** gateway は固定 script を `DOCICH_CONFIG` を継承しない
  限定環境で実行するため、`load_global(..., None)` は `config/docich.toml`（`state_dir=run`）を
  選んでしまい本番の `config/docich.soren-live.toml`（`run-soren-live`）を操作しない。
  script は `--config "$root/config/docich.soren-live.toml"` を渡し、モジュール側も既定で
  この live config を選ぶ。
- 恒久修正 (#2213) で終端理由の受理集合は一致済み。残る停滞は durable `result` が契約違反の
  場合などで、tick の `_load` は fail-closed のまま拒否し続ける。operator だけが
  `_load_recoverable` を使い、**所有権（identity / title / window / nonce / status / remote id）は
  厳格に検証したまま** durable result の契約だけを緩く扱って当該 round を終わらせられる。
  壊れた台帳そのものは修復も上書きもしない（`ledger_unreadable` で拒否）。
- 認可は既存と同じ。owner 本人・`refs/heads/main`・`ref_protected`・確認文字列
  `production`・workflow は canonical な `corner-rotation-operator.yml` のみ・本番 SHA 一致。
  運用手順は `docs/corner-rotation.md`。


## 9. 検証

```sh
env -u PYTHONPATH python3 -m pytest -q tests/test_prediction_leaderboard.py
env -u PYTHONPATH python3 -m pytest -q tests/test_hanjuku_predictions.py
env -u PYTHONPATH python3 -m pytest -q tests/test_yoso_view.py tests/test_yoso_corner.py
env -u PYTHONPATH python3 -m pytest -q tests/test_corner_rotation.py tests/test_webui.py -k "corner or yosou or rotation"
env -u PYTHONPATH python3 -m pytest -q ops/vm_actions/tests/test_collect_diagnostics.py -k "leaderboard or yosou or prediction"
```

エビデンス: PR-1 の時点で中核 22 件 green。PR-3 では view / adapter / lifecycle / WebUI 予約の
回帰を追加し、既存の rotation・webui・diagnostics の回帰も緑のまま。
PR-5 では `tests/test_prediction_leaderboard.py` 39 件、`tests/test_hanjuku_predictions.py` 136 件、
`ops/vm_actions/tests/test_collect_diagnostics.py` の ledger 系 10 件が green（schema 1 の
読み替え、不成立キャンセルの参加・返金、5指標ボード、再観測での冪等を含む）。
Twitch・ゲーム・本番 VM への接続は行わない（view は loopback のみ、テストは GameSwitch を
fake runtime で駆動する）。

frame 描画の目視確認: [`docs/features/yosou-oh-corner-view.png`](yosou-oh-corner-view.png)
（合成台帳12名・8予想を headless Chromium で 960×540 に描画）。四辺・見出し・
最終行（10位）・footer の欠けがないことを確認した。実行コマンドは
`python3 -m docich.yoso_view --state-dir <合成台帳> --port 8811 ...` を起動し、
`Google Chrome --headless=new --window-size=960,540 --screenshot=... /broadcast`。
合成データのみで、実際の視聴者情報は使っていない。
