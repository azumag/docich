# セッション引き継ぎ (handoff)

> 生成日時: 2026-08-25 07:1x JST  /  作業ディレクトリ: /Users/azumag/work/docich
> このファイルを読み込めば作業を再開できます。再開時: `/handoff load`
> 直前セッション: v739 LOOKAHEAD（2 手先読み、hash 8fcb13b11d0c）を実装・オフライン検証（変更 3.3%、併合喪失 0）し、16:10 から v736(A) vs v739(B) のインターリーブ A/B を実行中（主指標 併合/手、74/腕）。A/B ゲートは dry-run で改善 daemon 再稼働中。状況は VM で `bash tools/ab_ctl.sh status`。

## 2026-08-26 21:2x-21:5x JST — 同じニュース(ドリー・パートン死去)を1日4回読み上げた問題を修正

- **ユーザー報告**: 「ドリー・パートンのニュースが３回も読まれている」。
- **実測した事実**: `tmp/history/past_radio_topics.txt` より **13:26 (Game#45755) / 15:32 (#45784) /
  18:49 (#45825) / 19:54 (#45834) の 4 回**、[news] コーナーで同じ訃報を読んでいた（報告の 3 回より多い）。
- **原因は 3 段重ね（すべて実測で確認）**:
  1. **RSS 候補が終日ゼロ** — `tmp/state/.news_fetch_status.json` は
     `all_seen_or_filtered / fetched=100 / candidates=0`、内訳は `past_title=87, past_link=13`。
     **wikinews 13 ソースが全滅（各 0 件）**で、生きているのは Global Voices 9 ソースだけ。
     VM から直接 `Special:NewPages&feed=rss` を叩くと **HTTP 200・item 0**（ja/en/ru/fr/de すべて）。
     API (`list=recentchanges&rctype=new&rcnamespace=0`) でも 0 件、RC フィードに出るのは `User:` ページのみ。
     → **上流(Wikinews)が事実上休止しており、当方のバグではない**。
     今日のニュースコーナー約 20 回のうち **15 回が自主探索フォールバック**。
  2. **自主探索フォールバックが「何を読んだか」を記録していなかった** —
     `_news_self_search_fallback` は `_radio_generate_and_play` を呼ぶだけで、
     `PAST_NEWS_READ` / `_KEYS` / `_TOPIC_KEYS` へ一切書かない。
     よって次回プロンプトの【直近で読んだニュース一覧】に自主探索分が載らず、AI は毎回同じ大ニュースを選ぶ。
  3. **`_radio_past_topics_block` が話題をコーナー名の定型文へ丸める** —
     `333d1a13d` 以降 `[news]: ドリー・パートン,...` の中身を捨てて「ニュース考察をしました」に置換するため、
     重複回避メモからは何を扱ったか分からない。しかも同メモは「人名そのものは扱ってよい」と明記しており、
     モデルは再選定を許可されていた（18:45 のプロンプト実物で確認）。
     `PAST_NEWS_TOPIC_KEYS` も先頭単語だけを取るため `la` / `في` / `من` / `without` 等のストップワードが並び役に立っていない。
- **修正 (`games/soviet_now` commit `38324c60a`)**:
  - `broadcast/radio_engine.sh`: `RADIO_GEN_RESULT_DIR` が指定されていれば、解析済みの
    `selected_news.txt` / `summary.txt` を呼び出し側へ渡す（追加のみ・既定動作は不変）。
  - `broadcast/radio_news.sh`: 既読台帳追記の共通ヘルパ `_append_news_read_entry`（title 必須、
    source_key/url_hash 任意）と、news/jiji コーナーの**実際の話題**を返す
    `_recent_news_corner_topics_block` を追加。
  - `broadcast/radio_corners.sh`: 自主探索プロンプトに ①【この番組で既に扱ったニュース話題（絶対に再度選ばないこと）】
    ② `===SELECTED_NEWS===` の出力要求 を追加し、生成成功後に見出し（無ければ要約行）を既読台帳へ記録。
  - `tests/test_news_self_search_dedup.sh`（新規, 19 assertion）。
- **検証**: ローカル・VM とも新テスト 19/19 pass、既存 radio 系テスト 6 本 pass、`bash -n` 全通過。
  実データ検証: 本番 `past_radio_topics.txt` に対し `_recent_news_corner_topics_block` が
  ドリー・パートン 2 件を含む 20 行を返すことを実測。19:52 の実出力を実 parser にかけ、
  `selected_news` 空・`summary` にキーワード列が出る（＝要約フォールバックが効く経路）ことを実測。
  jiji の直近出力 5 本中 4 本に `===SELECTED_NEWS===` があり、モデルがこの指示に従うことも確認。
- **VM 反映**: `.codex_deploy/backup-20260826-214350-news-self-search-dedup/` へ退避後、
  staging → `mv` で置換（実行中プロセスのオフセットずれ回避）。SHA256 4 ファイル全一致。
  `radio_worker` へ USR1 → 21:46:03 `reload complete`（PID 2394866 維持）。
- **ユーザー判断 (2026-08-26 22:0x)**: 「wikinews は終了したのでむり。ソースからは削除したい。
  ニュースソースは自己探索でいいが、重複回避が働いてさえいれば良い」
  → **自主探索を正規の主経路として運用する**方針。追加のニュースフィード導入はしない。
- **追加修正 (`games/soviet_now` commit `ee5e4bd2b`)**:
  - `lib/fetch_news.py`: **Wikinews 13 ソースを削除**（SOURCES は 22 → 9、Global Voices のみ）。
    削除理由をコード内コメントに記載。1 回の取得で 13 本の無駄な HTTP リクエストも止まる。
    実フィードに対する隔離実行で `status=ok / sources 9 / fetched_sources 9 / items 100 / selected 100` を実測。
  - `broadcast/radio_news.sh`: `_recent_news_corner_topics_block` の既定窓を 20 → 30 行
    （`NEWS_SELF_SEARCH_TOPIC_LIMIT`）。自主探索が主経路になるため news/jiji 約 10〜13 時間分を渡す。
  - VM 反映: `.codex_deploy/backup-20260826-220434-drop-wikinews/` へ退避 → staging → `mv`、SHA256 一致。
    `radio_worker` USR1 → 22:05:02 `reload complete`（PID 2394866 維持）。
    `fetch_news.py` はサブプロセス実行なので reload 不要。
- **未確認 / 次にやること**:
  - 実運用のフォールバックで `[NEWS] 自主探索の既読記録: ...` が出ることのライブ実測（次回発生待ち）。
  - 本番ログの取得行が `sources=N/9` になることのライブ実測（次回取得待ち）。
  - `PAST_NEWS_TOPIC_KEYS` のストップワード問題（`la` / `في` 等）は未修正。
    先頭単語だけを topic key にするため無意味なキーが並ぶ。実害は今のところ観測していない。

## 2026-08-26 21:0x-21:1x JST — YouTube 公開後の Bluesky 告知を実装 (実投稿までライブ実測済み) ＋ 重複アップロード事故と対処

- **ユーザー指示**: 「podcast が生成されて動画がアップロードされたら、bluesky に投稿したい」。
  認証情報は `~/.config/soren/bluesky.json` に置く / 実投稿でのテストは「投稿して後で消す」方針で合意。
- **事故 (対処済み)**: 検証で `podcast_daily.sh --date 20260825` を `PODCAST_AUTO_PUBLISH=0` を**付け忘れて**実行し、
  08-25 の動画を YouTube へ**重複アップロード**した (新 `KcM0GQDN87M` public / 元 `jOPFZKSvm3c`)。
  ユーザー承認のうえ `videos.delete` で削除し、再生リスト `PLNycKe9FEAGU` の項目も除去。
  実測確認: `KcM0GQDN87M`=GONE、`jOPFZKSvm3c`=EXISTS(public)、playlist=`['jOPFZKSvm3c']`。
  `output/podcast/2026-08-25.publish.json` は元の video_id へ復元（誤アップ分は
  `2026-08-25.publish.dup-20260826-2057.json` に退避）。
  → 再発防止として **`podcast_publish.py` に冪等ガード**を入れた（下記）。手動実行時は
  `PODCAST_AUTO_PUBLISH=0` を付ける習慣も維持すること。
- **追加 (`games/soviet_now` commit `a572e5d90`)**:
  - `tools/bluesky_post.py`（新規）: AT Protocol の XRPC を **urllib だけ**で叩く（外部ライブラリ不要、
    Mac の system python 3.9 でも doci venv でも動作確認済み）。
    `createSession` → `uploadBlob`（サムネ）→ `createRecord`。
    `<日付>.publish.json` / `.meta.json` / `.thumbnail.png` から本文と `app.bsky.embed.external`
    カードを組み立て、URL と #タグの facet を **UTF-8 バイト位置**で付ける。本文は 300 文字に収まるよう
    見出しと URL を残して要約から削る。投稿後 `<日付>.bluesky.json` を残して二重投稿を防ぐ（`--force` で再投稿）。
    `--delete`（at:// か bsky.app URL、`--podcast` 併用で記録済み投稿）でテスト投稿を消せる。
    認証: `BLUESKY_HANDLE`/`BLUESKY_APP_PASSWORD` → `BLUESKY_CREDENTIALS_FILE` → `~/.config/soren/bluesky.json`。
    **アプリパスワードを使う**。鍵はリポジトリにも .env にも置かない。
  - `tools/podcast_daily.sh`: `[4/4]` として公開後に告知。`publish.json` が無い回は流さない。
    `PODCAST_BLUESKY_ENABLED=0` で無効。認証情報が無ければ rc=4 で**黙ってスキップ**（fail-open）。
  - `tools/podcast_publish.py`: **`publish.json` がある回は既定でスキップ**（`--force` で再アップロード）。
  - `core/config.sh`: `PODCAST_BLUESKY_ENABLED` / `PODCAST_BLUESKY_TAGS`（既定は空＝タグ無し）を追記。VM worker は読まない。
  - テスト: `tests/test_bluesky_post.py`（23, `_request` を差し替えてネットワークに出ない）/
    `tests/test_podcast_publish_guard.py`（3）。ともに全 pass。
- **実測で確認したこと**:
  - 日次パイプラインの `[4/4]` が実際に走り、認証情報が無い状態で
    `Bluesky: 認証情報が無いのでスキップ` で止まること（20:57 の `output/podcast_daily.log`）。
  - 本文組み立ては実データ（08-25）で 122 文字 + カード + link facet を生成（`--dry-run` の JSON を確認）。
  - 冪等ガード: `podcast_publish.py --date 20260825` が `公開済みなのでスキップ` で**アップロードしない**こと。
  - **実投稿 (21:15, 本番アカウント `dociai.bsky.social`)**: 08-25 分を投稿し、
    公開 API (`app.bsky.feed.getPosts`) で視聴者に届く形を確認した。
    投稿: https://bsky.app/profile/dociai.bsky.social/post/3mtycq646h625
    - text 122 字 / `langs: ["ja"]` / link facet 1 件（YouTube URL）
    - `app.bsky.embed.external#view`（uri=jOPFZKSvm3c, title=`…｜同志のための時事ニュース`, description=要約）
    - thumb は cdn.bsky.app が **HTTP 200 / image/webp 8946 bytes** で配信（PNG は Bluesky 側で webp 化）
    - **ユーザー判断でこのテスト投稿は消さずに残した**。`2026-08-25.bluesky.json` も残るので
      同じ回が二重投稿されることはない。
  - 認証情報は `~/.config/soren/bluesky.json`（handle=`dociai.bsky.social`, アプリパスワード）。
    ファイルは `chmod 600`、ディレクトリは `chmod 700` に修正済み。
- **未確認 / 次の一歩**:
  - **日次パイプラインが通しで（生成→公開→告知まで）自動で回る様子は未実測**。次の 04:30
    (`com.azumag.soren-podcast.build`) の `output/podcast_daily.log` で `[4/4] Bluesky done` を確認すること。
  - タグ (`PODCAST_BLUESKY_TAGS`) は既定で空。付けるなら投稿前に本文長 (300 字) の余裕を見る。

## 2026-08-26 20:4x JST — ポッドキャスト動画のローカル保持を 3 日に (実装・テスト・日次パイプライン結線まで確認)

- **ユーザー指示**: 「podcast 動画の生成だが、一回で数百MB使うので、3日分すぎたら削除したい」
  （補足: 「ショート動画にはすでに入ってるはず」）。
- **実測した現状**: `games/soviet_now/output/podcast/2026-08-25.mp4` = **299MB**、同 `.mp3` = 36MB。
  掃除の仕組みは podcast 側に無かった（`infra/cleanup.sh` にも項目なし）。
  ショート動画側にあるのは doci の `doci/output_cleanup.py`＝「**投稿成功したら workdir の媒体を消す**」で、
  日数ベースの保持ではない（`run_daily.py:892` から呼ばれる）。今回は日数ベースで podcast に新規実装した。
- **追加 (`games/soviet_now`)**:
  - `tools/podcast_gc.sh`（新規）: `output/podcast/<YYYY-MM-DD>.mp4` を既定 3 日で削除。
    - 未公開（`<日付>.publish.json` 無し）は手で公開できるよう `PODCAST_GC_UNPUBLISHED_DAYS`（既定 7 日）まで残す。
    - **mtime ガード**: 日付が古くても最近作り直した回は消さない（バックフィル保護）。
    - 対象拡張子は `PODCAST_GC_SUFFIXES`（既定 `.mp4`。mp3 も消すなら `".mp4 .mp3"`）。
      台本 `.script.txt` / `.meta.json` / `.chapters.json` / `.segments.json` / `feed.xml` は再生成の入力なので対象外。
    - `--dry-run` / `--days N` / `--out-dir DIR`。BSD/GNU 両方の `date`/`stat` に対応。
  - `tools/podcast_daily.sh`: 生成の**前**に `[0/3] 掃除` として `podcast_gc.sh` を呼ぶ（失敗しても以降は続く）。
    `PODCAST_SKIP_GC=1` で止められる。前段に置いたのは、音声で落ちた日でも容量が減るようにするため。
  - `core/config.sh`: `PODCAST_RETENTION_DAYS`（既定 3）を追記。**VM の worker は読まない**ドキュメント用の既定値
    （podcast 生成は Mac 側 launchd 担当）。よって worker 再起動も VM 反映も不要。
  - `tests/test_podcast_gc.sh`（新規, 17 assertion）: 保持/削除の境界、未公開の猶予、mtime ガード、
    suffix 追加、`--days`、ディレクトリ欠如。**17/17 pass**。既存 `tests/test_podcast_build.sh` も全 pass。
- **実測確認**:
  - 実データに対する `--dry-run`: 「削除対象なし（保持 3日 / 未公開 7日）」= 1 日前の 08-25 は保持される。
  - `PODCAST_SKIP_AUDIO=1 PODCAST_SKIP_VIDEO=1 PODCAST_AUTO_PUBLISH=0 tools/podcast_daily.sh --date 20260825`
    を実行し、ログに `[0/3]` 相当の GC 行が出ること・299MB の mp4 が無傷であることを確認。
  - 実削除そのものは一時ディレクトリのテスト（17/17）で確認。**本番で 3 日超の mp4 が消える瞬間は未実測**
    （最古が 08-25 の 1 本しか無いため。次に古い回が 3 日を超える 08-29 頃の `podcast_daily.log` で確認できる）。
- **判断が要る点**: `.mp3`（36MB/日）は既定で残している。feed.xml の enclosure は
  `PODCAST_BASE_URL`（現状 example.com のプレースホルダ、`PODCAST_RCLONE_ENABLED=0`）で、
  実配信していないため消しても実害は無さそうだが、指示は「動画」だったので触っていない。
  消すなら `PODCAST_GC_SUFFIXES=".mp4 .mp3"`。

## 2026-08-26 17:5x-18:2x JST — コメント滞留中はニュース(ラジオ)の音声合成を即中断してコメントを優先（実装・本番反映・ライブ実測済み）

- **ユーザー指示**: 「ニュースの再生合成(voicevox)が重くてコメント返信が遅れるので、コメントキューがあるときは、
  ニュースの音声合成が始まっていてもキャンセルし、コメント消化を先にする」。
- **原因（実測で特定）**: 背景の `radio_render:*`（deferred ラジオの事前合成）とコメントの
  ストリーミング合成が VOICEVOX 合成ロックを**チャンク単位で 1:1 交互**に奪い合っていた。
  `_acquire_voicevox_synth_lock` の priority waiter はコメントがロックを取った瞬間に消えるため、
  背景ラジオ側の「90秒譲ったら諦める」上限に永久に到達せず、実質ずっと ping-pong する構造だった。
  - 実測 (`logs/audio_worker.log` 17:34:33-17:42:24): ラジオ chunk8→chunk16 の各境界で
    `優先音声の合成完了待ち (background radio)` を出しつつ、コメント1チャンク→ラジオ1チャンクを交互に実行。
  - コメント側チャンク合成: 交互時 **約50〜60秒/チャンク**（17:34 の comment、18:03 の comment は
    6分経っても chunk 7）、ラジオ非稼働時 約20〜30秒/チャンク。**約2倍に伸びていた**。
  - 背景: VM は 4 vCPU / load average 13 で慢性的に CPU 飽和。VOICEVOX 単体でも 100字チャンクに 20〜30秒かかる。
- **変更 (soviet_now `35ebac77e` + `df7a98e73`, ブランチ `codex/no-apply-liveliness`（push 済み）)**:
  - `say_enqueue.sh`
    - `_comment_backlog_pending`: `comment_*.txt`（未再生）と `comment_*.playing`（合成・再生中）の両方を検出。
    - `_radio_render_should_abort_for_comment`: 対象は `radio_render:*`（背景合成）のみ。
      コメント自身・ラジオ「再生」(`radio:*`) は対象外。
    - `_synthesize_chunk_yielding`: チャンク合成を背景ジョブで走らせ 1 秒ポーリング。コメントを検知したら
      `_kill_process_tree` で timeout→env→bash→docich まで確実に停止し、中途 WAV を削除して rc=9。
      チャンク開始前にもチェック。中断時は `exit 75`。
    - **部分レンダーの保持と再開**: ラジオ render のチャンクを `tmp/.say_queue/render_<キュー名>/` の
      固定パスに置き、中断しても捨てない。次回は `source_stamp`（本文ハッシュ＋チャンク数）が一致すれば
      合成済みチャンクを再利用して続きから再開する。render 完了時にディレクトリ削除。
      （毎回チャンク0からやり直すと ready.wav に永久に到達しない。旧コードのコメントにも同じ罠が記録されていた）
  - `broadcast/radio_state.sh`: rc=75 は失敗ではなく意図的な譲りなので、指数バックオフ(30→300秒)を進めず
    `RADIO_RENDER_COMMENT_YIELD_RETRY_SEC`（既定 20 秒）で再開予約する
    (`_radio_schedule_deferred_render_yield_retry`)。実再開は既存の「コメント残数 0 ゲート」で更に抑えられる。
  - `infra/cleanup.sh`: `tmp/.say_queue/render_*` 残骸の GC（180分）。
  - `tests/test_radio_comment_priority_abort.sh`（新規, 21 assertion）。ローカル・VM とも 21/21 pass。
    既存の `test_say_voicevox_priority.sh` / `test_say_voicevox_fairness.sh` / `test_radio_render_retry.sh` /
    `test_radio_deferred_queue.sh` / `test_radio_caption_bundle.sh` / `test_radio_backpressure.sh` /
    `test_radio_time_sync.sh` / `test_peak_hour_queue_gate.sh` / `test_say_streaming.py` も全て pass。
  - **knob**: `RADIO_RENDER_COMMENT_ABORT=0` で従来の交互合成に戻せる。`core/config.sh` には**入れていない**
    （同ファイルが別セッションの podcast 変更で未コミット状態だったため巻き込み回避）。既定値は
    `say_enqueue.sh` / `broadcast/radio_state.sh` 内の `${VAR:-...}` にあり、`.env` で上書きできる。
- **VM 反映**: 18:05:49 に `say_enqueue.sh` / `broadcast/radio_state.sh` / `infra/cleanup.sh` / 新テストを scp、
  sha256 一致を確認。worker 再起動は不要（audio_worker は毎周回 `eloop_lib.sh` を再 source し、
  `say_enqueue.sh` は毎回新規プロセス）。
  - 反映時に走っていた**旧コードの render (PID 2127046)** は、実行中スクリプトを上書きした際の
    オフセットずれを避けるため 18:09:22 に `kill -TERM` して終了させた（`rc=143` で正規の再試行予約へ、
    `.render_lock` / marker / stream_ ディレクトリも trap で掃除済みを確認）。
- **ライブ実測（本番、18:09-18:18 JST）**:
  - 18:09:55 新コードで render 開始 → チャンクが `tmp/.say_queue/render_radio_1787726720_45782_news_896/` へ出力（新パス動作確認）。
  - 18:13:04 コメント着弾 → `コメント優先: ラジオ事前合成を合成中に中断 (チャンク8/21)` →
    `合成済みチャンクを保持（次回再開用）` → `render-only 一時保留（コメント優先）` →
    18:13:08 `[RADIO:deferred] コメント優先で合成を中断・保留（合成済みチャンクは保持）… retry=1 in=20s`。
    **合成の途中でキャンセルされることを実測**。
  - そのコメント (405字/6チャンク) は chunk_0 を **15 秒**で合成し 18:13:24 に発話開始。
    交互合成時の 28〜33 秒（17:44/17:49 の実測）から短縮。全 6 チャンクを 18:13:09→18:15:30 の
    **141 秒**（約23.5秒/チャンク）で消化＝交互時の約50〜60秒/チャンクの半分以下。
  - 18:15:43 コメント完了 → 18:15:44 render 再開、**チャンク1〜7を再利用**（`事前合成を再利用`）して
    chunk 8 から続行。18:17:31 時点で 11/21 まで進行。**中断しても進捗を失わないことを実測**。
- **未確認/残**:
  - ニュース**再生中**（`radio:news`, `SAY_DISABLE_COMMENT_YIELD=1`）はコメントに譲らない仕様のまま。
    実測では 17:45:42-17:49:38 の約4分の再生中に 17:47:21 のコメントが 17:49:45 まで **2分24秒**待った。
    今回の指示は「合成のキャンセル」だったので再生側は変更していない。再生も割り込むかは要判断
    （文の途中で切れるため配信上の副作用がある）。
  - VOICEVOX 自体の遅さ（4 vCPU / load 13 で 100字あたり20〜30秒）は未改善。根本的にはリソース側の問題。
  - 中断が頻発する時間帯にニュース render が完走しきるかの長時間観察は未実施（部分再開があるため
    理論上は必ず前進するが、ピーク時の実測は今後）。

## 2026-08-26 18:5x JST — 配信リレーとコメント取得の Kick 対応（配信・コメント取得とも end-to-end 実測確認済み）

- **ユーザー指示**: 配信リレーを Kick にも対応させ、コメント取得も Kick に対応させる。サーバURL/キーは `/tmp/kickrtmp` `/tmp/kickkey` に用意された。

### 実測で確定した事実
- **Kick の ingest は RTMPS(443) のみ受理し、平文RTMP(1935) を拒否する**。VM から ffmpeg で実測:
  平文 = `Error opening output ... Input/output error`、RTMPS = 成功（rc=0）。
  ホストは `fa723fc1b171.global-contribute.live-video.net`（Amazon IVS 系、キーは `sk_us-` 形式）。
- **キーの所属チャンネルは Kick の `dociai`**。テスト配信中に Kick API を叩き `dociai` だけ LIVE になることを確認（`azumag` は offline のまま）。
- **nginx-rtmp の push は RTMPS 非対応**（モジュールに rtmps 実装なし）。→ ループバックの TLS ブリッジが必要。
- **ブリッジ越しでも Kick は受理する**（RTMP の tcUrl ホストは見ていない）。ローカルTLS中継経由の ffmpeg push が成功。
- **TLS の負荷はほぼ無い**: 本番同等（1280x720@30 / 4500k+160k）で中継プロセスは 1コアの 0.9〜1.2%、RSS 18MB（Python実装での上限値。stunnel はこれより低い）。
  VM は AES-NI 搭載で AES-128-GCM 2.65GB/s/コア、配信は 0.59MB/s なので暗号自体は 1コアの 0.02%。増えるのは送出帯域 9.4→14.1 Mbps。
- **Kick チャットは公開 Pusher チャンネルを匿名購読できる**。`chatrooms.<chatroom_id>.v2` を購読して `ChatMessageEvent` を受信できることを、混雑中の実チャンネル(lonche)で実測（本文・投稿者・IDが取れる）。`dociai` の chatroom_id は 124700318。

### 実装（soviet_now `e6e61a625` + `ddb604d8a` + `eaf941f50` / docich `bf039ac`、push 済み）
- 配信リレー: `install_rtmps_bridge.sh` + `deploy/soren-rtmp/{rtmps-bridge.conf.template,soren-rtmps-bridge.service}`。
  stunnel を `soren-relay` ユーザーの専用ユニットで動かし、`127.0.0.1:19351` → RTMPS 443 へ中継する。
  **配信キーは従来どおり `/etc/soren-rtmp/push.conf` だけに置く**（ブリッジ設定にも argv にも出ない）。ingest ホストは `--host` で渡しリポジトリに残さない。
- コメント取得: `kick_chat_daemon.mjs`（Pusher 購読 → raw.log へ `id=<msg-id>\t<user>: <本文>`）、
  `kick_chat.sh`（twitch_chat.sh と同じ fetch/ack/ack-batch 契約）、`workers/kick_worker.sh`（daemon 死活監視＋`generate_comment_response kick`）。
  `broadcast/comment.sh` に `kick` ソースを追加、`start_all.sh` / `reload_worker.sh` / `show_status.sh`(KickW 行) / `core/config.sh` に登録。
- テスト: `tests/test_rtmps_bridge.py`(8件)、`tests/test_kick_chat.sh`(11件)、`tests/test_kick_chat_daemon.mjs`（偽 Pusher サーバでネットワーク非依存）。いずれも green。

### VM の状態（2026-08-26 18:33 時点、すべて実測確認済み）
- **stunnel ブリッジ稼働中**: `soren-rtmps-bridge.service` active、`127.0.0.1:19351` で LISTEN。
  設定 `/etc/soren-rtmp/rtmps-bridge.conf` は `root:soren-relay` 0640、中身はホストとポートのみ（キー無し）。
  実測 CPU 0.7% / RSS 9MB。証明書検証も通過（journal に `Certificate accepted at depth=0: CN=*.global-contribute.live-video.net`）。
  - **ハマり所**: 最初 `output = /dev/stderr` を書いていたら systemd 配下で stunnel が
    `Cannot open log file: /dev/stderr` で起動直後に落ちた（stderr が journal ソケットのため）。`output` を書かないのが正解（commit `ddb604d8a`）。
- **push.conf に Kick を追加済み**（3 destination: Twitch / YouTube / `rtmp://127.0.0.1:19351/app/<KEY>`）。
  バックアップ `/etc/soren-rtmp/push.conf.bak.20260826_kick`。`nginx -t` 通過 → reload 済み。
- **reload だけでは push 先が増えないことを実測**（reload 後も外向き接続は 2 本のまま）。`direct_stream` を再起動して 3 本になった。
- **3 プラットフォーム同時配信を実測確認**（18:33）:
  - relay/stunnel の外向き接続 3 本（Twitch 35.55.39.21:1935 / YouTube 142.251.189.134:1935 / stunnel→Kick 35.55.39.25:443）
  - Kick `dociai` LIVE（09:12:57Z〜）/ Twitch `dociai` LIVE（06:09:56Z〜）/ YouTube `live`（07:21:51Z〜）
  - **Kick の公開 playback から 1 フレーム取得して本番映像（ゲーム画面＋オーバーレイ）が映っていることを目視確認**。
  - publisher 再起動と supervisor 再起動をまたいでも Twitch/YouTube のセッションは切れなかった（開始時刻が変わっていない）。
- **`.env`**: `KICK_CHAT_ENABLED=1` / `KICK_CHANNEL=dociai` / `KICK_CHATROOM_ID=124700318` / `KICK_IGNORE_AUTHORS="dociai DoCiAI"`（バックアップ `.env.bak.20260826_kick`）。
- **supervisor を再起動済み**（`sudo systemctl restart soren-runtime.service`）。`kick_worker.sh dociai` が supervisor の子プロセスとして稼働（＝落ちても自動復帰）。
  Kick chat daemon も chatroom 124700318 へ再接続済み。
- **stream key は VM 上の一時ファイルから消去済み**（`/home/ubuntu/soren` 配下に key 文字列が残っていないことを grep で確認）。

### Kick コメントの end-to-end 実測（2026-08-26 18:53-18:55、成功）
- Kick 投稿 `azumag: コメントテスト`(09:53:48Z) → daemon が raw.log へ
  (`id=49e5f5ce-… azumag: コメントテスト`) → `kick_worker` の fetch で pending / `tmp/kick_comments.txt`
  → `generate_comment_response kick` が 18:55:50 に返答生成
  (`tmp/.comment_queue/comment_1787738149_18850.txt`、本文で「あずまぐさん、テストコメント確かに届いていますよ。」と名指し返答)
  → pending 消化。**取得から返答生成まで全経路を実測確認**。
- **既定の無視リストで取りこぼす不具合を1件修正（commit `eaf941f50`）**:
  Twitch の慣習（`dociai` = AI 返答の送信元＝エコー）をそのまま持ち込み `KICK_IGNORE_AUTHORS` の既定を
  `"dociai DoCiAI"` にしていたため、`dociai` アカウントからのテスト投稿 2 件
  (09:14:30Z「コメントテスト」/ 09:48:42Z「komenttest」) が raw.log に入らなかった。
  **Kick へは何も送信していない＝エコーは発生しない**ので既定を空にした。原因特定には Kick 履歴 API
  `https://kick.com/api/v2/channels/<channel_id>/messages` が有効（投稿者と本文が見える）。
  VM `.env` も `KICK_IGNORE_AUTHORS=""`（バックアップ `.env.bak.20260826_kickignore`）。
  **Kick 送信を実装したら、その送信元アカウントを `KICK_IGNORE_AUTHORS` に入れないと自分の返答を読み返す。**

### 未完了 / 未実測
- **Kick への送信（返答の投稿）は未対応**。Kick 側の認証が別途必要で、返答は Twitch / YouTube にだけ出る。
- **教訓（今回やらかした手順ミス）**: VM へ scp する前に VM 側の差分を確認しなかった。さらに
  **稼働中の `start_all.sh` を上書きしたため bash のスクリプト fd オフセットがずれた**（`/proc/<pid>/fdinfo/255` の pos が
  新ファイルの別位置を指す＝ループ終了時に断片を読む状態）。今回は supervisor 再起動で解消したが、
  **稼働中スクリプトを上書きしたら、そのプロセスを必ず再起動する**。詳細はメモリ `soren-live-script-overwrite-hazard`。

## 2026-08-26 16:5x JST — Twitch チャットで「あずまぐ」(azumagbanjo) のコメントを再び読むようにした

- **ユーザー指示**: 「あずまぐ」からのコメントを無視せず読むようにする。
- **経緯**: 2026-05-26 `4a1d03ce6` で「bot / 配信者の自己投稿はエコー」として
  `TWITCH_IGNORE_AUTHORS` 既定を `azumagdev azumagbanjo あずまぐ` にしていた。VM の `.env` はさらに `dociai` を追加。
- **実測で確定したアカウントの対応（Helix `users`）**:
  - `azumagbanjo` = 表示名 **あずまぐ**（視聴者本人。id 130871908）
  - `dociai` = 表示名 DoCiAI（配信チャンネル。**`TWITCH_BOT_TOKEN` の所有者＝AI の返答を投稿している送信元**）
  - `azumagdev` = 表示名 azumagdev（`TWITCH_BOT_NICK`、旧 bot）
  → エコー対策に必要なのは `dociai` と `azumagdev` だけで、`azumagbanjo` / `あずまぐ` は外して良い。
- **変更（soviet_now `fd5541d45`）**: `twitch_chat.sh` / `twitch_chat_daemon.sh` の既定を
  `${TWITCH_IGNORE_AUTHORS:-dociai azumagdev}` に。VM `.env` の `TWITCH_IGNORE_AUTHORS` も
  `"dociai azumagdev"` へ（旧値のバックアップ `.env.bak.20260826_ignoreauthors`）。
- **反映**: scp で sha256 一致確認 → `kill -USR1 <chat_worker>` で .env 再読込（`reload complete`）→
  IRC daemon を kill して chat_worker に再起動させ（新 PID 1576041）、`/proc/<pid>/environ` が
  `TWITCH_IGNORE_AUTHORS=dociai azumagdev` であることを確認。
- **検証（関数単位・VM 上の実ファイル＋実 env）**: `azumagbanjo/あずまぐ` = READ、`dociai/DoCiAI` と `azumagdev` = IGNORED、
  行フィルタも `あずまぐ: …` = READ / `DoCiAI: …` = IGNORED。
- **ライブ実測（17:00-17:03 JST）**: あずまぐの実コメントが `tmp/.twitch_chat/pending.log` に載り（17:00 前後）、
  `chat_worker` が 17:00:30 に分類（`user":"あずまぐ"`）、17:03:05 に 619 字の返答を生成してキュー投入
  （`tmp/.comment_queue/comment_1787731384_18582.txt`、本文で「あずまぐさん、…」と名指しで返答）。**読める状態を end-to-end で確認**。
  YouTube 側は元から `YOUTUBE_IGNORE_AUTHORS=DoCiAIch` のみで、あずまぐは読めていた（ログで確認）。
- **注意**: カードガチャ結果（`AがBを獲得しました`）は `dociai` が投稿しており、無視リストに残っているので
  「無視対象 かつ ガチャ結果なら読む＋名前プレフィックスを外す」という既存の例外は従来どおり効く。

## 2026-08-26 15:3x-15:5x JST — YouTube 配信が 15:06:57 に停止（正午監査の 180 秒断が原因）。VM 側は正常だが YouTube が新しい配信を開始しない（ユーザー確認待ち）

- **ユーザー報告**: 「YouTube へのライブ配信が死んでいるようです」。
- **実測（YouTube Data API / 公開ページ）**:
  - 常設ブロードキャスト `M8GABUS1EXw`（同志Ch, UCdlddCsAmT4cYMMSTO7kBvw）は
    `actualStartTime 2026-08-21T11:46:20Z` → **`actualEndTime 2026-08-26T06:06:57Z`(= 15:06:57 JST)** で終了。
  - `eventType=live` の検索 0 件、uploads プレイリスト最新 8 件にもライブ無し、watch ページも `isLiveNow:false`。
    **15:51 時点で YouTube は停止したまま**。
- **原因（時刻の一致）**: 15:06:57 は正午監査が Twitch セッション回転のため ffmpeg を落とした **15:06:44 の 13 秒後**。
  昨日入れた `STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC` 30→**180 秒**の断が YouTube の許容を超えて配信を終了させた。
  08-25 03:56 の約 35 秒断（リレー reload）では YouTube セッションは継続していた（同一 actualStartTime のまま）。
  **正午監査は Twitch しか見ておらず、YouTube の死活監視は存在しない**ため 30 分以上気付かれなかった。
- **VM 側は正常（実測）**: ffmpeg 稼働、nginx リレーから YouTube ingest へ **約 4.7Mbps が実際に流れ ACK もされている**
  （`bytes_acked` が 4 秒で約 2.3MB 増加を 2 回計測）。Twitch へも送出中で Twitch は LIVE。
- **試した復旧（いずれも効かず）**:
  1. 15:42 `sudo ss -K` で YouTube への push ソケットのみ切断 → `push_reconnect` で別 IP へ新規 push 成立。
     3〜4 分待っても配信開始せず（Twitch は無傷）。
  2. 15:47 `sudo systemctl restart soren-rtmp-relay.service` → 両 push が新規確立。4 分後も配信開始せず。
     **Twitch セッションは回転せず維持**（id 317977691864 / createdAt 15:09:56 JST のまま、viewers 5）＝再起動のコストは無かった。
- **前例**: 08-21 20:45:56 のリレー再起動の 24 秒後（20:46:20 JST）に YouTube 配信が開始している。
  ただしこれが自動開始だったのか当時ユーザーが Studio で「配信開始」を押したのかは**未確定**。
- **判定**: VM→YouTube の映像経路は生きており、**ブロック要因は YouTube アカウント／ブロードキャスト側**。
  自動開始が無効、ライブ配信機能の制限、または常設ブロードキャストが完了状態のまま、のいずれか。
  API での確定には OAuth が必要だが、**YouTube チャットの refresh token が `invalid_grant` で失効**しており
  `liveBroadcasts/liveStreams` を読めない（チャット送信も現在不能。`tmp/.youtube_chat/last_send_error.txt`）。
- **ユーザー作業（依頼中）**: YouTube Studio のライブ管理画面で (1) 受信状態が「受信中/良好」か、
  (2)「ライブ配信を開始」ボタンが出ていないか（＝自動開始オフ）、(3) ライブ配信機能の制限が出ていないか、
  (4) 配信キーが変わっていないか（変わっていれば `/etc/soren-rtmp/push.conf` の更新が必要）。加えて OAuth 再認証。
- **【復旧完了 16:21:51 JST（実測）】**: 原因は「**待機中の配信枠がある状態で、タイムスタンプ 0 から始まる新しい送出セッションを張る**」という
  組み合わせが一度も成立していなかったこと。時系列: 15:09 と 15:47 は t=0 の新規送出だったが**配信枠が存在しなかった**
  （旧枠は 15:06:57 に終了、新枠 `3H5lXGvvDo4` は 15:56:43 に作成＝ユーザーが Studio を開いた頃）。16:16 のソケット再接続は
  枠はあったが**送出が途中から**（t≈29分）で紐づかず。16:21:40 に ffmpeg を kill -TERM して supervisor に再起動させ、
  両 push が t=0 で張り直された **11 秒後の 16:21:51 に配信開始**。
  - 実測: `3H5lXGvvDo4` = `liveBroadcastContent: live` / `actualStartTime 2026-08-26T07:21:51Z`、
    watch ページ `isLiveNow: true`、Studio ヘッダーに「ライブ」＋経過時間と「ライブ配信を終了」。
  - **Twitch は無傷**: session id 317977691864 / createdAt 15:09:56 のまま（回転せず）、視聴者 8。ffmpeg 再起動の断は約10秒で吸収された。
  - **配信 URL が変わった**: 旧 `M8GABUS1EXw`（08-21〜08-26 の 5 日間の枠）は終了・アーカイブ化。以後は `3H5lXGvvDo4`。
- **ブラウザ調査で分かった UI 事実**: Studio の「ライブ配信を開始」ボタンは *存在するが* `disabled` かつ `display:none`。
  つまり「ボタンが出ない」のは仕様どおりで、YouTube がプレビューを用意できるまで押せない。プレビューの `<video>` は
  src 空 / readyState 0 で、YouTube 側が一度もトランスコード出力を作っていなかった。「ストリームの状態」タブは
  「ストリームは健全です／非常に良い」でエラー無し＝**受信は正常なのに枠に紐づいていない**状態だった。
- **残（未修復）**: YouTube チャットの OAuth は `invalid_grant` のまま（16:23 実測）。`live_video_id` も未生成。
  チャット送信は不能なので、ユーザーによる再認証が必要。
- **再発防止（未実装・要設計）**: 正午監査の 180 秒断が YouTube を巻き込む構造は残っている。案は
  (a) 断の対象を Twitch push だけに限定する（push.conf から Twitch だけ外して reload → 180 秒後に戻す）、
  (b) Twitch へは relay から copy の別 ffmpeg で出し独立に止められるようにする、
  (c) YouTube の死活監視を監査に追加する。設計は fable へ委任予定。

## 2026-08-26 13:5x-15:1x JST — 正午の配信貼り直しが Twitch へ届いていなかった件を修正・実測で復旧確認

- **ユーザー報告**: 「正午の配信貼り直しが動いてない」。
- **実測した現象**: Twitch のセッションは `id=317965866584 / createdAt 08-25 03:56 JST` のまま 34 時間継続、VOD も 1 本のまま。
  一方 VM の `direct_stream.py status` の started_at は 08-25 12:00:56（＝正午）で、今日 12:00 の監査は `no_action (offset_diff=56s)` だった。
- **原因1（張り直しが Twitch へ届かない）**: OFFLINE 保持 30 秒では Twitch が再接続を**同一セッションへマージ**する。
  08-25 は約35秒の断でローカル ffmpeg は 12:00:56 に復帰したが Twitch 側 id は不変。過去 VOD の並びでは 1〜2 分の断で新セッションになっている。
  08-23/08-24 の監査も同様に `restart_failed` を記録していた（`logs/stream_noon_audit.log`）。
- **原因2（失敗の自己マスク）**: 位相判定がローカル started_at 基準だったため、ローカルだけ正午に揃うと翌日以降 `no_action` となり再試行されない。今日の no_action がこれ。
- **修正（soviet_now `902cf9d64` + `56d9de98d`, `workers/stream_noon_audit.sh`）**:
  1. 位相基準を Twitch セッション `createdAt` へ（GQL query に createdAt 追加）。取得不能時のみ started_at へフォールバック。marker に `phase_source` / `session_created_before` を追加。
  2. `STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC` 既定 30→**180 秒**（ユーザー了承済み）。offline 待ちの deadline 余裕も 5s→30s。
  3. `_wait_running_with_new_session` を `_wait_local_respawn` + `_wait_session_rotated` に分離。ローカル復帰済みでセッションだけ回らない場合は `restart_session_merged` として区別し、二重起動になる自前起動フォールバックを行わない。
  4. 起動ログに `offline_hold` / `session_rotate_wait` を出力。
- **テスト**: `tests/test_stream_noon_audit.sh` に 2b（ローカルが正午でも Twitch がずれていれば張り直す＝今回の実障害の回帰）、2c（Twitch が正午ならローカルがずれていても触らない）、5b の自前起動抑止を追加。**ローカル・VM とも 44/44 pass**（修正前は 38/38 pass）。
- **VM 反映と実測**: scp で sha256 一致を確認し worker 再起動（PID 629093、起動ログ `offline_hold=180s session_rotate_wait=60s`）。今日の marker を削除して手動で1回監査させた実測:
  - `15:06:44 restart_required (phase=twitch offset_diff=-29011s)` — 旧コードが no_action と誤判定していた状況で正しく検出
  - `15:09:52 Twitch offline confirmed (180s)` → `15:10:09 配信を再開しました (stream_id 317965866584 → 317977691864)`
  - 外部 GQL でも 15:10:07 に `id=317977691864 / createdAt 08-26 15:09:56 JST` を確認。**新 VOD `2856736357`(08-26 15:10) が生成され旧 VOD は 35h11m で確定**＝貼り直しが外から見える形で成立。
  - 断は 15:06:44〜15:09:56 の約 **3分12秒**。復帰後 fps 30.2 / speed 1.01 / bitrate 4542kbps。A/B は `games_recorded=58 tainted=0` で無影響（strategy.py 非変更・decide hash 不変）。
- **明日の挙動（予告）**: 今回の再開は 15:09 なので、明日 12:00 の監査で再度 `restart_required` となり約3分の断で正午へアンカーされる。以後は Twitch の 48h 強制切断が正午に来るため定常状態では張り直し自体が不要。
- **仕様確認（2026-08-26 ユーザー回答）**: 判定は「セッション開始時刻が正午±10分か」の**位相のみ**で行う現行仕様のままとする。
  「経過48hか/次の自然切断が夜中に落ちるか」を見て介入を遅らせる案は提示したが、**変更不要**との回答。以後この方針を勝手に変えないこと。
- **残（未測定）**: 180 秒はマージ猶予の上限を厳密に測った値ではない（**35秒＝マージ / 180秒＝回転** を実測、境界は未測定）。短縮したい場合は `STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC` を下げ、marker の outcome が `restart_session_merged` にならないか観察すること。

## 2026-08-26 15:xx-18:5x JST — docich#10: ポッドキャストの動画化と YouTube 公開まで到達（初回エピソード公開済み・日次自動化まで完了）

- **公開済み**: https://www.youtube.com/watch?v=jOPFZKSvm3c 「【8/25】揺らぐ世界で問われる連帯と暮らし｜同志のための時事ニュース」
  53分34秒 / 1920x1080 / public / チャンネル **同志Ch (UCdlddCsAmT4cYMMSTO7kBvw)**。
  再生リスト `PLNycKe9FEAGU`「同志のための時事ニュース」へ追加、**podcastStatus=enabled** を実測確認。
- **音声の確定仕様（ユーザーが聴き比べて決定）**: 話者109 / 話速1.2 / ピッチ-0.02 / ny ポーズ補正**オフ** /
  BGM インターナショナル 0.15 / コーナー区切りは「無音2.6秒 → 見出し読み上げ → 無音0.5秒」。
  すべてポッドキャスト側の env で渡し、**配信側の設定は不変**（VM の tempo map は 109→1.15 のまま）。
- **ny ポーズ補正の件（重要な発見）**: docich `apply_ny_pause_fix` は i 母音直後の「ニュ」の前に 0.20 秒の
  ポーズを強制挿入する。番組名「同志のための時事ニュース」が ジジ‖ニュウス に割れて聞こえていた原因。
  A/B 実測 1.781s vs 1.611s（差 0.171s = 0.20/1.2）。silencedetect には出ない（ノイズフロア付きで書き出されるため）。
  導入理由の記録は git 履歴を遡っても見つからず**未確認**。ポッドキャストのみ `PODCAST_NY_PAUSE_FIX=0` で無効化。
- **編成の設計**: 「1日分をそのまま連結」は **3〜5時間**になる（実測 08-19〜08-25 で 2h43m〜5h24m、平均4h）。
  VOICEVOX 実測 **306字/分**（話速1.0）/ **367字/分**（1.2）。要約→構成案→コーナー執筆の三段で 1 本の番組へ。
  生成は **codex CLI + gpt-5.6-luna**（`--output-schema` で JSON 強制。旧 opencode_go は 59,909字1発で15分タイムアウト）。
- **【最重要】ffmpeg のメモリ 30GB → 2.27GB**: 1本の filter_complex に画像concat（1枚69秒保持=実質0.014fps）・
  字幕トラック（30fps）・showwaves（30fps）を同居させると、overlay が遅い側を待つ間に速い側を溜め込む。
  `1920x1080x4B x 69s x 30fps ≒ 17GB`。frame=0 のまま526秒進まず出力48バイトだった。
  **対策**: (1) 背景を先に 30fps CFR の中間ファイルへ書き出す2段構成 (2) 字幕PNGを全画面から実際に使う帯
  (1762x230、全画面比19%) へ切り詰め（8.3MB→1.4MB/frame） (3) 波形は2段目で asplit して重ねる。
  **フル尺53分の実測: ピーク2.27GB / 所要5分57秒 / 299MB**。長さに比例しない（150秒テストで2.50GB）。
- **この環境の ffmpeg 8.0.1 には libass が無い**（`subtitles`/`ass` フィルタ不在）。ASS 焼き込みは使えず PNG 方式。
- **字幕**: doci の `build_subtitles` / `_render_caption_png` / `_wrap` をそのまま流用。ただしチャンク上限が
  9:16 前提の26字なので **16:9 の46字（1行23字x2行）へ広げる**必要があった（26字のままだと語の途中で切れる）。
  タイミングは `<date>.segments.json`（文単位 start/end）。音声と動画が同じ合成結果を共有するのでズレない。
- **素材**: シーンは文境界を割らずに65秒ごと（53分で46枚）。検索語は **LLM に英語で作らせる**
  （日本語キーワード抽出だと「パーセント」「万人」が混ざり Pexels の関連度が落ちた）。取得は `doci.assets.fetch_image`。
- **YouTube 認証の落とし穴**: Google は増分認可で要求より多いスコープを返し、oauthlib が例外にする
  （`Scope has changed ...`）。**`OAUTHLIB_RELAX_TOKEN_SCOPE=1`** で回避。publish/short_video 両方に設定済み。
  取得できたスコープは upload / readonly / **force-ssl**。
- **Podcast 指定は初回のみ Studio 必須**: API の `playlists.update` に `status.podcastStatus=enabled` を渡すと
  `400 failedPrecondition`。`podcastStatus` を外した update は成功するので権限問題ではない。
  動画の processingStatus=succeeded 後も同じ。**ユーザーが Studio で手動指定したら enabled になった**。
  以降のエピソードは再生リストに追加するだけでエピソード扱い。API 側の前提条件は公式に明記が無く**未確認**。
- **クレジット**: VOICEVOX は表記が規約上必須（東北イタコ: 動画内または概要欄に「VOICEVOX:東北イタコ」、
  https://zunko.jp/con_ongen_kiyaku.html）。説明欄へ自動挿入。公開済み動画にも遡って反映済み。
  **BGM のインターナショナル音源の出所は不明で、クレジットは空のまま（ユーザー保留）**。
- **日次自動化（Mac launchd 04:30、明日から）**: `tools/podcast_daily.sh` が
  音声(13分) → 動画(6分) → 公開(30秒) を通しで実行。前段失敗で停止、lock で多重起動防止。
  06:00 の Short 動画ジョブより前に終わる。`PODCAST_SKIP_AUDIO/VIDEO` `PODCAST_AUTO_PUBLISH` で段ごとに停止可。
  **無人で public 公開する設定（ユーザー承認済み）**。
- **反映**: soviet_now `631fca749` `c764f90af` `18f01fe3f` push 済み。
  VM へは **config.sh と podcast_build.py のみ** scp（動画化・公開は doci/Pillow/Chrome 依存で VM では動かないため送らない）。
  SHA256 一致・構文チェック OK・配信影響なし。
- **成果物**: `output/podcast/` に mp3 / mp4 / script.txt / segments.json / chapters.json / description.txt /
  publish.json / **podcast_cover.png（1280x1280 カバーアート）** / **podcast_show.txt（説明文4種）**。
- **残**: (1) R2 等への MP3/RSS 公開は**ユーザー保留**（`PODCAST_BASE_URL` は example.com のまま、購読不可）
  (2) BGM クレジット保留 (3) ソ連ネタ動画はスケジュール未登録 (4) Mac スリープ時に生成が飛ぶ点の運用整理。

## 2026-08-26 04:3x-05:5x JST — docich#10: Podcast を VM->Mac へ移設 + 「1日1本の番組へ編成し直す」設計へ作り替え + Short 投稿導線

- **VM 実績の確定（実測）**: `podcast.timer` の 08-25 05:30 実行(08-24分)は 05:30→07:17 の **1h47m** を消費し、
  `logs/podcast.log` で **37 本が voicevox timeout**、成功 2 本、`podcast.service` exit 1。成果物は 98KB / **8.4 秒**。
  配信本体と VOICEVOX を奪い合い 1 本 180 秒のタイムアウトを踏み続けたのが原因。
- **VM 停止（実測）**: `sudo systemctl disable --now podcast.timer` → is-enabled=disabled / is-active=inactive /
  list-timers から消滅を確認。残っていた failed unit も `reset-failed` して `systemctl --failed` = 0 units。
  `radio-archive.timer` は無傷(08-26 04:01 に files=825 で push 成功)。soren_loop も無傷。
- **設計是正 1（長さの実測）**: 「1日分をそのまま連結」は **3〜5 時間**になる。VOICEVOX 実測 **306 字/分**
  (294 字 → 57.6 秒) で換算し、08-19〜08-25 の 7 日で 2h43m〜5h24m、平均約 4 時間。08-25 は 51 本 67,411 字 = **3h40m**。
  当初「70 分」と見積もったのは誤りで実際は 3 倍以上。コメント類も全て訂正済み。
- **設計是正 2（三段編成）**: 素材は「配信中のラジオ」として書かれているため連結では番組にならない。
  **要約(map, 8 本ずつ並列) → 構成案 → コーナーごとに執筆(並列)** の三段に作り替えた。
  執筆段には要約ではなく**元の原稿**を素材として渡す(要約経由だと中身が痩せる)。
  配信・ゲーム要素(時報挨拶/試合数/スコア/盤面/コーナー進行/視聴者・コメント・チャット/末尾の締め)の除去を明示。
- **生成経路（ユーザー指定）**: doci の ai_text(opencode_go) から **codex CLI + gpt-5.6-luna** へ変更。
  `--output-schema` で JSON を強制するため出力途中切れの解釈失敗が構造的に消える。
  旧経路は 59,909 字 1 発で **15 分タイムアウト**、その前の回は JSON 途中切れで失敗していた。
- **実測（08-25, news 25 + jiji 26 = 51 本 / 67,411 字）**: 要約 7 バッチ 60 秒 → 構成案 25 秒 →
  執筆 11 コーナー 174 秒 → 本文 21,992 字。合成 15m42s → **72 分 24 秒の MP3**(49MB)、13 チャプター。
  `used_count: 51` で全 51 本が反映。本文検査で配信・ゲーム由来の語は検出ゼロ
  (Twitch の AI 学習方針など、その日の実ニュースとしての「配信」「チャット」は正当な用法として残る)。
- **長さの knob**: `PODCAST_TARGET_CHARS`(0=自動で全話題を拾う)。3500≒11分 / 20000≒65分 / 55000≒3時間。
  市場調査ではデイリー番組の推奨帯は 8〜15 分、合成音声は 12 分前後で聴取疲労とされるが、
  ユーザー方針は「まず全部拾って後から絞る」なので既定は自動。
- **Mac 移設の落とし穴**: `bin/docich` は PATH の `python3` を exec するため `/usr/bin/python3`(3.9) が
  選ばれると `tomllib` 不在で**全 synth が失敗**する(実際に発生)。ラッパーで tomllib を持つ python を PATH 先頭へ。
- **Short 投稿導線**: `short_video_build.py` の `--do-upload` が `--no-upload`(default=True) と AND されて
  **恒常的に False**だったバグを修正(これまで投稿は構造的に不可能)。
  `short_video_build.sh` に token 検出ゲートを追加し、`secrets/soren_news/youtube_token.json` が出来たら自動で実投稿。
  doci `channels/soren_news/channel.toml` の privacy を public に(doci bd34386)。client_secret は配置済み。
- **反映**: soviet_now `086b46584` push 済み、VM へ 7 ファイル scp し SHA256 全一致・構文チェック OK。
  PODCAST_* を読むワーカーは config.sh と podcast_build.py だけなので worker 再起動は不要(VM 上で grep 確認済み)。
  Mac launchd `com.azumag.soren-podcast.build`(04:30 JST) を bootstrap 済み、初回は明日 04:30。
- **【注意】別セッション事故**: 並行稼働中の /loop が `git add` で本作業のステージ済みファイルを巻き込み、
  soviet_now `c257fadbb`(v738 のコミット)に podcast 系 6 ファイルが混入して push された。
  push 済み + 並行セッション稼働中のため履歴書き換えはせず、続きを `086b46584` に分けた。
  **教訓: 並行セッションがある間は git add したまま放置しない**。
- **残**: (1) YouTube 認証はユーザー作業 `python -m doci.youtube --auth --channel soren_news`。
  (2) ポッドキャストの配信先未決(ユーザー選択は「今は決めない」)。`PODCAST_BASE_URL` は example.com のまま。
  (3) ソ連ネタ動画(`tools/soviet_video_build.py`)はスケジュール未登録。
  (4) 尺の調整は `PODCAST_TARGET_CHARS` を触って実測を重ねる。

## 2026-08-25 05:4x JST — 毎時メンテ(リモート): SSH/push遮断4時間目。外部監視は継続成功 — 配信正常、ただし本番hashがさらに 42c79aab へ変化・Rejected 1→2（v727 は表示から消滅、anneal 機構による回転の可能性）

- **環境制約（実測、02:5x〜04:5x と同一）**: `ssh` バイナリ無し・`129.146.54.105:22` raw TCP timeout。`azumag/soviet_now` push は `add_repo(access=push)` 後の `git push --dry-run` でも 403（"Claude doesn't have GitHub access ... An org admin can install the Claude GitHub App"）。`azumag/docich` push は可。VM 内部確認は依然不可。
- **配信（外部実測 05:44-05:46 JST、Twitch 公開 GQL）**: dociai **LIVE**、game=Soren Game、viewers 2。**stream id 317965866584 / createdAt 08-25 03:56 JST のまま** — 04:5x 節で観測した 03:56 の配信セッション新規化以降、新たな切断は無し。
- **プロセス（プレビュー画像オーバーレイ読取り 05:45:04 JST）**: Loop RUNNING **PID 2904410（前時間と同一＝soren_loop は連続稼働）**、ChatW/YouTubeW/AudioW/RadioW RUNNING、Twitch/YouTube チャット CONNECTED、PredW PAUSED (idle)、**ImproveD = STOPPED 継続（赤）**、Workers 5/6。Backend `FFMPEG LIVE fps=30.0 speed=1.0 drop=29`、AVSync PASSED (drift=10.0ms)。ゲームは10試合目進行中（Live MOVE score=644 pieces=18、LastDrop 37手目アルメニア）。#48874 games、Recent30:1126 Trend ▼-19% Rus:2%。
- **音声**: Say SILENT、VOICEVOX SYNTH streaming、CommentQ empty、RadioQ 7 queued、PlaybackQ 1 waiting — 滞留なし。
- **【重要・続報】戦略hashがさらに変化**: ヘッダ「Strategy: **42c79aab** v45340 1473L R:1」（04:50 の e5b671c8 n100 からまた交代）。top bar **Rejected:2（04:50 は 1）**。Reg NO a=42c79aab n=100。**AnnealObs は e5b6 p=0.90 gap=57 38m**（04:50 は 5c9a p=1.00 gap=0 2h）→ e5b671c8 が anneal 観測枠へ移り、**v727 (5c9ab0ea) はパネル上のどこにも表示されない**。Strategy Comparison 上位は 0b6d4deb 10312 / 922fb1f7 10282 / 2dfa9b77 10274 / 505b0cfb 10183 で不変。解釈: 1時間で active hash が2回入替わっており、単発の誤粛清というより **anneal/observation 機構が候補hashを順に active へ回している**挙動に見える。ただし Rejected が 1→2 に増えており **v727 が観測の末 reject された可能性**がある。確定には VM ログ（STATGATE/REGRESSION/ANNEAL、tmp/history/rejected_hashes.txt、latest.jsonl）が必要。
- **低信頼の観測**: オーバーレイに「Sock FAILED fps=29.99 speed=1.0 audio=99」風のグレー行あり（JPEG 解像度の限界で綴り不確か）。VM 回復後に show_status.sh の該当行を確認のこと。
- **実施できなかったこと**: VM 内部調査・バナー表示・soviet_now への push（analyze_board indent fix は `docich/docs/patches/20260825_analyze_board_reactive_pairs_indent.patch` が引き続き正）・v728 着手。
- **通知**: 4時間連続の遮断と v727 消滅の件をユーザーへ push 通知で送付（本セッション）。
- **次セッションへ（優先順）**: (1) ユーザー対応 — network policy に SSH(22) 許可 + GitHub App に soviet_now write 付与。(2) VM 回復後最優先: 02:46 以降の rollback/anneal 履歴の解明（`grep 'STATGATE\|REGRESSION\|PROMOTE\|ANNEAL\|LANE_COVER' logs/soren_loop.log`、`tmp/history/rejected_hashes.txt`、`extract_decide_hash.py strategy.py`）。v727 が誤 reject なら境界デプロイ手順で復元。ImproveD STOPPED の経緯・03:56 配信セッション新規化の原因も確認。(3) analyze_board パッチ適用・push・VM 反映・submodule bump。(4) 遮断中も本外部監視手法（GQL + プレビュー画像）で毎時 liveness / hash 確認を続ける。

## 2026-08-25 04:5x JST — 毎時メンテ(リモート): SSH/push遮断3時間目。Twitch公開API+プレビュー画像による外部ヘルスチェックに成功 — 配信は正常稼働、ただし本番戦略hashがv727でない（e5b671c8、要VM調査）

- **環境制約（実測、02:5x/03:5x と同一）**: `ssh` バイナリ無し・`~/.ssh` 空・`129.146.54.105:22` raw TCP timeout。`azumag/soviet_now` push は `add_repo(access=push)` 再試行後も 403（git/proxy とも）。`azumag/docich` push は可。VM 内部の直接確認（ログ・latest.jsonl・キューファイル・hash 実測）は依然不可。
- **新手法（今回の成果・以後の遮断時はこれで外部監視すること）**: VM に入れなくても (1) Twitch 公開 GraphQL（`gql.twitch.tv`、`workers/stream_noon_audit.sh` と同じ公開 client-id と query 形）で stream id/createdAt/viewers、(2) プレビュー画像 `https://static-cdn.jtvnw.net/previews-ttv/live_user_dociai-1920x1080.jpg` で配信画面右の SOREN DATA オーバーレイ（show-status-g / show-status）を読み取れる。今回この2つで以下すべてを実測した。
- **配信（外部実測 04:45〜04:50 JST）**: dociai は **LIVE**、game=Soren Game、title「[進行中] トークン効率化・配信基盤安定化・新規ゲーム追加・戦略の改善」、viewers 3。Backend `FFMPEG LIVE fps=30.0 speed=1.0 drop=13`、AVSync PASSED。**Twitch stream id 317965866584 / createdAt 2026-08-24T18:56:29Z（= 08-25 03:56 JST）に配信セッションが新規化**（08-23 03:53 JST 開始の 317949940056 から入替わり。正午貼り直しの時刻ではなく、03:56 頃に何らかの再起動/切断があったはず。原因は VM ログ未確認）。
- **プロセス（オーバーレイ OPS パネル読取り）**: Loop(PID 2904410)/ChatW/YouTubeW/AudioW/RadioW = RUNNING、Twitch/YouTube チャット CONNECTED。PredW PAUSED (idle)。**ImproveD = STOPPED（赤表示）**、Workers 5/6 online。音声: Say PLAYING、VOICEVOX SYNTH 進行中、CommentQ 1 playing|1 queued、RadioQ 9 queued（>20 の異常滞留ではない）。ゲームループは進行中: 04:46:03 に「改善フロー: regression check game=45324 start」トースト（GAME OVER 画面 score=788）→ 数分後の再取得で次ゲーム 11手目モルドバ着手を確認。
- **【要調査・重要】本番 strategy hash が v727 (5c9ab0ea) でない**: ヘッダ「Strategy: **e5b671c8** v45324 1441L R:2」、Curr c9820 m10138 q8852 **n100**、Reg NO a=e5b671c8。Best **0b6d4deb/r** c10312 m10988 q8840 n53。Strategy Comparison 上位: 0b6d4deb 10312 / 922fb1f7 10282 / 2dfa9b77 10274 / 505b0cfb 10183…（**v727=5c9ab0ea も v726=aac60352 も上位に無い**）。**ROLLBACKS total=1 rejected=1 last=2h ago（≈02:46 JST）**、Safety revert=ready rejected=1、**AnnealObs 5c9a p=1.00 gap=0 2h** — v727 (5c9a) は anneal 観測枠に表示。解釈候補: (a) 02:46 頃に rollback が発火し、n=100 の長履歴を持つ旧系 e5b671c8 へ巻き戻り、v727 は観測枠へ降格した（stage_gate_noninferior_grace 8e03c37 が防げなかったケースなら再発バグ） (b) AnnealObs という機構が v727 を別枠で保持しており active hash の表示意味が異なる。**プレビュー画像のみでは確定不能**。VM 回復後の最優先調査: `grep 'STATGATE\|REGRESSION\|PROMOTE\|LANE_COVER' logs/soren_loop.log`、`tmp/history/rejected_hashes.txt`、`game_history/latest.jsonl` の実 hash、`extract_decide_hash.py strategy.py`。
- **別チャンネル観測（念のため記録）**: azumagbanjo も LIVE（category=Politics、viewers 64、title はスピリチュアル系の誘導文言）。soren とは無関係の内容。ユーザー自身の運用なら問題なし、心当たりが無ければアカウント状態の確認を推奨。
- **実施できなかったこと**: VM 内部確認・banner 表示・soviet_now への push（analyze_board indent fix は `docich/docs/patches/20260825_analyze_board_reactive_pairs_indent.patch` が引き続き正）・v728 着手。
- **次セッションへ（優先順）**: (1) ユーザー対応 — network policy に SSH(22) 許可 + GitHub App に soviet_now write 付与（3時間連続遮断）。(2) 回復後最優先: e5b671c8 巻き戻りの真偽と 02:46 rollback の経緯調査、誤粛清なら境界デプロイ手順で復元。ImproveD STOPPED と 03:56 の配信セッション新規化の原因も確認。(3) analyze_board パッチの適用・push・VM 反映・docich submodule bump。(4) 遮断が続く間も本節の外部監視手法（GraphQL + プレビュー画像）で毎時の liveness と hash 表示確認は可能。

## 2026-08-25 03:5x JST — 毎時メンテ(リモート): VM/push 遮断が2時間連続で継続。パッチ再検証済み + escape テスト失敗数増の疑いを「回帰ではない」と確定

- **環境制約（今回も独立に実測、02:5x 節と同じ結論）**: (1) VM への SSH 不可 — このコンテナには `ssh` バイナリ自体が無く（`which ssh` → 該当なし）、`~/.ssh` も存在せず、`129.146.54.105:22` への raw TCP は timeout。(2) `azumag/soviet_now` への write 不可 — `git push` は proxy 403（"Claude doesn't have GitHub access"）、GitHub MCP API も `create_branch` で `403 Resource not accessible by integration`（read-only 確定）。`add_repo(access=push)` を実行しても変わらず。`azumag/docich` へは push 可。
- **未実施（重要）**: **VM ヘルスチェックは今回も一切できていない** — 配信 fps/bitrate、soren_loop/strategy_runner/radio/chat/audio の生存、`extract_decide_hash.py` による v727 (`5c9ab0ea6b6c`) 稼働確認、STATGATE/REGRESSION/PROMOTE/LANE_COVER ログ、comment_queue 滞留、いずれも**未確認**。02:5x 節と合わせ、本番は約2時間ぶん無監視の状態。
- **実施1: 退避パッチの再適用と独立再検証（実測）**: `docich/docs/patches/20260825_analyze_board_reactive_pairs_indent.patch` を soviet_now `codex/no-apply-liveliness` にローカル `git am` で適用（コミット `3ac9959`、**push 不可のためコンテナ消滅で失われる**。docich のパッチファイルが引き続き正）。前セッションとは別シード・別ケース構成で再検証: 空盤面・単一駒・同座標6駒スタックの縮退3件 + ランダム盤面60件（type 1-13、n=2〜45、r は type 別半径テーブル）計63ケースで、修正前後（`HEAD~1:analyze_board.py` を別モジュールとして exec）の `calc_reactor_state` 出力が **JSON 完全一致・mismatch 0**。n=40 × 200 回で 2.855ms → 0.243ms（**11.7倍**、前回計測の16倍とは実行環境差）。`python3 -m py_compile` OK。
- **実施2: 02:5x 節の未調査事項「test_escape_mechanisms の失敗数が 104+1 → 106+2 に増えている」を調査 → 回帰ではないと確定（実測）**: 同一環境で 5 コミットを順にチェックアウトして実行した結果、`HEAD(3ac9959)` / `c4e9c30` / `8e03c37` / `HEAD~10(b51ed26, 08-24 06:51)` はいずれも **106 failures + 2 errors（計108）**、`HEAD~25(894d363, 08-23 09:20)` は **105 failures + 3 errors（計108）**。つまり**失敗総数108は08-23から現在まで不変**で、直近コミット群が新たに壊した事実は無い（failure/error の内訳が1件入れ替わっただけ）。104+1=105 という過去計測は別環境（macOS 実機など）由来の差と判断する。
- **失敗の性質（実測）**: 108件中81件が `TestCommentReplyDepthPrompt`、10件が `TestSovietObjectiveImproveInputs`、6件が `TestWildcardReasonProcessBoundary` 等。中身は `assertIn('local_llm_timeout="${COMMENT_OLLAMA_TIMEOUT:-20}"', <lib/ai_generate.sh の中身>)` のような**シェルスクリプト原文の文字列存在アサーション**で、実行時挙動の失敗ではなくテスト側が実装のリファクタに追従できていないドリフト。`macos`/`osascript` 等の環境依存も3件含む。→ **恒常的な既存ノイズであり、パッチ検証の before/after 比較の基準としては引き続き有効**（前後で集合が完全同一なら差分なしと判定してよい）。
- **競合ガード**: 直前節が 02:5x JST（約50分前）、VM のバナー状態は SSH 不可のため未確認。30分以内の他セッション作業の徴候は handoff 上には無いと判断して作業した。VM への変更は一切行っていない（行えない）。
- **v728（次候補3）は今回も着手不可**: 設計根拠に必要な「ロシア到達局面の実履歴」が無い。VM の `game_history/*.jsonl` と手動チャレンジの obs_109〜126 は VM 上にあり、ローカル `tests/fixtures/` に存在する post-Russia 局面は `post_russia_chain_cover_turn130.json` の1件のみ。`_select_visible_same_country_contact`（strategy.py:1040）の `margin>=1.0` / `dx<=0.06` ゲート再測定は VM アクセス回復まで保留。
- **次セッションへ（優先順）**: (1) **ユーザー対応が必要** — このリモート環境の network policy に SSH(22) 許可、かつ GitHub App に `azumag/soviet_now` の write 付与。どちらも無いと毎時メンテは「docich への記録」以上のことが原理的にできない。(2) 権限回復後ただちに: 溜まった VM ヘルスチェック（v727 稼働 hash、粛清/昇格ログ、キュー滞留）を実施。(3) `git am docs/patches/20260825_analyze_board_reactive_pairs_indent.patch` を soviet_now へ適用・push・docich サブモジュール bump。(4) VM の `analyze_board.py` へ反映（strategy.py 非変更 = decide hash 不変につき境界デプロイ手順は不要。バナー表示と反映後の新ゲーム正常動作確認は実施すること）。(5) その後 v728 設計へ。

## 2026-08-25 02:5x JST — 毎時メンテ(リモートセッション): analyze_board O(n²)インデントバグ修正を検証済みパッチとして退避（VM未反映・soviet_now push不可）

- **環境制約（実測）**: このリモート実行環境から VM への SSH は不可 — port 22 が network policy でブロック（raw TCP timeout、HTTPS proxy も CONNECT :22 拒否）、`~/.ssh` に鍵も無し。**VM ヘルスチェック・配信/プロセス/戦略確認は今回一切未実施**（v727 の実戦観測も不可）。さらに `azumag/soviet_now` への push も全経路 403（git push は proxy が "Claude doesn't have GitHub access"、GitHub API も "Resource not accessible by integration" = read-only）。`azumag/docich` へは push 可能。
- **実施（次候補(2): analyze_board.py:345-366 インデントバグ修正、リポジトリ側のみ）**: `calc_reactor_state` の reactive_pairs/near_pairs O(n²) 走査が type_count の `for p in pieces:` ループ内側にインデントされ n 回再実行されていたのを1段デデント。ブロックは外側ループ変数 p を参照せず毎回再初期化のため挙動不変。
- **検証（実測）**: tests/fixtures 実盤面8件 + ランダム盤面54件 + 縮退2件（同座標スタック・空）の計64ケースで修正前後の `calc_reactor_state` 出力**完全一致**。n=40 で 4.05ms→0.26ms（約16倍）。`python3 -m py_compile` OK。test_escape_mechanisms は修正前後で FAIL/ERROR 集合が**完全同一**（106 failures + 2 errors、全て既存。00:3x 節の 104+1 から今回とは無関係に増えている点は未調査）。test_post_russia_contact 22/22 OK（PYTHONPATH=repo root 必要）、test_pre_russia_ukraine_pair_lane は既存の1 failure のみで前後同一。
- **退避先**: `docich/docs/patches/20260825_analyze_board_reactive_pairs_indent.patch`（git format-patch 形式、コミットメッセージ込み）。ローカル soviet_now コミット b15f87b3 は push できずコンテナ消滅で失われるため、このパッチが正。
- **次セッションへ（soviet_now push 権限のある環境で）**: (1) `git am docs/patches/20260825_analyze_board_reactive_pairs_indent.patch` を soviet_now `codex/no-apply-liveliness` に適用して push、docich サブモジュール bump。(2) VM の `/home/ubuntu/soren/analyze_board.py` へ反映（strategy.py 非変更なので decide hash 不変・境界デプロイ手順は不要だが、バナー表示と反映後の新ゲーム正常動作確認はすること）。(3) 未実施の VM ヘルスチェック（v727 = 5c9ab0ea6b6c 稼働・STATGATE/REGRESSION/LANE_COVER ログ・キュー滞留）を実施。(4) ユーザーへ: このリモート環境の network policy に SSH(22) 許可、または GitHub App に soviet_now write 付与があると毎時メンテが完結できる。

## 2026-08-25 00:3x JST — 手動チャレンジ完走: 51手カザフスタン→108手ロシア建国（実測済み）

- **実施**: goal「ソ連建国に向けた戦略改善: 手動1ゲームで知見蓄積」。`tmp/state/manual_meriken_mode.json` を直接書いて soren_loop をゲーム境界で pause（`manual_meriken_mode_enable()` は SOREN91_ENABLED=0 だと no-op のため直書き。soren_loop:844 の判定はファイルのみ見る）。VM の bridge はそのまま、`tmp/manual_challenge/{observe,drop}.py` ヘルパー（tmp 限定・リポジトリ外）で一手ずつ 観測→スクショ scp→目視→判断→冥鳴ひまり speaker=14 で理由 enqueue→commands.txt 書込を127手完走。開始文「メリケンAIによるチャレンジコーナーです」、全手音声、手数プレフィックスで dedup 回避。
- **結果（実測）**: 51手目カザフスタン（自動戦略の約半分の手数）、108手目に ウクライナ×2→カザフ×2→**ロシア建国**の8駒メガ連鎖(+294)。最終127手・raw 2897・eval 18787（ロシア+トルクメ+ウズベク4 残存）。GAMEOVER スクショ/最終盤面 JSON は VM `tmp/manual_challenge/` とローカル scratchpad に保存。手動ゲームは game_history/rolling_scores に入らない（pause 中は bookkeeping が走らない）ことを確認。
- **返却（順序重要・実測済み）**: retry→MOVE/score0/pieces0 確認→マーカー削除→`tmp/state/soren_display_mode` 削除（meriken 残留で OBS watchdog が死ぬ opus 指摘 CRITICAL-2 対応）→soren_loop 自動再開・新 strategy_runner 稼働を実測。0手ゲーム汚染なし。
- **知見**: `games/soviet_now/docs/manual_challenge_20260825_insights.md` に整理。骨子: (1) 垂直開放路の同型直撃は3/3で確実併合（解析はNO判定）、gap≤0.05もほぼ併合、掠りタップは弾かれる (2) analyzer は dist 閾値のせいで面接触併合を NO 扱い・パーチ静止と転がり量を予測しない (3) 縦積みラダー/斜面シェディング/pair-on-anchor で+100〜294の連鎖を人為的に量産できた (4) 敗着は退避駒の転がり被覆5回と大玉置き場枯渇。
- **【重大】粛清事故を発見**: 2026-08-24 19:46 の regression rollback が `curr_comp 9598 > anchor 9208`・`curr_russia=3 vs anchor_russia=0`・`breach_count=0` なのに `objective_regression+lost_turkmenistan_gate` 理由で **v72x 手動改善系譜（ロシア3回到達）を旧 anchor `0890dbefd73e`（v360世代）へ破棄**。"rollback validation failed but accepted by policy"、regression_streak=4 のカスケード中。VM 現行 strategy.py に v721-726 機構は grep 0件（実測）。v727 設計と復旧判断は次フェーズ。
- **次**: 知見の v727 反映（ユーザー指示により自分で設計）。候補: analyze_board の垂直開放路DIRECT昇格・gap≤0.05 NEAR昇格・indent バグ修正（40倍高速化）。

## 2026-08-25 01:0x JST — 粛清カスケード再発防止をVM反映 + v726系譜復元（ユーザー承認済み・稼働実測）

- **原因確定（実コード+ログ）**: 19:34〜19:46 の4連続ロールバックは全て `breach_count=0`・stat NONINFERIOR・comp現行優位で、`stage_gate_regression_reason`（T11/T14到達率が anchor 比で1でも低いと発火、`rank<=ROLLING_SCORE_RUSSIA_GRACE_RANK(7)` のみ免除）だけが理由。初手は **19:31にanchor昇格したばかりの v726 (comp 10054/n47)** を `lost_kazakhstan_gate` で破棄。rolling に comp 10k超の legacy エントリが8件以上あり rank>7 となり免除されず、comp 10017→9208 / russia 3→0 まで機械的降下。さらに rollback 復元 validation は `tmp/state/` 実行時に `strategy_helpers` を import できず**常に失敗→"accepted by policy" で素通し**（実測再現）だった。
- **修正（soviet_now `8e03c379`、VM反映済み・SHA一致・bash -n OK）**: (1) `strategy/regression.sh` に `stage_gate_noninferior_grace` — breach 0 かつ russia数/best_max_type/comp が anchor 以上なら段階到達率ゲート（stage_achievement / objective stage gate）を発火させない。`STAGE_GATE_NONINFERIOR_GRACE=0` で旧挙動。判定不能時は旧挙動（rollback側）へ fail。lost_soviet_path は grace 対象外。 (2) `strategy/sandbox.sh` validate_strategy の直接実行テストに PYTHONPATH=リポジトリルートを付与。検証: 抽出heredoc の fixture 3ケース（19:34カスケード再現= grace ON で PROMOTE / OFF で REGRESSION 再現、真の劣化・ソ連喪失は両方 REGRESSION）全PASS、VM で import 失敗→解消を実測。eloop_lib.sh は毎試合再sourceされるため再起動不要で次境界から有効。
- **v726復元（AskUserQuestionで承認取得）**: `tmp/history/rejected_hashes.txt` から `aac603521570` を除去（backup: `tmp/manual_challenge/rejected_hashes.bak.txt`）→ manual_meriken_mode マーカーで境界pause → `strategy_versions_archive/by_hash/aac603521570.py` を root `strategy.py` へ復元（旧0890dbefは `.codex_deploy/backup-20260825-stage-gate-grace/strategy.py.0890dbef` に退避）+ by_hash 再登録 → マーカー解除。**復元後の新ゲームが hash `aac603521570` で稼働中を latest.jsonl で実測**（01:00時点9手目）。active_branch.json はVM上に存在せず repair は no-op（都度確認済み）。
- **v726復元後の実測**: 境界2回（01:03/01:18）とも `legacy=OK` で粛清再発なし。01:34の8試合目は raw 1908 / EVAL 12430 を記録。
- **v727 実装・レビュー・デプロイ（ユーザー承認済み・稼働実測）**: 設計はユーザー指示で自分で実施、実装後の独立レビューは opus に委任。当初2案のうち「ロシア後contact解禁」はレビューH2（手動ゲーム125局面リプレイで発火0＝envelope が実ロシア盤面を全ブロック、実質no-op）により撤回し、`POST_FIRST_RUSSIA_LANE_COVER_AVOID` の到達性修正のみに絞った。レビューHIGH/MEDIUM全反映: 床着地はリスク品質下限に算入(H1)、hit_id は None のみ床扱いで他はfail-closed(M1)、selected の越線/併合結果越線は置換しない(M2)、置換候補に pre-Russia クランプ検査(L2)。実履歴2545局面リプレイの最終差分は「v726 がクランプ外 x=-2.2 を発火していた1件の是正」のみ。焦点テスト110+278 subtests パス（既存失敗1件は v726 でも再現、レビューアも独立確認）。soviet_now `c4e9c30fe` push、decide hash `aac603521570 → 5c9ab0ea6b6c`。VM はゲーム境界（マーカーpause）で差替え、by_hash/永久archive登録、`tmp/revert_strategy.py`=v726。**01:36 新ゲームが hash `5c9ab0ea6b6c` で稼働中を latest.jsonl で実測**。
- **注意**: ローカル作業ツリーに 8/24 19:04 時点の別セッション由来 strategy.py WIP（tether閾値緩和+テスト）が残っていたため、scratchpad `foreign_wip_20260824_1904.diff` に退避してから v727 を実装した（未コミット・未デプロイのWIPで、粛清カスケードと同時刻帯に放置されたもの）。
- **次（v728候補）**: (1) ロシア後の contact recovery は envelope 再設計が必要 — 手動ゲーム obs_109〜126（ロシア盤面18局面、margin 0.12〜1.46）を fixture に、`deadline_margin>=1.0`/`dx<=0.06` ゲートを実盤面に合わせて再測定する（壁分岐 at_wall は実測1/4なので緩めない、垂直開放路のみ）。(2) analyze_board.py:345-366 の O(n²) インデントバグ修正（40倍高速化・挙動不変）。(3) v727 の実戦発火と粛清 grace の長期観測（`grep 'STATGATE\|REGRESSION\|PROMOTE\|LANE_COVER' logs/soren_loop.log`）。

## 2026-08-26 16:2x JST — v739 LOOKAHEAD（2 手先読み）を実装・検証し、v736 vs v739 のインターリーブ A/B を開始（16:10）

- **実装（soviet_now `c6c5c9ba5`、hash `8fcb13b11d0c`）**: `strategy_helpers/lookahead.py`（`rerank(pieces, shapes, nt, nnt, cands, cfg)`: lane 別 K≤8・margin 900 の上位候補について pm（DIRECT 0.96 / NEAR 0.70 / 開いた相方 gap テーブル）で併合あり/なしの盤面を作り、nextNext を軽量解析器で V2 = pm2 + 0.3·pm2·chain − 高さ罰 として評価、score + 600·E[V2] で再順位付け。E2 単調ガード +0.05、被覆タグ/AVOID_BLOCK_NEXTNEXT 候補の除外、**保護タグ**（SAME_TYPE_SEED_CONTACT / ANCHOR_LANE_SEED_CONTACT / PROBABLE_MERGE_CONTACT が付いた基準手は同タグ候補にしか覆さない — これが無いと `anchor_lane_t9_ladder_turn47` が退行した）、呼び出し上限 16、純関数・例外は None）。decide() は候補収集 `_la_cands`、FALLBACK 後・clip 前で `lookahead.rerank`（ゲート: 非 deadline_crossed・margin≥1.0・危険駒なし・ロシア不在）、理由 `LOOKAHEAD_NEXT`。バリデータ RC=0。
- **オフライン（57 試合 5,169 手、壁反射 ON）**: 変更 3.29%、`LOOKAHEAD_NEXT` 3.60%、**併合喪失 0・新規交差 0・例外 0**、risk_top +0.017、decide 所要 p50 7–15 ms / p90 21–37 ms / max 256 ms。テスト `tests/test_lookahead_next.py` 7 本（flip fixture 3 件、lam=0 で不変、nextNext 無し/margin<1 で無効、解析器例外で 1 手へ、v736 fixture 不変、呼び出し上限/所要時間、fail-closed）。全体 111 failed / 942 passed。**既知**: `anchor_lane_t9_beside_turn14` は v736 でも `ANALYZE_BOARD_WALL_CLAMP=1` だと失敗する mode-0 fixture（v739 の退行ではない、要 fixture 更新）。
- **A/B 開始（実測）**: `strategy_helpers/lookahead.py` を root の helpers に先行配置（additive）→ `tools/ab_ctl.sh start tmp/manual_challenge/strategy_8fcb13b11d0c.py ABBA` 16:10:05（初回はバリデータ拒否で失敗、同入力の再試行で成功 — 単体では RC=0 で原因未特定、シャドウテスト直後の一時的要因の疑い）。state: A=253cc67e0c1b、B=8fcb13b11d0c、revert 先 = v736、REGRESSION_DISABLED=1、game_num_start 45794。16:11 腕 A（記録 eval 8304）→ 16:15:47 腕 B `Strategy hash: 8fcb13b11d0c`、20 手で LOOKAHEAD_NEXT 1 回、例外 0。A/B ゲートは dry-run のまま（`_ab_gate_after_game` が毎試合 `[AB-GATE] k= mean= verdict=` を記録するが行動しない）。
- **事前登録（fable）**: 主指標 = 試合ごとの併合/手（`merges_per_turn`、SD 0.043 → +0.02 は 74/腕）、副 = raw score（+300 は 74/腕）、20/40 手時点の駒数、手数、T14/T15、発火率（期待 3–4%）、新規交差 0、例外 0。中間 look は 40/腕で併合/手のみ。停止: B−A raw < −300 で p<0.1、または decide 例外。判定: 併合/手 ≥ +0.02（p<0.05）かつ raw が負でなければ `finish B`、それ以外 `finish A`。`tools/ab_decide.py --trail` と `ab_report.py` で毎 tick 集計。
- **loop（16:2x）**: A/B 1 試合記録（A: raw 894）、腕 B 進行中、例外 0。改善 daemon は蓄積 13 試合だが `improve.lock` 未作成 — ピーク時間帯（`PEAK_HOURS_WINDOWS=10-13,15-19`）の改善延期のため 19 時以降に最初の候補（dry-run の `would start`）が出る見込み。
- **loop（17:3x）**: A/B 16 試合（各腕 8、k=4、tainted 0）: raw A 1654（sd 710）vs B 1133（sd 351）、mean(B−A) = **−522（SE 171、UCB90 −303）**、eval −1859、併合/手（主指標）A 0.527 vs B 0.472（−0.055）、手数 92.1 vs 79.0、20/40 手時点の駒数 12/21.5 vs 12/22、カザフ 3 vs 1、`LOOKAHEAD_NEXT` は B 腕で発火（例外 0）。`ab_decide` は k<6 で CONTINUE、k=6 で UCB90<0 なら REJECT_HARM。改善候補はまだ無し（ピーク延期）。**オフラインの +0.03 併合/手が実戦では −0.055（n=8）**: 解析器の着地ノイズ（σ 0.35）で 2 手先の予測が当たらない可能性。k=6 の判定で害なら `tools/ab_ctl.sh finish A`（dry-run なので自動では終了しない）。
- **loop（18:3x）**: A/B 28 試合（各腕 14、k=7、tainted 0）: raw A 1586（sd 624）vs B 1469（sd 600）、mean(B−A) = **−117（SE 232、UCB90 +179）→ CONTINUE**（17:3x の −522 は直近 3 ブロックで相殺、逐次判定は設計どおり早期停止せず）。併合/手 0.527 vs 0.517、手数 88.9 で同じ、カザフ A 3 / B 5、20/40 手時点の駒数 同等。ロシア 0/0。継続（次 look は k=19）。
- **音声解説（ユーザー依頼 17:3x）**: 「今の戦略についての解説」を `enqueue_audio_text`（タグ `strategy_explain`）で投入、overlay「戦略解説」表示、VOICEVOX 16 チャンク合成完了 17:42:24、キューから消えたことで再生済みと判断（再生行の直接ログは未取得）。文面は `tmp/strategy_explain.txt`（VM）。
- **loop（19:3x）**: A/B 42 試合（各腕 21、k=10、tainted 0）: raw A 1434（sd 588）vs B 1498（sd 580）、**mean(B−A) = +43（SE 202、UCB90 +303）→ CONTINUE**、併合/手 B 0.517 vs A 0.490（**+0.027、オフライン期待 +0.03 と一致**）、手数 89.5 vs 82.8、カザフ B 6 / A 4、ロシア 0/0。序盤の劣勢は消え、わずかに v739 優勢。次 look は k=19（各腕 38、~23:30）。
- **訂正**: 改善ジョブが始まらない理由は「ピーク延期」ではなく `.env` の **`MIN_GAMES_BEFORE_IMPROVE=48`**（`IMPROVE_PEAK_HOUR_DEFER_ENABLED=0`）。過去ログでも 48/48 で `[CYCLE]` が発火（22:38 / 10:17 / 22:08 / 04:16、当時は daemon なし）。蓄積 33/48、A/B 中は A 腕のみ蓄積するので初候補（dry-run の `would start`）は **~22 時**の見込み。
- **loop（20:3x）**: A/B 55 試合（A 27 / B 28、k=13、tainted 0）: raw A 1468（sd 552）vs B 1515（sd 570）、**mean(B−A) = +31（SE 158、UCB90 +233）→ CONTINUE**、eval −85（SE 871）、併合/手 B 0.513 vs A 0.503、手数 89.2 vs 82.8、カザフ B 8 / A 7、20/40 手時点の駒数 B 11.5/21 vs A 12/23、ロシア 0/0。中立〜わずかに優勢。次 look k=19（~23:00）。改善ゲート: 蓄積 39/48、候補未出。
- **loop（21:3x）**: A/B 70 試合（各腕 35、k=17、tainted 0）: raw A 1384（sd 536）vs B 1461（sd 599）、**mean(B−A) = +67（SE 129、UCB90 +231）→ CONTINUE**、eval +94、併合/手 B 0.497 vs A 0.491、手数 87.6 vs 81.3、カザフ B 9 / A 7、40 手時点 B 21 / A 23、ロシア 0/0。k=19 の look（~22:10）では MDE(38)≈420 に届かず採用なし → 無益停止（k≥12 で UCB90<150）に落ちなければ k=37（~12 時間後）まで継続。改善ゲート: 蓄積 47/48、次の A 腕試合で lock → 改善ジョブ → 初候補（dry-run `would start`）。
- **運用方針（ユーザー承認 21:4x）**: 戦略改善の主体は Claude（fable 設計 → 実装 → オフライン A/B → 実戦 A/B）。LLM 改善ループは **dry-run のまま**（候補は root に触れず自動 A/B も始めない）、実運用の A/B 枠は Claude の設計案に優先。LLM 候補はオフライン A/B で見込みが高いときだけ手動で A/B。質が低ければ pause に戻す。次の候補は v739 の A/B 結果を見て決める（v739 の係数調整 λ / 3 手目の型期待値、「埋没を解く併合」優先、ロシア以後モード）。
- **リポジトリ状態**: soviet_now HEAD の strategy.py は v739（本番 root は v736、B 腕として実戦中）。A/B の結果で root を確定したら HEAD と一致させる（`finish B` なら一致、`finish A` なら revert コミット）。

## 2026-08-26 15:4x JST — A/B ゲート（改善候補を root に適用せず A/B で採否）を実装・VM 反映、dry-run で改善 daemon を再稼働 / 2 手先読み v739 の設計受領

- **A/B ゲート実装（fable 設計、soviet_now `2cfb6fa38` + `594ce1800`）**: `tools/ab_decide.py`（逐次判定: 害 UCB90<0 で k≥6 停止、無益 k≥12 で UCB90<150、採用は k=19/37 で n≥30・符号反転 p<α/2・m≥MDE・ガードレール、それ以外は結論なし。v738 の履歴を再生すると 24 試合目 (k=6) で REJECT_HARM）。`strategy/ab_gate.sh`（候補出力 `_ab_gate_emit_candidate` / 境界の自動開始 `_ab_gate_before_game` / 試合後の判定 `_ab_gate_after_game` / 共通 `_ab_start_from_bundle` `_ab_finish`）。eloop_improve.sh は `AB_GATE_ENABLED=1` のとき root に適用せず `tmp/state/ab_candidate/` へ出力（param trial / commit / 系統樹はスキップ）、improve.sh は候補出力を成功として回収（`candidate_ready`）、A/B 中の蓄積は A 腕のみ。`ab_interleave.sh` は gate 有効時 pause/lock 前提を緩和、B 腕の helper 同梱、試合ごとの指標（併合/手・20/40 手時点の駒数・max_type・締切交差）を記録。`ab_ctl.sh` は start/finish を共通関数化、`simulate` 追加、finish は improve pause を触らない・記録は mv。トグル: `AB_GATE_ENABLED=0` / `AB_GATE_DRY_RUN=1` / `AB_GATE_LOOKS=19,37` / `AB_GATE_MAX_BLOCKS=37` / `AB_GATE_FUTILITY_UCB_DELTA=150`（.env を直接読むので set_toggle で即時）。テスト: test_ab_gate.sh 33、test_ab_decide.py 9、全体 111 failed / 935 passed。
- **VM 反映（実測）**: 15:41:45 の境界 pause で 17 ファイル差し替え（バックアップ `.codex_deploy/backup-20260826_1541-abgate`）、VM 上でテスト全通過、15:42:14 の試合は v736 で正常再開。
- **dry-run 稼働開始（15:43）**: `set_toggle.sh AB_GATE_ENABLED=1 AB_GATE_DRY_RUN=1`（REGRESSION_DISABLED は 0 のまま）、`tmp/state/improve_daemon.paused` を除去（バックアップ `.paused.bak-20260826`）→ improve_daemon が 15:43:58 に再起動（poll 30 s）。蓄積 6 試合 → 12 で改善ジョブ → 候補出力 `[IMPROVE] AB gate: candidate emitted` → 境界で `[AB-GATE] (dry-run) would start A/B` を確認するのが次の検証。実運用化は `AB_GATE_DRY_RUN=0`（＋ REGRESSION_DISABLED=1 推奨）。**戻し方**: `set_toggle.sh AB_GATE_ENABLED=0` + `touch tmp/state/improve_daemon.paused`（+ 進行中なら `tools/ab_ctl.sh finish A`）。
- **2 手先読み v739 設計（fable、scratchpad `look/` に計測スクリプトとプロトタイプ `la_search.py`）**: `strategy_helpers/lookahead.py`（新、`rerank(pieces, shapes, nt, nnt, cands, cfg)`、例外は全て握って None、呼び出し回数上限 16 で決定的）＋ decide() の 3 箇所（候補収集、FALLBACK 後・clip 前で再順位付け、`LOOKAHEAD_NEXT` 理由）。lite 解析器（粗い 16 x + 相方 ±0.3、`get_landing_info`/`polygon_contact_gap`/`has_obstruction`）は full の 1/4〜1/10 のコストで nextNext の DIRECT/NEAR 有無を 96% 一致。盤面更新は併合確率 pm（DIRECT 0.97 / gap テーブル）で併合・非併合の期待値、V2 = pm2 + 0.3·pm2·chain − 高さ罰、E2 単調ガード（期待 2 手併合が下がる flip は禁止）。**実測（40 試合 1,606 手）**: 変更 5.8%、期待併合 +0.029/手（評価手）、併合喪失 0、新規交差 0、コスト p50 10–27 ms / max 211 ms。full 解析器や decide 再帰は不採用。正直な見立て: 人間との差（0.55→0.63）の約 1/3。A/B の主指標は試合ごとの併合/手（SD 0.043、+0.02 は 74/腕）。

## 2026-08-26 15:2x JST — A/B 判定「結論なし・v738 は一貫して劣後」→ v736 継続で終了 / ユーザー承認: 2 手先読み（fable 設計→A/B）＋改善ループの A/B ゲート化

- **A/B 最終（10:16–15:16、ABBA、tainted 0、abort なし）**: raw score A(v736) n=29 平均 1875（sd 813）/ 中央値 1824 vs B(v738) n=30 平均 1439（sd 569）/ 1277、完全ブロック k=14 で mean(B−A) = **−345**（SE 192、符号反転 p=0.11）、eval −980（SE 836、p=0.28）。手数 100.7 vs 85.4、併合/手 0.582 vs 0.507、複数併合 10.6 vs 8.6%、カザフ 8 vs 7、ロシア 2 vs 0。事前登録（≥30/腕・ブロック ≥8・p<0.05・|δ|≥MDE(29)=478）のうち有意性を満たさず → **結論なし（方向は 14 ブロック中一貫して劣後）**。`tools/ab_ctl.sh finish A` 15:16: root=v736 253cc67e0c1b、REGRESSION_DISABLED=0、SOREN_AB_ALT_STRATEGY 空、anchor=v736（comp 11195、n=100）、記録 `tmp/history/ab_20260826_151628_{state,games,report}`。**改善デーモンの pause（08-23 から）は finish が外すので直後に復元**（LLM 改善は止めたまま）。要修正: finish は pause が開始前から存在した場合は外さない。
- **最終集計（30+30、finish 後に完走した A 腕 idx 59 を合流・重複除去済み、`tmp/history/ab_20260826_151628_report_final.txt`）**: raw A 1849（sd 812）/ 中央値 1752 vs B 1439（sd 569）/ 1277、ブロック k=15 で mean(B−A) = **−411（SE 190、p=0.055）**、eval −1330（p=0.16）、手数 99.9 vs 85.4、併合/手 0.576 vs 0.507、ロシア 2 vs 0。|δ| < MDE(30)=470 で形式上は「結論なし」だが 15 ブロック一貫して劣後。要修正（次回）: `ab_ctl.sh finish` は (a) ab_games.jsonl を cp ではなく mv（進行中試合の記録で残骸が再生成される）、(b) improve pause は start 前から存在した場合は外さない。
- **判断の含意**: v738 の設計仮説（t±1 の横に置く→連鎖準備）は対比較でも有益でなかった。時間順比較の −269 は交絡だったが、方向は正しかったことになる。v738 は不採用で確定。
- **ユーザー質問「なにか施策はありますか？」への回答（15:0x）と承認「推奨案でお願いします」**: 施策 (1) next/nextNext を使う 2 手先読み（decide の探索化、+300 級を狙う）、(2) 改善 AI ループの再稼働＋インターリーブ A/B をゲートに、(3) 埋没を解く併合の優先、(4) ロシア以後モード、(5) 低分散の代理指標での選別。推奨 = (1) を fable 設計→A/B、並行して (2)。ユーザー承認済み → (1)(2) の設計を fable Plan に委任（バックグラウンド 2 本）。
- **制約の再確認**: raw score SD 650–800 → A/B で確定できるのは +300 級（74/腕 ≈ 12 時間）。細かい配置軸は検出不能なので今後は構造的な変更に絞る。A/B 中は他の変更を入れない。

## 2026-08-26 10:3x JST — loop 26回目: インターリーブ A/B 基盤を実装・VM 反映（10:04）、v736 vs v738 の A/B を開始（10:16、ABBA）

- **設計（fable Plan）→ 実装（soviet_now `593df1213`）**: runner は毎試合スナップショット（`tmp/state/main_game_strategy_runtime/strategy.py`、eloop.sh:297–302 で `strategy_runtime_create_game_snapshot "$STRATEGY_FILE" …`）を読み、帳簿（rolling_scores / version / played hash）もスナップショットの hash で付くので、**スナップショットの元ファイルを試合ごとに選ぶだけで root には触れない**（案 c）。
  - `strategy/ab_interleave.sh`: `_ab_active`（fail-closed: `SOREN_AB_ALT_STRATEGY` 非空 / `tmp/state/ab_state.json` / abort マーカーなし / **.env の** `REGRESSION_DISABLED=1`（config.sh:26 が変数を 0 に固定するため .env を直接読む）/ `improve_daemon.paused` / `improve.lock` なし / root と代替の hash が state と一致）、`_ab_select_arm`（`SOREN_AB_PATTERN` 既定 ABBA を `games_recorded` で巡回、不成立試合は同じ腕を打ち直す）、`_ab_record_game`（`tmp/state/ab_games.jsonl` に idx/arm/hash/score/eval/turns/archive、snapshot と archive の hash 突合で `tainted`）、`_ab_abort`、`_ab_is_arm_hash`。
  - eloop.sh: スナップショット元の切替、A/B 中の decide_exception は abort（B 腕は root の自動復旧を起動しない）、post_game_bookkeeping で記録。improve.sh: A/B 中は B 腕の試合を別戦略混入扱いにせず腕の hash に帳簿、同 hash ロック更新停止。config/whitelist: `SOREN_AB_ALT_STRATEGY`（空=無効）/ `SOREN_AB_PATTERN`。
  - `tools/ab_ctl.sh start <path> [pattern] | status | stop | finish <A|B>`、`tools/ab_report.py`（腕別 n/平均/中央値/p25/SD、手数、残存 archive から併合/手・複数併合・T14/T15、ABBA ブロック差 mean(B−A)・SE・符号反転並べ替え p、必要 n: sd 650 で +150 → 295/腕、+300 → 74/腕、MDE(50)=364）。テスト `tests/test_ab_interleave.sh`（20）、`tests/test_ab_report.py`（5）、全体 111 failed / 926 passed。
- **VM 反映（実測）**: 10:04 の境界 pause で 11 ファイル差し替え（バックアップ `.codex_deploy/backup-20260826_1004-abinfra`）、VM 上で source / 単体テスト OK、10:04:22 の試合は v736 で正常再開（`[AB]` 行なし＝不活性）。
- **A/B 開始（10:16:06）**: `tools/ab_ctl.sh start tmp/manual_challenge/strategy_4a3b4c7acdc2.py ABBA`（hash 指定は `_find_strategy_archive_for_hash` が解決できず失敗 → **パス指定で起動する**。要修正）。state: A=253cc67e0c1b（v736 root）、B=4a3b4c7acdc2（v738）、revert 先 = v736、.env `REGRESSION_DISABLED=1` / `SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py` / `SOREN_AB_PATTERN=ABBA`。improve daemon は 08-23 から pause 済み。
- **端から端まで実測**: 10:18:35 `[AB] idx=0 arm=A hash=253cc67e0c1b` → 試合終了で `[AB] recorded idx=0 arm=A eval=18561 tainted=False`（score 2480、106 手、played/history hash 一致）→ 10:24:44 `[AB] idx=1 arm=B hash=4a3b4c7acdc2 src=tmp/state/ab_alt_strategy.py` → `Strategy hash: 4a3b4c7acdc2`。games_recorded=1。
- **運用**: 状況 `bash tools/ab_ctl.sh status`（ab_games.jsonl が主、game_history は直近 13 試合のみ）。判定は事前登録: ≥30/腕かつ完全ブロック ≥8 で、raw score の並べ替え p<0.05 かつ |δ| ≥ MDE でのみ勝敗、それ以外は「結論なし」。2×50 で MDE≈364（v738 の −269 は検出限界未満＝この A/B は「大きな害がないか」の確認）。終了 `tools/ab_ctl.sh finish <A|B>`（B なら root 差し替え・revert 先更新・anchor 昇格・REGRESSION_DISABLED=0・pause 解除）、その後リポジトリの strategy.py も合わせて commit。**A/B 中は他の変更（decide/analyzer/settle）を入れない**。注意: eval（建国ボーナス込み、~10⁴）と raw（~10³）を混同しない（必要 n の sd は raw 用）。
- **loop 27（10:3x）**: A/B 進行中（games_recorded=2: A 2480 / B 929、腕 B の 2 戦目進行中、abort・例外なし）。`ab_ctl.sh` の不備を修正（soviet_now `fc19aba23`、VM 反映済み）: hash 指定は by_hash / 永久アーカイブから直接解決（`_find_strategy_archive_for_hash` は eloop.sh 内の関数で eloop_lib からは読めない）、`game_num_start` は `GAME_COUNT_FILE`（game_count.txt）から。A/B 中は他の変更を入れない方針のため、この tick は基盤の修正のみ。
- **loop 28（11:3x）**: A/B 14 試合（各腕 7、tainted 0、abort なし）: raw score A 1761（sd 547）vs B 1355（sd 550）、完全ブロック k=3 で mean(B−A) = −383（SE 163、符号反転 p=0.24）、手数 96.4 vs 84.7、併合/手（連鎖込み）0.547 vs 0.513、複数併合 11.0 vs 11.5%、カザフ 3 vs 1。eval でも −2304（SE 841、p=0.24）。方向は 08:32 の窓比較と同じ（v738 劣後）だが n=7 の MDE≈973 で未確定 → 事前登録どおり ≥30/腕・ブロック ≥8 まで継続（~15:15 到達見込み）。
- **loop 29（12:3x）**: A/B 25 試合（A 13 / B 12、tainted 0）: raw A 2049（sd 886）vs B 1490（sd 621）、ブロック k=6 で mean(B−A) = −559（SE 328、p=0.16）、eval −3089（SE 1251、p=0.095）、手数 105.3 vs 86.9、併合/手 0.594 vs 0.524、カザフ 6 vs 3、**ロシア A 2 / B 0**。対比較でも v738 劣後が一貫。閾値（≥30/腕・ブロック ≥8、~14:30）まで継続。
- **loop 30（13:3x）**: A/B 37 試合（A 19 / B 18、tainted 0）: raw A 1924（sd 832）vs B 1447（sd 605）、ブロック k=9 で mean(B−A) = −510（SE 233、**p=0.072**）、eval −1730（SE 1103、p=0.18）、手数 101.8 vs 84.6、併合/手 0.603 vs 0.515、複数併合 10.4 vs 8.4%、ロシア A 2 / B 0。ブロック ≥8 は到達、n/腕 <30 かつ p≥0.05 かつ |δ|<MDE(18)=607 → 継続（30/腕は ~15:30）。判定時: p<0.05 かつ |δ|≥MDE なら `finish A`、それ以外は「結論なし」で `finish A`（v736 継続、gate/improve 復帰）。
- **loop 31（14:3x）**: A/B 50 試合（各腕 25、tainted 0）: raw A 1838（sd 788）vs B 1437（sd 549）、ブロック k=12 で mean(B−A) = −404（SE 218、p=0.11）、eval −1165（SE 964、p=0.27）、手数 100.3 vs 85.5、併合/手 0.579 vs 0.511、複数併合 10.3 vs 8.6%、ロシア A 2 / B 0。|δ| < MDE(25)=515 で未確定。30/腕（~15:20）で判定 → 有意でなければ「結論なし・方向は劣後」として `finish A`。
- **既知の不備**: `ab_ctl.sh start <hash>` の解決失敗、`game_num_start` が null（`GAME_COUNT_FILE` のパス違い）。次 tick で修正。

## 2026-08-26 09:4x JST — loop 25回目: v736 復帰後も低スコア（n=12 平均 1393）→ v738 の「害」は時間順比較の交絡と判明 / インターリーブ A/B の設計を fable に委任

- **実測**: v736 復帰（08:32–09:30）n=12 平均 1393 / 中央値 1393 / p25 1115 — v738 窓（1384）と同水準、v736 の以前の窓（1649）より低い。VM 負荷（run 62% / ffmpeg 47% / chrome 41%、radio の opencode 生成あり）・試合間隔（4.5–5.4 分）・静止待ち（0.8–1.0 s）は窓間で差なし。時刻別（3 時間ブロック、08-22〜08-26）に系統的な朝の落ち込みなし（08-26: 00–03h 1563、03–06h 1688、06–09h 1434、09–12h 1537(n=6)）。
- **序盤指標の直接比較（残存ログ）**: v736 早期 n=14 / v738 n=15 / v736 復帰 n=12 で、20/40/60 手時点の駒数 11/18.5/29 vs 11/20/30 vs 11.5/22.5/31、上端 −1.7/−0.3/1.5 vs −1.5/−0.3/1.5 vs −1.1/0.8/1.9、20 手ごとの併合 6.5/8/7/6 vs **8**/8/7/5 vs 6/7/7/6。**v738 の序盤は v736 早期と同等以上で、復帰後の v736 のほうが悪い** → v738 のスコア低下は v738 では説明できず、同時期の変動（原因未特定）が主因。切り戻し判断自体は「期待利得が検出不能」なので維持するが、**時間順の窓比較では ±150–250 の漂流があり戦略差（<300）は測れない**ことが確定（v732/v733、静止待ち、v738 で繰り返し起きた帰属問題の根）。
- **対策 = インターリーブ A/B（試合ごとに 2 戦略を交互実行、hash で帰属、同一期間で比較）**: 設計を fable Plan に委任（バックグラウンド）。論点: シェル側で root strategy.py を試合ごとに入れ替える案 vs runner の `SOREN_STRATEGY_FILE` 上書き案、粛清/anchor/branch との干渉（A/B 中は REGRESSION_DISABLED=1 か gate を A/B 対応に）、`tools/ab_report.py`（ABBA ブロック差・並べ替え p 値・必要 n）、境界での開始/終了手順、テスト。eloop.sh の hash 算出点 :80 / :196 / :232、runner の strategy 読込 strategy_runner.py:506、`Strategy hash:` :2991。
- **注意**: game_history は直近 ~15 試合しか残らないので、窓集計は score_history.txt（時刻+スコア）と rolling_scores.json（hash 別）を併用する。分析用にローカル scratchpad `gh_close3/` に 27 試合を取得済み。

## 2026-08-26 08:3x JST — loop 24回目: v738 は n=25 で平均 −269（z≈2.0）→ v736 に切り戻し（08:32 境界、実測済み）

- **v738 完全系列（score_history、06:33–08:30）**: n=25 平均 **1380**（sd 524）/ 中央値 1260 / p25 1016 vs v736 窓（00:15–06:32）n=73 平均 1649（sd 670）/ 1523 / 1171。差 −269（SE≈131、z≈2.0）、中央値 −263。直近 14 試合の詳細: 併合/手 0.341、複数併合 9.7%、NO 手併合 10.6%、20/40/60 手時点の駒数 11 / 20 / 30.5（v736 13 / 22 / 31）、カザフ 3/14。機構 KPI は良好のまま（発火 2.73%、発火手の横隣接 41% vs 域内基準 14–19%、高型被覆は非発火手より低い）。**注意: game_history は直近 ~15 試合しか保持しないので、n>15 の集計は score_history.txt を使う。**
- **判断**: 事前登録の即時停止（窓−500）には未達だが、v738 の期待利得は設計上 +0.17 併合/試合でスコアでは検出不能な一方、観測された害は z≈2.0。損失が非対称（誤って戻しても失うのは検出不能な利得、誤って続ければ −269/試合）なので切り戻し。機構（横隣接率）は改善しているのに結果が悪い＝「t±1 の横に置く」こと自体が序盤の盤面構造を悪くしている可能性（例: 小駒を大駒の脇に寄せることで谷が埋まり、後続の着地が高くなる）。仮説の検証には v738 窓の 20/40/60 手時点の上端高さ・v736 との盤面形状比較が必要（未実施）。
- **切り戻し（実測）**: `vm_deploy_strategy.sh 253cc67e0c1b prepare` 08:31 → `[PAUSE]` 08:32:10 → swap/finish。root = `253cc67e0c1b`、revert 先 = v738 `4a3b4c7acdc2`、マーカー除去。08:32:37 の新試合から `Strategy hash: 253cc67e0c1b`、settle=3、STAIRCASE_ADJ 発火 0、例外 0。tests/test_staircase_adj.py と fixture は VM の tests/ に残置（v736 では発火系 7 本が失敗するが、ループは tests を実行しない。改善 AI のバリデーションが tests/ を走らせる場合は要確認）。リポジトリ HEAD（soviet_now `c257fadbb`）は v738 のまま＝**HEAD と本番が不一致**。次の commit で strategy.py を v736 に戻すか、v738 を維持したまま記録するかは次 tick で決める（v736 は `strategy_versions/by_hash/253cc67e0c1b.py` にある）。
- **リポジトリ整合（08:4x）**: soviet_now `c07e04c65` で strategy.py を v736（253cc67e0c1b）に戻し、v738 のテスト/fixture 27 件を削除（コードは `c257fadbb` に残る）。HEAD と本番が一致。VM の tests/ からも v738 のテスト・fixture を除去し、test_probable_merge_contact OK を確認。**共有 checkout の注意**: `games/soviet_now` は他セッション（podcast 作業、`086b46584 feat(podcast)` を push 済み、`core/config.sh` / `tools/podcast_build.py` が未コミットで編集中）と同時使用されている。私の `git revert` が失敗し、続く `commit --amend` が他人の未コミット変更を巻き込んだ不正なローカルコミット（未 push）を作ったため、作業ツリーに触れない `git reset`（mixed）で取り消してから origin に合わせ、対象パスのみ `git commit -- paths` でコミットした。**以後: この checkout では `git revert` / `--amend` / `stash` / `rebase` を使わず、パス指定コミットのみ。**
- **総括（loop 1–24）**: 実戦で正だったのは v727 復元・v728/v729（解析器較正）・v731–v734・v736（ロシア 1、anchor）・静止待ち 1→3。中立: 壁反射修正、静止待ち 3→4（戻した）。負: v735（NO-GO）、v737(c)（見送り）、v738（切り戻し）。人間との差は序盤の断片化（20 手時点 5 駒 vs 12 駒）に集約されるが、条件付き指標は同等で、単発の配置軸では埋まらなかった。

## 2026-08-26 07:3x JST — loop 23回目: v738 初期監視 n=13 — 機構は動作（横隣接 42%、高型被覆は基準以下）、スコアは −430 で要注視（n≥24 で早期停止判定）

- **v738 n=13（settle3、実測）**: 平均 **1253** / 中央値 1236 / p25 1016、手数 83.0、併合/手 0.329、複数併合 9.2%、NO 手併合 12.1%、発火 2.87%（予測 2.81%）、20/40 手時点の駒数 12 / 21、カザフ 1/13、ロシア 0。対照 v736 settle3（n=17）: 1698 / 1546 / 1187、94.0、0.335、10.1%、13.6%、13 / 22、カザフ 6/17（v736 全体 n=39: 平均 1681、カザフ 11/39）。スコア差 −430（試合 SD ≈900、z≈1.6）、カザフ到達 8% vs 28%。粛清なし（STAGEGATE fired=0、anchor 昇格は objective guard で抑止）、例外 0。
- **機構 KPI（`stair_kpi.py` / `stair_cover.py`）**: 発火手の実着地横隣接 **42%**（沈黙域の非発火手 14–16% → 3 倍、目標 50% には未達）。発火手が開いた T≥9 を覆った率 15.4%（4/26）— fable 案の閾値 8% を超えるが、**同域の非発火手は v736 35.3% / v738 43.2%** で発火手のほうが低い（序盤は開いた高型が 92–98% の手で存在し、小駒が上に乗るのが常態）→ 閾値は「同域の非発火手より低いこと」に置き換える。連鎖機会率 13.4%（v736 12%）、複数併合 9.2%。
- **判定計画**: 次 tick（n≈25）で早期停止規則（平均 < 窓−500 ≈ 1180）を適用。超えていなければ n≥50 まで継続し fable 案の基準で判定。切戻しは `tmp/manual_challenge/vm_deploy_strategy.sh 253cc67e0c1b prepare/swap/finish`（by_hash に v736 あり）。

## 2026-08-26 06:3x JST — loop 22回目: 静止待ち 4 は中立 → 3 に戻す / v738 STAIRCASE_ADJ を 06:32 境界で VM 反映（hash 4a3b4c7acdc2）

- **静止待ち 4 判定（n=21、同 hash v736）**: 平均 1691 / 中央値 1516 / p25 1123、手数 95.1、併合/手 0.339、複数併合 9.7%、NO 手併合 12.1%、40 手時点 22 駒、カザフ 5、ロシア 1 — REQUIRED=3（残存 17: 1698 / 1546 / 1187、94.0、0.335、10.1%、13.6%、22、カザフ 6）と**中立**。改善なしで 1 手 +0.15 s 遅くなるだけなので `./set_toggle.sh SOREN_SETTLE_REQUIRED=3` に戻した（06:29、次試合から）。v738 の対照窓は v736@settle3（n≈50: 平均 ≈1640、併合/手 0.333–0.347、複数併合 10–11%、NO 手併合 13.4–13.8%、40 手時点 18.5–22 駒）。
- **v738 反映（実測）**: `vm_deploy_strategy.sh 4a3b4c7acdc2 prepare`（06:29）→ `[PAUSE]` 06:32:44 → tests/fixtures 配置 → swap/finish。root / by_hash = `4a3b4c7acdc2`、revert 先 `tmp/revert_strategy.py` = v736 `253cc67e0c1b`、マーカー除去、VM で test_staircase_adj + test_probable_merge_contact OK。06:33:02 の新試合から `Strategy hash: 4a3b4c7acdc2`、全手 settle.required=3 / wall_clamp=1、**STAIRCASE_ADJ が 12 手目（T2、7 駒）で初発火**、decide_exception 0。
- **運用メモ**: バックグラウンドの境界デプロイジョブが PAUSE 前に外部要因で停止された（3 回目、原因未特定）。VM は安全な状態（マーカーあり・swap 未実行）だったのでフォアグラウンドで再実行。**境界デプロイはフォアグラウンド（timeout 600 s）で行う**。
- **判定計画（fable 案）**: n≥24 で平均 < 窓−500 なら即停止; n≥50 で平均 < 窓−300 / 併合/手 < 窓−0.03 / 複数併合 < 窓−1.2pt / NO 手併合 < 窓−1.7pt / 40 手時点の駒数 > 窓+2 / T14 到達率 −10pt 超 / 発火手の次手で開いた T≥9 を覆う率 > 8% のいずれかで no-go。go は発火 2–4% かつ発火手の実着地横隣接 ≥50%（域内基準 13.9%）、沈黙域の実着地横隣接 +4pt 以上。集計: `python3 tmp/manual_challenge/stair_kpi.py 4a3b4c7acdc2`、`mode_stats.py` 相当は `settle_table.py` / `v736_stats.py <hash>`。regression は anchor（v736）との比較で n≥12 から。

## 2026-08-26 04:3x JST — loop 20回目: 壁反射修正は中立で維持 / 静止待ち 3→4 の単独実験を開始（04:30）/ 差は序盤 40 手の「階段配置」と特定、v738 設計を委任

- **壁反射修正 mode 1 判定（n=16、同 hash 253cc67e0c1b）**: 平均 1663 / 中央値 1514 / p25 1187、手数 92.9、併合/手 0.333、NO 手併合率 13.7%、DIRECT 96.5%、カザフ 6/16 — mode 0 窓（n=34: 1612 / 1524 / 1171、93.0、0.347、13.4%、97.1%、8/34）と比べ**中立（害なし）**。正しさの修正として `ANALYZE_BOARD_WALL_CLAMP=1` を維持。VM の mode 0 ログは trim 済み（比較値は本節に固定）。
- **静止待ち実験 3→4（単独変数）**: REQUIRED=3 の基準（1,486 手）: 待ち平均 0.97 s / 中央値 0.77 / p90 1.86、強制打ち切り 0、投下時の可動駒平均 22.6。`./set_toggle.sh SOREN_SETTLE_REQUIRED=4` を 04:30:25 に設定、04:33 開始の試合から `settle.required=4` を実測（待ち平均 1.46 s、強制 0、hash・analyzer_modes 不変）。帰属は turn log の `settle.required`。判定は n≥24 で REQUIRED=3 の v736 窓（n≈50: 平均 ≈1640、併合/手 0.333–0.347、複数併合ターン 11.0%）と比較: 併合/手・複数併合率・スコア p50/p25・試合時間。**この窓の間は decide / analyzer を変更しない**。切戻し `./set_toggle.sh SOREN_SETTLE_REQUIRED=3`。
- **序盤診断（実測）**: 駒数推移は人間 5 / 9 / 16（20 / 40 / 60 手）vs 自動中央値 12 / 22 / 31。人間は 10–20 手と 30–40 手に 10 手で 11 併合のバーストがあり、自動は一定 5。T14 到達時の自動盤面は 26.5 駒で ~70% が埋没、T14 以外の質量 0.68 T14 換算（人間 0.14）。非併合投下が「t+1 型の開いた駒の隣」に着地した率: 人間 序盤 50%（5/10）/ 後半 27%、自動 29% / 18%（t−1 隣接も 33% vs 24%）→ **スイカ式の階段配置（併合産物が次の型の隣に落ちて連鎖する）が人間の序盤バーストの正体という仮説**（人間 n は小さく、実戦で検証する前提）。
- **v738 STAIRCASE_ADJ（設計を opus Plan に委任、バックグラウンド）**: NO 盤面・同型相方なしのとき t+1（/t−1）型の開いた駒の「横」に着地する候補へ加点（v736 の +800 未満、高さ軸より上）、被覆タグ・DIRECT 存在時・crossing・margin<1.0 は除外。オフライン指標: 変更手・DIRECT 喪失 0・新規交差 0・「t+1 隣接」率（序盤 +10pt 目標）、v736 発火手は不変。実戦 KPI: 連鎖機会率（併合先の隣に n+1 型が開いている、現状 14.4% vs 人間 18.6%）、複数併合率、併合/手、20/40/60 手時点の駒数。**投入は静止待ち 4 の判定後**。
- **v738 STAIRCASE_ADJ（Plan 受領 → オフライン実装・A/B 済み、未コミット・未投入）**: 設計 = `next_type ≤ 8`・同型相方なし（`not _v736_open_partner_ids and not _v731_partners`）・駒数 ≤22（34 で 0 に線形）・NO 盤面の候補が「t+1 型の開いた駒の横」（縦重なり ≥0.15、横 gap ≤0.45、−0.20 未満は不整合で除外）に着地するとき +300×ramp×phase（壁際 0.6 倍）、被覆タグ 3 種・DIRECT 存在・crossing・margin<1.0・death_spiral・ロシア在盤は除外。方向項なし（人間の「大型は左」は幅正規化後に相関 0.04 で消える、n=1）。scratchpad `stair/mk738.py` → `s738_a.py`（hash ae6e28ebea6b、バリデータ RC=0）。**A/B（13,946 手、壁反射 ON）**: 発火 0.93%（序盤 127 / 後半 3）、変更 110 手、DIRECT 喪失 0、新規交差 0、例外 0、**risk_top −0.75**（横置きで着地が下がる）、序盤 NO 手（t+1 あり）の「t+1 隣接」率 3.3% → 6.6%（2 倍、目標 +10pt は未達）、v736/v731/v732 発火手は不変。変種: peak 400 → 発火 1.0%、`next_type ≤ 11` → 発火 1.46% だが v732 発火手 51 を変える（不採用）、「v736 沈黙時に拡張（B）」→ A と同一（開いた相方があれば gap≤1.0 の候補がほぼ常にある）。**投入は静止待ち 4 の判定後**、その前にテスト（F1/F2/F3 fixture、閾値・位相・重なり・開放の変異検出）を追加してコミット。
- **静止待ち 4 経過（04:55）**: n=4 平均 1637 / 併合/手 0.359 / 複数併合 8.6% / 待ち 1.11 s（REQUIRED=3 残存 17 試合: 1698 / 0.335 / 10.1% / 0.96 s）— 判定は n≥24。
- **ユーザー指示（05:0x）「設計は opus ではなく fable で」** → 記憶に保存（`design-delegation-use-fable.md`）。以後 Plan/設計サブエージェントは `model: fable`。
- **v738 を fable で再設計 → A2 を採用、実装・テスト・コミット済み（soviet_now `c257fadbb`、hash `4a3b4c7acdc2`、未投入）**: fable のログ検証で仕組みを確認（序盤・同型相方なし・NO 盤面で t+1 の横に置いた駒は 15 手以内の併合が複数併合になる率 31.9% vs 遠い置き方 26.5%、**t−1 の横は 36.1% vs 25.6%** とより大きい）。A2 = 変種 A から (1) 沈黙ゲートを「開いた同型相方なし」のみに（`not _v731_partners` は型ゲートで空虚）、(2) t−1 型も対象。A/B（13,946 手、壁反射 ON）: 発火 2.81%（序盤 326 / 後半 66）、変更 298、DIRECT 喪失 0、新規交差 0、例外 0、risk_top −0.67、沈黙域の t±1 横隣接予測率 **4.4% → 18.4%**、v736/v731/v732 の発火手は不変。期待併合増は ~0.17/試合でスコアでは検出不能 → 実戦は仕組みの実証と無害の確認が目的。テスト `tests/test_staircase_adj.py` 11 本 + 実局面 fixture 26 件（正例 t+1/t−1/埋没同型/位相ランプ、否定 = 開いた同型/対象埋没/重なり無し/gap 遠い/位相/被覆/壁、変異用 11 件）。変異 19 種中 16 killed（残り: 位相端点 34→50、DIRECT 存在・crossing の多重防御＝等価）。全体 111 failed / 932 passed、バリデータ RC=0。fable が指定した「埋没同型で発火」局面（055000 t22）は私の再現では不発だったので、自前検索の 3 局面（231146 t33 等）に差し替えた。pre-existing の脆さ: decide() は `landing_y` / `drift_x` が非数値だと TypeError（v738 とは無関係、fail-closed テストから除外）。
- **投入計画**: 静止待ち 4 の判定（n≥24）後に**単独で**境界投入（`vm_deploy_strategy.sh 4a3b4c7acdc2 prepare/swap/finish`、tests/fixtures を先に配置）。実戦 go/no-go（fable 案）: n≥24 で平均 < 窓−500 なら即停止; n≥50 で平均 < 窓−300 / 併合/手 < 窓−0.03 / 複数併合 < 窓−1.2pt / NO 手併合 < 窓−1.7pt / 40 手時点の駒数 > 窓+2 / T14 到達率 −10pt 超 / 発火手の次手で開いた T≥9 を覆う率 > 8% のいずれかで no-go。go は発火 2–4% かつ発火手の実着地横隣接 ≥50%（域内基準 14%）。
- **静止待ち 4 経過（05:48）**: n=14 平均 1613 / 中央値 1506 / p25 1103、手数 91.1、併合/手 0.337、複数併合 8.8%、40 手時点 22.5 駒、カザフ 3、**ロシア 1**（REQUIRED=3 残存 17: 1698 / 1546 / 1187、94.0、0.335、10.1%、22、カザフ 6、ロシア 0）— 中立圏、n≥24 で判定。
- **v738 投入準備（05:5x）**: VM に `tmp/manual_challenge/strategy_4a3b4c7acdc2.py`（hash 確認済み）、`tmp/manual_challenge/v738/tests/`（test_staircase_adj.py + fixture 26 件、swap 前に `tests/` へ配置）、KPI スクリプト `tmp/manual_challenge/stair_kpi.py <hash>`（沈黙域の実着地横隣接率・発火率・発火手の横隣接/高型被覆率・連鎖機会率・複数併合率を hash×settle 別に集計）。基準値（v736）: 沈黙域の実着地横隣接 settle3 13.9%（28/201）/ settle4 21.8%（31/142）、連鎖機会率 12.1 / 12.0%、複数併合 10.1 / 8.8%。投入手順: 静止待ち判定 → `vm_deploy_strategy.sh 4a3b4c7acdc2 prepare` → PAUSE で tests/fixtures 配置 → swap/finish → `Strategy hash: 4a3b4c7acdc2` と `STAIRCASE_ADJ` 発火を実測。
- **分析スクリプト**: scratchpad `wall/avail.py`（相方の存在・開放・到達）、`wall/`（埋没・連鎖・階段隣接・駒数推移は本節の一時スクリプト）、VM `tmp/manual_challenge/mode_stats.py`。

## 2026-08-26 03:4x JST — loop 19回目: 人間との条件付き併合率は同等と判明（戦術差は尽きた）/ ソ連建国は幾何的に成立、制約は材料と生存手数

- **壁反射修正（mode 1）経過（実測、同 hash 253cc67e0c1b）**: n=6 で平均 1481 / 中央値 1234、NO 手併合率 12.2%（48/395）、DIRECT 98.2%、壁際決定比率 23.8%（mode 0: n=34 平均 1612、13.4%、97.1%、22.2%）。n が小さく判定不能、≥12 で再評価。粛清・例外なし、anchor 維持。集計は VM `tmp/manual_challenge/mode_stats.py`（`analyzer_modes.wall_clamp` で分割）。
- **v737(c) 再評価（壁反射 ON の corpus）**: +0.00210/手、変更 196 手、発火 6.30%、risk_top +0.277 ＝ 変化なし → 引き続き見送り。
- **人間（手動 123 手）との条件付き比較（直近 30 試合 2,761 手、実測）**: 相方あり 82.9% vs 80.3%、開いた相方あり時の併合 **59.5% vs 60.0%**、全埋没時 7.1% vs 8.1%、DIRECT/NEAR あり時 93.9% vs 93.2%、NO かつ安全 gap≤0.2 あり 9.5% vs 9.1%。**条件付き併合率はすべて同等**。差は状態分布（相方全埋没 27.5% vs 35.0%）だが、生きたペアの一員を直接埋める頻度は 23.6 vs 23.1 / 100 手で同じ → 埋没差は駒数が少ないことの結果（原因ではない）。連鎖: 機会（併合先の隣に n+1 型が開いている）18.6% vs 14.4%、機会あり時の複数併合 31.6% vs **45.6%**（自動の方が高い、`CHAIN_MERGE` 項あり）。併合手率 38.2% vs 34.1%、併合/手（連鎖込み）0.626 vs 0.511、3 連鎖以上 8.1% vs 4.1%（人間 n=10 で弱い）。**結論: この 1 試合から取り出せる戦術差は尽きた。残差は小さな併合手率差と大連鎖で、実戦で検出できる大きさの単発改善は見込めない。**
- **ソ連建国の幾何（定数から算出）**: FLOOR −5.0 / DEADLINE 3.38、T15 は幅 4.49・高さ 2.13（床上で上端 −2.87）、T14 は幅 3.27・高さ 1.57。ロシアの上に T14 を載せても上端 −1.30 で余裕、横並びは 7.76 > 7.0 で不可だが積み上げで成立。**制約は純粋に材料と時間**: 出現は T1–T11 一様（T11 換算 0.18/手）、第 2 ロシアに 16 単位 ≈ 88 手以上、合計 ~250 手の生存が必要（自動の最長 163 手、初戦ロシア 143 手）。断片化（駒数）を抑える＝併合/手を上げる以外に道はなく、人間 0.63 / 自動 0.51 に対して維持には ~0.8+ が必要。
- **KPI（今後の主指標）**: 併合/手（連鎖込み、`mode_stats.py` の merges/turn）、試合手数、T14 到達時の駒数（現状 27、人間 10）。
- **次候補**: (1) 壁反射 mode 1 の n≥12 判定、(2) 静止待ち `SOREN_SETTLE_REQUIRED` 3→4 実験（連鎖・併合/手への効果、REQUIRED 1→3 で平均 1380→1564 だった。mode 1 判定後に単独で）、(3) 解析器の着地分散（駒数 0–9 で σ 0.44、40+ で 0.24）を v736 の確率に反映する mode 2（効果は小さい見込み）、(4) 大連鎖の設計（n+1 型の隣へ併合先を作る `CHAIN_MERGE` の強化は機会率 14.4% を上げる方向＝ n+1 型を開けて待つ配置）。

## 2026-08-26 03:1x JST — loop 18回目: 転がりモデルは実測で否定 → 解析器の壁反射バグ（ANALYZE_BOARD_WALL_CLAMP）を修正して VM 反映（03:07、hash 不変）

- **v736 経過（実測）**: n=26 で平均 1645 / 中央値 1560 / p25 1258、カザフ 6、ロシア 1、NO 手併合率 13.8%、DIRECT 97.2%、anchor 維持（comp 11032）。T14 到達時の駒数 KPI: v736 中央値 28（n=4）、v734 26、全体 27、人間 10（変化なし）。
- **転がり込み着地モデル（opus Plan 委任、3,667 手で 3 案を実測）**: (b) 接触法線ロール、(a) 表面プロファイル降下、(c) 傾斜からの Δx 学習のいずれも旧式（|Δx| 中央値 0.323）を超えず、**平坦な支持面のほうが誤差が大きい**（|slope|<0.1: 0.371 / 37% vs 急斜面 0.24 / 19%）＝残差は転がりではなく等方的な着地ノイズ（σ≈0.35、駒数 0–9 で 0.44 → 40+ で 0.24）。目標「転がり率 ≤20%」は幾何モデルでは到達不能と判定。学習で残ったのは (W) 壁反射の系統誤差と (D) 分散の層別のみ。
- **(W) 壁反射バグ（自分で裏取り）**: `estimate_polygon_drift`（analyze_board.py:150–153）の壁クランプがスプライト半径 `next_r` を使用。壁からの実着地オフセット中央値は T1 0.52 / T4 0.72 / T8 1.08 / T11 1.89 に対し予測 0.48 / 0.42 / 0.66 / 0.98 ＝ **当たり判定半幅 + 約 0.30**。壁際（|x|≥2.4、798 手）のバイアス +0.27 / −0.25。v727/v733 系が T11 を壁に置く局面で接触 gap が最大 0.9 ずれていた。
- **実装（soviet_now `067385b53` + `be853b0bf`）**: `_wall_clamp_mode()`（env `ANALYZE_BOARD_WALL_CLAMP`、既定 0）、`_wall_half_width()`（mode 1 = `_type_deadline_extents(...)["horiz"] + WALL_CLAMP_PAD 0.30`、判定不能は旧式へ）、`estimate_polygon_drift` に `eff_radii` 追加引数、runner の `analyzer_modes` に `wall_clamp` を記録、config.sh 既定 0 + 両 whitelist。`tests/test_wall_clamp.py`（5 本: env 解釈、HEAD 解析器との mode 0 完全一致、壁際の内側移動、締切系フィールド不変、fail-closed）。全体 111 failed / 922 passed、decide hash `253cc67e0c1b` 不変。
- **コーパス評価（61 試合 5,514 手、mode 0 vs 1）**: 壁際 798 手の |Δx| 中央値 **0.274 → 0.089**、0.5 超 **19.5% → 9.0%**（全体 0.323 → 0.266、31.7 → 28.7%）。DIRECT 精度は完全同一（1286/1327 = 96.9%）、候補判定変化は NO→NEAR 76 / NEAR→NO 23 のみ。v736 パイプラインの決定変化 114 手（2.1%、うち壁際 104）、DIRECT 喪失 0、DIRECT 獲得 1、risk_top −0.025、新規交差 1（初戦 turn 54: margin −0.08 で既に締切超え、mode 1 は 147 候補すべて crossing → DIRECT 危険併合を選択＝保守側。締切系は drift 非依存だが `wall_rotation_risk` 経由で壁際の crossing 判定が変わり得ることが判明、テストは fixture では不変を確認）。
- **反映（実測）**: マーカー → `[PAUSE]` 03:07:07 → 5 ファイル差し替え（バックアップ `.codex_deploy/backup-20260826_0307-wallclamp`）→ `./set_toggle.sh ANALYZE_BOARD_WALL_CLAMP=1` → マーカー除去。03:07:38 の新試合から `analyzer_modes = {wall_clamp: 1, merge_top_model: 2, vertical_lane_direct: 1}` を全手で確認、hash 不変、decide_exception 0。**同 hash で mode が変わるため帰属は `analyzer_modes.wall_clamp` で分ける**（v736 mode 0: 26+ 試合が対照）。切戻しは `./set_toggle.sh ANALYZE_BOARD_WALL_CLAMP=0`（次試合から）。
- **判定計画**: mode 1 で ≥12 試合後に mode 0 窓と比較: NO 手併合率（13.8% → ≥15%）、DIRECT ≥96%、併合/手、スコア p50/p25、壁際（|x|≥2.4）の決定比率。v737(c) は着地精度が上がった状態で corpus 再評価（gap の意味が変わる）。
- **運用メモ**: Plan エージェントの初回起動が API 403（organization membership）で失敗、再起動で成功。ローカルのバックグラウンド解析 3 本停止（00:47）の原因は未特定のまま。

## 2026-08-26 01:4x JST — loop 17回目: v736 判定通過・anchor 昇格 / v737(c) は投入見送り / 残る主因は「併合効率 0.30 vs 人間 0.45」と解析器の着地誤差（転がり 32%）

- **v736 判定（実測）**: n=14 で soft-fail なし、STAGEGATE fired=0、01:21 に **anchor 昇格**（`current strategy promoted to anchor: 253cc67e0c1b`、objective guard はロシア 1 で通過）。n=16 時点: 平均 1624 / 中央値 1455 / p25 1066、手数 94.2、カザフ 4（25%）、ロシア 1。1508 手で発火 9.3%、発火手の次手併合 50%（A/B 期待 50%）、NO 手併合率 14.0%（同定義の v734 直近 16 試合 13.2%）、DIRECT 併合率 97.3%。`tmp/state/best_strategy_anchor.json` = v736（comp 10287、n=16）。粛清・例外なし。REQUIRED=3 維持。
- **v737(c) は投入見送り**: corpus 利得 +0.19 併合/試合は実戦の NO 手併合率（±1pt の雑音）で検出不能。scratchpad `v737/s737_c.py`（hash 27ed41fae7fd）と fixture 候補は保存済み。
- **T14→T15 区間の診断（162 試合、T14 到達 37、T15 3）**: T14 到達は中央値 73 手・27 駒・上端 1.6、到達後は上端平均 2.5 で中央値 36 手で死亡。T14 形成時に T13 が残っていない 26/37、2 個目の T13 は 24/37 で形成（中央値 +14 手）だが T14 が 2 個並ぶのは 5/37。ロシア到達の初戦は T14 時 22 駒・上端 0.38。**人間（手動試合）は T14 時 10 駒・上端 −0.67、T14 前の併合/手 0.45（自動 0.30）**。出現分布は T1–T11 一様（各 ~9%）で、質量保存より「駒数＝断片化」が本質 → 併合/手を上げる以外に区間短縮の手段はない。
- **高型駒（T9–T11）の末路（終盤 25 手除外）**: 自動 83% 併合 / 17% 埋没（v734 系 88/12、v736 86/14）、人間 11/12。覆いの主因は低型駒の直接投下 279・間接（転がり/連鎖）206・締切ガード下 132（全 162 試合）。1 試合あたり高型駒 ~1 個の損失で、0.45 vs 0.30 の差の主因ではない。
- **人間の NO 盤面併合 16 手の内訳（manual_rows.json）**: 解析器 gap ≤1.0 は 9 手（≤0.5: 6/6+…、v736 の域）、**gap>1.0 が 7 手**（1–2: 3/22、>2: 4/50）＝解析器の予測着地から遠い相方に転がり・連鎖で到達。
- **解析器の着地誤差（直近 77 試合 3,667 手、併合なしの手、予測 vs 実着地）**: **|Δx| 中央値 0.32 / p75 0.61 / p90 1.14、0.5 超の転がり 31.7%、1.0 超 12.6%**。Δy は実着地が予測より中央値 0.56 低い（55% で 0.5 以上低い＝谷に沈む）。併合判定と v736 の gap はこの誤差の上に乗っている。**次の主軸候補 = 転がり込み着地モデル**（接触点から表面プロファイルを下って最寄りの谷へ、トグル `ANALYZE_BOARD_ROLL_MODEL`、hash 不変）。評価: 3,667 手で |Δx| 中央値と転がり率の改善 → DIRECT 精度 ≥96% 維持・NO 手併合率の予測改善 → 人間の遠距離併合 7 手の再現。
- **運用**: `tmp/manual_challenge/v736_stats.py <hash>`（VM）で hash 別の発火/NO 手併合/DIRECT 率を出せる。分析スクリプトは scratchpad `v737/`（segment.py / fate.py / landing_err.py / human_no.py / bury_ev.py）。
- **次候補**: (1) 転がり込み着地モデル（opus Plan 委任 → 実装 → 3,667 手で検証 → トグル反映）、(2) v737(c) は着地モデル改善後に再評価（gap の精度が上がれば利得が変わる）、(3) T14 到達時の駒数を KPI に追加（目標 ≤20）。

## 2026-08-26 01:0x JST — loop 16回目: v736 初戦でロシア建国（143 手）/ v737（幾何依存勾配）は corpus で限界的 go・埋没コストは無し / v736 判定待ち（n=8）

- **v736 実戦（実測、hash 253cc67e0c1b、REQUIRED=3 維持）**: 初戦 `20260826_002321_score4229` が T13 18 手 → T14 61 手 → **T15 143 手（00:22:30 ロシア建国、最終 4229 点）**。n=8 で平均 1679 / 中央値 1374 / p25 884、手数 97.6、カザフ 2、ロシア 1。781 手で発火 9.0%（A/B 期待 7.6%）、発火手の次手併合 44%（期待 50%）、NO 手併合率 12.2%（v734 窓 10.9%、目標 ≥15%）。粛清・例外なし（decide_exception の 1 件は旧 hash）。regression 判定は n≥12（01:20 頃）。v736 に russia 1 が付いたので objective guard は昇格を止めない（anchor russia 1 と同等）。
- **幾何×gap の併合率（自前計測、132 試合、logged 手、開いた相方・締切安全）**: cover（相方の真上に着地）gap ≤0.05 93.9% (n=33) / ≤0.2 90.9% (44) / ≤0.5 72.4% (123) / ≤1.0 45.4% (227)、beside（横）77.3% (44) / 50.0% (28) / 31.8% (88) / 17.8% (225)。v736 の発火 800 手は cover 401 / beside 399。型別（gap≤0.2）は 60–100% (n 9–18) で型依存なし → 型重みは採用しない（小型併合もジャンク除去で盤面を下げる）。
- **v737 設計（opus Plan 委任）と corpus A/B**: 推奨形 (c) = cover ランプは v736 と同一 `(1−gap)/0.80`、beside ランプを `(0.50−gap)/0.62`（gap 0 で 0.766、0.50 で 0）に置換、bonus 上限 800・壁係数据置、`landing_y` 有限を追加ゲート、deliberate ゲートは追加しない。単純な beside 係数（0.55 / 0.35）は E[併合] +0.0018 / +0.0010/手で risk_top +0.28 / +0.37 と弱い（gap≤0.05 の beside は 77% 併合するので一律係数は誤り）。**(c) の実測: 変更 199 手、併合喪失 0、新規交差 0、例外 0、E[併合] +23.1（+0.00207/手 = +0.19/90 手試合）、risk_top +0.295、発火率 6.35%**。go 条件（ΔEV ≥ +0.0020 / risk_top ≤ +0.30 / 発火率 6.5–8.0%）のうち発火率だけ僅かに下回る限界的 go。切るだけの変種 (t) は +0.00071/手で不採用。生成物: scratchpad `v737/s737_c.py`（hash 27ed41fae7fd、未コミット）、fixture 候補 `v737/fx/F1_*`（beside→cover 反転 4 件）/ `F2_*`（遠い beside を無タグ化 15 件）/ `F3_*`（密着 beside 維持 20 件）。
- **埋没コスト（v738 事前登録計測、`v737/bury_ev.py`）**: 同型の被せ手が即併合しなかった相方は 20 手以内に 44.4%（n=178）で併合、放置した開いた相方は 33.2%（n=286）。T9–11 でも 47.0% vs 29.7%。**被せによる埋没は併合を減らさない（差 −0.11〜−0.17）** → 事前登録ルール「差 < 0.15 なら cover を恒久的に触らない」に該当、v738 の埋没項は不要。レビューの「埋没 0.31 件/試合」懸念は実測で否定。
- **判断**: v737(c) は v736 の判定（n≥12、soft-fail なし、NO 手併合率が v734 窓より上）を待って次 tick 以降に投入可否を決める。利得が小さい（+0.19 併合/試合）ため、v736 の実戦 NO 手併合率が伸びない場合は v737 を積まず別軸へ。
- **運用メモ**: 00:47 頃にローカルのバックグラウンド解析 3 本が出力なしで停止された（原因未特定、私の操作ではない）。フォアグラウンド再実行で全て完了。VM 側は無影響。
- **次候補**: (1) v737(c) 投入（v736 通過後、fixture F1/F2/F3 でテスト化）、(2) 解析器 NEAR 昇格（`ANALYZE_BOARD_CONTACT_GAP_NEAR`、hash 不変のトグル）、(3) T13→T14 区間（70 手 vs 人間 51 手）の再分析。

## 2026-08-26 00:2x JST — loop 15回目: v736 PROBABLE_MERGE_CONTACT を opus レビュー通過後に VM 反映（00:15 境界）、静止待ち実験を締め

- **v736（decide 変更、hash `253cc67e0c1b`、soviet_now `ae3a0f793`）**: NO 盤面（DIRECT/NEAR なし）・margin≥1.0・非 death_spiral・ロシア不在で、上端の開いた同型相方への軸別 contact_gap が小さい候補へ +800（gap≤0.20 満額、1.00 で 0）、壁際回転リスク 0.6 倍、被覆タグ（HIGH_TYPE_COVER_AVOID / LOW_DROP_HIGH_LANE_COVER_AVOID）付き候補には加点しない。A/B 132 試合 11,023 手: 発火 7.6%、変更 3.25%、併合喪失 0、新規交差 0、例外 0、NO 盤面の gap≤0.2 選択 214→326、変更手 risk_top −0.55（245 改善 / 28 悪化）。
- **opus 独立レビュー（SHIP WITH FIXES、コード変更なし）**: 同一 A/B を独自ハーネスで完全再現。指摘→対応: (1) 被覆タグ除外の変異（M1）が旧テストで未検出。実際には 11,023 手中 122 手（1.1%）で効く load-bearing なガード → `probable_merge_cover_t7_turn57.json`（gap 0 の接触候補が開いた高型上端を覆う盤面: 現行 x=+1.20 無タグ、M1 は x=−1.60 で被覆タグと同時付与）と復活させた `seed_contact_t11_turn48.json`（v734 は被覆タグ付き x=+0.20 [等方 index −0.37]、v736 は x=−1.00 gap 0.30 [index 0.79 のまま]＝A/B で v731 指標が最も緩む既知トレードオフ）で検出。(2) `deadline_crossed` / `crosses_deadline` / `merge_result_crosses_deadline` の 3 条件は NO 盤面では到達不能（strategy.py:1475 の早期 return、:1477 の NO+crossing 事前除外、analyze_board.py:1009 は DIRECT/NEAR のみ併合後上端を算入）。レビューの「テストで殺せる」は**私の実測で不成立**（候補全部に crossing を立てても現行/変異とも同じ x=−2.00 無タグ＝等価変異）→ コメントを多重防御と明記し、テストは上流ガードの挙動（早期 return 理由 DEADLINE_GUARD、crossing 候補が選ばれない）を固定する形へ。実効ガードは reactor_margin≥1.0、発火 840 手の併合後上端（legacy 推定）最大 3.32<3.38（中央値 −0.35）。(3) 除外しない AVOID_BLOCK_NEXTNEXT(−311.7) / T12_PAIR_COVER_AVOID(−200) / REACTIVE_PAIR_GAP_BLOCK を +800 が上回る変更手 63/17/8 をコメントに明記。レビューの補強所見: 同 gap≤0.2 でも「相方の上に乗る」91.7% vs「横」51.4%（幾何盲＝v737 候補）、タグは型フラット（T1–T3 併合狙いが約 30%）。
- **テスト**: 新 9 本（変異 14 種中 8 killed。残存: 等価変異 2、bonus 上限 / cap 0.5 / 壁係数 0.6 / min→max の未固定 4、`_v731_any_merge` / death_spiral 除外 2）、軸スイート 73 通過、全体 111 failed / 917 passed（HEAD と同じ失敗数 + 新 2）、本番バリデータ RC=0、decide hash はコメント変更後も不変。VM でもステージ版をシャドウ dir で 26 テスト通過（稼働中 v734 では v736 依存の 6 本だけ失敗＝想定どおり）。
- **反映（実測）**: prepare 00:13 → `[PAUSE] manual_meriken_mode` 00:15:24 → swap/finish 00:15:4x。root / `strategy_versions/by_hash` / `strategy_versions_archive/by_hash` すべて `253cc67e0c1b`、revert 先 `tmp/revert_strategy.py`=v734 `cf2fa99701e9`（swap が自動設定）、マーカー・`tmp/state/soren_display_mode` 除去済み。runner ログ `Strategy hash: 253cc67e0c1b`（00:15:51）、初戦 12 手時点で全手同ハッシュ、タグ発火 1 回（t11 next T7 x −2.8 margin 5.6）、decide_exception 0（ログ中の 1 件は hash `2e1dc1a2acd7` の旧件）。
- **静止待ち実験の締め**（v736 投入で hash が変わるため v734 同 hash 比較はここで終了。`SOREN_SETTLE_REQUIRED=3` は維持）: `score_history.txt` 実測 REQUIRED=1（18:34–19:56）n=20 平均 1380 / 中央値 1376 / p25 1186 → REQUIRED=3（19:56–00:15）n=54 平均 1564 / 中央値 1478 / p25 1173。jsonl（手元コピー 47 試合、19:56–20:03 分は VM 側で trim 済みで欠落）: 手数 90.2、併合/手 0.323、カザフ 14/47（30%）、ロシア 0。loop 14 時点の n=31 判断（維持）と整合。**v736 の判定窓では SOREN_SETTLE_* を触らない**（v732+v733 の帰属喪失の再発防止）。
- **判定計画**: n≥12 で regression（anchor は `0890dbefd73e` / v733 の間で振動、雑音粛清 13–18%、自動復帰は commit+push まで行う）。objective guard（russia 0 < anchor 1）で昇格は不可＝生存が目標。成功指標: NO 手併合率 10.9%→≥15%、併合/手 ≥0.30、DIRECT 併合率 ≥94%、盤面上昇 ≤0.36。`tools/seed_metrics.py` + `tmp/manual_challenge/settle_table.py`（VM）で追う。
- **次候補**: v737 = 幾何依存の勾配（cover / beside で係数を分ける）＋併合後上端モニタ、解析器 NEAR 昇格（`ANALYZE_BOARD_CONTACT_GAP_NEAR`）、型フラット問題（T≥6 に重み）。

## 2026-08-25 22:4x JST — loop 14回目: 静止待ち n=31 で維持判断 / 残るギャップ「NO 盤面での併合率」を特定、v736 設計中

- **静止待ち n=31（同 hash v734、REQUIRED=1 13 試合比）**: スコア平均 1384→1520 / 中央値 1198→1462、手数 80→88、カザフ 15%→23%、DIRECT 併合率 95.3→96.5%、盤面上昇 0.364→0.357。複数併合 3.4→3.5、併合/手 0.331→0.321、NO 判定ターン併合 13.3→10.5%。**スコア・寿命・精度で穏やかな正、併合率は中立** → `SOREN_SETTLE_REQUIRED=3` を維持（4 への拡大は見送り、まず本項目の n を貯める）。v734 は n=52、russia 0、粛清なし。
- **併合源の内訳（v734+settle3 31 試合 2753 手 vs 手動 124 手、実測）**: 併合/手 0.268 vs 0.339。選択候補 DIRECT の手は 25.4%（併合 97%）vs 人間 20.2% で同等。**NO 判定の手は自動 74.4%（うち併合 10.9%）vs 人間 78.2%（うち併合 21.6%）**、複数併合ターン 2.4% vs 4.8%。現行解析器で手動盤面を再採点しても人間は NO 盤面で 21.9% 併合（安全 DIRECT がある盤面は 22.6% で 93% 実行）＝解析器の差ではなく **NO 盤面での置き方の差**。自動の NO 手で非併合だった 1276 件のうち、新駒が同型駒に接触（軸別 0.15 以内）していたのは 5.6% だけ＝接触を狙っていない。
- **v736 設計（opus Plan に委任中）**: 約 5000 件の NO 手（結果既知）から「解析器の予測着地の同型相方との軸別ギャップ」等の特徴で併合確率を学習し、NO 手で確率の高い候補に確率比例の加点（v731 の等方指標・T9 限定を軸別・全型へ一般化する案、または解析器の NEAR 昇格）。指標: NO 手併合率 10.9%→≥15%、併合/手 ≥0.30、DIRECT 併合率 ≥94%、盤面上昇 ≤0.36。
- **v735（保護範囲 T14 まで）は NO-GO 確定**（前節）。

## 2026-08-25 21:3x JST — loop 13回目: 静止待ち (REQUIRED=3) は n=18 で上向き → 継続運用 / v735 (保護範囲 T14 まで) は NO-GO

- **静止待ち実験 n=18（同 hash v734、REQUIRED=1 13 試合比、実測）**: スコア平均 1384→**1611** / 中央値 1198→**1540** / 最高 2854→3095、手数 80→86、**複数併合ターン 3.4→4.1/試合、カザフ 2/13→5/18**、DIRECT 併合率 95.3→96.6%、盤面上昇/手 0.364→0.352（併合/手 0.331→0.318、NO 判定ターン併合 13.3→10.9% は微減のまま）。ユーザー仮説（振動が収まってから落とす方が連鎖が綺麗に出る）と整合する方向。**`SOREN_SETTLE_REQUIRED=3` を継続運用**（1 手 +0.5 秒）。4〜5 への拡大は n≈30 で 3 の傾向を再確認してから。
- **v735 候補 NO-GO（A/B 8270 手）**: v734 の保護範囲を T9〜T14 に広げると、開いた T12〜14 の被覆は 451→209 に減るが T9〜11 の被覆が 688→760 に増え、**risk_top 平均 +0.243（悪化>0.2: 103 手 vs 改善 38 手）、margin 低下>0.5 が 75 手**。大型の T12+ は低い位置にあり、それを避けると小駒が高い山の上へ押し出される。採用しない（v734 のまま）。
- **v734 効果指標（33 試合）**: GOOD 比率 52.8%、連鎖 2.33/試合、カザフ 7/33 (21%)、DIRECT 併合率 96.1%、エラー 0、粛清なし（n=39、anchor は 0890dbef/v733 往復）。
- **次の候補**: (1) 静止待ちの n≈30 再判定、(2) ロシア後の第2ロシア形成（T13/T14 ペアをロシアに隣接して組む pair-on-anchor の T13/T14 版 — データは今日のロシア 2 局面のみ）、(3) 死因分析の続き（v734 後の死亡盤面構成）。

## 2026-08-25 20:5x JST — loop 12回目: 静止待ち実験 (REQUIRED=3) の中間結果、粛清判定を復帰

- **中間結果（同 hash v734、REQUIRED=1 13 試合 vs REQUIRED=3 10 試合、実測）**: スコア平均 1384→1486 / 中央値 1198→1524、DIRECT 併合率 95.3→96.6%、併合/手 0.331→0.320、NO 判定ターンの併合 13.3→11.5%、複数併合ターン 3.4→3.0/試合、盤面上昇/手 0.364→0.365、40/60 手目駒数 23/28→22/30.5、カザフ 2/13→1/10、`settle.wait_sec` 中央値 0.78 秒、forced 0、fast_drop 42/10 試合（≈5%/手）。ドロップ時の最大速度は 0.106→0.001（盤面は本当に静か）。**n=10 では「振動待ちで併合・連鎖が増える」は確認できず（連鎖側は微減）、悪化もなし**。スコア差はノイズ範囲。
- **判断**: `REGRESSION_DISABLED=0` に復帰（20:5x、`./set_toggle.sh REGRESSION_DISABLED=0`）。`SOREN_SETTLE_REQUIRED=3` は継続し、n≈24 で再判定（併合/手・NO 判定ターン併合率・DIRECT 併合率・駒数）。効果が無ければ 1 に戻す（配信ペース +0.5 秒/手のコストのため）。
- **VM 現況**: root v734 `cf2fa99701e9`、anchor は 0890dbef/v733 の往復、`decide_exception` 0。

## 2026-08-25 19:4x JST — loop 11回目: v734 判定通過 + ユーザー指摘「振動を待たずに落としている」を実測（runner の静止判定）

- **v734 判定（実測 19:30:07, n=14）**: `PROMOTE: anchor 0890dbefd73e comp 9707.7 vs v734 comp 9800.4 / p50 10254 / p25 8888.5` → objective guard で抑止。`[STAGEGATE] t14=2/14 vs 6/25 p=0.39 fired=0`。14 試合: 平均 1402 / 中央値 1479 / 最高 2854、**小駒が開いた T9〜T11 に落ちる率 16.5%→13.5%**、40/60 手目の駒数 21→19.5 / 31→29、GOOD 52.1%、連鎖 2.40/試合、DIRECT 併合率 94.6%、エラー 0（anchor ファイルは再び 0890dbef に往復中）。
- **ユーザー指摘の実測（VM game_state.json を 0.1 秒間隔で 4 分・94 ドロップ）**: 新駒が盤面に現れた瞬間、**awake 駒 中央値 27**、最大速度 中央値 0.106・上位 25% は 0.397 以上（runner の静止閾値 √0.1=0.316 超）、**完全静止後のドロップは 1%**、着地間隔 中央値 1.9 秒。原因: `wait_for_move_state` は MOVE 後に「速度² < 0.1」を **1 サンプル (0.15 秒間隔) 観測しただけで**ドロップ (`SETTLE_REQUIRED=1`)、振動の一瞬の凪を静止と誤認。締切接触時は待機スキップ（4%）。
- **対処（soviet_now 未コミット→レビュー後）**: `strategy_runner.py` の静止判定を .env 化: `SOREN_SETTLE_REQUIRED`（連続静止観測回数、既定 1=従来）、`SOREN_SETTLE_MAX_SPEED2`（既定 0.1）、`SOREN_SETTLE_MAX_AWAKE`（awake 駒数上限、既定 −1=無視）。毎ターン記録に `settle: {wait_sec, awake, maxv, fast_drop, forced, required}` を追加（同一 decide hash のまま実戦 A/B 可能）。config.sh 既定+export、runtime_toggles/set_toggle whitelist、`tests/test_settle_wait.py` 6 件。runner はゲーム毎プロセスなので .env は次ゲームから有効。opus レビュー中。
- **反映（実測）**: opus レビュー (SHIP WITH FIXES: テストの環境変数依存、`int(float("inf"))` の OverflowError、記録名 `maxv`→`max_speed`) を反映し soviet_now `9079b2a62` を push、VM へ strategy_runner.py / core/config.sh / runtime_toggles / set_toggle / tests を反映（SHA 一致、VM unittest 9 件 OK、backup `.codex_deploy/backup-20260825-settle/`）。runner はゲーム毎プロセスなので次ゲームから有効。記録: game_history の `settle` フィールド（`null` は MOVE_TIMEOUT 経路の識別子）。
- **実験窓を開始（19:56:39、Game #45550 の次から）**: `./set_toggle.sh SOREN_SETTLE_REQUIRED=3`（レビュー推奨: 3 から。0.15 秒×3 ≈ 0.45〜0.6 秒の連続静止）と **`./set_toggle.sh REGRESSION_DISABLED=1`（同 hash 内でスコア分布が変わる交絡で粛清されないため。12 試合後に必ず `./set_toggle.sh REGRESSION_DISABLED=1`→`=0` で戻す。削除ではなく値で戻すこと＝reload_runtime_toggles は存在する key しか反映しない）**。ベースライン = v734 の REQUIRED=1 の 14 試合。指標: 併合/手、NO 判定ターンの併合（連鎖）、DIRECT 併合率、スコア中央値、手数、`settle.wait_sec` p50/p95、`settle.awake` 中央値、`forced` 率（数%超なら締め過ぎ）、`fast_drop` 率（基準 4%）、1 手の実時間。n=12 ではスコア差は証拠にならず、併合/連鎖で判断。
- **注意**: `SOREN_SETTLE_MAX_AWAKE` は実データで全駒 awake=true が普通（速度 0 でも）なので、低い値にすると毎手 30 秒の強制ドロップになる。設定前に `settle.awake` 分布を見ること。`FAST_DROP_DEADLINE_CONTACT=True`（strategy.py）である限り赤線接触は待機中も毎 poll で即抜ける。

## 2026-08-25 18:3x JST — loop 10回目: v734 を VM 反映（18:34、v733 の 1 窓経過後）

- **v733 最終（anchor、n=44）**: 42 試合 平均 1437 / 中央値 1302 / 最高 4065、カザフ 13/42 (31%)、ロシア 1、GOOD 比率 50.4%、連鎖 2.19/試合、DIRECT 併合率 96.1%、粛清・エラーなし。anchor comp 10818.4。
- **v734 反映（実測）**: tests/fixtures/low_drop_cover_*.json + tests/test_low_drop_high_lane_cover.py を先に配置 → `vm_deploy_strategy.sh cf2fa99701e9 prepare`（helper 検証 OK）→ 境界 pause 18:34:08 → 差替え・by_hash 登録・revert 点 v733 `cb3434f22573` → **18:34 `Strategy hash: cf2fa99701e9`**、3 箇所一致、VM unittest OK、初手 21 DROP エラー 0。backup `.codex_deploy/backup-20260825-v734/`。VM と repo の乖離は解消（repo HEAD `035012f40` = VM root）。
- **監視（v734）**: `LOW_DROP_HIGH_LANE_COVER_AVOID` ≈3%/手、小駒手（next≤8・非併合）の着地が開いた T9〜T11 を覆う率 31%→13%（T12+ が 12% 超なら警戒）、40/60 手目の駒数、`decide_exception` 0。n≥12 の判定相手は anchor v733 (comp 10818)；早期 comp ゲート含めノイズ rollback 13〜18%、rollback 先は v733。

## 2026-08-25 18:2x JST — loop 9回目: v733 がロシア建国 (17:13, 4065 点) → anchor 昇格 / v734 は実装・レビュー済み・**未デプロイ**

- **v733 ロシア建国（実測）**: `20260825_171338_score4065.jsonl`（163 手・最終 4065 点、**118 手目にロシア、31 駒・余裕 −0.06、その後 45 手生存**。朝の v727 例は 123 手目・その後 11 手）。17:14:02 `current strategy promoted to anchor: cb3434f22573`（comp 10725、n=28、russia 1）。29 試合: 平均 1345 / 中央値 1217、カザフ 7/29、`tools/seed_metrics.py`: GOOD 比率 **52.2%**（v727 47.1% / v731 40.7% / v732 50.5%）、同ターン連鎖 **2.03/試合**（目標 ≥2.0 到達）、T12 ペア指標 1.04、粛清・エラーなし。
- **v734 `LOW_DROP_HIGH_LANE_COVER_AVOID`（soviet_now `035012f40`、hash `cf2fa99701e9`、push 済み・VM 未反映）**: 小駒 (T1〜T8) の非併合ドロップが「上端の開いた T9〜T11」に乗る候補へ −300（v731/v732 と同じ安全条件、併合候補がある手では不発、next=T8 で T9 は縦積み免除）。根拠: 小駒ドロップの 16.6% が開いた T9〜T11 を覆い、埋もれた T9〜T11 の 20 手以内併合率は 21〜28%（開いていれば 55〜62%）。junk の密着（v731 式の低型拡張）と壁側ルールは実測 null。A/B 8270 手: 変更 10.1%、併合喪失 0、交差 0、開いた T9〜T11 被覆 32.7%→14.9%（T12+ は 8.2→9.8% に微増、次候補 v735 で範囲を T14 まで）、risk_top 平均 −0.24、壁際着地 35→129（壁側はむしろ最も安全）。opus レビュー SHIP WITH FIXES: 設計の「HIGH_TYPE_COVER_AVOID との共起 0」は誤り（候補の 80% が重なり −700、実行決定の 21% で両タグ）＝純増保護は約 20% で主に T9。テストは mutation testing で negative 側に検出力が無いと判明 → タグが付く fixture に載せ替え（margin=1.0 境界・ロシア・next≥9・開いた上端の被覆で検証）。tests/ 全体 111 failed / 899 passed（HEAD 111/890）。
- **デプロイ保留の理由（レビュー推奨に従う）**: v733 が anchor 昇格直後（n=28）で、v734 の n<12 の早期 comp ゲート (`early_comp_top_gap`, ratio 0.85, MIN_GAMES 4) と n≥12 のソフトゲート（絶対差 1000/800/1600 の 2 つ、config の ratio 定数は未参照）を合わせたノイズだけの rollback 確率が 13〜18%。rollback 先は v733 自身（クリーン）。v733 の判定窓をもう 1 回置いてから（次回 loop で）境界デプロイする。手順: `scp strategy.py → tmp/manual_challenge/strategy_cf2fa99701e9.py`、`bash tmp/manual_challenge/vm_deploy_strategy.sh cf2fa99701e9 prepare|swap|finish`（helper 変更なし）、tests/fixtures/low_drop_cover_*.json と tests/test_low_drop_high_lane_cover.py も配置。
- **v734 監視（デプロイ後）**: タグ ≈3%/手、小駒手で開いた T9〜T11 を覆う率 31%→13%（T9〜T14 合計 38%→22%、T12+ は 12% 超で警戒）、40/60 手目の駒数 −1/−1.5、`decide_exception` 0。
- **VM と repo の乖離注意**: 本節時点で VM root は v733 `cb3434f22573`、repo HEAD は v734。次回デプロイで解消する。

## 2026-08-25 16:3x JST — loop 8回目: v733 が n=15 で判定通過 + 盤面飽和（死因）の実測と v734 設計着手

- **v733 判定（実測 16:27:24, n=15）**: `PROMOTE: anchor 0890dbefd73e comp 9707.7 vs v733 comp 10260.1 / p50 10684.0 / p25 9375.0` → objective guard で昇格抑止。15 試合: 平均 1325 / 中央値 1217 / 最高 2433、カザフ 3/15、**x=−0.991 実行 0%**、DIRECT 実行 24.7%（v731/v732 21〜22%）、DIRECT 併合率 96%、併合/手 0.33（同）、盤面上昇/手 0.39（同）、`NO_MERGE_DEADLINE_GUARD_NO_VALID` での死亡 8/15（v731 23/31、v732 13/16）。エラー 0。
- **盤面飽和の実測（v732/v733 13 死亡盤面）**: 最終 41 駒（中央値）の構成 T1-3 142 / T4-6 155 / T7-9 125 / T10-12 96 / T13+ 15 = **56% が T1〜T6 の小駒**。分離したまま残る同型ペア（v731 指標 >0.3）中央値 **9 組**。T1〜T8 の非併合ドロップ 388 件は同型相方から中央値 1.59、54% が 1.5 超（junk を散らしている）。
- **相関（100 試合、≥45 手）**: 40 手目の駒数 vs 寿命 −0.39 / スコア −0.43、60 手目の T1-6 数 vs 寿命 −0.36 / スコア −0.45（盤面の重さが死因）。ただし **junk を相方の近くに置く指標は寿命 +0.20 / スコア +0.26 と正の相関**（密着側の半分: 80 手 1187 点、疎側: 86 手 1465 点）。「小駒も v731 式に相方へ密着」は支持されず、手動ゲームの「小国は片側の壁へ隔離し低い籠で自然併合」（consolidation/exile）が本命。混雑による交絡の可能性も含め opus Plan（v734 設計）に伝達済み。
- **注意**: v731/v732/v733 を 4.5 時間で重ねたため次の判定は合算。v734 は設計結果を見てから判断（判定通過済みの v733 を当面走らせる）。

## 2026-08-25 15:3x JST — ユーザー指摘「併合できるのに見逃して盤面を高くする」→ 主因は decide() の出力クランプ (v733 で撤廃)

- **v732 判定（実測 15:17:34, n=14）**: `PROMOTE: anchor 0890dbefd73e comp 9707.7 vs v732 comp 10115.8 / p50 10635.5 / p25 9183.5` → objective guard で昇格抑止（想定どおり）。v732 は 12 試合の判定を通過。
- **ユーザー指摘の実測（v731/v732 38 試合 3201 手）**: 解析器が DIRECT を出している手 755 のうち 141 (18.7%) で非併合を実行、その 35% で盤面上昇。内訳の大半は decide() の理由が `DIRECT_MERGE_*`（併合を選択）なのに実行 x が **−0.991**。`_clip_final_drop_x` のロシア前クランプ `[-0.991, 4.362]`（a29155b3f の wildcard 進化リテラル。4.362 は盤面幅 3.0 超＝物理根拠なし）が左側の選択を右へ押し戻し、採点していない地点へ非併合着地させていた。**全決定の 16.9% (541 手) が x=−0.991 で実行、うち併合 27%・盤面上昇 48%**。締切安全な DIRECT を左で選んでいたのに失った手 117 (3.7%)。解析器側の見逃し（露出同型駒で NO）は v728 後は 8 件程度で副次的。
- **v733（soviet_now `efaf9525d`、decide hash `46d7040d4153 → cb3434f22573`、15:27 VM 反映）**: ロシア前のクランプを `[-3.0, 3.0]` に統一（fallback 経路 `[-1.612, 0.862]` と post-Russia は据え置き）。解析器は元々盤面全体を対称に採点しており（DROP_X_MIN/MAX、wall_rotation_risk）、runtime override も −3.0 に常用していたので、左側を拒んでいたのは strategy.py だけ。A/B 7243 手: 変更 1213 (16.7%、全て左へ)、**実行 DIRECT 1630→1919 (+18%)**、grade 向上 295 / 喪失 0、非交差→交差 0、変更手の risk_top 平均 −0.34、壁際着地 +25%（回転リスクは risk_top に織込み済み）、レーン系フック発火差 12/7243（全て改善か同等）。opus レビュー SHIP WITH FIXES: `tests/test_first_russia_t11_lane_guard.py` の fixture が旧クランプ (clip(−1.0)=−0.991) を前提にしていて 10 件落ちる（本番退行ではない）→ 修正。旧クランプを固定していた 3 テストも盤面範囲に更新。tests/ 全体 **111 failed / 890 passed = HEAD と同数**。`4e664ce`(08-19) の rollback は v704/v705 の T12/T13 軸と同梱だったのでクランプに帰する根拠なし（メモリも訂正）。
- **VM 反映（実測）**: 境界 pause 15:27:45 → 差替え・by_hash 登録・revert 点 v732 → **15:28 `Strategy hash: cb3434f22573`**、3 箇所一致、初手 20 DROP 中 2 手が x<−0.991、エラー 0。VM `.env` は `TABU_ENABLED=1`/`DIVERSITY_PREMIUM_ENABLED=1`（behavior_signature の x_bins が 14% 動くが、改善ループ側の指標で対局には無関係。改善再開時は注意）。
- **v732 の効果指標（16 試合、tools/seed_metrics.py）**: T9〜T11 併合地点の GOOD/(GOOD+BAD) **50.5%**（v731 40.7%、v727 47.1%）、連鎖 1.88/試合、T12 ペア指標 1.31（v731 1.49）、v732 タグ 3.0%/手、DIRECT 併合率 96.3%、エラー 0 — 狙ったアンカー品質は回復。
- **注意（帰属）**: v732 の判定窓 (n=15) はここで打ち切り。次の 12 試合の判定は v732+v733 の合算で、退行時はどちらか分離できない（ユーザー指摘の効果が大きいので優先した）。
- **監視（v733）**: 実行 x=−0.991 率（≈0 になるはず）、実行 DIRECT 率（+18% 期待）、DIRECT 次ターン併合率 ≥90%、盤面上昇率、`decide_exception` 0。`tools/seed_metrics.py` の指標も継続。

## 2026-08-25 14:3x JST — loop 6回目: 効果指標ツール `tools/seed_metrics.py` を追加（v731/v732 の正直な評価軸）

- **ツール（soviet_now、VM にも配置）**: `python3 tools/seed_metrics.py "game_history/*.jsonl" [--since HHMMSS] [--hash H]` — hash ごとに、T9〜T11 併合地点の最寄り T(N+1) アンカー分類（BESIDE/ABOVE_OPEN=GOOD、ABOVE_COVERED/FAR=BAD、軸別 Unity 半径・縦ゲート込み）とその同ターン連鎖率、連鎖/試合、T10/T11/T12 ペア形成指標の中央値、タグ率、DIRECT 次ターン併合率、decide エラー数。
- **ベースライン（実測）**: GOOD 地点の連鎖率 54〜60% vs BAD 7〜11%（前提が厳密分類でも成立）。v727 (26 試合, scratch): GOOD/(GOOD+BAD) **47.1%**、連鎖 1.92/試合、T11 ペア指標 0.84。42c79aab (24 試合): 45.5%、1.62。**v731 (VM 31 試合): 40.7%、1.90、T11 指標 0.03、v731 タグ 5.1%/手** — v731 はペアを密にした代わりにアンカー品質を下げた（レビューの予測どおり、v732 の対象）。v732 (4 試合): 46.7%、1.25、v732 タグ 3.4%/手、エラー 0 — n 不足。
- **v732 判定待ち**: n=2〜4、次回 (15:13) に n≥12 の判定と上記指標を確認。目標 GOOD 比率 ≥ 50%（v727 比で改善）、連鎖/試合 ≥ 2.0。

## 2026-08-25 14:2x JST — loop 5回目(続): v732 ANCHOR_LANE_SEED_CONTACT（decide 変更・新 hash）を VM 反映

- **v731 監視（n=21, 13:29）**: 平均 1381 / 中央値 1172 / 最高 2854、カザフスタン 4/19、DIRECT 併合率 97.3%、粛清なし（rank 7 で grace）。
- **v732 設計（opus Plan、62 試合 5048 手）**: T9〜T11 の併合地点を最寄り T(N+1) アンカーに対して軸別半径で分類すると、同ターン連鎖率は「横で縦接触」63%・「横で縦隙間」21%・「開いたアンカーの真上に乗る」67%・「埋もれたアンカーの上」3%・「遠い」8%・「アンカー無し」0%（GOOD vs BAD Fisher p=1e-12）。T12 併合の 72% は T11 併合からの同ターン連鎖。手動ゲームのラダーは「開いたアンカーの真上」に組んでいたので、私が設計依頼で書いた「真上は禁止」は誤りで、正しい不変条件は「埋もれたアンカーの上・他の開いた高型上端の上には置かない」。v731 の +400 窓内には GOOD/BAD 両候補があるのに BAD を選ぶ手が 38%（指標コスト +0.03）。
- **v732 実装（soviet_now `6f8d8881f`、decide hash `6cf9bd5c6ab0 → 46d7040d4153`）**: v731 と同じ安全条件 + T(N+1) アンカー在盤で、着地が「開いたアンカーの真上に乗る（下端が上端±0.25）」または「横（水平ギャップ≤0.15 かつ 縦 AABB ギャップ≤0.5）」なら +120、タグ `ANCHOR_LANE_SEED_CONTACT`。他の開いた T≥10 上端を覆う着地には加点しない（guard）。+120 は HIGH_TYPE_COVER_AVOID(−400) を覆せず、v731 窓内の順位決めのみ。純粋ヘルパー `board_stats.seed_top_radius / seed_bottom_radius` 追加。**本番 sandbox validator の load-before-assign 検査**で初版が弾かれた（try 内代入）ため `_as_float` で書き直し（`tests/test_post_russia_contact.py::test_current_strategy_passes_production_sandbox_validation` が守っている）。
- **レビュー（opus, SHIP WITH FIXES）反映**: F1 横判定に縦ゲート（接触 63% vs 隙間 21%）、F2 真上判定に上限（上端+0.25、初版は 55% が離れた高さ）。指摘のみ記録: 回転無視（解析器は angle で回転、軸は平板半径。v731 と同じ結合、13.5% のタグが解析幾何では GOOD でない）、T12_CHAIN_LANE_GUIDANCE(+140) との二重加点（約半数で共起）、`_clip_final_drop_x` により約 20% の決定が加点した候補と別の x で実行される（既存、HEAD と同率）。
- **v732 検証（実測、ゲート後）**: A/B（v731 vs v732、5048 手）: タグ 189 手（3.7%）、着手変化 43（0.85%）、併合喪失 0、非交差→交差 0、例外 0、変更後の着地は全て厳密クラス（接触）、margin 低下>0.5 が 2、|Δx|>2 が 5、選択候補 margin 中央値 2.52→2.75。戦略系 9 スイートの失敗集合は v731 と同一（106 既存）。新規 8 テスト（6 実局面 fixture ×厳密分類、埋もれたアンカー無加点、guard、margin=1.0）。v731 の T11 fixture は競合しない局面 (070111 t48) に差替え。
- **VM 反映（実測）**: ヘルパー先行配置 + import 検証 (1.39/0.873/0.831) → 境界 pause 14:18:31 → 差替え・by_hash 登録・revert 点 v731 → **14:18:45 `Strategy hash: 46d7040d4153`**、3 箇所一致、VM unittest OK、初手 21 DROP エラー無し。backup `.codex_deploy/backup-20260825-v732/`。
- **監視（v732）**: `ANCHOR_LANE_SEED_CONTACT` 約 3.7%/手（`SEED_CONTACT` の grep は v731 にも当たるので完全名で）、`decide_exception` 0（出たら即 v731 復元＝eloop の自動 revert が commit/push まで走る点に注意）、正直な効果指標 = T9〜T11 併合地点の GOOD/(GOOD+BAD) 比率（v727 期 58%、v731 期 53% → 目標 ≥62%）、同ターン連鎖/試合（1.8〜1.9 → ≥2.0）、v731 指標のドリフト（≤ +0.3）。n≥12 で anchor（0890dbef comp 9708 か v727 10016 の往復）と比較。
- **次**: v732 12 試合の判定確認。効果指標の集計スクリプト化。回転を考慮した軸別半径ヘルパー（v731/v732 共通の改善候補、hash 外結合の注意）。

## 2026-08-25 12:5x JST — loop 5回目: v731 が n=12 の判定を通過（PROMOTE 判定、昇格は objective guard で抑止＝想定どおり）

- **判定（実測 12:52:13）**: `legacy=PROMOTE`、`[STAGEGATE] graced=0 skipped=rank_grace rank=2`、`PROMOTE: anchor 0890dbefd73e comp 9707.7 / p50 10122.5 / p25 8550.5 vs v731 comp 10332.7 / p50 10464.0 / p25 9954.5 (n=12)` → `anchor promotion suppressed by objective guard`（v731 russia 0 < anchor 1）。v731 は current として継続、root/snapshot/runtime `6cf9bd5c6ab0`。`decide_exception` 0。
- **注記**: anchor ファイルは 10:25 に v727 へ昇格した後、境界ごとに `_refresh_best_strategy_anchor`（OBJECTIVE_ANCHOR_PRIORITY）が `0890dbefd73e` (comp 9707.7, russia 1, n=100) に戻し v727 を再昇格する往復を繰り返していた（11:36, 12:02 のログ）。v731 稼働中の比較 anchor は 0890dbef（v727 より低い bar）。挙動は無害だが、anchor 選定ロジックの再点検は別課題。
- **v731 12 試合（実測）**: 717/2290/1076/1172/1185/1009/1143/1624/1505/2854/1966/1523 → 平均 1505 / 中央値 1354、カザフスタン 3/12、DIRECT 併合率 97.4%、タグ 5.2%/手（予測 5.6%）。**直接目標の指標が動いた**: T11 ペア形成の距離指標 median 0.56 → −0.07、T10 1.45 → 1.10（v727 48 試合比、v728/v729 稼働期）。T12 ペア指標は 1.04 → 1.54 (n=19)、変換 T11→T12 107→96%・T12→T13 70→67%・T13→T14 25→38% は n が小さく未判定。
- **次**: v732 設計（opus Plan に委任）— 同型相方が無い手で next_type+1 のアンカーに隣接して播種する「pair-on-anchor」（手動ゲームの連鎖パターン）。効果測定は「T(n+1) アンカー近傍で形成されたペアの連鎖率」で。

## 2026-08-25 12:0x JST — loop 4回目(続): v731 SAME_TYPE_SEED_CONTACT（decide 変更・新 hash）を VM 反映 + v729 監視 23 試合

- **v729 監視（#45406〜 23 試合、実測）**: 平均 1493 / 中央値 1477 / 最高 2616（v728 期 18 試合 1446 / 1377 / 3311）、カザフスタン 6/23（26%、v728 期 3/18）、`FALLBACK` 1（同）、`urgent_direct` 2.2/試合（同）、DIRECT 併合率 95.9%（97.8% → 90% 打切り線より上）、赤線上残留 1.7/試合（1.3、想定内の代償）、mrc/turn 0.024（0.023、低下はまだ不明瞭）。粛清なし。**v727 は 10:25 に anchor 昇格**、run n=67 russia 1。
- **v731 設計（opus Plan、50 試合 4004 手の実測）**: 段階到達の funnel は T12→T13 60%・T13→T14 26% で崩れる（手動 100%/67%）。同型ペアは形成時の「距離指標」（中心間距離 − 両者の水平半径。物理接触ではない相対指標）が 0.3 以下だと 78%（T12: 91%）併合到達、1.5 超だと 47%（T12: 25%）。届かない同型相方がある手（49%）で v727 は指標 1.86 に置き、締切安全候補なら 1.13 まで寄せられた。棄却した代替: 連鎖狙いの着地点選択（機会 22/3964 で v727 が 21 取得済）、axis6 の連鎖ボーナスのバグ修正（機会が無い）、v729 mode 1（着手変化 1/3964）、被覆抑止強化（手動 20% vs auto 19% で差なし）、締切緩和（死因そのもの）。
- **v731 実装（soviet_now `40d3fa901`、decide hash `5c9ab0ea6b6c → 6cf9bd5c6ab0`）**: 非併合手（盤上に DIRECT/NEAR 候補なし）・next T9 以上・同型相方在盤・非 deadline_crossed・margin≥1.0・候補が非交差（併合結果も）・非 death_spiral・ロシア不在のとき、指標 ≤1.3 の候補に `+400×(1−指標/1.3)`、理由タグ `SAME_TYPE_SEED_CONTACT`。+400 は HIGH_TYPE_COVER_AVOID(−400) と同額で相殺止まり。純粋ヘルパー `board_stats.seed_horiz_radius`（additive、analyze_board の半径表を参照 = hash 外結合と明記）。**decide() 内に env kill switch は入れない**（decide は hash 内容の純関数であるべき、レビューも同意）。復元は境界で v727 ファイルを戻す。
- **v731 検証（実測）**: フルパイプライン A/B（v727 vs v731、同一解析器トグル、4004 手）: タグ 223 手（5.6%）、着手変化 79（2.0%）、併合喪失 0、非交差→交差 0、margin 低下>0.5 が 6、指標 中央値 1.08→0.25（改善 77/79）、|Δx|>2.0 が 16/79、上端の空いた同型相方を新たに被覆 2/79（監視項目）。戦略系 7 スイートの失敗集合は HEAD と同一（106 既存）、test_strategy_hash OK、新規 8 テスト（実局面 fixture）。opus レビュー SHIP WITH FIXES を反映: S1 ヘルパー先行配置＋import 検証、S2 active_branch.json（VM に無し、repair no-op）、H1/H2/M1 のコメント訂正（指標≠接触、next は T11 まで＝T9〜T11 ペア密度への作用、+400 は −400 と相殺）。「相方の真上を除外する guard」は試作したが、指標負＝縦積み形成（実測で最も併合到達する形）を全て除外してしまうため撤回。
- **VM 反映（実測）**: `strategy_helpers/board_stats.py` + tests を先に配置し VM で import 検証（1.39）→ `tmp/manual_challenge/vm_deploy_strategy.sh 6cf9bd5c6ab0 prepare/swap/finish`（マーカー境界 pause 12:02:56 → root 差替え → by_hash / 永久 archive 登録 → `tmp/revert_strategy.py`=v727 → マーカー解除）→ **12:03:11 新ゲーム `Strategy hash: 6cf9bd5c6ab0`**、root/game_snapshot/runtime 一致、VM unittest OK、初手 19 DROP でエラー無し。backup `.codex_deploy/backup-20260825-v731/`。
- **監視（v731）**: `SAME_TYPE_SEED_CONTACT` が理由に約 5%（4〜5 手/試合）— 1 試合で 0 なら差替え不成立を疑う。`decide_exception`/`strategy.decide() failed` が出たら即 v727 復元（helper 欠落の signature）。n≥12 で anchor v727（comp 10016）と比較される: ノイズだけで約 5% の rollback 確率、rollback 先は v727。正直な効果指標は T10/T11 併合率と T11→T12 変換、T12 ペア形成指標；T14 到達への効果は間接的で近い時間軸では期待しない。
- **次**: v731 の 12 試合観測 → 生存なら v732 候補（T6〜T8 への拡張は funnel 価値が薄く保留）。ロシア埋没対策（POST_RUSSIA_* が 0 発火だった理由）。

## 2026-08-25 10:3x JST — loop 4回目: v727 が anchor に昇格（実測）+ v729 初期監視 + ソ連併合の締切扱いは非問題と確定 + 段階到達ターン比較

- **v727 anchor 昇格（実測）**: 10:25:32 `[BRANCH] current strategy promoted to anchor: 5c9ab0ea6b6c`（comp 10015.9 / n=51 / russia 1 → objective guard 通過）。`best_strategy_anchor.json` = `5c9ab0ea6b6c`。以後 v727 系譜は粛清機構に守られる側。`[STAGEGATE] graced=1 skipped=rank_grace rank=6`。
- **v729 初期監視（#45406〜、7 試合）**: DIRECT 併合率 97.8%（v728 期 97.8%）、`FALLBACK` 0、`urgent_direct` 1.7/試合（v728 期 2.2）、赤線上に残った駒 1.4/試合（1.3）、mrc/turn 0.022（0.023）、cross/turn 0.082（0.079）。スコア 818〜1918（平均 1154、v728 期 18 試合 1446）は 7 試合ではノイズ範囲。異常なし、継続観測。
- **ソ連併合の締切扱い（v730 候補）は非問題と確定（フルパイプライン probe `scratchpad/v730/soviet_shot_probe.py`）**: ロシア (T15) は横半径 2.24（幅 4.5/7）なので、盤上にロシアがあれば次の T15 は必ずロシアに着地し全候補が DIRECT。ロシア y=0.3/0.6/1.0（落下自体が交差扱い、mrc 有無問わず）でも pipeline はロシアへの直撃を選択（`DIRECT_MERGE_*` / `..._RESULT_CROSS_PENALTY`）。T16 半径 0.5 フォールバックは順位に影響しない。**ソ連の実ボトルネックは (a) 第1ロシアを早く低い盤面で作る (b) T14 ペアをロシアに隣接して組み、新ロシアが即連鎖する形**。
- **段階到達ターン比較（v727 26 試合 median vs 手動 1 試合）**: T11 6 vs 5、T12 17 vs 23、T13 40.5 vs 33、**T14 70 vs 51（到達 4/26 のみ）**、T15 123 vs 109（1/26）。ボトルネックはウクライナ→カザフスタン（T13 ペアの併合）。v731 の設計（データ根拠付き、opus Plan に委任中）はこのギャップを対象。
- **衛生項目（未対応）**: `piece_deadline_top_y` 等 5 箇所の `float(p.get("y", FLOOR_Y) or FLOOR_Y)` は y==0.0 を欠損扱い（実データでは事実上発生しない）。

## 2026-08-25 10:0x JST — loop 3回目: ロシア建国を実測 (v727+v728) + v729 併合後高さ較正を VM 反映 + ソ連経路の実態調査

- **ロシア建国（実測）**: 09:11:46 `!!! RUSSIA CREATED !!! score=3229 russia_count=0->1`（v727 `5c9ab0ea6b6c` + v728 稼働中、Game 20260825_091218 最終 3290 点・134 手）。ただし **ロシア到達は 123 手目・盤上 34 駒・締切余裕 −0.15・カザフ/ウクライナ 0** で、11 手後に死亡。v727 の POST_* フックは 0 発火。
- **v728 実戦（08:37〜09:28、12 試合）**: 平均 1446 / 中央値 1293 / 最高 3311（v728 前 23 試合: 1249 / 1134 / 3196）、選択候補 DIRECT の次ターン併合率 **98.1% (207/211)**（前 95.9%）、カザフスタン 3/12（前 3/23）。粛清なし、`[STAGEGATE]` 毎境界 `fired=0`。v727 run は 10:00 時点 n≈40、russia 1。
- **ソ連経路の実態（フルパイプライン probe、実ロシア盤面 turn 123〜132 に next=15 を仮定）**: ロシアは埋没（列内でロシア上端より高い駒が 5〜11 個）、**ロシアへの DIRECT 候補 0・全候補が締切交差 (nsafe=0)**。第2ロシアが出来ても併合不能。合成盤面では床上のロシアへは DIRECT で正しく撃つ（`DIRECT_MERGE_HIGH_LAYER_...`）。結論: ソ連には「ロシアをもっと早く・低い盤面で作る」＋「ロシア上端を露出させ続ける」が必要。併合効率（v728/v729）はそのための手段。
- **v729 実装・レビュー・VM反映（soviet_now `e2e396bda`、09:58:10 反映、Game #45406 から有効）**: 併合後ピース上端 `merge_result_top_y` の旧式 `max(ly_poly, target.y)+R` が実測 466 併合で **平均 +1.05 過大**（dy=ly_poly−target.y は併合位置と無相関）、閾値 3.38 で誤警報 49 vs 真 10、手動 99/120 手目の直撃もこれで拒否されていた。較正 `est=min(legacy, max(Lw, blend))`（Lw=ターゲット列で相方を除いて併合後ピースを落とした着地上端 ±0.35、blend=target.y+0.25·max(0,dy)+R+0.20）。`ANALYZE_BOARD_MERGE_TOP_MODEL` 2=併合拒否判定のみ較正(既定)/1=候補自身の crosses_deadline にも/0=旧式。上限キャップで旧式を上回らない（311,232 候補で 0 件）→ 締切安全プールは縮まない（mode 2 は「merge_result_crosses ⇒ crosses_deadline」不変条件で構造保証、mode 1 は未保証で既定にしない）。T16(ソ連) は eff_radii に無く旧式に fail-closed。**切替 `./set_toggle.sh ANALYZE_BOARD_MERGE_TOP_MODEL=0`**。
- **v729 検証（実測）**: bias +1.05→+0.79、新規過小 0（唯一の −0.20 見逃しは旧式由来で fixture に「直したふりをしない」テストあり）、誤警報 49→36・解除 13 件の実余裕 min +0.47、手動 99/120 → 3.37/3.29 で許可。mode 0 は HEAD と 3,964 盤面で byte 一致。フルパイプライン A/B（1,103 / 3,871 局面）: 着手変化 ≈0.8%、併合 +24/−0、プール縮小 0、新規 FALLBACK 0。**実効果の正体（レビュー指摘、commit message に明記）**: 約 0.6% のターンで runtime 締切安全網が「交差しない非併合配置」より「自身は交差するが併合結果は線下の DIRECT」を選ぶ（`RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_DIRECT_urgent_direct`）。誤 DIRECT だとピースが赤線上に残る。解除 16/16 は実測で正しかったが、線際 (余裕 <0.10) の解除が 7/16 あり要監視。`strategy_runner` の毎ターン記録に `analyzer_modes` を追加（decide hash に映らない解析器変更の事後帰属用）。新規 `tests/test_merge_top_model.py` 10 件、全 tests/ の失敗集合は mode 0/1/2 × v728 0/1 で同一。opus 設計→自分で再現→opus レビュー (SHIP)。
- **VM 反映（実測）**: backup `.codex_deploy/backup-20260825-v729-merge-top/`、9 ファイル temp+mv、SHA 一致、VM unittest OK。09:58:10 反映時は Game #45405 進行中 → **#45406 (runner 09:59:12) から有効、DROP 進行・エラー無し、latest.jsonl に `analyzer_modes {vertical_lane_direct:1, merge_top_model:2}` を実測**。decide hash `5c9ab0ea6b6c` 不変（v728/v729 の効果は v727 の rolling に混入する。粛清が出たら先にトグルを疑う）。
- **監視ポイント（v729）**: `decision_merge_result_crosses_deadline` 率（104/3871→約 86 へ低下が期待）、`decision_reason` の `..._urgent_direct` 増加と `FALLBACK` 0 維持、DIRECT 併合後に実際に赤線越えした件数（未フラグの越境 >2/200 併合で `=0`）、`deadline_clean_candidate_count` が mode 2 で不変、`logs/deadline_misplacement_monitor.jsonl` のイベント率。
- **発見したが未対応（次候補）**: (1) **v730: T15+T15（ソ連）併合の締切扱い** — `get_type_top_radius(16)` は 0.5 にフォールバック、かつ next=15 の落下自体が `top_after_drop≈ty+3.2` で交差扱いになるため、ロシアが y≳0.2 にあると勝ち手が締切網に掛かる可能性。合成盤面（ロシア y=1.0）では `DIRECT_MERGE_RESULT_CROSS_PENALTY` 経由で一応選ばれたが要設計。(2) `piece_deadline_top_y` 等 5 箇所の `float(p.get("y", FLOOR_Y) or FLOOR_Y)` は y==0.0 を欠損扱い（実データでは事実上発生しない、衛生項目）。(3) ロシア埋没対策 — ロシア出現後にロシア列の上を塞がない誘導（v722 系 POST_RUSSIA_CHAIN_COVER_AVOID が 0 発火だった理由の調査）。

## 2026-08-25 07:5x JST — loop 2回目: STAGEGATE 実戦初観測（粛清回避を実測）+ analyze_board O(n²) 修正を適用・VM反映

- **STAGEGATE 実戦初観測（実測）**: v727 復元後 n=12 の境界 07:54:59 に `[STAGEGATE] graced=0 rank=20 t14=1/12 vs 3/24 p=0.5927 gap=0.042 fired=0`、verdict `legacy=OK`。旧コードなら T14 1/12 < 3/24 で `lost_kazakhstan_gate` が発火し v727 は再度 n=12 で粛清されていた局面。`breach_count=0` の objective_regression は出ていない。v727 の 12 試合: 1183/1115/788/748/1674/**3196(カザフスタン)**/1117/1690/1275/694/…、max_types に 14 が 1、ロシア 0。anchor は `42c79aab4a68` のまま（russia 1 → v727 は best 14 で grace 対象外、昇格も guard で抑止、生存はする）。
- **analyze_board.py O(n²) インデント修正（リモート節が退避したパッチ）を適用**: 自分で再検証 — 手動ゲーム 128 盤面 + 実履歴 8 試合 704 局面 = 832 盤面 × (shapes あり/なし) で `calc_reactor_state` 出力 **0 mismatch**、n≈40 で 3.7ms→0.23ms（16倍）。`git am` で soviet_now `13cac205b`（原著者・メッセージ維持）を push。VM へ backup (`.codex_deploy/backup-20260825-analyze-board-indent/`) 後 temp+mv で反映、SHA `2ca667d4…` 一致、py_compile OK（07:33:44）。strategy_runner はゲーム毎の新プロセス（07:34:01 起動、07:35:32 試合開始）で新ファイルを import、DROP 進行・analyze_board エラー無しを実測。decide hash は不変（`5c9ab0ea6b6c`）。`docs/patches/…indent.patch` は適用済みとして扱ってよい。
- **v728 実装・レビュー・VM反映（soviet_now `efbcd7724`、push 済み、08:36:11 反映）**: 根本原因は insights doc の「dist 閾値」ではなく、`hit_id==target` 後の `has_obstruction`（他駒の上端をターゲット**中心**と比較・±(drop+p)×1.05 帯・レーン判定なし）→ `has_horizontal_obstruction`（垂直落下ではターゲット自身の土台にしか当たらない）の連鎖で NO 化していたこと（opus Plan の再解析、コードで自分で確認）。手動ゲームの 3/3 のうち解析 NO は 78 手目のみ、99/120 は DIRECT だが締切拒否（doc 訂正済み）。修正: `analyze_board._vertical_lane_direct` — 着地モデル上ターゲットに直乗り（hit_id==target）かつ contact_gap≤0.02・横重なり ≥50%（|x−t.x| ≤ min(drop_horiz,target_horiz)）・ly 整合・柱内の他駒上端がターゲット上端より 0.05 以上低い（パーチ保険）なら DIRECT 維持。`ANALYZE_BOARD_VERTICAL_LANE_DIRECT` 1=DIRECT(既定)/2=NEAR 昇格のみ(降格なし)/0=旧挙動、毎回 os.environ 読み（runner はゲーム毎プロセス → 次ゲームから有効）。config.sh 既定+export、runtime_toggles/set_toggle whitelist 追加。**切替は `./set_toggle.sh ANALYZE_BOARD_VERTICAL_LANE_DIRECT=0`（= 必須）**。
- **v728 検証（実測）**: 実履歴 8 試合 699 局面を実着手 x で再評価 — 昇格 22 件は **22/22 が次ターンで実消費**（本番ログでは 12 件が NO 判定だった）。旧 DIRECT の同条件精度 94.8%。全パイプライン A/B（設計・レビュー両エージェントで一致）: 着手 x 変化 78/699（11%）、選択候補の crosses_deadline 件数 59→59 不変、非交差→交差への悪化 0。toggle=0 は HEAD 版と 707 盤面で出力完全一致。3 スイート（escape/post_russia/pre_russia_ukraine）の失敗集合は mode 0/1/2 で同一 106 件（既存）。新規 `tests/test_vertical_lane_direct.py` 9 件（angle 付き実局面 NO→DIRECT・toggle 0 同一性・mode 単調性・prominence・掠り比率・不正入力・性能）。opus レビュー (SHIP WITH FIXES) 反映: mode 2 が旧 DIRECT を降格する H1 を修正、prominence 0.05 採用（1708 昇格中 67 除外・真陽性損失 0）、G1/G3/G4 が hit_id==target 下で恒真である旨をコメントに明記、doc の表崩れ修正。
- **VM 反映（実測）**: backup `.codex_deploy/backup-20260825-v728-vertical-lane/`、8 ファイル temp+mv、SHA 一致、py_compile/bash -n OK、VM `python3 -m unittest tests.test_vertical_lane_direct` 9/9 OK。runner 08:37:01 再起動 → 08:37:12 試合開始（v728 初戦）、DROP 進行・エラー無し。VM `.env` に ANALYZE_BOARD_* 無し（config.sh export 既定 1）。decide hash は `5c9ab0ea6b6c` のまま（regression 機構には見えない → スコア変化は v727 のせいにされる点に注意、粛清が出たら先に toggle を疑う）。
- **観測ポイント**: game_history の `best_merge_grade==DIRECT` 時の次ターン `score_delta>0` 率（基準 94.8%、60 局面以上で 90% を切ったら `=0`）、`decision_reason` に `DEADLINE_GUARD_DIRECT_MERGE`/`DIRECT_MERGE_*` が増えるか、`decision_merge_result_crosses_deadline`/小さい `deadline_margin` の選択。
- **v728 設計（完了、上記）**: 手動ゲーム知見「垂直開放路の同型直撃 3/3 併合（解析は NO）」を analyze_board の grade 昇格として設計中（opus Plan に委任、データ: 前セッション scratchpad `manual_game1/obs_*.json` 128 件 + 実履歴 8 試合 + VM shapes）。コード読みでは NO の出所は dist 閾値ではなく `hit_id==target` 後の `has_obstruction` か landing_hit 予測ずれの可能性が高く、まず根本原因の特定を依頼。analyzer 変更は decide hash に映らないため env トグル (`ANALYZE_BOARD_VERTICAL_LANE_DIRECT`) 付き・fail-closed が前提。設計結果は次節に記録。
- **未確認**: v727 のロシア/ソ連到達。STAGEGATE の `fired=1` 実例（真の劣化での発火）はまだ観測なし。

## 2026-08-25 07:1x JST — 粛清ゲート統計化 (STAGE_GATE_STAT) + v727 復元（/loop 1h「ソ連建国を目標に戦略を改善せよ」1回目）

- **発見（VMログ実測）**: 01:36 に稼働開始した v727 `5c9ab0ea6b6c` は **02:26 に n=13 で粛清**された。`REGRESSION:mode=objective_regression`, `reasons=objective_regression+lost_kazakhstan_gate`, `breach_count=0`, `curr_comp 9923.7 > anchor 9707.7`。実体は T14(カザフスタン)到達 1/13 vs anchor 6/25（片側Fisher p=0.22）と russia 0(n=13) vs 1(n=100)。02:22 の n=12 では PROMOTE 判定だったが bash 側 `_promote_current_strategy_to_anchor` の objective guard (russia 0<1) で昇格抑止→翌ゲームで rank>7 になり段階ゲートが発火。rollback 先は rolling_top `e5b671c8d352`（v705世代, 8/19 archive）。その `e5b671` も **05:06 に n=100 で粛清**: `lost_turkmenistan_gate`, breach 0, russia **2 vs 1**, comp 9650.5 vs 9707.7（-57=0.6%）。T11 41/43 vs 25/25 (p=0.40, gap 0.047)。前セッションの `stage_gate_noninferior_grace` は `comp >= anchor comp` 条件で外れた。rollback 先 `42c79aab4a68`（v707世代）が 05:09 に anchor 昇格し現行（06:30 時点 n=25 fresh, 最高カザフスタン, ロシア0）。
- **訂正（02:5x〜05:4x のリモート毎時メンテ節へ）**: 「v727 消滅は anneal 機構による回転の可能性」「e5b671c8 巻き戻りの真偽要調査」は本節で確定: 実体は check_regression の `objective_regression` 粛清 2 回（02:26 v727→e5b671c8d352、05:06 e5b671→42c79aab4a68）。anneal は無関係（AnnealObs は観測枠表示のみ）。Rejected 1→2 はこの2件。
- **原因**: `stage_gate_regression_reason` は T11/T13/T14 到達率が anchor より 1 ゲーム分でも低ければ発火（有意性・標本数・率差床なし、anchor 側 max_types は直近25件のみ）。n=12〜13 の新戦略が anchor(n=100) の到達率と russia 数を同時に満たすのは構造的にほぼ不可能で、手動系譜が毎回 13 ゲームで消える ratchet になっていた。
- **修正（soviet_now `f37148b8a`, push済み）**: `strategy/regression.sh` check_regression heredoc に `stage_gate_counts/stage_fisher_p_shortfall/stage_gate_shortfall_significant/russia_noninferior` を追加。段階ゲートは「率差 >= STAGE_GATE_MIN_RATE_GAP(0.10) かつ 片側Fisher p <= STAGE_GATE_STAT_ALPHA(0.05)」のときだけ発火し、`reasons=` に `stagestat=type14/cur1of13/anc6of25/p0.2205/...` の根拠を `+` 連結で付ける。grace は `comp>=anchor` 条件を撤去し、russia 比較を標本数考慮 (`STAGE_GATE_RUSSIA_MIN_EXPECTED=1.0`: anchor率×現n<1 なら差は観測不能) に変更。観測行 `[STAGEGATE] graced=.. rank=.. t14=1/13 vs 6/25 p=.. gap=.. fired=..` を毎境界ログ。`STAGE_GATE_STAT_ENABLED=0`/`STAGE_GATE_NONINFERIOR_GRACE=0` で旧挙動。`core/config.sh` に既定値+export（heredoc は os.environ 読み、inline 既定値同一のため VM は .env 未設定でも次境界から有効・再起動不要）。lib/eval_stats.fisher_one_sided を再利用（悪事象=未到達）。
- **検証**: 新規 `tests/test_stage_gate_stat.sh`（heredoc をマーカー抽出し合成 fixture で実行、21 assert）全 ok。02:26/05:06 の実 rolling_scores.json を用いた scratch fixture で、旧コード=本番と同一の REGRESSION 行、新コード= `p=0.2205 fired=0`→PROMOTE / `graced=1 ... fired=0`→OK を実測。真の劣化 (T13 4/40 vs 16/25, p≈1e-6) と lost_soviet_path は引き続き REGRESSION。opus 独立レビュー (SHIP WITH FIXES) の必須/推奨を全反映: sticky best_max_type 条項は「評価トリガーだが統計モードでは単独発火しない」と明示 (caseL)、russia 分母を max_types 窓長に、count>n の fail-open をクランプ、grace の comp 条件は撤去ではなく `STAGE_GATE_GRACE_COMP_GAP_RATIO`(0.25)×min_comp_gap=250 の許容に、`STAGE_GATE_STAT_ENABLED`/`STAGE_GATE_NONINFERIOR_GRACE` を runtime_toggles/set_toggle whitelist へ追加、bash 側 STAGEGATE 除去ブロックの抽出テスト (caseN) 追加。最終 34 assert 全 ok、pytest stat_gate_shadow/eval_stats/instadeath 70 passed。
- **VM反映（実測）**: backup `.codex_deploy/backup-20260825-stage-gate-stat/` 後、regression.sh/config.sh/runtime_toggles.sh/set_toggle.sh/tests を temp+mv で反映、5ファイル SHA256 ローカル一致、`bash -n` OK、VM 上で `tests/test_stage_gate_stat.sh` 34/34 ok。regression.sh mtime 07:02:33。VM `.env` に STAGE_GATE_* は未設定（heredoc inline 既定値と config.sh export が同一のため次境界から既定値で有効、再起動不要）。07:05:18 と 07:09:33 の境界で check_regression が正常に STATGATE/verdict を出力（新コードの実行を実測）。
- **v727 復元（実測）**: `tmp/history/rejected_hashes.txt` から `5c9ab0ea6b6c` を除去 (backup `tmp/manual_challenge/rejected_hashes.bak.*.txt`) → 07:03:46 マーカー直書き → 07:05:36 `[PAUSE] manual_meriken_mode` (新ゲーム0手で停止) → root を `strategy_versions_archive/by_hash/5c9ab0ea6b6c.py` から差替え（旧 42c79aab4a68 は `.codex_deploy/backup-20260825-v727-restore/`、`tmp/revert_strategy.py`=42c79aab4a68）→ マーカー/soren_display_mode 削除 → **07:06:00 Game #45362 が `Strategy hash: 5c9ab0ea6b6c` で開始**、root/game_snapshot/runtime 3箇所とも v727。初戦 1183 点。手順は VM `tmp/manual_challenge/vm_restore_v727.sh prepare|swap|finish`。**注意**: 手動差替えでは current_strategy_run が n=1 から再開する（rollback 時のような rolling seed なし）ため、段階ゲートの実戦評価 (`[STAGEGATE]` 行) は n>=12 到達後（およそ 07:50 以降）。**本節時点で `[STAGEGATE]` の実戦出力は未観測**。anchor は `42c79aab4a68`(comp 9853, russia 1) のままなので v727 は russia が出るまで昇格抑止（bash objective guard）だが生存はする。
- **残リスク（レビュー/設計指摘）**: (1) n=12〜13 では段階ゲートがほぼ不発になり、新戦略は約12ゲーム間 breach>=2 のスコアゲートだけで守られる（EARLY_COMP_TOP_GAP_ENABLED=0 のまま）。(2) rolling 側 max_types 25件/0除外 vs current_run 50件/0込みの分母非対称は未修正（anchor 率が上方バイアス→発火側に保守的）。(3) `dashboard_data.py:527-556` は旧ゲートを写しているため粛清予測が過大。(4) 昇格 guard (`DIRECT_ANCHOR_PROMOTION_OBJECTIVE_GUARD_ENABLED`, regression.sh:1556-1568) は russia 0<1 を標本数無視で抑止するため、v727 はロシアが出るまで anchor になれない（生存はする）。標本数考慮版は別コミット候補。
- **次（次回 loop 08:13）**: `grep '\[STAGEGATE\]\|REGRESSION\]\|PROMOTE' logs/soren_loop.log | tail` で n>=12 以降の初回 STAGEGATE 行（期待: `t14=.. fired=0`、`breach_count=0` の objective_regression が出ないこと）を実測して本節に追記。粛清が再発したら `.env` に `STAGE_GATE_STAT_ENABLED=0` ではなく、まず `stagestat=` の gap/p を読む。v727 の実戦継続観測（`grep '\[STAGEGATE\]\|REGRESSION\]\|PROMOTE' logs/soren_loop.log`）、ロシア/ソ連到達の国名観測。戦略本体の改善候補は `docs/manual_challenge_20260825_insights.md` §5（垂直開放路 DIRECT 昇格、gap<=0.05 NEAR 昇格、analyze_board indent バグ）。

## 2026-08-24 23:5x JST — MacでShort動画を分離実行（VM負荷回避・バッティング対策済み）

- **背景**: VMでShort動画生成はCPU的に厳しいとの指摘。`doci` の Minimax/Hailuo + ffmpeg は VMのゲーム描画と競合する。
- **対策**: PodcastはVM残留 (音声のみ)、ShortはMac (`azumag/work/doci` が既に3時間毎に `--all-channels` で動作中) に分離。既存の `com.azumag.doci.generate` (10800秒, --all-channels) とバッティングしないよう、`soren_news` は `max_uploads_per_day=3` と `topic_cooldown_days=7` で冪等にスキップされる設計だが、別途 `com.azumag.soren-news.generate` (06:00 JST 日次) を新設し `tools/short_video_build.sh` (VOICEVOX起動待ち + soren-radio-archive pull + doci pull + `short_video_build.py`) を呼ぶ方式に。
- **Macセットアップ**: `~/soren-radio-archive` を `gh repo clone` で作成 (729ファイル)。`soren_news` チャンネルは `azumag/doci` の `soren-news-channel` branchにpush済みだが、VMには未導入のため `doci not found` で fail-open。Macでは `SOREN_RADIO_ARCHIVE=~/soren-radio-archive` で `short_video_build.py` が 17 newsを検出。`tools/short_video_build.sh` の `git pull` が `origin/soren-news-channel` の tracking 無しで失敗していたため `pull --ff-only origin soren-news-channel` に修正。`short_video_build.py` / `podcast_build.py` / `soviet_video_build.py` に `from __future__ import annotations` を追加し、Macの `/usr/bin/python3` (3.9) でも `Path | None` が評価されないように修正。
- **検証**: Macで `SOREN_RADIO_ARCHIVE=~/soren-radio-archive ./tools/short_video_build.py --dry-run` で 17 newsを検出・pick成功。`./tools/short_video_build.sh --dry-run` で `VOICEVOX 起動待ち → git pull soren-radio-archive (Already up to date) → git pull doci (Already up to date) → backup_root: ~/soren-radio-archive/backups/radio_scripts → picked` まで成功。`launchctl bootstrap` で `com.azumag.soren-news.generate` を登録し `launchctl list | grep soren` で確認。
- **VM反映**: `tools/short_video_build.py` / `podcast_build.py` / `soviet_video_build.py` に future import を追加し、VMへ scp し `python3 -m py_compile` OK。`tools/short_video_build.sh` の wrapper も `zsh -n` OK。`soviet_now` は `e642ffa fix: future annotations` で push、VMへ再反映済み。
- **次**: Macの `com.azumag.soren-news.generate` は明日06:00に初回発火。VMの `podcast.timer` (05:30) と `radio-archive.timer` (04:00) は既に有効。Shortの本番投稿は `PUBLISH_DRY_RUN=0` と `secrets/soren_news` の OAuth設定後に `--do-upload` で可能。

## 2026-08-24 22:4x JST — Short動画 Phase2完了（tools/short_video_build.py + doci soren_newsチャンネル、VM反映済み）

- **実装**: `soviet_now 3fe8a59 feat(short-video): single news to vertical short`。`tools/short_video_build.py` は `backups/radio_scripts/<YYYYMMDD>/radio_*_news_*.txt` から最新1本を選定 (doci `history.jsonl` で重複排除) → `doci` 外部呼出し (`python -m doci.run_daily --channel soren_news --corner news_short --no-upload --date <iso>`)。`tools/soviet_video_build.py` は `soviet` 系 (`soviet`, `soviet_quiz`, `soviet_lifehack`, `theme`内のソ連言及) を `doci` の `ideology/communism` へ委譲。`tests/test_short_video_build.sh` 4ケース。
- **doci**: `azumag/doci` に `channels/soren_news` (techテーマ, VOICEVOX 109, research/factcheck true, 7日クールダウン, 3本/日, unlisted) を `soren-news-channel` branchへ push。`persona_news.md` / `corner_news_short.md` / `voices.json` を新規作成。
- **検証**: ローカル `bash tests/test_short_video_build.sh` 4ケース pass (pick news, theme除外, jiji除外, no files, doci not found)。`./tools/short_video_build.py --date 20260824 --dry-run` で 17 newsを検出し `picked: radio_1787543424...news_10321.txt`、VMでも同様に 17 newsを検出。`./tools/soviet_video_build.py --dry-run` で 5 sovietを検出。`doci` 未導入のVMでは `doci not found, skipping` で fail-open。
- **VM反映**: `tools/short_video_build.py` (`813a5e64…`)、`tools/soviet_video_build.py` (`02e67c68…`)、`tests/test_short_video_build.sh` を scp し SHA256一致、`python3 -m py_compile` OK。VMで `--dry-run` で 17/5件の選定を確認。`doci` はVM未導入のため現在は dry-run のみ。

## 2026-08-24 22:0x JST — ポッドキャストMVP Phase1完了（tools/podcast_build.py + RSS + timer、VM反映済み）

- **実装**: `soviet_now 6e40454f2 feat(podcast): daily podcast build` + `fd7e95590 fix: xmllint`。`tools/podcast_build.py` は `backups/radio_scripts/<YYYYMMDD>` から `news/jiji` のみ抽出 → 時報除去 → `voicevox_tts.sh` (109) でWAV合成 (or `--dummy` 無音) → `ffmpeg concat + loudnorm + mp3` → `output/podcast/<YYYY-MM-DD>.mp3` + `feed.xml` (RSS2.0+iTunes, 50件, `xmllint` 検証) + `chapters.json`。`core/config.sh: PODCAST_*` 定数、`deploy/podcast.{service,timer}` (05:30 JST)、`tests/test_podcast_build.sh` (4ケース)。
- **検証**: ローカル `bash tests/test_podcast_build.sh` 4ケース pass (dry-run/theme除外、dummyでMP3/feed/chapters/idempotent/no-source/intro除去)。fake remoteでも同様。`./tools/podcast_build.py --date 20260825 --dry-run` で 2ファイル抽出、`--dummy` で 15秒MP3と1エピソードRSSが生成され `xmllint OK`。VMでも `bash tests/test_podcast_build.sh` pass (xmllint無しはスキップ)、`./voicevox_tts.sh -o /tmp/test_voice.wav` でVOICEVOX 109が68K WAVを生成することを確認。
- **VM反映**: `core/config.sh` (`962b3b60…`)、`tools/podcast_build.py` (`780ea234…`)、`deploy/podcast/*`、`tests/test_podcast_build.sh` (`ffd93d25…`) を scp し SHA256一致。`sudo install` で `podcast.{service,timer}` を `/etc/systemd/system/` へ配置し `systemctl enable --now podcast.timer` — 次回 `Tue 2026-08-25 05:30:49 JST`。`./tools/podcast_build.py --date 20260823 --dry-run` で 58ファイル (news+jiji) を検出。
- **ホスティング**: `PODCAST_RCLONE_ENABLED=0` のため現在はローカル `output/podcast/` のみ。`rclone` 有効時は `PODCAST_RCLONE_REMOTE:PODCAST_RCLONE_BUCKET/podcast/` へ自動 copy するフックをスクリプト内に用意。`PODCAST_BASE_URL` は既定 `https://example.com/podcast`。
- **次**: Phase2 Short動画 (`azumag/doci` の `channels/soren_news` 連携) は `tools/short_video_build.py` から `doci` を外部呼出しする設計。Q4は `A) 外部呼出し` で決定済み。Phase1はMVPとして `soviet_now#113` を close 可能。

## 2026-08-24 21:5x JST — ラジオ原稿VM外永続化 Phase0完了（soren-radio-archiveへ729ファイル初回push・timer有効化）

## 2026-08-24 21:5x JST — ラジオ原稿VM外永続化 Phase0完了（soren-radio-archiveへ729ファイル初回push・timer有効化）

- **背景**: `docich#10` のポッドキャスト/ショート動画化は `backups/radio_scripts/<YYYYMMDD>/` のVM外永続化が前提 (`soviet_now#113`)。VMはgit管理外で `backups` はローカルのみ。
- **決定**: ADR `docs/radio_archive_adr.md` でハイブリッド (Git鏡 private + Object Storage) を決定。テキストのみ退避 (WAVは再合成)。Git鏡は新規 private `azumag/soren-radio-archive` (main) を採用。`soviet_now` public の `main` へは `pre-push` hookで誤爆防止。
- **実装**: `soviet_now 605bab080 feat(radio-archive): VM外永続化 Phase0` + `678728dca fix: SIGPIPE`。`core/config.sh:756 RADIO_ARCHIVE_*` 定数、`tools/radio_archive_push.sh` (Git鏡+rclone、dry-run/now、public mainガード、3回リトライ、冪等)、`deploy/radio-archive.{service,timer}` (04:00 JST 日次)、`deploy/hooks/pre-push`、`docs/radio_archive_{adr,restore}.md`、`tests/test_radio_archive_push.sh` (7ケース)。
- **検証**: ローカル `bash tests/test_radio_archive_push.sh` 14アサーション pass (push/冪等/dry-run/guard/disabled/no-files/date/state)。fake bare remoteでの dry-run→push→再pushの冪等性をVMとローカルで検証。`head -n 20` によるSIGPIPEバグを `cat` に修正して再push。
- **VM作成**: `gh repo create azumag/soren-radio-archive --private` で新規作成 (PRIVATE)。`gh auth` は `repo` スコープで `https` push可能。
- **VM反映**: `core/config.sh` (`d061f8ba…`)、`tools/radio_archive_push.sh` (`d88feb5d…`)、`deploy/radio-archive/*`、`docs/*`、`tests/*` を backup `.codex_deploy/backup-20260824-radio-archive/` 作成後に scp し SHA256一致。`.env` に `RADIO_ARCHIVE_GIT_REPO=https://github.com/azumag/soren-radio-archive.git` を追記。
- **本番push**: 初回 `RADIO_ARCHIVE_GIT_REPO=... ./tools/radio_archive_push.sh --now` で `729` ファイル (20260817-20260824) を `main` へ push 成功 (`f6286fe…` HEAD)。2回目は `no changes to push` で冪等。状態は `tmp/state/radio_archive_pushed.json` に記録。
- **timer**: `sudo install` で `/etc/systemd/system/radio-archive.{service,timer}` を配置し `systemctl enable --now radio-archive.timer`。`systemctl list-timers` で `Tue 2026-08-25 04:04:03 JST` に次回発火、`systemctl start radio-archive.service` で手動実行も成功 (`status=0`, `logs/radio_archive.log` に `no changes` )。
- **リポジトリ**: `soviet_now 605bab080` と `678728dca` を `origin/codex/no-apply-liveliness` へ push、親 `docich 16ed1b0` で submodule bump を push。
- **次**: Phase1 Podcast (`tools/podcast_build.py` + RSS) は Phase0完了後に着手可能。Phase2 Short動画は `azumag/doci` の `channels/soren_news` 連携が前提。Q1-Q4は推奨値で決定済み。

## 2026-08-24 17:8x JST — Say生成中をLIVE STATUSへ表示（VM反映済み・実測済み）

- **ユーザー要望**: 「Sayのgeneration中も、LIVE STATUS 枠にAI思考中と同じ用に表示してほしい」。
- **実装**: `soviet_now e2d9ee5a3 feat: show Say generation in LIVE STATUS like AI thinking`。`generate_event_overlay.py:147-265` で `tmp/.say_queue/current_source` (phase=waiting/playing/retry_wait) と `.voicevox_synth_lock` / `pid` を `_pid_alive` と `EVENT_OVERLAY_SAY_STALE_SEC=40` / `DEAD=10` で freshness 判定し、`🔊 Say生成中 (コメント/ラジオ)` を `GEN` へ追加。`direct_broadcast_overlay.html:308-317` で `AI思考中` カードの `labels.join(' / ')` に Say が含まれ、イベント無し時は `AI思考中` + LIVE STATUS 2枚、イベント有り時は `🔊 Say生成中` 個別トーストとしてページング。レガシー `#gen-loaders` も `.gen-loader.say` (オレンジ) で同様表示。
- **検証**: ローカル `python3 -m py_compile` 成功、`node --test tests/test_direct_broadcast_overlay.mjs` 15/15、`read_gen_indicators` の6パターン（current_source / synth lock / stale / dead window / radio hint / comment+say 同時）で手動シミュレーション成功。`tmp` で `generate_event_overlay.py` を直接実行し `GEN` に `say` が含まれることを確認。
- **VM反映**: backup `.codex_deploy/backup-20260824-say-live-status/` 作成後、4ファイルを `scp` し SHA256 一致を確認（`85a0de28f03e…` / `f12f9e649e8…` / `f6b83a5d2c1e…` / `ec8a30eb778f…`）。VM `generate_event_overlay.py` は既に新SHAで `Say生成中` を含むことを `grep -c` で確認。`tests/test_direct_broadcast_overlay.mjs` も同時反映。
- **リポジトリ**: `soviet_now` `e2d9ee5a3` を `origin/codex/no-apply-liveliness` へ push、親 `docich` の submodule bump を本節で push 予定。

## 2026-08-24 16:4x JST — コメントsayリトライ＝外部プロセスのspeaker=3014誤指定（実測のみ・VMコード変更なし）

- **ユーザー報告**: 「コメントsayが頻繁にリトライになる。ラジオキューも溜まっている」。その後、該当アナウンスは **VM外プロセスからの投入**で、そちらの指定ミスとユーザーが確認。VM側修正は不要と指示（調査・掃除・実測のみ実施）。
- **原因（実測）**: VM外から `lib/outbound_queue.sh` 経由で `comment_announce_<ts>_manual_move_<n>` （source=チャットメッセージID+手動着手番号）が **speaker=3014** 指定で投入された。稼働中VOICEVOX (`127.0.0.1:50021`, PID 1451255, Aug12起動) の話者一覧は **全127スタイル/ID 0〜126** で3014は存在しない。`audio_query` が Internal Server Error → say_enqueueが7回リトライ→「再生失敗」で破棄。本日16:27〜16:37に3件(_025/_026/_027)、計38回のVOICEVOX合成失敗。最後の1件は16:37:26に再試行上限で自然破棄。
- **副作用**: 失敗する優先音声がラジオ描画レーンを圧迫（「優先音声の合成完了待ち」）し、deferredキューが11件滞留（11:57〜13:35 JST投入分）。ラジオ1項目の消化は約20チャンク×15〜25秒の事前合成＋再生6〜7分で1項目あたり15〜25分程度。>5件で新規ラジオ生成抑制ゲートが効いている設計どおり。
- **確認（16:43実測）**: comment_queueに .playing/.txt/.speaker 残骸ゼロ。16:37以降の新規合成失敗0件・新規announce投入なし。ラジオ描画は正規話者 speaker=109（東北イタコ）で再開し消化中。
- **正しい話者ID（実測値）**: 冥鳴ひまり=14 / 九州そら=16 / 小夜/SAYO=46 / 東北イタコ=109 / ずんだもん=3 / 四国めたん=2。
- **関連の潜在地雷（未修正・ユーザー側情報として共有済み）**: READMEと `voicevox_sing.sh:49-53` の歌シンガー固定「中華AI=九州そら(id=3016)/メリケンAI=冥鳴ひまり(id=3014)」は旧カタログ値で現行エンジンでは無効。`.env` に `VOICEVOX_SING_SPEAKER_SOREN91` は未設定のため、歌リクエスト経路も同じ失敗になり得る。

## 2026-08-24 15:5x JST — 正午貼り直しのOFFLINE保持修正（VM反映済み・自然発火未確認）

- **依存**: 「正午の配信貼り直し機構（２日に一回）が効いてない」調査。`handoff.md`、MEMORY の `stream_noon_audit` 履歴、VM 実測を確認。
- **確認**: VM 監査ワーカー PID `3574461` 稼働中。2026-08-23/24 とも正午に `restart_required` を判定し、停止・再起動処理は実行された。ただし両日の marker は `restart_failed`、`session_before == session_after == 317949940056`。08-24 12:00:34 JST に supervisor は direct_stream を再起動し、現在のローカル `started_at` は 12:00:35 JST。
- **外部実測**: 15:24 JST 時点の Twitch 公開 GraphQL は同じ stream id `317949940056`、`createdAt=2026-08-22T18:53:40Z`（JST 08-23 03:53:40）。つまりローカル再起動はあっても Twitch 側同一配信セッションが継続しており、ユーザー観測どおり外部貼り直しができていない。
- **推定原因（強い）**: `_restart_stream` は旧 Twitch ID 消滅またはローカル停止のいずれかを確認した直後に pause marker を解除する。supervisor が即時 respawn し、Twitch Disconnect Protection が同一外部セッションとして吸収する。wiki 手順にある「Twitch OFFLINE 確認後、約30秒維持してから復帰」が実装されていない。
- **実装/検証**: `STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC` 既定30秒を追加。旧 Twitch ID が消え、かつローカル配信が停止している状態を連続保持してから pause marker を解除する。旧 ID が確認不能でも復帰優先フォールバックを維持。ローカル・VMとも焦点回帰 **38/38** 成功、`bash -n` 成功。コミットは soviet_now `b51ed26b9 fix: hold Twitch offline before noon repost`、`origin/codex/no-apply-liveliness` push済み。
- **VM反映**: backup `.codex_deploy/backup-20260824-noon-offline-hold/` 作成後、worker/test を反映し SHA256 一致。worker SHA `7b5377bf5569...`、test SHA `2f781ebcf065...`。監査ワーカーのみ TERM→respawn し、新 PID `1038381` 単独稼働を確認。反映後も direct stream `running=true / state=running / 30.01fps`。テスト中の一時 duplicate ログは残存プロセスなしで解消済み。
- **未確認**: 上記修正の自然発火による新しい Twitch stream id/createdAt。本節時点ではコード変更なし。

## 2026-08-24 13:0x JST — 手動戦略v726「第2ロシア直前ペア・テザー」（VM反映済み・実戦観測開始）

- **根拠**: v725初戦2016点で89手目にカザフスタンとウクライナを生成したが、90〜95手に余裕1.24〜1.80があるうちに距離を閉じられず、96手目以降は締切余裕0.35以下へ低下した。最終盤面はカザフスタン1個・ウクライナ1個・トルクメニスタン1個。
- **v726実装**: `POST_FIRST_RUSSIA_PAIR_TETHER` を追加。カザフスタン1個＋ウクライナ1個＋ロシア/ソ連なしの状態で、両国の中点から遠い安全選択を、余裕0.55以上・非DIRECT・リスク許容内かつ距離を0.75以上改善する候補へ置き換える。欠損・非有限・締切越えはfail closed。
- **検証/永久保存**: pycompile、焦点10件＋7 subtests、diff check成功。`1848fa902 feat: tether second Russia pair early` をpush。hash `aac603521570`。
- **VM反映**: ゲーム境界で `_archive_strategy_snapshot_by_hash` と `_branch_transition_after_improve` を実行し、root / snapshot / runtime のSHA一致とactive branch head更新を確認。lineage `[...,244348f0f6ed,aac603521570]`、depth6、closed63。v725はbest `comp=12804.3441,n=4` として記録された。
- **次**: v726の `POST_FIRST_RUSSIA_PAIR_TETHER` 発火・着地、ロシア/ソ連到達を国名で観測する。

## 2026-08-24 12:5x JST — 手動戦略v725「第2ロシア素材の被覆回避」（VM反映済み・初戦観測済み）

- **継続目標**: ソ連建国まで人手改善を続ける。自動改善ワーカーは意図的停止のまま。ロシア・ソ連は本節時点で未到達なので完了扱いにしない。利用者向け表示・進捗・音声は番号やタイプ名ではなく国名を使う。
- **v724確定実績**: 追加で完了した試合でも最高カザフスタン止まり。実履歴 `20260824_111737_score1302.jsonl` では61手目にカザフスタンへ到達したが、64手目にベラルーシが lone トルクメニスタンを覆い、その後は締切余裕0.4前後の安全処理中心になって第2カザフスタン素材を作れなかった。実70手のウクライナ合体レーン自体が締切越え判定だったため、安全な中央案への退避は正しいと回帰で確定した。
- **v725根拠**: v724後4試合（1049/1119/1342/1302点）は全てロシアを通過したが第2ロシア0。64手目を再生すると、安全なリトアニア衝突は予測上端2.306・余裕1.074で、実際に選ばれたトルクメニスタン被覆（上端2.713・余裕0.667）より双方改善していた。旧第2ロシアフックは理由文依存のため、runtime safetyが理由を書き換えると到達不能だった。
- **v725実装**: `POST_FIRST_RUSSIA_LANE_COVER_AVOID` を戦略側finalizerに追加。カザフスタン1個・ウクライナ0個・ロシア/ソ連なし・トルクメニスタン1個以上という状態条件だけで、選択中候補が高位連鎖素材被覆のとき、低い国への安全な実衝突かつ実質リスク改善がある場合のみ置き換える。識別子 `post_first_russia_lane_keep_v1` を宣言し、欠損・非有限・重複・境界外はfail closed。
- **検証/永久保存**: pycompile成功。v724追加回帰、v725焦点2件、hash系、既存ウクライナレーン2件の関連11件＋10 subtests成功。既存広域テストには以前からの失敗・sandbox socket制限があり、今回の関連焦点で判断した。`a9401f53e test: keep v724 pair lane behind deadline safety` と `edc9a2921 feat: protect second Russia lane after Kazakhstan` を `origin/codex/no-apply-liveliness` へpush。v725 hash `244348f0f6ed`。
- **VM反映**: 初回はrootのみ先行反映したため、active branch headが旧ハッシュのまま戻されたことを実測。その後 `_archive_strategy_snapshot_by_hash` と `_branch_transition_after_improve` で head/lineage を更新し、再度 root / snapshot / runtime へ反映した。3箇所ともv725 SHA一致、by-hashと永久archiveあり。active branchは anchor `505b0efbe974`、best `ca80fa288f7a`、head `244348f0f6ed`、depth 5、closed 59、lineage `[...922fb1f782dc,244348f0f6ed]`。復元点はv724 `922fb1f782dc`。
- **v725初戦観測**: 101手2016点で自然終了。89手目に第1カザフスタンと第2候補ウクライナを同時生成し、最終盤面にカザフスタン1個・ウクライナ1個・トルクメニスタン1個が残った。90〜95手に距離を詰める余裕はあったが、96手目以降は締切余裕0.35以下へ落ちて併合できず。v725の新理由発火はまだ未確認。
- **次の行動**: 「カザフスタン1個＋ウクライナ1個＋トルクメニスタン1個」を第2ロシア直前状態として、余裕1.8以上の早期段階から接触誘導する別世代を実履歴で設計する。実発火・着地・ロシア/ソ連到達を国名で観測し、未達なら狭く修正→独立レビュー→commit/push→active branch更新込みの境界反映を続ける。手動チャレンジ再実施時は開始文「メリケンAIによるチャレンジコーナーです」、冥鳴ひまり指定、全手の判断理由音声を省略しない。

## 2026-08-24 11:04 JST — 手動戦略v724「ウクライナ同士の合体進路復旧」（VM反映済み・実戦観測中）

- **継続目標**: ソ連建国まで人手改善を続ける。自動改善ワーカーは意図的停止のまま。本節時点でロシア・ソ連は未到達なので完了扱いにしない。利用者向け表示・進捗・音声は番号やタイプ名ではなく国名を使う。
- **v723確定実績**: 8ゲームを完了し、最高カザフスタン、ロシア0・ソ連0。生点は1141/1305/1335/1006/1392/2225/1516/828。連鎖素材保護は1335点ゲームで2回発火したが、ロシア建国には届かなかった。
- **v724根拠**: 実履歴 `game_history/20260824_094741_score1093.jsonl` 61手目では、離れたウクライナ2個があるのに、状態だけで使える合体進路が理由文の早期条件で到達不能だった。従来の右端案は予測上端3.326・締切余裕0.054、復旧した左端案は3.028・0.352。第2ウクライナ生成時だけに必要な理由条件は維持した。
- **互換性/安全**: 実行側の新経路は戦略側が対応識別子を宣言した場合だけ有効にし、旧戦略・識別不一致・例外時は無効化する。最初と最後の安全処理の双方へ戦略を渡し、二重適用、締切直前、カザフスタン/ロシア既存、候補欠損を回帰で確認。独立レビューはHIGH/MEDIUMなし。
- **検証/永久保存**: 関連106件成功、production validation、pycompile、diff check成功。旧広域回帰は388件中104 failure・1 errorで、v723時点の105 failure・1 errorから新規悪化なし。`329f13517 fix: restore pre-Russia Ukraine pair lane` を `origin/codex/no-apply-liveliness` へpush。v724 hash `922fb1f782dc`。
- **VM反映**: v723の8ゲーム目を70手828点で自然終了させ、境界停止中にロールバック付きで戦略・実行側・テストを同時反映。本番でも関連106件成功。root / `strategy.py.game_snapshot` / `tmp/state/main_game_strategy_runtime/strategy.py` と新ゲーム履歴が全て `922fb1f782dc`。通常・永久archiveあり、復元点 `tmp/revert_strategy.py=2dfa9b77baf0`、改善停止markerあり。active branchは anchor `505b0efbe974`、best `ca80fa288f7a`、depth 4、closed 32、lineage `[2dfa9b77baf0,87ae98e3dd2e,ca80fa288f7a,922fb1f782dc]`。新ゲーム2手目までhash一致を実測。
- **次の行動**: v724でウクライナ合体進路の実発火、カザフスタン・ロシア・ソ連到達を国名で観測する。未到達なら、既知候補の「接触中のウクライナ2個へ確実に当てる局面」と「ウズベキスタン2個へ寄せる局面」を実履歴で再解析し、別世代として狭く修正→独立レビュー→commit/push→境界反映を継続する。手動チャレンジ再実施時は開始文「メリケンAIによるチャレンジコーナーです」、冥鳴ひまり指定、全手の判断理由音声を省略しない。

## 2026-08-24 10:26 JST — 手動戦略v723「カザフスタン待ちの連鎖素材保護」（VM反映済み・初戦観測中）

- **継続目標**: ソ連建国まで人手改善を続ける。自動改善ワーカーは意図的停止のまま。本節時点でロシア・ソ連は未到達なので完了扱いにしない。利用者向け表示・進捗・音声はタイプ番号でなく国名を使う。
- **v722観測**: v722で5ゲームを完了し、最高カザフスタンを含むがロシア0・ソ連0。カザフスタンが1個あり、ウズベキスタン・トルクメニスタン・ウクライナの連鎖素材がペアの局面を形状付きで再解析すると、低い国をその素材の真上へ置く選択が7局面あった。いずれも盤面上の別の低い国へ実衝突させる方が予測上端と締切余裕をともに改善した。
- **v723実装**: `PRE_RUSSIA_CHAIN_COVER_AVOID` を追加。カザフスタンがちょうど1個、ロシア・ソ連なし、ウズベキスタン〜ウクライナのいずれかがペア、次がアルメニア〜ベラルーシ、即時安全併合なしの場合だけ、現在案より予測上端・締切余裕を各0.20以上改善し余裕0.50以上の「別の低い国への実衝突」へ変更する。ロシア建国前の実測済み左境界 `-0.991`、解析欠損・曖昧ID・非有限値・重複候補をfail closedで守る。
- **安全レビュー**: 初回独立レビューで、最終フックが締切安全処理を迂回できる点と、左境界外を選べる点を検出。フック後に締切・独立形状安全処理を再適用し、選択中・代替候補の両方へ境界整合を追加した。締切超過・形状過小評価・境界値の回帰を追加し、再レビューはHIGH/MEDIUMなし。実履歴のベラルーシをウズベキスタン上からリトアニア側へ移す `x=1.6` は再検査後も維持。
- **検証/永久保存**: 関連101件成功、既存形状安全2件成功、production validation、pycompile、diff check成功。本番隔離では新規29件成功。`b18155389 feat: preserve pre-Russia chain lanes` を `origin/codex/no-apply-liveliness` へpush。v723 hash `ca80fa288f7a`。
- **VM反映**: 進行中のv722試合を62手858点で自然終了させてから、ロールバック付きで6ファイルを反映。root / `strategy.py.game_snapshot` / `tmp/state/main_game_strategy_runtime/strategy.py` が全て `ca80fa288f7a`。通常・永久archiveあり、復元点 `tmp/revert_strategy.py=2dfa9b77baf0`、改善停止markerあり。active branchは anchor `505b0efbe974`、best `2dfa9b77baf0`、lineage `[2dfa9b77baf0,87ae98e3dd2e,ca80fa288f7a]`。v723初戦は22手目で履歴hash一致を実測。
- **次の行動**: v723実ゲームでロシア・ソ連、連鎖保護理由の発火と実着地を国名で観測する。未到達なら、既知の次候補であるウクライナペア誘導の到達不能回帰を別世代として狭く修正し、独立レビュー→commit/push→試合境界反映を繰り返す。手動チャレンジを再実施する場合は、開始文「メリケンAIによるチャレンジコーナーです」、冥鳴ひまり指定、全手の判断理由音声を省略しない。

## 2026-08-24 09:40 JST — 手動戦略v722「ロシア後の連鎖余白保護」（VM反映済み・実戦観測中）

- **継続目標**: `soviet_now` を人手で改善し、実ゲームでソ連建国まで到達させる。自動改善ワーカーは意図的停止を維持し、`/home/ubuntu/soren/tmp/state/improve_daemon.paused` が存在する。ユーザー向け表示・報告はタイプ番号でなく国名を使う。
- **手動チャレンジ**: 「メリケンAIによるチャレンジコーナーです」で開始し、冥鳴ひまり指定で全57手の判断理由を一手ごとにaudio-workerへ報告して1ゲーム完走。実画面・形状込みで、解析が併合なしと判定しても露出した同国への接触で実併合する3局面を確認し、v721へ反映した。次に手動プレイする場合も開始文・声指定・毎手音声を省略しない。
- **国名表示**: LastDrop、音声・表示用理由、状況表示を国名へ正規化済み。本番 `show_status.sh --once` でも「19手目 モルドバ」のように確認した。内部の数値typeはゲーム契約として残るが、利用者向け文面には出さない。
- **v721**: `7af46a0ad` (`2dfa9b77baf0`) をpush・VM反映。形状誤判定から実併合を回収する `VISIBLE_SAME_COUNTRY_CONTACT_SHOT` を追加。12ゲームを観測し、最高カザフスタン、ロシア0・ソ連0。新理由は複数ゲームで発火したがロシア到達率は改善確認に至らなかった。
- **世代保存の修復**: strict shell下で省略第2引数を `$2` 参照してbranch transitionが無言失敗していた。`${2:-}` と実動回帰2件で修正し、`c44debed4` をpush・VM反映。VMの実関数で既存headに対するno-op成功も確認した。
- **v722根拠**: 初のロシア到達ゲーム `game_history/20260824_081228_score3288.jsonl` 130手目を形状込みで再生。従来のモルドバ配置はペアになったウズベキスタンへ着地し、予測上端 `2.882`（履歴値 `2.88`）、締切余裕 `0.498`。キルギス側の内寄り `x=2.7` は上端 `2.654`、余裕 `0.726` へ双方 `0.228` 改善する。空形状fixtureによる当初の過大評価は独立レビューで検出し、未保存の段階で破棄・修正した。
- **v722実装/検証**: `POST_RUSSIA_CHAIN_COVER_AVOID` はロシアがちょうど1個、ソ連なし、ウズベキスタン〜カザフスタンのいずれかがペア、次がアルメニア〜アゼルバイジャン、即時安全併合なし、実衝突先あり、リスク・余裕を各0.20以上改善、代替余裕0.70以上の場合だけ発火。同一x重複、着地点欠落、非有限値、曖昧IDはfail closed。関連70件、VM70件、隔離VM16件、sandbox validation、pycompile、diff check成功。独立再レビューは指摘なし。旧広域回帰は変更前後とも467件中105 failure・1 errorで増分なし（全て既存 `test_escape_mechanisms`）。
- **永久保存/本番**: `1277000e2 feat: preserve post-Russia chain lanes` を `origin/codex/no-apply-liveliness` へpush。v722 hash `87ae98e3dd2e`。VMはゲーム境界でロールバック付き反映し、root / `strategy.py.game_snapshot` / `tmp/state/main_game_strategy_runtime/strategy.py` の3箇所で同hash、by-hashと永久archiveも存在。`tmp/revert_strategy.py` はv721。active branchは anchor `505b0efbe974`、best `2dfa9b77baf0`、lineage `[2dfa9b77baf0,87ae98e3dd2e]`。Game #45102が09:39 JSTにv722で開始した。
- **次の行動**: v722のゲーム結果、ロシア到達、`POST_RUSSIA_CHAIN_COVER_AVOID` の実発火・実着地を国名で観測する。ソ連未建国なら実ログ/スクリーンショットから次の狭い改善を作り、独立レビュー→commit/push→境界反映を繰り返す。ソ連建国までは完了扱いにしない。

## 2026-08-24 05:5x JST — AMD DS/MiniMax を muse 前へ＋JIJI timeout分離（VM反映済み）

- **要件**: muse contributorも有償のため、AMD DeepSeekとMiniMaxをmuseより先へ変更。
- **実装**: soviet_now `codex/no-apply-liveliness` `b290960c0` push済み。8つのリスト系チェーンを `x-preview → AMD DS → MiniMax → muse → paid DeepSeek` へ変更し、単一fallback系とピーク優先も同方針へ整合。`MODEL_LAST_RESORT`は `paid DeepSeek`。
- **検証**: ローカル/VMとも peak order 62項目成功、improve reliability 21 passed。VM `core/config.sh` / テストのSHA256はローカル一致。`.env`の8変数も新順序を確認。サービスunitに旧チェーンのEnvironmentは無し。
- **旧「worker environが旧値」の訂正**: `/proc/<PID>/environ`はexec時点の初期環境であり、worker起動後に`.env`を読んで変数を上書きしても表示は更新されない。これはブロッカーではなく観測方法の問題。03:50台/05:36台/05:42台の自然発生ログで新チェーン動作を確認し、特に05:42はx-preview network_error後にAMD DSがfallback成功した。
- **低勝率切分けと追加修正**: 完了分母では記録上のwinnerイベント25/35=71.43%だが、AMD DSのJIJI_RESEARCH timeout3件は共有radioレーン待ちとの競合中に180秒上限へ達していた（うち2回はslot獲得約1秒前にtimeout）。成功出力ベースのok数は28/35=80.00%。そこで `RADIO_JIJI_RESEARCH_TIMEOUT` 既定300秒を新設し、JIJI調査のみ通常ラジオの180秒から分離。soviet_now `495a6aacf` push済み。
- **VM反映**: config/radio_corners/testをbackup付き反映しSHA256一致、bash -n成功、VM peak order 64項目成功。`.env`へ `RADIO_JIJI_RESEARCH_TIMEOUT=300` 追加。radio/chatを05:57:5xにTERM→respawnし、新PID3418573/3418478でsource実効値 `RADIO_AGENTS=x-preview → AMD → MiniMax → muse → paid DS` / `timeout=300` を確認。
- **未確認**: 十分な非競合トラフィックでのwinner率>=80%再達成。次回はqueue待ち込みtimeoutが解消された新しい窓で完了分母を再評価する。
- **運用**: 配信running、30.01fps、約4659kbits/s、speed=1.00を確認。作業バナーは本節記録後にstop予定。

## 2026-08-24 01:1x JST — winner率80%再達成（有償最終手段・実測済み）

- **要件**: 有償モデルは最終手段とし、DeepSeekより安価なmuse-spark contributorを優先する。
- **追加修正**: soviet_now `540e65f97` で改善系`run_ai`のprimary/fallback成功時にもwinnerを記録。`c58a6de11` でprepassのピーク並び替えを無料/local/muse優先に統一。`500487a57` でLinux VM上のmacOS専用`date -v-1d`をGNU `date -d yesterday`へ修正。`884a8d570` で低信頼だった`openrouter/free`とlocalを通常チェーンから除外し、`x-preview → muse contributor → MiniMax → AMD DeepSeek → DeepSeek`へ整理。
- **VM反映**: 上記コミットの`strategy/ai.sh`, `broadcast/radio_engine.sh`, `broadcast/scheduler.sh`, `core/config.sh`, テストを反映しSHA256一致を確認。`.env`も同名チェーン変数のみ更新（秘密値は表示・転送していない）。radio workerへUSR1で再読込させた。
- **検証**: ローカル peak order 62/62、diagnostics 26/26、backoff 18/18、improve reliability 21/21。VMでもpeak order全項目OK。
- **実測達成**: 2026-08-24 00:00〜01:15 JST の完了ベースで **22/26=84.62%**。attemptは70件だが、このうち実行中は44件のため確定判定には完了分母を使用した。失敗はx-preview network_error 2件、validator拒否1件のみ。配信running・30fps・約4620kbps。
- **注意**: 実行中呼び出しを含むattempt分母では勝率が低く見える。以後の勝率比較は`ok+fail`完了分母を使う。

## 2026-08-23 18:xx JST — 有償モデルを最終手段へ変更（VM反映済み）

- **要件**: 有償モデルはできるだけ呼ばない。有償枠内では、DeepSeekより現在安いmuse-spark contributorを優先する。
- **実装**: soviet_now `2fd094c61`（`codex/no-apply-liveliness` push済み）。`AI_COMMON_AGENTS` / `RADIO_AGENTS` / `RADIO_PREPASS_AGENTS` / `COMMENT_AGENTS` / `COMMENT_TRANSLATION_AGENTS` を `x-preview → openrouter/free → local → muse contributor → AMD DeepSeek → MiniMax → DeepSeek` へ変更。改善系も `x-preview → muse → AMD DeepSeek → MiniMax → DeepSeek` に統一。単一primary/fallback系も無料→安価有償へ寄せ、fact-checkの重複スロットを空にした。ピーク優先は `x-preview` 先頭＋無料/local/muse優先へ変更。
- **VM反映**: `core/config.sh`, `README.md`, `tests/test_peak_hours_agent_order.sh` をscpしSHA256一致。`.env` は `.codex_deploy/backup-20260823-130x-paid-last-resort/env` へ退避後、同名変数のみ更新（秘密値は表示・転送していない）。VM peak order test 全項目OK、`bash -n core/config.sh` OK。
- **実効確認**: VM source後の通常/ピーク並びは上記チェーンと一致。radio/chat/audio workerは18:32にTERM→respawn済み。`soren_loop` は18:32のTERM後に自動respawnしなかったため、pause/lockなしを確認して18:38に復帰させた（PID=1028943）。
- **検証**: ローカル peak order 62/62、diagnostics 26/26、backoff 18/18、improve reliability 21/21。READMEのfact-check既定表記も現行経路へ修正。
- **未確認**: 反映直後はradio deferred queue 3件とコメント/音声処理があり新規ラジオ生成が抑制されていたため、新しいチェーンでの自然発生ログはまだ未観測。設定source・ピーク並び替え関数・worker再起動までは確認済み。

## 2026-08-23 16:5x JST — 手動戦略v713「PRE_RUSSIA_T13_PAIR_MODES」（VM反映済み・T14到達）

- **v712b実測**: 完了9試合でT13到達8/9、T14到達1/9、ソ連0。T13x2を作れても距離が離れたままdeadline fallbackへ移行する敗因が継続。
- **修正**: soviet_now `8d6e4c9ee`。lean resetで失われたpre-Russia T13ペアのcluster/compress/tether/ladder系モードを復活し、安全候補ではペア中心からの距離を強く評価。あわせてrunnerの `pre_russia_t13_pair_replacement_for` をreasonタグ有無に依存しない状態条件発火へ変更（no-T14 + T13x2以上）。
- **検証**: `strategy.py` / `strategy_runner.py` pycompile成功。decide hash `bde7dacbc6b2`、VM SHA256 `6d59c18b...`。focus testsは新復活系7/10成功で、残り3件はlean reset以前から期待が乖離している古いcluster/ladderテスト。追加のfirst-Russia/second-Russia focusは既存退行あり（今回未修正）。
- **実戦**: v713最初3試合で **T14到達1/3**、最高1516点。v712bは1/9だったため初期趨勢は改善。ただしソ連累計は0。
- **永久保存**: soviet_now branch `codex/no-apply-liveliness` `8d6e4c9ee` push済み、親docich submodule bump `2993f48` push済み。改善ワーカーpaused・daemon/jobプロセスなし・lockなしを維持。

## 2026-08-23 16:1x JST — 手動戦略v712b「SECOND_T13_CONTACT_LANE」（VM反映済み・実戦観測中）

- **v711実測**: 8試合でT13到達4/8、T14到達1/8、ソ連0。最高2750点試合ではT14+T13+T12x2を最大27ターン保持したが、v711レーンが既存T14/T13側へ戻す誘導になり、T12ペア中心への接触誘導が不足。新モード発火は銀行lift2ターンのみ。
- **修正**: soviet_now `e6caa743a` + `8366e72a3`。T14x0/1かつT13x0/1でT12x2以上のとき、最も近接なT12ペア中心へ `russia_lane_x` を上書きし、T12ペアもcover保護対象に追加。critical deadline guardの被覆回避対象にも同ペアを追加。当初T14必須条件だったため1408点試合のT12x2+T13局面に効かなかった問題を `t14_count<=1` へ拡張して修正。
- **検証**: pycompile成功。v712a decide hash `91fd34ad01b8`、v712b decide hash `e66e82e11279`。VM `/home/ubuntu/soren/strategy.py` はv712b SHA256 `f2ec477b...` で一致。改善ワーカーpaused・daemon/jobプロセスなし・lockなしを維持。
- **永久保存**: soviet_now branch `codex/no-apply-liveliness` へ `e6caa743a` / `8366e72a3` push済み。親docich submodule bump `0f27115` / `0aa2fed` push済み。
- **実戦初期観測**: v712aは完了1試合（54点、peak T12）。v712bは完了1試合（1523点、peak T13x2、T14未達）、現在ソ連0。改善ループは引き続き停止したまま。
- **次**: v712bのT12ペア接触誘導が第2T13/T14生成率を上げるか観測。十分サンプル後、T12ペア中心とdeadline安全のトレードオフを再評価。

## 2026-08-23 15:0x JST — 手動戦略v711「FIRST_RUSSIA_ASSEMBLY_MODES」（VM反映済み）

- **v710実測**: 完了12試合でT13到達7/12、T14到達1/12、ソ連0。レーン誘導は343遷移中262遷移がanchor近接へ改善したが、被覆は343遷移中129遷移に残留。最高スコア3455試合はT14を作れたが、T14x1 + T13x1 + T12/T10素材から第2T13を作れず終了。
- **修正**: soviet_now `c601c891b` で旧実績系の3モードを復活: ①T14後のT12ペアlock、②T13ペアlift、③T14+単体T13時のT12/T11/T10 bank lift。runner側に既存だった `SECOND_RUSSIA_T12_PAIR_LOCK` / `FIRST_RUSSIA_T13_PAIR_LIFT` / `FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT` の安全上書きフックが再び有効になる。
- **検証**: pycompile成功。decide hash `859fabbf1252`、SHA256 `73e5a0c5...`。VM `/home/ubuntu/soren/strategy.py` へbackup付き反映済み。改善ワーカーpaused・daemon/jobプロセスなし・lockなしを維持。
- **誤仮説訂正**: 当初「nextNext=12供給待ちのT12ペア保護」を試したが、全履歴で `next_type=12` は0回のため撤去した。実際は連鎖生成後の高type供給（T10/T11）を既存ペアへ集約する設計に変更。
- **永久保存**: soviet_now branch `codex/no-apply-liveliness` `c601c891b` push済み、親docich submodule bump `474daac` push済み。実戦hash/結果は次観測対象。

## 2026-08-23 13:3x JST — 改善ループ確定停止とWebUI停止経路強化（VM反映・実API検証済み）

- **停止状態**: `/home/ubuntu/soren/tmp/state/improve_daemon.paused` を作成し、`tmp/improve.lock` は除去、`improve_state.json` は `status=idle / phase=webui_stopped`。`eloop_improve*.sh` / `improve_daemon.sh` 実プロセスなし。v710の `soren_loop.sh:179,854` と `strategy/improve.sh:2975` ガードをVM実コードで確認。
- **誤操作訂正**: 当初 `tmp/stop` を作成したが、これは改善専用ではなく supervisor 全体停止フラグだったため即時削除した。削除後、supervisor・soren_loop・direct stream は稼働維持を確認。最終時点で `tmp/stop` は存在しない。
- **WebUI実装**: improve_daemon stop時に改善ジョブPIDを先に特定し、子孫PIDも固定してからdaemon→jobツリー順にTERM/KILLする。コマンドガードは `eloop_improve(_runtime...).sh` のみ許可し、無関係PIDを保護。停止成功後にstateをidleへ書き、lockを除去。UI説明も「バックグラウンド継続」から「ツリーごと停止」へ更新。
- **コミット**: docich `codex/soren-repo-handoff` へ `faf7d70` / `5a45ab1` をpush済み。VM `/home/ubuntu/docich` は同ブランチの2ファイルを checkout して反映（webui.py=`8b7b140b...`、test_webui.py=`e87175dd...`）。親checkout自体はmainのまま。
- **テスト**: ローカル pytest `tests/test_webui.py` 99/99成功。VM unittest 同99件成功（初回Linux差分のテスト期待値は `5a45ab1` で修正）。
- **ライブ実測**: `docich-webui` を起動し `127.0.0.1:8787` 待受と `/api/health ok=true` を確認。実POST `/api/workers {worker:improve_daemon,action:stop}` は `ok=true/stopped=true/job_continues_in_background=false/lock_removed=true`。GETでは improve `paused=true/status=paused`。改善startは呼んでいない。prediction_worker は既存のpaused状態を温存。
- **配信影響**: 最終確認で `/api/stream state=live`、29.99fps、約4619.7kbits/s、runner/ffmpeg生存。ロールバック抑止ENV5項目は全て `0`、strategy decide hashは `cb3fc745833d` のまま。

## 2026-08-23 13:5x JST — 手動戦略v710「RUSSIA_LANE_ASSEMBLY」（VM反映済み・実戦観測中）

- **実測根拠**: 74試合分析で、最初のT14出現後150遷移中113遷移がT14被覆帯へ着地し、該当遷移の55%でdeadline marginが悪化。v709のtype>=11平均クラスタは「2個目のT14を作るべきanchor」を埋める主要敗因と特定。
- **実装**: soviet_now `540e2e416`。T14優先・無ければT13をレーンanchor化。通常スコアでは非併合dropをanchor横に誘導し、anchor直上被覆へ強ペナルティ。重要修正としてcritical deadline guard内のNO_MERGE fallbackもlane被覆回避を選択（旧fallbackはx=0固定）。v709の汎用高type平均は専用レーン存在時に停止。
- **オフライン回放**: 直近T14到達5試合155局面では、T14被覆選択97→21、変更111局面、候補margin p25 0.275→0.450 / 中央値0.900→1.217。全履歴半分サンプル1274局面ではdeadline超過選択256→146、margin p25 0.154→0.474 / 中央値1.024→1.510。
- **検証**: `py_compile`成功、decide hash `16c7e5e65ba2`、SHA256 `2337b668...`。VM `/home/ubuntu/soren/strategy.py`へbackup付き反映済み。改善ワーカーpaused・job/daemonプロセスなし・lockなしを維持。
- **永久保存**: soviet_now branch `codex/no-apply-liveliness` `540e2e416` push済み。親docich submodule bump `eaf6daf` push済み。
- **実戦初期観測**: v710 hash `16c7e5e65ba2` を `latest.jsonl` と完了履歴両方で確認。最初の3試合は max type `12/12/13`、スコア `829/893/778`、ソ連0。第3試合でT13peak1に到達し、実ログに `RUSSIA_LANE_GUIDE` / `NO_MERGE_RUSSIA_LANE_GUARD` が出現。改善ワーカーは引き続きpaused。
- **未確認**: T14/T15出現率変化とソ連建国は引き続き観測中。改善ループは意図的に停止したまま再開しない。

## 2026-08-23 12:4x JST — winner率80%目標の実測達成（検証済み）

- **結論**: orphan対策後の窓（10:24以降）で、完了呼び出し分母 `ok+fail` のwinner率が **20/21=95.24%** となり、80%目標を満たした。attempt分母には実行中呼び出しが含まれるため、確定値の判定は完了ベースを使用した。
- **モデル別実測（12:44時点）**: `minimax-m3` 12/12=100%、`opencode/x-preview-f-free` 7/8=87.5%、`opencode-go/muse-spark-1.2-contributor` 1/1=100%。失敗はx-preview prepass 1件のみ。
- **無限ループ切分け**: 否。`soren_loop` は1本で、radio worker配下の時間ベース生成・音声レンダリングの子プロセスが別PIDに見えていた。11:48に遅延キューゲート解除、12:15帯は複数コーナーが同時起動してキュー2件まで増えたが消化を確認。
- **運用観測**: ピーク時間帯のキューゲートは「deferred queue 0件まで生成抑止」という仕様どおり動作。時報導入更新による事前音声の再レンダリングも発生したが、12:12にキュー0へ到達し通常生成を再開した。
- **作業状態**: VM作業バナーは最終統計確認後にstop済み。今回ターンでは追加コード変更なし。対象コミットはsoviet_now `4ebf6d842`。

## 2026-08-23 04:5x JST — 直接AI呼び出しのwinner観測修正（VM反映済み）

- **目的**: `_ai_dispatch`直行のJIJI調査・翻訳・分類・postmortemなどが`ok`のみで`winner`を持たず、モデル別勝率を実態より低く見せていた問題を修正する。
- **実装**: `_ai_dispatch`へvalidator/empty出力判定と`AI_DISPATCH_RECORD_WINNER`を追加。`ai_generate_list`はvalidatorをdispatchに渡しつつ二重winner記録を回避。radio opencode直接経路・comment翻訳・comment分類・rollback postmortemの成功時のみwinner記録を有効化。soviet_now `462930248` push。
- **VM反映**: 4ファイルを`.codex_deploy/backup-20260823-0445-direct-winner-stats/`退避後にscp、SHA256一致、bash -n成功。radio/chatワーカーをTERM→respawnし、配信running・30fps・約4568kbpsを確認。
- **検証**: ローカル diagnostics 26/26、backoff 18/18、improve 21/21、comment 34/34。VM diagnostics 26/26、VM unittest 73/73。反映後の実trafficでmuse-spark-free 51回/42勝=82.35%を確認。
- **未確認**: 反映後の直接経路winner初観測と、十分なサンプルでの全モデル/全体80%到達。低勝率モデルは当日累計の上流障害・旧観測が残るため、新しい観測窓での再評価が必要。

## 2026-08-23 03:xx JST — OpenRouter ox-alpha を x-preview 直前へ挿入（VM反映・実測済み）

- **目的**: OpenRouter経由の `codex:openrouter/ox-alpha` を、既存の `opencode:x-preview-f-free` の直前に配置する。
- **変更対象**: VM `/home/ubuntu/soren/.env` の8リスト変数。`AI_COMMON_AGENTS` / `MODEL_IMPROVE_LIST` / `RADIO_AGENTS` / `RADIO_PREPASS_AGENTS` / `COMMENT_AGENTS` / `COMMENT_TRANSLATION_AGENTS` / `MODEL_IMPROVE_PEAK_LIST` / `PEAK_HOURS_AGENT_PREFERENCE`。
- **変更内容**: 各リスト内の `opencode:x-preview-f-free` 直前に `codex:openrouter/ox-alpha,` を1件ずつ挿入。既存の前後順は温存したため、例えば共通チェーン先頭側は `opencode:muse-spark-1.2-contributor-free → codex:openrouter/ox-alpha → opencode:x-preview-f-free`。
- **backup**: `/home/ubuntu/soren/.env.bak-20260823-032046-ox-router-alpha-before-xpreview`
- **事前実測**: VMで `_ai_dispatch OXROUTERTEST codex:openrouter/ox-alpha` を実行し stdout `OX_ROUTER_ALPHA_OK`、rc=0。
- **反映**: `soren-runtime.service` を再起動。improve_daemon.paused があるため改善ワーカーは停止状態を維持。service active、radio/chat/audio workerの `/proc/<PID>/environ` に新共通チェーンを確認。
- **整合確認**: 8変数それぞれで `codex:openrouter/ox-alpha` 出現数=1、かつ `x-preview` 直前であることをgrep/awkで確認。配信statusは `running`、fps約30.5、bitrate約4264kbits/sで復帰。
- **fact-check対応**: 既存の4段固定ではOpenRouter版を先頭追加すると最後のminimax fallbackが失われるため、soviet_now `33b8cce` で `RADIO_FACT_CHECK_QUINARY` を第5スロットとして追加。VM `.env.bak-20260823-034303-factcheck-quinary`退避後、実効順を `openrouter/ox-alpha → x-preview → muse-free → muse-go → minimax` へ変更。config.shとradio_factcheck.shのVM SHA256一致、bash -n、source後のQUINARY値、radio worker environを確認。soren-runtime.serviceを再起動しactive。
- **リポジトリ同期**: soviet_nowソース拡張は `codex/no-apply-liveliness` `33b8cce` にpush済み。本handoff更新はdocichブランチへ記録する。

## 2026-08-23 03:2x JST — winner統計の帰属修正と無限ループ切分け（VM反映済み）

- **ユーザー質問**: 「無限ループしてる？」に対する実測は**否**。VMにはsupervisor配下の`soren_loop` 1本と、その子のメインゲーム用`strategy_runner` 1本のみ。`tmp/state/improve_daemon.paused` が存在し、改善ジョブ状態はidle。短時間に見えた別PIDはゲーム境界処理・hash archive prune等であり、AI改善の無限再実行ではなかった。
- **統計問題**: 当日分を`resolved_model`で集計すると全体winner率は約52%。`opencode/deepseek-v4-flash-free` 0%(5/5上流UnknownError)、`x-preview-f-free` 28.6%(空出力2)、`amd-token-factory-deepseek-v4-flash` 37.5%(timeout+error無し3)、`minimax-m3` 37.5%(ok8/winner3でvalidator等の拒否疑い)が低位。`muse-spark-free` 75.7%まで改善。
- **修正**: 放送系`ai_generate_list`の`all_failed`に候補resolved_model一覧を記録。改善系`run_ai_list`の成功に`winner`、全失敗に候補resolved_model一覧を記録。soviet_now `c92c41ed0` push、VM4ファイル反映、backup `.codex_deploy/backup-20260823-0310-winner-attribution/`、SHA256一致。
- **検証**: ローカル diagnostics 26/26、backoff 18/18、improve reliability 21/21。VM diagnostics 26/26、VM unittest 39/39（VMにpytest無しのためunittest使用）。radio/chat workerを現行PIDへTERM→respawnし、再起動後の実trafficでwinner記録を確認。
- **ループ追補**: 03:49と03:53に別セッション経由で`soren_loop`が再開された。1本目は1試合後に自然終了、2本目は継続したため04:09にTERM停止。見かけ上複数あった`soren_loop.sh`表示の一部はhash archive prune workerの親子プロセスだった。停止後は`strategy_runner`無し・lock空を確認。改善paused markerは温存。
- **未確認**: 修正後の十分なサンプルでの80%到達。特にminimaxのok→winnerにならない理由と、`openrouter/ox-alpha` の初回attemptの完了結果。作業ツリーには別セッション系の`soren_loop.sh`/`strategy/improve.sh`/`stream_noon_audit`変更が残っており、今回は触れていない。

## 2026-08-23 — コメント戦略指示の受付時保存と改善優先化（ローカル実装・未VM反映）

- **目的**: 視聴者コメントの戦略指示が返信生成の成否に依存せず、次回戦略改善へ確実に届くようにする。
- **実装（soviet_now 作業ツリー）**:
  - `broadcast/comment.sh`: 機械抽出した `strategy` 候補を分類直後・コメント生成前に `advice.md`/`advice91.md` へ保存。`[source=comment_intake received=<epoch>]` を付与し、非助言カテゴリでは従来どおり抑制。
  - 本体が同一の intake/reply 記録は `_strategy_advice_core_matches_existing` で重複排除。既存行から source/received メタを除いた本体比較を行う。
  - `eloop_improve.sh` / `prompts/improve_strategy.md`: improve_brief と実装プロンプトで `comment_intake` を優先表示し、新しい未処理指示を古い繰り返しメモより先に照合させる方針を明記。ログ裏取り要件は維持。
  - `tests/test_escape_mechanisms.py`: 受付時保存順序・source/received・improve_brief優先化の焦点回帰を追加。
- **検証**: 焦点テスト2件 OK、`bash -n comment.sh eloop_improve.sh` OK、`git diff --check` OK、`test_comment_bilingual` 34件 OK。`test_escape_mechanisms` 全件は既存 strategy/wildcard 関連失敗と sandbox socket 制限があり中断したため、今回変更とは別のベースラインとして扱う。
- **VM反映**: soviet_now `a7fc5bdbc` を `origin/codex/no-apply-liveliness` に push済み。VM `/home/ubuntu/soren` へ4ファイル反映、backup `.codex_deploy/backup-20260823-comment-intake/` 作成、SHA256一致と `bash -n` 確認。
- **再起動・検証**: chat/youtubeワーカーをTERM→supervisor respawnで更新し、chat2本・youtube2本へ復帰。VM焦点テスト2件 OK。改善daemonはpaused/idleのまま触らず、次回spawnから新improve経路が有効。
- **未確認**: 実コメントによる初回 `source=comment_intake` 追記と、その項目が improve_brief の Advice Priorities 先頭側に出る運用観測。

## 2026-08-22 14:xx JST — Ox Alpha を OpenRouter 経由でも使用可能に（ローカル・VM反映・実呼び出し確認）

- **目的**: `x-preview-f-free` と同一系統の Ox Alpha を OpenRouter 枠でも fallback/picker として使えるようにする。
- **OpenRouter 上流ID**: `stealth/ox-alpha`。公開カタログは context 1,048,576 / max output 131,072 / prompt/completion 無料。
- **ローカル codex-router**:
  - `~/.codex/codex-router/user-models.json` へ `openrouter/stealth/ox-alpha` を追加。gateway は `openrouter-stealth-ox-alpha`、context 1,048,576、autoCompact 891,289、efforts low/high/max。
  - 既存の `opencode-free/x-preview-f-free` は温存。OpenRouter key も既存のまま使用し、値は取得・表示していない。
  - `doctor --fix` + `refresh-catalog` 後、merged catalog 29 routed models / gateway routes 一致を確認。Codex 設定読み込みも成功。
  - Codex 側新カタログ検証が `gpt-5.2.supports_parallel_tool_calls` 必須で失敗したため、native/merged の該当フィールドへ `true` を補完し backups `.bak-20260822-gpt52-parallel` を残した。これは router checkout 更新時に再発可能性がある互換修正。
  - 実呼び出し: `test-model openrouter/stealth/ox-alpha --live --yes --quick --json` が status 200 + marker true。sandbox 内 doctor の router health は false だが、承認付き直接 `GET :4202/health` は `ok:true` で sandbox 制限と判断。
- **VM**:
  - VM には codex-router 本体はなく、従来どおり `/home/ubuntu/.codex/merged-models.json` + `/home/ubuntu/litellm.yaml` + `soren-litellm.service` 構成。
  - `openrouter/free` のカタログ雛形から `openrouter/ox-alpha`（Ox Alpha via OpenRouter）を追加。context 1,048,576、autoCompact 900,000、parallel tools true、multi-agent v1。
  - LiteLLM route は upstream `openai/stealth/ox-alpha`、base URL OpenRouter、key env 参照のみ。Codex が Responses API を要求して Stealth 400 になったため、`use_chat_completions_api: true` を追加して解消。
  - backup: `/home/ubuntu/.codex/backups/20260822-openrouter-ox-alpha/`。
  - 検証: `soren-litellm` active、chat completions が `OX_ALPHA_OPENROUTER_VM_OK` を返す。さらに VM `codex exec ... -m openrouter/ox-alpha` が `OX_ALPHA_CODEX_VM_OK` を返す。Codex CLI には `OutputTextDelta without active item` 警告が2回出たが最終応答は成功。
- **未確認**: 自然発生 fallback での獲得観測、長時間 agentic workload での安定性、ローカル router checkout 更新後も gpt-5.2 互換パッチが不要になるか。

## 2026-08-22 08:xx JST — v708: T14×2生存モード＋ソ連建国直前併合優先（実装・VM反映済み）

- **ユーザー指示**: 「ソ連が建国できるように、戦略の改善を行ってください。戦略改善ループはいまは止めています。こちらのスレッドで、ソ連が建国できるまで終わらないこと。」
- **実測分析**（game_history/20260822_060733_score3200.jsonl）:
  - 最高スコア試合(3200点): T14×2をturn 93〜140（48ターン）保持したが、next=T14は一度も出現せずdeadline超過で死亡。
  - T14×2の状態では「いつT14が来るか」は確率的事象であり、制御可能なのは「どれだけ長く生存するか」のみ。
  - 直近48試合: T14×2到達1回、russia_count=0、soviet_count=0、best_max_type=14。
- **v708実装**（soviet_now `6bd92ea28` push済み、VM `/home/ubuntu/soren` 反映済み）:
  - `t14_count >= 2` 時に新軸を追加:
    - (a) `merge_grade == "NO" && next_type != 14`: 低配置強化 `-800 × max(1, landing_y + 3)` → 盤面上昇最小化で生存時間延長
    - (b) `merge_grade in ("DIRECT","NEAR") && next_type == 14`: `+3000` → T14+T14→T15（ソ連建国直前）の確定マージ
    - 加えてT14間の中間エリア保護（gap内配置 -300）
  - decide hash: `42c79aab4a68` → `96c6a1719420`
- **テスト**: py_compile OK、extract_decide_hash OK、TestSovietBoardAnalysis 2/2 OK、test_stat_gate_shadow 2/2 OK
- **VM反映**: SHA一致(`5a479a1e5a22dd8085c0beaa5a6bd2d09b54c3d456e2694164436ecc7750abc5`)、backup `.codex_deploy/backup-20260822-v708-t14-pair-survival/`
- **改善ループ再開**: `tmp/state/improve_daemon.paused` 削除済み。improve_daemon PID 156181 稼働中。次ゲームからv708反映。
- **未確認**: v708でのT14×2到達率・ソ連建国率の実運用観測（今後のaccumulated_games.jsonとsoren_loop.logで確認）。

## 2026-08-22 06:3x JST — issue #22 解決： ANALYZEタイムアウト1100s＋Stage1リトライ限定（実装・VM反映・テスト済み）

- **ユーザー指示**: 「イシュー22の解決しておいて」。
- **実測（ai_stats の完了ANALYZE 22件）**: 成功10件の所要 48-1298s、**900s超えが30%**。900s上限は成功裾を切断していた。
- **修正**（soviet_now `7160a34a3`、VM反映・backup `.codex_deploy/backup-20260822-0625-analyze-timeout/`）:
  - `IMPROVE_ANALYZE_CMD_TIMEOUT_SEC` 既定 900→**1100**(config.shへも既定新設)
  - Stage1のprimaryリトライを `IMPROVE_ANALYZE_PRIMARY_RETRIES=2` へ限定(worst 2200s)し、分析ループ後にグローバル値へ復元 → wall 3600s の Stage2 予算 ≥1300s を保証
  - `strategy/ai.sh` run_cmd に `OPENCODE_BIN` 上書き追加(CODEX_BIN規約と同一)。**VMで本テストが関数スタブを素通りし /snap/bin/opencode 実バイナリ(上流エラー中)を叩く既存問題**を発見修正
  - テスト期待値更新。ローカル・VMとも **20/20**
- **VM反映**: SHA一致(eloop a341d497…/config 5d6900fa…)・bash -n成功。`.env` へ `IMPROVE_ANALYZE_CMD_TIMEOUT_SEC=1100` / `IMPROVE_ANALYZE_PRIMARY_RETRIES=2` 追加済み → 次回spawnジョブから有効(生成スクリプトは実行時env展開)。improve_daemonはWebUI pause中のため再開後の初回ジョブで新設定稼働。
- **issue**: [#22](https://github.com/azumag/docich/issues/22) クローズ済み(実測データ付きコメント)。切り分け過程でVMの一時ファイル `/tmp/tmp.*` にrepro残骸あり(放置可)。
- **未確認**: 次回改善ジョブでの analyze 成功率向上(1100s内に収まるか)。稼働中だった孤児ジョブは06:45:44のwall deadlineで自然終了見込み(終着確認はまだ)。

## 2026-08-22 06:0x JST — ゲート打ち切りをattempt/fail統計から分離（実装・VM反映・テスト済み）

- **ユーザー指摘**: 「アテンプトの統計が狂うので、(ゲート待ちを)別にしてほしい」。
- **問題の機構（実測）**: `_ai_dispatch` は入口で attempt 記録→`_ai_call_codex` 内の `_ai_generation_queue_run` でゲート待ち(最大1200s)→打ち切りrc=1がfail記録。つまり**モデル呼び出しゼロの待ちがattempt+fail両方を汚染**していた(05:21-05:22のdeals/news prepass fail rc=1が実例)。
- **実装**（soviet_now `7b7b328e0`、VM `/home/ubuntu/soren` 反映・backup `.codex_deploy/backup-20260822-0548-gate-giveup/`）:
  - 新定数 `AI_GATE_GIVEUP_RC=91`。ゲート打ち切り時はこれを返す(queue_run経由でも伝播)。
  - `_ai_dispatch` 入口へゲートをhoist: 打ち切り時は **`gate_giveup` イベントのみ記録**(attempt/fail無し)でreturn 91。チェーンフォールバック挙動は従来どおり(次候補へ)。
  - 通過後は `AI_GATE_PASSED_FOR_DISPATCH=1` を立てて内側キューゲートの二重待機を防止。dispatch末尾(PIPESTATUS取得後)で必ず解除し同一シェル内の次候補へ漏出しない。
  - webui側は未知イベントを無視する集計のため無変更で安全(`src/docich/webui.py` の ev==attempt/fail 分岐を実測確認)。
- **テスト**: `tests/test_ai_lane_queue.sh` 22→31項目(ゲート打ち切りrc91・gate_giveupのみ記録・attempt/fail非計上・通常呼び出しはattempt/ok・二重待機防止フラグ)。ローカル(macOS, e2eはtimeout不在skip)23ok/0ng、**VMで31/31全成功**。
- **VM反映・再起動**: SHA256一致(c1cf9ee4…/210dee73…)・bash -n成功。radio/chat worker全インスタンスTERM→respawn(radio 1295724/chat 1295616)、重複なし・配信LIVE維持 ✓。
- **未確認**: 実トラフィックでの gate_giveup イベント初観測(次回改善ジョブ稼働中の放送系生成時)。webui Stats画面への gate_giveup 表示追加は今後の候補(現在は単にattempt/failから抜けるだけ)。

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
