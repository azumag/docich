# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 04:55 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: #18 広告スヌーズと作業中VM読み上げキュー連携（リポジトリルール化）を soviet_now 6960a37 → docich 08d9bd5 で完遂、VM へ7ファイル反映・検証まで完了

## 🎯 ゴール / タスク

1. **#18 読み上げ中はTwitch広告をスヌーズ**（完了・VM反映済み）— `speaking` を `say_enqueue.sh` の実再生に紐付け、 `lib/twitch_ads.sh` で閾値600s・poll240sでスヌーズ、VMで 900/6件 dedup を実測。
2. **作業中→VM読み上げキュー連携をリポジトリルール化**（完了・VM反映済み）— `codex_work_indicator.sh` の `start`/`stop` で `VM` へ `enqueue_audio_text` を自動連携（120s dedup）、`AGENTS.md` §8 として文書化、 `ls` で 2件の `作業中:` ファイルを実測。
3. **二重読み上げと no-apply は 2b7813a で完遂**（TTL 900 + dedup、advisory 化）、VM 156件 STATGATE で観測中。
4. **Phase 2→3→4 完走は完了**（`.env` 5キー enforce、VM 156件）。
5. **web UI プロンプトは完了**（Peak右 Prompts 37件）。

## ✅ やったこと（実測で確認済み）

- **#18 設計**（opus `ses_fdf568e61ffeNJKYJixvbTPHie`）— `POST /helix/channels/ads/schedule/snooze`（`channel:manage:ads`、5分/回）と `GET /helix/channels/ads`（`snooze_count/next_ad_at`）を `channel:read:ads` で確認、 `speaking.json` を `tmp/state/speaking.json` に atomic 管理する設計を確定。作業中バナーは `codex_work_indicator.sh start` で `active:true` を実測、VM へ `enqueue_audio_text` で `comment_announce_*` を実測。

- **作業中→VM読み上げキュー連携を実装**
  - `games/soviet_now/codex_work_indicator.sh` に `_enqueue_work_audio`（80字丸め、120s dedup、SSH to VM）と VMオーバーレイ同期（`ssh ... ./codex_work_indicator.sh start ...`）を追加、`AGENTS.md:61` と `games/soviet_now/AGENTS.md:19` に §8 追記。`bash -n` OK、`./codex_work_indicator.sh start "テストVMオーバーレイ同期"` で VM側 `codex_work_indicator.json` と `comment_announce_*` ができることを実測（2件の `作業中:` ファイルを `ls` で実測）。
  - `VM` へ `ssh` で `enqueue_audio_text "作業中: 実装中 ..."` を `tmp/.comment_queue/comment_announce_*` に実測。

- **#18 実装**
  - `lib/twitch_ads.sh` 新規（600s閾値、240s poll、同一 `next_ad_at` dedup、429/401 backoff、log `tmp/debug/twitch_ads.log`、非同期 `&`）。`bash -n` OK、`TWITCH_ADS_ENABLED=0` で exit 0 を実測。
  - `core/config.sh:709` に 6行追加（`TWITCH_ADS_ENABLED` 等）、`bash -n` OK。
  - `say_enqueue.sh` に `SPEAKING_STATE_FILE`/`_speaking_enter`/`_speaking_leave`（`tmp/state/speaking.json` atomic、 `twitch_ads_maybe_snooze &` と 240s poll、 `_cleanup` で `kill` と grace 3s + キュー残確認）を追加、`2613` の再生直前に `enter` を追加、`_cleanup` に `leave` を統合。`bash -n` OK、`grep -n _speaking` 3件を実測。

- **soviet_now コミット・push** — `games/soviet_now` で `6960a37 feat: add Twitch ad snooze during TTS and work-to-audio queue rule`（`AGENTS.md`/`codex_work_indicator.sh`/`core/config.sh`/`say_enqueue.sh`/`lib/twitch_ads.sh` 5ファイル、311 ins）をコミット・`push origin HEAD:main`（`2b7813a..6960a37`）を実測。

- **docich bump・push** — `docich` で `08d9bd5 chore: bump soviet_now to 6960a37 — ad snooze + work audio rule + handoff`（`AGENTS.md` 9 ins + `games/soviet_now` 2 ins + `handoff.md` 69 ins）をコミット・`push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`（`783ad6b..08d9bd5`）を実測。

- **VM反映** — `VM:/home/ubuntu/soren` へ `lib/twitch_ads.sh`/`codex_work_indicator.sh`/`core/config.sh`/`say_enqueue.sh`/`AGENTS.md` を `scp` で原子置換、`grep -n` で `900`/`6`/`0.85`/`redundant.*change` と `bash -n` 5ファイル OKを実測。`VM:/home/ubuntu/docich` は `git fetch`→`reset --hard origin/main`→`submodule update` で `08d9bd5`/`6960a37` に同期（`git status` clean、`grep -c 900` 3件、`grep -c audio_dedup` 6件、`grep prompts` 1件を実測）。`soren` の `.env` 5キー `enforce` と `STATGATE` 156件、`api/prompts` 37件を再実測。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `08d9bd5`（`origin/main` も `08d9bd5`、前回 `783ad6b` から 3ファイル bump）。`games/soviet_now` は `6960a37`（`origin/main` も `6960a37`）。`git status` は `??` 未追跡のみで `M` なし（`handoff.md` は本ファイルへ更新したが未コミット、次で反映）。`bash -n` 4ファイル OK。
- **VM 本番**: `VM:/home/ubuntu/docich` `08d9bd5`（`git log` 2件、`status` clean、`grep prompts` 1件を実測）、`VM:/home/ubuntu/docich/games/soviet_now` `6960a37`（`git rev-parse` と `grep 900` 3件で実測）。`VM:/home/ubuntu/soren` はコード 7ファイル（`twitch_chat_daemon 900`/`twitch_chat 900`/`outbound_queue dedup`/`say 0.85` + `twitch_ads.sh`/`speaking.json`/`codex_work_indicator VM連携`/`AGENTS.md` §8）を `scp` と `grep -c` で実測、`.env` は `enforce` 5キー（`grep -E` で5キー実測）、`STATGATE` 156件（`02:36:02` 以降も継続）、`docich-webui` 1461345 activeで `api/prompts` 37件を実測。`VM` の `tmp/.comment_queue/audio_dedup` と `tmp/state/speaking.json` は今後の再生で生成される。
- **作業中バナー**: `設計中`→`実装中`→`検証中`→`デプロイ中`→`handoff更新中` と粒度更新、現在 `handoff更新中` で active。本 `handoff` 更新後は `stop` 予定。

## ⏭️ 次にやること

1. **handoff のコミット・push**（残り）: 本ファイル（04:55版）を `git add handoff.md && git commit -m "docs: update handoff for ad snooze + work audio VM reflected"` → `push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`。
2. **1週間観測**（#18）: 短文と長文（300字超の streaming 3チャンク）で `tmp/state/speaking.json` が再生中のみ `true` で `grace` 後に `false` になること、`tmp/debug/twitch_ads.log` に `snoozed` と `429` 時の backoff が残ることを配信ログで確認。連続 `enqueue_audio_text` 2件で `speaking` が flap しないことを実測。
3. **1日観測**（二重読み）: `ls /home/ubuntu/soren/tmp/.comment_queue/audio_dedup | wc -l` と `grep "self-report advisory"` で新ロジックが効いているか確認。
4. **Phase 3/4 の7日観測は継続**（`STATGATE` 156件）。
5. **残課題**: 画面解析導入の設計、Phase B/C の合意ゲート。

## 📂 重要なファイル

- `games/soviet_now/lib/twitch_ads.sh` — 新規（GET/POST、600s閾値、240s poll）
- `games/soviet_now/say_enqueue.sh:95,2613` — `speaking` と `enter`
- `games/soviet_now/core/config.sh:709` — `TWITCH_ADS_ENABLED` 等
- `games/soviet_now/codex_work_indicator.sh:68` — VMオーバーレイ同期 + `_enqueue_work_audio`
- `games/soviet_now/AGENTS.md:19` / `AGENTS.md:61` — §8 work-to-audio
- `games/soviet_now` `6960a37` — 上記5ファイル
- `/home/ubuntu/soren/.env` — enforce 5キー
- `soren-stat-gate-design.md` — Phase3/4設計

## 🧭 決定と前提

- #18 は `say_enqueue.sh` の実再生区間に `speaking` を紐付け、`lib/twitch_ads.sh` は配信中でない・権限不足では `log` のみで `return 0`。5分効果は `poll 240s` で繋ぐ。連続キューは `grace 3s` で flap抑止。
- 作業中→VM読み上げは `codex_work_indicator.sh` に統合（`7` と `8` は同じ `start` で発火）。ローカルでは `ELOOP_LIB_DIR` が `/home/ubuntu/soren` でないことを検出して `ssh` で VM へも同期。`120s dedup` で spam 抑止。
- `soviet_now` 5ファイルは1コミット `6960a37`、docich は bump + handoff で `08d9bd5`、VM soren は `scp` で反映。

## ⚠️ 未解決・ブロッカー・落とし穴

- **handoff.md は本ファイルが未コミット**（`M`）。次で `git add` し `origin` と同期すること。
- **Twitch API 権限要確認**: `TWITCH_BOT_TOKEN` に `channel:manage:ads` と `channel:read:ads` が付いているかを配信前に `curl` で実測しないと `401` で backoff。
- Phase3/4の7日観測は継続（`STATGATE` 156件）。
- `tmp/.comment_queue/audio_dedup` は TTL120で掃除、VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh`（900/1）、`cat /home/ubuntu/soren/tmp/state/speaking.json`、`cat /home/ubuntu/soren/tmp/debug/twitch_ads.log`
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY' /home/ubuntu/soren/.env`（enforce/100/1/1）
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（156）
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` → `cat tmp/state/codex_work_indicator.json` と `ssh ... cat /home/ubuntu/soren/tmp/state/codex_work_indicator.json`、`ls /home/ubuntu/soren/tmp/.comment_queue/comment_announce_*`

## 🔗 参照

- `games/soviet_now/twitch_chat_daemon.sh:18` / `twitch_chat.sh:33` — 900
- `lib/twitch_ads.sh` — 新規
- `say_enqueue.sh:95,2613` — speaking
- `codex_work_indicator.sh:68` — VM連携
- `AGENTS.md:61` — §8
- `games/soviet_now` `6960a37`
- `/home/ubuntu/soren/.env` — enforce

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -2` と `git status`（`M` なしか）、`ssh ubuntu@129.146.54.105 "grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh; ls /home/ubuntu/soren/lib/twitch_ads.sh 2>&1 | head"` を実測してから着手すること。
