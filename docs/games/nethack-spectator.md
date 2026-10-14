# NetHack spectator renderer (P2)

NetHack長期攻略コーナー #490 の視聴者向け表示。ゲーム実行・AI観測・save/resumeとは独立した read-only sidecar として動く。

## データ経路

```text
GameSwitchStore canonical state
        |
        | phase=ready / active.game=nethack のみ
        v
active runtime identity
        |
        | tmux generation ownership を照合
        v
tmux capture-pane (read only)
        |
        v
TTY -> presentation cell classes
        |
        +--> Docich original SVG tiles (default)
        |        or
        +--> ASCII fallback
        |
        | atomic replace
        v
state_dir/nethack/spectator/index.html
        |
        v
OBS Browser Source (Local file)
```

spectator は `send-keys` を呼ばない。coordinatorの `candidate` / `previous` / `retiring` も追わず、commit済みの `active` runtimeだけを表示する。

## P2c: グラフィック表示

既定の `tiles` mode は `src/docich/nethack_tiles.py` の **Docich original SVG tiles** を使う。外部画像ファイルやネットワークアクセスは不要で、生成HTMLの中にsprite atlasを埋め込む。

現在の描き分け:

- floor / corridor
- horizontal / vertical wall
- door
- stairs up / stairs down
- player
- creature
- trap
- weapon / armor / tool
- ring / wand
- food / potion / scroll
- gem / gold / amulet
- generic item / other

TTYだけではモンスター文字などの意味を一意に決定できないため、creatureは種族を断定した絵にしない。代わりに汎用モンスターspriteへ元glyphを小さく重ね、視聴者が文字情報も失わないようにする。このglyph分類をAI semantic observationとして再利用してはいけない。

NetHack本体に付属する公式タイル画像はこのリポジトリへコピーしない。P2cの標準タイルは単純な幾何SVGから独自作成しており、NetHack本体のアート資産とは分離する。将来、利用者自身が用意した外部tilesetを選択できるadapterを追加しても、標準配布物には混ぜない。

### ASCIIへ即時フォールバック

問題があればゲームやsidecar構造を変えずに表示だけ戻せる。

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-spectator-live --visual-mode ascii
```

既定は:

```bash
--visual-mode tiles
```

`status.json` の `visual_mode` でも現在値を確認できる。

## 一回だけ生成して確認

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-spectator-live --once
```

既定の出力先は次のとおり。

```text
<state_dir>/nethack/spectator/index.html
<state_dir>/nethack/spectator/status.json
```

`status.json` は `idle` / `active` / `degraded`、`visual_mode`、active時の runtime id / generation を診断用に記録する。

## 常駐実行

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-spectator-live
```

既定では500msごとにcanonical stateと画面を確認し、HTML自身は750msごとにreloadする。変更する場合:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-spectator-live --interval-ms 500 --refresh-ms 750
```

systemd templateは `scripts/systemd/docich-nethack-spectator.service`。本番へinstall/enableするまでは自動起動しない。

## OBS

Browser Sourceで **Local file** を選び、上記 `index.html` を指定する。推奨canvasは1280x720または1920x1080。タイルはviewBoxベースのSVGなので、解像度を上げてもラスター画像のようには劣化しない。

HTMLは自分自身を定期reloadするため、OBS側の手動refreshは不要。writerは一時ファイルをfsyncしてから `os.replace()` するため、OBSが書込み途中のHTMLを読むことはない。

## 切替・障害時

- NetHack以外がactive、またはcoordinatorが遷移中: 待機画面を表示する。
- active NetHack windowのownershipがcanonical identityと一致しない: captureしない。
- tmux/canonicalの一時的な読取り失敗: `degraded` として最後の正常HTMLを維持する。
- 初回から取得できない場合だけ「NetHack画面を準備しています。」のplaceholderを生成する。
- spectator障害を理由にゲームをstop/rollbackしない。

## 次段階

P2cまでで「配信映像をグラフィック化する」経路は成立する。次はP3でゲーム操作を以下の3層に分ける。

1. tactical: 定型入力、単純戦闘、退避、`--More--` 等を低遅延ローカルpolicyで処理
2. mid-level: 探索、stairs、HP/hunger/resource、inventory pressureを状態機械で判断
3. strategic: 未鑑定品、長期build、branch progression、危険な意思決定だけLLMへ委任

P4ではNLE等のstructured glyph observationを比較し、AI observationとpresentationの双方が使えるnormalized schemaを検討する。ただしhidden informationをAIへ漏らさないことを必須条件とする。
