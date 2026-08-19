# soren game (Unity WebGL)

soren game は docich の `browser` アダプタで動かす。**soren 本体 (AI 戦略・自動プレイ・配信
システム) の Linux 移植そのものは docich の管轄ではない**(移植計画は下記関連ドキュメント)。
docich が担うのは「ブラウザを起動し、その画面を Xvfb 上に映して ffmpeg で配信する」という
表示の場の提供のみである。

## 本番 soren との関係 (重要)

**この VM では soren (sorengame) の本番システムが既に稼働している** (docich とは別に、
system `soren-runtime.service` として、ディスプレイ `:99`・`soren_null` PulseAudio sink で
FFmpeg 直接配信 + ネイティブ Twitch 字幕を運用中)。docich の `browser`
アダプタは、docich 自身のディスプレイ `:98` 上に同じゲームを表示する**別インスタンス**で
あり、稼働中の本番配信とは完全に独立している。`bin/docich start sorengame` などの docich
側の操作が、本番 soren のプロセス・配信に影響することはない。

## サブモジュール: games/soviet_now

sorengame 本体の実装 (Unity WebGL 自動プレイ、`soviet_local.mjs` / `strategy.py` / `soren91`
等) は git submodule `games/soviet_now` (`azumag/soviet_now`, main 追跡) として取り込まれて
いる。

`games/soviet_now` では Codex による OBS → ffmpeg 直結配信への移行が完了し、本番は
`soren-runtime.service` として FFmpeg 直接配信 + ネイティブ Twitch 字幕で稼働している。
**Codex による改善は引き続き進行中のため、docich 側からは読み取り専用として扱い、変更を
加えない。** main の毎時監視 (Routine) で新規コミット
の有無を確認し、変化があれば内容を評価したうえでサブモジュールポインタを進める運用に
なっている。現状把握・共通部品化ロードマップ (C0〜C4) の詳細は
[docs/multi_repo_plan.md](https://github.com/azumag/docich/blob/main/docs/multi_repo_plan.md)
§2・§4 を参照。

## 運転形態: viewer 専用 (現状)

**docich の `sorengame` 定義は viewer 専用である。** `browser` アダプタが chromium を
kiosk 起動し、同一 VM の Soren 本番が公開するローカル WebGL viewer
(`http://127.0.0.1:8080`) を表示して画面を docich の配信 (`:98`) に乗せるだけで、
`[agent] enabled = false` に固定されている。

```bash
bin/docich up
bin/docich start sorengame
```

本番運転 (Playwright/CDP による自動プレイ・戦略 AI・配信) は `start_all.sh` を含めて
soviet_now が所有し、`soren-runtime.service` として `:99` で稼働し続ける。`start_all.sh`
は browser アダプタ契約の対象外であり、docich はこれを呼ばない。

本番運転を docich へ移管することは**将来の別 cutover**として扱う (game 専用の Soren
エントリポイントとロールバック証明が前提。詳細: `handoff.md`「Remaining gates」、
`docs/multi_repo_plan.md` §5)。将来の cutover 後は `[browser] launch_command` をその
game 専用エントリポイントに差し替える想定だが、現時点では未達で、上記の viewer 専用構成の
ままである。

## 設定の要点

`config/games/sorengame.toml` の既定 `url` は `http://127.0.0.1:8080`
(同一 VM の Soren 本番が公開するローカル WebGL viewer) で、差し替えなしにそのまま
viewer 専用として動く。`binary = "auto"` は `chromium` /
`chromium-browser` / `google-chrome` / playwright の chrome を順に自動検出する。
`scripts/setup_ubuntu_arm.sh` は既定では chromium を導入しないため、別途用意が必要
(選択肢: snap 版 chromium の導入、または soren の Playwright chromium
(`~/.cache/ms-playwright/`) を `[browser] binary` に指定して共用する)。

## 本番 AI モデルフォールバックチェーン (参考情報、2026-08-19 時点)

soviet_now 側の実装は上記の通り読み取り専用として扱うが、本番 VM 上の AI ディスパッチ
(ラジオ生成・コメント応答) がどのモデルを順に試すかは、運用上よく参照するため参考情報として
ここに記す。実体は `soren-litellm` (VM 上の litellm プロキシ、port 4100) と soviet_now
`.env` の `RADIO_AGENTS` / `COMMENT_AGENTS` であり、いずれも docich 側からは変更しない
soviet_now 管轄の設定である。

| チェーン | 現在の順序 |
|---|---|
| `RADIO_AGENTS` | `local` → `codex:deepseek-v4-flash-free` → `codex:openrouter/free` → `codex:amd-token-factory-deepseek-v4-flash` → `codex:deepseek-v4-flash` → `codex:minimax-m3` |
| `COMMENT_AGENTS` | `local` → `codex:deepseek-v4-flash-free` → `codex:openrouter/free` → `codex:amd-token-factory-deepseek-v4-flash` → `codex:deepseek-v4-flash` → `codex:minimax-m3` |

- `codex:openrouter/free` は OpenRouter 公式の「無料モデルをランダムに選ぶ」ルーター
  (`openrouter/free`)。`codex:amd-token-factory-deepseek-v4-flash` は AMD Token Factory
  経由の DeepSeek V4 Flash。どちらも無料/クォータ制の枠のため、上位が失敗した場合のみ
  実際に呼ばれる。
- `MODEL_IMPROVE` (戦略改善ループ) は `codex:deepseek-v4-flash` 固定で
  `MODEL_FALLBACK_IMPROVE=disabled`（意図的にフォールバック無し）であり、上記チェーンとは
  無関係。
- 各モデルの実接続先・API キー参照は VM `/home/ubuntu/litellm.yaml` /
  `/home/ubuntu/.config/soren-litellm.env`（git 管理外、秘密鍵につきここには書かない）。
  健全性は `curl http://127.0.0.1:4100/health`（VM 上 or SSH 経由）で確認できる。
- 詳細な変更履歴・検証ログは docich `handoff.md` §14/§15 を参照。この表はスナップショットで
  あり、soviet_now 側の運用変更で随時ズレうる（正は常に VM の `.env`/`litellm.yaml`）。
- **実データ確認済み（2026-08-19 18:32 JST）**: 設定変更なしの通常運用中に実際の
  `[RADIO:news]` コーナー生成で `codex:amd-token-factory-deepseek-v4-flash` が勝者になり、
  1298字のコンテンツが配信キューに投入された（`local`/`deepseek-v4-flash-free`/
  `openrouter/free` は先に失敗/スキップされた上での到達とみられる）。RADIO_AGENTS の
  amd-token-factory までの到達・成功を実ログで確認済み。

## AI モデルラダー (改善ループ / ラジオ pre-pass、参考情報)

上記の RADIO_AGENTS/COMMENT_AGENTS とは別に、改善ループ (`eloop_improve.sh` の
ANALYZE/IMPLEMENT/FIX/REVIEW) とラジオ pre-pass (事前調査) にもモデルフォールバックが
ある。実体は VM `soren/core/config.sh`（`eloop_lib.sh` が読むのはこちらで、リポジトリ内に
同名で存在する古い `config.sh` ではない点に注意）:

```text
MODEL_IMPROVE_LIST (既定) = codex:deepseek-v4-flash-free → codex:openrouter/free →
                             codex:deepseek-v4-flash → codex:minimax-m3
RADIO_PREPASS_AGENTS (既定) = 同上 (local を除いた同じ4段)
```

- 2026-08-19 時点でどちらにも `amd-token-factory` は**含めていない**（今回のユーザー依頼は
  RADIO_AGENTS/COMMENT_AGENTS への追加のみ。この2つへ追加するかは別途要判断）
- **解決済み（2026-08-19 追記）**: 上の「未解決の疑問」は誤りだった。`soren/radio_engine.sh`
  （リポジトリ直下）と `soren/broadcast/radio_engine.sh` の**2ファイルが同時に存在**しており、
  `eloop_lib.sh` が実際に `source` するのは **`broadcast/radio_engine.sh`** の方
  （直下の同名ファイルは古いコピーで未使用）。前回はうっかり直下の古い方を読んでいた。
  `broadcast/radio_engine.sh` は当日 15:52 JST（本セッションとは別の並行作業）に更新されており、
  そちらでは `radio_prepass_agent`（単数、`RADIO_MAIN_PREPASS_AGENT` 由来）は
  「pre-pass を実行するかどうかの on/off 判定」にしか使われておらず、実際のモデル選択は
  `radio_prepass_agents`（複数形、`RADIO_PREPASS_AGENTS` 由来、上記の4段ラダー）を
  `ai_generate_list` に渡す形で行っている。ログにも `prepass agents=...` と
  `prepass provider=...` が出る。`ai_generate.sh`/`config.sh` も同様に直下とサブディレクトリ
  (`lib/`, `core/`) に重複があり、`eloop_lib.sh` の `source` 行が常に正（`lib/ai_generate.sh`,
  `core/config.sh`）。この手の「直下 vs サブディレクトリの重複ファイル」は soviet_now の
  リファクタ移行途上の産物と見られ、今後もこのリポジトリを読むときは `eloop_lib.sh` の
  `source` 行で実体を確認してから読むこと
- `MODEL_IMPROVE_LIST` は improve_daemon.log の `[ANALYZE(1)] primary OK` 等のログで
  改善ループ自体が稼働していることは確認したが、ログには `primary` としか出ず、実際に
  どのモデル名で成功したかまでは today's 実測では確認していない

## 関連ドキュメント

- [docs/games/sorengame.md](https://github.com/azumag/docich/blob/main/docs/games/sorengame.md) — 本ページの詳細版
- [docs/soren_linux_migration_plan.md](https://github.com/azumag/docich/blob/main/docs/soren_linux_migration_plan.md) — soren 本体の Linux 移植計画・リスク
- [docs/oracle_arm_setup_guide.md](https://github.com/azumag/docich/blob/main/docs/oracle_arm_setup_guide.md) — 移植先 VM (Oracle Ampere A1) の構築手順
- [[アーキテクチャ|Architecture]] §4.3 相当 (`docs/architecture.md`) — docich 側のアダプタ設計・共存原則
