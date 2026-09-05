# 日常運用

docich を導入済みの前提で、日々の操作をまとめる。導入がまだなら
[[クイックスタート|Quickstart]] を先に。コマンド全体の一覧は `docs/architecture.md` §7 または
`bin/docich --help` で確認できる。

## ゲーム切替 (`docich switch`)

`docich switch <game>` は canonical 状態 (`run/game_switch.json`) を正本とするトランザクション
として動く。旧ゲームの停止に失敗したら自動で旧ゲームを復元し (`rolled_back`)、切替中の事故は
`docich recover` で収束できる。`display` / `audio` / `stream` の window はそのまま残るため、
**配信ストリーム (ffmpeg プロセス) は切替中も途切れない**。機構の詳細は
[[ゲーム切替機構|Game-Switch]] を参照。

```bash
bin/docich switch hanjuku-hero
```

## 状態確認: status / snap / obs / send の使い分け

| コマンド | 用途 | 例 |
|---|---|---|
| `status` | 各 window の生死・現在のゲーム・配信設定をまとめて見る | `bin/docich status` |
| `snap` | 今の画面を PNG で保存する (目視確認用) | `bin/docich snap -o /tmp/now.png` |
| `obs` | brain に渡る観測 JSON をそのまま出力する (brain 開発・デバッグ用) | `bin/docich obs nethack` |
| `send` | 行動 JSON を単発でゲームに注入する (brain を介さない動作確認用) | `bin/docich send nethack '{"type":"text","text":"h"}'` |

`obs` は `cli` アダプタなら `kind:"text"` (端末画面のテキストダンプ)、`retroarch` /
`browser` アダプタなら `kind:"screenshot"` (PNG パス) を返す。迷ったら「`status` で生きて
いるか確認 → `obs` で見えている内容を確認 → `send` で反応を試す」の順で使うとよい。

## 字幕 (caption)

ネイティブ Twitch 字幕は既定で無効の opt-in 機能である。有効化には `docichcc` filter 入りの
custom FFmpeg と環境変数が要る:

```bash
export DOCICH_FFMPEG_BIN=/path/to/docichcc-ffmpeg/bin/ffmpeg
export DOCICH_CC_ENABLED=1
export DOCICH_CC_SOCKET="$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock"  # 省略可 (既定パスを使う)
```

字幕計画の作成と FFmpeg への送信は CLI から行う:

```bash
bin/docich caption plan --chunks-file <jp.txt> --translations-file <en.json> \
  --execution-id <id> --output <plan.json>
bin/docich caption send prepare --plan <plan.json> --chunk 0 --page 0
bin/docich caption send commit  --plan <plan.json> --chunk 0 --page 0
bin/docich caption send clear   --plan <plan.json>
```

`bin/docich status` は `captions.requested` (opt-in で要求されたか) と `captions.active`
(custom FFmpeg・`docichcc`・`libx264 a53cc` の能力が揃って実際に有効化されたか) を分けて表示し、
食い違う場合は `captions.detail` に fail-open の理由が出る (`doctor` でも同じ能力チェックを
事前確認できる)。

字幕は補助経路であり、能力不足・翻訳失敗・socket 失敗のいずれでも通常の映像・音声コマンドへ
fail-open する (配信・音声は止まらない)。詳細: `docs/twitch_closed_captions.md`。

## RetroArch のステート保存 (`ra-cmd`)

半熟英雄 (`retroarch` アダプタ) 実行中は、RetroArch の Network Command インターフェース
(UDP, ポート 55355) 経由でステート操作ができる:

```bash
bin/docich ra-cmd SAVE_STATE      # 現在の状態を保存
bin/docich ra-cmd LOAD_STATE      # 直前のセーブステートを復元
bin/docich ra-cmd PAUSE_TOGGLE    # 一時停止の切替
```

配信中に落ちた際の復帰や、brain 開発時の巻き戻しデバッグに使う (詳細:
[[半熟英雄 (SFC)|Game-Hanjuku-Hero]])。

## ログと調査

- **`run/logs/<component>.log`**: `display` / `audio` / `stream` / `game` / `agent` それぞれの
  監督ループ (`docich run <component>`) が書くログ。起動コマンド・終了コード・稼働時間・
  再起動間隔がタイムスタンプ付きで残る。
- **`tmux attach -t docich`**: docich 本体の tmux セッションに入り、各 window の生ログを直接
  見る。window 一覧は `display` / `audio` / `stream` / `game` / `agent` (`stream` は
  `stream.mode != "null"` のときのみ、`agent` は `[agent] enabled = true` のゲームのときのみ
  存在する)。tmux の既定キーバインドなら `Ctrl-b w` で window 一覧、`Ctrl-b d` で detach。
- **`docich-game` セッション**: `cli` アダプタ (NetHack 等) のゲーム本体はこの独立した tmux
  セッションで動いている。`docich` セッションの `game` window は、これを読み取り専用
  (`tmux attach -r`) で映しているだけの xterm である。誤って書き込み attach
  (`tmux attach -t docich-game`、`-r` 無し) すると打鍵がそのままゲームに届いてしまうため、
  内容だけ確認したい場合は `tmux attach -r -t docich-game` を使う (`bin/docich obs` は内部で
  `capture-pane` を使うため、この心配なく安全に確認できる)。

## 停止と再起動: stop / down / up の違い

- **`docich stop`**: 現在のゲーム (`game`/`agent` window) だけを止める。`display` / `audio` /
  `stream` は残るため配信は継続する。
- **`docich down`**: `stop` 相当の処理に加え、tmux セッション `docich` ごと全 window を落とす。
  基盤ごと停止する。
- **`docich up`**: `display` / `audio` / `stream` の window を (無ければ) 作る。既に起動して
  いれば何もしない (冪等)。

いずれの window も中身は `docich run <component>` (監督ループ) であり、子プロセスが異常終了
すると指数バックオフ (1s → 2s → 4s → … 上限 30s) で自動再起動する。ただし直前のプロセスが
60 秒以上生きていた場合はバックオフを 1s にリセットする (瞬間クラッシュの連続と、たまたま
長時間稼働後に落ちたケースを区別する設計)。

**配信 (FFmpeg → Twitch) だけを明示終了したい場合は、`down` による強制終了ではなく、
FFmpeg へ `q` を送って正常終了させる方法がある** (RTMP の `FCUnpublish` / `deleteStream`
が実行され、Twitch が即 OFF LINE になる)。詳細は [[配信の明示終了|Stream-Ending]] を参照。

**Soren 本番 (soviet_now の `start_all.sh --supervisor` 管理下) では、`direct_stream`
worker が自動再起動するため、`q` 送信の前に `tmp/state/direct_stream.paused` を作成して
再起動を抑止する** (下記マーカー。抑止しないと FFmpeg 停止直後に supervisor が配信を
再起動する)。再開時は同マーカーを削除する。正確な手順は wiki の
「配信の明示終了」→「Soren 本番 (supervisor 管理下) での注意」を参照。

## 時間割ローテーション (`docich rotate`)

`config/docich.toml` の `[rotation] games = ["nethack", "hanjuku-hero"]` のように巡回順を
定義しておくと、`docich rotate` が「現在のゲームの次」へ `switch` する (`--dry-run` で
切替先の確認のみ)。定期実行は cron や `scripts/systemd/docich-rotate.timer` から叩く想定で、
docich 自身は常駐スケジューラを持たない。

## watchdog (フリーズ検知と window 復旧)

`[watchdog] enabled = true` にすると `docich up` が watchdog window も起動する。

- **フリーズ検知**: game window・agent window の両方が稼働中のときだけ、`interval_s` (既定60秒)
  ごとのスクリーンショット digest を比較し、`freeze_cycles` (既定5) 回連続で同一なら
  `docich switch <現在のゲーム>` で復旧する (配信は維持)。agent が動いていない静的画面を
  誤検知しないための条件になっている。長い静止画面が正常なゲームでは `freeze_cycles` を
  大きくする。
- **window 復旧**: `display` / `audio` / `stream` の window が消えていたら冪等な `up` で再生成する。

## systemd ユニット (任意)

`scripts/systemd/` に `--user` ユニットの雛形がある (`docich.service` = up/down、
`docich-rotate.timer` = 毎時 rotate)。`__DOCICH_ROOT__` を置換して導入する手順と、
soren-runtime.service とは完全独立である旨の警告は `scripts/systemd/README.md` を参照。
既定では何も enable しない。

## スモークテスト

```bash
scripts/smoke_cli.sh    # 基盤: 起動・観測・入力注入・配信・切替の一括検証
scripts/smoke_brain.sh  # 半熟英雄 brain: fake LLM で観測→行動→RetroArch メニュー入力到達まで
```

いずれも Xvfb + xdotool を使い、display 番号は `:96` のため本番設定 (`:98`) や soren (`:99`)
とは衝突しない。

**警告**: これらのスクリプトは実行中の `docich` tmux セッションを `down` する
(開始時・終了時に `docich down` を呼ぶ後始末が入っている)。**本番稼働中 (実配信中) の環境では
実行しないこと。**
