# 予想王コーナー — チャネルポイント予想の的中率ランキング（設計案）

- Status: **Proposal（起案）**。実装・配備は未着手。production の corner catalog は変更しない。
- 関連 Issue: [azumag/docich#933](https://github.com/azumag/docich/issues/933)（番組コーナー（24時間中ローテーション）に追加）、
  [azumag/docich#9](https://github.com/azumag/docich/issues/9)（自動チャネルポイント予想）
- 関連実装: `src/docich/prediction_leaderboard.py`（本PRのオフライン中核。記録・集計・ランキングの契約）、
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
  現状は応答本文を捨て、HTTP コードのみ検査している（`_resolve_prediction_with_best_outcome`）。

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
- 読み上げ: 上位 3 件程度を実況。視聴者の呼称は既存契約に合わせ「同志◯◯」。

## 4. コーナー追加案（番組ローテーションへの接続）

- 新 adapter **`yoso`**（game id `yosou-view`）。`WeatherCornerAdapter` / `TsuitateCornerAdapter` と
  同列の**合成 view**（GameSwitch の program slot に乗る読み取り専用ビュー）。
- ビューは台帳を **read-only** で読み、`予想王` ランキング表を 960×540 へ描画する。
  既存ゲーム本体・配信 encoder・Soren の予想 worker は変更しない。
- catalog 例（**既定無効・手動予約可**で提案）:

  ```toml
  # 予想王: 予想的中率ランキング。既定は無効。opt-in と privacy 確認が前提。
  { id = "yosou-oh", adapter = "yoso", game = "yosou-view", enabled = false, manual_only = true }
  ```

- 実行は他コーナーと同じ program slot 排他。表示時間は上限つき（例: 2 分）で自動終了し、
  既存の GameSwitch 復帰経路へ戻す。
- 有効化・catalog への追加は **オーナー承認後**（本 PR では行わない）。

## 5. プライバシー・同意（要検討）

- 視聴者の表示名を配信画面へ出すため、参加者への周知と、荒らし・不適切名の扱い、
  個別 opt-out（非表示申請）の手段を要検討。未確定。
- 本案は既定無効。オーナーの明示承認なしに production catalog へ入れない。

## 6. MVP

- [x] 台帳の検証・記録（冪等）・ランキング集計の**オフライン中核**（`prediction_leaderboard.py`、本PR）
- [ ] 予想精算時の記録フック（半熟英雄 `hanjuku_predictions.py`）
- [ ] 予想精算時の記録フック（Soren `twitch_predictions.sh`）
- [ ] `yoso` adapter ＋ 読み取り専用 view
- [ ] corner catalog 登録（既定無効・手動予約）
- [ ] 読み上げ原稿
- [ ] overlay / Twica 表示

## 7. 実装ステップ（PR 分割案）

1. **PR-1（本PR）**: オフライン中核 `prediction_leaderboard.py` ＋ `tests/test_prediction_leaderboard.py` ＋ 本設計 doc。
2. **PR-2**: 半熟英雄 / Soren の予想精算で `RESOLVED` を観測したときに台帳へ記録する配線
   （共有 lock・atomic write・fail-closed）。予想本体の作成・精算ロジックは変えない。
3. **PR-3**: `yoso` adapter ＋ view ＋ catalog 登録（既定無効）。
4. **PR-4**: 読み上げ・overlay、オーナー承認後の有効化と VM 反映。

## 8. 未確定・オーナー判断が要る点

1. 対象予想: 半熟英雄のみ / Soren のみ / **両方**（本案は両方を想定）。
2. `top_predictors` の件数上限（Twitch 非公開。`MAX_PREDICTORS` は安全上限で 100）。
3. `min_rounds`（既定 3）・`limit`（既定 10）・コーナー表示時間（例: 2 分）。
4. 集計期間: 全期間 or 直近 N 日。
5. opt-out / 不適切名の扱い。
6. 既存予想への遡及（Helix は `id` 指定で過去予想も取得できるが、初回のみ 25 件/ページ）。

## 9. 検証

```sh
env -u PYTHONPATH python3 -m pytest -q tests/test_prediction_leaderboard.py
```

エビデンス: 本PRの時点で 22 件すべて green（`src/docich/prediction_leaderboard.py`）。
Twitch・ゲーム・本番 VM への接続は行わない。
