# 半熟英雄ローカル ドライバ / ビューア運用メモ

半熟英雄のローカル攻略を回すための**ヘッドレスドライバ**と**読み取り専用ビューア**の扱いをまとめる。
本番VMのアダプタ（Soren）とは別に、ローカルでは snes9x libretro コアを直接叩いて同じ bot
（`brains/hanjuku/bot.py` / `src/docich/hanjuku/`）を回す。

- ドライバ: `tmp/hanjuku-local/core_driver.py`（リポジトリ外・未追跡）
- ビューア: `tmp/hanjuku-local/viewer.py`（リポジトリ外・未追跡）
- コア: `tmp/hanjuku-local/cores_arm64/snes9x_libretro.dylib`（`cores_161/` は現行 state を読めない。§5.1）
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
HANJUKU_CORE=/Users/azumag/work/docich/tmp/hanjuku-local/cores_arm64/snes9x_libretro.dylib \
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

## 5.1 停止後の復帰手順（2026-11-17 実測）

同じ日の復旧で踏んだ罠と、その順序。**この順でやらないとドライバは即終了する。**

1. **コアを確認する**。`cores_161/` のコアでは現行の `.state` が読めない
   （`retro_unserialize` が False）。`cores_arm64/snes9x_libretro.dylib` を使う。
2. **古い terminal と停滞時計を消す**。`<runtime>/hanjuku_run.json` に
   `terminal_reason` / `terminal_candidate` が残っていたり、`unchanged_since` が古いままだと、
   1 観測目で `screen_stalled` になりドライバが終了する。編集前に `.bak-<日時>` へコピーし、
   次の値を現在値（数値）へ戻す:
   - `terminal_reason=null` / `terminal_candidate=null` / `terminal_evidence=null` / `terminal_at=0`
   - `unchanged_since=now(monotonic)` / `unchanged_seconds=0`
   - `frame_unchanged_since=now` / `frame_unchanged_seconds=0`
   - `title_since=now` / `title_count=0` / `last_input_monotonic=now`
   - `last_input_observations=observations`
   - `playing=true`
3. **runtime は `HANJUKU_CODE_ROOT` の中に置く**。外に置くと bot が
   `invalid runtime`（`relative_to(ROOT)` が 3 要素でない）で判断不能になり、無入力のまま進む。
4. **worktree 追随は run worktree の HEAD を detached で切り替える**。検証したい commit を
   `.worktrees/hanjuku-local-main` で `git checkout --detach <sha>` してから再起動する。
   `HANJUKU_CODE_ROOT` を別 worktree へ向けると 3. に当たる。

### タイトルに落ちた state からの復帰（実測した入力列）

タイトル画面の文字は `スクウェア19921992` だけで、メニュー項目は画像（読めない）。
**中身のある `.srm` があれば**次の入力列でロードできる（実測: セーブ1 = だい11わ 57ねん9のつき）:

| 入力 | 画面 |
|---|---|
| Start | メニュー（NEWGAME 等） |
| A | `どれをロードなされますかな?` ＋ `ーセーブ1ーだい11わ …` |
| A | `このデータをロードするのですな? うむッ! / いかんッ!` |
| A | 遷移（画面は空） |
| A | 先発隊ピッカー `…どのしょうぐんを せんぱつたいに くわえますか` ＝ **ロード完了** |

- 再開前に **`.srm` が非ゼロか**必ず確認する。`prod1190.srm` は 8192 バイトすべてゼロで、
  「セーブがある」と誤認しやすい（有効な例: `g1190-ch9run.srm` は `HAN2…` で非ゼロ）。
- `.srm` が全ゼロだと同じ A が NEWGAME を確定し、導入文
  `ひとりのゆうしゃが でんせつのけんを てに …` と名前入力へ進む（＝新規ゲーム）。
- **この列が効くのは「ROM から起動して `.srm` を注入した」ときだけ**（2026-11-17 実測）。
  - タイトル画面を保存した `.state` をロードする経路では、`retro_unserialize` が SRAM も復元し、
    タイトルメニューの「セーブ有無」は起動時に確定済みなので、後から `.srm` を注入しても
    一覧は出ず Start→A が新規ゲームになる（state 復元の前後どちらに注入しても同じ）。
  - したがって**タイトルで保存された state は入力では復帰できない**。`.state.prev` など
    タイトル前の state に戻すか、ROM 起動 + 有効 `.srm` + `HANJUKU_LOAD_FLOW=1` を使う。
- Start のタイミングにも依存する（ROM 起動 + 有効 `.srm` の実測）:
  attract 開始から **約400フレーム**または**850フレーム以降**の Start はセーブ一覧が開くが、
  **550〜700フレーム**ではタイトルのままだった。1観測（約90フレーム）ごとに1入力するボット主導の
  復帰はこの窓を安定して踏めないため、**復帰はドライバの `HANJUKU_LOAD_FLOW` に任せる**。
- ドライバの `HANJUKU_LOAD_FLOW=1` は attract を約760フレーム待ってからこの実測列（Start→A×5）を送り、
  各確定後に画面を読む。新規ゲームの導入文や名前入力を検出したら **その場で停止して
  バッテリーSRAMを書き戻さない**（exit 3）。先発隊ピッカー／`HP` 行を確認したらロード成功として
  本ループへ入る。タイトルメニューのままなら exit 2。

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

## 7. 現況（2026-11-16 17:2x JST 時点）

- ビューア: 稼働中（`http://127.0.0.1:8765/`）。
- ドライバ: 稼働中、`HANJUKU_CODE_ROOT=.worktrees/hanjuku-ch9-clear`、bot `hanjuku-chart-v951-...`、
  第9話 month 16-2 からリプレイ中。
- **2026-11-16 夕方の事故と復帰（重要）**:
  - `pkill -TERM` 後に `.state` / `.prev` を読むと `load state -> False` で失敗し、
    新規ブート→タイトルで `terminal: True` 終了した。「タイトルに落ちた」ではなく
    **state の unserialize 失敗**。ファイルヘッダは `#!s9xsnp:0011`（正常に読める
    `ch9-local.state` は `0014`）で、停止時の serialize が現行コアと互換でない形式を
    書いた疑いがある。読めない state に再試行し続けると終了時の上書きで元の進行が消える。
  - `prod1190.srm` のセーブスロットは第9話（13:41 の slot.png で `だい9わ 16ねん2のつき`
    を確認）だが、そのまま LOAD_FLOW すると第1話が始まった — スロット内の実データが
    ロード可能な第9話セーブではなかった。月16→35の進行は state のみにあり失われた。
  - `ch9-local.srm`（= `prod1190.srm.bak`、同 MD5）を `HANJUKU_SRAM_IN` にして
    `HANJUKU_LOAD_FLOW=1 --noload` で起動すると第9話 16-2 に正しく復帰した。
  - 教訓: state を読めないときは無理に再試行せず、**まず全 `.state`/`.srm` を
    `/tmp` などへ丸ごとバックアップ**してから、確実な第9話 srm（slot.png で章を確認）
    を `SRAM_IN` にする。`SRAM_OUT` は別名ファイルへ向けて既存 srm を上書きしない。

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
