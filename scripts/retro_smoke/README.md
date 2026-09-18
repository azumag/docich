# retro_smoke — 実バイナリでのレトロゲーム smoke

`brains/<game>/brain.py` と `games/cli-wrappers/<game>_docich.sh` は、**合成 pane の
ユニットテストだけでは検証できない**。実ゲームの pane には次のような癖があり、
実際にこれらで無人プレイが止まった (2026-09-19)。

- 枠線が `x`/`q` の文字で返り、`xScore:` のように単語文字と密着する (bastet)
- スコアが右寄せの可変スペース (`Score:      0`) (bastet)
- ランクインすると Game Over の後に名前入力プロンプトが挟まる (moon-buggy)
- 新試合の直後は自機が描画されず、最初のキー入力で現れる (ninvaders)
- ブレインが判断中に送ったキーが Game Over 画面を先に閉じ、wrapper がスコアを
  記録できない (pacman4console / ninvaders)

このツールは、使い捨ての **オフライン Docker コンテナ**（Ubuntu 24.04・実ゲーム・tmux）
で、リポジトリの実 wrapper と実ブレインを docich 本体の `CliGameAdapter` →
`CommandBrain` → エージェントループの 1 周と同じ経路で回し、実 pane と scorelog を
採取する。coordinator（表示・配信・切替ロック）は含まない。本番には一切触れない
(`--network none`、リポジトリは読み取り専用マウントのコピー、scorelog の既定の本番パスは
`/out` へ上書き)。

## 使い方

```sh
scripts/retro_smoke/run.sh bastet 300            # 結果は一時ディレクトリ (OUT で指定可)
OUT=/tmp/smoke scripts/retro_smoke/run.sh ninvaders 300
SMOKE_NULL_BRAIN=1 scripts/retro_smoke/run.sh nsnake 150   # wrapper 単体の終了画面処理を観察
```

Docker が必要。初回だけイメージ (`docich-retro-smoke:local`, 約200MB) を build する。
`SMOKE_INTERVAL_MS` / `SMOKE_DUMP_ALL=1` は `smoke_play.py` の docstring を参照。

## 出力 (`$OUT/<game>/`)

| ファイル | 内容 |
|---|---|
| `summary.json` | サイクル数・行動数・行動内訳・空観測数・pane 変化数・例外数・brain 応答時間 |
| `snapshots.json` | メニュー/ダイアログ/Game Over/名前入力の初出 pane (各 3 件) + 周期 + 変化時 |
| `scores.jsonl` | wrapper が記録した試合結果 (コーナーの「3 試合で早期終了」検知が数えるもの) |
| `actions.jsonl` / `final_pane.txt` / `stderr.log` | 生の行動ログ・最終 pane・ブレイン警告 |

## 読み方の注意

- 「動いた」と判断する前に `snapshots.json` の実 pane を見る。`cycles_with_actions` が
  多くても、メニューで無意味なキーを送っているだけのことがある。
- `scores.jsonl` が 0 件なら、ゲームが終わっていない（nsnake は死なない）か、終了画面を
  wrapper が拾えていない。`snapshots.json` の `kw` に `game over` 等があるか確認する。
- 実走は乱数を含む。差を主張する比較 (A/B) は複数走・複数試合で、レンジが重ならないか
  を見る。1 走の 1 試合で優劣を言わない。
- ここでの結果は「ローカル Docker (aarch64) の実バイナリ」での実測で、VM 本番での
  実測ではない。VM 実測は別途必要。
