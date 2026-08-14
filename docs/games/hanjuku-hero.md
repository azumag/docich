# 半熟英雄 (SFC) を docich で動かす

半熟英雄 (SFC) は docich の `retroarch` アダプタ (RetroArch + snes9x コア) で動かす。
本書は ROM 配置からの起動手順とトラブルシュートをまとめる。設計の背景は
`docs/architecture.md` §4.1 / §9 を参照。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 1. 準備: ROM の配置

1. 自己吸い出しした ROM ファイルを `games/roms/hanjuku-hero.sfc` に置く。
   - docich は ROM の取得・配布に一切関与しない。著作権ポリシーは `games/roms/README.md` を参照。
   - ファイル名は `config/games/hanjuku-hero.toml` の `[retroarch] rom` と一致させる
     (既定値は `games/roms/hanjuku-hero.sfc`)。
2. `config/games/hanjuku-hero.toml` の内容を確認する (既定値):

   ```toml
   [game]
   name = "hanjuku-hero"
   title = "半熟英雄 (SFC)"
   adapter = "retroarch"

   [retroarch]
   rom = "games/roms/hanjuku-hero.sfc"
   core = "auto"

   [agent]
   enabled = false     # 半熟英雄 brain は Phase 2 (§7 参照)
   brain = "command"
   command = ""
   interval_ms = 2000
   ```

---

## 2. 起動

```bash
bin/docich up                 # display(:98)/audio/stream 基盤を起動
bin/docich start hanjuku-hero # ゲーム起動
bin/docich status              # 起動確認
```

---

## 3. コア選択 (`core = "auto"`)

`core = "auto"` の場合、`/usr/lib/*/libretro/` を以下の優先順位で探索し、最初に見つかったコアの
`.so` を使う (`docs/architecture.md` §4.1)。

| 優先順位 | コア | apt パッケージ | 状態 |
|---|---|---|---|
| 1 | snes9x | `libretro-snes9x` | 【確認済】Ubuntu 24.04 universe に arm64 版 (1.61) が存在 (packages.ubuntu.com で確認)。RetroArch 本体 (1.18.0) も同様に存在 |
| 2 | bsnes_mercury_performance | `libretro-bsnes-mercury-performance` | 【確認済】arm64 版 (094+git20220807) が存在 (packages.ubuntu.com で確認) |
| 3 | bsnes_mercury_balanced | `libretro-bsnes-mercury-balanced` | 【確認済】同上 |

snes9x は軽量で Oracle A1 (2 OCPU) に適するため既定の第一候補になっている。コアを固定したい
場合は `core` に `.so` の絶対パスを直接指定する。

音声は `audio_driver = "pulse"` で生成 cfg に固定され、`PULSE_SINK=docich_sink` 環境変数で
docich 用の sink にルーティングされる (soren の既定 sink は変更しない。`docs/architecture.md` §0)。

---

## 4. ra-cmd (RetroArch へのコマンド送信)

`docich ra-cmd` は RetroArch の Network Command インターフェース (UDP, ポート 55355。生成 cfg で
`network_cmd_enable = true` になっている) 経由でコマンドを送る。

```bash
bin/docich ra-cmd SAVE_STATE      # 現在の状態を保存
bin/docich ra-cmd LOAD_STATE      # 直前のセーブステートを復元
bin/docich ra-cmd PAUSE_TOGGLE    # 一時停止の切替
```

配信中に落ちた際の復帰や、brain 開発時の巻き戻しデバッグに使う。

---

## 5. 操作確認

入力とスクリーンショットの単発確認:

```bash
bin/docich send hanjuku-hero '{"type":"pad","buttons":["start"]}'
bin/docich snap                                  # run/screenshots/ に保存
```

`pad` の意味ボタン (`a`/`b`/`x`/`y`/`l`/`r`/`start`/`select`/方向) → 物理キーの対応は
`docs/architecture.md` §3.3 を参照。

---

## 6. トラブルシュート

### 入力が効かない

- 生成された `run/retroarch/retroarch.cfg` に **`input_driver = "sdl2"`** が入っているか確認する。
  【確認済】既定の `udev` ドライバは Xvfb + xdotool からの XTEST 入力を受け付けず、`"x"` ドライバも
  sdl2 ビデオドライバと組むと初期化されない。`"sdl2"` で XTEST キーが届くことを RGUI メニュー操作で
  実証済み (`docs/architecture.md` §9-1)。
- フォーカスの取得方法に注意する。**WM (ウィンドウマネージャ) の無い Xvfb では
  `windowactivate` (EWMH 依存) は機能しない。** docich は `windowfocus --sync`
  (XSetInputFocus を直接叩く) でフォーカスしてから XTEST (`keydown`/`keyup`) を送る実装になっている
  【確認済】(`docs/architecture.md` §9-2)。
- **押下時間にも注意**。RetroArch はフレーム毎のキー状態ポーリングのため、瞬間的な press/release は
  取りこぼされる【確認済】。docich の `pad`/`key` アクションの `hold_ms` (既定 100ms) を
  下げすぎないこと (`docs/architecture.md` §4.1)。
- それでも改善しない場合の代替案: RetroArch Network Remote (UDP パッド) 経由の入力。【要検証】

### 起動直後に落ちる (Aborted)

- 【確認済】セッション D-Bus が無い環境では GameMode 統合が abort する。docich は
  `dbus-run-session -- retroarch ...` で包んで起動する設計になっている
  (`docs/architecture.md` §4.1, §9-1b)。`dbus-daemon` パッケージ (メタパッケージ `dbus`) が
  入っているか確認する。

### 映像が出ない / 真っ黒

- `video_driver = "sdl2"` になっているか確認する。Xvfb は GLX が無い/不安定なことがあり、
  `"gl"` だと失敗し得る (§9-3)。SNES コアはソフトレンダなので sdl2 で十分な性能が出る想定。
- それでも出ない場合は mesa (llvmpipe) 経由の `"gl"` を試す選択肢がある。【要検証】

---

## 7. Phase 2: brain (今後の計画)

半熟英雄の本物の brain (画面認識・戦略プロンプト) は Phase 2 で実装する
(`docs/architecture.md` §10)。スクリーンショット → claude CLI → pad 操作、というプロンプト設計と、
`ra-cmd SAVE_STATE` を絡めた復帰運用が計画されている。Phase 1 時点では
`config/games/hanjuku-hero.toml` の `[agent] enabled = false` のまま、
`bin/docich send` での単発操作確認にとどめる。
