# 半熟英雄 (SFC)

半熟英雄 (SFC) は docich の `retroarch` アダプタ (RetroArch + libretro コア) で動かす。
本ページは要約。詳細な手順・裏付けは
[docs/games/hanjuku-hero.md](https://github.com/azumag/docich/blob/main/docs/games/hanjuku-hero.md)
を参照。

## ROM 配置

自己吸い出しした ROM を `games/roms/hanjuku-hero.sfc` に置く (docich は ROM の入手方法には
一切関与しない。ポリシー: `games/roms/README.md`)。ファイル名は
`config/games/hanjuku-hero.toml` の `[retroarch] rom` と一致させる。

```bash
bin/docich up
bin/docich start hanjuku-hero
bin/docich status
```

## コアの自動探索 (`core = "auto"`)

`/usr/lib/*/libretro/` を次の優先順位で探索し、最初に見つかった `.so` を使う:

1. `snes9x` (`libretro-snes9x`) — 既定候補。軽量で Oracle A1 (2 OCPU) に適する
2. `bsnes_mercury_performance` (`libretro-bsnes-mercury-performance`)
3. `bsnes_mercury_balanced` (`libretro-bsnes-mercury-balanced`)

コアを固定したい場合は `core` に `.so` の絶対パスを直接指定する。

## 操作確認

```bash
bin/docich send hanjuku-hero '{"type":"pad","buttons":["start"]}'
bin/docich snap
bin/docich ra-cmd SAVE_STATE
```

`pad` の意味ボタン (`a`/`b`/`x`/`y`/`l`/`r`/`start`/`select`/方向) → 物理キーの対応は
`docs/architecture.md` §3.3 を参照。

## 代表的なトラブル

- **起動直後に落ちる (Aborted)**: セッション D-Bus が無い環境では RetroArch 1.18 の GameMode
  統合が abort する。docich は `dbus-run-session -- retroarch ...` で包んで起動する実装に
  なっている (`dbus` パッケージが必要)。
- **入力が効かない**: `input_driver = "sdl2"` (docich が生成する `run/retroarch/retroarch.cfg`
  に既定で入る)、押下時間 (`hold_ms` を既定の 100ms 未満に下げない)、フォーカス取得
  (`windowfocus --sync`) の 3 点が要点。

いずれも詳細は [[トラブルシューティング|Troubleshooting]] を参照。

## サブモジュール: games/hanjuku-sfc-speedrun

半熟英雄の RTA チャート・ゲーム機構データ (卵落ち判定・切り札ダメージ等の分析ツールを含む)
は git submodule `games/hanjuku-sfc-speedrun` (`azumag/hanjuku-sfc-speedrun`, main 追跡)
として取り込まれている。**Phase 2 で実装する brain の知識ベース** (プロンプトの grounding
素材) として使う計画になっている。詳細は
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
§1・§5 を参照。

## Phase 2: 本物の brain (計画中)

半熟英雄の本物の brain (画面認識・戦略プロンプト) は Phase 2 で実装する計画。スクリーン
ショット → claude CLI → pad 操作、というプロンプト設計と、`ra-cmd SAVE_STATE` を絡めた
復帰運用が予定されている。brain は `games/hanjuku-sfc-speedrun` のチャート/データを知識
ベースにする想定 (上記参照)。Phase 1 時点では `config/games/hanjuku-hero.toml` の
`[agent] enabled = false` のまま、`bin/docich send` での単発操作確認にとどまる。

## 詳細

- [docs/games/hanjuku-hero.md](https://github.com/azumag/docich/blob/main/docs/games/hanjuku-hero.md) — ROM 配置・コア選択・トラブルシュートの全文
- [docs/architecture.md](https://github.com/azumag/docich/blob/main/docs/architecture.md) §4.1・§9 — 設計の裏付け
- [[アーキテクチャ|Architecture]] — wiki 側の設計ダイジェスト
