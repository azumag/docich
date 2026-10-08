# RTX 3060 実機向けローカルLLM の選定と実行環境 (#1263)

Jev 分類器のローカルLLM化（[#1263](https://github.com/azumag/docich/issues/1263)）の
前段として、RTX 3060 12GB 実機で動かす **対象モデル 5 個**と **実行環境**を確定した。
本番の精度・レイテンシ比較は後段タスクが `bench/jev_bench.py`（PR #1933）で行う。
ここが用意するのは「どのモデルを、どう入れて、いくら VRAM を食うか」と、
全モデルが実際に **1 応答を返す**ことの実測記録。

- 実測日: 2026-10-09
- 実測ホスト: `desktop-9j2it17`（Windows 実機, RTX 3060 12GB, Tailscale 経由）
- 推論ランタイム: **Ollama 0.30.10**
- 生ログ: `bench/results/2026-10-09_rtx3060_smoke.json`
- 起動確認スクリプト: `bench/tools/smoke_ollama.py`

## 1. 候補モデルと選定

選定基準（#1263 の方針 + Jev 固有条件）:

1. **日本語性能** — 対象は日本語の口語・誤字混じりコメント。
2. **ライセンス** — 本番置換候補なので再配布・業務利用の条件が明確なもの。
3. **入手容易性** — Ollama レジストリから `ollama pull` 一発で入る（実機の常駐運用に載る）。
4. **既存 Jev パイプラインとの互換** — OpenAI 互換 API で叩け、`{"choice","confidence"}`
   の JSON 契約を安定して返せる（`docich.comment_classifier.jev.CRITERIA` のラベル）。
5. **モデルサイズ** — 3〜8B 級・Q4/Q5、12GB VRAM に KV キャッシュ込みで全載せできる。

### 候補（8 個）

ディスクサイズは Ollama レジストリのマニフェスト実値（実機 `/api/tags` と一致）。

| # | モデル | パラメータ | 量子化 | ディスク | ライセンス | 選定 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `qwen2.5:7b-instruct-q4_K_M` | 7.6B | Q4_K_M | 4.68 GB | Apache-2.0 | **✔** |
| 2 | `qwen3:8b-q4_K_M` | 8.2B | Q4_K_M | 5.23 GB | Apache-2.0 | **✔** |
| 3 | `llama3.1:8b-instruct-q4_K_M` | 8.0B | Q4_K_M | 4.92 GB | Llama 3.1 Community | **✔** |
| 4 | `schroneko/llama-3.1-swallow-8b-instruct-v0.1:q4_k_m` | 8.0B | Q4_K_M | 4.92 GB | Llama 3.1 Community | **✔** |
| 5 | `MHKetbi/sbintuitions-sarashina2.2-3b-instruct-v0.1:q4_K_S` | 3.4B | Q4_K_S | 1.97 GB | Apache-2.0 | **✔** |
| 6 | `mistral:7b-instruct-v0.3-q4_K_M` | 7B | Q4_K_M | 4.37 GB | Apache-2.0 | ✘（次点） |
| 7 | `gemma3:4b-it-q4_K_M` | 4B | Q4_K_M | 3.34 GB | Gemma Terms of Use | ✘ |
| 8 | `llama3.2:3b-instruct-q4_K_M` | 3B | Q4_K_M | 2.02 GB | Llama 3.2 Community | ✘ |

ディスクサイズは Ollama レジストリのマニフェスト実値。パラメータ数は選定 5 モデルが実機
`/api/tags` の実測値、落選 3 モデルはモデルカードの公称値。

### 選定理由（5 個）

- **`qwen2.5:7b-instruct-q4_K_M`** — 7B 帯の日本語性能と JSON 追従の実績。非 thinking の
  素直な指示追従で、#1263 が名指しする比較基準。
- **`qwen3:8b-q4_K_M`** — Qwen 系の後継。同サイズで精度上限を狙う枠（thinking を切る運用が前提、
  §4）。7B 帯との世代差を測る。
- **`llama3.1:8b-instruct-q4_K_M`** — Llama 系のベースライン。Swallow の比較対照（同一
  トークナイザ基盤）として必須。
- **`schroneko/llama-3.1-swallow-8b-instruct-v0.1:q4_k_m`** — Llama-3.1-8B に日本語の
  継続事前学習 + 指示チューニングを当てた国産モデル。**日本語性能を重視**する #1263 の
  要求に直接対応する唯一の選定枠。
- **`MHKetbi/sbintuitions-sarashina2.2-3b-instruct-v0.1:q4_K_S`** — SB Intuitions の日本語
  特化 3B。軽量・低レイテンシ枠で、VRAM/速度の下限を押さえる。

### 落選理由

- **mistral:7b-instruct-v0.3** — 日本語（特に口語・誤字）の扱いが Qwen/Llama-3.1 系より弱く、
  7B 枠は qwen2.5 が代表。同一ファミリの重複を避けるため次点に退けた（枠が空けば追加候補）。
- **gemma3:4b-it** — 4B 枠は日本語特化の sarashina を優先。Gemma 系は実機に既存の
  `gemma4:12b`（§5）で家族傾向を確認できる。
- **llama3.2:3b-instruct** — 3B 枠は同サイズで日本語特化の sarashina を優先。Llama 系は
  8B の 2 本で押さえる。

## 2. 実行環境

| 項目 | 値 |
| --- | --- |
| ホスト | `desktop-9j2it17`（Windows 実機、Tailscale 経由で HTTP 到達） |
| GPU | NVIDIA GeForce RTX 3060 12GB（Ampere） |
| 推論ランタイム | Ollama **0.30.10**（内蔵 llama.cpp バックエンド、GGUF） |
| API | `http://<host>:11434`（native `/api/*` と OpenAI 互換 `/v1`） |
| ドライバ / CUDA | **未取得**（下記） |
| 実機に既存のモデル | `gemma4:12b`（11.9B / Q4_K_M / VRAM 8.09GB 常駐） |

### ランタイム決定: Ollama（llama.cpp バックエンド）

- 実機に導入済みで常駐でき、**OpenAI 互換 `/v1/chat/completions`** を出すため
  `bench/jev_bench.py --backend openai --base-url http://<host>:11434/v1` から直接叩ける。
- GGUF の Q4/Q5 と KV キャッシュ量子化を扱え、モデル差し替えが `ollama pull` で完結する。
- **vLLM 不採用** — Windows 実機へ CUDA ツールチェーンを持ち込む必要があり、SSH が閉じた
  現状（下記）ではセットアップと再現手順の維持が現実的でない。Jev は数十トークンの分類で
  連続バッチの利得も小さい。
- **llama.cpp 単体不採用** — Ollama と同じバックエンドを内包するため、モデル管理・常駐・
  API 層を自前で持つ理由がない。将来 vLLM/別ホストへ移す場合も `bench/jev_bench.py` は
  `--base-url` 差し替えで対応できる。

### ドライバ / CUDA バージョンが未取得な理由と代替証拠

`nvidia-smi` は実機で実行する必要があるが、**Windows 側の SSH:22 が閉じている**ため
この経路では取得できない（既知のブロッカー: `handoff.md` 2026-09-27 節「Windows の SSH:22 が
閉じている。ユーザーに OpenSSH 有効化と鍵登録・ユーザー名を依頼中」）。代わりに得られた
GPU 実行の証拠は次。

- Ollama `/api/ps` の `size_vram` が `size` と一致（**100% GPU オフロード**、CPU フォールバックなし）。
- 12GB 級を超える重み（`gemma4:12b` = 8.09GB VRAM 常駐、8B Q4 = 5.3〜9.8GB）が全載せできている
  → 8GB 超の VRAM があることまでは実測で確認できる。

次の一手（ユーザー作業）: Windows で OpenSSH を有効化 + 公開鍵登録。
入り次第 `nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv` と
Ollama が使う CUDA ランタイム版を本ファイルへ追記する。

## 3. 量子化バリアントと VRAM 実測

VRAM は Ollama `/api/ps` の `size_vram` 実測（重み + KV キャッシュ + compute buffer、
プロセス全体の合計）。`num_ctx` は Ollama 側で**モデルの最大長にクランプ**されるため、
上限が 8192 のモデルは 16k/32k 指定でも値が伸びない。

| モデル | 量子化 | 最大 ctx | ディスク | VRAM @4k | VRAM @16k | VRAM @32k | 全載せ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `MHKetbi/…-sarashina2.2-3b-…:q4_K_S` | Q4_K_S | 8,192 | 1.97 GB | 2.67 GB | 3.80 GB | 3.80 GB | 100% |
| `qwen2.5:7b-instruct-q4_K_M` | Q4_K_M | 32,768 | 4.68 GB | 4.75 GB | 5.62 GB | 6.59 GB | 100% |
| `qwen3:8b-q4_K_M` | Q4_K_M | 40,960 | 5.23 GB | 5.58 GB | 7.52 GB | 9.84 GB | 100% |
| `llama3.1:8b-instruct-q4_K_M` | Q4_K_M | 131,072 | 4.92 GB | 5.27 GB | 7.02 GB | 9.06 GB | 100% |
| `schroneko/…-swallow-8b-…:q4_k_m` | Q4_K_M | 8,192 | 4.92 GB | 5.27 GB | 5.93 GB | 5.93 GB | 100% |

見積もりの内訳は `VRAM ≒ 重み + KVキャッシュ(ctx に線形) + compute buffer`。
実測では KV が支配的なのは 8B で、4k→32k で **+3.5〜4.3GB**。3B は同じ 8k 上限でも 3.8GB に収まる。

### 12GB への収まりと縮小設定

- **全 5 モデル × 全 ctx で 12GB に収まり、CPU へ一切こぼれない**（offload 100%）。
- ただし 8B の 32k は 9.1〜9.8GB で、配信 PC が OBS/VRChat を同時に使う前提では残り ~2GB
  しかない。**Jev 用途は `num_ctx` 8192〜16384 を推奨**（32k は単独運用時の検証枠）。
- 元コメントは 4〜477 文字（中央値 36）で、分類プロンプトも短い。8k でも運用上十分な余裕がある。
- `schroneko/…swallow` と `MHKetbi/…sarashina2.2` はモデル上限が 8192 のため、
  **ハーネス側で `num_ctx` を明示しないと VRAM 比較が揃わない**（Ollama の既定 ctx に依存）。

## 4. 起動確認（smoke test）

全 5 選定モデル × ctx {4096, 16384, 32768} で **1 プロンプト**を流し、起動と応答を確認した。

```sh
python3 bench/tools/smoke_ollama.py \
    --base-url http://<host>:11434 \
    --model qwen2.5:7b-instruct-q4_K_M --model qwen3:8b-q4_K_M \
    --model llama3.1:8b-instruct-q4_K_M \
    --model schroneko/llama-3.1-swallow-8b-instruct-v0.1:q4_k_m \
    --model MHKetbi/sbintuitions-sarashina2.2-3b-instruct-v0.1:q4_K_S \
    --num-ctx 4096 --num-ctx 16384 --num-ctx 32768 \
    --out bench/results
```

固定条件: 同一プロンプト、`temperature=0 / top_p=1.0 / num_predict=128 / seed=42`、
warmup 1 回（初回ロードを計測から除外）。モデル差は chat template と（Qwen3 のみ）
`think:false` に限る。

| モデル | TTFT(warm) | 生成 | 応答（JSON 契約） |
| --- | --- | --- | --- |
| `MHKetbi/…-sarashina2.2-3b-…:q4_K_S` | 116 ms | 107 tok/s | `{"choice":"card_gacha","confidence":1.0}` |
| `qwen2.5:7b-instruct-q4_K_M` | 216 ms | 66 tok/s | `{"choice":"card_gacha","confidence":1}` |
| `qwen3:8b-q4_K_M` | 209 ms | 61 tok/s | `{"choice":"card_gacha","confidence":0.95}` |
| `llama3.1:8b-instruct-q4_K_M` | 258 ms | 61 tok/s | `{"choice":"stream_goal","confidence":0.8}` |
| `schroneko/…-swallow-8b-…:q4_k_m` | 307 ms | 59 tok/s | `{"choice":"card_gacha","confidence":0.9}` |

**全 5 モデルが実機で 1 応答を返した**（受入条件）。生成速度は 3B 107 tok/s / 8B 59〜66 tok/s、
8B の warm TTFT は 209〜307 ms。#1263 の暫定目標（warm p95 300ms 程度）は境界域で、
実際の判定は同一プロンプト・全 108 ケースのハーネス計測で行う。

観測メモ（後段タスクへの申し送り）:

- **Qwen3 は thinking を切らないと応答本文が空になる。** `num_predict` を思考トークンで
  使い切るため、Ollama の `think:false` を渡さないと `message.content` が空のまま返る
  （実測: 128 トークン全て思考、content 空）。`bench/jev_bench.py` も同じ扱いが必要。
- この 1 プロンプト（`つぎカードつかって、5れんしょう狙おう`）は本来 `strategy_advice` 相当だが、
  唯一 Llama-3.1 だけ `stream_goal` を返した。精度の良否は件数を持って判断する（後段）。
- TTFT はプロンプト長に比例する。本番の Jev プロンプト（ラベル定義込み）はこの smoke プロンプト
  より長いため、実測 TTFT は上表より伸びる。

## 5. 補足: DiffusionGemma 26B-A4B

#1263 の方針どおり本命候補にはしない（Ampere は NVFP4 ネイティブでない、26B の全重みを
12GB に置けない）。実機には `gemma4:12b` Q4_K_M（8.09GB 常駐、32k ctx）が既に入っており、
必要なら CPU/MoE オフロード時の参考値として使える。ハーネスは `--model` 差し替えだけで
これも計測できる。

## 6. 残件

- 精度 / F1 / 誤分類一覧 / p50-p99 latency / 負荷の本計測（後段タスク、`bench/jev_bench.py`）。
- ドライバ / CUDA バージョンの実機記録（Windows OpenSSH 有効化待ち、§2）。
- `num_ctx` をハーネス側で固定して VRAM 比較を揃える（§3）。
