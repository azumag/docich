# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 02:39 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: 二重読み上げ（nightbot）と戦略改善 no-apply を調査・修正、soviet_now へコミット・push まで完了（VM反映は次）

## 🎯 ゴール / タスク

1. **二重読み上げ修正**（夜間 nightbot 通知が二回読まれる）— `twitch_chat` の 60s TTL による再送重複と `enqueue_audio_text` の dedup 無しが原因と特定、TTL延長 + audio dedup + ストリーミング誤リトライ緩和で修正。
2. **戦略改善 no-apply 修正**（よく失敗）— `eloop_improve.sh` の `self-report` 誤検出（`不要`/`冗長` 等の謙虚表現を hard fail）と `string-only` の情報不足が主因と特定、self-report を advisory 化 + string-only エラー詳細化で修正。
3. **Phase 2→3→4 完走は完了**（`.env` 5キー enforce、VM 127件 STATGATE を実測）。
4. **web UI プロンプト管理は完了**（Peak右 Prompts 37件、VM 37件を実測）。

## ✅ やったこと（実測で確認済み）

- **Phase 2→3→4 完走**（前 handoff 02:37 どおり、VM実測済み）
  - `VM:.env` に `MIN_GAMES_FOR_BEST_ROLLBACK=100` / `STAT_GATE_MODE=enforce` / `DEAD_REGRESSION_ENABLED=1` / `IMPROVE_FUTILITY_RELEASE_ENABLED=1` / `MIN_GAMES_BEFORE_IMPROVE=48` を原子追記、5キーと `STATGATE` 127件（`02:36:02 NOT_A_LOOK n=94` が enforce後）を実測。`docich-webui` 1461345 active、`api/prompts` 37件を再実測。`9c42058` で push・VM `git reset --hard origin/main` で同期済み。

- **二重読み上げ調査**（opus `ses_fdf68b18cffe9v1NPVPsQxJ566`）
  - パイプライン `lib/outbound_queue.sh:328 enqueue_audio_text` → `tmp/.comment_queue/comment_*.txt` → `workers/audio_worker.sh:246 _play_comment_queue` → `say_enqueue.sh` を実測。`nightbot` は `TWITCH_IGNORE_AUTHORS` から意図的に除外（`twitch_chat_daemon.sh:98`）のため raid 用に ingest され、`broadcast/comment.sh:1526` で `raid` 分類。`recent_line_hashes` 60s と `SEEN_LINE_TTL` 60s が `processed_line_hashes` 1800s より短く、60s後の再送で2nd queueファイルが生成されるのが最有力（H1）。`enqueue_audio_text` に dedup が無い（`lib/outbound_queue.sh:204` の chat dedupに対し audio は無し）のが H2、`say_enqueue.sh:1119` の truncated 誤判定が H3。

- **no-apply 調査**（opus `ses_fdf66205bffemg5W2xC3yNT53u`）
  - `eloop_improve.sh` の hard ゲートを整理: `string-only` (`720,3611`) / `hash unchanged` (`3598`) / `self-report` (`790,3477`) / `diff empty` (`3580`) が hard、`fixed-turn`/`review` は advisory。`self-report` が謙虚表現（`不要`等）で誤爆し 3×6 budget を消費するのが H1、`string-only` がコメント/reason 変更で頻発するのが H2 と特定。ログは `tmp/debug/improve_ai.log` が不在で未確認だが、コード上の正規表現と budget 消費は実測で一致。

- **二重読み上げ修正**（`games/soviet_now` 5ファイル、未 push → 今回 push）
  - `twitch_chat_daemon.sh:18` `RECENT_LINE_HASH_TTL_SEC` 60→900、`twitch_chat.sh:33` `SEEN_LINE_TTL_SEC` 60→900（`TWITCH_RECENT_LINE_HASH_TTL_SEC`/`TWITCH_FETCH_LINE_HASH_TTL_SEC` で上書き可、既定900で `RECENT_MSG_ID_TTL 900` と整合、`core/config.sh:694` の `1800` より短いが再送抑止に十分）。
  - `lib/outbound_queue.sh` に `_comment_audio_cleanup_dedup_markers` と `_comment_audio_claim_enqueue_key`（`COMMENT_AUDIO_DEDUP_DIR=tmp/.comment_queue/audio_dedup`、mkdir原子 TTL 120）を追加、`enqueue_audio_text` と `enqueue_audio_file` の先頭で dedup（同一テキストは120s以内の2回目はキューを作らず `return 0`）。`bash -n` OK、ローカルで `enqueue_audio_text "hello duplicate test"` を2回呼び出し count 1のまま・別テキストは count 2と実測。
  - `say_enqueue.sh:90` `SAY_TRUNCATE_RATIO` 0.8→0.85（中間閾値を上げて誤リトライを減らす）。`bash -n` OK。

- **no-apply 修正**（同 `games/soviet_now` コミットに含む）
  - `eloop_improve.sh:790` `_implementation_self_report_rejects_change` の正規表現を `redundant.*(change|modification)|does not change behavior|harmless but unnecessary|no[ -]?op|self-report.*redundant` に狭窄（裸の `不要`/`冗長` 等を除外）。ローカルで `不要`→ not rejected、`redundant change`→ rejected と実測（以前は `不要` も rejected だった）。
  - `eloop_improve.sh:3477` の self-report ブロックを hard `VALIDATE_ERROR` + `continue_retry` 消費から advisory 化（`log self-report advisory` + `_improve_note` のみで budget 消費せず、次の string/hash ゲートで再判定）。`bash -n` OK。
  - `eloop_improve.sh:3611` の string-only ブロックを詳細化: `python3 -` で `string literals a->b, code nodes c->d, ex: diff snippet` を生成し `log` と `VALIDATE_ERROR` に付記（例: `reason` 文字列変更だけでなく `decide()` 内の数値・分岐を変えよとガイド）。`bash -n` OK。
  - `soviet_now` で `2b7813a0a fix: prevent duplicate TTS and reduce no-apply failures` をコミット、`git push origin HEAD:main` で `d2655eac7..2b7813a` を push 済み（exit 0）。`git log` 2件を実測。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `9c42058`（`origin/main` も `9c42058`、前回の prompts+Phase Aで同期）。`games/soviet_now` は `2b7813a`（`origin/main` も `2b7813a`、今回の二重読み+no-apply 5ファイルで `d2655ea..2b7813a`）。`docich` 側で `games/soviet_now` は `M`（`d2655ea..2b7813a` の gitlink差分、未コミット）。`git -C games/soviet_now status` は clean（5ファイルはコミット済み）。`docich` の `git status` は `M games/soviet_now` + `M handoff.md`（本ファイル）+ 未追跡 `??` のみ。
- **VM 本番**: `VM:/home/ubuntu/docich` は `9c42058`（`git log` で 2件を実測、status clean、docich-webui 1461345 activeで `api/prompts` 37件を実測）。`VM:/home/ubuntu/soren` はコードはまだ `d2655ea` 相当（VMの soren は非gitで `2b7813a` の 5ファイルが未反映）、`.env` は `enforce` 5キー（`STAT_GATE_MODE=enforce` 等）を実測。`soren_loop` の `STATGATE` 127件、`best_strategy_anchor n=85` は継続。VMの soren 5ファイル（`twitch_chat*`、`outbound_queue.sh`、`say_enqueue.sh`、`eloop_improve.sh`）は scp 未反映のため、次で VM へ原子置換が必要。
- **検証**: ローカルで `bash -n` 5ファイル OK、`enqueue_audio_text` dedup 1件維持を実測、`self-report` の `不要` が not rejected になることを実測。VMの `twitch_chat` TTL と `say` ratio はまだ旧値（60/0.8）のまま（未反映）。
- **作業中バナー**: `調査中` → `設計中` → `実装中` → `検証中` → `コミット中` → `handoff更新中` と粒度更新、現在 `handoff更新中` で active。

## ⏭️ 次にやること

1. **docich 側の gitlink bump をコミット・push**（最優先）: `git -C /Users/azumag/work/docich add games/soviet_now handoff.md && git commit -m "chore: bump soviet_now to 2b7813a — fix duplicate TTS + no-apply"` → `git push origin HEAD:main` と `HEAD:codex/soren-repo-handoff`。
2. **VM soren へ 5ファイル反映**（原子置換 + sha256一致確認）: `VM:/home/ubuntu/soren/twitch_chat_daemon.sh` / `twitch_chat.sh` / `lib/outbound_queue.sh` / `say_enqueue.sh` / `eloop_improve.sh` を `scp` で置換、`bash -n` と `grep -c`（TTL 900, dedup 120, ratio 0.85, self-report narrow）で実測。`soviet_now` の worker は `eloop_improve.sh` 変更のため次回 `improve` 起動時に自動で新ロジックが読まれる（`soren_loop` が毎ゲーム `eloop_lib.sh` を再読込）が、念のため `improve_daemon` の動作はログで確認。
3. **VM検証**（二重読み）: `grep '\[STATGATE\]'` は継続、`tmp/.comment_queue/audio_dedup` に dedup マーカーが作られること、`tmp/.twitch_chat/recent_line_hashes.log` の TTL が 900で保持されることを `grep` で実測。nightbot の二重通知が再現しないことを配信ログで1日観測。
4. **VM検証**（no-apply）: `grep -c "self-report advisory" /home/ubuntu/soren/logs/improve_daemon.log` と `grep "文字列・reason文言のみ" /home/ubuntu/soren/logs/soren_loop.log` で新ログが詳細付きで出ることを実測。`improve` の `failed_no_apply` 率が下がることを `grep -c "failed_no_apply" logs/soren_loop.log` で週次確認。
5. **残課題**: 画面解析導入の設計（opus委任）、Phase B/C の合意ゲート。

## 📂 重要なファイル

- `games/soviet_now/twitch_chat_daemon.sh:18` — `RECENT_LINE_HASH_TTL 60→900`
- `games/soviet_now/twitch_chat.sh:33` — `SEEN_LINE_TTL 60→900`
- `games/soviet_now/lib/outbound_queue.sh:240` — `_comment_audio_*` dedup（TTL120）と `enqueue_audio_text:378`/`enqueue_audio_file:403` での先頭 dedup
- `games/soviet_now/say_enqueue.sh:90` — `SAY_TRUNCATE_RATIO 0.8→0.85`
- `games/soviet_now/eloop_improve.sh:790` — `_implementation_self_report_rejects_change` 窄め、`3477` advisory化、`3611` string-only詳細化（`86` 行追加）
- `games/soviet_now` `2b7813a` — 上記5ファイルのコミット（`d2655ea..2b7813a`）
- `soren-stat-gate-design.md` — Phase3/4設計（enforce 5キー）
- `/home/ubuntu/soren/.env` — `enforce` 5キー（`STAT_GATE_MODE=enforce` 等）
- `handoff.md` — 本ファイル

## 🧭 決定と前提

- **二重読みは ingest（nightbot）を落とさず audio層で dedup**。`nightbot` は `TWITCH_IGNORE_AUTHORS` から除外しない（`broadcast/comment.sh:1526` で `raid` に必要なため）。重複抑止は `twitch` の 60s→900s 延長（`recent_msg_id 900` と整合）と `enqueue_audio_text` の 120s dedup（`tmp/.comment_queue/audio_dedup` mkdir原子）で二重化。`say_enqueue` の 0.85 は誤リトライの追加緩和。
- **no-applyは hardゲートを弱めず advisory化と情報付与**。`string-only` の AST 判定は維持（ロジック変更を強制するため）、self-report は謙虚表現の誤爆を避けるため advisory 化し `continue_retry` を消費しない。string-only エラーは `string literals a->b` と diff snippet を付けて次の fix で数値・分岐を変えられるようにガイド。
- **soviet_now の 5ファイルは一度に1コミット**（`2b7813a`）とし、`docich` 側は gitlink bump + `handoff.md` で1コミット（次で push）。VMの soren は非gitのため `scp` 原子置換 + `sha256` 一致で反映（`docich` と `soren` の二重管理は維持）。

## ⚠️ 未解決・ブロッカー・落とし穴

- **VMの soren 5ファイルは未反映**（`d2655ea` のまま）。`docich` の gitlink は `2b7813a` に進んだため `M`。次で VM へ `scp` し `bash -n` と `grep -c` で実測しないと乖離が残る。
- **Phase3/4の7日観測は継続**（`STATGATE` 127件、週 churn 6以下かはこれから）。`.env` の 5キー enforce は `.env` 1行で `shadow` に即時ロールバック可能（再起動不要）。
- **anchor n=85 < 100** で fallback中（mature 0-2件）。`MIN_GAMES=100` の効果は `rolling_scores.json` の mature数が 100に育つまで限定的。
- mtime粒度1秒で同一秒 dedup は `mkdir` 原子で保証されるが、異なるテキストの偶然衝突は `md5` で無視できる。
- `tmp/.comment_queue/audio_dedup` は TTL120で古いマーカーを掃除（`_comment_audio_cleanup_dedup_markers`）。`50` 件制限の `played_hashes.txt` と二重化しているが、enqueue側の dedup が先に効くため再生側は安全網。
- VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`systemctl --user status docich-webui`（`1461345`）、`curl -s http://127.0.0.1:8787/api/prompts | python3 -m json.tool`（37）
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY|MIN_GAMES_BEFORE_IMPROVE' /home/ubuntu/soren/.env`
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（127） / `grep '\[STATGATE\]' ... | sed ... | sort | uniq -c`
- 二重読み検証: `grep -E 'RECENT_LINE_HASH_TTL|SEEN_LINE_TTL' /home/ubuntu/soren/twitch_chat*.sh`（900） / `grep -c 'audio_dedup' /home/ubuntu/soren/lib/outbound_queue.sh` / `bash -n` 5ファイル / `ls /home/ubuntu/soren/tmp/.comment_queue/audio_dedup | wc -l`
- no-apply検証: `grep -c "self-report advisory" /home/ubuntu/soren/logs/soren_loop.log` / `grep "文字列・reason文言のみ" /home/ubuntu/soren/logs/soren_loop.log | head`
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` / `stop`

## 🔗 参照

- `games/soviet_now/twitch_chat_daemon.sh:18` / `twitch_chat.sh:33` — TTL 900
- `games/soviet_now/lib/outbound_queue.sh:240` — audio dedup
- `games/soviet_now/say_enqueue.sh:90` — 0.85
- `games/soviet_now/eloop_improve.sh:790,3477,3611` — self-report / string-only
- `games/soviet_now` `2b7813a`（`d2655ea..2b7813a`）
- `soren-stat-gate-design.md`（E移行表）
- `/home/ubuntu/soren/.env` — enforce 5キー

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -2` と `git -C . status`（`M games/soviet_now` が `2b7813a` か）、`ssh ubuntu@129.146.54.105 "grep -E 'RECENT_LINE_HASH_TTL|SEEN_LINE_TTL' /home/ubuntu/soren/twitch_chat*.sh; grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK' /home/ubuntu/soren/.env"` を実測してから、`games/soviet_now` の `M` を `git add/commit/push` し VMの soren 5ファイルへ `scp` 反映すること。
