# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-22 03:0x JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: タイムアウト追加余裕(COMMENT 240s/RADIO 360s)へ再設定・実測、改善レーンANALYZE 900s timeout を issue #22 へ起票。

## 2026-08-22 05:3x JST — タイムアウト値の更なる余裕増し＋改善レーンtimeout起票（VM反映・実測済み）

- **ユーザー指示**: 「もう少し余裕入れてもいいかも」＋「改善レーンの修正はissueに」。
- **反映**（VM `.env`）: `COMMENT_CODEX_TIMEOUT=180→240` / `RADIO_CODEX_TIMEOUT=300→360`。sourcing proof `comment=240 radio=360` ✓。radio/chat worker全インスタンスTERM→respawn（radio 879816/chat 879723）、重複全1・配信LIVE維持 ✓。improve稼働中のためsoren_loopは触らず。
- **効果の直接実証（前段・05:17実測）**: COMMENT(amd)が173秒でok→winner＝旧90sでは死んでいた呼び出しが成功。240sで更に余裕。
- **issue起票**: https://github.com/azumag/docich/issues/22 — ANALYZE(1):primary の900秒rc=124が2回連続(05:01:41/05:16:41実測)。**訂正・補足**: 900s自体は同日04:0xセッションが意図的に新設したStage1分析専用上限(wall 3600s食い潰し対策)。ただし実測で900s超えが連続しており、`.env` の `IMPROVE_ANALYZE_CMD_TIMEOUT_SEC` 上書きで調整可。issueへ文脈コメント済み。
- **並行セッションとの絡み**: 05:21の `improve_daemon.paused` / `prediction_worker.paused` マーカー(source:"webui" 実測)は05:1xセッションのwebui worker停止/開始機能によるもの。improveの実ジョブ(ANALYZE#3)はpause後も孤児プロセスで稼働継続・heartbeat生きている(soren_loop harvest設計)。勝手な解除禁止。
- **未確認**: 新値240/360での実トラフィック観測（次回生成時）。

## 2026-08-22 05:1x JST — WebUI予想・改善ワーカー停止/開始コントロール（実装・VM反映・ライブ実測済み）

- **ユーザー要件**: webuiに予想ワーカー(prediction_worker)・改善ワーカー(improve_daemon)の停止/開始を追加したい。
- **既存機構の確認（実測）**: supervisor (`start_all.sh`) の pause gate — `tmp/state/<worker名>.paused` マーカーがある間そのworkerを起動しない・死んでもrespawnしない (start_all.sh `_worker_paused`)。prediction_worker.sh は起動時マーカー自己検知でexit。improve_daemon.sh にはマーカー自己検知が**ない**（supervisor経由なら効く）。
- **実装**（docich `2121565`、main/handoff branch push済み、VM `/home/ubuntu/docich` 同期・`docich-webui` 再起動済み）:
  - `POST /api/workers {worker: prediction_worker|improve_daemon, action: start|stop}`: **stop** = マーカー作成 → pidfileのPIDへSIGTERM（`_pid_matches_worker_process` cmdlineガード、macOSは許可）→ 最大10秒pidfile除去待ち。**start** = マーカー削除 → supervisor respawn (pidfile出現+生存) を最大30秒待ち、未復帰ならhint返却。
  - `GET /api/workers` 各要素へ `paused` フィールド追加、status に "paused" 値。TOGGLEABLE_WORKERS に2workerを追加。
  - improve_daemon stop 時、improve_state.json status=running+PID生存なら `job_continues_in_background: true` を返す（ジョブ子プロセスは孤児化してバックグラウンド継続、harvestはsoren_loopが担うため結果は失われない設計）。
  - UI: Streamタブ内に「予想・改善ワーカーの停止 / 開始」カード（状態badge/pid/start/stopボタン、confirm文はworker毎に分岐）。StatusタブのWorkers詳細表もpaused badge対応。10秒自動更新。
- **テスト**: `tests/test_webui.py` +8件（stop/start往復、job_continues通知、invalid worker/action 400、read_only 403、pausedフィールド、UI要素、cmdlineガードのunknown workerフォールトクローズ）→ 計97件全成功。
- **VM反映・ライブ実測**（05:11-05:12 JST）: GET /api/workers pausedフィールド配信 ✓。invalid値400 ✓。index にwc-rows ✓。**prediction_worker ライブサイクル**: stop=term_sent/stopped true・マーカー作成・プロセス消滅 ✓ → 10秒経過してもsupervisor respawn無し ✓ → start=新PID 734365で復帰、workerログ「起動 (PID=734365, game=44234)」✓。
- **未確認**: (1) improve_daemonのライブstop/start（実施時に改善ジョブstatus=running稼働中のため意図的に未実施。機構は同一・単体テスト済み。ジョブ稼働中の停止はジョブがバックグラウンド継続する点をUI/レスポンスで警告）。(2) supervisor不在時のstart hint表示の画面確認。(3) 改善ジョブ稼働中の実際の孤児化→soren_loop harvestの運用観測。
- **備考**: レビュー委任サブエージェントは2回とも空回答（handoff既知事象）のためメインループでセルフレビュー実施。作業中バナー開始/終了・音声enqueueを実施済み。

## 2026-08-22 04:4x JST — 配信開始位相の正午監査ワーカー新設（実装・VM反映・テスト済み／実正午発火は未観測）

- **ユーザー要件**: Twitchの48h強制切断後に「JST 12:00 正午開始・約2日周期」へ自動是正したい。当初案「正午チェックで稼働34h以上なら再起動」は**不十分**と解析: カット後のsupervisor即再接続が位相を保存するため、正午に観測される稼働時間は位相φにつき `{24−φ}, {48−φ}` の2値のみ。開始が02:00〜12:00帯(φ>14h)だと閾値に届く前に48hカットが来て永久に非正午ループに固定される(例: 08:00開始では正午uptimeが4h/28hを行き来)。
- **採用設計**: Twitchカットを「正確な48hタイマー」として利用し位相だけ監査。毎日JST 12:00に1回、現在の `started_at` のJST時刻が正午±`STREAM_NOON_AUDIT_TOLERANCE_SEC`(既定600s)以内なら無処置、外れていれば wiki正規手順 (`lib/direct_stream.py stop`) → 一時pause marker(`tmp/state/direct_stream.paused`)でsupervisor再spawnを防ぎつつ graceful停止 → marker削除 → respawn待ち(90s) → 超過時は自前起動フォールバック(direct_stream.py run の flock が二重防止)。任意の乱れから最長2日で正午グリッドへ収束。定常状態ではTwitchカットが正午付近で来るため強制再起動ゼロ。EXIT trapで異常終了時もmarker解除(dead air防止)。是正過渡期だけ配信短縮があり得る(位相とラン長は両立不可、仕様)。
- **実装**（soviet_now `50f3cb057`+`969078497`、親 docich submodule bump `0ae8436`、各push済み）:
  - 新規 `workers/stream_noon_audit.sh`: 日次マーカー `tmp/state/stream_noon_audit/<jst_day>.json` に decision/outcome を記録(1日1回保証)。skipped_paused(意図的停止は触れない)/skipped_not_running/skipped_bad_status/no_action/restart_required→restarted|restart_failed を判別。
  - `start_all.sh`: ffmpeg backend roster へ `stream_noon_audit` 追加(pidfile/pattern/重複検出python辞書/`STREAM_NOON_AUDIT_ENABLED!=1`時スキップ)。
  - 新規 `tests/test_stream_noon_audit.sh` 27項目(stub status/stop/run/now で分岐網羅)。config.sh は未変更(既定値はワーカー内蔵、`.env` 上書き可: ENABLED/POLL_SEC/TOLERANCE_SEC/RESPAWN_WAIT_SEC/STOP_TIMEOUT_SEC ほかパス系)。webui の worker 一覧は `tmp/state/*.pid` glob で自動表示されるためUI変更なし。
- **テスト**: ローカルmacOS 4回・VM 1回すべて 27/27。開発中に検出したバグ: `_log`がstdoutへ流れて`_restart_stream`の戻り値を汚染(stderrへ修正)、実行ビット欠落(100755へ修正)、テスト側はgrep -c終端コード/grep -E交互パターン/launch直後0.3sのpidfileレースを修正。
- **VM反映**: `.codex_deploy/backup-20260822-0430-noon-audit/start_all.sh` 退避後、start_all.sh+worker+testをscp、SHA256一致(`13d82c3b`/`e74fc7a0`/`62072017`)、bash -n成功。`sudo systemctl restart soren-runtime.service` 完全再起動(**配信が04:38:49→04:38:57の約8秒途切れ**)。新rosterで `stream_noon_audit`(PID 389335)起動・workerログに起動行を実測。Twitch(35.55.x)/YouTube(74.125.x)両レグESTAB再確認。
- **dry-run実測**: 再開直後のstatus.jsonに対し位相計算を実行 → started_at=1787341137(04:38:57 JST)、offset_diff=-26463s>600s → restart_required 判定。**本日12:00 JSTに最初の自動張り直しが入る**(視聴者からは数十秒の切断)。
- **未確認**: (1) 実正午での初回発火と、その後Twitchカットが正午付近に定着するサイクルの運用観測(本日12:00と翌日以降)。(2) Twitch 48hカット実際の実施時刻ジッタ(±600s許容に入るか。外れる日は1回余分に是正が走るだけだが観測は必要)。(3) 日中の手動stop/start直後が正午へ引き戻される挙動の実運用確認。
- **運用メモ**: 無効化は VM `.env` へ `STREAM_NOON_AUDIT_ENABLED=0` + soren-runtime再起動。当日分の再監査は `tmp/state/stream_noon_audit/<jst_day>.json` 削除で。

## 2026-08-22 04:0x JST — 改善ループ自体の改善（no_apply backoff上限600s・Stage1タイムアウト900s）実装・VM反映済み

- **Ralph Loop タスク**: 「ソ連が建国できるように、改善ループ自体の改善を含む、戦略（strategy.py) 改善をせよ。ソ連が建国できるまで終わってはならない」。ループ継続中。
- **ゲーム現状（04:00実測）**: 蓄積33試合、ウクライナ(T13)率74%、カザフ(T14)13%、ロシア(T15)3%、**ソ連(T16)0回**。
- **敗因パターン（実測2試合分析）**:
  - 最高スコア試合(3293点): **T14×2まで作ったのにT15併合前にdeadline超過死**(margin -1.22)。最終盤面 `{14:2, 13:1, 11:3...}` — T14+T14→T15 の素材は揃っていた。
  - 別試合(1534点): guard発火ターン78/96(**81%**)で延命→素材残したまま死(margin -2.7)。
  - 共通構造: 終盤、高type育成とdeadline回避が両立できずガード延命→詰み。improve_brief の hard_signal「ロシア到達後に type16 へ進めていない」と一致。
- **改善ループ問題（実測）**: failed_no_apply が本日5回 (07:37/19:45/20:56/22:22/00:48)。原因は wall timeout 3600s — Stage1(モデル1800s上限×2リトライ)が予算を食い潰し Stage2 実装中に死亡 (`elapsed=3704s phase=ai_retry1`)。失敗のたび指数backoffが伸び count=6=9600s待ち → 改善頻度激減。
- **実装**（soviet_now `8117b12e5` push済み、VM反映・daemon再起動済み）:
  - `_schedule_improve_retry_backoff`: backoffファイル行3へ `no_apply` 種別タグ追加（1〜2行目形式は不変＝既存テスト互換）。
  - 新関数 `_improve_backoff_wait_sec`: no_apply系は `IMPROVE_NO_APPLY_BACKOFF_MAX_SEC`(既定600s)で上限キャップ、rate_limit系(タグ無し)は従来どおり300×2^min(n-1,5)=最大9600s。daemon側判定も同関数経由へ変更。
  - eloop_improve.sh: Stage1分析専用 `IMPROVE_ANALYZE_CMD_TIMEOUT_SEC`(既定900s)を新設、Stage2突入時に `${IMPROVE_RUN_CMD_TIMEOUT_SEC:-1800}` へ復元。
- **テスト**: tests/test_improve_retry_reliability.py +2件（no_apply上限・タグ、Stage1/Stage2タイムアウト切替位置）→ 20件全成功。test_escape_mechanisms.py 107 failures は stash比較で**クリーンHEADでも同数＝既存の無関係な失敗**（handoff L181記載どおり受入れ判定外）。test_improve_peak_hour_defer 16件成功。
- **VM反映**: `.codex_deploy/backup-20260822-0340-noapply-backoff/` 退避後2ファイルscp、SHA256一致(eloop `168ddeed…` / improve.sh `0913626e…`)、bash -n OK。improve_daemon 完全再起動(旧2861945→新4175869)、新関数ロードと計算値実測(`_improve_backoff_wait_sec 6 no_apply`=600 ✓ / `99`=9600 ✓)。
- **設計判断**: wall timeout延長(3600→5400)は見送り。改善中は放送系AI生成が待機(AI_RADIO_IMPROVE_WAIT_MAX_SEC=1200s上限)するため、ジョブ長期化は放送停止を悪化させる。C+B適用後の成功率観察が先。
- **稼働中ジョブ注意**: 03:29開始のジョブ(PID 3854979)は旧スナップショット設定で動作中（新設定は次回ジョブから）。このジョブがwall timeout死しても、次回からはbackoff 600sで早期再試行される。
- **未確認**: (1) 次回改善ジョブでの新タイムアウト・新backoffの実運用挙動。(2) 改善AIによる戦略改善の適用成功（harvest時 hash_before≠hash_now）。(3) strategy.py 本体の数値調整は改善AIサイクルへ委ねている（手動変更は regression 機構と冒頭警告「数値を書き換えるのは危険」により非推奨）。

## 2026-08-22 04:xx JST — AIタイムアウト値引き上げ（VM .env反映・worker再起動・実測済み）

- **ユーザー指摘**: 「AIの失敗理由にタイムアウトが多い。タイムアウト時間が短いのでは？」→ 実測で一部正当と確認。
- **実測（VM `tmp/state/ai_stats/2026082{0,1,2}.jsonl` 4,359件）**: 失敗453件中タイムアウト31件。ただし394件はエラーフィールド記録開始(8/21夜)前の無記録で、**記録済み失敗59件中31件(53%)がタイムアウト**＝Stats画面での体感どおり。内訳: 240s×17(RADIOレーン)/120s×8(fact-check等)/90s×6(COMMENTレーン)。
- **乖離の実証**（`tmp/debug/ai_dispatch/*_output.txt`−`_prompt.txt` のmtime差、成功呼び出しのみ）: x-preview-f-free 中央値145s/p90 376s（成功の60%が90s超え・32%が240s超え）、amd-deepseek-flash p90 233s（30%が90s超え）、minimax-m3 の39%が120s超え。→ COMMENTレーン90s(`lib/ai_generate.sh:1003` `${COMMENT_CODEX_TIMEOUT:-90}`)が特に短すぎ。タイムアウト(rc=124)はリトライ対象外で即フォールバック(`ai_generate.sh:744`)。
- **反映**: VM `.env` へ `COMMENT_CODEX_TIMEOUT=180` / `RADIO_CODEX_TIMEOUT=300` を追加（バックアップ `.codex_deploy/backup-env-20260822-timeout/.env.042915`）。config.shは両変数を定義しないため上書き競合なし。
- **再起動**: improveジョブ稼働中(phase ai_retry1)のため **improve_daemon・direct_stream配信は触らず** radio_worker(2862162)/chat_worker(2861962)/audio_worker(2862014)/soren_loop(2861680) をTERM。watchdogがradio/audio/chatを新PID(308284/304052/292180)でrespawn、重複カウント全て1。
- **運用知見（重要）**: ①`soren-runtime.service` ユニットは現存しない（`systemctl --user cat` → No files found）。実態は `start_all.sh --supervisor`(PPID=1) 直下＋python watchdog。旧handoffの同unit表記は要読み替え。②watchdogは `_find_existing_worker_pid` で**残存プロセスを採用する**ため、単一PIDだけTERMすると旧環境のstrayサブシェルを採用してしまう。正規の完全再起動は **同名プロセスを全部killしてから** watchdogにspawnさせること。③soren_loopは `tmp/improve.lock` 存命中は意図的にrespawnしない設計（`start_all.sh` 監視ループ内 `continue`）— 改善完了後に自動復帰。④`/proc/<pid>/environ` はexec時スナップショットなので、起動後にsourceした `.env` 値は映らない（検証はworker起動経路の再現 `bash -c 'set -a && . ./.env && echo ...'` で行う）。
- **検証実測**: sourcing-proof `comment=180 radio=300` ✓（worker起動経路そのもの、新PIDは.env改変後の起動）。配信 running=True/30fps/4632kbps 維持 ✓。chat_worker IRC起動ログ ✓。
- **未確認**: (1) 実トラフィックでの `timeout after 180s/300s` ログへの変化（次回タイムアウト時に観測）。(2) soren_loopの改善完了後自動復帰（設計上の待ち。強制spawnはしていない）。(3) RADIO 300sでも x-preview-f-free のp90 376s超え層は落ちる可能性（チェーン後段で拾う）。
- **反映後の実トラフィック観測（05:01-05:22実測）**:
  - **効果の直接実証**: 05:17:45にCOMMENT(amd-deepseek-flash)が**173秒でok→winner**。旧90s制限ならタイムアウトしていた呼び出しが新180sで成功。ただし残り7秒の僅差であり、さらに余裕を持たせる検討余地あり。
  - **RADIO prepassは未試験**: deals/news/soviet prepassはimproveゲートで1200s満額待ち→05:21-05:22にrc=1でゲート放棄（タイムアウトではなく設計どおりの諦め）。新300sのcodex呼出しには未到達。次回改善サイクル非稼働時の生成で初観測となる。
  - **soren_loop自動復帰確認**: 04:38:54にwatchdogがrespawn ✓（improve.lock待ち設計どおり）。
  - **新たな観測課題（未対応）**: 改善レーンの `ANALYZE(1):primary` が**900秒タイムアウトで2回連続rc=124**（05:01:41・05:16:41、`strategy/ai.sh` run_cmdの `/usr/bin/timeout 900` を実測）。今回変更外のIMPROVE系固有上限だが「タイムアウト短すぎ」パターンが改善レーンにも存在。900の出所と適正値の調査は次タスク候補。

## 2026-08-22 03:0x JST — WebUI配信停止をwiki正規手順(stdin q)へ準拠（実装・VM反映・単体テスト済み／ライブstop未実施）

- **ユーザー指摘**: 配信停止は送信をやめただけでは Twitch 上の配信停止にならない。wiki `Stream-Ending` を参考に停止ボタンを実装すること。**停止・開始のテストはもうしなくてよい**。
- **問題（前実装 bcb4e47 の親コミット d15f6cb）**: stop を runner への SIGTERM＋5秒でKILLエスカレートとしていた。runner のシグナルハンドラは ffmpeg stdin へ `q` を流して RTMP 正常終了 (FCUnpublish/deleteStream) する経路だが、猶予は q15秒+SIGINT15秒=最大約30秒であり、5秒でのKILLはこの正常終了を打ち切り得た（回線断扱い→Disconnect Protection 最大90秒 LIVE 残存リスク）。
- **実装**（docich `bcb4e47`、main/handoff branch push済み）:
  - `_run_direct_stream_stop_script`: 第一選択として `python3 lib/direct_stream.py stop` を subprocess 実行 (cwd=soren_root、timeout 25s)。wiki記載の正規手順そのもの。
  - スクリプト不在・失敗時のみフォールバックで runner へ SIGTERM (`direct_stream.py stop` と同等の効果)。KILL エスカレートは 35秒経過後（runner の q/SIGINT 猶予を保護）。
  - レスポンスへ `method` (direct_stream_stop|signal_fallback) を追加。UIヘルプ文を wiki 手順の説明に更新。
  - テスト +3件（スクリプト優先・フォールバック・missing）。macOS では TERM 死滅子プロセスがゾンビのまま `os.kill(pid,0)` 成功＝生存誤判定になるため、pid検出もスタブしてロジック検証。計89件全成功。
- **VM反映**: `/home/ubuntu/docich` → origin/main `bcb4e47` 同期、`systemctl --user restart docich-webui` active。GET /api/stream state=live・invalid action 400・新ヘルプ文配信を確認。**pause マーカー無し＝配信には一切触れていない**。
- **未確認（意図的に未実施）**: 実配信に対する webui 経由 stop→Twitch OFFLINE 確認、および start 復帰。次回実際にボタンを使う際に Twitch GQL / 画面で OFFLINE 化（wiki実測では約15秒）を観測すること。

## 2026-08-22 02:4x JST — WebUI Streamタブ追加（配信オンオフ・設定UI）（実装・VM反映・実測済み）

- **ユーザー要求**: webuiに配信のオンオフ、設定などのUIがほしい。
- **実装**（docich `d15f6cb`、main/handoff branch push済み）:
  - `GET /api/stream`: `tmp/state/direct_stream/status.json` ＋ pause マーカー判定で `{state: live|paused|off, fps, bitrate, drop_frames, out_time, uptime_sec, pid, ffmpeg_pid, chat_paused, ...}` を返す。runningはPID生存確認込み。
  - `POST /api/stream {action}`: **stop** = `tmp/state/direct_stream.paused` マーカー作成 → runner/ffmpegへSIGTERM（猶予半分超過でKILL、最大10秒）。**start** = マーカー削除 → supervisor自動respawnを最大20秒待機。無関係プロセス誤殺防止に `/proc/<pid>/cmdline` ガード（`direct_stream` or ffmpeg basename。macOSは読めないため許可）。
  - `POST /api/chat {action}`: `tmp/state/chat_worker.paused` のトグル。シグナル不要 — chat_workerがマーカーを自己検知して park/resume（既存機構のUI化）。
  - 配信設定キーを WEBUI_ALLOWLIST/DEFAULTS へ追加（`SOREN_DIRECT_STREAM_SIZE/FPS/VIDEO_KBPS/AUDIO_KBPS/AUDIO_DELAY_MS`、`DOCICH_CC_ENABLED`）。バリデータは lib/direct_stream.py load_config と同じ範囲（例: FPS 1-60、映像500-6000kbps、解像度偶数320-3840x180-2160）。**反映には配信再起動が必要**な旨をUI明記。
  - UI: 新「Stream」タブ。状態KPI（LIVE緑表示）、詳細kv、start/stopボタン（confirm付き、状態でdisable）、チャットトグル、設定フォーム。10秒自動更新。read_onlyモードでは操作ボタン無効。
- **テスト**: `tests/test_webui.py` +15件（バリデータ、state遷移3態、マーカー、HTTP roundtrip、read_only 403、invalid action 400）→ 計86件全成功（python3.14）。
- **VM反映**: `/home/ubuntu/docich` fetch→reset→origin/main `d15f6cb` 同期、`systemctl --user restart docich-webui` active。
- **実測検証**:
  - 画面: VM上で独立headless chromium（playwright、ゲームbridgeには非接触）でStreamタブを開き、「LIVE」表示・ffmpeg|1280x720|29.99fps|4634.5kbits/s・stop有効/start無効・fps入力30 をスクリーンショット+DOM実測。
  - API: pre state=live（runner pid 2862374、Twitch 35.55.x＋YouTube 64.233.x レグESTAB）。
  - **本番stop→startサイクル（ユーザー承諾済み）**: stop=4.0秒でgraceful完了（escalated_kill:false、runner/ffmpeg 0件、マーカー作成）。8秒以上pause維持でsupervisor再起動なし・レグ0を確認。start=2.0秒でlive復帰（新PID 3213009/3213100）、12秒後にbitrate 4536kbps安定、Twitch/YouTube両レグESTAB再確認。
- **備考**: 検証中に `.env` へ `SOREN_DIRECT_STREAM_FPS=30` を書くPUT試験を実施（既定値と同値のため挙動不変。webuiが自動バックアップ `.env.bak.1787332928394114748` 作成）。chatトグルの実運用テストは未実施（機構自体はhandoff既記載のpause/resume実測済み）。opusサブエージェントは設計委任時に空回答×2だったため設計・セルフレビューはメインループで実施。

## 2026-08-22 02:2x JST — AIレーン同時実行制御の実装（実装・VM反映・実測済み）

- **ユーザー要件**: 放送系AI生成の並行起動はおかしいのでキュー化。全体は放送系1・コメント返し1・改善1の最大3並列。改善中は新規の放送系生成を止めるが、改善開始前に始まった放送系生成はキャンセルしない。読み上げは継続。コメント返しもキュー化。
- **実装**（soviet_now `6b5ddc70e`、docich `1499bad`）:
  - `lib/ai_generate.sh`: `_ai_queue_lock_scope` が RADIO*/NEWS*/JIJI*/CELEBRATION* を単一 `radio` ロックへ畳む（従来はモデル別スコープで別モデルが並行していた＝20:56実測のprepass二重起動の原因）。COMMENT* は `comment` レーン。新 `_improve_job_active`（improve_state.json の status==running ＋PID生存＋7200s stale）。新 `_ai_radio_improve_gate`（改善稼働中、新規RADIO系呼び出しのみ待機。`RADIO_GEN_STARTED_AT` < improve started_at なら通過=キャンセルしない。上限 `AI_RADIO_IMPROVE_WAIT_MAX_SEC`=1200s 超過でその生成だけ諦め rc=1）。
  - `broadcast/radio_engine.sh`: `_radio_opencode_should_defer_for_improve` を rate_limit_backoff 判定のみに縮小（**improve.lock は常時存在するデータファイルなので信号に使えない**旧バグを修正。これで opencode 系が改善中にほぼ常時 fail-fast していた状態も解消）。`_radio_generate_and_play` 冒頭で `RADIO_GEN_STARTED_AT` をexport。
  - `broadcast/radio_corners.sh`: jiji研究呼出が generate_and_play 前に先行するため `start_radio_corner_jiji` 冒頭でも同export。
  - `core/config.sh`: 新既定値 `AI_RADIO_LANE_LOCK/AI_COMMENT_LANE_LOCK/AI_RADIO_IMPROVE_GATE=1`, `AI_RADIO_IMPROVE_WAIT_MAX_SEC=1200`。
  - 新テスト `tests/test_ai_lane_queue.sh` 22項目（ローカル/VM両方で全成功。bashの「代入文への前置代入は永続する」罠と `AI_GENERATION_QUEUE_LOCK_DIR` がスコープ接尾辞を付けない既存仕様に注意）。
- **VM反映**: `.codex_deploy/backup-20260822-0145-ai-lane-control/` 退避後4ファイルscp、md5一致、`bash -n` 成功。config.sh 既定値変更のため **soren-runtime.service 完全再起動**（radio/chat/audio worker・improve_daemon・soren_loop 全て新PID）。VM側 tests/test_ai_generate_backoff.py が古かったため現行版を同期（31テスト全成功）。
- **実測検証**: 実idle状態で `_improve_job_active`→INACTIVE ✓。本番パスと同一ラベル形状で `_ai_generation_queue_run` 実行中に `tmp/state/.ai_generation_locks/{radio,comment}` 出現を実測 ✓。読み上げ（VOICEVOX合成・再生）は再起動後も継続 ✓。デッドロック無しの構造確認（ゲートはロック取得前、improveはレーンを取らない）。
- **未確認**: (1) 実際の改善ジョブ稼働中の放送系待機ログ `[AIQ:...] improve cycle active` は自然発火で未観測（遅延キュー60件>5により新規ラジオ生成が抑制中＝既存挙動。ユニットテストでは動作確認済み）。(2) 次回改善サイクル中の放送系生成が実際に待つかの運用観測。(3) chat再開後のコメント返しの comment レーン経由化。

## 2026-08-21 21:0x JST — AI同時実行上限の現状確認（調査のみ・未変更・→翌日実装済み、上の節参照）

- **結論**: 上限機構は未実装のまま（handoff既記載どおり）。`lib/ai_generate.sh` にロック/セマフォ/同時数制御なし（ローカル+VM grep実測）。`WILDCARD_PARALLEL_*` はゲーム並列でAI呼出制御とは無関係。
- **VM実測 (20:56-20:59)**: 放送系ラジオprepass codex exec ×2（amd-token-factory-deepseek-v4-flash）と改善系 `strategy_runner.py` → codex exec が**同時3呼出で走行中**。CPU idle ~1%（vmstat実測、Chromium 86%/VOICEVOX 58%/ffmpeg 47%が主占有）。`tmp/improve.lock` は改善サイクル稼働中のものが存在。
- **含意**: 現状は放送系と改善系が無制限に重なる。ユーザーは「AI Improve はできれば止めたくない」と表明。次タスク（issue #7必須条件）は Improve を止めない形での同時実数制限の設計。

## 2026-08-21 20:4x JST — YouTube同時配信 push 実装（VM反映・サーバ側実測済み）

- **実装**: issue #7 の計画どおり、VM `/etc/soren-rtmp/push.conf` に `push rtmp://a.rtmp.youtube.com/live2/<KEY>;` を追加（2行構成: Twitch＋YouTube、再エンコードなし）。キーはユーザー提供（ローカル /tmp/ytk 経由で VM /tmp へ転送後削除。push.conf のみに記録、repo/.env/issue には不記載）。事前バックアップ: `soren/.codex_deploy/backup-20260821-2025-youtube-push/push.conf`。権限 root:soren-relay 0640 維持。
- **再起動**: `sudo systemctl restart soren-rtmp-relay.service` → active・127.0.0.1:1935 LISTEN 再確認。エンコーダ ffmpeg は `lib/direct_stream.py` の supervise ループが自動再起動（新PIDで publish 再確立を実測）。
- **実測（反映直後）**: 3レグ ESTAB — ffmpeg→127.0.0.1:1935、nginx→35.55.x（Twitch）、nginx→173.194.194.134:1935（YouTube ingest）。
- **安定性**: YouTube レグ接続が 60秒間サンプリング6回すべて ESTAB（無切断＝キー拒否なら再接続チェーンが出る）。relay journal の error/fail 0件。
- **増分**: TX 合計 ~9.5Mbps（60sで71MB、Twitch分+YouTube分。issue予測 ~9.2Mbpsと一致）。nginx worker CPU 30s平均 **0.4% core**（publish 込み。issue予測どおり増分微小）。
- **未確認**: (1) ユーザー視点の YouTube Studio ストリームヘルス／実際の視聴画面。(2) `.env` の `YOUTUBE_VIDEO_ID` は 5/20 の旧配信（chat poll 用）で現行配信と紐付かないため API での live 判定は不能だった。
- **残課題（必須条件が未実施）**: issue #7 の必須条件「AI 同時実行数の制限」は未実装。CPU idle は反映後も 0.4〜1.7% で飽和気味。次タスク候補。

## 2026-08-21 19:xx JST — ラジオ「でございます」過剰敬語の抑制（実装・VM反映済み）

- **ユーザー観測**: ラジオで「〜でございます」が異常に頻発する時がある。プロンプトで防ぎたい。
- **実測（VM `tmp/debug/ai_dispatch` 直近7日、RADIO出力687本）**: 「でございます」入りは9本。最悪は 20260821_181529 night_snack（minimax-m3）で**1本23回**、次いで8回・6回・5回。ほぼ全て minimax-m3 由来（deepseek-free は1回×2のみ）。
- **原因**: ペルソナ/出力ルールが「だ・である」「ね」「よ」「しまして/でして」は禁止するが**「でございます」系の過剰敬語は未指定**。「ですます調の徹底」「1文でも混じったら失格」の強い圧力で弱めモデルが過剰敬語へ暴走。既存 `_normalize_radio_tone`（radio_engine.sh）は「ね/よ」除去のみで「でございます」無対応。コメント側プロンプト（comment_response.md:129）には既に ございます 禁止があり、ラジオ側だけ抜けていた。
- **実装**（soviet_now `acaeec49c`）:
  - `broadcast/radio_persona.sh`: ペルソナ3ブロック（soren91/main/rollback）と `_radio_output_rules` に「でございます」「〜でございました」「〜ております」禁止＋×→○例を追加。
  - `broadcast/radio_engine.sh` `_normalize_radio_tone`: 冒頭に機械的置換を追加（でございまして→です／でございました→でした／でございます→です／ております→ています）。`おはようございます` は影響なしを実測。
- **検証**: bash -n（ローカル/VM）、radio系シェルテスト6本成功。実物の悪例23回（9行）が正規化関数で0行化、挨拶の保存を確認。VM反映後 `_radio_persona_block`/`_radio_output_rules` の出力に新規則が出ることを実測。radio_worker は USR1 reload 完了（19:10:33、PID 2769060 維持）。
- **VM反映**: `.codex_deploy/backup-20260821-1930-gozaimasu-tone/` 退避後2ファイル scp、SHA256一致。docich main/handoff branch `9c1afed`（submodule bump）、VM `/home/ubuntu/docich` も同期済み。
- **未確認**: 次回以降の実ラジオ生成での発話レベルの改善（観測は今後の debug 出力で継続）。
- **作業ツリー訂正**: ローカル `games/soviet_now/broadcast/radio_persona.sh` に前セッションの壊れた未コミット編集（二重 else で syntax error、一人称「私」追加のボツ案）が残っていたため HEAD へ復元した上で本修正を適用。一人称規定の追加自体は引き続き未実施（19:xx調査節の「ユーザー確認待ち」のまま）。他セッションの変更中ファイル（batch_commentary.sh, comment.sh, prompts/celebration.md, comment_persona_main.md）には触れていない。

## 2026-08-21 19:3x JST — フォローアップ3件完了（実装・VM反映・実測済み）

- **①codex失敗の可視化**: エラーpreviewがstderr先頭のセッションバナーで埋まり真因が切れていたため、`_ai_error_preview_from_text` を200字超で「先頭70字…総字数…末尾90字」のhead+tail形式へ変更。併せて `_ai_call_codex_unqueued` の codex exec に `</dev/null` を固定（stdinがパイプだと `<stdin>` ブロックとして読まれる仕様の回避）。
- **②WebUI Stats拡張**（docich `66adafb`）: `/api/stats` に `recent_errors`（error付きfail最大40件・新しい順）と `by_agent[].fail` を追加。Statsタブに「最近のAI失敗理由」カードと agentテーブルの fail/失敗率 列を追加。VM webui は user systemd unit `docich-webui.service`（`bin/docich` ラッパー）で再起動し、API実測で recent_errors 6件・x-preview attempt35/winner13/fail10 を確認。※handoff旧記載の「plain プロセス」は実態と不一致だった。
- **③free枠復旧監視**（soviet_now `446b56308`）: `probe_free_slot.sh` を新設（単発プローブ、cron想定 `*/30 * * * *`、FREE_PROBE_MODELS で対象指定、down→recovered 遷移で overlay_notify）。streak≥3の解除では `_ai_fail_streak_clear` が復旧ログを出す。VMで1回実行し `deepseek-v4-flash-free down (rc=1)` / `x-preview-f-free ok` を実測（19:22時点でdeepseek-v4-flash-freeはまだダウン中）。cron登録は未実施（ユーザー判断）。
- **テスト**: `tests/test_ai_dispatch_diagnostics.sh` 21項目成功、`tests/test_webui.py` 71件成功。
- **リポジトリ同期**: soviet_now `codex/no-apply-liveliness` `446b56308` push、docich `codex/soren-repo-handoff` `66adafb` push済み。

## 2026-08-21 19:1x JST — free枠(opencode)失敗率の原因特定と観測・レジリエンス実装（実装・VM反映・実測済み）

- **原因（実測）**: レートリミット枯渇ではなく上流free gatewayの不安定さ。(1) `opencode/deepseek-v4-flash-free` は07:06以降ほぼ終日ダウン（CLI直接実行で `UnknownError: Unexpected server error` rc=1 を再現、30/30失敗。ユーザーが16:19の.env更新でチェーン外へ）。(2) `x-preview-f-free` / `muse-spark-1.2-contributor-free` は断続的なストリーム中断 — opencode.db のセッション記録で、失敗呼び出しは reasoning 出力後に `step-finish reason=unknown, output tokens=0` で死んでいる（成功時は reason=stop）。15〜17時にバースト、数分後には自然復旧。
- **観測ブラックホール（副因）**: `workers/chat_worker.sh:314`・`workers/radio_worker.sh:189,202`・`broadcast/radio_engine.sh:1386` の `2>/dev/null` でAI失敗診断が全廃棄され、workerログに stderr 系ログ0件だった。
- **実装**（soviet_now `35d8c418c` + `069961326`）: ①ai_stats JSONL の fail レコードへ `error` フィールド追加（teeパイプラインのサブシェル対策でテンポラリファイル経由。JSON妥当性を回帰テストで担保）②stderr廃棄を `logs/ai_stderr.log` へ変更＋高頻度ノイズ `twitch_chat fetch: 未読コメントなし` を無音化 ③連続provider失敗のサーキットブレーカ（300→×2/×4/×8、上限 `AI_FAILURE_STREAK_MAX_BACKOFF_SEC=3600`、成功で解除。`tmp/state/ai_fail_streak/`）④opencode CLIの一過性失敗（タイムアウト・レート制限以外）を同一モデルで1回だけ自動再試行（`OPENCODE_ABORT_RETRY=0` で無効、`OPENCODE_BIN` でスタブ差し替え可）⑤`UnknownError`/`unexpected server error` を provider error 判定に追加。
- **テスト**: 新規 `tests/test_ai_dispatch_diagnostics.sh` 17項目がローカル(macOS)・VM両方で成功。既存 `test_ai_generate_backoff` + `test_improve_retry_reliability` + `test_model_output_guard` + `test_comment_bilingual` の110件も成功。
- **VM反映**: `.codex_deploy/backup-20260821-1825-free-slot-resilience/` へ6ファイル退避後、lib/ai_generate.sh・core/helpers.sh・workers/2本・broadcast/radio_engine.sh・twitch_chat.sh を反映（SHA256一致、bash -n成功）。chat/radio worker を完全再起動（新PID 2768893/2769060、28分以上安定稼働を確認）+ radio_worker USR1 reload。
- **実トラフィック検証**: ダウン中の deepseek-v4-flash-free への実dispatchで fail レコードに `"error":"rc=1 (4s): Error: { \"name\": \"UnknownError\"...}"` が記録されることを実測。19:00台の実運用では amd の `rc=1: Reading additional input from stdin...` や openrouter/free の `timeout after 90s` など従来不可視だった失敗理由が記録され、x-preview-f-free は ok→winner で自然復旧も実測。ai_stderr.log はノイズなし（2.8KBの診断のみ）。
- **リポジトリ同期**: soviet_now `codex/no-apply-liveliness` へ push（`069961326`）、docich `codex/soren-repo-handoff` `eacdbe9` でsubmodule bump。origin/main は別セッションの merge (`7d4d907d0`) で乖離しているため今回は未マージ。
- **フォローアップ候補**: ①codex CLI が稀に `Reading additional input from stdin...` で即死する件（`_ai_call_codex_unqueued` の stdin `</dev/null` 化で直る可能性・要調査）②WebUI Stats へ error フィールド表示を追加 ③deepseek-v4-flash-free の上流復旧監視。

## 2026-08-21 19:xx JST — YouTube同時配信の処理能力検討（調査のみ・未実装・方針決定）

- **結論**: 可能。新規リレーサーバではなく既存ローカル nginx-rtmp（`/etc/soren-rtmp/nginx.conf`、push.conf でTwitchへpush中）に `push` を1行追加する形（再エンコードなし）。ユーザーがやる方向で決定。
- **必須条件（ユーザー決定）**: AI 同時実行数の制限を必須とする。CPU idle 平均1.6%・最小0%でほぼ飽和（主因: Chromium SwiftShader ~86%、VOICEVOX ~58%、ffmpeg x264 ~47%、AI生成バースト）。
- **実測**: VMは Oracle A1.Flex 4 vCPU/24GB/NW上限4Gbps。remux プル14秒で CPU<0.1%コア・+4.7Mbps。nginx ワーカー全体(publish+Twitch push)平均0.9%コア。YouTube ingest TCP 1935 到達 OK。再エンコード方式は +40〜50% コアで非推奨。
- **3本目以降**: レグあたり +4.7Mbps・CPU 0.3〜0.5% 見込みで問題なし。ただしレグ単位増分は未測定のため追加のつど実測する。
- **記録先**: azumag/docich issue #7「youtube用dociを統合する」にコメント保存済み（https://github.com/azumag/docich/issues/7#issuecomment-5368564672）。キー等の機密は書いていない。
- **未確認**: 実 push 追加状態での増分、YouTube キー発行・受付品質。`youtube_worker.sh` はチャット受信専用で動画は未対応。

## 2026-08-21 20:0x JST — 中華AI一人称「私」統一＋履歴モード分離（実装・VM反映・部分実測）

- **実装**（soviet_now `0df683c18`、docich `78f9277`）:
  - 一人称: `radio_persona.sh` mainブロック（214行目）、`comment_persona_main.md`、`celebration.md`、`batch_commentary.sh` に「一人称は「私」。「僕」「俺」「自分」は使わない」を追加。soren91側（僕）・rollback DJ（私）は既存どおり。
  - 履歴モード分離: `_remember_comment_reply_text` が第2引数modeを受け `_<mode>.txt` 接尾辞付きファイル名に変更（デフォルト・不正値はmain）。`_remember_spoken_comment` は再生ファイルの `.mode` サイドカー→ホストモード順に解決。`_build_recent_spoken_comment_context` / `_build_comment_followup_hints` はmode引数でフィルタし、「再生中」項目もサイドカー照合。旧形式（接尾辞なし）ファイルはmainのみ参照（現行16件は全てmain期のものを実測済み、16件ローテーションで自然消滅）。`.reply_hashes` のモード間共有dedupは意図的に維持。
  - 新テスト `tests/test_comment_persona_mode.sh` 19項目（ローカル/VM両方で全成功）。
- **並行セッションとの競合**: 作業中に別セッションが `acaeec49c`（でございます抑制）を同一ファイル群へコミット。私の一人称行が一時消失→再追加。最終的にコミットは完全分離（私の変更は `0df683c18` のみ）。
- **VM反映**: `.codex_deploy/backup-20260821-persona-mode-split/` 退避後、5ファイルscp、SHA256一致、`bash -n` 成功。
- **反映経緯（重要な落とし穴）**: USR1リロード（radio/chat両方、19:33完了）だけでは **soren_loop.sh 系の長命生成プロセスが旧 `_radio_persona_block` を保持し続け**、19:40:19のjijiプロンプトに一人称ルールが入らないことを実測。→ `soren-runtime.service` を 19:45:50 に完全再起動し、全プロセス（soren_loop 3449493 ほか全worker）が新PIDで復帰。
- **実測済み**: chat_worker がリロード後に `20260821_194029_3456_main.txt`（mode接尾辞付き）を生成。worker再起動後も新試合検知・deferred再生は正常継続。
- **未確認**: (1) 再起動後の次回RADIO AI生成プロンプトへの `一人称は「私」` 載載（deferredキュー5件の再生優先でAI生成が抑止中、キュー消化後に初回生成で確認する）。(2) 実放送での中華AI発話の一人称。（3) soren91代打稼働時の履歴分離の実運用挙動。

## 2026-08-21 19:xx JST — 中華AIが「僕」と自称する原因調査（診断のみ・未変更）

- **ユーザー観測**: 資本主義（メリケンAI）と共産主義（中華AI）のペルソナが合わさった返答がある。「僕」はメリケンAI専用のはずが中華AIも使っている。
- **主因（実測）**: 中華AI（mainモード）の全プロンプト系統に**一人称の指定が一切ない**。soren91側だけ規定がある: `broadcast/radio_persona.sh:176`（ラジオsoren91ブロック=僕）、`prompts/comment_persona_soren91.md:5`（コメントsoren91=僕）。main側は `radio_persona.sh:202-233`（ラジオmainブロック）・`comment_persona_main.md`・`batch_commentary.sh:181`（バッチ解説、当日追加）・`prompts/celebration.md` のどれにも一人称規定がないため、モデルが自由選択している。
- **実出力の裏取り**: VM `tmp/debug/ai_dispatch/` で 8/18〜8/20 の main モード RADIO 生成9本（soviet/theme/news/jiji/strategy、deepseek系）が「僕」を使用。プロンプト側には「僕」「メリケンAI」は皆無（モデルの自発選択）。8/21は211本中0本、COMMENT 146本中0本。内容は生産計画・ソ連ネタ等の中華AI版なのに一人称だけ漂白されていない状態。
- **構造的な混線リスク（現時点で実害未確認）**: `_build_recent_spoken_comment_context`（`comment.sh:573-655`）は `tmp/.comment_queue/spoken_history`（`core/config.sh:695`）を**モード無関係に全件**読み、「最近自分が実際に読み上げたコメント返し」としてプロンプトへ入れる。soren91代打が稼働して「僕」入り返信を喋ると、中華AI生成時に自分の過去返信として提示され得る。現在は履歴16件中「僕」0件・代打未稼働（`soren_loop.log` で「soren91代打を起動せず」を確認）のため未発火。
- **性格記述の重複**: main側ペルソナも「斜に構えた」「褒めるときも素直に褒めない」「皮肉」（`radio_persona.sh:208-209`）とメリケンAIのひねくれ系 traits が重なり、声の方向性が近い。
- **逆方向の混ざりは仕様**: 共有テンプレ `comment_template.md:104` と `comment_persona_soren91.md:4` が両モードに「同志○○」呼びかけを指示（メリケンAIが共産用語を使うのは意図済み、「仲間」の意味と明記）。
- **未実施**: 一人称規定の追加、spoken_history のモード分離、性格記述の差別化はすべて未着手。ユーザー確認待ち。

## 2026-08-21 15:xx JST — 歌唱機能をきらきら星以外にも対応（実装・VM反映済み）

- **実装**（soviet_now `536739a4e`、親 `f7a080f`）:
  - `data/voicevox_sing_reference.md` に完全な楽譜JSON（歌詞付き）を5曲収録: きらきら星・ちょうちょう・メリーさんの羊・かえるのうた・ハッピーバースデー。「リクエストへの応え方」節を新設し、収録曲はそのまま使う・未収録曲は自作可（C4〜D5）・フォールバックは曲を変えるよう指示。
  - `prompts/comment_response_sing_request.md` と `prompts/comment_template.md`: リクエスト曲が収録ならその楽譜を出力、未知曲でもきらきら星固定ではなく収録曲から選ばせる規則に変更。
  - `broadcast/comment.sh` の ===SING=== なし補完（歌唱宣言あり時）を3曲（きらきら星/ちょうちょう/メリーさんの羊）から `RANDOM` 選択に変更。ログに `song=` 付き。
- **テスト**: `tests/test_comment_sing_json.sh` にフォールバック配列の複数曲・JSON妥当性検査を追加（9項目全て成功）。`test_comment_duplicate_guard.sh`、`tests/test_comment_bilingual.py` 34件も成功。`bash -n` ローカル/VM 成功。
- **実エンジン検証**: VM の VOICEVOX で5曲全部の合成に成功（WAV 343KB〜537KB、出力は `/tmp` のみでキューには未投入）。これでスコア形式が実パイプラインで通ることを実測。
- **VM反映**: `.codex_deploy/backup-20260821-sing-multisong/` に4ファイル退避後、SHA256 ローカル/VM一致（comment.sh + prompts 2件 + data/voicevox_sing_reference.md）。※reference.md を誤って `prompts/` へ scp したが削除し `data/` へ再配置済み。`soren-runtime.service` 完全再起動し、各workerとも supervisor 直下のメイン1本＋heartbeat子1本を確認（chat_worker PID 1023585、IRC daemon 起動）。
- **リポジトリ同期**: soviet_now `codex/no-apply-liveliness` へ push、docich main `f7a080f`（submodule bump。origin/main の webui コミットとローカル同内容コミットの乖離は cherry-pick で解消）、VM `/home/ubuntu/docich` を fast-forward で `f7a080f`/`536739a4e` へ同期。
- **未確認**: 実視聴者からの sing_request でのエンドツーエンド歌唱（リクエスト待ち）。歌詞の聞こえ方（「めりいさんのーの・ひつじー」等の意訳アライメント）は放送での実聴がまだ。
- **備考**: VM の webui は systemd unit ではなく plain プロセス（`python3 -m docich webui`、PID 157671、8787番ポート HTTP 200）。旧 handoff の `docich-webui.service` 表記は実態と不一致。VM docich の reset 時に未コミットの `webui.py` 差分は破棄（main 版が正）。

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

## 2026-08-21 17:2x JST — show_status_g グラフ彩色が配信に届いていなかった修正（実装・VM反映・画面実測済み）

- **ユーザー観測**: 「show status g のグラフがカラフルになったはずなのに変わって見えない」→ **観測は正しかった**。
- **原因（実測）**: 前セッション（16:2x）の彩色化は `status_dashboard.py` / `generate_status_overlay.sh` までで、`tmp/state/status_overlay.html` には確かに勾配色spanが生成されていた（再実測でも35span確認）。しかしVM配信は ffmpeg backend の **broadcast surface 経路**（`soviet_local.log` に `broadcastSidebar/Top/Bottom` のpoll記録）で、`lib/direct_broadcast_overlay.mjs` の `extractLegacyOverlayText` が `<pre>` 内から**タグごと剥ぎ取って平文化**し、サイドバー `overlays/direct_broadcast_overlay.html` が `textContent` で再描画するため、色は配信に一切届いていなかった。個別stats iframeを使うのは `useBroadcastSurface=false` のときだけで、VM `.env` は該当しない。
- **修正**（soviet_now `716190f21`）:
  - `lib/direct_broadcast_overlay.mjs` に `extractLegacyOverlayLineSegments` を新設。`<pre>` 内の色付きHTMLを許可リスト（`color:#hex` / `font-weight:700` / `opacity:.68` 形式のみ）でパースし、`{t,c,b,o}` テキストセグメント配列へ変換。未知タグ・未知スタイルは装飾なし平文として残し、マークアップはクライアントへ渡さない。`feeds.showStatusG.segments` としてstate routeに追加（ops/improveは従来どおり平文）。
  - `overlays/direct_broadcast_overlay.html` はセグメントを `createElement`+`textContent`+`style` で描画（**innerHTML不使用**。既存テストのinnerHTML禁止不変条件を維持）。セグメント未達時はtextフォールバック。
- **テスト**: `tests/test_direct_broadcast_overlay.mjs` 13件全成功（セグメント化・サニタイズ・state経路・描画スパン・フォールバックを追加）。実物 `status_overlay.html` 43行が43セグメント行へ一致することも確認。
- **VM反映**: `.codex_deploy/backup-20260821-1710-sidebar-graph-color/` 退避後2ファイルscp、SHA256一致。node bridge（soviet_local.mjs）は kill → watchdog/guardianが5秒で自動復旧（新PID 2051323、tmux soren_bridge再構築）。
- **実測検証**: state route `/__soren_overlay/broadcast/state` が41セグメント行・色付き96セグメント（#facc15/#fb923c/#f59e0b等）を返すこと、CDPスクリーンショットで**配信合成画面のサイドバーに Timeline黄オレンジ・Distribution赤→緑グラデ・Strategy comp勾配が表示されていること**を画像で確認。

## 2026-08-21 16:2x JST — show_status_g グラフの彩色化（※配信までは届いていなかった → 17:2xに修正済み）

- **実装**: `soviet_now` `4ea4b77b6` — `status_dashboard.py` の Score Timeline をスコア値に応じた赤→緑グラデーション（SCORE_GRADIENT 256色）で線分・マーカーごとに彩色（従来は単色シアン）。Strategy Comparison の中立行バーも comp 値勾配で色分け（current=緑/rollback=黄は維持）。`_render_timeline_grid` は (rows, color_rows) を返すようになり、`_colorize_plot_row` で同色ランをまとめてANSI化。可視テキスト・幅は不変。
- **オーバーレイ**: `generate_show_status_overlay.sh` のANSI→HTMLパレットに `38;5;190`(=#bef264) を追加。SCORE_GRADIENTの全コードがパレットカバー内ことを出力全体で確認。
- **VM反映**: `.codex_deploy/backup-20260821-1620-colorful-graphs/` 退避後、2ファイルscp。SHA256一致（dashboard `ceca0864…` / overlay `08ddfdc5…`）、py_compile/bash -n成功。`generate_status_overlay.sh once` で再生成し、`tmp/state/status_overlay.html` に勾配色span（#facc15/#f59e0b/#fb923c等59件）が出力されることを実測。Nodeテスト(direct broadcast overlay)11件pass。
- **テスト**: `tests.test_score_timeline` + `tests.test_status_dashboard_founding_rate` 25件、`tests.test_overlay_text` 成功。表示のみの変更のためworker再起動は不要（次回overlay生成周期から自動反映）。
- **訂正（17:2x）**: この時点の検証は中間生成物 `status_overlay.html` までで、実際の配信はbroadcast sidebarが平文で再描画するため色は視聴者に見えていなかった。修正は上の17:2x節を参照。

## 2026-08-21 16:xx JST — codex dispatch修復・fact-checkタイムアウト・improve統計の修正（実装・VM反映・実測済み）

- **修正1（主因）**: `soviet_now` `3c5e68a95` — `lib/ai_generate.sh` の `_ai_dispatch` 内 `codex|codex:*)` 空分支（`ad088ebe0`で混入）に `_ai_call_codex` 呼び出しを復元。スタブ検証（ローカル+VM）でCLI実呼び出し・出力透過・ok記録を確認。
- **修正2**: `995515ce9` — `broadcast/radio_factcheck.sh` のfact-checkタイムアウト既定値 45s→120s（実測応答33-70sに対し45sではdeepseek/minimaxがkillされ全候補失敗→原稿ごと破棄されていた）。VM `.env` には上書き設定なし、リポジトリ既定値が有効。
- **修正3**: `8fd0a2e3a` — `strategy/ai.sh` のrun_cmd統計で、expect watchdogによる結果ファイル書き込み後の意図的SIGTERM（rc=143）をログマーカー(`stopping provider after completed write`)検出でok記録に変更。REVIEW:primaryの0%表示問題を解消。
- **タイムライン確定（矛盾点の解決）**: chat_workerはメインループ毎に `eloop_lib.sh` を親プロセスで再sourceするため**08:20に即壊れた**（最終COMMENT codex winner=08:17:43）。radio_workerは反復内のサブシェルsourceのため親関数は不変で**14:22のUSR1まで旧コードで稼働**（codex winner最終=14:10:52、実セッション=14:24:59まで）。ダッシュボードの281回は各ワーカーが壊れる前の実呼び出し累計＋改善ループ（独自ハーネス・影響外）。opencode.dbに今日の有料deepseekは0件＝Codex CLI→LiteLLM→zen/go経路のため。
- **VM反映**: `.codex_deploy/backup-20260821-1600-codex-dispatch-fix/` に退避後、3ファイルをscp。SHA256一致（ai_generate `2702e4c7…` / radio_factcheck `a7c20b9e…` / strategy/ai `ca1e0b0d…`）、`bash -n` 成功。chat_workerは自動反映（毎ループ再source）、radio_workerは16:03:17にUSR1 reload完了を実測。improve_daemonはサイクル実行中のため再起動せず、次回ジョブspawn時に新ai.shを取得。
- **実トラフィック検証**: 16:06:09のCOMMENT生成で `attempt codex:minimax-m3` →44秒の実呼び出し→`ok`→**`winner`**（08:17以来初）。16:06:56に435字をキュー追加(model=codex:minimax-m3)。16:07:56の2回目も12秒でok→winner(455字)。muse一本立ち状態からcodexチェーン復活を実測。REVIEW:primaryも16:07にok記録を確認。
- **テスト**: `tests.test_ai_generate_backoff` + `tests.test_improve_retry_reliability` 36件中35件成功。1件失敗(`test_codex_backend_returns_rate_limit_code_only_for_explicit_signal`)はクリーンHEADでも失敗するmacOS環境依存(GNU timeout不在)で今回の変更と無関係。radio系シェルテスト3本成功。

## 2026-08-21 15:xx JST — WebUIチェーン統計の失敗率原因調査（診断のみ・未変更）

- **主因①（重大・実装バグ）**: `soviet_now` `ad088ebe0`（08:16 commit、VM反映 08:19:45）で `lib/ai_generate.sh` の `_ai_dispatch` 内 case 文が `'' ) return 1 ;;` → `codex|codex:*) ;;` に置き換わり、**codex系エージェントのcase分支が空**になった。codex指定はどのCLIも呼ばず、直前コマンドの stale `PIPESTATUS[0]`=0 を拾って **「ok・空出力」で即return** する（VMで stale PIPESTATUS=0 を再現実測）。`*` 分枝の `_ai_call_codex` は有効specでは到達不能の死に枝。`chat_worker.log` の `codex call` ログ0件、codex dispatchの `_output.txt` が今日1件も存在しないことで裏取り済み。
- **主因②（14:22に顕在化）**: workerは起動時にライブラリをsourceするため、08:19反映直後は旧コードがメモリ内で稼働し被害は限定的だった。**14:22:02のUSR1 reloadが `eloop_lib.sh` を再source** し壊れたdispatchがロード。以後COMMENTは365 attempt → 363 ok（ほぼ全て空）→ **winner 1件・all_failed 90件** に崩壊（実測）。15:02-15:04には同秒all_failedが約6秒周期で連発。現在も `コメント返し生成失敗` が継続中（15:13-15:14実測、muse経由の成功のみ）。codex候補は空出力=rc0のためbackoffも入らず毎周期無消費で再試行される。
- **RADIOラベル1.5%（fact-check直呼び）**: 14:22以前の実呼び出しでは `RADIO_FACT_CHECK_OPENCODE_TIMEOUT_SEC:-45` の45秒タイムアウトで有料deepseek/minimaxがほぼ毎回kill（attempt→fail 45-46秒のパターンを実測）。museは45秒内でも後段の長さ/スタイル検証で落ちて次候補へ。全候補失敗時は `radio_engine.sh:1615` で生成済み原稿ごと破棄（fact_check_failed）。
- **REVIEW:primary 0%**: 09:11の5連敗はrc=126（Argument list too long、09:15のstdin化で既修正済み）。12:48のrc=143は `EXPECT_READY ... stopping provider after completed write` → `primary OK` の**実質成功**だがstatsはrc≠0をfail記録する表示問題。
- **未実施**: `_ai_dispatch` のcodex分枝修復（`_ai_call_codex` 呼び出しへ復元）、worker完全再起動、fact-checkタイムアウト見直し、rc=143成功扱いのstats修正はすべて未着手。ユーザー確認待ち。

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
- **作業報告音声**: VMの `played.log` で既存の `work_indicator` 音声を07:36:11、07:37:13に確認。15分抑止が解けた07:52に「ラジオの先頭キューを同じレンダー世代で完成させ、再生開始まで確認しています。」を1件enqueueし、07:53:21に再生完了した。以後は追加しない。バナーは共有作業中状態のため、他作業を消さないよう最終停止時のactive状態を再確認する。

## 2026-08-21 07:54 JST — VM設定ON機能の実稼働棚卸し（調査のみ）

- **現在の常駐系**: `soren-runtime.service` はactive。Loop/ChatW/YouTubeW/AudioW/RadioW/PredW/ImproveDは各1本、重複検知stateも`status=ok`。FFmpeg direct streamは`running=true`、字幕`active=true/requested=true`。VoiceVox・LiteLLM・WebUIも稼働中。
- **設定ONだが機能単位で未稼働**: YouTube Chatはworker自体は動くが`last_poll=-`、`activeLiveChatId`解決失敗の900秒backoff、OAuth refresh `invalid_grant`。Twitch Chatは受信/IRC接続は動く一方、送信はAPI/IRCとも`Invalid OAuth token`/`Login authentication failed`。Twitch Adsは`TWITCH_ADS_ENABLED=1`だが直近API GETがHTTP401で、成功snooze実績なし。改善daemonはaliveだがstateが`failed_no_apply:rate_limited`、primary利用上限でfallbackなし。
- **バッチ解説**: `BATCH_COMMENTARY_ENABLED=1`・閾値48。現在の蓄積は38/48で、今は閾値待ち。直前の48試合バッチは`RADIO:batch_commentary`の`__invalid_agent__`失敗記録のみで、done/explanation/audio投入を確認できないため未達（現チェーン設定はnon-empty）。
- **意図的に動いていない設定**: `GAME_RESULT_CHAT_ENABLED=0`（試合ごとの自動投稿停止）、`SOREN91_ENABLED=0`、`RADIO_BRIEFING_ENABLED=0`、旧peak-hour defer/改善後parallel系の0設定。OBS unit inactiveも、現在の`SOREN_STREAM_BACKEND=ffmpeg`では想定どおりで、配信と字幕はFFmpeg側で稼働。
- **動作中だが要観測**: Twitch Predictions APIはHTTP200で予想作成済み、現在remote statusは`LOCKED`。30分の受付終了後で、サイクル38/48のため解決待ちと判定（自動投票は07:09に63pt成功）。Radio worker/Audio workerは動作し、生成も再生も進むがdeferred queueは直近確認で42件、古い項目が滞留。
- **表示履歴の注意**: `viewer_chat_monitor.json` の最新履歴には旧形式の`[36/100]`メッセージが残っていたが、現行のoverlay・蓄積state・予想タイトルは48基準。これは過去チャット履歴であり、現行カウンターの値ではない。
- **一時障害**: 07:38:07にruntime全体のTERM停止、07:39:21にsystemdが自動起動。現在は復帰しているが、停止契機はjournal上で特定できていない。今回の調査では再起動操作は行っていない。

## 2026-08-21 07:54 JST — Score Timelineのブラウザ表示をスパークラインへ再修正・VM反映

- **再診断**: 連続線へ修正した多段ドットグリッドでも、direct broadcast overlayの極小モノスペース表示では各点が密なマス目に見えた。入力の`score_history.txt`と上下限（実データの2825/396）は正常だった。
- **実装**: `status_dashboard.py:render_score_timeline()` を、直近100件を幅42区間へ平均化するASCII一行スパークライン（`.:-=+*#@`）へ変更。Braille依存を削除し、上限・下限ラベルと基準線を維持。`chart_h`引数は後方互換のため受け付ける。CLAUDE.mdの説明も更新。
- **リポジトリ**: `soviet_now` `3000426f2`（表示実装本体 `cbdbf0fc9` を含む）を`origin/codex/no-apply-liveliness`へpush。親リポジトリのサブモジュールポインタ更新は次のコミットで行う。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-score-timeline-sparkline/status_dashboard.py` に反映前ファイルをバックアップ後、VMへ反映。ローカル/VM SHA256 `9396a2adad61762a813fe74d586b486656eb81089954749fc966b829297dfa41` 一致、`py_compile`成功。`generate_status_overlay.sh once` の実データ出力は次の3行になり、Braille文字数0を確認。
  `Score Timeline (last 100 games)`
  `2825│:==:--:-:==*-+=:=+-:-:=:==+==--::=-:-+--=-`
  `396└──────────────────────────────────────────`
- **テスト**: `tests.test_score_timeline`、`tests.test_status_dashboard_founding_rate`の25件、direct broadcast overlay Node 11件、dashboard関連escape 3件、`py_compile`、`git diff --check`が成功。
- **作業報告音声**: 今回専用の「score timelineのブラウザ表示がまだ崩れる原因を確認し、点字グリフを使わない表示へ修正しています。」をVMへ重複抑止付きでenqueue。VM `tmp/state/overlay_events.jsonl` の07:52:35 `コメント返信 playback`イベントで再生を確認した。ラジオレンダー負荷のため投入直後は待機していたが、キュー残存なしになった。

## 2026-08-21 08:10 JST — Score Timelineの時間軸・上下スケールを復元・VM反映

- **再評価**: 一行の強度文字列は左から右の傾向しか示さず、上下のスコア位置と時間方向が読み取りにくかったため、タイムラインとして不十分と判断した。
- **実装**: `render_score_timeline()` を7段のブラウザ対応テキスト折れ線へ変更。直近100件を横幅42区間へ平均化し、`2825`/`396`の上下ラベル、`old -> now`の見出し、横軸、`old`/`now`目盛りを表示する。Brailleは使わず、`*`, `/`, `\\`, `-`と軸文字で描画する。
- **リポジトリ**: `soviet_now` `5ea608039`（実装 `d791e5494`を含む）を`origin/codex/no-apply-liveliness`へpush。親リポジトリのサブモジュールポインタ更新は次のコミットで行う。
- **VM反映**: `/home/ubuntu/soren/.codex_deploy/backup-20260821-score-timeline-axes/` および説明更新用バックアップへ反映前ファイルを保存。最終ローカル/VM SHA256 `03ca1be2b76c182308a7bc42fd1324114e53fc2f25e4945efc1e5b6b60bc7a42` 一致、`py_compile`成功。実データで7段折れ線、上下ラベル、`old`/`now`、軸線、Braille文字数0を確認した。worker再起動は不要な表示変更のみ。
- **テスト**: `tests.test_score_timeline`、`tests.test_status_dashboard_founding_rate`の25件、direct broadcast overlay Node 11件、dashboard関連escape 3件、`py_compile`、`git diff --check`が成功。

## 2026-08-21 08:30 JST — 有料DeepSeek残存経路の再照合（変更なし）

- **実測された新規到達**: VMの`resolved_model=deepseek-v4-flash`は07:11:12 JSTの`RADIO` `attempt` 1件のみで、07:11:57に`fail`。直前にmuseの`RADIO`がprovider `ok`になっているため、fact-checkの意味検証を通らず`RADIO_FACT_CHECK_SECONDARY`へ進んだ候補と一致する。`winner`はない。
- **実効チェーンの上書き**: 起動経路で読み込むVM `.env`（06:23:47更新）が、`AI_COMMON_AGENTS`/`RADIO_AGENTS`/`RADIO_PREPASS_AGENTS`を`AMD → deepseek-free → openrouter/free → muse → local → paid DeepSeek → MiniMax`、`MODEL_IMPROVE_LIST`を`AMD → deepseek-free → muse → paid DeepSeek → MiniMax`に固定している。リポジトリ側`core/config.sh`のmuse先頭既定値は、VM `.env`があるため実効値になっていない。
- **補助経路**: `RADIO_FACT_CHECK_ENABLED=1`でmuse→有料DeepSeek→MiniMaxの二次候補が稼働中。Soren91、celebration、AI classifier、bug dispatchは各トグル0。Stats外の改善`run_cmd`ログも反映後の有料DeepSeek開始はなく、最後の`codex:deepseek-v4-flash`は05:34台（反映前）で、現在のcodex子プロセスはAMD版。未知specを`CODEX_MODEL`（有料DeepSeek）へ正規化する後方互換はコード上残るが、反映後の`__invalid_agent__`再発はない。OpenCodeのセッションDBは非無料`deepseek-v4-flash`が0件で、直接`opencode run --agent minimax`は有料版経路ではない。
- **状態**: 今回はコード・VM設定・workerを変更していない。修正する場合は、VM `.env`のチェーン順とfact-check二次候補を別々に判断し、`.env`変更後はworker完全再起動とStats再確認が必要。

## 2026-08-21 08:34 JST — YouTube保留・残りのVM機能修正と再実測

- **YouTube**: relay serverが必要なため、今回の対応対象から外した。workerは停止せず、OAuth `invalid_grant`／900秒backoffが残る未達として扱う。
- **バッチ解説**: `lib/ai_generate.sh` にagent仕様検証を追加し、未知の値をCodex既定モデルへ黙って流さないよう変更。`batch_commentary.sh` は無効候補を除去し、全候補が無効なら `RADIO_AGENTS` → `AI_COMMON_AGENTS` の順に有効チェーンへフォールバックする。`__invalid_agent__` の再発を防ぐテストを追加した。soviet_now `ad088ebe0`。
- **改善再開**: VM実測で旧 `strategy/ai.sh` がLiteLLM `/health`を確認していたため、実際の生存確認先 `/health/liveliness` を反映（ローカル/VM SHA256一致、`bash -n`、HTTP 200）。さらに、失敗状態だけ残ってロックが消えた場合に保存済み同一hashバッチを復元する `strategy/improve.sh` 修正を追加し、soviet_now `28a4f8670` としてpush・VM反映した。VMでは`count=95`のretry batchが`tmp/improve.lock`へ復元され、`improve_state=running / phase=analyze_retry1`へ遷移した。分析中の`improve_ai.log`に新しい`LITELLM_DOWN`はない。
- **予想**: 既存の`48ゲーム中に建国できる？`はHTTP 200／LOCKEDのまま。現時点の蓄積は46/48で、分析中は解決条件未到達のため、作成・解決コードの追加変更は行わず次の2試合後に実測する。
- **Twitchチャット／広告**: 送信・広告APIは依然としてInvalid OAuth token／HTTP401。トークン本文は取得・保存せず、ユーザー側の有効な権限付きトークン更新が必要な未達として残す。
- **検証**: `tests.test_ai_generate_backoff` と `tests.test_improve_retry_reliability` 合計35件、`bash -n`、`git diff --check` 成功。VM反映前バックアップは `.codex_deploy/backup-20260821-0816-ai-chain-runtime` と `.codex_deploy/backup-20260821-0822-improve-retry-restore`。

## 2026-08-21 09:22 JST — YouTube保留・バッチ解説/改善/予想の実パス完了

- **YouTube**: リレーサーバーが必要なため今回も保留。workerは停止せず、OAuth `invalid_grant` と900秒backoffが残る未達として扱う。
- **バッチ解説**: 完全な48試合単位へ修正し、48〜95試合は同じバッチIDを使う。作業メモ風の先頭行を除去するガードも追加した。VMで `99bace571ca533a545dcd2e3`（48試合）のdone/explanationを生成し、平均1362.9、最高4354、ロシア1/48、ソ連0/48を含む解説本文を確認。`comment_announce_1787270675328069271_batch_commentary.playing` が09:06:14に再生済み。旧仕様で作られた49試合doneは履歴として残るが、今後のトリガーは完全バッチ単位。
- **改善AI**: LiteLLM生存確認を`/health/liveliness`へ修正し、失敗状態で失われた同一hash retry lockを復元する経路を追加。今回の改善はVMへ適用され、`strategy.py`のdecide hashはVM/ローカルとも`1a99aa3e5244`、`accumulated_games.json`は新サイクルの2試合、`improve_state=idle`となった。type14ではロシアフェーズを発火させず、type15のみで発火する差分とT14 merge priorityの再キーイングを実際に同期した（soviet_now `3d2fb70c6`）。
- **レビュー引数長対策**: 旧実行ではStage 3のCodex/opencode候補が`Argument list too long`で全滅したが、runtime smoke後の適用は完了した。`strategy/ai.sh`をCodex/opencodeともプロンプトargv渡しから標準入力へ変更し、次回レビューで同じ失敗を避ける。VM反映前バックアップは`.codex_deploy/backup-20260821-0915-ai-stdin`。
- **予想**: 直前の「48ゲーム中に建国できる？」は、 outcome「ロシア建国(ソ連不成立)」でRESOLVED。改善後に新しい同名予想（`prediction_window=1800`）が自動作成され、09:20 JST時点でACTIVE、蓄積2/48。自動作成・解決機構の再開を実測した。
- **残る外部認証ブロッカー**: Twitchチャット送信と広告APIはInvalid OAuth/HTTP401のまま。トークン本文は取得・保存しておらず、ユーザー側で適切な権限付きトークンを更新するまで未達。YouTubeも同様に今回の対象外。
- **検証**: AI/改善回帰36件、stat-gate/continuous改善6件、dashboard回帰25件、`bash -n`、`py_compile`、`git diff --check`が成功。VM `soren-runtime.service=active`、LiteLLM liveliness=`I'm alive!`を確認。

## 2026-08-21 — TwiCa #1114 presence estimate / preview PR #1115

- **リポジトリ**: `/private/tmp/twica-1114-live-presence` の `codex/issue-1114-live-presence` で実装。最終HEADは `6c4d3abde7e1b097424a887db8e79de8e7aa2024`、[preview PR #1115](https://github.com/azumag/twica/pull/1115) は未マージ。
- **実装**: overlay接続済みroomの短期leaseを匿名集約し、`/live` に5件単位で切り下げた「overlay接続中チャネルの推定下限」を表示。認証済み設定画面からHMAC capabilityを発行し、socketごとに認可。30日更新tokenは配信者単位のlocalStorageへ保存し、明示的な `presence` 付きURLだけで復元。設定画面内のiframe/demoはtokenlessのまま。presence Workerはsingle-flight、60秒成功キャッシュ、15秒負キャッシュ、短期lease掃除を持ち、polling-only・preview・切断遅延・残留タブをUI注記。
- **検証**: focused 4 files/99 tests、unit 285 files/3722 tests、integration 13 tests、overlay Worker typecheck、`tsc --noEmit`、対象eslint、`git diff --check`、application/auxiliary Workers build が成功。GitHub CI run `32437475806` と Claude Auto Review `32437475877` が最終HEADで成功。Workers Builds のpreview反映も成功（commit preview URLはPRコメントの記録を参照）。
- **実パスの境界**: 匿名 `/live` はpreviewで表示まで確認。dashboardはTwitchログインへリダイレクトされ、認証済みoverlay接続→presence→`/live` 更新の正経路は未確認。資格情報を取得・保存しておらず、channel point redemptionや本番反映は実施していない。

## 2026-08-21 12:15 JST — Twitch広告スヌーズのepochパース不具合修正・VM反映

- **原因（本番実測）**: `lib/twitch_ads.sh:154` の `next_ad_at` パースが `RFC3339 datetime.fromisoformat` のみで、Twitch `GET /helix/channels/ads` が返す epoch 整数 `1787283212` を `Invalid isoformat` として `next_sec=0` にしていた。`twitch_ads_maybe_snooze` は `case 0 return 0` で `snooze` に到達せず、`snooze_count=3` でも `diff` 計算が常に失敗していた。`04:43 401` 時代は backoff で隠れていたが、現在の `zd7y...` トークンで `HTTP 200` になった後も `diff 1194` が閾値 `600` 外でスヌーズ待ちの間に再現した。
- **修正**: `soviet_now` `173a72cea fix: parse Twitch ads next_ad_at as epoch or RFC3339` で epoch `1e9-4e9` を先に整数として解釈し、失敗時のみ RFC3339 へフォールバックする。`bash -n`、epoch/RFC3339/範囲外/小数のローカルパーステスト、閾値内・閾値外・count 0・dedup の `maybe_snooze` スタブテストが成功。
- **VM反映**: `.codex_deploy/backup-20260821-ads-parse-fix/` に退避後、`lib/twitch_ads.sh` を `08812946...` でローカル/VM SHA256一致、`bash -n` 成功を確認。手動 `VM _twitch_ads_get_status` は `snooze_count=3 next_ad_at=1787283212` で `HTTP 200`、修正後パーサーで `next_sec=1787283212 diff=1194 PARSE_OK` を実測。`TWITCH_SNOOZE_THRESHOLD_SEC=2000` で拡大したスタブでは `STUB_SNOOZE_WOULD_BE_CALLED` と `dedup` 書き込みを確認し、テスト用の `last_next_ad_at` と追加ログを削除してクリーンに戻した（`tmp/debug/twitch_ads.log` は `04:43 401` の1行のみに復元、backoff は期限切れで削除済み）。現在の `next_ad 12:33 JST` まで `diff 1194>600` のため実 `POST /snooze` は正しく未発火で、約10分後に閾値内へ入った次の `speaking` で実 POST が検証される。
- **未確認**: 閾値内での実 Twitch `POST /helix/channels/ads/schedule/snooze` の `200/204` 成功と `snooze_count 3→2`、`tmp/state/last_next_ad_at`/`last_snooze_at` の本番記録は、次回の閾値内 `speaking` 時点で観測する。

## 2026-08-21 12:40 JST — 設定済みだが未稼働の機能棚卸しとWARN対応（実装・VM反映済み）

- **棚卸し結果（YouTube 제외）**: `soren_loop.log` の `OBS dashboard show failed` 2-5分毎、`direct_stream.log` の `Twitch offline streak / Connection refused rtmp://127.0.0.1:1935`、`improve_daemon.log` の `144282B → rc126`、`*wildcard_parallel* infra_failed ERR_MODULE_NOT_FOUND external_game_audio.mjs`、`tmp/prediction.log` の `WARN: prediction create failed` バースト、`twitch_chat_daemon.sh:431 printf: write error: Broken pipe` を全て `ps/pgrep/ss/curl` と `logs/**/journalctl` で実測。Twitch Ads は epochパース修正で `GET200 snooze_count 3` へ復帰済み。
- **OBS**: `eloop.sh:284,655` が `SOREN_STREAM_BACKEND=ffmpeg` でも無条件で `obs_control.sh show/hide` し、`ws://127.0.0.1:4455` Listenなしで `WARN`。`eloop.sh` に `STREAM_BACKEND==obs` ガード、`obs_control.sh:67` 先頭で `STREAM_BACKEND!=obs → exit 0` を追加。`bash -n`、`SOREN_STREAM_BACKEND=ffmpeg` で `show` が `exit 0` になることを実測。次ゲームで `grep -c dashboard` が増加しないことを確認する。
- **Predictions**: `twitch_predictions.sh:636` の `POST 400` が `Prediction already active` の場合、従来は `WARN+backoff`。同一チャンネルにACTIVE/LOCKEDがあるのは正常競合なので `grep -qi already` で検出し `INFO: prediction already active, syncing` に降格し `_recover_remote_active_prediction` でローカルを復元して `exit 0`。`bash -n` と `already` 検出スタブで確認。
- **Direct Stream**: `lib/direct_stream.py:447` の `validate_local_relay` はソケット失敗時に即 `RuntimeCheckError`。`nginx master /etc/soren-rtmp/nginx.conf` が `systemctl inactive` でもListen中なら正常扱いするよう `pgrep -f "nginx: master.*soren-rtmp"` フォールバックを追加。`_reload_relay:598` は `systemctl reload` → `nginx -s reload` → `sudo nginx` の順にフォールバック。`SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD=3` を `.env` に追加（既定2→3で誤検知抑制）。`py_compile`、手動 `validate ok` と `pgrep` を実測。
- **Wildcard**: `wildcard_parallel.py:1884 prepare_candidate_dir` が `soviet_local.mjs` の `import './external_game_audio.mjs'` や `lib/*.mjs` をスロットへコピーしていなかったため `infra_failed`。`external_game_audio.mjs`/`browser_frame_limiter.mjs` と `lib/` 全体をコピーするように修正。`prepare_candidate_dir` の一時ディレクトリで `external_game_audio.mjs` と `lib/chrome_launch_lock.mjs` が存在することを `python` で実測。
- **Chat**: `twitch_chat_daemon.sh:431` の `printf | md5 -q` は Linux で `md5 -q` がなく `SIGPIPE`。`md5sum | awk` を先に試す順序へ入れ替え、Broken pipe を抑止。`bash -n` 成功。
- **リポジトリ**: `soviet_now` `617428acf` (OBS/Predictions/Direct) + `b43e37e94` (Wildcard) + `862170af3` (Chat) を `origin/codex/no-apply-liveliness` へ push。
- **VM反映**: `.codex_deploy/backup-20260821-obs-predict-direct/` と `backup-20260821-wildcard-chat` に退避後、4ファイルのSHA256一致（`eloop.sh dbc3d8de…` `obs_control.sh 0eafea…` `twitch_predictions.sh 33383c…` `direct_stream.py 1f4295…` `wildcard_parallel.py fdd9a9de…` `twitch_chat_daemon.sh a8c599…`）、`bash -n`/`py_compile`、手動 `SOREN_STREAM_BACKEND=ffmpeg` の `obs_control` スキップ、`prediction already` 検出、`validate ok`、wildcard `prepare` 成功を実測。`SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD=3` は `.env` で `python load_reconnect_config` が `3` になることを `set -a; . .env` で実測。
- **未確認**: 次ゲームでの `OBS dashboard show failed` ゼロ、`direct_stream` の `Twitch offline streak 3` 閾値での誤再起動抑制、次改善での `REVIEW 144282B rc=0`、次Wildcard実行での `infra_failed` 解消、長時間運用での `prediction already active` の INFO化と `Broken pipe` ゼロは、次回サイクルで継続観測する。

## 2026-08-21 15:20 JST — OpenCode フリー枠 Ox Alpha Free をローカル・VM の使用可能モデルへ追加

- **ローカル**: `~/.config/opencode/opencode.json` の `provider.opencode.models` に `x-preview-f-free`（Ox Alpha Free、context 1M / output 131072、入力 text/image/video）を明示追加。`bunx opencode-ai models` で `opencode/x-preview-f-free` 表示と他 opencode/ モデル7件が制限されず残ることを実測。
- **VM**: `ubuntu@129.146.54.105` の `~/.config/opencode/opencode.jsonc` へ同じ定義を追加（バックアップ `opencode.jsonc.bak-20260821-ox-alpha`）。反映前から snap 版 opencode 1.18.18 のカタログに同モデルは表示されていたが、明示定義でローカルと状態を揃えた。反映後も opencode/ モデル7件を確認。
- **実呼び出し検証**: VM で `opencode run -m opencode/x-preview-f-free 'Reply with exactly: OX_ALPHA_VM_OK'` が `OX_ALPHA_VM_OK` を返すことを実測（無料モデルのため課金なし）。
- **リポジトリ同期**: VM の `~/.config/opencode/` は soren/docich いずれのリポジトリ外のため、今回コミット・push 対象なし。soren 側のチェーン設定（`.env` の AGENTS 系）は変更していない。

## 2026-08-21 15:35 JST — Ox Alpha Free を既存モデルチェーンへ組み込み（muse 直前）・VM反映

- **変更**: VM `/home/ubuntu/soren/.env`（バックアップ `.env.bak-20260821-ox-alpha-chain`）の `AI_COMMON_AGENTS` / `MODEL_IMPROVE_LIST` / `RADIO_AGENTS` / `RADIO_PREPASS_AGENTS` / `COMMENT_AGENTS` の5チェーンで、`opencode-go:muse-spark-1.2-contributor` の直前に `opencode:x-preview-f-free` を挿入。実効順は例えば RADIO 系が `amd → openrouter/free → ox-alpha → muse → local → paid DeepSeek → MiniMax`。fact-check チェーン（repo 既定の muse 先行 + `.env` の FALLBACK=minimax）は今回の対象外。
- **コード適合の確認**: `lib/ai_generate.sh:_ai_agent_spec_valid` は `opencode:<model>` を許容し、`_ai_call_opencode_unqueued` は `opencode:x-preview-f-free` → `opencode/x-preview-f-free` へ解決して CLI 直接呼出（LiteLLM ゲート迂回）。`strategy/ai.sh:_run_cmd_resolved_model` も同解決。VM の `lib/ai_generate.sh` / `strategy/ai.sh` はローカルと SHA256 一致。
- **反映**: `soren-runtime.service` を完全 stop/start。改善 daemon は idle だったため安全。再起動後、supervisor 直下に soren_loop/improve/chat/youtube/audio/radio/prediction の各メインPID 1本を確認。
- **実測検証**: (1) 新 worker の `/proc/PID/environ` に新チェーンを確認。(2) VM で `_run_cmd_resolved_model opencode:x-preview-f-free` → `opencode/x-preview-f-free`。(3) `_ai_dispatch OXTEST opencode:x-preview-f-free` の実呼び出しで stdout `OX_ALPHA_CHAIN_OK` rc=0（無料モデルのため課金なし）。(4) `tmp/state/ai_stats/20260821.jsonl` に `OXTEST` の attempt+ok、`resolved_model=opencode/x-preview-f-free` を記録確認。
- **未確認**: 実運用の放送・コメント・改善ループで amd/openrouter 失敗後に ox-alpha が獲得する様子は次回以降の自然発生待ち（Stats の `RADIO`/改善ラベルで継続観測）。

## 2026-08-21 15:50 JST — ピーク時間帯も ox-alpha が muse より先になるよう PEAK_HOURS_AGENT_PREFERENCE へ追加

- **ユーザー観測の解明（実測）**: 15:33 JST の COMMENT で muse が winner になったのは、ピーク時間帯（`PEAK_HOURS_WINDOWS=10-13,15-19` JST）に `_peak_priority_agent_list`（`core/helpers.sh:163`）が `PEAK_HOURS_AGENT_PREFERENCE`（旧値 `minimax,openrouter/free,muse,local`）順へ候補を並べ替えるため。15:33 の実際の試行順は `minimax → openrouter/free → muse → amd → ox-alpha → paid DeepSeek`（`logs/chat_worker.log` の `コメント返し生成中... agents=` 行で実測）で、muse が先に winner になり ox-alpha は未到達。基本チェーン自体の挿入は正しく機能していた。
- **変更**: VM `.env`（バックアップ `.env.bak-20260821-ox-alpha-peakpref`）の `PEAK_HOURS_AGENT_PREFERENCE` を `codex:minimax-m3,codex:openrouter/free,opencode:x-preview-f-free,opencode-go:muse-spark-1.2-contributor,local` へ変更し、`soren-runtime.service` を完全再起動（improve idle 確認済み）。再起動後 supervisor 直下に全7 worker 各1本。
- **実測検証**: 新 worker の environ に新 preference を確認。`.env` を source した上で `_peak_priority_agent_list` を実行し、ピーク時の実効順を実測: COMMENT/RADIO とも `minimax → openrouter/free → ox-alpha → muse → ...`、IMPROVE は入替対象外で `amd → ox-alpha → muse → ...`。全経路で ox-alpha が muse より先。
- **教訓**: `_peak_priority_agent_list` 単体テスト時は必ず先に `.env` を source する（省くと repo 既定チェーンで並べ替わり、見かけ上別リストになる。本検証で一度やり直した）。
- **未確認**: 次回の自然発生 dispatch で `agents=` 行に ox-alpha が muse より先に出ること、ox-alpha が winner を取ること。

## 2026-08-21 16:05 JST — 残りの muse 先行経路（fact-check / improve peak / 翻訳）にも ox-alpha を挿入

- **背景**: ユーザー指示「Ox alphaをmuseの前に持ってきて」。棚卸しで muse が ox-alpha より先に残っていた経路は3つ: (1) fact-check（repo 既定 `RADIO_FACT_CHECK_AGENT=muse`、`.env` 未設定）、(2) `MODEL_IMPROVE_PEAK_LIST`（ピーク時の改善チェーン、muse 先頭。`IMPROVE_PEAK_CHAIN_ENABLED=1` で有効）、(3) `COMMENT_TRANSLATION_AGENTS`（`.env` 明示値で ox-alpha なし）。
- **変更**: VM `.env`（バックアップ `.env.bak-20260821-ox-alpha-fullchain`）へ (1) `RADIO_FACT_CHECK_AGENT=opencode:x-preview-f-free` + `RADIO_FACT_CHECK_SECONDARY=opencode-go:muse-spark-1.2-contributor` を追加（FALLBACK=minimax は既存のまま、実効順 ox-alpha → muse → minimax）、(2) `MODEL_IMPROVE_PEAK_LIST` の muse 直前に ox-alpha を挿入、(3) `COMMENT_TRANSLATION_AGENTS` の muse 直前に ox-alpha を挿入。`radio_factcheck.sh:220` は AGENT → SECONDARY → FALLBACK → TERTIARY の順で1件ずつ試行を確認済み。
- **反映**: `soren-runtime.service` 完全再起動（improve idle 確認済み）。再起動後 supervisor `1118487` 直下に全7 worker 各1本。
- **実測検証**: 新 worker（radio/chat）の environ に4変数すべての新値を確認。これで全経路（基本チェーン・ピーク入替・改善通常/ピーク・翻訳・fact-check）で ox-alpha が muse より先に試行される。
- **未確認**: 自然発生の fact-check / 改善ピーク / 翻訳で ox-alpha が実際に獲得するかは継続観測（Stats の `resolved_model=opencode/x-preview-f-free` と `logs/radio_worker.log` の `fact-check中... (opencode:x-preview-f-free)` で判別可能）。

## 2026-08-21 16:20 JST — フリー枠 Muse Spark 1.2 Free を ox-alpha 直後に追加

- **経過**: 16:0x の実運用で `REVIEW:primary` が `opencode/x-preview-f-free` を ok 記録（改善ループで実獲得を確認）。一方 COMMENT はピーク入替の第1候補 minimax が winner となり ox-alpha 未到達（現行設計どおり。ox-alpha を最前面へ上げるかはユーザーに確認したが回答なし）。
- **ユーザー指示**: 「Ox freeと同様に、おなじフリー枠にmuseもあるようなので、oxの後ろにフリーのmuse設定したい」。
- **事前実測**: VM カタログに `opencode/muse-spark-1.2-contributor-free` を確認。`_run_cmd_resolved_model opencode:muse-spark-1.2-contributor-free` → `opencode/muse-spark-1.2-contributor-free`、`_ai_dispatch OXTEST2` 実呼び出しで `MUSE_FREE_OK` rc=0（無料）。
- **変更**: VM `.env`（バックアップ `.env.bak-20260821-musefree`）。8つのリスト変数（AI_COMMON_AGENTS / MODEL_IMPROVE_LIST / RADIO_AGENTS / RADIO_PREPASS_AGENTS / COMMENT_AGENTS / COMMENT_TRANSLATION_AGENTS / MODEL_IMPROVE_PEAK_LIST / PEAK_HOURS_AGENT_PREFERENCE）の `opencode:x-preview-f-free` 直後に `opencode:muse-spark-1.2-contributor-free` を挿入。fact-check は単値4段を再編: AGENT=ox-alpha → SECONDARY=muse-free → FALLBACK=muse-go → TERTIARY=minimax。
- **反映**: `soren-runtime.service` 完全再起動（improve idle 確認済み）、supervisor `1419423` 直下に全7 worker 各1本。新 radio_worker の environ に新チェーン・新 fact-check 変数を実測。
- **実効順の例**: RADIO ピーク時 `minimax → openrouter/free → ox-alpha → muse-free → muse-go → local → ...`、改善ピーク `ox-alpha → muse-free → muse-go → minimax → ...`、fact-check `ox-alpha → muse-free → muse-go → minimax`。
- **注意**: `RADIO_FACT_CHECK_AGENT` は単値変数のためリスト一括 sed の対象から除外済み（誤ってカンマ入り1specにすると無効specになる）。`AI_BACKOFF_SEC_ITEMS` の backoff キーは `muse-spark-1.2-contributor:86400` で、free 版 id `-free` は別キー（既定 backoff 適用）。
- **未確認**: 自然発生 dispatch での muse-free 獲得は継続観測（Stats `resolved_model=opencode/muse-spark-1.2-contributor-free`）。
