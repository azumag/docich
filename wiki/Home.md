# docich wiki

docich は、VM 上で AI がゲームをプレイし、その画面と音声を ffmpeg で配信するための基盤である。
プレイするゲームは「アダプタ」で抽象化されており、`browser` (ブラウザゲーム) / `retroarch`
(レトロエミュレータ) / `cli` (端末ゲーム) を差し替えて同じ配信基盤の上で動かせる。

この wiki は入り口・運用ハンドブックである。詳細な設計・手順は複製せず、repo の `docs/` と
README を一次情報として要約 + リンクする。**記載が repo 側と食い違う場合は repo 側 (docs/,
README, コード) が正しい。**

## 現在地

**Phase 1 完了 (PR #2 マージ済み)**: 基盤 (CLI・設定読み込み・状態管理・監督ループ・配信コマンド
構築) + アダプタ 3 種 (`retroarch` / `cli` / `browser`) がそろっている。
**Phase 2 の半熟英雄 LLM brain (`brains/hanjuku/`、既定無効) と Phase 3 の運用強化
(watchdog / `docich rotate` / systemd 雛形) も実装済み** ([[半熟英雄 (SFC)|Game-Hanjuku-Hero]]、
[[日常運用|Operations]])。残るは実機 (Oracle ARM) での ROM 実プレイ検証
(詳細: [[アーキテクチャ|Architecture]] / `docs/architecture.md` §10)。

マルチリポジトリ構成の**C0 (サブモジュール組み込み・main 監視の開始) も完了**している。
ゲーム固有の実装は `games/soviet_now` (sorengame 本体) と `games/hanjuku-sfc-speedrun`
(半熟英雄 RTA データ) のサブモジュールに分離済みで、soviet_now 側の main 更新を毎時監視し、
変化があれば docich 側が後追いで反映する運用になっている (詳細: [[アーキテクチャ|Architecture]]
のマルチリポジトリ構成、`docs/multi_repo_plan.md`)。

続く **C1 (配信経路の役割整理) と C-caption (字幕の共通部品化) も完了**している。Codex の
作業により字幕の共通部品 (`src/docich/captions.py` + `native/ffmpeg/`) が最初の共通部品として
docich へ昇格し (main PR #3)、Soren 本番は soviet_now の `soren-runtime.service` が FFmpeg
直接配信とネイティブ Twitch 字幕で稼働中である。単体テストは 371 件
(詳細: [[アーキテクチャ|Architecture]]、`docs/multi_repo_plan.md`、
`docs/twitch_closed_captions.md`、`handoff.md`)。

## 全体像 (簡約)

```
tmux セッション "docich"
  display (Xvfb :98) ─┐
  audio   (null sink) ─┤→ 常駐、ゲームを切り替えても止まらない
  stream  (ffmpeg)    ─┘
  game    (アダプタが起動するゲーム本体) ← switch で入れ替わる
  agent   (観測→brain→行動、[agent] enabled のゲームのみ)
```

正確な構成 (アダプタ契約・soren 共存原則など) は [[アーキテクチャ|Architecture]] または
`docs/architecture.md` を参照。上の図はさらに簡略化してあるので注意。

## まず読む

1. [[クイックスタート|Quickstart]] — clone から起動・配信有効化までの最短手順
2. [[日常運用|Operations]] — ゲーム切替・状態確認・ログ調査・停止再起動
3. [[対応ゲーム|Games]] — 対応ゲーム一覧と各ゲームの個別ページへの入り口

## 全ページ

- [[Home]] — この wiki のトップページ (現在地)
- [[クイックスタート|Quickstart]] — 導入から起動・配信有効化までの最短手順
- [[日常運用|Operations]] — ゲーム切替・状態確認・ログ・停止再起動の運用ハンドブック
- [[配信の明示終了|Stream-Ending]] — FFmpeg を kill せず正常終了させ Twitch 配信を即offする手順
- [[対応ゲーム|Games]] — 対応ゲーム一覧表とゲームの追加方法
- [[半熟英雄 (SFC)|Game-Hanjuku-Hero]] — RetroArch アダプタでの半熟英雄の動かし方
- [[NetHack|Game-NetHack]] — cli アダプタの標準例、テキスト観測
- [[soren game|Game-Sorengame]] — browser アダプタ、本番 soren との関係
- [[アーキテクチャ|Architecture]] — 設計ダイジェスト (基本方針・アダプタ契約・プロセスモデル・共存原則)
- [[トラブルシューティング|Troubleshooting]] — 症状から原因・対処を逆引き

一次情報:
[README.md](https://github.com/azumag/docich/blob/main/README.md) /
[docs/architecture.md](https://github.com/azumag/docich/blob/main/docs/architecture.md) /
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
