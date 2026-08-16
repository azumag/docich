# マルチリポジトリ構成計画 — docich を親、ゲーム実装をサブモジュールに

docich を「配信の基盤 (とりまとめ)」とし、各ゲームに固有の実装は**別リポジトリ (git submodule)** に置く構成の設計書。
共通部品化の対象と手順、および **soviet_now で並走中の Codex 作業との協調ルール**を定める。

> **現在の作業引き継ぎ**: `docs/handoff_common_parts.md` (2026-08-16。半熟英雄ペンディング → 共通部品化フェーズの現在地と最初の一歩)

## 表記ルール

- 【確認済】: 一次情報 (各リポジトリの実クローン・実コミット) で裏取りした事実
- 【要検証】: 実機・実運用での検証が必要な事項

---

## 1. 構成

```
docich/                            # 親: 配信基盤 (このリポジトリ)
├── src/docich/                    #   ゲーム非依存: アダプタ/配信/エージェント/CLI
├── config/games/*.toml            #   ゲーム定義 (サブモジュール内の実装を参照する)
├── games/
│   ├── roms/                      #   ROM 置き場 (gitignore)
│   ├── soviet_now/                # submodule → azumag/soviet_now (main 追跡)
│   └── hanjuku-sfc-speedrun/      # submodule → azumag/hanjuku-sfc-speedrun (main 追跡)
└── docs/ wiki/ scripts/ ...
```

| リポジトリ | 役割 |
|---|---|
| **docich** (親) | 配信基盤: ディスプレイ/音声/ffmpeg 配信、ゲーム切替、アダプタ契約、エージェントループ、運用 CLI。**ゲーム非依存の共通部品の置き場** (§3) |
| **soviet_now** | sorengame (ソ連ゲー) 本体: Unity WebGL 自動プレイ (soviet_local.mjs / strategy.py / soren91)、および現状は配信・チャット・ラジオ等の一式も同居 (§3 で段階的に共通部品化) |
| **hanjuku-sfc-speedrun** | 半熟英雄の RTA チャート・ゲーム機構データ (卵落ち判定・切り札ダメージ等) と分析ツール。【確認済】Phase 2 で作る半熟英雄 brain の**知識ベース** (プロンプトの grounding 素材) |

### 1.1 clone / 更新の作法

```bash
git clone --recurse-submodules https://github.com/azumag/docich.git
# 既存 clone に後から:
git submodule update --init

# サブモジュールを最新 main に進める (docich 側の明示的な操作でのみ行う):
git submodule update --remote games/soviet_now
git add games/soviet_now && git commit -m "Bump soviet_now to <sha> (<理由>)"
```

- **サブモジュールのポインタ更新は「意図を持った commit」**として docich に記録する (自動追従はしない。§4 の監視が更新の契機を検知し、内容を確認してから進める)。
- docich のセットアップ/CI/スクリプトがサブモジュール内を参照するときは `games/<name>/...` の相対パスを使う。

### 1.2 ゲーム定義との接続

- `config/games/sorengame.toml`: **viewer 専用定義** (`http://127.0.0.1:8080` のローカル WebGL viewer を表示するのみ)。本番運転 (start_all.sh 等) は browser アダプタ契約の対象外で、soviet_now の `soren-runtime.service` が所有する (【確認済】docich main PR #3 / handoff.md で確定)。本番運転の docich への移管は将来の別 cutover として扱う (§5)。
- `config/games/hanjuku-hero.toml`: Phase 2 の brain (`[agent] command`) は `games/hanjuku-sfc-speedrun/` のチャート・データを読み込む実装をサブモジュール側 (または docich 側 brain スクリプト + サブモジュール参照) に置く

---

## 2. soviet_now の現状把握 (2026-08-14 時点)

【確認済】main 先端 = `8b210a5` "Merge pull request #97 from azumag/codex/soren-ffmpeg-direct" (2026-08-12)。
**Codex が OBS → ffmpeg 直結配信への移行を進行中** (`direct_stream.sh` / `cutover_direct_stream.sh` /
`configure_linux_stream_profile.sh` / `install_direct_stream_relay.sh` / soak・recovery テスト群)。
docich の設計 (ffmpeg 直結) と同一方向であり、共通部品化の合流先として好条件。

共通部品候補の所在 (【確認済】実クローンのファイル配置):

| 領域 | soviet_now 内の実体 |
|---|---|
| コメント応答 | `broadcast/comment.sh`, `broadcast/comment_lib.sh`, `twitch_chat*.sh`, `youtube_chat.sh`, `viewer_chat_monitor.sh` |
| ラジオ | `broadcast/radio_engine.sh` ほか radio_* 一式 (persona/news/corners/factcheck/quality/state/themes/celebration), `fetch_radio_grounding.py`, `fetch_news.sh` |
| 配信レイアウト (オーバーレイ) | `generate_status_overlay.sh`, `generate_event_overlay.py`, `generate_improve_overlay.sh`, `generate_dashboard.sh`, `dashboard_data.py` ほか |
| 音声 (TTS/再生) | `say_enqueue.sh`, `google_tts.sh`, `coeiroink_tts.sh`, VOICEVOX 経路 |
| 配信本体 | `direct_stream.sh` 系 (Codex 移行中) |
| ゲーム固有 (共通化しない) | `soviet_local.mjs`, `strategy*.py`, `analyze_*.py`, `soren91/`, eloop 系 |

---

## 3. 共通部品化ロードマップ

**原則: 「移動」より先に「参照」。** docich からサブモジュール内のスクリプトを呼ぶ形で共通利用を始め、
ゲーム非依存であることが実証できた部品だけを docich 側へ昇格させる。昇格時は soviet_now 側を薄い
ラッパ (docich 呼び出し) に置換して二重管理を避ける — これは soviet_now 側の変更を伴うため
**Codex の作業完了とユーザー合意の後**に行う。

| 段階 | 内容 | 前提 |
|---|---|---|
| C0 (完了) | サブモジュール組み込み・main 監視の開始 | 本書 |
| C1 (完了) | 配信経路の役割整理: 【確認済】Soren 本番は soviet_now の FFmpeg direct (`soren-runtime.service`, custom FFmpeg + ネイティブ字幕) が所有。docich `stream.py` は :98 側の汎用基盤として分離維持。字幕要求時のみ `docichcc + libx264 a53cc` を追加し、能力が無ければ fail-open (docich main PR #3 / handoff.md) | Codex の移行完了 (2026-08-15 確認) |
| C-caption (完了・前倒し) | **字幕が最初の共通部品として docich に昇格済み**: `src/docich/captions.py` (バイリンガル plan + Unix socket IPC) + `native/ffmpeg/` (docichcc フィルタ・pinned build)。soviet_now 側はプロトコル v1 検証で対になる (PR #101-103)。残課題: soviet_now 側の互換コピーとの同期 (handoff.md「Remaining gates」#2) | Codex 実装 (docich PR #3) |
| C2 | コメント応答・ラジオを docich から**参照実行**するアダプタ非依存の口を設計 (`docich chat` / `docich radio` 相当。ゲーム名を渡すだけで動く形) | broadcast/ 系の変更が落ち着くこと |
| C3 | オーバーレイ: docich の ffmpeg drawtext フック + `generate_*_overlay` の生成物を接続 | C2 と同時期に判断 |
| C4 | 実証済み部品の docich への昇格 (tts/ → chat/ → radio/ の順を想定)、soviet_now 側のラッパ化 | C2/C3 + ユーザー合意 |

---

## 4. 監視 — 「改善が入ったら動く」の仕組み

**soviet_now は現在 Codex が改善中のため、docich 側からは一切変更しない** (読み取り専用)。
毎時の Routine (Claude セッションへの定期トリガ) で以下を確認し、変化があったときだけ行動する:

1. **soviet_now main**: `git ls-remote` の main sha が docich のサブモジュールポインタと異なるか。
   - 異なれば: 新規コミットを `git log/diff` で確認 → 変更内容の要約と docich への影響 (配信経路・共通部品候補の変化) を評価 → 問題なければサブモジュールポインタを進める commit を作成 → C1 以降の段階に進める状態かを判断してユーザーへ報告。
2. **docich main**: origin/main が作業ブランチの基点より進んでいるか。
   - 進んでいれば: 変更内容を確認し、作業ブランチ (`claude/docich-game-switching-lsom1e`) を最新 main に追従 (再作成 or rebase) して継続作業の基点を保つ。
3. どちらも変化がなければ**何もしない** (報告もしない)。

---

## 5. 未決事項 (次の判断ポイント)

1. ~~配信経路の一本化~~ → **解決済み** (C1)。Soren 本番 = soviet_now 所有、docich = :98 の汎用基盤 + 再利用可能な字幕部品の正典、という分担で確定 (handoff.md)。
2. **sorengame 本番の docich への移管**: 将来の別 cutover。game 専用の Soren エントリポイント (start_all.sh 非依存) とロールバック証明が前提 (handoff.md「Remaining gates」#4)。それまで docich の sorengame 定義は viewer 専用。
3. ~~半熟英雄 brain の置き場~~ → **解決済み** (Phase 2 着手時に決定)。brain 本体は docich 側 `brains/hanjuku/` に置き、`games/hanjuku-sfc-speedrun/charts/`・`data/` を読み取り専用の知識ベースとして参照する (【確認済】卵落ちテーブル・キャラデータ・話数別チャートが存在)。理由と設計は `docs/hanjuku_brain.md` §0。
4. **docichcc フィルタの二重管理解消**: docich の `native/ffmpeg/` と soviet_now 側互換コピーを、バージョン付きアーティファクト依存に置き換えるまで手動同期 (handoff.md「Remaining gates」#2)。
