# NetHack を docich で動かす

NetHack は docich の `cli` アダプタの標準例である。ゲーム画面がそのままテキストで観測できるため、
brain 開発・動作確認の題材として最も扱いやすい。設計の背景は `docs/architecture.md` §4.2 を参照。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 1. インストール

`scripts/setup_ubuntu_arm.sh` で `nethack-console` パッケージ (Ubuntu 24.04 universe) が
導入される。個別に入れる場合:

```bash
sudo apt install -y nethack-console
```

---

## 2. 起動

```bash
bin/docich up             # display(:98)/audio/stream 基盤
bin/docich start nethack  # ゲーム起動 (docich-game tmux セッション内で nethack を実行)
bin/docich status
```

`config/games/nethack.toml` の既定値:

```toml
[game]
name = "nethack"
title = "NetHack"
adapter = "cli"

[cli]
command = "nethack"
cols = 80
rows = 24
font = "monospace"
font_size = 18

[agent]
enabled = false
brain = "random"
interval_ms = 1500
```

---

## 3. 観測 (テキストで取れる)

```bash
bin/docich obs nethack
```

`cli` アダプタは `tmux capture-pane -p` の結果をそのまま Observation JSON の `text` フィールドに
入れて返す (`kind: "text"`)。screenshot を介さないため、brain (LLM) に画面がそのまま正確に渡る
(`docs/architecture.md` §3.1, §4.2)。

---

## 4. 操作

```bash
bin/docich send nethack '{"type":"text","text":"h"}'    # 左移動 (vi キー)
bin/docich send nethack '{"type":"special","key":"Escape"}'
```

`text` は `tmux send-keys -l` (リテラル送出)、`special` はキー名 (`Escape`/`Enter`/`C-c` 等) を
`send-keys` に渡す (§3.2, §4.2)。

`docich-game` セッションは読み取り専用 (`tmux attach -r`) で xterm に表示されているだけなので、
Xvfb 上に映る画面を直接クリック/キー入力しても NetHack には届かない【確認済】。入力は必ず
`bin/docich send` (内部で `send-keys`) 経由で行うこと。誤操作が紛れ込みにくい構造になっている。

---

## 5. 表示の調整 (xterm フォントサイズ)

game window では xterm が `tmux attach -r` (読み取り専用) して端末を Xvfb 上に表示する。
【確認済】monospace 18pt (既定値) で 80 桁がちょうど 1280px 幅に収まり、NetHack の 80x24 画面が
そのまま綺麗に表示されることをコンテナで実証済み。別の解像度・フォントを使う場合は
`config/games/nethack.toml` の `[cli] font_size` を変更して調整する。

また `docich-game` セッションの tmux status バーは off にしてある。付けたままだと配信画面の
下部に tmux の緑色ステータスバーが映り込むため【確認済】。

---

## 6. 補足

NetHack は例であり、`[cli] command` を差し替えれば任意の CLI/TUI ゲームが同じアダプタで動く
(§4.2)。ゲームの生死は `docich-game` tmux セッションの生死で判定するため、xterm 表示側が
落ちても (映像が消えるだけで) ゲーム進行自体は失われない。
