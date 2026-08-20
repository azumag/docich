# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 05:20 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: docich webui に audio-worker 手動enqueue パネル（Audioタブ）を追加し、ローカル検証→コミット ea5a250→push→VM反映（docich-webui 再起動）→実測まで完了

## 🎯 ゴール / タスク

1. **webui に audio-worker 手動enqueue パネル追加**（完了・VM反映済み）— `src/docich/webui.py` に Audioタブ（キュー一覧 + 手動enqueue + 削除/全クリア + プリセット）と API（`GET /api/audio/queue`、`POST /api/audio/enqueue`、`DELETE /api/audio/queue/<fname>`、`DELETE /api/audio/queue`）を追加、`tests/test_webui.py` 14件追加（計66件 pass）、VM `docich-webui` 3166488 で enqueue→dedup→list→delete を実測。
2. **#18 広告スヌーズ** — 完了（`speaking.json` + `lib/twitch_ads.sh`、VMで `GET` 200 `snooze 2→1` を実測、5分効果を `poll 240s` で繋ぐ）。
2. **作業中→VM読み上げの丁寧化・大くくり化**（ユーザー指示 04:36/04:50）— `codex_work_indicator.sh` の音声を丁寧な敬語で詳細に、バナーは細かく・音声は大くくり（300s同一タイトル、180s包含はスキップ、`stop` は常に読む、300s dedup）へ更新、`AGENTS.md` §8 を更新。
3. **dociai用トークン・設定の更新**（ユーザー指示 05:0x）— `TWITCH_BOT_TOKEN` を `zd7y...`（dociai）で更新、`TWITCH_CHANNEL=dociai`/`TWITCH_BROADCASTER_ID=1526886844` が一致することを `helix/users` で検証、`TWITCH_ADS_ENABLED=1` を `.env` で明示して有効化、 `GET /helix/channels/ads` 200 `snooze_count 2` と `POST /snooze` 200 `1` を実測。
4. **二重読み上げと no-apply は 2b7813a で完遂**（TTL 900 + dedup、advisory 化）、`docich` 08d9bd5 で bump・VM 反映済み。
5. **Phase 2→3→4 完走は完了**（`.env` 5キー enforce、VM 156件）。

## ✅ やったこと（実測で確認済み）

- **webui Audio パネル**（docich `ea5a250`）
  - `src/docich/webui.py`: `AUDIO_TEXT_LIMIT=1000` 等定数、`_comment_queue_dir`/`_comment_audio_dedup_dir`（`.env`/env の `COMMENT_QUEUE_DIR`/`COMMENT_AUDIO_DEDUP_DIR` を参照、既定 `tmp/.comment_queue`）、`_comment_audio_claim_enqueue_key`（md5 + mkdir dedup、TTL 120s）、`_enqueue_audio_text`（原子書き込み `comment_announce_{ns}_{source}.txt` + `.speaker` sidecar）、`_list_audio_queue`（txt + .playing、speaker、mtime順）、ハンドラ 4種（GET/POST/DELETE単品/DELETE全クリア、read_only は既存ゲートで 403）を追加。`python3 -c ast.parse` OK、`tests/test_webui.py` 66件 pass を実測。
  - フロントエンド: nav に Audioタブ、`#tab-audio`（キュー一覧 table + 手動enqueue textarea/source/speaker + プリセット3種 + 全クリア/削除）、`loadAudioQueue`/`enqueueAudio`/`updateAudioTextCount` を追加。ローカル実サーバーで HTML に `data-tab="audio"`/`audio-enqueue`/`loadAudioQueue` が含まれること、read_only で enqueue/clear が 403・GET が 200 を実測。
  - コミット `ea5a250 feat: add audio-worker manual enqueue panel to webui (Audio tab)`（webui.py + tests 2ファイル 689 ins）を `docich` で実施、`push origin HEAD:main`（614aaf0..ea5a250）と `HEAD:codex/soren-repo-handoff`（ead5b2e..ea5a250）を実測。
  - **VM反映**: `VM:/home/ubuntu/docich` を `git fetch`→`reset --hard origin/main`→`submodule update` で `ea5a250`/`1434820ff` に同期（`git status` clean、`grep -c audio-enqueue|_handle_post_audio_enqueue` 9件を実測）、`systemctl --user restart docich-webui.service` で新 PID 3166488（active running を実測）。`GET /api/audio/queue` が `queue_dir=/home/ubuntu/soren/tmp/.comment_queue`、`dedup_count 22`、`count 8`（実データ）を実測。`POST /api/audio/enqueue`（source=webui_test, speaker=46）→ `comment_announce_*_webui_test.txt` 生成、同一テキスト再投で `dedup:true`、DELETE で削除、`count 8` 復帰、HTML に `data-tab="audio"` 等 14件を実測。テストアイテムは配信再生前に削除した。

- **Twitch OAuth URL生成とトークン検証**
  - `TWITCH_CLIENT_ID=6gr8gpkyjtcjp20w8iwn9a98iazucv`（`VM:.env` から実測）で `https://id.twitch.tv/oauth2/authorize?response_type=token&client_id=...&redirect_uri=http://localhost&scope=channel:manage:ads+channel:read:ads+chat:read+chat:edit+channel:manage:broadcast+user:read:email&state=dociai_ads_20260821` を生成、ユーザーに提示。ユーザーが `http://localhost/#access_token=zd7yllewm6fraw1mlriqdalbn8yv4y&...` を貼り付けたため、VMの `TWITCH_BOT_TOKEN` を同値で原子更新（`printf ... | ssh ... cat > /tmp/new_token.txt && python3 - <<PY upsert`）。
  - 新トークンで `helix/users` が `200 login=dociai id=1526886844` で `broadcaster_id` と一致、`validate` が `scopes=['channel:manage:ads','channel:read:ads',...]`、`GET /helix/channels/ads?broadcaster_id=1526886844` が `200 snooze_count=2`、 `POST /snooze` が `200 snooze_count 1 next_ad_at 1787256212`（5分延長）を実測。旧トークン（`412gv...`）では `401 Invalid OAuth token` を実測していたため、更新で解消。`VM:.env` の `TWITCH_CHANNEL=dociai`/`TWITCH_BROADCASTER_ID=***`（1526886844）/`TWITCH_ADS_ENABLED=1` を `grep -E` で実測。

- **作業中音声の丁寧化・大くくり化**（`games/soviet_now` `1434820ff`）
  - `codex_work_indicator.sh` の `_enqueue_work_audio` を `お待たせしております。現在、${title}の作業を丁寧に進めております。詳細としまして、${body}でございます。…何卒よろしくお願い申し上げます。`（240字丸め、`stop` 時は `作業が完了いたしました…`）へ書換。同一タイトル 300s、包含なら 180s 以内は `tmp/state/work_audio_last.json` でスキップ、`stop` は常に読む、`COMMENT_AUDIO_DEDUP_TTL_SEC=300` で二重抑止。`bash -n` OK。
  - `AGENTS.md:61`（docich）と `games/soviet_now/AGENTS.md:19` に §8 を同文で更新（自動 `enqueue_audio_text`、手動 `ssh ... enqueue_audio_text "お待たせしております。..."`、対象は `7` と同じ）。`grep -A 5 "作業中はVMの読み上げ"` で実測。
  - `soviet_now` で `1434820ff refine: make work-to-audio more polite and larger-grained` をコミット・`push origin HEAD:main`（`6960a37..1434820ff`）を実測。`VM` へは次で `scp` 予定だったが、トークン更新を優先したため `VM` の `codex_work_indicator.sh` はまだ旧 `6960a37` 版（`grep -c work_indicator` 2件）のまま。

- **前回までの #18 広告スヌーズと作業中ルール**は `6960a37`（`lib/twitch_ads.sh` 新規、`say_enqueue.sh` の `speaking.json`、`core/config.sh` 6行、`codex_work_indicator.sh` VM連携）として `soviet_now` でコミット・`docich` `08d9bd5` で bump・`VM` へ 5ファイル `scp` と `git fetch` で同期済み（`grep 900`/`6`/`0.85`/`redundant.*change` を実測）。`VM` の `soren` へは `codex_work_indicator.sh` の旧版を `scp` 済みだが、丁寧化版は未反映。

- **Phase 2→3→4** は `.env` 5キー `enforce` と `STATGATE` 156件を前回実測のまま。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `ea5a250`（`origin/main` も `ea5a250`、`origin/codex/soren-repo-handoff` も `ea5a250`）。`games/soviet_now` は `1434820ff`（`origin/main` も `1434820ff`）。`docich` 側で `games/soviet_now` は `M`（サブモジュール実体 `e3bb58fab` と親参照 `1434820ff` の乖離、前回からの継続、今回の webui 変更とは無関係）。`handoff.md` は本ファイルが未コミット（`M`）。
- **VM 本番**: `VM:/home/ubuntu/docich` は `ea5a250`（`git log` で実測、`status` clean、`grep -c audio-enqueue|_handle_post_audio_enqueue` 9件）、`games/soviet_now` は `1434820ff`。`docich-webui.service` は `3166488` active running。`/api/audio/queue` は実データ（count 8、dedup_count 22）を実測、enqueue/dedup/delete round-trip を実測。`VM:/home/ubuntu/soren` の `.env`（dociaiトークン等）と `STATGATE` 156件は前回のまま。
- **作業中バナー**: `デプロイ中` を表示→検証完了後 `stop` 済み（VM `codex_work_indicator.json` が空になったことを実測）。

## ⏭️ 次にやること

1. **handoff のコミット・push**（残り）: `git add handoff.md && git commit -m "docs: update handoff for webui audio enqueue panel" && git push origin HEAD:main` / `HEAD:codex/soren-repo-handoff`。
2. **webui Audio パネルの実運用観測**: 配信視聴者操作で `POST /api/audio/enqueue` が audio_worker により `say_enqueue.sh` で再生されること（`.playing` 経由）を確認。dedup 120s・TTL掃除（`audio_dedup`）の動作も観測。
3. **1週間観測**（#18 広告スヌーズ・二重読み・STATGATE 156件）は継続。

## 📂 重要なファイル

- `src/docich/webui.py` — Audioタブ + `_comment_queue_dir`/`_enqueue_audio_text`/`_list_audio_queue`/ハンドラ 4種（`ea5a250`、AUDIO_TEXT_LIMIT 1000、dedup 120s）
- `tests/test_webui.py` — `TestAudioQueue` + HTTP 8件（計66件 pass）
- `games/soviet_now/codex_work_indicator.sh` — 丁寧化版（`1434820ff`、240字丸め、300s/180sスキップ、`work_audio_last.json`）
- `AGENTS.md:61` / `games/soviet_now/AGENTS.md:19` — §8 丁寧化・大くくり化（`お待たせしております…`）
- `games/soviet_now/lib/twitch_ads.sh` — `6960a37` 版（GET/POST、600s閾値）
- `games/soviet_now/say_enqueue.sh:95,2613` — `speaking.json` と `enter`
- `games/soviet_now/core/config.sh:709` — `TWITCH_ADS_ENABLED` 等
- `games/soviet_now` `1434820ff` — 上記丁寧化（`6960a37..1434820ff`）
- `/home/ubuntu/soren/.env` — `TWITCH_BOT_TOKEN=zd7y...`（新、dociai）、`TWITCH_BROADCASTER_ID=1526886844`、`TWITCH_CHANNEL=dociai`、`TWITCH_ADS_ENABLED=1`、`STAT_GATE_MODE=enforce` 等
- `soren-stat-gate-design.md` — Phase3/4設計

## 🧭 決定と前提

- **webui Audio パネル**は `lib/outbound_queue.sh:enqueue_audio_text` と同等の契約（`comment_announce_*` ファイル + `.speaker` sidecar + md5 dedup 120s）を Python で再実装。キュー実体は `COMMENT_QUEUE_DIR`（既定 `tmp/.comment_queue`）で、audio_worker が消化する本番キューを直接操作する。read_only 時は POST/DELETE が 403。
- **dociai用トークンは `zd7y...`（30字）**で `helix/users` が `login=dociai id=1526886844` と `broadcaster_id` 一致、`scopes` に `channel:manage:ads` を含むことを実測。旧トークン（`412gv...`）は `401 Invalid OAuth token` を実測していたため更新で解消。`TWITCH_BOT_TOKEN` は `.env` に平文で保存するが、handoff やログには `***` で秘匿。
- **作業中音声は丁寧な敬語で大くくり**（`7` のバナーは細かく、`8` の音声は 300s同一タイトル・180s包含はスキップ、 `stop` は常に読む）。`codex_work_indicator.sh` の `work_audio_last.json` で判定、`enqueue_audio_text` の 300s dedup でも二重抑止。手動でも丁寧な敬語で詳細に書く。
- **soviet_now `1434820ff` は `codex_work_indicator.sh` の丁寧化のみ**、前回の `6960a37`（広告スヌーズ本体）は既に `docich` `08d9bd5` と `VM` `6960a37` で反映済み。`VM` の `codex_work_indicator.sh` はまだ旧版のため次で `scp` する。

## ⚠️ 未解決・ブロッカー・落とし穴

- **VM の `codex_work_indicator.sh` は丁寧化前**（`6960a37` 版）。`VM:/home/ubuntu/docich` の `games/soviet_now` も `6960a37` のまま（`M`）。次で `scp` と `git fetch/reset` で `1434820ff` に同期しないと乖離が残る。
- **handoff.md は本ファイルが未コミット**（`M`）。次で `git add` し `origin` と同期すること。
- **Twitch トークンは平文で `.env` に保存**されるため、handoff や `git` には書かない。`tmp/debug/twitch_ads.log` には `snoozed` の記録のみ残り、トークン自体は残らない。
- Phase3/4の7日観測は継続（`STATGATE` 156件）。
- VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh`（900）、`cat /home/ubuntu/soren/tmp/state/speaking.json`、`cat /home/ubuntu/soren/tmp/debug/twitch_ads.log`、`grep -E '^TWITCH_BOT_TOKEN=' /home/ubuntu/soren/.env | sed 's/=.*/=***/'`
- .env: `grep -E 'STAT_GATE_MODE|MIN_GAMES_FOR_BEST_ROLLBACK|DEAD_REGRESSION|IMPROVE_FUTILITY' /home/ubuntu/soren/.env`（enforce/100/1/1）
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（156）、`ls /home/ubuntu/soren/tmp/.comment_queue/audio_dedup | wc -l`
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` → `cat tmp/state/codex_work_indicator.json` と `ssh ... cat /home/ubuntu/soren/tmp/state/codex_work_indicator.json`、`ls /home/ubuntu/soren/tmp/.comment_queue/comment_announce_*` で `お待たせしております。` を確認

## 🔗 参照

- `src/docich/webui.py` — Audioタブ/API（`ea5a250`）
- `tests/test_webui.py` — `TestAudioQueue`
- `games/soviet_now/twitch_chat_daemon.sh:18` / `twitch_chat.sh:33` — 900
- `lib/twitch_ads.sh` — `6960a37` 版
- `say_enqueue.sh:95,2613` — speaking
- `codex_work_indicator.sh` — `1434820ff` 丁寧化版
- `AGENTS.md:61` — §8 丁寧化・大くくり化
- `games/soviet_now` `1434820ff`
- `/home/ubuntu/soren/.env` — `TWITCH_BOT_TOKEN=zd7y...`（dociai）、`TWITCH_BROADCASTER_ID=1526886844`

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -2` と `git status`（`M` なしか）、`ssh ubuntu@129.146.54.105 "grep -E 'RECENT_LINE_HASH_TTL|TWITCH_ADS' /home/ubuntu/soren/twitch_chat*.sh; grep -E '^TWITCH_BOT_TOKEN=' /home/ubuntu/soren/.env | sed 's/=.*/=***/'"` を実測してから着手すること。
