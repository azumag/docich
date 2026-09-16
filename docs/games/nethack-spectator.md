# NetHack spectator renderer (P2)

NetHack長期攻略コーナー #490 の視聴者向け表示。ゲーム実行・AI観測・save/resumeとは独立した read-only sidecar として動く。

## P2b のデータ経路

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
TTY -> spectator cell classes -> HTML
        |
        | atomic replace
        v
state_dir/nethack/spectator/index.html
        |
        v
OBS Browser Source (Local file)
```

spectator は `send-keys` を呼ばない。coordinatorの `candidate` / `previous` / `retiring` も追わず、commit済みの `active` runtimeだけを表示する。

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

`status.json` は `idle` / `active` / `degraded` と、active時の runtime id / generation を診断用に記録する。

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

Browser Sourceで **Local file** を選び、上記 `index.html` を指定する。推奨canvasは1280x720または1920x1080。

HTMLは自分自身を定期reloadするため、OBS側の手動refreshは不要。writerは一時ファイルをfsyncしてから `os.replace()` するため、OBSが書込み途中のHTMLを読むことはない。

## 切替・障害時

- NetHack以外がactive、またはcoordinatorが遷移中: 待機画面を表示する。
- active NetHack windowのownershipがcanonical identityと一致しない: captureしない。
- tmux/canonicalの一時的な読取り失敗: `degraded` として最後の正常HTMLを維持する。
- 初回から取得できない場合だけ「NetHack画面を準備しています。」のplaceholderを生成する。
- spectator障害を理由にゲームをstop/rollbackしない。

## P2a/P2bでまだ行わないこと

現在のcell classはTTY文字をpresentation用に粗く分類したもの。NetHackの文字は文脈依存なので、この分類をAI semantic observationへ流用しない。

P2cで実タイルセット/sprite atlasへ置換し、P4でNLE等のstructured glyph observationを比較する。renderer境界はその差し替えを前提としている。
