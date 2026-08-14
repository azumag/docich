# soren game (Unity WebGL)

soren game は docich の `browser` アダプタで動かす。**soren 本体 (AI 戦略・自動プレイ・配信
システム) の Linux 移植そのものは docich の管轄ではない**(移植計画は下記関連ドキュメント)。
docich が担うのは「ブラウザを起動し、その画面を Xvfb 上に映して ffmpeg で配信する」という
表示の場の提供のみである。

## 本番 soren との関係 (重要)

**この VM では soren (sorengame) の本番システムが既に稼働している** (docich とは別に、
自身の tmux セッション・ディスプレイ `:99`・PulseAudio sink で動作中)。docich の `browser`
アダプタは、docich 自身のディスプレイ `:98` 上に同じゲームを表示する**別インスタンス**で
あり、稼働中の本番配信とは完全に独立している。`bin/docich start sorengame` などの docich
側の操作が、本番 soren のプロセス・配信に影響することはない。

## サブモジュール: games/soviet_now

sorengame 本体の実装 (Unity WebGL 自動プレイ、`soviet_local.mjs` / `strategy.py` / `soren91`
等) は git submodule `games/soviet_now` (`azumag/soviet_now`, main 追跡) として取り込まれて
いる。

**現在 `games/soviet_now` では Codex が OBS → ffmpeg 直結配信への移行を並走中のため、docich
側からは読み取り専用として扱い、変更を加えない。** main の毎時監視 (Routine) で新規コミット
の有無を確認し、変化があれば内容を評価したうえでサブモジュールポインタを進める運用に
なっている。現状把握・共通部品化ロードマップ (C0〜C4) の詳細は
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
§2・§4 を参照。

## 2 つの運転形態

- **形態 A (docich 単体の最小利用)**: `browser` アダプタが chromium を kiosk 起動し、
  指定 URL を開いて画面を docich の配信に乗せる。汎用の `key` / `mouse` 入力のみで、
  soren 独自の戦略 AI は使わない最小構成。
  ```bash
  bin/docich up
  bin/docich start sorengame
  ```
- **形態 B (soren 側システムで運転)**: soren game の本運転は soren リポジトリの既存システム
  (Playwright/CDP + 戦略 AI) が担う。docich 側は `[browser] launch_command` を soren の
  起動スクリプトに差し替え、`[agent] enabled = false` のまま「場所と映像の提供」に徹する。

## 設定の要点

`config/games/sorengame.toml` の既定 `url` は仮値 (`https://example.invalid/sorengame`)
であり、**実 URL への差し替えが必須**。`binary = "auto"` は `chromium` /
`chromium-browser` / `google-chrome` / playwright の chrome を順に自動検出する。
`scripts/setup_ubuntu_arm.sh` は既定では chromium を導入しないため、別途用意が必要
(選択肢: snap 版 chromium の導入、または soren の Playwright chromium
(`~/.cache/ms-playwright/`) を `[browser] binary` に指定して共用する)。

## 関連ドキュメント

- [docs/games/sorengame.md](https://github.com/azumag/docich/blob/main/docs/games/sorengame.md) — 本ページの詳細版
- [docs/soren_linux_migration_plan.md](https://github.com/azumag/docich/blob/main/docs/soren_linux_migration_plan.md) — soren 本体の Linux 移植計画・リスク
- [docs/oracle_arm_setup_guide.md](https://github.com/azumag/docich/blob/main/docs/oracle_arm_setup_guide.md) — 移植先 VM (Oracle Ampere A1) の構築手順
- [[アーキテクチャ|Architecture]] §4.3 相当 (`docs/architecture.md`) — docich 側のアダプタ設計・共存原則
