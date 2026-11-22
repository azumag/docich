# IMPLEMENT-5065 — 共産主義法律シミュレーター改善（表示の可読性・因果の見える化・娯楽性）

対象: docich#5065。分析は `ANALYSIS-5065.md`。

## 方針

- エンジン `src/docich/communist_law_simulator.py`（正典・契約テスト固定済み）は**無変更**。`progress_frames` / `validate_status` / `render_overlay` の出力と fail-closed 契約を壊さない。
- 追加要素はすべて「エンジンが検証済みの出力（overlay/turns）＋ `LAWS` のレビュー済み定義」から**決定論的に再導出**する。乱数・時計・外部入力は使わない。
- 変更はコーナー内部（view と narration）に限定し、GameSwitch・rotation・catalog・adapter 契約は触らない。

## 変更ファイル

### 1. `src/docich/lawsim_view.py`

payload（`/api/lawsim` の JSON）に表示用フィールドを追加。すべて既存検証データの再導出で、fail-closed 経路は不変。

- `turns[]` に追加: `lawId` / `summary` / `risks` / `categoryLabel` / `votesFor` / `reaction`
  - `votesFor`: 場内採決率（合成の演出数値）。`55 + 直前support×0.3 + support差分×1.2` を 25–99 にクランプ。人気法案・支持が高い世情ほど可決率が上がり、支持を削る法案は下がる決定論ルール。
  - `reaction`: 議場リアクション 1 行（警告発火 → 「議場に緊張が走った」、支持大幅増 → 「労働者席が沸き立った」等の決定論ルール）。
- トップレベルに追加: `rules`（goal/collapse/premise の日本語ルール文）、`categoryLabels`、`warningMeta`（警告指標→ラベル・閾値・方向。`WARNING_THRESHOLDS` から導出）。

HTML（960×540 ビュー）を改修:

- **法案カード**（turn frame）: カテゴリバッジ + 「法律内容: summary」+「懸念: risks」+ 場内採決率（数値＋バー）+ 議場リアクション。
- **ルールパネル**（start frame）: 達成条件・崩壊条件・固定近似則の前提を明示。header に「目標: 計画100に到達」タグを常時表示。
- **スポイラー解消**: `finalStatusText`/`finalStatus` は閉会 frame でのみ表示。`#status-text` は審議中に「審議中・残りN法案」、開会時は「審議の開始を待機中」。`#republic` は会期名のみ。
- **差分の見える化**: 指標バー右端に +N/-N バッジ（`planGain` は計画バーへ合算）。警告閾値内側の指標は `.danger` で強調。
- **警告の因果表示**: イベント文に「（外貨が下限10を突破）」等の閾値を添付。
- **進行の見通し**: 「第N回会議（全M回）」「残りN法案」「次の議題: X」「これが最後の議題。次は閉会と結果発表」。
- **end frame の勝敗演出**: `.win`（plan_achieved=金）/`.lose`（collapse_*=赤）/無印（未達=琥珀）の verdict バナー + 「全会期の変化」サマリ（初期→最終・差分）。
- 既存契約は維持: canvas 装飾（z-index:-1, visibilitychange 連動）、cover-fit、`lang="ja"`/`notranslate`、CSP、loopback のみ、`PAGE_SECONDS=12`。

### 2. `src/docich/lawsim_narration.py`

- `_delta_bits` を新設し、指標を `METRIC_LABELS` の日本語名で読む（`production+6` → `生産+6`）。
- turn 実況に**法案の summary**・**計画前倒し**（`計画前倒し+4`）を追加し、`metricsAfter` の羅列も日本語化（`現在の指標: 生産45、…`）。
- `announcement()` の narration payload の `turns` に `summary` を補完（`LAWS` 参照）。
- 枠組み（MAX_SPEECH 上限・best-effort・audio_enabled opt-in）は不変。

### 3. `tests/test_lawsim_corner.py`

追加した契約テスト（#5065 節）:

- `test_view_payload_carries_law_cards_rules_and_warning_meta`: rules/categoryLabels/warningMeta と turn の lawId・summary・risks・categoryLabel・reaction・votesFor の存在・内容。
- `test_view_payload_votes_are_deterministic_and_bounded`: 採決率が同一 payload で同一値・整数・25–99 内。
- `test_view_html_hides_final_status_until_the_end_frame`: 進行中に `finalStatusText` を出さないこと・`republic` 行に status を混ぜないことを HTML 契約で固定。
- `test_view_html_shows_rules_goal_law_card_and_next_agenda`: ルールパネル・法案カード・採決・次の議題の存在を HTML 契約で固定。
- `test_narration_uses_japanese_metric_labels`: `生産+5`・`計画前倒し+4`・summary を含み `production` が出ないこと。

### 4. `docs/features/communist-law-simulator.md`

表示仕様節に #5065 の改善内容を追記（表示・実況の契約の記録）。

### 5. `ANALYSIS-5065.md`（新規）

原因分析の成果物。

## バージョン採番について

- エンジン `ENGINE_ID = "law-sim-v1"` は無変更のままが正しい: エンジンの計算結果（トレース・指標・終端・frame 列）は今回一切変更していない。画面側の追加フィールドは全て表示層の派生値であり、trace_signature・golden 値・validate_status の同一性検査に影響しない。
- view モジュール・narration モジュール・payload にバージョン採番の既存規約はないため、今回は採番しない（リポジトリ内の他 view も同様）。

## テスト

- **本環境では pytest / python3 の実行が拒否されたため、テストは未実行**（git コマンドのみ許可）。以下はコード読みによる検証。
- 追加テストは既存ヘルパ（`_stored_body`, `load_payload`, `HTML`）の上に立てており、import 追加は不要（`HTML`/`load_payload` は既存 import 済み）。
- 既存テストとの整合を確認済み:
  - `test_view_payload_rederives_the_trace_from_the_session` — payload の既存キー（ok/corner/sessionIndex/finalStatus/frames/turns/statusEntry/caveatFooter/pageSeconds）は全て維持。
  - `test_view_serves_the_board_with_runtime_identity` — HTML は `<title>共産主義法律シミュレーター` と h1 を保持。
  - `test_lawsim_view_uses_fullscreen_cover_fit` / `test_lawsim_view_has_throttled_canvas_scene` — `Math.max(...)`、`<canvas id="scene">`、`visibilitychange`、`setInterval(sceneTick,250)`、`canvas#scene{...z-index:-1` を保持。
  - `test_served_japanese_pages_declare_notranslate` — `<html lang="ja" translate="no">` と `<meta name="google" content="notranslate">` を保持。
  - `test_lawsim_narration_is_bounded_and_covers_session` — 法案名と MAX_SPEECH 上限を維持。
  - `test_lawsim_narration_frame_timing_contract` — `announcement` のシグネチャ・戻り値構造は不変。
  - `test_broadcast_view_disables_browser_translation` — adapter/viewer command 無変更。
  - エンジン契約テスト `test_communist_law_simulator_contract.py` — エンジン無変更のため影響なし。
- 期待される実行コマンド（レビュー側/CI で実施）:
  `PYTHONPATH=src python3 -m pytest -q tests/test_lawsim_corner.py tests/test_communist_law_simulator_contract.py tests/test_broadcast_view_standard.py`

## 残件・確認事項

- 実機での見た目（960×540 内での要素の収まり、カード行の高さ）はローカルでブラウザ確認できていない。実機受入時の目視で、法案カード + バー6行 + 警告 + agenda が board 内に収まることを確認されたい（board 高は `calc(100% - 122px)` に拡大済みだが、overflow:hidden で切れる可能性は残る）。
- 採決率・議場リアクションは合成の演出値であり、実投票ではない。合成表示の境界（合成マーク常時表示）を維持している。
- 次の議題は事前に見せる設計（実況の予感を作る狙い）。ネタバレと見なす判断なら `agenda` 行を外せる。
