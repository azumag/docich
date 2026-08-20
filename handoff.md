# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 02:40 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: 二重読み上げと no-apply 修正を soviet_now 2b7813a でコミット・docich 8ac4853 で bump・VM soren 5ファイルへ反映まで完遂

## 🎯 ゴール / タスク

1. **二重読み上げ修正**（完了・VM反映済み）— nightbot 二重を TTL 900 + audio dedup 120で抑止、VMで 900/6件 dedupを実測。
2. **no-apply 修正**（完了・VM反映済み）— self-report advisory化 + string-only 詳細化、VMで narrow regex と `string literals` 詳細ログを確認。
3. **Phase 2→3→4 完走は完了**（`.env` 5キー enforce、VM 156件 STATGATE を実測、churn観測中）。
4. **web UI プロンプトは完了**（Peak右 Prompts 37件、VM 37件）。

## ✅ やったこと（実測で確認済み）

- **Phase 2→3→4** — `VM:.env` 5キー `enforce`/`100`/`1`/`1`/`48`、 `STATGATE` 127→156件（`02:36:02` 以降も継続）、`docich` `8ac4853`→`9c42058` で VM `git reset --hard origin/main` 同期、`docich-webui` 1461345 activeで `api/prompts` 37件を再実測。

- **二重読み上げ調査・修正**（`games/soviet_now` 5ファイル）
  - `twitch_chat_daemon.sh:18` 60→900、`twitch_chat.sh:33` 60→900、`lib/outbound_queue.sh:240` audio dedup（120s mkdir原子）、`say_enqueue.sh:90` 0.8→0.85。
  - ローカルで `enqueue_audio_text` 同一テキスト2回で count 1、別テキストで 2、 `bash -n` 5ファイル OK、 `不要` が not rejected になることを実測。
  - `soviet_now` で `2b7813a` をコミット・`push origin HEAD:main`（`d2655ea..2b7813a`）、`docich` で `8ac4853` として `games/soviet_now` bump + `handoff.md` コミット・`push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`（`9c42058..8ac4853`）を実測。
  - `VM:/home/ubuntu/soren` へ `scp` で 5ファイル原子置換、 `grep -n` で `900`/`6`/`0.85`/`redundant.*change`、 `bash -n` 5ファイル OK、 `VM:/home/ubuntu/docich` は `git fetch`→`submodule update` で `2b7813a` に同期（`git status` cleanを実測、`grep -c 900` 3件、`grep -c audio_dedup` 6件を実測）。

- **no-apply 修正**（同 `2b7813a` に含む）
  - `eloop_improve.sh:790` narrow、`3477` advisory化（budget消費せず）、`3611` で `string literals a->b` 詳細を `VALIDATE_ERROR` に付記。
  - VMで `grep -n 'redundant.*change' eloop_improve.sh` と `grep -n 'string literals' eloop_improve.sh` は未反映だったが、今回の 5ファイル scp で新ロジックを実測。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `8ac4853`（`origin/main` も `8ac4853`、前回 `9c42058` から bump）。`games/soviet_now` は `2b7813a`（`origin/main` も `2b7813a`）。`git status` は `??` 未追跡のみで `M` なし（`handoff.md` は本ファイルへ更新したが未コミット、次で反映）。`bash -n` 5ファイル OK、`pytest tests/test_webui.py` 52 passed は前回実測。
- **VM 本番**: `VM:/home/ubuntu/docich` `8ac4853`（`git log` 2件、`status` clean、`grep prompts` 1件を実測）、`VM:/home/ubuntu/docich/games/soviet_now` `2b7813a`（`git rev-parse` と `grep 900` 3件で実測）。`VM:/home/ubuntu/soren` はコード 5ファイル（`twitch_chat_daemon.sh:18 900` / `twitch_chat.sh:33 900` / `lib/outbound_queue.sh` dedup 6件 / `say_enqueue.sh:90 0.85` / `eloop_improve.sh:790 narrow`）を `scp` と `grep -c` で実測、`.env` は `enforce` 5キー（`grep -E` で5キー実測）。`soren_loop` の `STATGATE` 156件（`02:36:02` 以降も継続）、`docich-webui` 1461345 activeで `api/prompts` 37件を実測。
- **作業中バナー**: `調査中`→`設計中`→`実装中`→`検証中`→`コミット中`→`デプロイ中`→`handoff更新中` と粒度更新、現在 `handoff更新中` で active。

## ⏭️ 次にやること

1. **handoff のコミット・push**（残り）: 本ファイル（02:40版）を `git add handoff.md && git commit -m "docs: update handoff for duplicate TTS + no-apply VM reflected"` → `push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`。`handoff.md` はVMには不要だが履歴のため。
2. **1日観測**（二重読み）: `grep '\[STATGATE\]'` とは別に `ls /home/ubuntu/soren/tmp/.comment_queue/audio_dedup | wc -l` と `grep "self-report advisory" /home/ubuntu/soren/logs/soren_loop.log` で新ロジックが効いているか配信ログと合わせて確認。nightbot の二重が再現しないことを1日観測。
3. **1週間観測**（no-apply）: `grep -c "failed_no_apply" /home/ubuntu/soren/logs/soren_loop.log` の週次と `grep "文字列・reason文言のみ" ... | head` の詳細ログで `string literals` 詳細が付くことを確認。`string-only` が減るか、減らない場合はプロンプトの閾値調整へ。
4. **Phase 3/4 の7日観測は継続**（`STATGATE` 156件、週 churn 6以下か）。
5. **残課題**: 画面解析導入の設計、Phase B/C の合意ゲート。

## 📂 重要なファイル

- `games/soviet_now/twitch_chat_daemon.sh:18` — 900
- `games/soviet_now/twitch_chat.sh:33` — 900
- `games/soviet_now/lib/outbound_queue.sh:240,378,403` — audio dedup 120
- `games/soviet_now/say_enqueue.sh:90` — 0.85
- `games/soviet_now/eloop_improve.sh:790,3477,3611` — narrow/advisory/detail
- `games/soviet_now` `2b7813a` — 上記5ファイル
- `soren-stat-gate-design.md` — Phase3/4設計
- `/home/ubuntu/soren/.env` — enforce 5キー
- `src/docich/webui.py:91` — Prompts 37件

## 🧭 決定と前提

- 二重読みは ingest を落とさず audio層で dedup（nightbot は raid のため `TWITCH_IGNORE` から除外しない）。60s→900s は `recent_msg_id 900` と整合、audio dedup 120s は `mkdir` 原子で 60sの再送窓をカバー。
- no-apply は gateを弱めず advisory化と情報付与で fix。string-only は維持しつつ `string literals` 詳細で次回 fix を数値・分岐へ誘導。
- `soviet_now` 5ファイルは1コミット `2b7813a`、 `docich` は bump + handoff で `8ac4853`、VM soren は `scp` 原子置換 + `sha256`一致で反映（非git）。

## ⚠️ 未解決・ブロッカー・落とし穴

- **handoff.md は本ファイルが未コミット**（`M`）。次で `git add/commit/push` し `origin` と同期すること。
- Phase3/4の7日観測は継続（`STATGATE` 156件、churn 6以下かはこれから）。
- `tmp/.comment_queue/audio_dedup` は TTL120で掃除、 `played_hashes.txt` 50件と二重化だが enqueue側が先に効くため安全。
- VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`grep -E 'RECENT_LINE_HASH_TTL|SEEN_LINE_TTL' /home/ubuntu/soren/twitch_chat*.sh`（900） / `grep -c audio_dedup /home/ubuntu/soren/lib/outbound_queue.sh`（6） / `bash -n` 5ファイル
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY' /home/ubuntu/soren/.env`（enforce/100/1/1）
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（156）

## 🔗 参照

- `games/soviet_now/twitch_chat_daemon.sh:18` / `twitch_chat.sh:33` — 900
- `lib/outbound_queue.sh:240` — dedup
- `eloop_improve.sh:790,3477,3611` — narrow/advisory/detail
- `games/soviet_now` `2b7813a`
- `/home/ubuntu/soren/.env` — enforce

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -2` と `git status`（`M` なしか）、`ssh ubuntu@129.146.54.105 "grep -E 'RECENT_LINE_HASH_TTL|SEEN_LINE_TTL' /home/ubuntu/soren/twitch_chat*.sh; grep -E 'STAT_GATE_MODE' /home/ubuntu/soren/.env"` を実測してから着手すること。
