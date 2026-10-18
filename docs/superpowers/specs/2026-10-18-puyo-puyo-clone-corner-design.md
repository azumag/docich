# ぷよぷよクローン — 設計 (企画)

**Date:** 2026-10-18
**Status:** Planning / non-production.
**Source issue:** azumag/docich#698 (ぷよぷよクローン)

## Goal

Define the planning and input contract for a stream corner that presents an
original falling-block "connect 4+" puzzle clone in the style of Puyo Puyo
(ぷよぷよクローン): rules, piece pairs, controls, chain mechanics, scoring,
win/loss, the AI opponent, the spectator view, and viewer (同志) participation.

This corner is the single-game sibling of the participatory multi-game corner
(azumag/docich#699). It fixes only the input contract so overlay, progression,
match runtime, and production startup can be designed separately.

## Non-goals

- No game runtime, board engine, rendering, input adapter, or AI brain.
- No matchmaking, networking, viewer chat integration, or Twitch/VM I/O.
- No overlay wiring, progression frames, external model billing, deployment,
  or production startup.
- The fixed fixtures are synthetic and are used only for contract acceptance.
- No claim that this corner is the original Puyo Puyo, nor a derivative that
  reproduces Sega's original assets. 「ぷよぷよ」 is a trademark of its owner;
  this clone is an independent original implementation of the same genre.

## Input contract

Each corner run becomes one structured record (`game` container) containing:

`title`, `gameId`, `genre`, `referenceWork`, `originalIpNote`, `boardSize`,
`colors`, `piecePair`, `rotationModes`, `controls`, `gravityMode`,
`connectionRule`, `chainMechanics`, `scoring`, `winCondition`, `lossCondition`,
`matchType`, `matchFormat`, `players`, `playerCount`, `realTime`, `turnBased`,
`multiplayer`, `aiOpponent`, `difficulty`, `comradeMatch`, `spectatorView`,
`chatCommands`, `roundStructure`, `thirtySecondSummary`, `oneLineSummary`,
`labelsPolicy`, `segmentLabels`, `limitations`, `uncertainty`,
`figureOrDiagram`, `youtubeIndependentDraft`, `primarySources`,
`sourcePriority`, `citationKeepUrl`, `url`.

The fixture also carries `verifiedWebMaterial` (citation provenance) and a
`nonProductionAcceptance` block.

## Slug variants

The operator may name this corner with any of the following slugs. Every
variant ships an equivalent fixture and contract test so the corner is
recognizable regardless of the name used:

- `puyo-puyo-clone` (canonical, from the issue title)
- `puyo-puyo`
- `puyo-clone`

## Non-production acceptance conditions

- fixed fixture parses as the contract above;
- `isSyntheticFixture` is `true` and the fixture is never presented as real;
- primary source URLs and the corner URL are present and HTTPS;
- source priority favors primary/original references and institutions;
- the fixture reports an originalIP note and never claims the original work;
- fact/analysis/forecast labels and multiple perspectives are explicit;
- honest-material hashes follow the adapter contract;
- overlay and progression are split out after the input contract;
- partial completion is allowed and reviewer approval is required.

## Offline core (PR-2)

`src/docich/puyo_corner.py` implements the offline mechanics behind this
contract, following the precedent set by the TRPG corner offline core
(Issue #684): pure functions, no live I/O.

`load_state`/`new_state` fall back to a built-in DEFAULT_RULESET when a saved
state has no ruleset, so legacy or empty states keep working. `record_match`
only records terminal states (END/FAILED/TIMEOUT).

Ruleset validation is fail-closed:

* supported board only (6x12 + 1 hidden spawn row as declared above);
* 2-4 distinct colors declared in the ruleset, never widened;
* gravitySettle / chainReevaluation / simultaneousClear must be true;
* scoring descriptors must not be None (exact tables stay in the module);
* saved states carry a ruleset digest that must match the live module.

The falling pair is axis + satellite, spawn on the first visible row.

## Board mechanics

Every chain pass settles the board per column, clears every group of 4+
orthogonally connected same-color puyo at once (simultaneousClear), and the
settle→clear loop re-evaluates until no clearable group remains; chains
1, 2, 3, ... each add the bounded chain bonus, and an emptied board adds the
all-clear bonus. MAX_ACTIONS, MAX_PAIRS_PER_ROUND and MAX_CHAIN_STEPS bound
any tick, so runaway loops fail closed with an explicit budget code. A locked
pair with no room for its refill ends the round: the surviving side wins and
both sides topping out is a draw.

## Match runtime + AI brain + overlay draft (follow-up slice 1, PR-3)

Agreed in the owner-approved thread (2026-10-18): PR #2485 stays the input
contract; follow-ups implement overlay / progression / match runtime / AI
brain / ネット対戦 / 本番起動. This slice adds, still fully offline:

* `plan_actions(state, side)` — bounded offline brain. Enumerates at most
  ``MAX_PLANS_PER_DECISION`` scripts (<= 3 rotations x 6 target columns x
  hard drop), plays every candidate through the real engine on a copy, and
  picks the highest evaluation (engine chain/all-clear score minus bounded
  height / hole / bumpiness penalties). Deterministic; never mutates state.
* `play_match(state)` — offline self-play driver. Applies the brain's script
  for each side, then ticks. The engine budgets remain authoritative: the
  action/pair budgets end a runaway round as a bounded TIMEOUT (the same way
  the live corner's fixed display window would end it), never an infinite
  loop. A top-out ends the round with a winner; reset clears the board.
* `overlay_draft(state)` — bounded pure draft of the overlay sections
  (phase, both boards, scores, chains, winner). The 960x540 synthetic view /
  TTS / GameSwitch wiring consumes this shape in a later, explicitly enabled
  step.

No chat input, no adapter, no catalog change and no VM touched in this slice.

## Adapter + catalog 登記 (owner-approved PR-3, 既定 disabled / manual_only)

Owner approval (2026-10-18 thread): PR-3 = `puyo` adapter + catalog row,
default disabled. Merging PR #2485 happens after PR-3 is done. The slice
follows the read-only synthetic-view precedent (`doushi_corner.py` /
`doushi_view.py` / `adapters/doushi_program.py`) minus audio and narration:

* `src/docich/puyo_view.py` — loopback-only HTTP view (port 8807,
  ``VIEW_NAME = "puyo-view"``) showing one already-played offline match
  summary at the fixed 960x540 frame. It never fetches and never starts a
  match itself: the program adapter plays one bounded self-play through
  ``puyo_corner`` before the switch commits and hands over a validated
  transient payload file; a missing or invalid payload keeps the process
  from serving at all (fail-closed, no stale board).
* `src/docich/adapters/puyo_program.py` — GameSwitch program-view adapter
  (`make_puyo_view_adapter`, registered for `game == "puyo-view"`). The
  preflight plays exactly one bounded offline match (fresh 64-bit seed,
  engine budget `MAX_STEPS` bound) and writes `puyo_match.json` into the
  transient runtime directory only.
* `src/docich/puyo_program.py` — `PuyoProgramManager` bounds the slot: fixed
  duration (default 2, max 14 minutes), full runtime identity persistence,
  stop/interrupt/restore receipts identical to the doushi lifecycle. No
  audio, no narration, no fetch during display.
* `corner_adapters.PuyoCornerAdapter` + `ADAPTERS["puyo"]` — identity
  contract: `game` must be `puyo-view` and `live_eligible` false, otherwise
  `CornerExecutionError`.
* `corner_catalog` validation for `adapter == "puyo"`: id must be
  `puyo-corner`, game `puyo-view`, `manual_only` must stay true,
  `audio_enabled`/`fetch_on_start` rejected, `live_eligible` false,
  duration 1–14 when present. Nothing is widened or defaulted on.
* Production catalog row (config/docich.soren-live.toml):
  `{ id = "puyo-corner", adapter = "puyo", game = "puyo-view",
  enabled = false, manual_only = true }` — inert until the owner flips
  `enabled = true`, and even then manual-queue-only until a later approved
  step drops `manual_only`.
* WebUI: the row is visible in manual diagnostics and can be queued/stopped
  (`("puyo", "ぷよぷよクローン")` label) once enabled.

While `enabled = false` the corner can never be selected, not even manually
(`disabled-or-paused`); enabling is an explicit owner action on the VM.

## 明示的に後回し (not in PR-3)

チャット参加 (`!puyo join`), progression/成績記録, ネット対戦, TTS,
自動ローテーション組入 (`enabled = true` / `manual_only = false`),
本番起動 — each stays a separate explicitly approved slice.

## PR-4: チャット参加 + 読み上げ + 有効化 (owner-approved 2026-10-18, Slack ts=1792288826)

範囲: オーナーが Slack ボタンで承認した「チャット参加/TTS/有効化/本番起動」のうち、
ローカルで実装・検証できる部分。本番 VM への配備・起動 (=「本番起動」) は本人/VM ops
の段階で、この PR では未実施・未検証として明記する。

### チャット参加 (オフラインコア)

* `puyo_corner.py` に参加者名簿とチャット操作キューを追加 (引擎はインターネットも
  Twitch も扱わない。入力は呼び出し側が供給する):
  - `parse_chat(text)`: `!puyo join` / `!puyo leave` / `!puyo <action>`
    (action は `ACTIONS` のトークン、大小文字は同一視)。それ以外は None (fail-closed)。
  - `observe_chat(state, user_id=, display_name=, text=, now=)`:
    講読 → 名簿更新/キュー投入。戻り値は `join` / `leave` / `action` / `ignored`。
    エラーコードは固定語のみ: `invalid_user`, `roster_full`, `not_live`,
    `not_in_roster`, `chat_queue_full`。
  - 有界: `MAX_ROSTER=16` 名・display名32字・user_id64字・キュー `MAX_CHAT_QUEUE=64`・
    1人あたり同時 `MAX_PENDING_PER_USER=2`・1ステップ消費 `MAX_CHAT_ACTIONS_PER_STEP=2`。
  - 名簿はラウンドをまたいで保持 (`reset` で保存)、キューはラウンド毎に消去
    (`start` で初期化)。離脱者は名簿から消え、そのユーザーの未再生分も消す。
* `play_match`: 名簿が非空のとき user 側はキュー注入を優先 (FIFO、1ステップ最大2つ)、
  キューが空なら内蔵 AI が代打 — 無人枠やチャット無しでも従来の予算内で完走する。
  予算枯渇は従来どおり TIMEOUT で fail-closed。自己再演と等しく決定論的。
* `record_match` 台帳項目に `participants` (user_id の有界リスト) を追加。
  `overlay_draft` に `participants` 節 (表示名のみ・先頭4人・節上限12内) を追加。
* 状態互換: `roster` / `chat_queue` は新規フィールドで、旧状態ファイルは
  `load_state` が既定値 (空) を補完して引き続き読める。

### 本番でのチャット入力の契約 (VM 側)

* チャット取り出し router は、ビュー実行プロセスと同じ runtime directory の
  `chat_inbox.jsonl` へ JSON 行 `{"user_id","display_name","text"}` を追記する。
* 表示側 (`docich.puyo_view --live --inbox-file ...`) は 0.5 秒間隔で新着行だけ
  消費し、字数上限 (text 200字) と JSON 検証で fail-closed、名簿/キュー上限超過は
  固定コードで無視し、生のチャット文はどこにも出さない。
* ビュー實行プロセスが引擎スレッドを持つ (`LiveMatch`): 1ステップ = `play_match(1)`、
  スリップ体は gravity/override に依る 0.1 秒刻み。ラウンド終了時に (1) 台帳記録
  (`puyo_corner_ledger.json`, flock)、(2) 端末スナップショット (`puyo_match.json`、
  既存スキーマのまま) を書いて次ラウンドへ start。ラウンド数は表示窓 (2–14分) で有界。
* `/api/puyo` は callable provider に対応し、ライブ本体は `phase` / `participants`
  を返す。provider が失敗したら `{ok:false}` を返す (stale データを出さない)。
* HTML: 参加者名と状態 (`対局中` / `対局終了`) を表示、案内タグ
  「チャット参加 !puyo join」。

### 読み上げ (opt-in)

* `puyo_narration.py`: doushi_narration と同じ二つの検証済みシンク
  (`overlay_queue.append_event`, category=game / `soren_output.enqueue_audio_text`)。
  固定文のみ・数字有界・チャットの表示名は読まない。
  - start: 対戦開始 + `!puyo join` 案内 (スロット開始時に1回)。
  - result: 台帳の今枠内ラウンド記録 (`started_at-60s ≤ ended_at` かつ直近7日) から
    勝者/得点/参加者数を1文で。検証できない記録は読まない (fail-closed)。
* `PuyoProgramManager(audio_enabled=, announce=)`: at-most-once 記録を状態に残す
  (`announce.start` / `announce.result`)。crash/再開後も同じ行を二度読まない。
  シンク失敗は固定ステータス記録のみで、コーナーは落とさない。
* カタログ `adapter == "puyo"`: `audio_enabled` を bool opt-in として許可
  (`fetch_on_start` は引き続き拒否)。読み上げは `audio_enabled = true` のときだけ音声キューへ。

### 有効化 (本番カタログ)

* `config/docich.soren-live.toml` の puyo 行:
  `enabled = true, audio_enabled = true, manual_only = true` (+ 承認コメント)。
  自動抽選 (`manual_only = false`) は本番チャット取り出し配線の実測確認後の
  別承認ステップへ残す — 既定値の変更なし (`enabled` は手動予約可能な意味での有効化)。
* WebUI/rotation: puyo は `eligible` (手動予約可能) だが auto 抽選では
  `manual-only` として除外される ( `test_real_adapters_derive_live_eligible_count`
  で固定)。

### 本番起動 (未実施)

config マージ後の VM 配備・GameSwitch 経由の初回予約・`chat_inbox.jsonl` を
生む chat router の配線は docich control plane (owner-only gateway) の手順であり、
この PR の範囲では未実行・未検証。PR 本文と本節に事実として記録する。
