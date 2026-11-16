# 半熟英雄ローカル ドライバ / ビューア運用メモ

半熟英雄のローカル攻略を回すための**ヘッドレスドライバ**と**読み取り専用ビューア**の扱いをまとめる。
本番VMのアダプタ（Soren）とは別に、ローカルでは snes9x libretro コアを直接叩いて同じ bot
（`brains/hanjuku/bot.py` / `src/docich/hanjuku/`）を回す。

- ドライバ: `tmp/hanjuku-local/core_driver.py`（リポジトリ外・未追跡）
- ビューア: `tmp/hanjuku-local/viewer.py`（リポジトリ外・未追跡）
- コア: `tmp/hanjuku-local/cores_161/snes9x_libretro.dylib`（ARM64 版は `cores_arm64/`）
- ROM: `games/roms/hanjuku-hero.sfc`
- 実行worktree: `.worktrees/hanjuku-local-main`（`HANJUKU_CODE_ROOT` で指定）

## 1. 全体像

```
ROM + snes9x core
      │ retro_run() を回す（--fast で実時間より速く）
      ▼
core_driver.py ── フレームを PNG 保存 ──► <runtime>/screenshots/latest.png ──► viewer.py :8765
      │                                                                        （読み取り専用）
      │ hanjuku_run.observe()（本番と同じ観測）
      ▼
bot（HANJUKU_INPROC=1 なら同一プロセス、無ければ brains/hanjuku/bot.py を子プロセスで呼ぶ）
      │ 観測JSON → decide() → pad アクション
      ▼
shared button bitmask → retro input_state()
      │
      └─ 一定間隔で状態を serialize（.state）＋バッテリーSRAMを保存（.srm）
```

ポイント:

- 1 観測 = 「core を進める → PNG 化 → `hanjuku_run.observe` → bot に入力 JSON を渡す → 返ってきた
  パッド入力をコアへ送る」。アクションが空なら「hold」としてログに残る。
- 観測ログ（`[frame] phase=... act=...`）と bot の判断ログ（`hanjuku_decisions.jsonl`）が運用の一次情報。
- ドライバは**本番アダプタの代替**であり、メモリ読み取りはしない（bot からメモリを読むのは禁止。開発時の
  改善ネタとして外部からメモリを覗くのは可）。

## 2. ドライバの起動

現在のセッションで使っている起動コマンド（worktree を `HANJUKU_CODE_ROOT` にするのが要点）:

```bash
cd /Users/azumag/work/docich
HANJUKU_LEASE=<lease-id> \
HANJUKU_RUNTIME=/Users/azumag/work/docich/.worktrees/hanjuku-local-main/run-local/runtimes/g1190-local \
HANJUKU_RUNTIME_ID=g1190-67435f4d \
HANJUKU_CODE_ROOT=/Users/azumag/work/docich/.worktrees/hanjuku-local-main \
HANJUKU_GENERATION=1190 \
HANJUKU_SRAM_OUT=/Users/azumag/work/docich/tmp/hanjuku-local/saves/prod1190.srm \
HANJUKU_INPROC=1 \
HANJUKU_CORE=/Users/azumag/work/docich/tmp/hanjuku-local/cores_161/snes9x_libretro.dylib \
HANJUKU_ROM=/Users/azumag/work/docich/games/roms/hanjuku-hero.sfc \
HANJUKU_SAVE_EVERY=10 \
HANJUKU_STATE=/Users/azumag/work/docich/tmp/hanjuku-local/states/prod1190.state \
nohup python3 tmp/hanjuku-local/core_driver.py --fast >> tmp/hanjuku-local/prod1190.log 2>&1 &
```

### 引数

| 引数 | 意味 |
|---|---|
| `--fast` | 実時間ペースをやめて可能な限り速く回す（通常はこれ）。付けないと 60fps 実時間。 |
| `--noload` | 起動時に `.state` をロードしない（ROM から新規ブート）。 |
| `--fresh` | 決定論的な新規ブート（アトラクトを送ってメニュー→A）。検証用。 |

### 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `HANJUKU_CODE_ROOT` | `/Users/azumag/work/docich` | bot ソースのルート。**worktree を使うときは必ずここを切り替える**。 |
| `HANJUKU_RUNTIME` | `run-local/runtimes/<RUNTIME_ID>` | 観測・判断ログ・スクショの出力先。 |
| `HANJUKU_RUNTIME_ID` | `g1163-42bfb85e` | 実行ID。`hanjuku_run` の identity に載る。 |
| `HANJUKU_GENERATION` | `1163` | 世代番号。 |
| `HANJUKU_LEASE` | サンプル値 | リースID（実行の同一性）。 |
| `HANJUKU_STATE` | `tmp/hanjuku-local/states/hanjuku-hero.state` | セーブステート（`retro_serialize`）。 |
| `HANJUKU_SAVE_EVERY` | `10` | 90 フレーム×この値ごとに state を保存（既定で約 900 フレーム毎）。 |
| `HANJUKU_SRAM_OUT` | 空 | バッテリーSRAMの保存先（`.srm`）。毎判断間隔で書き出す。 |
| `HANJUKU_SRAM_IN` | 空 | `HANJUKU_LOAD_FLOW=1` のときだけ、起動時に SRAM を注入する。 |
| `HANJUKU_INPROC` | 無効 | `1` で bot を同一プロセスで回す（子プロセス起動コスト無し）。 |
| `HANJUKU_LOAD_FLOW` | 無効 | `1` で「タイトル→CONTINUE/ロード」の入力列（`title_load_flow`）を起動時に送る。**タイトル復帰時の復帰経路**。 |
| `HANJUKU_CORE` / `HANJUKU_ROM` | cores_arm64 / games/roms | コアと ROM のパス。 |
| `HANJUKU_FORCE_565` | 無効 | XRGB8888 を出してくるコアでも RGB565 扱いにする（検証用）。 |
| `HANJUKU_STOP_AT_NAME` | 無効 | 名前入力画面で停止して state を保存する（検証用）。 |

### 保存と世代バックアップ（重要）

- 停止要求（SIGTERM / SIGINT）時:
  1. **既存 `.state` を `.state.prev` へコピー**（1世代前を残す）
  2. 現在の状態を `.state` へ serialize
  3. SRAM を保存
- これは 2026-11-16 の事故（state をロードしたらタイトルで、Start を押して第9話の進行が消えた）を受け、
  「タイトルに落ちた state でも 1 世代戻せる」ようにするためのバックアップ。
- したがって**停止は必ず SIGTERM**（`pkill -TERM -f 'core_driver.py --fast'`）。`kill -9` は state が
  保存されないので避ける。
- 定期的にも `HANJUKU_SAVE_EVERY` の間隔で `.state` を上書きする（`.prev` は停止時のみ更新）。

### 終了条件

- **SIGTERM**: 上記の保存をして終了（通常の停止）。
- **terminal 検出**: `hanjuku_run` がタイトル復帰などの terminal を確定すると、ドライバは
  `[drv] terminal: ...` を出して state を保存し終了する。**terminal の間は bot の decide() を呼ばない**
  ため、タイトルで「つづき」を押す前に終了し得る（既知の運用注意。§6）。

## 3. ビューアの起動

```bash
cd /Users/azumag/work/docich
HANJUKU_VIEW_RUNTIME=/Users/azumag/work/docich/.worktrees/hanjuku-local-main/run-local/runtimes/g1190-local \
HANJUKU_VIEW_LOG=/Users/azumag/work/docich/tmp/hanjuku-local/prod1190.log \
HANJUKU_VIEW_PORT=8765 \
nohup python3 tmp/hanjuku-local/viewer.py >> tmp/hanjuku-local/viewer.log 2>&1 &
```

| 変数 | 既定 | 用途 |
|---|---|---|
| `HANJUKU_VIEW_RUNTIME` | `.../runtimes/g2001-ch9-local` | 表示する runtime。**worktree を切り替えたら追随させる**。 |
| `HANJUKU_VIEW_LOG` | `tmp/hanjuku-local/ch9.log` | `/status` に出すドライバログ。 |
| `HANJUKU_VIEW_PORT` | `8765` | 待受ポート（127.0.0.1 のみ）。 |

### エンドポイント

| パス | 内容 |
|---|---|
| `/` | 256×224 の画面（3倍表示）＋ステータスを 1 秒毎に更新する HTML。 |
| `/frame.png` | `<runtime>/screenshots/latest.png` をそのまま返す（400ms 毎に再取得）。 |
| `/status` | bot 版・step・phase・screen、chapter/month/captured 系、最後の判断と理由、ドライバログ末尾 160 文字。 |

- **読み取り専用**でコストはほぼゼロ。ゲーム側の入力・状態には一切触らない。
- 見る URL: ローカル `http://127.0.0.1:8765/`、tailscale 経由 `http://azmacminim4.tail85a080.ts.net:8765/`。
- 表示はドライバが止まると更新が止まる（画面は最後のフレームのまま）。`/status` の `log:` が
  `driver stopped` 相当になっていないかで生存確認できる。

## 4. ランタイムディレクトリの中身

`<runtime>/`（例: `run-local/runtimes/g1190-local`）:

| ファイル | 内容 |
|---|---|
| `screenshots/latest.png` | 直近フレーム（ビューアの /frame.png の実体）。 |
| `hanjuku_bot.json` | bot の最新観測/判断（phase, policy, step, screen_kind, terminal 等）。 |
| `hanjuku_decisions.jsonl` | 判断ログ（decision, reason, observed_metric）。ローテーションあり。 |
| `hanjuku_decisions.previous.jsonl` | 直前世代の判断ログ。 |
| `hanjuku_events.jsonl` | イベントログ。 |
| `hanjuku_progress.json` / `hanjuku_run.json` | 進捗・実行メタ。 |
| `hanjuku_frames/` | 保存フレーム（任意）。 |

## 5. 起動・停止・再起動の手順

### 状態確認

```bash
curl -sS -m 5 http://127.0.0.1:8765/status
ps -eo pid,etime,command | grep -E 'core_driver|viewer.py' | grep -v grep
tail -3 tmp/hanjuku-local/prod1190.log
```

### 停止（state を保存する）

```bash
pkill -TERM -f 'core_driver.py --fast'
# 消えるまで待つ（最大30秒）。残っていたら -KILL は最後の手段（state 未保存に注意）
for i in $(seq 1 30); do pgrep -f 'core_driver.py --fast' >/dev/null || break; sleep 1; done
```

### 再起動（v9xx へ更新したとき）

1. `pkill -TERM` で停止し、`.state` / `.srm` が更新されたことを確認（mtime）。
2. 新しいコードで worktree を更新（`git -C .worktrees/hanjuku-local-main ...`）。
3. §2 のコマンドで起動（`HANJUKU_STATE` を同じファイルにすると続きから）。
4. `/status` で bot 版が新しいことを確認（`bot=hanjuku-chart-v9xx-...`）。

### タイトルに落ちたときの復帰（実測用手順）

state がタイトル画面で保存されている場合、そのままロードするとタイトルのまま terminal 終了し得る。
その場合は**バッテリーSRAM から復帰**する:

```bash
HANJUKU_LOAD_FLOW=1 \
HANJUKU_SRAM_IN=/Users/azumag/work/docich/tmp/hanjuku-local/saves/prod1190.srm \
# ほかは §2 と同じ
```

- `title_load_flow` が「タイトル→START→右→A→A→A」を送って CONTINUE/ロードを試みる。
- それでも戻らない場合は `HANJUKU_LOAD_FLOW=1` を外し、`--noload` でブートして bot の
  タイトル分岐（`phase=='title'` → down+wait+A を3回まで = つづき、だめなら START）に任せる。
- **`.state.prev` に 1 世代前**があるので、そちらを `HANJUKU_STATE` に差し替えてロードする手もある。
- 絶対にやってはいけない: タイトルで blind に START を連打して「はじめから」を選び、進行を消すこと
  （2026-11-16 の事故）。

## 6. 既知の注意点・落とし穴

1. **タイトル = terminal 扱いになり得る**。ドライバは terminal 中に bot を呼ばないため、タイトルで
   「つづき」入力を bot が送る前に終了することがある。タイトル終了を見たら §5 の復帰手順を使う。
2. **worktree を切り替えたら 3 点を追随**: `HANJUKU_CODE_ROOT`（ドライバ）・`HANJUKU_RUNTIME`
   （新 runtime にするなら）・`HANJUKU_VIEW_RUNTIME`（ビューア）。切り替えないと古いコード/古い画面を
   見続ける。
3. **レース**: 同一 runtime に対してドライバを二重起動しない（入力が二重になる）。起動前に `pgrep` で確認。
4. **SRAM が命綱**。`HANJUKU_SRAM_OUT` を必ず指定し、停止時に `.srm` の mtime が更新されていることを
   確認する。SRAM を消すと第9話の進行（バッテリーセーブ）も消える。
5. **バイト単位の state はボット/コードの版に依存しない**（`retro_serialize` はコアの状態のみ）が、
   bot の解釈は版で変わる。v9xx を上げるときは必ずコミット→テスト→再起動の順で。
6. **並行セッションの未コミット変更を巻き込まない**: `git status` を確認し、他者の作業（例:
   `judgment/battle.py`）はコミットしない。
7. **ドライバ/ビューアは未追跡の tmp ツール**。`git clean` や `tmp` の掃除で消さない。別途バックアップを
   取るなら `tmp/hanjuku-local/*.py` をコピーする。

## 7. 現況（2026-11-16 13:40 JST 時点）

- ビューア: 稼働中（pid 2829、`http://127.0.0.1:8765/`）。
- ドライバ: **停止中**。11:14 に `[drv] terminal: True`（タイトル復帰を terminal と判定）で正常終了し、
  state はタイトル画面で保存（`tmp/hanjuku-local/states/prod1190.state`、`.prev` は 05:02 のもの）。
- 最後に観測できた進行: 第9話 month 70-5、captured `タオ/ハヤク`（サヌトは 8 時間の間に失陥）。
- 再開するときは §5 の「タイトルに落ちたときの復帰」を先に読み、**blind な START 連打をしない**こと。

### タイトル復帰を恒久化する改善案（follow-up 候補）

現状は「タイトル復帰」を terminal として確定するとドライバが bot の判断を経ずに終了するため、
v922 で追加したタイトル分岐（down+wait+A＝つづき）が走る前に止まり得る。改善案:

1. ドライバの terminal 判定で、タイトルは「つづき試行が尽きるまで terminal にしない」例外にする、または
2. bot 側でタイトル時に terminal を確定せず、つづき入力を返す（v922 の分岐を terminal より優先）。

どちらも実機のタイトルメニュー（はじめから/つづき/ロード画面）の入力列を実測してから入れる。

## 8. 定期監視のチェックリスト

- [ ] `/status` が 5 秒以内に返る（ビューア生存）
- [ ] `ps` で core_driver が 1 本だけ動いている
- [ ] ログ末尾が `hold` の連続になっていない（停滞していないか）
- [ ] `hanjuku_decisions.jsonl` の mtime が数分以内（判断が進んでいる）
- [ ] `captured` が減っていないか（失陥の監視）
- [ ] `.state` / `.srm` の mtime が更新されている（保存が続いている）
