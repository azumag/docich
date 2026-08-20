# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-21 05:38 JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: chat pause中の outbound queue 蓄積防止（enqueue_chat_message の no-op）を soviet_now 715251b7a → docich a37a20f で完遂、VM反映・検証（queue 0維持）まで完了。chat は pause 中。

## 2026-08-21 05:44 JST — 中華AI改善の no-apply 多発修正

- **原因（本番実測）**: `strategy/ai.sh` の改善前チェックが LiteLLM の `/health` を使用していた。これは単純な生存確認ではなく設定モデルへの能動的な疎通確認で、`deepseek-v4-flash-free` の `FreeUsageLimitError` (HTTP 429) 時に5秒タイムアウトした。その結果、AMD枠・Muse・通常DeepSeek・MiniMaxも実呼び出し前に一律 `rc=79` とされ、`failed_no_apply:rate_limited` になっていた。`/health/liveliness` は本番で約3ms・HTTP 200・`"I'm alive!"` を実測。
- **修正**: 既定の `LITELLM_HEALTH_URL` を `http://127.0.0.1:4100/health/liveliness` に変更し、回帰テストを追加。soviet_now `f958ad8cc` (`codex/no-apply-liveliness`) に commit/push。
- **検証**: `bash -n strategy/ai.sh`、`tests.test_improve_retry_reliability` + `tests.test_ai_generate_backoff` の30件がローカルで成功。本番 `/home/ubuntu/soren/strategy/ai.sh` SHA256 `d0aa99449ff542fdb70654cdd5d51dd7d0ed46a31d536fd45d4d572ed2a182f4`、`bash -n` 成功。実モデルを消費しないスタブで本番 `run_cmd` を通し、旧 `LITELLM_DOWN` で遮断されずモデル実行地点まで到達することも確認（スタブが空出力のため最終rc=78は想定内）。
- **VM反映**: `.codex_deploy/backup-20260821-054059-no-apply-liveliness/strategy/ai.sh` にバックアップ後、`soren-runtime.service` を stop/start。6/6 worker online、duplicates none、Audio/Radio/Improve daemon の新PIDを確認。chat pause は維持。
- **再試行状態**: 誤判定で作られた `rate_limit_backoff` は `.codex_deploy/rate_limit_backoff-20260821-054158-pre-liveliness` へ退避済み。既存 `tmp/improve.lock` は保持され、現在の試合終了境界で daemon が再試行する予定。05:43時点では `strategy_runner active` のため実モデル呼び出し再開は未確認。
- **main統合・再反映（05:53 JST）**: soviet_now main `7d4d907d0f`、docich main `e52d47a356` へGitHub merge済み。VM `/home/ubuntu/docich` をcleanな状態からfast-forwardし、submoduleも `7d4d907d0f` へ同期。確定mainの `strategy/ai.sh` を `/home/ubuntu/soren` へ再反映（backup `.codex_deploy/backup-20260821-055254-main-no-apply`）、`soren-runtime.service` を完全再起動した。LiteLLM liveliness 200、worker 6/6、duplicates none、FFMPEG LIVE、chat pause維持を実測。

## 2026-08-21 06:08 JST — バッチ解説・自動予想再開（実装・反映済み）

- **実装**: `soviet_now` `3eb30f62f`（親 `docich` `90a7a75`）に、改善サイクル（`MIN_GAMES_BEFORE_IMPROVE`）単位の `batch_commentary.sh` を追加。`batch_summary.py` の実測値をAIに解説させ、形式検証後に `audio_worker` キューへ一度だけ渡す。AI失敗時はバッチID単位の再試行状態を残す。
- **チャット**: 試合ごとの旧進捗投稿は `GAME_RESULT_CHAT_ENABLED=0` を既定にして停止。VMの `tmp/state/chat_worker.paused` を解除し、`soren-runtime.service` 再起動後に `chat_worker` とIRC daemonの起動、送信待ち0件を実測した。
- **予想**: `twitch_predictions.sh` に作成・解決の指数バックオフ、HTTPエラー記録、再起動時の Twitch 側 ACTIVE/LOCKED 予想取り込みを追加。`prediction_worker.sh` はバックオフ中の5秒再試行を抑止する。VMで無効トークンへのcreateを一度実行し、HTTP 401を `tmp/state/prediction_retry/create.json` に記録、900秒バックオフ中にattemptが増えないことを確認した。
- **VM反映**: `.codex_deploy/backup-20260821-060351-batch-commentary/` に対象ファイルをバックアップ後、5ファイルのSHA256一致と `bash -n` を確認し、`soren-runtime.service` を再起動した。チャットpauseは解除済み。
- **バッチ経路スモーク**: 本番状態を変更しない一時fixtureで48試合分の集計を生成（summary 9,937 bytes）、AI失敗時に `retry.json` が300秒で作られることを確認。実AI成功と音声再生は次の実バッチ到達時に確認する。
- **ライブ未達**: VMの `TWITCH_PREDICTIONS_ENABLED=1` は確認済みだが、既存 `TWITCH_PREDICTIONS_TOKEN` は Twitch API で HTTP 401（Invalid OAuth token）。`channel:manage:predictions` を持つ有効な配信者トークンがユーザー側で更新されるまでは、予想の実作成・実解決は未確認のまま。

## 2026-08-21 05:xx JST — muse/deepseek attempt 統計の経路差を診断（未変更）

- **Stats の読み方**: Soren WebUI の Stats はチェーン単位ではなく、`tmp/state/ai_stats/<YYYYMMDD>.jsonl` の全用途・全ラベルの `_ai_dispatch` を合算する。2026-08-20/21 の表示は `codex:deepseek-v4-flash` が attempt 62 / winner 31、`opencode-go:muse-spark-1.2-contributor` が attempt 16 / winner 9。
- **通常チェーン**: WebUI Chains と VM `.env` の `AI_COMMON_AGENTS` / `RADIO_AGENTS` / `RADIO_PREPASS_AGENTS` は `...opencode-go:muse-spark-1.2-contributor,...codex:deepseek-v4-flash,...` の順で、muse が deepseek より前。`MODEL_IMPROVE_LIST` と `COMMENT_AGENTS` も muse が前。
- **実際に deepseek を増やす別経路**: VM `.env` に `RADIO_FACT_CHECK_AGENT` はなく、`core/config.sh:157` の既定 `codex:deepseek-v4-flash` が有効。`RADIO_FACT_CHECK_FALLBACK` は `codex:minimax-m3`。`broadcast/radio_factcheck.sh` はこの2つを順番に `_run_opencode_radio` → `_ai_dispatch "RADIO"` で呼ぶため、`RADIO_AGENTS` を通らず、`winner` も記録しない（attempt/ok/fail のみ）。
- **VM 実測**: 2日分の `label=RADIO` attempt は deepseek 27、MiniMax 23、muse 1。deepseek の `tmp/debug/ai_dispatch/*_RADIO_codex_deepseek-v4-flash_prompt.txt` 63件は全て fact-check 冒頭で、muse の同ラベル1件は `Say hi` の手動テスト。従って表示上の「deepseek 62 vs muse 16」は、通常フォールバック順の矛盾ではなく fact-check 直呼びの合算で膨らんでいる。
- **未実施**: fact-check も muse 先行にする設定・コード変更、VM反映、worker再起動はまだ行っていない。変更する場合は `RADIO_FACT_CHECK_AGENT` を muse 先行にし、deepseek を後段へ置く設計をリポジトリと VM で同時に同期してから検証する。

## 2026-08-21 06:xx JST — 当日VMログで有料deepseek課金経路を再確認（未変更）

- **当日Stats（`tmp/state/ai_stats/20260821.jsonl`）**: attempt は `codex:deepseek-v4-flash=6`、`opencode-go:muse-spark-1.2-contributor=12`。したがってStats単体ではmuseの方が多いが、これは `_ai_dispatch`（放送・コメント）だけで、改善ループの `strategy/ai.sh` 呼出を含まない。
- **museを迂回する直呼び**: 当日 `label=RADIO` の有料deepseekは 00:12、01:22、02:24、03:27 の4回。VM `.env` に `RADIO_FACT_CHECK_AGENT` はなく、`core/config.sh` 既定の `codex:deepseek-v4-flash` と `codex:minimax-m3` を `radio_factcheck.sh` が直接順番に呼ぶため、`RADIO_AGENTS` のmuse先行順はこの経路には適用されない。
- **チェーン内の有料deepseek**: `RADIO:theme` は 00:24 のmuse失敗後に 00:27 有料deepseek、`RADIO:news` はmuse出力が検証を通らず 03:43 有料deepseekへ進んだ。こちらはmuseを先に試しているが、`ok` は生成成功であって最終winnerとは限らない。
- **改善ループの誤ルーティング**: `logs/improve_daemon.log` で 03:09 と 04:08 に `START spec=opencode:deepseek-v4-flash-free target=codex`、直後に `model: deepseek-v4-flash / provider: soren-litellm` を確認。実行時間はそれぞれ439秒、828秒で、free指定が有料deepseekとして実行された。原因は `strategy/ai.sh` が非codex specの `agent` を空にして `CODEX_MODEL=deepseek-v4-flash` を選ぶ実装。
- **muse改善試行**: 02:57、03:47、04:22、05:34 のmuse選択は旧 `/health` の `LITELLM_DOWN`（rc=79）で止まり、プロバイダ実呼出に到達していない。05:53再起動後はliveliness既定値のプロセスに切り替わっている。
- **結論**: ユーザーが見た「有料deepseekが大量・muse attemptが少ない」は、(1) fact-check直呼びが共通チェーン外、(2) 改善ループのfree/muse識別子が有料deepseekへ正規化、(3) Statsが改善ループを数えない、の合成で発生する。今回の調査では設定・VMファイル・worker状態を変更していない。

## 2026-08-21 06:45 JST — muse/free経路の有料DeepSeekすり替え修正（実装・VM反映済み）

- **実装**: `soviet_now` の `10887d141` / `34af37d92` / `cfec799e4`（現在の作業ブランチ先頭）で、`strategy/ai.sh` が `opencode:*` / `opencode-go:*` を `CODEX_MODEL` へ正規化せず、OpenCode CLIへ実モデル名を渡すよう修正。`opencode:deepseek-v4-flash-free` は `opencode/deepseek-v4-flash-free`、muse は `opencode-go/muse-spark-1.2-contributor`、`codex:deepseek-v4-flash` はCodexの `deepseek-v4-flash` として解決する。直接OpenCode経路はLiteLLM livelinessゲートで遮断しない。
- **fact-check順序**: `RADIO_FACT_CHECK_AGENT` → `RADIO_FACT_CHECK_SECONDARY` → 既存の `RADIO_FACT_CHECK_FALLBACK` → `RADIO_FACT_CHECK_TERTIARY` の順に変更。VM `.env` の既存 `RADIO_FACT_CHECK_FALLBACK=codex:minimax-m3` を変更せず、実効順を `opencode-go:muse-spark-1.2-contributor` → `codex:deepseek-v4-flash` → `codex:minimax-m3` にした。重複候補はスキップする。
- **統計**: `run_cmd`、直接fact-check、共通 `_ai_dispatch` の attempt/ok/fail に `resolved_model` を追加し、改善ループの呼出しも `tmp/state/ai_stats` へ記録する。fact-checkの採用候補は `winner` として記録する。既存JSONLの後方互換を保つ追加フィールドのみ。
- **テスト**: `bash -n`（変更シェル全件）、`tests/test_improve_retry_reliability.py`、`tests/test_ai_generate_backoff.py`、`tests/test_model_output_guard.py` の計72件が成功。課金を発生させないスタブでmuse/free/codexのCLI・モデル分離と直接OpenCodeのLiteLLMゲート迂回を確認。大規模 `test_escape_mechanisms.py` は今回と無関係な既存作業ツリー差分由来の失敗が残るため、今回の受入れ判定には使用していない。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-0635-model-routing/` に対象ファイルと誤転送されたルート直下の一時ファイルを退避後、`strategy/ai.sh`、`lib/ai_generate.sh`、`broadcast/radio_engine.sh`、`broadcast/radio_factcheck.sh`、`core/config.sh` を反映。5ファイルのSHA集合はローカルと一致し、`bash -n` 成功。`soren-runtime.service` は完全停止→起動を2回行い、現在 active。再起動後のVMヘルパー実測は `muse=opencode-go/muse-spark-1.2-contributor`、`free=opencode/deepseek-v4-flash-free`、`paid=deepseek-v4-flash`。workerはsupervisor配下で復帰。
- **未確認**: 実プロバイダを追加課金するライブ呼出しは行っていない。次の実呼出しでStatsの改善ラベルと `resolved_model` が増えること、muse失敗時だけDeepSeekへ進むことは未観測。既存チャットpause状態は今回変更していない。

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

- **ブランチ / 変更状況**: `docich` は `codex/soren-repo-handoff` @ `4728fdb`（`origin/main`・同名remote branchも同じ）。`games/soviet_now` は `e497637c2`（`origin/main` も同じ）。既存の未追跡ファイル群には触れていない。
- **VM 本番**: `VM:/home/ubuntu/docich` `a37a20f`（`git log` 2件、`status` clean、`grep -c audio-enqueue` 等は別セッション webui audio panel）、`games/soviet_now` `715251b7a`。`VM:/home/ubuntu/soren` は `lib/outbound_queue.sh`（`_outbound_chat_paused` 2件）/`codex_work_indicator.sh`（plain-polite）/`AGENTS.md` を最新へ `scp` 済み、`tmp/state/chat_worker.paused` 有効で pending 0・`twitch_chat.sh send` 0件を実測。`.env` は `TWITCH_BOT_TOKEN=zd7y...`（dociai）、`TWITCH_BROADCASTER_ID=1526886844`、`TWITCH_CHANNEL=dociai`、`TWITCH_ADS_ENABLED=1`、`STAT_GATE_MODE=enforce` 等。`STATGATE` 156件、`docich-webui` active（`/api/prompts` 37件）。
- **作業中バナー**: 最終検証時にローカル・VMとも `active:false` を確認。handoff追記中だけ音声なしで再表示し、最終応答前に再度 `stop` する。

## 2026-08-21 06:27 JST — ラジオ時報の生成・再生時差を実測（未変更）

- **本番遅延**: `radio_1787247331_43788_news_24768` は生成時刻 02:35:31 JST、事前音声生成完了 05:48:57、再生開始 05:48:59（約3時間13分後）。`radio_1787247572_43788_jiji_3073` も生成 02:39:32 → 再生 05:58:40（約3時間19分後）、`radio_1787250493_43806_fortune_6904` は生成 03:28:13 → 再生 06:17:30（約2時間49分後）。
- **直接原因**: `broadcast/radio_engine.sh` が生成時に `現在時刻は〜です` を本文へ挿入し、deferred queue の `broadcast/radio_state.sh:_radio_start_deferred_render_if_needed` がその本文から WAV/bundle を事前生成する。一方、再生直前の `_play_deferred_radio_queue_once` は `mv` 後に `_refresh_radio_intro_for_playback_file` で本文ファイルだけ現在時刻へ書き換え、既存 WAV/bundle は更新しない。したがって字幕/バックアップ本文と実際の音声が別の時刻になる。
- **補助要因**: `radio_worker` は5分周期、スケジューラの時刻窓は±15分で、コメント優先・VOICEVOX前景優先のためキューが数時間滞留し得る。既存のキュー上限は新規生成を抑止するが、古い時報原稿を再生時に期限切れにする規則はない。
- **推奨方針（未実装）**: (1) 再生直前の本文だけの更新をやめ、時刻を反映した本文を WAV/bundle と同一世代でレンダリングする整合性ゲートを入れる、(2) 長期的には時刻行を静的ラジオ本文から分離し、audio-worker が再生直前に短い時刻音声だけを合成・連結する、(3) 時報付き項目には最大経過時間を設け、期限切れは時刻行を省略するか再生成する。`_radio_time_context` は1回の日時スナップショットから時/分を作る形に統一する。
- **変更/反映**: 今回は調査のみ。リポジトリ・VMのラジオ実装は変更していない。作業中音声はVMの `tmp/.comment_queue` へ投入済み（`work_indicator`）。

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

## 2026-08-21 06:40 JST — 二重読み上げの原因調査・修正・VM反映

- **原因1（確認済み）**: VM/Linuxに `md5` がなく、`broadcast/comment.sh` と `broadcast/comment_lib.sh` の `md5 -q` が空文字になっていた。コメント本文・バッチ・処理済み行・再生済みファイルの重複ハッシュ抑止が実質無効だった。VMで `_comment_hash_text "重複確認"` が `c586b6557078cb68832b1a09e20ceb46` を返すことを修正後に実測。
- **原因2（確認済み）**: `say_enqueue.sh` のストリーミング経路が、チャンクを数秒再生した後の途中切断を同じWAVの先頭から再試行していた。VMログに `途中切断の疑い (elapsed=3s/4s)` と `再試行` が残っていた。2秒以上再生済みの異常終了・短尺判定は、先頭再試行せず完了扱いに変更（短い起動失敗は従来どおり再試行）。
- **重複ガード**: 直接コメント生成のキュー投入にも本文単位の原子的な投入権（既定TTL 120秒）を追加し、同一本文の2本目を破棄してバッチをackする。
- **リポジトリ**: `games/soviet_now` `d293231fd fix: prevent duplicate speech from comment retries` を `origin/codex/no-apply-liveliness` へpush。無関係な作業ツリー変更はステージ・コミットしていない。
- **VM反映**: `/home/ubuntu/soren/tmp/deploy_backup_d293231fd/` に対象ファイルをバックアップ後、5ファイルのSHA256一致、`bash -n`、重複ガード7項目を実測。audio-workerはTERM後、旧PID 3586769から新PID 3928313へ1秒で自動復旧し、後続の監督再起動後もPID 3965941で生存。各時点でロック所有者・メインworkerは1本（同一workerのheartbeat子プロセス1本）を確認。
- **テスト**: ローカル/VMのストリーミング8テスト、ローカルのコメント周辺34テスト、重複ガード7項目が全て成功。実運用の再発有無は今後のコメント再生ログで継続観測する（反映後は新規コメント再生がなく、実音声の再発ゼロまでは未確認）。

## 2026-08-21 06:45 JST — WebUIにTwitch予想管理を追加・VM反映

- **実装**: `src/docich/webui.py` に Predictions タブと `/api/predictions`（リモート状態・ローカルstate・retry・worker表示）、`POST /api/predictions/action`（create/resolve/cancel/sync）を追加。トークンはレスポンスへ返さず、操作引数は固定allowlistと整数範囲で検証する。
- **ラッパー**: `games/soviet_now/twitch_predictions.sh` に `status` と `sync` を追加し、resolve/cancel成功時は構造化JSONを出力。既存の自動ワーカーと同じAPI経路・chat告知・retryを使用する。
- **リポジトリ**: soviet_now `3dd550561`、親 `f9d3694` を各originへpush済み。並行作業中の無関係な変更はコミットしていない。
- **VM反映**: `/home/ubuntu/docich/src/docich/webui.py` と `/home/ubuntu/soren/twitch_predictions.sh` を `prediction-webui-20260821-064544` のバックアップ後に反映。ローカル/VM SHA256はWebUI `887f8e1b...`、予想ラッパー `fa52bee5...` で一致。`python3 -m py_compile` / `bash -n` 成功、`docich-webui.service` active。
- **実パス確認**: VM `GET http://127.0.0.1:8787/api/predictions` はJSON 200、`enabled=true`、`configured=true`、remote予想0件、prediction worker稼働中を実測。予想の作成・解決・キャンセルは検証中に実行していない。
- **テスト**: `tests/test_webui.py` はHTTP込み `71 passed`。Nodeの埋め込みWebUI JavaScript構文、予想statusモック、秘密情報非露出も確認済み。

## 2026-08-21 06:47 JST — ラジオ時報の本文・事前音声世代同期を実装・VM反映

- **実装**: `games/soviet_now/broadcast/radio_state.sh` に本文SHA-256と `.render_meta` の照合を追加。deferred再生前に時報本文を更新し、ready WAV/bundleが別世代なら破棄して同じ本文から再レンダリングする。再生直前の本文だけの更新は削除し、成功時に本文ハッシュを保存する。
- **時刻処理**: `broadcast/radio_persona.sh:_radio_time_context` は1回の `date '+%H %M'` スナップショットから時刻を作る。deferred時報は既定で「N時」までとし、分までの告知は `RADIO_TIME_ANNOUNCE_MINUTES=1` で opt-in。`core/config.sh` の `RADIO_TIME_SYNC_ENABLED=1` が既定。
- **リポジトリ**: `soviet_now` の `c41d146bf fix: keep deferred radio time audio in sync` を作業ブランチへコミット済み。現在のブランチ先端 `cfec799e4`（後続の別変更を含む）に内包され、originと一致。無関係な作業ツリー変更はステージしていない。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-064422-radio-time-sync` に対象3ファイルをバックアップ後、ローカル/VM SHA256一致、`bash -n`、誤配置コピーなしを確認。`soren-runtime.service` を 06:46:23 JST に完全再起動し、service active、`audio_worker=4009538`、`radio_worker=4009639`、`chat_worker=4009480` のPIDファイルを確認。同名の追加プロセスは各workerのheartbeat子プロセスで、systemd supervisor直下のメインPIDは各1本。
- **実パス観測**: 再起動後の先頭キュー `radio_1787250972_43806_theme_24531.txt` は `おはようございます、現在時刻は6時です。` に更新され、ready音声がない状態で再レンダリング待ちになった。後続の旧時分本文は先頭項目処理待ちで、全件の新音声再生までは未確認。
- **テスト**: `test_radio_time_sync.sh`、`test_radio_deferred_queue.sh`、`test_radio_render_retry.sh`、caption bundle、backpressure が成功。`test_radio_peak_hour_defer.py` の2件は今回触れていない既存runtime toggle差分で失敗したため未達として記録（時報同期の失敗ではない）。
- **作業中音声**: `時報本文と事前生成WAVの世代照合を検証しています。` はVMの `played.log` で06:42:38に再生済み。反映フェーズの `バックアップとハッシュ照合を終え、VMのワーカーを再起動しています。` はoverlay上で再生イベントを確認し、played.logへの最終追記は継続観測。
- **残課題**: 時刻行を静的本文から分離して再生直前に短い音声を連結する方式、時報付き項目の最大経過時間ルールは未実装。現行修正は、時単位の時報と本文/WAV世代ゲートで不一致を防ぐ範囲。

## 2026-08-21 07:03 JST — Twitch予想の受付時間上限エラー修正・VM反映

- **原因**: 改善サイクルが45試合を超える設定で、`1試合40秒 × 試合数` がTwitch Predictions APIの`prediction_window`上限1800秒を超え、HTTP 400になっていた。VMにはこの失敗のcreateリトライ記録が残っていたが、予想stateは存在しなかった。
- **実装**: `games/soviet_now/twitch_predictions.sh` で受付時間を1〜1800秒へ丸める処理を追加。既定計算値・明示的な`TWITCH_PREDICTION_WINDOW_SEC`のどちらも上限超過で1800秒に収め、解決用stateは従来どおりサイクル完了まで保持する。soviet_now `1797a999b`、親 `523e131` を各originへpush済み。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-070059-prediction-window/` に現行ラッパーと400エラーのリトライ記録をバックアップ後、修正版を反映。ローカル/VM SHA256 `af2592f9265a9866dcc1dc102c5778ed0efe31ff0a47f365d48d7bdbc533eb40`、`bash -n` 成功。反映後に今回のcreateリトライ記録だけを削除した。
- **実測**: VM `twitch_predictions.sh status` はHTTP 200、`enabled=true`/`configured=true`、remote予想0件。local prediction stateなし、create retryなし、prediction worker稼働中、`docich-webui.service` active。予想の作成自体は副作用を避けて未実行。
- **テスト**: モックAPIで`MIN_GAMES_BEFORE_IMPROVE=48`のpayloadが`prediction_window=1800`になることを確認。WebUI回帰は`71 passed`（sandbox内のlocalhost bind制限による初回22件失敗は権限付き再実行で解消）。
- **作業中表示/音声**: ローカル・VM双方の作業中バナーを明示的に有効化し、VM側stateで`active=true`を実測。作業開始文をaudio-workerへenqueue済み。完了時に双方を停止しinactiveを確認する。

## 2026-08-21 07:06 JST — ラジオキュー滞留と音声レンダー飢餓の原因確認（未変更）

- **VM実測**: `tmp/.radio_deferred_queue` は本文`radio_*.txt`が3件、`ready.wav`が1件、`rendering`が0件、`render_retry`が1件。先頭は`radio_1787250972_43806_theme_24531.txt`で、対応する`ready.wav`は存在しない。`tmp/.say_queue/current_source`/`pid`は不在で、音声再生中ではなかった。
- **直近の停止条件**: 先頭themeのレンダーは07:01:29に15チャンク中1から開始し、07:04:39に6チャンクまで進んだところで「優先音声へ合成順を譲る」となり、07:04:42に`render_retry`が`7 1787263782`（07:09:42再試行予定）へ更新された。部分WAVは破棄され、再試行は毎回チャンク0から始まる。
- **再現した履歴**: 同一本文で06:24、06:29、06:36、06:42、06:44、06:51、07:01にレンダーを開始したが、コメント/作業中音声の到着時にチャンク境界で譲り、11/15、8/15、1/15、3/15、13/15、6/15、6/15で中断した。したがってAIのラジオ生成停止ではなく、VOICEVOX事前合成の再試行ループで音声世代が完成しない。
- **構造上の要因**: `broadcast/radio_state.sh` は本文を`sort | head -n 1`でFIFO選択し、先頭にready WAVがない場合はレンダー開始だけでreturnする。先頭を飛ばして後続ready項目を再生しない。`say_enqueue.sh` は`radio_render:*`をbackground renderとしてコメント音声のpriority waiterに譲り、render-onlyは完成品全量を要求するため途中チャンクをcheckpointしない。
- **別状態との混同**: `tmp/state/.radio_state` の`generating:theme:...`はradio_workerのAI原稿生成状態であり、audio_workerの再生世代・再生状態ではない。radio_workerが進んでいても、先頭deferred項目の音声がreadyになるまで再生は進まない。
- **未実施**: 今回は診断のみで、キュー削除・worker再起動・コード変更は行っていない。修正候補は、(1) render-onlyのチャンクcheckpoint/再開、(2) コメント優先の譲りに飢餓防止の予約枠またはbounded waitを設ける、(3) 先頭レンダー待ちの間に後続ready項目を安全に再生できる順序規則、のいずれかをテスト付きで設計すること。

## 2026-08-21 — TwiCa `/live` の overlay presence 概算案（調査のみ）

- `/Volumes/satelite/work_satelite/twica` の overlay realtime は配信者 UUID ごとの Durable Object room で、room 内の WebSocket 接続数は取得できる。ただし `do-primary` の overlay だけが WebSocket を使い、`polling-only` は対象外になる。
- 生接続数はチャネル数ではない。複数 OBS ソース、ダッシュボードのプレビュー iframe、切断遅延・残留タブ、公開 overlay URL の直接接続で過大/過小になるため、表示するなら「TwiCa overlay 接続中チャネルの概算」とする。
- 全 room を横断するには、IP・ユーザー名を保存せず、room の短期 lease を集約する presence registry（小規模なら Durable Object、将来は分割）を追加する必要がある。正確な Twitch 配信数の代替にはせず、Helix 判定とは別指標として扱う。
- 低負荷優先の候補は、既存 room alarm に相乗りして状態変化時と数分おきの更新だけを presence registry へ送り、lease TTL を数分、`/live` 側のスナップショットを 60 秒キャッシュする方式。overlay ごとの追加ポーリングや Twitch/PlanetScale 全件走査は行わない。
- 今回はコード変更・デプロイ・本番設定変更を行っていない。

## 2026-08-21 07:15 JST — 予想タイトルのサイクル値を照合（変更なし）

- **現行の正本**: VM `/home/ubuntu/soren/.env` は `MIN_GAMES_BEFORE_IMPROVE=48`、`soren_loop`・`improve_daemon`・`prediction_worker` の実行時環境も48。`tmp/state/accumulated_games.json` は25試合で、現在のACTIVE予想タイトル「48ゲーム中に建国できる？」と一致している。
- **100の出所**: `.env` の `ROLLING_SCORE_KEEP=100` は評価窓であり、改善サイクル長ではない。過去のコメント/AIプロンプトには100ゲーム時代の文面が残っているが、直近の予想作成プロンプトと実行設定は48。したがって現行予想を100へ変更する根拠は確認できなかった。
- **保留**: 100ゲームへ戻す場合は改善ループ自体の`MIN_GAMES_BEFORE_IMPROVE`変更とworker完全再起動が必要で、ACTIVE予想のcancel/recreateも伴うため、ユーザー明示なしには実施しない。

## 2026-08-21 07:32 JST — LIVE試合番号表示・開始通知を実装・VM反映

- **表示**: `show_status.sh` と `status_dashboard.py` の蓄積表示を `Game 31試合目 (games)` / `♦ 31試合目 (games)` へ変更。direct broadcast overlay の旧 `Queued` 検索も新表記を扱うよう更新。
- **通知**: `eloop.sh` は実プレイ用スナップショット準備後に `Game #N 開始 [n/48]` の `game` 通知を下部イベントバーへ追加。既存の試合終了通知（`Game #N 終了 [n/48]`）と対になる。VMで `Game #43877 開始 [33/48]` を実測し、直前の `Game #43876 終了 [32/48]` も確認。
- **リポジトリ**: soviet_now `93d518bba` を実装コミットとしてpush。親 `cd86cd4` でサブモジュールポインタを更新（並行作業で先行していた別コミットも先端に含む）。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backups/20260821-072738-live-game-progress/` に対象4ファイルをバックアップ。ローカル/VM SHA256一致、`bash -n`、`py_compile` 成功。設定既定値変更ではないためworker完全再起動は行わず、次試合の再source経路で反映を実測。
- **テスト**: 開始/終了/表示のPythonテスト3件、direct broadcast overlay Nodeテスト11件、構文・`git diff --check` 成功。`-k 'overlay or live_count'` の広い実行は、今回触れていない並行変更中の`outbound_queue.sh`期待値不一致1件を除き26件成功。
- **進捗報告**: ローカル/VMの作業中バナーを開始時に有効化し、VM音声キューへ開始文を1回enqueue。完了時に双方を停止しinactiveを確認する。

## 2026-08-21 07:33 JST — Score Timelineの縦棒表示を連続線へ修正・VM反映

- **原因**: `games/soviet_now/status_dashboard.py` の `render_score_timeline()` が各サンプルを下端まで塗りつぶしていたため、直近100ゲームが縦棒の集合に見えていた。スコア履歴の入力値・上下限は正常だった。
- **実装**: Braille dot座標上で隣接サンプルをBresenham線分として連結し、基準線までの塗りつぶしを廃止。`tests/test_score_timeline.py` に連続線・極値ラベル・短履歴の回帰テストを追加。
- **リポジトリ**: soviet_now `9eb0e4ff8 fix: render score timeline as a connected line` を `origin/codex/no-apply-liveliness` へpush。親 `cd86cd4` のサブモジュール参照は `9eb0e4ff8` を指している。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-score-timeline/status_dashboard.py` に反映前ファイルをバックアップ後、修正版を反映。ローカル/VM SHA256 `763b9230acba965e52143cfce3c4d25f34c199c721088af41ec1c157830a7109` 一致、`py_compile` 成功。`generate_status_overlay.sh once` でブラウザ用overlayを再生成し、実データのScore Timelineが連続線として出力されることを確認。表示スクリプトのみの変更のためworker完全再起動は未実施。
- **テスト**: `tests.test_score_timeline` 3件、`tests.test_status_dashboard_founding_rate` 22件、direct broadcast overlay Node 11件、dashboard関連escapeテスト3件、`git diff --check` が成功。

## 2026-08-21 07:44 JST — Stats `__invalid_agent__` の実データ確認（変更なし）

- VM `/home/ubuntu/soren/tmp/state/ai_stats/20260821.jsonl` の06:07:31 JSTに、`RADIO:batch_commentary` が `agent:"__invalid_agent__"` で `attempt` → `fail` となり、直後に `all_failed` が1件記録されていた。成功 `winner` はない。
- Statsのagent表は `attempt` を1、`winner` を0として表示するため、`__invalid_agent__ 1 0` は実モデルの呼出回数ではなく、不正なagent識別子で失敗した1試行を示す。該当行は今回のmuse/DeepSeek通常チェーンの成功行ではない。
- 当時のdispatcherは未知のagentをCodex既定モデルへ送る旧経路があり、課金の有無はこのJSONLだけでは確定できない。現在のチェーン設定と反映後の新規Statsでは同じsentinelの追加発生は確認していない。今回は診断のみでコード変更なし。

## 2026-08-21 07:48 JST — deferred radioレンダー飢餓の根本修正・VM復旧

- **原因（確認済み）**: `say_enqueue.sh` の `radio_render:*` は、チャンク境界で前景音声の priority waiter を検出すると `rc=75` で終了していた。`render-only` は完成品全量を要求するため部分WAVを削除し、`radio_state.sh` が同じ本文をチャンク0から再試行した。FIFO先頭にready WAVがない間は後続項目も再生しないため、AI原稿の `generating` 状態が進んでも音声再生世代は進まなかった。
- **修正**: `say_enqueue.sh:_acquire_voicevox_synth_lock` で背景ラジオは前景priority waiterと合成ロックの解放を待ち、同じプロセス・同じレンダー世代で次チャンクを継続する。既定は無期限待機（`VOICEVOX_RADIO_PRIORITY_WAIT_SEC` / `VOICEVOX_RADIO_LOCK_WAIT_SEC` の正数指定時のみ上限）。チャンク間ログとテスト期待値も「後で再試行」から「同一世代継続」へ更新。
- **リポジトリ**: `soviet_now` `8e4a1b7e2 fix: keep deferred radio render generation alive` を `origin/codex/no-apply-liveliness` へpush。変更は `say_enqueue.sh` と音声ロック公平性テスト2本のみで、無関係な作業ツリー変更はコミットしていない。
- **テスト**: `bash -n`、`test_say_voicevox_fairness.sh`、`test_say_voicevox_priority.sh`、`test_radio_render_retry.sh`、`test_radio_deferred_queue.sh`、`test_radio_caption_bundle.sh`、`test_radio_backpressure.sh`、`test_radio_time_sync.sh`、`test_audio_worker_failure_warning.sh`、`test_radio_script_backup.sh`、`tests.test_radio_parser`（8件）、`tests.test_say_streaming`（8件）が成功。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-073215-radio-render-generation/` に反映前 `say_enqueue.sh` と、停止時の先頭 `jiji` 本文・`.rendering` をバックアップ。修正版SHA256 `b499ed5ac7636497063222efc6b5a279473cf0e7cdb3bb738485814a491e1a02` をローカル/VMで一致、`bash -n` 成功。`soren-runtime.service` を停止して旧レンダーを終了し、先頭の stale `.rendering`/`.render_retry` だけをクリアして起動。再起動後のserviceはactive、worker PIDファイルは audio `371226` / radio `371362` / chat `371179`。
- **実パス確認**: 再起動後の先頭 `radio_1787251174_43806_jiji_7636.txt` は07:39:45の1/24から同じレンダー世代で進み、07:47:22に24/24完了。`ready.wav` 公開07:47:26、`再生開始`07:47:28を確認し、`render_retry`は作成されなかった。旧ログにあった「優先音声へ合成順を譲る → 再試行」は新レンダー区間には出ていない。次のFIFO先頭は `radio_1787251458_43806_news_9891.txt`。
- **作業報告音声**: VMの `played.log` で `work_indicator` 音声を07:36:11、07:37:13に確認。共有の15分抑止状態（`work_audio_last.json`）が有効なため、この修正専用の重複音声は追加していない。バナーは作業中のまま、最終停止時にactive=falseを確認する。
