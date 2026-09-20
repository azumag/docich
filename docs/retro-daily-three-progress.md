# レトロ日次3試合＋各ゲーム動作ブレイン: 実装・検証の現状

**2026-09-19更新 (feature/retro-daily-three)。** ローカル実装＋ローカル Docker の実バイナリ検証
まで完了。**VM実測・本番反映は未実施** (docich 正規フロー: branch→PR→CI→protected main→VM gateway)。

## 実装済み

- **A スケジューラ** (`retro_corner.py`): `RetroCornerConfig` に `daily_each_game` /
  `randomize_start` / `start_window_minutes` / `target_matches` を追加 (既定は従来挙動)。
  開始時刻は `scheduled_start()` が `random.Random(f"{date}|{game}")` で窓内オフセットを
  決定的に導出 (再起動・再tickで不変)。`daily_attempts` 台帳で各ゲーム日1回。
  3試合到達は scorelog (`<state_dir>/scores/<game>.jsonl`) のコーナー開始以降の件数で検知して
  早期 finish、検知できなければ `ends_at` で終了 (時間上限は常に保持・入力停止で終わらせない)。
  `improve-once --game` で実際に走ったゲームを改善ジョブへ明示。
- **B 改善 dispatch** (`corner_improve.py`, `resolver/bot_eval.py`): `BOT_GAMES` は
  `bot_eval` の改善対応5ゲームプリセット (`ninvaders`, `nsnake`, `bastet`, `moon-buggy`,
  `pacman4console`) から単一ソースで導出する。生バイナリ＋bot_eval 自前の start/retry
  キーで bounded headless 評価を行い、Snakeなど自然な Game Over が来ないゲームは
  固定手数までに得た数値スコアを比較対象にする。候補重みは一時 weights.json を
  `DOCICH_BRAIN_WEIGHTS` で渡す。昇格 `_promote` は live brain の
  `run/brain/<game>/weights.json` へも hot-swap (brain は毎サイクル新規プロセスで読む)。
- **C/D 各ゲームのコマンドブレイン** (live `[retro_corner].games` のうち改善対応は5ゲーム):
  nsnake / bastet / moon-buggy / pacman4console / ninvaders。いずれも
  `[agent] brain="command"` で、wrapper は開始・再開・スコア記録のみを担当する。6本目のNetHackは
  `[agent] brain="nethack"` と persistent save boundary を使う。

## 実バイナリ検証 (ローカル Docker, 2026-09-19)

`scripts/retro_smoke/run.sh` (README 参照)。Ubuntu 24.04 aarch64 / tmux 3.4 / Python 3.12.3、
bastet 0.43-7build1・moon-buggy 1.0.51-14・pacman4console 1.3-1build3・ninvaders 0.1.1-5・
nsnake 3.0.1-2.1。リポジトリの実 wrapper＋実ブレインを docich の `CliGameAdapter` →
`CommandBrain` → エージェントループ1周と同じ経路で回した (coordinator は含まない)。
**合成 pane のユニットテストでは全て緑だったのに、実走で下記の不具合が出た。**

| ゲーム | 実走で見つかった問題 | 対応 | 修正後の実測 |
|---|---|---|---|
| bastet | 枠線が `x` 文字で返り `xScore:` と密着、ブレインの `\bScore:` が**一度もマッチせず 40/40 サイクル無行動**。wrapper の `Score: [0-9]+` は右寄せスコア (`Score:      0`) を読めず、0 点は記録もしない → scorelog が永久に空 | ブレインの正規表現から `\b` を除去、wrapper は可変スペース対応・0点も記録・メニュー到達時の未記録フラッシュ | 430秒で 3試合記録 (Score は毎回 0: 盲目ブレイン) |
| moon-buggy | ランクインすると Game Over の後に `please enter your name` が挟まり wrapper が進めず停止、scorelog 0 件 | 名前入力で Enter | 330秒で 8試合記録 (8〜30点) |
| pacman4console | ブレインが判断中に送ったキーが「any other key で再開」の Game Over 画面を先に閉じ、wrapper (2秒ポーリング) が記録できないことがある | スコア低下=新試合とみなして直前の最大スコアを記録 | 300秒で 2試合 (523/491)・330秒で 1試合 (586) 記録。全 pane を記録した 500秒では 2試合が終了し、**Game Over 画面は 2回とも観測されなかった**が (ブレインのキーが先に閉じた)、スコア低下検知で 2/2 記録 (817/734) |
| ninvaders | (a) `!` は**自機の弾**、脅威は `:` (爆弾) なのにブレインは `!` を回避、(b) 新試合直後は自機が描画されず、無入力だと自機が出ないまま停止 (697/1185 フレーム自機不在) | ブレイン v2: `:` を回避・自機不在ならキーで再描画・移動+発射を1回の send-keys へ | 下記 A/B |
| nsnake | (問題なし) wrapper は実 `Game Over / Retry? <Yes>` を認識し記録・再戦 (ブレイン無効の実走で 2試合記録)。ブレインは Game Over/メニューで沈黙 | — | ブレイン有効では 330秒死なず Score 0→16 (試合は終わらない) |

### ninvaders: 評価対象と実プレイヤーの一致

改善ループ (bot_eval) が評価・昇格させるのは `brains/ninvaders/brain.py` の重みだが、従来の live
プレイヤーは wrapper の左右スイープ自走で、ブレインは未接続だった (昇格しても実プレイは変わらない)。
実走 A/B (各 2 走 × 300秒、同一ホスト):

| | 記録試合 | スコア範囲 | 平均 / 中央値 | 1試合の長さ |
|---|---|---|---|---|
| 基準 (wrapper 自走) | 6 | 1950–3100 | 2775 / 2900 | 約80秒 |
| ブレイン v2 (修正後) | 8 | 4750–6800 | 5938 / 6050 | 約60秒 |

レンジが重ならず約 2.1 倍。標本は小さい (2走) が差は大きい。これを受け `config/games/ninvaders.toml`
を `ninvaders_docich.sh brain` (wrapper は開始・記録のみ) + `[agent]` command brain (interval 250ms) に
切替えた。最終再走 (コミット対象の設定そのまま) で 330秒に 3試合記録 (6300/5050/6350)。

### 改善ループ (bot_eval) の実走で見つかった問題と修正

改善ループが ninvaders を評価できるかを、本番と同じ経路 (`corner_improve._bot_evaluator` →
`run_bot_matches` → 実 tmux + 実バイナリ) で Docker 実走して確かめた。

- **試合終了を検出できず、全試合が turn cap (`maxed`) になっていた**: preset の
  `game_over_res` は `Game Over` だが、実ゲームは Game Over 画面なしでタイトルへ直行する。
  `maxed` は完走扱いにしない (fail-closed) ため `played=0` で昇格ゲートは通らず、本番既定
  (3000手×0.7秒) では1試合 約35分の空回りだった。`Press SPACE to start` (タイトル再出現) で終了を検出するよう修正。
- **判断周期が live とかけ離れていた**: 評価は 0.7秒/手、live は 250ms。敵弾は約8行/秒で落ちるため
  別のゲームを評価していた。ninvaders preset に `interval_s=0.2` / `max_turns=1500` を追加。
- **CLI (`python -m docich.resolver.bot_eval <game>`) が起動直後に落ちていた**: GameConfig を
  名前 (str) の代わりに渡していた (本番の改善ループ経路は名前を渡すため無影響)。
- 修正後の実走 (評価器・既定重み・2試合): **5900点 (504手) / 5550点 (709手)、`maxed:false`、
  `played=2`、平均5725、所要328秒**。live の実測 (平均約5900) と整合する。
- nsnake: 実走 (CLI, 1試合・上限300手) で **蛇が死なず `maxed:true` (Score 24)** だったため、
  bounded preset は固定手数時点の数値スコアを完走扱いとして比較する。コーナーでスコアが
  1件も記録されなくても、改善ジョブは bounded headless 評価へ進むため、Snakeだけが
  `no-matches` で改善経路から外れることはない。
- 評価のばらつき (live 実測 4750–6800点/試合) に対し、`improve_matches=2` / `improve_margin_pct=10` は
  ノイズを昇格と取り違え得る。値の見直しは未対応。

## 検証まとめ (実測)

- ユニット/契約テスト: Ubuntu 24.04 (`/bin/sh`=dash, Python 3.12.3, pytest 9.1.1) で
  retro/brain/wrapper/corner_improve/NetHack corner 契約 **314 passed, 31 subtests passed**。
  CI の「Retro corner contract」ステップに、この PR の新規テストを追加した
  (従来は `test_ninvaders_wrapper` しか CI で走っていなかった)。
- 回帰テストは実 pane を fixture にし、修正を外す変異確認で落ちること (旧バグの捕捉) を確認済み。
- 記録スコアは wrapper のポーリング (pacman は2秒) 時点の最大値。スコア低下検知で記録した試合は
  真の最終スコアより最大でポーリング1回分低い (実測: 740 に対し 734 を記録)。

## 未検証・残件 (成功と扱わない)

- **VM 実測・本番反映・PR/CI/マージは未実施。** ここでの実測は「ローカル Docker (aarch64) の実バイナリ」で、
  VM 本番の実測ではない。ゲームの版・`/usr/games`・pacman のレベルファイル
  (`/usr/share/pacman4console/Levels`) は Ubuntu 24.04 パッケージで確認したもので、VM の版は未確認。
- coordinator (tick・`_target_reached`・program boundary) 経由の「無人3試合で早期終了」の通し実測は
  していない。wrapper が scorelog に試合を記録するところまでを実バイナリで確認し、カウントと早期終了は
  ユニットテストで検証している。
- **bastet ブレインは盲目**: 盤面は色付き空白で `capture-pane -p` に出ず、中央へ ENTER ハードドロップ
  するだけで Score は毎回 0 (前進はするが最適配置しない)。`tmux capture-pane -e` の色情報を読む
  ブレインが必要。0 点も記録するため「3試合で早期終了」は成立する (約7分)。
- **moon-buggy ブレインは最小方策** (周期ジャンプ + 稀な射撃、クレーター追跡なし)。平均約17点。
- **nsnake は死なない**: ブレインは生き延びるが Speed 1 では得点が遅く (300秒で 8個)、Game Over が来ない
  ため 3試合検知は現実的でなく、コーナーは時間上限で終了する。
- 改善対応の5ゲームすべてに binary / bot_cmd / start・retry keys / score 正規表現の preset を持たせ、
  `unsupported-game` のまま終了する対象をライブ設定から無くした。NetHackは別経路のため
  改善ジョブを起動しない。systemd の oneshot からの
  改善は独立 transient user service に投入し、親 tick 終了で巻き取られないようにした。
- 各ゲーム `[lifecycle] require_round_boundary=false` は据え置き。
- 改善昇格の live 重みへの初回 seed (既定重みの配布) は follow-up。
