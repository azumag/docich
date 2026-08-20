# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 05:38 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: chat pause中の outbound queue 蓄積防止（enqueue_chat_message の no-op）を soviet_now 715251b7a → docich a37a20f で完遂、VM反映・検証（queue 0維持）まで完了。chat は pause 中。

## 🎯 ゴール / タスク

1. **chat send 停止中も outbound queue を蓄積させない**（ユーザー指示 05:10）— 完了。`enqueue_chat_message` を `tmp/state/chat_worker.paused` 存在時は no-op（`OUTBOUND_CHAT_PAUSE_MARKER`）、40箇所以上の呼び出し元を一括抑止。VMで queue 0維持を実測。
2. **chat 投稿の一時停止**（ユーザー指示 05:07）— 完了。`tmp/state/chat_worker.paused` で durable pause（IRC・コメント生成・queue消費を停止、supervisor生存）。再開は `rm` で自動復帰。現在も pause 中。
3. **dociai用トークン・設定**（ユーザー指示 05:0x）— 完了。`TWITCH_BOT_TOKEN=zd7y...`、`helix/users` 200 login=dociai id=1526886844、`GET ads` 200 snooze 2→1 を実測。`TWITCH_ADS_ENABLED=1`。
4. **#18 広告スヌーズ / 作業中音声 / 二重読み / no-apply** — 完了（`715251b7a` まで、`lib/twitch_ads.sh`/`speaking.json`/TTL 900/audio dedup/advisory 化）。
5. **webui Audio 手動 enqueue パネル**（別セッション `ea5a250`、継続）— `src/docich/webui.py` Audioタブ、`_comment_queue_dir`/`_handle_post_audio_enqueue` 等、`/api/audio/queue` で count 8 を実測。
6. **Phase 2→3→4 完走** — `.env` 5キー enforce、VM 156件 STATGATE。
7. **作業中音声を自然文・セッション粒度へ変更**（本セッション）— 完了。ランダムな枠文テンプレートを廃止し、最初の `start` の本文をそのまま読む。フェーズ更新はバナーのみ、別セッション開始も15分に1回まで、完了音声は開始を読み上げた3分以上のセッションだけ。`soviet_now` `e497637c2` を main へ pushし、VMへバックアップ付きで反映。VM実測で読み上げ対象が `VMで作業中音声のユニーク文面を実測しています。` の1文だけになり、次のフェーズ更新後も audio state の title/start_ts/last_audio_ts が不変であることを確認。`bash -n`、関連 unittest 9件、隔離キュー実測も成功。

## ✅ やったこと（実測で確認済み）

- **chat send の実体を特定** — `ps` で `workers/chat_worker.sh dociai`（PID 649119）が `outbound_queue_consume_once`（`workers/chat_worker.sh:184` → `lib/outbound_queue.sh` `twitch_chat.sh send` → `curl POST /helix/chat/messages`）で投稿していたことを実測。`game_status` 等の `[33/100] score=...` メッセージは `generate_comment_response` が enqueue し chat_worker が送信。

- **chat 一時停止**（`VM:/home/ubuntu/soren/tmp/state/chat_worker.paused` 作成）— `workers/chat_worker.sh:257` `_worker_is_paused` → `_park_while_paused:258` で IRC daemon 停止・コメント生成・queue消費を停止。`chat_worker.log` に `paused: IRC daemon 停止`/`アイドル待機`、`twitch_chat.sh send` プロセス 0件、pending 4016→4016（消費停止）を実測。supervisor 下で `649119` は生存。

- **outbound queue 削除**（ユーザー指示 05:08）— `pending` 4017→0 / `processing` 1→0 / `sent` 0 / `dedup` 253→0 を実測。

- **pause 中の queue 蓄積防止**（`lib/outbound_queue.sh:299`）
  - `_outbound_chat_paused()` を新設（`[ -f "${OUTBOUND_CHAT_PAUSE_MARKER:-tmp/state/chat_worker.paused}" ]`）、`enqueue_chat_message` 冒頭で pause 中は `return 0`（no-op、積まない）。これで 40箇所以上の呼び出し元を個別に触らず一括抑止、再開（マーカー削除）で自動復帰。
  - ローカルテスト: not paused→1、paused→1（no-op）、resumed→2 を実測。`bash -n` OK。
  - `soviet_now` で `715251b7a fix: do not accumulate outbound chat queue while chat is paused` をコミット・`push`（`e3bb58fab..715251b7a`、※`e3bb58fab` は別セッションの作業中音声 plain-polite 文言 refine）。

- **VM反映と検証**
  - `VM:/home/ubuntu/soren/lib/outbound_queue.sh`/`codex_work_indicator.sh`/`AGENTS.md` を最新（`715251b7a`）へ `scp`、`bash -n` OK、`grep -n _outbound_chat_paused` 2件、`作業を進めています`/`進捗があり次第お知らせします`（plain-polite）を実測。
  - pause 中に `enqueue_chat_message "guard-test"` を新規シェルから呼び exit 0 で queue 0のまま、40s後も pending 0を実測。※途中 `1787256895_eloop_5.msg` が1件出たが、これは `scp` 前に旧ライブラリを source 済みの稼働中 `eloop_improve_runtime` サブプロセス（PID 3089590）による一過性。新規サブプロセスは新ガードで no-op。
  - `docich` で `a37a20f chore: bump soviet_now to 715251b7a — do not queue while chat paused + plain-polite wording` をコミット・`push origin HEAD:main`/`HEAD:codex/soren-repo-handoff`（`ea5a250..a37a20f`、※`ea5a250` は別セッションの webui audio panel）。`VM:/home/ubuntu/docich` は `fetch`→`reset --hard origin/main`→`submodule update` で `a37a20f`/`715251b7a` に同期（`git status` clean）。
  - 現在 `chat_worker.paused` 有効で、pending queue 0・`twitch_chat.sh send` 0件を実測。

## 📍 現在の状態

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff`。`games/soviet_now` は `e497637c2`（`origin/main` も同じ）。ルートは `AGENTS.md`・`handoff.md`・submodule pointer を本タスクのコミット対象として更新中。既存の未追跡ファイル群には触れていない。
- **VM 本番**: `VM:/home/ubuntu/docich` `a37a20f`（`git log` 2件、`status` clean、`grep -c audio-enqueue` 等は別セッション webui audio panel）、`games/soviet_now` `715251b7a`。`VM:/home/ubuntu/soren` は `lib/outbound_queue.sh`（`_outbound_chat_paused` 2件）/`codex_work_indicator.sh`（plain-polite）/`AGENTS.md` を最新へ `scp` 済み、`tmp/state/chat_worker.paused` 有効で pending 0・`twitch_chat.sh send` 0件を実測。`.env` は `TWITCH_BOT_TOKEN=zd7y...`（dociai）、`TWITCH_BROADCASTER_ID=1526886844`、`TWITCH_CHANNEL=dociai`、`TWITCH_ADS_ENABLED=1`、`STAT_GATE_MODE=enforce` 等。`STATGATE` 156件、`docich-webui` active（`/api/prompts` 37件）。
- **作業中バナー**: VMでは `反映確認中` で active。本更新・同期後に `stop` 予定。新しい頻度制御により、このフェーズ更新の音声は追加されていない。

## ⏭️ 次にやること

1. **作業中音声の運用観測**: 本文そのままの読み上げ、同一セッションのフェーズ更新抑止、15分間隔、3分未満の完了抑止を実運用で継続確認する。
2. **chat 再開時**（ユーザーが望む時）: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105 "rm /home/ubuntu/soren/tmp/state/chat_worker.paused"` で自動復帰（IRC・コメント生成・queue消費が再開）。再開後は pending が積み直される（pause 中に no-op だった分は送られない点に注意）。
3. **1週間観測**（#18 広告スヌーズ・二重読み・no-apply・STATGATE 156件）継続。`tmp/debug/twitch_ads.log` で `snoozed`/`429` backoff、`tmp/state/speaking.json` の再生区間を確認。
4. **webui Audio パネル実運用**（別セッション `ea5a250`）: `/api/audio/queue` の enqueue/dedup/delete を配信で観測。
5. **残課題**: 画面解析導入の設計、Phase B/C の合意ゲート。

## 📂 重要なファイル

- `games/soviet_now/lib/outbound_queue.sh:299` — `_outbound_chat_paused` + `enqueue_chat_message` 冒頭 no-op（`OUTBOUND_CHAT_PAUSE_MARKER`）
- `games/soviet_now/workers/chat_worker.sh:257-281` — `_worker_is_paused`/`_park_while_paused`（`tmp/state/chat_worker.paused`）
- `games/soviet_now/codex_work_indicator.sh` — 最初の `start` の本文をそのまま読む。フェーズ更新は無音、開始は15分間隔、3分未満の完了は無音（`work_audio_last.json`）
- `games/soviet_now` `e497637c2` — 自然文・セッション粒度の作業中音声
- `src/docich/webui.py` — Audioタブ（別セッション `ea5a250`）
- `games/soviet_now/lib/twitch_ads.sh` — 広告スヌーズ（`6960a37` 版）
- `/home/ubuntu/soren/.env` — `TWITCH_BOT_TOKEN=zd7y...`（dociai）、`TWITCH_BROADCASTER_ID=1526886844`、`TWITCH_CHANNEL=dociai`、`TWITCH_ADS_ENABLED=1`、`STAT_GATE_MODE=enforce` 等

## 🧭 決定と前提

- **chat pause 中は enqueue も no-op にする**（積んで後で送るのではなく積まない）。`OUTBOUND_CHAT_PAUSE_MARKER` の既定は `tmp/state/chat_worker.paused` で、chat を pause すれば queue も蓄積しない。再開で自動復帰。※pause 中に no-op されたメッセージは失われる（蓄積を望まない意図のため許容）。
- **work 音声は本文をそのまま読む**。本文は単独で自然に通じる簡潔なです・ます調とし、タイトルを `現在、〜を進めています` や `詳細は〜です` へ差し込まない。音声はセッション開始時のみを基本とし、フェーズ更新はバナーだけにする。
- **dociaiトークンは `zd7y...`**（`helix/users` で login=dociai id=1526886844 一致、`channel:manage:ads` を含む scopes を実測）。`.env` に平文保存、handoff/ログには `***` 秘匿。

## ⚠️ 未解決・ブロッカー・落とし穴

- **稼働中の旧ライブラリ source 済みプロセスは一過性で no-op が効かない**（例: `eloop_improve_runtime` サブプロセスは scp 前に source した古い `enqueue_chat_message` を使うため、pause 中でも数件積むことがある）。新規プロセスは新ガードで no-op。影響は限定的。
- **handoff.md は本ファイルが未コミット**（`M`）。次で `git add` し `origin` と同期すること。
- Twitch トークンは平文で `.env` に保存（handoff/`git` には書かない）。`tmp/debug/twitch_ads.log` には `snoozed` のみ。
- Phase3/4の7日観測は継続（`STATGATE` 156件）。`c13837ddf` 不明は Phase C で解消予定。
- VM共有のため `codex_work_indicator.sh` は粒度更新・完了時 `stop`。

## 🛠️ 環境・コマンド

- VM: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105`、`ls /home/ubuntu/soren/tmp/state/chat_worker.paused`、`ls /home/ubuntu/soren/tmp/.outbound_chat_queue/pending/*.msg | wc -l`、`ps -eo pid,cmd | grep -E 'twitch_chat.sh send|chat/messages' | grep -v grep | wc -l`
- chat pause 解除: `ssh ... "rm /home/ubuntu/soren/tmp/state/chat_worker.paused"`
- .env: `grep -E 'TWITCH_BOT_TOKEN|TWITCH_CHANNEL|TWITCH_ADS_ENABLED|STAT_GATE_MODE' /home/ubuntu/soren/.env | sed 's/=.*/=***/'`
- 観測: `grep -c '\[STATGATE\]' /home/ubuntu/soren/logs/soren_loop.log`（156）
- 作業中バナー: `games/soviet_now/codex_work_indicator.sh start "タイトル" "本文"` → `cat tmp/state/codex_work_indicator.json` / `ssh ... cat /home/ubuntu/soren/tmp/state/codex_work_indicator.json`

## 🔗 参照

- `lib/outbound_queue.sh:299` — queue guard
- `workers/chat_worker.sh:257-281` — pause gate
- `src/docich/webui.py` — Audioタブ（`ea5a250`）
- `lib/twitch_ads.sh` — 広告スヌーズ
- `games/soviet_now` `715251b7a` / `docich` `a37a20f`
- `/home/ubuntu/soren/.env` — dociaiトークン等

> 再開時: `/handoff load` で読んだ後、`git -C games/soviet_now log --oneline -3` と `git status`、`ssh ubuntu@129.146.54.105 "ls /home/ubuntu/soren/tmp/state/chat_worker.paused; ls /home/ubuntu/soren/tmp/.outbound_chat_queue/pending/*.msg 2>/dev/null | wc -l; grep -E '^TWITCH_BOT_TOKEN=' /home/ubuntu/soren/.env | sed 's/=.*/=***/'"` を実測してから着手すること。
