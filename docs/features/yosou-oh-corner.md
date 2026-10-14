# 予想王コーナー — チャネルポイント予想の的中率ランキング（設計案）

- Status: **PR-1（オフライン中核）／PR-2a（半熟英雄の記録フック）／PR-2b（Soren の記録フック）／
  PR-3（`yoso` adapter・view・catalog登録）／PR-4（読み上げ・eventOverlay・コーナー紹介）実装済み**。
  production catalog は **2026-10-14 オーナー承認で `enabled = true`・`manual_only = true`**
  （手動予約のみ。自動抽選には入らない）、**2026-10-15 オーナー判断で `audio_enabled = true`**
  （コーナー紹介と上位3名の読み上げ）。有効化・読み上げは
  `config/docich.soren-live.toml` の当該行の1行の変更で行う（§4参照）。TwiCa 前景への表示は
  **2026-10-15 のオーナー判断で対象外**（§4.1。docich 側は共通 eventOverlay までを担当する）。
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
- 表示は 960×540 の読み取り専用ビュー（既存 weather / paper と同じ合成 view 方式）＋読み上げ。

本ドキュメントは #933 の「統計の記録仕様とランキング表示の仕様を定義し、コーナー追加案として起案」に答える。

## 1. データ源の調査（一次情報）

Twitch Helix `GET /helix/predictions` の outcome に **`top_predictors`** がある。

出典（2026-10-08 確認）: <https://dev.twitch.tv/docs/api/reference/#get-predictions>

| フィールド | 型 | 説明（要約） |
| --- | --- | --- |
| `outcomes[].top_predictors` | Object[] | **的中した上位の視聴者**。該当が無ければ `null`。 |
| `top_predictors[].user_id` / `user_name` / `user_login` | String | 視聴者の id・表示名・ログイン名。 |
| `top_predictors[].channel_points_used` | Integer | その視聴者が賭けたチャネルポイント。 |
| `top_predictors[].channel_points_won` | Integer | その視聴者が獲得したチャネルポイント。 |
| `winning_outcome_id` | String | `status = RESOLVED` 以外は `null`。 |

**重要な制約**: 視聴者単位のデータは Twitch が公開する **top predictors だけ** で、
全参加者の一覧は取得できない。したがって本コーナーのランキングは
「**的中した上位予想者**のランキング」であり、全賭け参加者の完全な的中率ではない。
この制約は仕様として画面にも明記する（「上位予想者のみ集計」）。

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

- 台帳: `<state_dir>/prediction_leaderboard.json`、`schema = 1`。実装は `prediction_leaderboard.py`。
- 追記単位: `rounds[<prediction_id>]` に、**`RESOLVED` になった予想を1回だけ**記録する。
  - `prediction id` をキーにした冪等追記。再送・再精算の tick が二重計上しない。
  - `ACTIVE` / `LOCKED` / `CANCELED` は記録しない（未確定・返還のため）。
- 1 round の内容:
  `{ corner, title, ended_at, winning_outcome_id, outcomes[{id,title,users,points}], predictors[] }`
- 1 predictor の内容:
  `{ user_id, user_login, user_name, outcome_id, used, won, hit }`
  - `used` = `channel_points_used`、`won` = `channel_points_won`。
  - `hit` = 「その視聴者が `winning_outcome` を選んだか」（`outcome_id == winning_outcome_id`）。
- 記録タイミング: 精算確定後に `RESOLVED` を観測した tick。書き込みは予想所有者が行う。
  半熟英雄は docich の予想 tick が直接記録し、Soren は resolve 時に自身の handoff ファイルへ
  追記して docich の rotation timer（`soren_predictions.tick`）が記録する。
- 書き込み経路: `prediction_leaderboard.record_resolved(state_dir, row, corner=...)` が唯一の入口。
  `<state_dir>/locks/prediction-leaderboard.json.lock` の排他 lock の下で read-modify-write し、
  `save()` が temp file + `os.replace` で atomic に置換する。同じ `prediction id` の再送は no-op。
  壊れた台帳・壊れた row は例外で拒否し、既存の台帳を上書きしない（fail-closed）。
- 記録に失敗したとき: 半熟英雄の tick は固定コード `leaderboard`（台帳の read/write 失敗）または
  `invalid_response`（row が契約違反）で error mode に入り、台帳書き込みの失敗は同じ prediction id を
  次の観測で再試行する。`invalid_response` の row は再試行せず記録しない。
- 保持: 最大 `MAX_ROUNDS = 2000` 件。超過時は `(ended_at, id)` の古い順に間引く。
- 入力上限: 台帳 512 KiB、1 outcome あたり predictor 100 件、文字列 128 文字。
- 検証: `hit` を誤記録しないよう、壊れた resolved row は **fail-closed**（例外）で記録しない。
  非 resolved の row は静かにスキップする。
- プライバシー: 保存・表示する視聴者情報は Twitch が公開する `id / login / 表示名` と
  賭けポイントのみ。トークン・IP などは保存しない。診断（`stats`）へは **件数のみ**を出し、
  視聴者名は出さない。

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
- 翻訳対象外: view は常に日本語表示で、翻訳すると集計の意味が変わる。
  `yoso_view.HTML` は `<html lang="ja" translate="no">` と
  `<meta name="google" content="notranslate">` を持ち、日本語UIのブラウザが出す
  翻訳コントロール（日本語 / English）を出さない（2026-10-15 オーナー判断）。

## 4. コーナー追加案（番組ローテーションへの接続）

- 新 adapter **`yoso`**（game id `yosou-view`）。`WeatherCornerAdapter` / `TsuitateCornerAdapter` と
  同列の**合成 view**（GameSwitch の program slot に乗る読み取り専用ビュー）。
- ビューは台帳を **read-only** で読み、`予想王` ランキング表を 960×540 へ描画する。
  既存ゲーム本体・配信 encoder・Soren の予想 worker は変更しない。
- catalog 例（**手動予約のみ**で提案）:

  ```toml
  # 予想王: 予想的中率ランキング。手動予約のみ。
  { id = "yosou-oh", adapter = "yoso", game = "yosou-view", enabled = true, manual_only = true }
  ```

- 実行は他コーナーと同じ program slot 排他。表示時間は上限つき（既定 2 分、
  catalog の `duration_minutes` で 1〜14 分）で自動終了し、既存の GameSwitch 復帰経路へ戻す。
  手動 stop は開始前・表示中・復帰中のどこでも同じ bounded な restore 経路を通る。
- 配備済みの catalog 登録（**2026-10-14 有効化 / 2026-10-15 読み上げ有効化**）:

  ```toml
  # 予想王: チャネルポイント予想の的中率ランキング（上位予想者のみ集計）。
  # 2026-10-14 オーナー承認で手動予約を有効化（enabled = true）。manual_only を
  # 維持するため自動抽選には入らない。
  # 2026-10-15 オーナー判断で読み上げを有効化（audio_enabled = true）。
  { id = "yosou-oh", adapter = "yoso", game = "yosou-view", enabled = true, manual_only = true, audio_enabled = true }
  ```

- **有効化の契約**: `enabled = false` の行は rotation の適格判定で
  `disabled-or-paused` になるため、WebUI の手動予約も `hanjuku_not_eligible` で拒否される。
  手動予約を受け付けるには、同じ行を `enabled = true` にする（`manual_only = true` を維持する限り
  自動抽選には入らず、適格判定では `manual-only` として除外される）。
  視聴者の表示名を配信へ出す扱いはオーナー判断（§5）。
- 有効化後も自動抽選へ入れない（`manual_only = true` のまま）。本番反映は
  `branch → PR → protected main → owner-only gateway` の正規フローで行う。

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
- [x] corner catalog 登録（`manual_only`。2026-10-14 オーナー承認で `enabled = true`、PR-3＋有効化PR）
- [x] 読み上げ原稿（`yoso_narration.py`、`audio_enabled` opt-in、PR-4）
- [x] overlay 表示（共通 eventOverlay の `prediction` category、PR-4）
- [x] TwiCa 前景への表示: 2026-10-15 オーナー判断で**対象外**（§4.1）
- [ ] 台帳に実データを入れて ranking を実測する（初回は「確定した予想がまだありません」）

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

## 8. 未確定・オーナー判断が要る点

1. 対象予想: 半熟英雄のみ / Soren のみ / **両方**（本案は両方を想定）。
2. `top_predictors` の件数上限（Twitch 非公開。`MAX_PREDICTORS` は安全上限で 100）。
3. `min_rounds`（既定 3）・`limit`（既定 10）・コーナー表示時間（例: 2 分）。
4. 集計期間: 全期間 or 直近 N 日。
5. ~~opt-out / 不適切名の扱い~~ → **2026-10-14 オーナー判断で解決**（配信で扱う情報であり
   個別の opt-in / opt-out は設けない。制御文字除去と24文字上限のみ。§5）。
6. 既存予想への遡及（Helix は `id` 指定で過去予想も取得できるが、初回のみ 25 件/ページ）。

## 9. 検証

```sh
env -u PYTHONPATH python3 -m pytest -q tests/test_prediction_leaderboard.py
env -u PYTHONPATH python3 -m pytest -q tests/test_yoso_view.py tests/test_yoso_corner.py
env -u PYTHONPATH python3 -m pytest -q tests/test_corner_rotation.py tests/test_webui.py -k "corner or yosou or rotation"
```

エビデンス: PR-1 の時点で中核 22 件 green。PR-3 では view / adapter / lifecycle / WebUI 予約の
回帰を追加し、既存の rotation・webui・diagnostics の回帰も緑のまま。
Twitch・ゲーム・本番 VM への接続は行わない（view は loopback のみ、テストは GameSwitch を
fake runtime で駆動する）。

frame 描画の目視確認: [`docs/features/yosou-oh-corner-view.png`](yosou-oh-corner-view.png)
（合成台帳12名・8予想を headless Chromium で 960×540 に描画）。四辺・見出し・
最終行（10位）・footer の欠けがないことを確認した。実行コマンドは
`python3 -m docich.yoso_view --state-dir <合成台帳> --port 8811 ...` を起動し、
`Google Chrome --headless=new --window-size=960,540 --screenshot=... /broadcast`。
合成データのみで、実際の視聴者情報は使っていない。
