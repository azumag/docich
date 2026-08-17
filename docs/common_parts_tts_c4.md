# TTS 共通部品化 C4 — 責務分割と昇格設計 (2026-08-17)

本稿は `docs/common_parts_tts.md` §5 の「C4 昇格時に責務分割を先に設計する」を受けた
設計である。**ユーザー合意済み** (2026-08-17、handoff §3.7-5 の合意ゲートを通過)。

> **ステータス**: S1 (合成) の docich 正典を実装済み (2026-08-17)。
> `src/docich/speech.py` + CLI `docich voicevox synth|speakers`。

## 1. 責務分割 (現状の一体 → 5 責務)

`games/soviet_now/say_enqueue.sh` (2,572 行) が一体で持つ責務を分割する。

| # | 責務 | 現状 | C4 での扱い | 正典の置き場 |
|---|---|---|---|---|
| S1 | 合成 (TTS クライアント) | `voicevox_tts.sh` / `google_tts.sh` / `coeiroink_tts.sh` | **docich へ昇格** (本 PoC で実装) | `src/docich/speech.py` (VOICEVOX) |
| S2 | キュー (FIFO 排他) | `say_enqueue.sh` の mkdir ロック | 参照実行のまま (docich は追加キューを作らない) | `say_enqueue.sh` 所有 |
| S3 | 再生 | `say_enqueue.sh` 内の paplay/afplay 等 | 参照実行のまま。docich からは `--dry-run` / `--render-only` のみ | `say_enqueue.sh` 所有 |
| S4 | 字幕 | `src/docich/captions.py` (正典) + `lib/closed_captions.{sh,py}` | **既に昇格済み** | `src/docich/captions.py` |
| S5 | 話者運用 | `tmp/voicevox_voice.txt` / `.voice` sidecar / `SAY_VOICEVOX_SPEAKER_OVERRIDE` | docich 側は環境変数と引数で選択。sidecar 運用は soviet_now 側のまま | docich config / env |

## 2. 昇格方針

### 2.1 今回実装するもの (S1)

- `src/docich/speech.py`: VOICEVOX HTTP API の純 Python クライアント (stdlib-only)。
  `voicevox_tts.sh` の契約をそのまま移植する。
  - URL フェイルオーバー (`PRIMARY` → `URL` → `FALLBACK` → `LOCAL`、ヘルスチェック
    `GET /speakers`)
  - チャンク分割 (句点・読点・改行、`VOICEVOX_MAX_CHARS` 既定 200)
  - 読み替え辞書 (`config/voicevox_word_replace.txt`、TAB 区切り、行頭 `#` コメント)
  - `#` / `＃` 除去、`audio_query` (ピッチ/テンポ/抑揚適用)、`synthesis`
  - Python `wave` モジュールによる WAV 結合
- CLI: `docich voicevox synth -f <text> -o out.wav [--dry-run]` /
  `docich voicevox speakers` (実装済み)

### 2.2 今回実装しないもの (S2/S3)

- キューと再生は本番で広く使われている `say_enqueue.sh` のまま。docich は
  `docich say` (参照実行) で契約を固定する (common_parts_tts.md §4)。
- 合成だけを docich 正典にし、`docich say --render-only` と組み合わせられるよう
  `--engine` は追加しない (二重管理を避ける)。docich 正典の WAV 出力は
  `say_enqueue.sh --wav-playlist` 等の入力にそのまま使える。

### 2.3 soviet_now 側のラッパ化 (将来、別 PR)

- soviet_now 側は `voicevox_tts.sh` を薄いブリッジ (docich `voicevox` 呼び出し) に
  置き換える。これは **soviet_now リポジトリへの変更と外部 push を伴うため、
  Codex の作業完了を確認してから別途 PR で行う**。docich 側の push も
  ユーザー承認後に実施する。

## 3. 安全条件 (S1)

1. 合成は `-o <wav>` を必須とし、既定で再生しない (S3 は参照実行側に委ねる)
2. `--dry-run` は合成せず argv/env を表示
3. 外部コマンドを呼ばない (stdlib のみ)。シェル結合なし
4. 読み替え辞書はサブモジュール内の `config/voicevox_word_replace.txt` のみを対象にし、
   任意パスを渡さない
5. `speakers` は読み取りのみ (合成しない)

## 4. 検証計画

- unit tests: チャンク分割・読み替え・URL フェイルオーバー・audio_query 適用・WAV 結合を
  モック HTTP で検証
- 実施済み (2026-08-17): `tests/test_speech.py` 16 件 + 全体 443 件全緑、
  `docich voicevox synth --dry-run` 確認
- 実機検証は VOICEVOX が起動している環境 (VM 等) で `docich voicevox synth` を実行し、
  出力 WAV のヘッダ/サイズを確認する。初回は本番キューに触れない別 checkout で行う
- **実機合成は完了 (2026-08-17)**: ローカル VOICEVOX (127.0.0.1:50021, v0.25.2) で
  `docich voicevox speakers` (話者一覧) と `docich voicevox synth` (267,308 bytes WAV,
  RIFF ヘッダ確認) を実行して成功。VM とは独立したローカル合成で本番に触れていない。
