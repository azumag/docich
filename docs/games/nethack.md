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

`config/games/nethack.toml` の主要設定:

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

[nethack]
persistent_run = true
player_name = "docich"
save_dir = "/var/games/nethack/save"

[agent]
enabled = false
brain = "random"
interval_ms = 1500
```

`persistent_run=true` は長期攻略用の明示 opt-in。これが無い通常の CLI `nethack` 定義は、従来どおり
汎用 `CliCoordinatorAdapter` として動作し、ディストリビューション固有の save path を要求しない。

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

---

## 7. NetHack 長期攻略コーナー (#490)

P0 では既存の CLI NetHack を変更せず、独立した番組枠だけを追加する。定時コーナーと手動テストは
別々の state/lock を使うため、手動 smoke がその日の定時枠を消費しない。

通常設定は安全のため `enabled = false`。本番時刻を決めて有効化するまでは自動起動しない。

```toml
[nethack_corner]
enabled = false
start_hour = 22       # 設定例。P0では本番時刻として確定していない
duration_minutes = 30
timezone = "Asia/Tokyo"
# weekdays = [0, 2, 4]  # 任意。0=Mon .. 6=Sun
```

定時 runner:

```bash
bin/docich-nethack-corner tick
bin/docich-nethack-corner status --json
```

手動 smoke runner:

```bash
bin/docich-nethack-corner-manual start --duration-minutes 5
bin/docich-nethack-corner-manual status --json
bin/docich-nethack-corner-manual stop
```

どちらもゲーム切替を直接操作せず `GameSwitchCoordinator` を通す。開始前に別ゲームが active なら
NetHack へ transactional switch し、終了時に元のゲームへ戻す。元が idle なら NetHack 終了後も
idle に戻す。

P0 の時点では `agent.enabled=false` のままであり、「AI攻略が完成した」とは扱わない。以降は #490 の
ロードマップに従い、run 永続化 → spectator tile renderer → tactical/mid-level/LLM policy →
structured observation → 死亡履歴からの継続改善、の順に追加する。特にグラフィック表示は AI の
正確な text/structured observation と分離し、視聴者向け presentation のためだけに画像認識へ
退化させない。

---

## 8. 長期runの通常save/restore (P1a)

`persistent_run=true` の NetHack だけ、coordinator の switch/stop 前に通常の NetHack save を安全境界として
利用する。legacy `docich obs/send` は引き続き汎用 CLI adapter を使うため、AI observation 契約は変わらない。

起動時は coordinator adapter が `-u docich` を追加する。同じ Unix uid + player name を継続することで、
NetHack 自身の通常 restore を利用する。wizard (`-D`) / explore (`-X`) mode は長期攻略では拒否する。

終了時の順序:

1. active runtime/session ownership を確認
2. 実ゲームを持つ tmux birth window を一意に確認
3. 現在存在する同player saveの署名 (mtime/size) を記録
4. `Escape` でmenu/promptから抜ける
5. 通常コマンド `S` を送る
6. NetHack process window が終了したことを確認
7. **送信前から新規作成または更新された**同player saveを確認
8. 両方揃った場合だけ round-boundary を成功させ、coordinator が元ゲームへ切り替える

processだけ消えてsaveが作られない、tmux window一覧が取得できない、runtime windowが曖昧、という場合は
fail closed とし、単に「ゲームが終わった」と推測して切替を続行しない。古いsave fileが残っているだけでも
成功扱いしない。

ゲーム側が既に `S` で終了していて、process windowが無い一方で同player saveが存在する場合は
`suspended` として扱う。processもsaveも無い場合だけ `ended` と記録し、死亡・quit・ascension の分類は
P1b の run-history/dumplog 側へ委ねる。

境界の診断結果は generation runtime の `nethack_boundary.json` に `suspended` / `ended` として残す。
P1b ではこの結果と Debian/Ubuntu NetHack の dumplog を `NethackRunStore` に取り込み、冒険番号・死亡理由・
到達深度・turn・score・ascension 等を永続履歴へ接続する。
