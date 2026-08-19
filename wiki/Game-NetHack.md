# NetHack

NetHack は docich の `cli` アダプタの標準例である。ゲーム画面がそのままテキストで観測できる
ため、brain 開発・動作確認の題材として最も扱いやすい。本ページは要約。詳細は
[docs/games/nethack.md](https://github.com/azumag/docich/blob/main/docs/games/nethack.md)
を参照。

## 起動

```bash
bin/docich up
bin/docich start nethack
bin/docich status
```

`scripts/setup_ubuntu_arm.sh` で `nethack-console` パッケージが導入済みであることが前提
(個別導入する場合は `sudo apt install -y nethack-console`)。

## テキスト観測が特長

```bash
bin/docich obs nethack
```

`cli` アダプタは `tmux capture-pane -p` の結果をそのまま Observation JSON の `text`
フィールド (`kind: "text"`) に入れて返す。screenshot を介さないため、画面が LLM に画像より
正確に渡る。

## 操作 (send 例)

```bash
bin/docich send nethack '{"type":"text","text":"h"}'          # 左移動 (vi キー)
bin/docich send nethack '{"type":"special","key":"Escape"}'
```

`text` は `tmux send-keys -l` (リテラル送出)、`special` はキー名 (`Escape`/`Enter`/`C-c` 等)
を `send-keys` に渡す。`docich-game` セッションは読み取り専用で xterm に表示されているだけ
なので、Xvfb 上の画面を直接クリック/キー入力しても NetHack には届かない。入力は必ず `send`
(内部で `tmux send-keys`) 経由で行うこと。

## フォント調整

game window では xterm が `docich-game` セッションを読み取り専用 attach して Xvfb 上に
表示する。既定の monospace 18pt で 80 桁がちょうど 1280px 幅に収まり、NetHack の 80x24 画面が
そのまま表示される。別解像度・フォントを使う場合は `config/games/nethack.toml` の
`[cli] font_size` (/ `font`) を変更する。

`docich-game` セッションの tmux status バーは既定で off になっている (配信画面下部に緑の
ステータスバーが映り込むのを防ぐため)。

## 補足

NetHack は例であり、`[cli] command` を差し替えれば任意の CLI/TUI ゲームが同じアダプタで動く
(詳細: [[対応ゲーム|Games]])。ゲームの生死は `docich-game` tmux セッションの生死で判定する
ため、xterm 表示側が落ちても (映像が消えるだけで) ゲーム進行自体は失われない。

## 詳細

- [docs/games/nethack.md](https://github.com/azumag/docich/blob/main/docs/games/nethack.md)
- [docs/architecture.md](https://github.com/azumag/docich/blob/main/docs/architecture.md) §4.2
