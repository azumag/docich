# 引き継ぎ: 半熟英雄ペンディング → 共通部品化 (2026-08-16)

半熟英雄系の作業を**ユーザー判断で一旦ペンディング**し、次は**共通部品化**
(multi_repo_plan.md の C2 以降) を進める。この文書は、その作業を引き継ぐセッション
(または将来の自分) が、現在地・凍結状態・最初の一歩を迷わず把握するためのもの。

- 【確認済】= 一次情報 (実クローン・実コミット・実測) で裏取りした事実
- 実測日はすべて 2026-08-16

---

## 1. 現在地スナップショット

| 項目 | 状態 |
|---|---|
| 作業ブランチ | `claude/docich-game-switching-lsom1e` = docich main (`e261c31`) + Phase 2/3 実装一式。push 済み。**PR は未作成** (ユーザー指示があるまで作らない) |
| テスト | stdlib unittest **371 件全緑**。`scripts/smoke_cli.sh` / `scripts/smoke_brain.sh` とも PASS |
| submodule | `games/soviet_now` = `dcb2992` (本日 bump 済み) / `games/hanjuku-sfc-speedrun` = `5e98294` |
| 監視 | 毎時 Routine が soviet_now main と docich main を監視 (multi_repo_plan.md §4 のプロトコル)。変化がなければ沈黙 |
| wiki | `wiki/` 原稿は最新。GitHub への発行はユーザーが `scripts/publish_wiki.sh` を実行した時点で反映 |

## 2. 半熟英雄の凍結状態 (ペンディング)

**実装・検証は完了済み**で、安全に凍結されている (`[agent] enabled = false` が既定のため、
マージ・起動しても本番には何も出ない)。

- 完了: brain 本体 (`brains/hanjuku/brain.py`)、設計書 (`docs/hanjuku_brain.md`)、
  E2E スモーク、実 LLM (claude-opus-5) での 1 サイクル動作確認 (RetroArch メニューを画像認識し
  fail-soft 判断)。watchdog / rotate / systemd 雛形 (Phase 3) も実装済み。
- 再開時にやること (すべて VM 側・ユーザー作業が起点):
  1. ROM を `games/roms/hanjuku-hero.sfc` に配置
  2. VM の `claude` CLI 認証確認 (`claude -p "ping"`)
  3. `config/games/hanjuku-hero.toml` の `[agent] enabled = true` → 実プレイ検証
- 再開の入口: `docs/hanjuku_brain.md` §6、wiki の 半熟英雄ページ。将来改善の種:
  卵落ち CSV の retrieval 注入 (`docs/hanjuku_brain.md` §3)、`ra-cmd SAVE_STATE` を絡めた復帰運用。

## 3. 共通部品化ミッション (これからやること)

方針の原典は multi_repo_plan.md §3「**移動より先に参照**」。docich からサブモジュール内の
スクリプトを参照実行する形で始め、ゲーム非依存が実証できた部品だけ docich へ昇格させる。
soviet_now への変更 (ラッパ化等) は **Codex の作業完了とユーザー合意の後** (C4)。

### 3.1 前提条件の現状 【確認済・実測】

C2 の前提「broadcast/ 系の変更が落ち着くこと」は**まだ成立していない**。部品領域ごとの
churn は大きく異なる:

| 領域 | 直近の変更 | 判定 |
|---|---|---|
| TTS (`broadcast/say_enqueue.sh`, `google_tts.sh`, `coeiroink_tts.sh`) | **6週間変更なし** | 安定。着手可 |
| オーバーレイ (`generate_*_overlay*`, `dashboard_data.py` — リポジトリ直下) | 6週間で3件 | ほぼ安定 |
| コメント応答 (`broadcast/comment*.sh`, chat 系) | **本日も変更あり** (英語コメント分類、rate-limit backoff 等) | 活発。設計のみ可 |
| ラジオ (`broadcast/radio_*`) | 直近3週間で複数 (deferred queue、caption 同期等) | 活発。設計のみ可 |
| 字幕 (soviet_now 側 `lib/closed_captions.py`) | 改善2件が入った (下記 3.2) | **drift 対応が必要** |

### 3.2 最初の実務タスク: 字幕 drift の同期

字幕は既に docich へ昇格済みの共通部品 (`src/docich/captions.py` が正典) だが、Codex が
soviet_now 側の互換実装に改善を入れた:

- `a9fcc4c0a` fix: remove closed caption chunk count cap
- `9fb403afe` fix: keep partial caption translations

これは handoff.md「Remaining gates」#2 (互換コピーの手動同期) が**実際に発火した状態**。
次セッションの1歩目はこの同期評価が最適:
`games/soviet_now/lib/closed_captions.py` と docich `src/docich/captions.py` を diff し、
2コミットの意味 (チャンク数上限の撤廃・部分翻訳の維持) を docich 正典に取り込むか判断
→ 取り込むならテスト (`tests/test_captions.py`) ごと移植。

### 3.3 その後の進め方 (提案順)

1. **TTS の共通部品化設計** (C4 先頭 tts/ の前倒し設計)。say_enqueue 系の依存
   (VOICEVOX/GOOGLE/COEIROINK の環境変数・キュー・再生経路) を洗い出し、docich 側の口
   (例: `docich say` または adapters と並ぶ `tts` モジュール) を設計 → 参照実行の PoC。
   6週間安定しており、churn との衝突リスクが最小。
2. **C2 のインターフェース設計** (`docich chat <game>` / `docich radio <game>` 相当)。
   設計自体は churn と独立に進められる。**実装着手は broadcast/ の落ち着きを毎時監視
   Routine で確認してから** (目安: コメント/ラジオ系ファイルが2週間程度無変更)。
3. **C3 (オーバーレイ)** は C2 と同時期に判断 (ffmpeg drawtext フック + `generate_*` 生成物の接続)。
4. **C4 (昇格・soviet_now 側のラッパ化)** はユーザー合意ゲート。

### 3.4 進め方の作法

- 部品の inventory (ゲーム非依存度 × churn × 依存環境変数の分類表) を最初に作ると
  以後の判断が速い。soviet_now は読むだけ (書かない)。
- 設計文書は `docs/` に (例: `docs/common_parts_tts.md`)。実装は sonnet サブエージェントへ
  委任し、ファイル集合を分けて並行させる (今回の Phase 2/3 と同じやり方)。
- docich 本体は stdlib-only を維持。参照実行する shell 部品はサブモジュール相対パス
  `games/soviet_now/...` で呼ぶ (multi_repo_plan.md §1.1)。

## 4. 変わらない原則 (このプロジェクトの前提)

1. **役割分担**: 計画・設計・レビュー = このセッション (Fable)。実装 = sonnet サブエージェント。
   git 操作 (commit/push/merge) はセッション側で行う。
2. **soren 共存**: 本番 sorengame (soviet_now, :99) を壊さない。docich は :98 /
   `docich_sink` / `stream.mode="null"` 既定 / set-default-sink しない (architecture.md §0)。
3. **soviet_now は読み取り専用**: 改善は Codex が実施中。docich は main を監視して後追いする。
4. **ROM ポリシー**: 自己吸い出し品のみ。リポジトリにコミットしない。入手には関与しない。
5. **ブランチ規律**: `claude/docich-game-switching-lsom1e` で開発し `git push -u origin`。
   PR 作成はユーザー指示時のみ。ブランチの PR がマージ済みになったら、最新 main から
   同名ブランチを作り直して続きを積む。

## 5. 参照文書

| 文書 | 内容 |
|---|---|
| `docs/architecture.md` | 設計の一次情報 (共存原則 §0、アダプタ契約 §3、フェーズ状況 §10) |
| `docs/multi_repo_plan.md` | サブモジュール構成・C0〜C4 ロードマップ・監視プロトコル |
| `docs/hanjuku_brain.md` | 半熟英雄 brain の設計と検証状況 (再開時の正典) |
| `handoff.md` (リポジトリ直下) | Codex 側の運用引き継ぎ (Soren 本番の正体・残ゲート)。**Codex が所有** |
| `docs/twitch_closed_captions.md` | 字幕アーキテクチャ (drift 同期の背景知識) |
| `wiki/` | 入り口・運用ハンドブック (発行は `scripts/publish_wiki.sh`) |
