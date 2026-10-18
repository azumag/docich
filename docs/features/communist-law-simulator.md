# 共産主義法律シミュレーター — 仕様と配線（#697）

## この作業の範囲

Issue #697「共産主義法律シミュレーター」（2026-09-18 起票のゲーム案、本文なし）について、
**企画の仕様固定、決定論シミュレーションエンジン、production 配線** を扱う。

- offline slice（決定論エンジン＋fixture＋契約テスト）は PR #2486 として main に統合済み（merge commit `0772bbda`、2026-10-18）。
- production 配線（corner adapter / view / config / rotation 登録）は 2026-10-18 のオーナー承認（#697 スレッドの承認ボタン）で実装した。

- 実装したもの: 合成 fixture 1本、決定論エンジン、overlay payload、有界な start → 法案ごとの frame → end 進行、status overlay 整合検証、回帰テスト（変異で落ちることを実測）。
- この doc の残りは offline slice 当時の記録を保持する（対象 HEAD は当時のもの）。

対象 HEAD: `azumag/docich` `origin/main` `4c555712be84095389d8efe0db0d196ac7121f01`。

## ねらい

架空の社会主義共和国の最高会議を舞台に、AI同志（将来の brain）が法案を可決し、
固定の近似則で国家指標がどう動くかを決定論的に可視化するコーナー。
チャネルの世界観（ソ連風・同志）に合致し、法案1本ごとに効果と代償が可視化されるため、
「AIが決めて、世界が反応する」配信として見せやすい。

すべての共和国名・法令・指標・出来事は**架空の合成コンテンツ**であり、
実在の国家・経済・歴史のモデルであるとは主張しない（caveat を end frame まで保持する）。

## シミュレーションの仕様（決定論）

### 国家指標（6種、すべて 0–100 の整数）

| metric | 表示名 | 初期値 |
|---|---|---|
| production | 生産 | 40 |
| support | 支持 | 40 |
| discipline | 党規律 | 60 |
| reserves | 外貨 | 20 |
| blackMarket | 闇市場 | 20 |
| planProgress | 計画 | 0 |

### 1ターンの計算（`apply_turn`、純粋関数）

1. 可決した法案の効果差分を各指標へ加算し、0–100 にクランプする。
2. 計画前倒しルール: クランプ後の `production // 10` を `planProgress` に追加し、再度クランプする（生産が計画を前倒しする近似則）。
3. 警告イベント: 閾値を**跨いだ瞬間だけ**1回発火（戻れば再装填）。`blackMarket >= 50`（上方向）、`support <= 25`（下方向）、`reserves <= 10`（下方向）、`production >= 80`（上方向）、`discipline <= 30`（下方向）。
4. 終端判定（計画達成を優先、その後崩壊）:
   - `planProgress >= 100` → `plan_achieved`（五カ年計画達成）
   - `support <= 0` → `collapse_public_unrest`（民衆の不満が爆発）
   - `discipline <= 0` → `collapse_party_split`（党の統制喪失）
   - `reserves <= 0` → `collapse_trade_default`（対外破綻）
   - `blackMarket >= 100` → `collapse_black_market`（闇市場の侵食）
   - 法案リストの末尾まで終端なし → `plan_incomplete`（会期終了・計画未達。明示的な非達成終端）

### 法案ライブラリ（14本、固定）

`src/docich/communist_law_simulator.py` の `LAWS` が正本。各法案は `law_id`（英小数字とハイフン）、日本語名、category（economy / society / security / foreign / culture）、1行要約、効果差分、リスク文を持つ。同一会議での同一法案の再提出は拒否する。例:

- 穀物調達の強化: 生産+6 / 支持-10 / 闇市場+12
- 科学技術五カ年計画: 生産+6 / 計画+12 / 外貨-5
- 鉄鋼生産倍増令: 生産+14 / 計画+10 / 外貨-8 / 支持-4
- 消費財生産の優先: 支持+12 / 闇市場-8 / 生産-4
- 国家保安省の粛清: 党規律+14 / 支持-12 / 生産+2

### セッション入力契約（fail-closed）

```json
{"corner": "communist-law-simulator",
 "session": {"title": "…", "republic": "…", "laws": ["<law_id>", …]}}
```

- 厳密JSON（重複キー・非有限値を拒否、64KiB上限）。
- `session` は `title` / `republic` / `laws` の3キーちょうど。title・republic は前後空白なし80字以内。
- `laws` は 1〜12 本（`MAX_SESSION_LAWS`）、既知の `law_id` のみ、重複禁止。
- 終端（崩壊・計画達成）の後に法案が続くセッションは拒否する（`session_continues_after_terminal`）。
- 不明な `law_id`、未知のキー、型違い、範囲外はすべて `LawSimError` で拒否する。
- 同一セッションは常に同一トレースを返す（`trace_signature` が sha256 で固定）。

### 到達性の備録

固定の初期値と14本のライブラリでは、1会議（最大12本・重複なし）で到達する終端は
`plan_achieved` / `plan_incomplete` / `collapse_public_unrest` である。
`collapse_party_split` / `collapse_trade_default` / `collapse_black_market` は
現ライブラリの差分総和では固定開始値から届かない安全則であり、`apply_turn` の単体テストで
直接検証する（仕様の存在を固定し、到達性を主張しない）。

## overlay / status 契約

- `render_overlay`: タイトル、共和国、`isSynthetic: true`、最終状態とその日本語文、初期/最終指標、ターン別レコード（法案・効果差分・計画前倒し・警告・状態）、警告一覧、caveats。
- `progress_frames`: **start → 法案ごとの turn frame → end** の有界な frame 列（最大 1+12+1=14 ≤ `MAX_PROGRESS_FRAMES=48`）。index は連続、start/end は同一タイトル、end frame は最終状態文と caveat を保持する。
- `status_overlay_entry` / `validate_status`: frame 列と status overlay 記録が同一 fixture から導出され、総数・開始/終了 index・種別・caveat・最終状態で整合することを再検証する。構造不変検査を canonical 再導出との一致検査より先に行うため、`progress_frames` 自体の変異（caveat 削除・種別改変）も検出する。改ざんされた frame 列（本文差し替え・順序入替）は受理しない。

## 非本番受入（固定 fixture）

`tests/fixtures/communist_law_simulator/fixed_law_session_example.json`
（fixtureId `fixed-law-session-first-plan-001`、`isSyntheticFixture: true`）:

- 第1期最高会議（第一次五カ年計画）として11法案をこの順で可決し、**`plan_achieved` で終端**。
- 最終指標の golden 値: 生産86 / 支持26 / 党規律63 / 外貨25 / 闇市場25 / 計画100。
- 警告イベントの golden: 第3回 外貨、第4回 支持、第10回 生産。
- 物語として「計画は前倒し達成、ただし外貨と支持は危険水域」という代償つき達成を見せられる。

`tests/test_communist_law_simulator_contract.py`（27件）で以下を実測:

1. fixture 契約（synthetic 署名、nonProductionAcceptance フラグ、法案ライブラリの整合、初期値の範囲）。
2. 決定論（同一セッション→同一トレース・同一署名）と golden 値。
3. fail-closed（不明/重複法案、終端後の法案、本数上限、キー契約、厳密JSON、範囲外の状態）。
4. overlay / frame / status の整合と改ざん拒否。

### 変異テストの実測（2026-10-18、macOS ローカル）

モジュールへ一時的な変異を適用し、テストが落ちることを確認してから復元した（復元後はバイト一致を確認）。

| 変異 | 結果 |
|---|---|
| `_clamp` を恒等関数化 | 4 failed |
| `_collapse_status` の終端判定を無効化 | 5 failed |
| 警告イベントを毎ターン発火に変更 | 2 failed |
| end frame の caveats を削除 | 5 failed |
| 科学技術五カ年計画の計画効果 12→11 | 1 failed（golden への効果差分ピン留め追加後に検出を確認） |
| 計画前倒しルール（production // 10）の削除 | 7 failed |

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_communist_law_simulator_contract.py
```

## production 配線（2026-10-18 オーナー承認分）

決定論セッションバンク（LLMなし）を GameSwitch 経由の bounded 表示コーナーとして配線する。
doushi（2026-10-15 オーナー承認の auto-rotation 対象）をライフサイクルの正本テンプレートとし、
receipt identity 検査・write-ahead state・復帰契約を同一に保つ。

| コンポーネント | 役割 |
|---|---|
| `src/docich/lawsim_sessions.py` | 著作済みセッションバンク（3本: 計画達成 / 支持崩壊 / 計画未達）。generation ごとに1ステップ回転する決定論選択。LLM brain は別承認のため存在しない |
| `src/docich/lawsim_view.py` | loopback 専用 HTML view（port 8808）。payload は**セッションのみ**を保持し、表示時にエンジンで完全再導出する fail-closed 構成。改ざんファイルは配信へ出ない |
| `src/docich/lawsim_corner.py` | GameSwitch 統合ライフサイクル（state `lawsim_corner.json`、既定3分・上限14分、手動停止・interrupted・settle 契約） |
| `src/docich/adapters/lawsim_program.py` | program view adapter。preflight でセッションを構築・エンジン検証・payload 書き出し、readiness で `/api/lawsim` の identity 一致を確認 |
| `src/docich/corner_adapters.py` | `LawSimCornerAdapter`（`ADAPTERS["lawsim"]`、audio_enabled は fail-closed で拒否）＋ `RetiredCornerObserver.STATE_FILES` |
| `src/docich/corner_catalog.py` | `lawsim` 分岐（id=`communist-law-simulator` / game=`lawsim-view`、duration 1–14、fetch_on_start・audio_enabled 拒否） |
| `src/docich/adapters/__init__.py` | `make_coordinator_adapter` へ `lawsim-view` を登録 |
| `src/docich/webui.py` | `CORNER_MANUAL_STATE_FILES` へ `lawsim_corner` を追加（WebUI 手動予約・停止経路） |
| `config/docich.soren-live.toml` | `[corner_rotation].corners` へ `enabled = true` で登録（自動抽選対象） |

- 表示: 960×540 標準ゲーム領域（全画面例外の weather/yosou 型には**しない**）。1 frame 12秒で start → 法案 → end を回転。合成シミュレーションマークと model caveat を常時表示。
- 読み上げ（narration/TTS）は未配線。`audio_enabled=true` は catalog・adapter の両方で拒否する（follow-up）。
- 本番配備は canonical 経路（protected main → VM operations）のみ。実機受入（起動・表示・枠整合・バナー/音声）は owner 側の実行レーンで実施する。

## 後続カード（このPRでは実施しない）

1. **LLM 法案提出 brain**: 厳密スキーマ（`law_id` の選択のみ）で同志AIが毎回の法案を決める。エンジン側は `parse_session` → `simulate` の純粋な口をすでに用意しており、brain はこの口に流す。
2. **配信 overlay の HTML view** と `broadcast_view` 標準（`docs/broadcast-html-view-standard.md`）への適合、status overlay への接続。
3. **corner adapter / rotation 登録**（`config/` と `src/docich/corner_adapters.py`。`validate_game_name("communist-law-simulator")` は既存の命名規則に合致）と、GameSwitch 切替・復帰の実装。
4. **視聴者参加**: チャネルポイントでの法案提出・採決（#699 参加型系との統合判断を含む）。
5. 本番配備・実機受入・目視（owner-only ゲート）。

## 検証記録（計画・実装段階）

- 分岐元 `origin/main` `4c555712`。専用 worktree で作業（`feat/697-communist-law-simulator`）。
- 変更ファイル: `src/docich/communist_law_simulator.py`、`tests/fixtures/communist_law_simulator/fixed_law_session_example.json`、`tests/test_communist_law_simulator_contract.py`、本doc。`src/` と `tests/` と `docs/` 以外は変更しない。
- ローカル実行: `tests/test_communist_law_simulator_contract.py` 27 passed、隣接する新コーナー契約テスト（world-history / architecture）26 passed、`compileall` 相当の構文検査 OK。フルスイートは CI に委ねる。
- 本番接続・配備・rotation 登録・LLM 呼び出しは未実施。
- 作業中バナー/音声: この実行環境に authorized な VM/webui 経路が無いため未操作（本PRで記録）。
