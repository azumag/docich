# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 01:51 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: web UI から各種プロンプト参照・変更機能を実装・VM反映（Peak右にPromptsタブ）まで完了（未コミット）

## 🎯 ゴール / タスク

1. **web UI から各種プロンプトを参照・変更**（ユーザー指示 2026-08-21）— `prompts/` + `soren91/prompts/` の Markdown を webui で一覧・編集。実装・VM反映まで完了、Peak右に `Prompts` タブで表示されることを VM の tailscale URL (`soren-prod-vnc.tail85a080.ts.net`) 経由で実測。
2. **基盤共通化の残課題を順番に着手**（ユーザー指示 2026-08-20: 順番に）。残課題は docichcc 二重管理解消 → soviet_now 残ラッパー化 → sorengame 移管 → 半熟英雄 bring-up。
3. **Phase 2（統計判定ゲート shadow 化）の7日間観測継続**（VM `STAT_GATE_MODE=shadow` 稼働中）。

## ✅ やったこと（実測で確認済み）

- **web UI プロンプト管理機能を実装**（`src/docich/webui.py` 約350行追加、opus設計 `ses_fdff98f8effexUu9zYTon9VEVP` を採用）
  - 定数 `PROMPTS_REL_DIRS=["prompts","soren91/prompts"]` / `PROMPTS_MAX_BYTES=200KB` / `PROMPT_FNAME_RE` / `PROMPTS_PREVIEW_LEN=500` を `src/docich/webui.py:91` に追加。
  - ヘルパー `src/docich/webui.py:136` `_prompts_dirs` / `_prompt_mtime` / `_resolve_prompt_path`（`..`/`//`/`\\`/`\x00` 拒否、先頭文字検証、許可dir `is_relative_to` で traversal 遮断）/ `_validate_prompt_content`（200KB・制御文字拒否）/ `_list_prompts`（`*.md` を `PROMPT_FNAME_RE` と `._`/`.` 除外で 64件上限、preview 500文字）/ `_atomic_prompt_write`（`tmp/state/.webui_prompts.lock` mkdirロック 30s stale回収、mtime楽観ロック、`.bak.<ns>` 600、mkstemp+fsync+replace 644）。
  - API `src/docich/webui.py:3930` `_handle_list_prompts` / `_handle_get_prompt` / `_handle_put_prompt`（`_read_body` 413、`invalid_id` 400、`conflict` 409、`read_only` 403、`not_found` 404）、`do_GET` `src/docich/webui.py:2812` と `do_PUT` `src/docich/webui.py:2832` に `/api/prompts` 分岐追加。
  - `MAX_BODY_BYTES` 64KB→256KB（`src/docich/webui.py:1192`）、`run_webui` dry-run の endpoints に `/api/prompts` 追記。
  - フロント `src/docich/webui.py:1284` nav `Prompts` ボタン、`src/docich/webui.py:1448` `tab-prompts` section（一覧テーブル `#prompts-table`、編集 `#prompts-content` 22行、preview `<pre>`）、JS `src/docich/webui.py:1489` `promptsState` と `loadPrompts`/`loadPrompt`/`updatePromptsPreview`/`savePrompt`（409時競合トースト）、タブ切替 `src/docich/webui.py:2702` とボタン配線 `src/docich/webui.py:2781`。
  - 検証: `python3 -m py_compile src/docich/webui.py` OK、`python3 -m pytest tests/test_webui.py -q` 52 passed、一時 soren_root で `ThreadingHTTPServer` 起動し `GET /api/prompts` 2件・`GET single`・`PUT` 更新と mtime競合409・traversal 400・control 400・size超過400・read_only 403・not found 404・token 401→200 を curl相当で実測。実 `games/soviet_now` で `GET /api/prompts` 37件（`prompts/`32+`soren91/prompts/`5）と `improve_strategy.md` に「品質最優先」含むことを実測。

- **VM 本番反映と表示確認**（2026-08-21 01:49 JST）
  - ローカル `src/docich/webui.py` を `scp -i ~/.ssh/id_rsa` で `VM:/home/ubuntu/docich/src/docich/webui.py` へ原子置換、`grep -c 'data-tab="prompts"'` 0→1、`python3 -m py_compile` OK を実測。
  - `VM:/home/ubuntu/soren/prompts/._*.md` 9件（mac resource fork、163B）が `GET /api/prompts` に混入 → `_list_prompts` を `PROMPT_FNAME_RE` と `.`/`._` 除外で修正、再 scp・`systemctl --user restart docich-webui`（PID 1131700）し `curl 127.0.0.1:8787/api/prompts` 37件・`curl / | grep prompts` 1件を実測。`._*.md` 9件は `rm` で掃除済み。
  - ユーザー提示の `soren-prod-vnc.tail85a080.ts.net` スクショでは `Peak` 右に `Prompts` が未表示だったが、VM再起動後に `Prompts` が表示される（ハードリロード Cmd+Shift+R が必要）。`VM` の `docich-webui` は `1131700` で active、`/api/prompts` が 37件返すことを VM上で実測。

- **既存機能の維持**（前セッション分、再掲）
  - **作業中バナー「メリケンAI」削除**（2026-08-20 19:40）— `games/soviet_now` 5ファイル + `docich` `src/docich/webui.py` を `d2655eac7` / `798d511` で push・VM反映、`event_overlay.html` で「メリケン」0件実測。
  - **docichcc 二重管理解消 Phase A（docich 側、実装済み・未コミット）** — `native/ffmpeg/build.sh` MANIFEST.json 出力、`native/ffmpeg/README.md` / `docs/twitch_closed_captions.md` / `docs/multi_repo_plan.md §5.4` / `tests/test_native_captions.py` / `docs/handoff_common_parts.md §3.2` を変更。`python3 -m unittest tests.test_native_captions tests.test_captions` 33件 PASS、`bash -n` PASS。
  - **ピーク改善チェーン**（`eb906ccad` + VM `.env`）— `MODEL_IMPROVE_PEAK_LIST` / `IMPROVE_PEAK_CHAIN_ENABLED=1`、`PEAK_HOURS_TEST_NOW` で peak時切替を実測。
  - **Phase 2 shadow**（`f36e7ef21` + `ab9258a4f`）— B3分離・STATGATE shadow配線。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `798d511`（`origin/main` も `798d511`）。`games/soviet_now` は `d2655eac7`（`origin/main` と同期）。`git status` は `M docs/handoff_common_parts.md` / `M docs/multi_repo_plan.md` / `M docs/twitch_closed_captions.md` / `M games/soviet_now`（gitlink `ab9258a4f` のまま `M`）/ `M handoff.md` / `M native/ffmpeg/README.md` / `M native/ffmpeg/build.sh` / **`M src/docich/webui.py`（prompts 350行、未コミット）** / `M tests/test_native_captions.py` + 未追跡 `docich-integration/` 等。**`src/docich/webui.py` はローカルで prompts 追加済みだが git未コミット、originは依然 798d511。VMの `/home/ubuntu/docich/src/docich/webui.py` は scpで最新（promptsあり）に置換済みのため、VMとoriginが乖離（VMが新しい）。同期には commit→push→VMで正とした同期直しが必要。**
- **VM 本番**: `docich` は scp版（promptsあり、未コミット相当） / `soren` は `d2655eac7` 相当。`docich-webui` active (`1131700` 1:49 JST restart、`python3 -m docich webui --soren-root /home/ubuntu/soren`)、`curl 127.0.0.1:8787/api/prompts` 37件・`soren_root` `/home/ubuntu/soren` を実測。`.env` は `STAT_GATE_MODE=shadow` / `INSTADEATH_SPLIT_ENABLED=1` / `PEAK_HOURS_WINDOWS=10-13,15-19` / `IMPROVE_PEAK_HOUR_DEFER_ENABLED=0`（変更なし）。`prompts/._*.md` 9件は削除済み。
- **STATGATE 観測**: 前回 112件集計（`31 OK NONINFERIOR` / `60 PROMOTE INSUFFICIENT_REFERENCE` / `11 OK INSUFFICIENT_REFERENCE` / `10 OK NOT_A_LOOK`、`OK→REGRESSION` 0件）は継続観測中。今回セッションでは追加観測なし（未実測）。
- **作業中バナー**: 本セッションで `codex_work_indicator.sh start "handoff更新中"` → `stop` を実施予定（下記）。

## ⏭️ 次にやること

1. **prompts 変更のコミット・pushとVM正規同期**（最優先）: `src/docich/webui.py`（prompts 350行）をコミットメッセージ `feat(webui): add Prompts tab for prompts/soren91 editing` 等でコミット。`handoff.md` と Phase A 5ファイルの扱いを分離してコミット（Phase A は `docs/`/`native/` 別コミット、prompts は単独）。`origin/main` と `codex/soren-repo-handoff` へ push。VMでは `cd /home/ubuntu/docich && git fetch origin && git status` で乖離を確認し、`git diff` が prompts 差分のみであることを確認して `git reset --hard origin/main` または `git checkout -- src/docich/webui.py` 相当で正規同期（scp版はコミット版と同一のはずだが sha256突合せ）。`systemctl --user restart docich-webui` はコミット版でも再実測（`curl | grep prompts` 1件）。
2. **Phase A の残り手続き**: `handoff.md` を含めた Phase A 5ファイルのコミットがまだなら 1. と同時に実施。VM Phase C ビルドはユーザー合意ゲート・配信オフ時に実施（`native/ffmpeg/build.sh /home/ubuntu/build/docich-cc-$(git rev-parse --short HEAD)` → `MANIFEST.json` 確認 → `.env` の `SOREN_DIRECT_STREAM_FFMPEG_BIN` 更新 → direct_stream respawn）。
3. **Phase B（soviet_now 側、合意ゲート）**: `lib/closed_captions.py` を `docich caption` 委譲シムへ置換、`native/ffmpeg/` 7ファイル削除等。計画は opus `ses_fe005cc9cffe8Xi9kPxk8yFh0z`。
4. **Phase 2 shadow 観測継続**: 1日1回 `grep '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log | sed 's/.*legacy=\([A-Z]*\).*stat=\([A-Z_]*\).*/\1 \2/' | sort | uniq -c` で E基準確認（`OK→REGRESSION` 0件か）。PROMOTE増加傾向（60/112）に注目。
5. **残課題a-c**: soviet_now 残ラッパー化 → sorengame 移管 → 半熟英雄 bring-up（順番、ユーザー合意ゲート）。
6. **画面解析導入**: `src/docich/agent/` の observe 経路と `captions.py` 基盤を参照して設計（ユーザー指示 2026-08-20、要設計）。

## 📂 重要なファイル

- `src/docich/webui.py:91` — `PROMPTS_*` 定数（`PROMPTS_REL_DIRS` 等）
- `src/docich/webui.py:136` — promptsヘルパー群（`_prompts_dirs`/`_resolve_prompt_path`/`_validate_prompt_content`/`_list_prompts`/`_atomic_prompt_write`）
- `src/docich/webui.py:1192` — `MAX_BODY_BYTES=256*1024`（prompts 200KB対応）
- `src/docich/webui.py:1284` — nav `Prompts` ボタン
- `src/docich/webui.py:1448` — `tab-prompts` section（一覧+編集）
- `src/docich/webui.py:1489` — `promptsState` と `loadPrompts`/`loadPrompt`/`savePrompt`
- `src/docich/webui.py:2812,2832` — `do_GET`/`do_PUT` の `/api/prompts` 分岐
- `src/docich/webui.py:3930` — `_handle_list/get/put_prompt`
- `games/soviet_now/prompts/*.md` 32件 + `games/soviet_now/soren91/prompts/*.md` 5件 — 編集対象（`improve_strategy.md:1` 等）
- `games/soviet_now/codex_work_indicator.sh:13` — 作業中バナー制御
- `native/ffmpeg/build.sh` / `native/ffmpeg/README.md` / `docs/twitch_closed_captions.md` / `docs/multi_repo_plan.md` / `tests/test_native_captions.py` — Phase A 未コミット群

## 🧭 決定と前提

- **prompts 編集範囲**: `soren_root` 直下の `prompts/` と `soren91/prompts/` のみに限定（`PROMPTS_REL_DIRS`）。`id` は `dir/file.md` 形式、`PROMPT_FNAME_RE` で `A-Za-z0-9._-` のみ、`.`/`._` 除外、200KB・制御文字拒否。`soren_root` 外への `is_relative_to` 逃げを防ぐ。将来 `config/prompts` 等は含めない前提。
- **原子性・競合**: `.env` と同様の mkdirロック（30s stale）と mtime楽観ロック（409 `mtime mismatch`）で並行編集を防ぐ。`MAX_BODY_BYTES` を 64KB→256KB に上げたが Tailscale LAN 前提で DoS リスクは許容。バックアップは `*.bak.<ns>` 600、7日prune。
- **VM反映方式**: 今回は `scp` による直接置換 + `systemctl restart` で即時反映（`sha256`一致ではなく `grep prompts` 1件と `/api/prompts` 37件で実測）。正規は commit→push→VM `git pull` だが、ユーザーからの「Peak右に無い」即時解消を優先。次回コミットで正規同期し直す前提。
- **docichcc解消・Phase B/C** は前セッションの opus 計画を踏襲（薄いラッパー+バージョン付きビルドディレクトリ、ローカル fallback無し）。
- **表示**: プレTOTは raw `<pre>` エスケープのみ（markdownレンダラ無し、XSS対策）。`read_only` 時は `PUT` 403、`GET` は許可。

## ⚠️ 未解決・ブロッカー・落とし穴

- **`src/docich/webui.py` 未コミットでVMと乖離**: VMは scp版（promptsあり）、originは 798d511（prompts無し）。`git status` で `M`。push前に `git diff src/docich/webui.py` で prompts差分のみか確認し、Phase A 5ファイルと分離コミットすること。VM pull時は stash/上書きに注意。
- **VMの `prompts/._*.md`**: mac由来の resource fork 9件を削除済みだが、再 scp や Finder からのコピーで再発する。`_list_prompts` で除外しているため表示影響は無いが、定期的に `ls prompts/._*` で確認。
- **mtime 粒度 1秒**: `_prompt_mtime` は `int(st_mtime)` のため同一秒内の連続保存は409を検出できない（`_atomic_env_update` と同型）。人間操作では無視できるが、自動テストでは `sleep 1.1` が必要。
- **`c13837ddf` 不明**: VMの `docich-cc-c13837ddf` 由来不明は継続。Phase C の MANIFEST 化で解消予定。
- **macOS 実ビルド不可**: build.sh の x11grab/pulse 検査が通らず、Phase C は VMで実施。
- **STATGATE PROMOTE増加**: 60/112 で過半数。次回観測で再集計が必要（未実測）。
- `test_escape_mechanisms.py` は 108失敗（ローカル環境依存、無関係）。
- VMは他セッション共有、`codex_work_indicator.sh` はフェーズ毎に `start` 更新、完了時 `stop`。

## 🛠️ 環境・コマンド

- テスト: `python3 -m py_compile src/docich/webui.py` / `python3 -m pytest tests/test_webui.py -q`（52 passed） / `python3 -m unittest tests.test_native_captions tests.test_captions`（33件 PASS） / `bash -n native/ffmpeg/build.sh`
- 起動/反映: `scp -i ~/.ssh/id_rsa src/docich/webui.py ubuntu@129.146.54.105:/home/ubuntu/docich/src/docich/webui.py` → `ssh ubuntu@129.146.54.105 "python3 -m py_compile /home/ubuntu/docich/src/docich/webui.py && systemctl --user restart docich-webui"` → `curl -s http://127.0.0.1:8787/api/prompts | python3 -m json.tool` / `curl -s http://127.0.0.1:8787/ | grep prompts`
- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`/home/ubuntu/soren`（非git）、`/home/ubuntu/docich`（git）、`systemctl --user status docich-webui`（`127.0.0.1:8787`）
- 観測: `grep '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log | sed 's/.*legacy=\([A-Z]*\).*stat=\([A-Z_]*\).*/\1 \2/' | sort | uniq -c`
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` / `stop`（VMは `/home/ubuntu/soren/codex_work_indicator.sh`）

## 🔗 参照

- `src/docich/webui.py:91,136,2812,2832,3930` — prompts 実装
- `games/soviet_now/prompts/improve_strategy.md:1` / `analyze_strategy.md:1` / `review_strategy.md` 等 37件 — 編集対象
- `soren-stat-gate-design.md`（Phase 2 E移行表）
- `docs/multi_repo_plan.md` §5.4 / `docs/handoff_common_parts.md` §3.2 / `docs/twitch_closed_captions.md`（Phase A）
- `docs/architecture.md` / `docs/games/sorengame.md`
- VM反映: `VM:/home/ubuntu/docich/src/docich/webui.py` scp版（promptsあり、PID 1131700）

> 再開時: `/handoff load` で読んだ後、`git status` と `ssh ubuntu@129.146.54.105 "grep -c 'data-tab=\"prompts\"' /home/ubuntu/docich/src/docich/webui.py; curl -s http://127.0.0.1:8787/api/prompts | python3 -c 'import json,sys;print(json.load(sys.stdin)[\"count\"])'"`、作業中バナー状態を実測してから着手すること。`src/docich/webui.py` の未コミット差分は `git diff` で promptsのみか確認してからコミットすること。
