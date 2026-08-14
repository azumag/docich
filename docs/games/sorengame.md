# soren game (Unity WebGL) を docich で動かす

soren game は docich の `browser` アダプタで動かす。**soren 本体 (AI 戦略・自動プレイ・配信システム)
の Linux 移植そのものは本書の管轄ではない。** 移植計画は `docs/soren_linux_migration_plan.md` を、
移植先の VM 構築は `docs/oracle_arm_setup_guide.md` を参照。docich が担うのは
「ブラウザを起動し、その画面を Xvfb 上に映して ffmpeg で配信する」という**表示の場の提供**である
(`docs/architecture.md` §4.3)。

## 表記ルール

- 【確認済】: 一次情報 (パッケージリポジトリ・`docs/architecture.md`・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 0. 前提: soren 本番稼働との関係 (重要)

**この VM では soren (sorengame) の本番システムが既に稼働している** (docich CLI とは別に、
soren 自身の tmux セッション・ディスプレイ `:99`・PulseAudio sink で動作中)。本書で説明する
docich の `browser` アダプタは、**docich 自身のディスプレイ `:98` 上に同じゲームを表示する
別インスタンス**であり、稼働中の soren 本番配信とは完全に独立している (`docs/architecture.md` §0)。
`bin/docich start sorengame` 等の docich 側の操作が、本番 soren のプロセス・配信に影響することはない。

---

## 1. 位置づけ: 2 つの運転形態

### 形態 A: docich 単体の最小利用 (URL を開いて配信に乗せるだけ)

docich の `browser` アダプタが chromium を kiosk 起動し、指定 URL を開いて画面を docich の配信に
乗せる。汎用の `key` / `mouse` 入力のみで、soren 独自の戦略 AI は使わない最小構成。

```bash
bin/docich up
bin/docich start sorengame
```

### 形態 B: soren 側システムで運転する場合

soren game の本運転は soren リポジトリの既存システム (Playwright/CDP + 戦略 AI) が担う。
docich 側は `launch_command` の差し替え (soren の起動スクリプトを呼ぶ) と `[agent] enabled = false`
で「場所と映像の提供」に徹する (`docs/architecture.md` §4.3)。

```toml
# config/games/sorengame.toml (形態 B の例)
[browser]
launch_command = ["bash", "-lc", "cd ~/soren && ..."]  # soren 本体の起動スクリプトに差し替え

[agent]
enabled = false   # soren 側の自動化が運転するため docich agent は使わない
```

---

## 2. `config/games/sorengame.toml` について

既定値の `url` は仮値であり、実 URL への差し替えが必要:

```toml
[browser]
url = "https://example.invalid/sorengame"   # TODO: 実 URL に差し替え
kiosk = true
binary = "auto"
```

`binary = "auto"` は `chromium` / `chromium-browser` / `google-chrome` / playwright の chrome を
順に自動検出する (`docs/architecture.md` §4.3)。`scripts/setup_ubuntu_arm.sh` は既定では
chromium をインストールしないため、いずれかを別途用意する必要がある (選択肢はスクリプト本体の
コメントを参照)。

---

## 関連ドキュメント

- soren 本体の Linux 移植計画・リスク: `docs/soren_linux_migration_plan.md`
- 移植先 VM (Oracle Ampere A1) の構築手順: `docs/oracle_arm_setup_guide.md`
- docich 側のアダプタ設計・共存原則: `docs/architecture.md` §0, §4.3
