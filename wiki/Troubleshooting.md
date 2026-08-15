# トラブルシューティング

症状から逆引きする。裏付けの一次情報は `docs/architecture.md` §9 と `docs/games/*.md`
(該当箇所は各項目末尾にリンク)。

## RetroArch が起動直後に落ちる (Aborted)

- **症状**: `bin/docich start hanjuku-hero` の直後、RetroArch が abort して落ちる。
- **原因**: セッション D-Bus が無い環境では、RetroArch 1.18 の GameMode 統合の dbus 呼び出し
  が assert → abort する (`gamemode_enable=false` にしても回避不可。ヘッドレス環境で実証済み)。
- **対処**: docich は `dbus-run-session -- retroarch ...` で包んで起動する実装になっている
  (実装済み)。発生する場合は `dbus` パッケージ (`dbus-daemon`) の導入を確認する。
  詳細: `docs/architecture.md` §4.1・§9-1b、[[半熟英雄 (SFC)|Game-Hanjuku-Hero]]。

## SFC に入力が届かない

- **症状**: `send`/agent からの `pad`/`key` アクションがゲームに反映されない。
- **原因**: 既定の `udev` 入力ドライバは Xvfb + xdotool からの XTEST を受け付けない。瞬間的な
  press/release も RetroArch のフレーム単位ポーリングで取りこぼされる。
- **対処**: 生成される `run/retroarch/retroarch.cfg` の `input_driver = "sdl2"` (既定で設定
  済み)、`hold_ms` を既定の 100ms 未満に下げないこと、`windowfocus --sync` によるフォーカス
  取得 (実装済み) の 3 点を確認する。詳細: `docs/architecture.md` §9-1・§9-2、
  [[半熟英雄 (SFC)|Game-Hanjuku-Hero]]。

## 映像が真っ黒

- **症状**: `snap`/`obs` のスクリーンショットや配信映像が真っ黒になる。
- **原因**: Xvfb には GLX が無い/不安定なことがあり、`video_driver = "gl"` だと失敗し得る。
- **対処**: 生成される cfg の `video_driver = "sdl2"` (既定で設定済み。SNES コアはソフト
  レンダなので十分な性能が出る想定) を確認する。それでも出ない場合は mesa (llvmpipe) 経由の
  `"gl"` が代替候補になる 【要検証】。詳細: `docs/architecture.md` §9-3。

## 配信にマウスカーソルや緑のバーが映る

- **症状**: 配信画面の中央にマウスポインタが映る、または画面下部に tmux の緑色ステータス
  バーが映り込む。
- **原因**: ffmpeg の x11grab は既定でマウスカーソルを描画する。`cli` アダプタの
  `docich-game` セッションは tmux の既定でステータスバーを表示する。
- **対処**: **docich は両方とも対応済み**。ffmpeg 呼び出し (配信・スクリーンショットとも)
  には常に `-draw_mouse 0` を付与し、`docich-game` セッション作成時に `status off` を設定
  している。この症状が出る場合は導入している docich のバージョン・設定を確認すること。
  詳細: `docs/architecture.md` §4.2・§5。

## スクリーンショットが失敗する

- **症状**: `bin/docich snap`/`obs` が失敗する、またはエラーになる。
- **原因**: ディスプレイ (Xvfb) が起動していない、または `docich up` が未実行。
- **対処**: `bin/docich up` を実行してから再試行する。`bin/docich status` の
  `display_ready` が「いいえ」の場合は、`DISPLAY=:98 xdpyinfo` 等でディスプレイ自体の生死を
  個別に確認する。

## `docich stop`/`down` が遅い・プロセスが残る

- **症状**: 停止コマンドの応答が遅い、または `down` 後に Xvfb/ffmpeg 等のプロセスが孤児化
  して残り続ける。
- **原因 (Phase 1 の開発中に発見・修正済み)**: tmux が pane を kill すると、そこに繋がる
  stdout への書き込みが `OSError [Errno 5] Input/output error` になることがある。これが
  停止シグナルのハンドラ内でログ書き込みより先に子プロセスの terminate/kill を行わない
  実装だと、例外が漏れた場合に後始末が実行されないまま孤児化する (`scripts/smoke_cli.sh`
  の実行で発見)。また子の生死確認に `Popen.wait()`/`poll()` を使うと、シグナルハンドラから
  の再入で内部ロックが取得できず、子が既に死んでいても毎回 15 秒 (待機上限+予備) 待って
  しまう問題もあった。
- **対処**: 現行実装 (`src/docich/supervise.py`) は「子プロセスの terminate/kill を最優先し、
  ログ書き込みは例外を握りつぶして何が起きても後始末を止めない」「生死確認は
  `os.waitpid(WNOHANG)` を直接ポーリングする」という構造になっており、この問題は解消
  済みである。再発する場合は回帰の可能性があるため報告すること。

## 字幕が表示されない

- **症状**: Twitch 配信に英語字幕 (CC) が出ない。
- **原因**: docich 側では (1) `DOCICH_CC_ENABLED` 等の opt-in 環境変数が未設定、または
  (2) custom FFmpeg・`docichcc` filter・`libx264 a53cc` option のいずれかが揃っておらず
  fail-open し、字幕なしの通常コマンドで配信している場合がある。加えて Twitch 側で
  broadcaster/視聴者が字幕を有効化していないと、docich が正しく送出していても表示されない。
- **対処**: `bin/docich status` の `captions.requested` / `captions.active` /
  `captions.detail` で opt-in と実際の有効化状況・fail-open 理由を確認する
  (`bin/docich doctor` でも同じ能力チェックを事前確認できる)。本番実績では、Twitch 側で
  broadcaster が字幕設定を明示的に有効化し配信を再起動して初めて表示された
  (`handoff.md` の Live evidence)。視聴者側は player メニューでの有効化が必要な場合もある。
  詳細: `docs/twitch_closed_captions.md`、[[日常運用|Operations]] の「字幕 (caption)」。
