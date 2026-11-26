# IMPLEMENT-5584 — new_corner_runtime 共通 production adapter（docich#5584）

## 概要

`CORNER_SPECS`（`src/docich/new_corner_runtime.py`、PR #4905 で main 入り済み）を
data-driven に消費する単一の production adapter 経路を新設し、初回発火対象
`geography-world` を production rotation で発火可能にした。コーナー固有の検索
HTTP や OpenCode research prepass は新設していない（#1767 の連携メモ契約）。
新経路は既定 OFF の環境フラグ `DOCICH_NEW_CORNER_ENABLED` でのみ有効化され、
OFF の間は既存 rotation への影響はゼロ（adapter が `eligible()=False` を返し、
除外理由は `adapter-unavailable` として記録されるのみ）。

## 変更ファイル

### 新規

- `src/docich/new_corner_production.py` — flag ゲート付き record 生成。
  `corner_id` → `spec_for` → テーマ候補選択（`list_theme_candidates` /
  `select_theme`、generation 決定論ローテーション）→ `web_material.collect_verified_web_material`
  で実材料取得 → `RADIO:new-corner-draft` の bounded direct-API chain
  （`DIRECT_CHAT_PROVIDERS` + `DIRECT_CHAT_MODELS` 両 allowlist 検証）で
  コンテンツ項目のみ生成 → record 組立 → `build_draft` / `render_overlay` /
  `progress_frames` / `status_overlay_entry` / `validate_status` で契約検証。
  provenance 項目（title/topicId/url/primarySources/labelsPolicy/segmentLabels/
  materialContract/mapHandling 等）は runner が決定論的に埋め、モデル出力には
  生成キー完全一致・URL混入禁止・有界長の制約をかける。失敗は全て固定の
  status 文字列（disabled / invalid_config / invalid_input / input_limit /
  material_unavailable / generation_failed / invalid_output / invalid_record /
  timeout）で返し、発火しない（fail-closed）。
- `src/docich/new_corner_view.py` — `CORNER_SPECS` 全コーナーに対応する汎用
  loopback HTML view。`view_name_for(corner_id)` = `<id>-view`、ポートは
  `PORTS` 辞書で明示割当（geography-world=8811）。payload 再検証は runtime
  契約関数を通す fail-closed。合成/実材料マーカーを常時表示。
- `src/docich/new_corner_manager.py` — 共有 GameSwitch スロットの bounded
  lifecycle owner（lawsim/doushi パターン準拠）。状態ファイルは全 newcorner
  行で共有の `new_corner.json`（`state["game"]` が owner view を記録し、
  別 owner の非終端 state への上書きは `NewCornerError` で拒否）。
  request + runtime identity + 固定終了時刻のみ永続化。試合終了待ち・
  結果保存・復帰・rollback 検証は GameSwitch の既存経路に委譲。
- `src/docich/adapters/new_corner_program.py` — GameSwitch program view
  adapter。`preflight` で `produce_record` を fail-closed 実行し、検証済み
  record を transient runtime file (`new_corner_record.json`) として view に
  渡す。`[new_corner] draft_agents` 未設定なら AdapterError で開始しない。
- `tests/fixtures/geography_world_corner/theme_candidates.json` — geography
  のテーマ候補（実テーマの seed。材料は production フローが検証する）。
- `tests/test_new_corner_production.py` — catalog/flag ゲート/テーマ選択/
  record 生成（fake collector+generator＝記録済み応答と等価）/view/lifecycle
  の contract テスト。合成 fixture と生成 record の両方で同一 runtime 契約を検証。

### 変更

- `src/docich/adapters/__init__.py` — `make_coordinator_adapter` に
  `<id>-view`（`id ∈ CORNER_SPECS`）の dispatch を追加。
- `src/docich/corner_adapters.py` — `NewCornerAdapter`、ADAPTERS に
  `"newcorner"`、RetiredCornerObserver.STATE_FILES に
  `"newcorner": "new_corner.json"`。
- `src/docich/corner_catalog.py` — `adapter == "newcorner"` 検証
  （id ∈ CORNER_SPECS、game = `<id>-view`、fetch_on_start 拒否、
  audio_enabled は bool、duration_minutes 1-14）。
- `config/docich.soren-live.toml` — catalog 行
  `{ id = "geography-world", adapter = "newcorner", game = "geography-world-view",
  enabled = true, duration_minutes = 4, audio_enabled = false }` と
  `[new_corner] draft_agents`（direct API の bounded chain）を追加。
- `ops/vm_actions/collect_diagnostics.py` — `CORNER_STATE_FILES` に
  `"new_corner"`、`ROTATION_PENDING_OWNER_FILES`、状態ファイル投影ループ、
  `end_reason` allowlist に `operator-moved-during-new-corner` /
  `released-failure-before-retry` を追加（§10 read-only diagnostics 観測経路）。
- `tests/test_broadcast_view_standard.py` — `geography-world-view` を
  BROADCAST_VIEWS / HTML マーカー検査に追加。

## Runtime 変更 checklist 確認

- **runtime registry / manifest**: 永続 worker は新設しない。view プロセスと
  ブラウザは GameSwitch 管理下の runtime（generation/lease 付き RuntimeSpec）
  のため `runtime_registry.WORKERS` への追加は不要。KNOWN_LANES 変更なし。
- **worker health 契約**: 新規 worker なし。既存の GameSwitch runtime 監視
  （canonical state / receipts / readiness）に従う。
- **queue registry**: 既存 corner catalog 行のみ。新規 queue 登録なし。
- **structured telemetry**: `run_prompt` 経由で `llm/telemetry.record()` が
  `RADIO:new-corner-draft` ラベルで stats/YYYYMMDD.jsonl に記録（共通基盤）。
- **diagnostics coverage**: 上記 collect_diagnostics 4 箇所に登録済み。
- **regression tests**: `tests/test_new_corner_production.py` 新設 +
  `test_broadcast_view_standard.py` 拡張。**未実行**（下記残件）。
- **secret-redaction**: 秘密情報をファイル/ログに書かない。view は request
  path/viewer identity をログしない（handler log_message 抑制）。material の
  URL と sha256 のみ保持。
- **deploy 影響**: flag OFF 既定のため反映のみでは無変更。ON にするには
  `DOCICH_NEW_CORNER_ENABLED=1` を rotation service の env に設定する正規
  リリース手順が必要（本 issue では ON にしない）。

## 環境制約で未実施・残件

1. **`git fetch` / `git branch` / `git checkout` が sandbox に拒否された**。
   worktree は detached HEAD `757f24fcd`（origin/main `6c7d3dae` より
   ~80 ファイル遅れ）。ブランチ `impl/5584-production-adapter` は作成
   できていない。実装は HEAD の内容に対して行い、origin/main の参照は
   `git show origin/main:<path>` で確認した。**残件**: origin/main を
   base にブランチを作り直して cherry-pick / 再適用し、origin/main との
   コンフリクト確認を行うこと（新規ファイル中心だが catalog/config/
   collect_diagnostics/adapters の差分は base 差異の影響を受けうる）。
2. **python / pytest の実行が拒否された**ため、テストは記述のみで未実行。
   **残件**: `pytest tests/test_new_corner_production.py tests/test_broadcast_view_standard.py tests/test_lawsim_corner.py` と flag OFF 状態のフル regression を CI/VM で実行すること。
3. ゴミファイル `wtest5584.tmp`（probe）が repo 直下に残っている。
   **残件**: コミット前に `rm wtest5584.tmp` で削除すること。
4. **version.py bump 不要**: runtime 変更は hanjuku 専用の bump 対象外
   （`src/docich/hanjuku/version.py` のみ存在し、近傍の非 hanjuku runtime
   変更は bump していない）。
5. **preview 検証・本番 flag ON は本 issue スコープ外**（issue 本文§4 より
   正規リリース順序で別途実施）。VM への本番反映・サービス再起動は実施
   していない。
6. **`git add` / `git commit` も拒否された**（git 書き込み全般）。変更は
   worktree 上に未コミットで残置。検品ゲート側で origin/main base の
   ブランチへ適用・コミットすること。
7. **Slack 投稿も失敗**: slack-remote MCP サーバの tool 一覧取得が失敗し、
   投稿できなかった。投稿文案は下記に残す。

## 代理投稿文（検品ゲートが掲載）

### issue #5584 コメント案

```
共通 production adapter を実装しました（branch: impl/5584-production-adapter 作成は環境制約で未実施、検品ゲート側で origin/main base で作り直し予定）。

- 単一の data-driven runner: corner_id → CORNER_SPECS → web_material.collect_verified_web_material（#1828 共有基盤）→ RADIO:* direct API → build_draft/render_overlay/progress_frames/status_overlay_entry/validate_status。コーナー固有の検索 HTTP・OpenCode prepass は新設していません。
- 初回発火対象 geography-world: catalog 行 + `geography-world-view`（loopback:8811）+ `[new_corner] draft_agents`。
- flag ゲート: DOCICH_NEW_CORNER_ENABLED（既定 OFF）。OFF の間 adapter は ineligible で既存 rotation への影響ゼロ。ON は preview 検証後の正規リリース順序で（本 issue では ON にしません）。
- ライフサイクル: GameSwitch 規約準拠（lawsim/doushi と同一パターン、共有状態ファイル new_corner.json）。
- テスト: 合成 fixture と記録済み応答ベースの実材料経路の両方で同一 contract テストを追加（tests/test_new_corner_production.py）。pytest 実行は環境制約で未実施のため残件です。
- diagnostics: collect_diagnostics に new_corner 状態ファイル・pending owner・end_reason を登録済み（§10 観測経路）。

実装記録: IMPLEMENT-5584.md
```

### Slack #agent-council 完了報告案（thread ts 1795604262.176699）

```
<@U0CCCH69XMW> docich#5584 実装完了報告です。
結論: 共通 production adapter（data-driven、CORNER_SPECS 消費）を実装し、geography-world の発火経路を flag ゲート（DOCICH_NEW_CORNER_ENABLED、既定OFF）付きで配線しました。実材料は web_material + RADIO:* direct API、lifecycle は GameSwitch 規約準拠、diagnostics 登録済み。
残件: (1) ブランチ作成が sandbox 拒否のため未作成（origin/main base での作り直し要）、(2) pytest 未実行、(3) flag ON は本 issue スコープ外。詳細は worktree の IMPLEMENT-5584.md。
```
