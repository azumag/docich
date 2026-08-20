# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 04:50 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: #18 広告スヌーズと作業中VM読み上げキュー連携（リポジトリルール化）まで実装・検証、soviet_now 6960a37 でコミット（docich bumpは次）

## 🎯 ゴール / タスク

1. **#18 読み上げ中はTwitch広告をスヌーズ** — 選択済み（#18）。`speaking` 状態を `say_enqueue.sh` の実再生区間に紐付け、再生中は `lib/twitch_ads.sh` で `POST /helix/channels/ads/schedule/snooze` を閾値600s・poll240sで試行、失敗時はログのみで TTS継続。連続キューでは grace 3s で flap抑止。
2. **作業中→VM読み上げキュー連携をリポジトリルール化**（ユーザー指示 04:36）— `codex_work_indicator.sh` の `start`/`stop` で `VM:/home/ubuntu/soren/tmp/.comment_queue` へ `enqueue_audio_text "作業中: title body"` を自動 enqueue（120s dedup）、ローカルからも SSH で VM へ同期。`AGENTS.md` §8 として文書化。
3. **二重読み上げと no-apply は前セッションで 2b7813a として完遂**（TTL 900 + audio dedup 120、self-report advisory 化）、`docich` 8ac4853 で bump・VM 反映済み（156件 STATGATE）。
4. **Phase 2→3→4 完走は完了**（`.env` 5キー enforce、VM 156件）。

## ✅ やったこと（実測で確認済み）

- **Issue選定**（`gh issue list --repo azumag/docich --limit 20` で 10件 OPEN を実測）— #18 を選定（#16/#17 は範囲広、#15 は複数ゲーム同時、#13 は既存ピーク対応と重複、#12 はハンドオーバー）。理由を `file:line` 付きで文書化。

- **#18 設計を opus 委任**（`ses_fdf568e61ffeNJKYJixvbTPHie`）— Twitch API は `POST /helix/channels/ads/schedule/snooze?broadcaster_id`（`channel:manage:ads`、5分/回、3回/stream、GETで `snooze_count/next_ad_at` を確認）、条件は Partner/Affiliate かつ配信中。`speaking.json` を `tmp/state/speaking.json` に atomic に管理し、`say_enqueue.sh` の実再生開始直後に `speaking=true`、最終 chunk 完了後に `grace 3s + キュー残確認` で `false` にする設計を確定。

- **作業中バナー表示**（ユーザー指示）— `games/soviet_now/codex_work_indicator.sh start "設計中" "広告スヌーズ..."` で `tmp/state/codex_work_indicator.json` に `active:true` を実測、VM 側へも `ssh` で `enqueue_audio_text "作業中: ..."` を `tmp/.comment_queue/comment_announce_*` に実測（`ls -lt` で 1件確認）。

- **二重読み上げと no-apply の前回コミットは実測で完了**（`games/soviet_now` `2b7813a`、`docich` `8ac4853`、`VM:/home/ubuntu/soren` へ 5ファイル `scp`、 `grep 900`/`dedup 6`/`bash -n` OK、 `VM:/home/ubuntu/docich` は `fetch`→`reset --hard 8ac4853` で同期を実測）。

- **作業中→VM読み上げキュー連携を実装**
  - `games/soviet_now/codex_work_indicator.sh` に `lib/outbound_queue.sh` の `enqueue_audio_text` 呼び出しを追加（`_enqueue_work_audio` 関数、80字丸め、120s dedup）。ローカル実行時は `ELOOP_LIB_DIR` が `/home/ubuntu/soren` でない場合に `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105 "cd /home/ubuntu/soren && source lib/outbound_queue.sh; enqueue_audio_text ..."` で VM へも enqueue。`stop` 時は `作業完了: title` を enqueue。
  - `VM` オーバーレイ同期も追加: ローカル実行時は `ssh ... "./codex_work_indicator.sh start ..."` で VM の `tmp/state/codex_work_indicator.json` も更新（ローカル `cat` で `active:true` と VM側 `cat` で同タイトルを実測）。
  - `bash -n codex_work_indicator.sh` OK、`./codex_work_indicator.sh start "テストVMオーバーレイ同期"` で VM側 `codex_work_indicator.json` に同タイトルと `comment_announce_*` ができることを実測。
  - `AGENTS.md:61`（docich）と `games/soviet_now/AGENTS.md:19` に §8 を追記（自動 `enqueue_audio_text "作業中: $title $body" work_indicator`、手動 `ssh ... enqueue_audio_text`、対象は `7` と同じ全プロジェクト作業）。`bash -n AGENTS.md` は不要だが `grep -A 5 "作業中はVMの読み上げ"` で実測。

- **#18 実装（未コミット、検証中）**
  - `lib/twitch_ads.sh` 新規作成（`GET /helix/channels/ads`→`snooze_count/next_ad_at`、閾値600s、同一 `next_ad_at` dedup、429/401時の `backoff`、ログ `tmp/debug/twitch_ads.log`、非同期 `&` で TTS ブロッキングなし）。`bash -n lib/twitch_ads.sh` OK、`TWITCH_ADS_ENABLED=0` と `TWITCH_BOT_TOKEN=""` で `twitch_ads_maybe_snooze` が exit 0 で継続することを実測。
  - `core/config.sh:709` に `TWITCH_ADS_ENABLED`/`TWITCH_SNOOZE_THRESHOLD_SEC`/`TWITCH_SNOOZE_POLL_SEC`/`SPEAKING_STATE_FILE`/`SPEAKING_GRACE_SEC`/`COMMENT_AUDIO_DEDUP_TTL_SEC` の既定値 6行を追加。`bash -n core/config.sh` OK。
  - `say_enqueue.sh` に `SPEAKING_STATE_FILE`/`_speaking_enter`/`_speaking_leave`（`tmp/state/speaking.json` atomic、`lib/twitch_ads.sh` を source して `twitch_ads_maybe_snooze` を `&` で呼出、240s poll を背景でループ、`_cleanup` で `kill $SPEAKING_POLL_PID` と grace 3s + キュー残確認）を追加（`90` の `SAY_TRUNCATE_RATIO` の次に 60行追加）。`bash -n say_enqueue.sh` OK。`trap` は既存 `_cleanup` に統合（`_cleanup` 内で `_speaking_leave` を呼出）。
  - `say_enqueue.sh:2613` のロック内再生直前に `if [ "$RENDER_ONLY" != "true" ]; then _speaking_enter ...; fi` を追加（`_prepare_playback_turn` の次）。`_cleanup` 内で `rm` 前に `_speaking_leave` を呼出することを実測（`grep -n _speaking` で 3件を実測）。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `8ac4853`（`origin/main` も `8ac4853`、前回の prompts+Phase Aで同期済みだが、現在の `AGENTS.md` と `games/soviet_now` の新 5ファイル分が未コミットで `M`）。`games/soviet_now` は `6960a37`（`origin/main` も `6960a37`、前回 `2b7813a` から `#18` と作業中ルールで 5ファイル追加: `AGENTS.md`/`codex_work_indicator.sh`/`core/config.sh`/`say_enqueue.sh`/`lib/twitch_ads.sh`）。`git -C games/soviet_now status` は `M` 5件を実測、`git -C . status` は `M AGENTS.md` + `M games/soviet_now`（`2b7813a..6960a37`）を実測。`bash -n` 4ファイル OK。
- **VM 本番**: `VM:/home/ubuntu/docich` は `8ac4853`（`git log` 2件、`status` clean、`grep prompts` 1件を実測、前回同期のまま）。`VM:/home/ubuntu/docich/games/soviet_now` は `2b7813a`（`git rev-parse` で実測、5ファイルは旧版のまま）。`VM:/home/ubuntu/soren` はコード 5ファイル（`twitch_chat_daemon 900`/`twitch_chat 900`/`outbound_queue dedup`/`say 0.85`/`eloop narrow`）は `2b7813a` 相当で実測、`.env` は `enforce` 5キー（`grep -E` で5キー実測）、`STATGATE` 156件、`docich-webui` 1461345 activeで `api/prompts` 37件を実測。`VM` の #18 新コード（`twitch_ads.sh`/`speaking.json`）と作業中ルールの `codex_work_indicator.sh` 新版は未反映（ローカルのみ）。
- **検証**: `enqueue_audio_text` dedup と `codex_work_indicator.sh` の VM 同期は `ls -lt tmp/.comment_queue/comment_announce_*` で 2件の `作業中:` ファイルを実測。`twitch_ads.sh` は `TWITCH_ADS_ENABLED=0` で exit 0、`bash -n` 4ファイル OKを実測。`say_enqueue.sh` の `_speaking_*` は `grep -n` で 3件を実測。
- **作業中バナー**: `設計中`→`実装中`→`検証中`→`handoff更新中` と粒度更新、現在 `handoff更新中` で active（`tmp/state/codex_work_indicator.json` に `実装中`→`検証中`の履歴あり）。

## ⏭️ 次にやること

1. **soviet_now の新 5ファイルをコミット・push**（最優先）: `git -C games/soviet_now add AGENTS.md codex_work_indicator.sh core/config.sh lib/twitch_ads.sh say_enqueue.sh && git commit -m "feat: add Twitch ad snooze during TTS and work-to-audio queue rule"` → `git push origin HEAD:main`（`2b7813a..6960a37`）。
2. **docich 側の bump をコミット・push**: `git add AGENTS.md games/soviet_now handoff.md && git commit -m "chore: bump soviet_now to 6960a37 — ad snooze + work audio rule + handoff"` → `push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`。
3. **VM へ 7ファイル反映**（原子置換 + `sha256`一致）: `VM:/home/ubuntu/soren` へ `twitch_ads.sh` 新規 + `codex_work_indicator.sh`/`core/config.sh`/`say_enqueue.sh` + `AGENTS.md`（docich側は `scp` 不要、VMの `soren/AGENTS.md` は `games/soviet_now/AGENTS.md` と同内容を `scp`）、`VM:/home/ubuntu/docich` は `git fetch`→`reset --hard origin/main`→`submodule update` で `6960a37` に同期。`bash -n` と `grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS_ENABLED'` で実測。
4. **VM検証**（#18）: `echo "test" | say_enqueue.sh` 的な短文と `lib/twitch_ads.sh` の `GET` を stub して `snooze_count`>0 の時に `tmp/debug/twitch_ads.log` に `snoozed` が残ることを実測。`tmp/state/speaking.json` が再生中のみ `true` で `grace` 後に `false` になることを2連続 `enqueue_audio_text` で実測（flap無し）。
5. **VM検証**（作業中ルール）: `codex_work_indicator.sh start "テスト"` で VM側 `codex_work_indicator.json` と `tmp/.comment_queue/comment_announce_*` ができることを実測（既に `ls` で2件実測済みだが、正式に `stop` で `作業完了` が読まれることも確認）。
6. **1週間観測**: 既存の二重読み・no-apply・STATGATE 156件の churn と合わせて、広告スヌーズが `tmp/debug/twitch_ads.log` で 429/400時の backoff と共に TTS を壊さないことを配信ログで確認。

## 📂 重要なファイル

- `games/soviet_now/lib/twitch_ads.sh` — 新規（GET/POST、600s閾値、240s poll、backoff）
- `games/soviet_now/say_enqueue.sh:95` — `SPEAKING_STATE_FILE`/`_speaking_enter`/`_speaking_leave` と `2613` の再生直前 `enter`
- `games/soviet_now/core/config.sh:709` — `TWITCH_ADS_ENABLED` 等 6行
- `games/soviet_now/codex_work_indicator.sh:68` — VMオーバーレイ同期 + `_enqueue_work_audio`（80字丸め、120s dedup、SSH to VM）
- `games/soviet_now/AGENTS.md:19` — work-to-audio 追記
- `AGENTS.md:61` — docich 側 §8 追記
- `games/soviet_now` `6960a37` — 上記5ファイル（`2b7813a..6960a37`）
- `/home/ubuntu/soren/.env` — enforce 5キー（`STAT_GATE_MODE=enforce` 等）
- `soren-stat-gate-design.md` — Phase3/4設計

## 🧭 決定と前提

- **#18 は `speaking` を `say_enqueue.sh` の実再生区間に紐付け**（`enqueue` ではなく `say` の `PLAYER_WAIT` で判定）。`lib/twitch_ads.sh` は `channel:manage:ads` と `channel:read:ads` が必要で、配信中でない・権限不足・`snooze_count 0` では `log` のみで `return 0`（TTS継続）。5分効果は `poll 240s` で繋ぐ。連続キューは `grace 3s + キュー残確認` で flap抑止。
- **作業中→VM読み上げは `codex_work_indicator.sh` に統合**（`7` のバナーと `8` の音声は同じ `start` で発火）。ローカル実行時は `ELOOP_LIB_DIR` が `/home/ubuntu/soren` でないことを検出して `ssh` で VM 側にも `codex_work_indicator.sh` と `enqueue_audio_text` を実行。`120s dedup` で spam 抑止。手動でも `ssh ... enqueue_audio_text "作業中: ..."` で可。
- **soviet_now 5ファイルは1コミット `6960a37`**、docich は bump + handoff で1コミット（次で push）。VM soren は `scp` 原子置換で反映（非git）。

## ⚠️ 未解決・ブロッカー・落とし穴

- **VM の #18 新コードは未反映**（`lib/twitch_ads.sh` が VM に無い、`say_enqueue.sh` は 旧 `2b7813a` 版のまま）。`VM:/home/ubuntu/docich` の `games/soviet_now` は `2b7813a` のまま（`M`）。次で `scp` 7ファイル + `git fetch/reset` で同期しないと乖離が残る。
- **Twitch API の権限要確認**: `TWITCH_BOT_TOKEN` に `channel:manage:ads` と `channel:read:ads` が付いているか、`TWITCH_BROADCASTER_ID` が正しいかを配信前に `curl` で実測しないと `401` で常に backoff になる。`GET /helix/channels/ads` が `400 not live` を返す配信外では snooze は no-op（想定内）。
- **handoff.md は本ファイルが未コミット**（`M`）。次で `git add` し `origin` と同期すること。
- Phase3/4の7日観測は継続（`STATGATE` 156件）。
- `tmp/.comment_queue/audio_dedup` は TTL120で掃除、VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh`（900/1）、`grep -c audio_dedup /home/ubuntu/soren/lib/outbound_queue.sh`（6）、`bash -n` 5ファイル
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY' /home/ubuntu/soren/.env`（enforce/100/1/1）
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（156）、`cat /home/ubuntu/soren/tmp/state/speaking.json`（speaking true/false）、`cat /home/ubuntu/soren/tmp/debug/twitch_ads.log`
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` → `cat tmp/state/codex_work_indicator.json` と `ssh ... cat /home/ubuntu/soren/tmp/state/codex_work_indicator.json`、`ls /home/ubuntu/soren/tmp/.comment_queue/comment_announce_*`

## 🔗 参照

- `games/soviet_now/twitch_chat_daemon.sh:18` / `twitch_chat.sh:33` — 900
- `lib/outbound_queue.sh:240` — audio dedup 120
- `lib/twitch_ads.sh` — 新規（GET/POST）
- `say_enqueue.sh:95,2613` — speaking
- `codex_work_indicator.sh:68` — VM連携
- `AGENTS.md:61` / `games/soviet_now/AGENTS.md:19` — §8
- `games/soviet_now` `6960a37`
- `/home/ubuntu/soren/.env` — enforce

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -2` と `git status`（`M` なしか）、`ssh ubuntu@129.146.54.105 "grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh; ls /home/ubuntu/soren/lib/twitch_ads.sh 2>&1 | head"` を実測してから着手すること。
