# bench — Jev ローカルLLM計測ハーネス (#1263)

RTX 3060 12GB 実機で「Jev のコメント分類をローカルLLMへ置き換えられるか」を
**同一入力・同一生成パラメータ**で比較するための共通基盤。このディレクトリの
成果物は2つ。

1. `jev_eval_v1/` — Jev 実運用ログ由来の評価セット（`docich.eval.case.v1`）
2. `jev_bench.py` — 全モデル共通の計測ハーネス（生ログ + 集計 CSV を出力）

実機 GPU 上での本番計測そのものは **担当外**（別タスク）。ここが用意するのは
再現可能な入力と計測手順。

## 1. 評価セット `bench/jev_eval_v1/`

| ファイル | 内容 |
| --- | --- |
| `manifest.json` | `docich.eval.suite.v1`（suite=`jev-live-v1`, seed=33, 60/20/20） |
| `public_cases.jsonl` | 106 件（train 62 / validation 21 / sealed_test 23 に group 単位で分割） |
| `critical_cases.jsonl` | 2 件（境界ケースの常時 hard-gate bucket） |
| `rubric.json` | ラベル定義（`docich.comment_classifier.jev.CRITERIA` をそのまま再利用） |

### 件数・出典

全 108 件。`tags` の `origin:*` で出典が分かる。

| 出典 | 件数 | 説明 |
| --- | --- | --- |
| `live-log` | 78 | Soren 本番の `tmp/debug/ai_dispatch/*_COMMENT_*_prompt.txt` から抽出 |
| `curated-829` | 16 | soviet_now PR-0（#829）の手書き分類 fixture を移植 |
| `synthetic-coverage` | 14 | ログ窓に存在しなかったラベル（subscription / stream_goal / bits）と境界ケースを補完 |

`live-log` の参照ラベルは **本番分類器が実際に選んだカテゴリ**（keyless heuristic、
または Jev が置換した行）。つまり「現行Jevの出力」を再現した参照解であり、
人手で作り直した正解ではない。この点は次の「既知の限界」に効く。

### カテゴリ分布

| category | 件数 |
| --- | --- |
| card_gacha | 12 |
| raid | 2 |
| subscription | 2 |
| stream_goal | 2 |
| bits | 2 |
| sing_request | 2 |
| game_question | 3 |
| game_status | 15 |
| general_question | 6 |
| strategy_advice | 4 |
| comment_advice | 3 |
| stream_bug_report | 7 |
| chitchat | 29 |
| other | 19 |

intent_family 別では chitchat 29 / game 22 / notification 20 / other 19 /
stream_ops 7 / question 6 / advice 5。コメント本文は 4〜477 文字（中央値 36）で、
短文・曖昧文・誤字・英語混在・通知文を含む。

件数は **層化サンプリング**による。実ログ窓は card_gacha（カード獲得通知）と
chitchat が大量で、そのままでは希少な意図ラベルが埋もれるため、ラベルごとに
上限（`build_jev_suite.py` の `CAPS`）を掛けてから均等に抽出している。

### 匿名化・投影（Git に出す前の加工）

`live-log` の行は production の投影規則を通してある。

- モデル入力は production と同じく **コメント本文のみ**（`heuristic.split_line`）。
  カード獲得通知は通知本文を保持し、batchの投稿者prefixを除去する。
  既存suiteは再生成していない。新規投影を架空の投稿者/受取人で検証する。
- `@handle` は ASCII・非 ASCII を問わず `[user]` に置換（`build_jev_suite.redact_handles`）。
  `docich.eval.corpus.sanitize_text` は ASCII の `@[A-Za-z0-9_]` しか消さないため、
  日本語表示名が残らないよう自前で補っている。
- URL は `[url]`、secret / private path / private IP は `sanitize_text` と
  `contracts.assert_public_safe` が拒否する。**全ケースが `validate_case` と
  `assert_public_safe` を通過しない限りビルドは失敗する。**
- 500 文字超（contract 上限）の行は切り詰めずに除外。

コミットしてよいのはこの**投影済み・レビュー済み**のセットだけ。生ログの
`user: comment` 行そのものは Git に置かない（`docs/operations/comment-eval.md` §2）。
`bench/tools/extract_live_logs.py` は読み取り専用で stdout に出すだけ。

### 既知の限界

- 参照ラベルは現行分類器の出力なので、分類器自身の誤りや、システム告知行
  （`tags` に `system-announcement` を持つ 17 件）のラベルゆれを含む。
  ハーネスは `all` / `non_notification` / `live_log` の3サブセット精度を併記する。
- `synthetic-coverage` の 14 件は CRITERIA 定義に沿って作成した合成ケースで、
  実ログではない（`tags` に `synthetic`）。
- 実ログ窓（2026-10-06〜08 の返信生成プロンプト）には subscription /
  stream_goal / bits が無かったため、同ラベルは合成ケースのみで測る。

## 2. 再生成手順

```sh
# 1) Soren のログから生ペアを抽出（読み取り専用。VM などログを読めるホストで）
python3 bench/tools/extract_live_logs.py /home/ubuntu/soren > /tmp/jev_live_pairs.jsonl

# 2) 層化・投影・検証して suite を書き出す（生ペアは Git に置かない）
python3 bench/tools/build_jev_suite.py --input /tmp/jev_live_pairs.jsonl --out bench/jev_eval_v1
```

`build_jev_suite.py` は `docich.eval.contracts` / `docich.eval.corpus` を import するので
`src/` が見える checkout で実行する（後述の `PYTHONPATH=src` は不要、スクリプトが解決する）。

## 3. 計測ハーネス `bench/jev_bench.py`

### 全モデル共通の契約

- **同じプロンプト列**: system 指示＋ラベル定義＋出力契約は
  `docich.comment_classifier.jev.CRITERIA` から組み立てる。モデルごとに変えない。
- **同じ生成パラメータ**: `temperature` / `top_p` / `max_tokens` / `seed` を
  CLI で固定（既定 0.0 / 1.0 / 64 / 42）。全モデルで同一値を渡す。
- **モデル差は chat template だけ**: 既定 `--prompt-format chat` は
  OpenAI 互換サーバに messages を渡し、サーバ側がモデルの template を適用する。
  生 completion 系のランタイムでは `--prompt-format template --chat-template FILE`
  で `{{ system }}` / `{{ user }}` を差し替える。差し替わるのはテンプレートだけで、
  プロンプト本文・パラメータは共通のまま。

### 記録項目

- **精度**: `docich.eval.graders.classifier` を再利用（accuracy / coverage /
  macro F1 / ラベル別 precision・recall / confusion / game-intent recall）。
  `all` に加えて `non_notification` と `live_log` も出力。
- **レイテンシ**: 1 プロンプトごとに TTFT（最初のストリーム片までの時間）、
  総生成時間、tokens/sec。実行全体の p50 / p95 / p99 も出力。
- **VRAM**: `nvidia-smi` を background thread でポーリングし、ピーク使用量
  （MiB）を記録。Mac など `nvidia-smi` が無い環境では `None`（`--vram-source none`）。
- **失敗**: JSON parse failure 件数、backend error 件数、`finish_reason`、
  `usage`、生成本文を生ログに保持。

### 出力（1 実行 = 1 ディレクトリ）

```
bench/runs/<YYYYmmdd-HHMMSS>_<model>/<label>/
  config.json    実行条件（モデル・量子化・パラメータ・suite digest・開始時刻）
  report.json    精度/レイテンシ/VRAM の集計（サブセット別 + ラベル別）
  raw.jsonl      1 プロンプトごとの生ログ（生成本文・usage・TTFT・総時間）
  outputs.jsonl  再生用出力（case_id + category。docich.eval の jsonl candidate 形式）
  per_case.csv   1 プロンプト 1 行
  summary.csv    集計 CSV（モデル別比較の主出力）
```

### コマンド例

```sh
# ダミーモデルでの end-to-end スモーク（GPU・ネットワーク不要）
python3 bench/jev_bench.py --suite bench/jev_eval_v1 \
    --backend dummy --model dummy-1b --quantization none --out bench/runs

# llama.cpp / Ollama / vLLM / LM Studio（OpenAI 互換）
python3 bench/jev_bench.py --suite bench/jev_eval_v1 \
    --backend openai --base-url http://127.0.0.1:8080/v1 \
    --model Qwen2.5-7B-Instruct-Q4_K_M --quantization Q4_K_M \
    --temperature 0 --top-p 1.0 --max-tokens 64 --seed 42 \
    --warmup 3 --runs 3 --out bench/runs

# 既存の合成 suite でもそのまま動く
python3 bench/jev_bench.py --suite evals/comment/v1 --backend dummy --model dummy-1b
```

`--warmup N` は各 run の先頭 N 件を集計から除外する。`--runs N` で繰り返し、
`summary.csv` の各行が 1 run に対応する（同一モデルの複数 run を median/分散比較に使う）。

### native Ollama の公開結果とオフライン再集計

`bench/tools/run_ollama_bench.py` の公開 `summary.csv` はモデルごとに1行を出す。
`cases_per_run` は warmup 除外後の1 run の件数、`pooled_n` は全 runs の試行数。
accuracy / macro F1 の `*_median` は run 別の中央値であり、report の `all` は
全試行を採点した pooled 集計となる。`accuracy` は応答がある試行の正答率、
`correct_fraction_all` はmissを含む全試行の正答率で、coverageを併記する。同じ `case_id` の各試行を独立に採点し、
confusion と label support の合計を pooled の母数に揃える。

保存した raw から report・summary・metadata のみを再生成するには:

```sh
python3 bench/tools/reaggregate_ollama_results.py bench/results/2026-10-09_rtx3060_jev
env -u PYTHONPATH python3 -m unittest discover -s bench/tests -v
```

再生成処理は suite digest を照合し、API・GPU・VM へ接続せず、raw を読み取るだけ。
公開 report の suite は repo 相対パス（外部 suite は digest による識別子）、run_dir は
`results:<結果ID>/<モデルID>` とする。ローカル run の report は元の場所情報を保持する。
公開 metadata の command は相対出力先と `<ollama-base-url>` を用いる再現用テンプレート。

2026-10-09 の保存 raw は各モデル324行、warmup 除外後105件 × 3 runs = 315試行。
訂正後の Swallow pooled accuracy は157/315 = 0.4984126984、macro F1 は0.6576904727。
run 別 median は accuracy 0.4952380952 / macro F1 0.6555766816 のまま。
Llama の通知ラベル FP は6/315 = 約1.90件/100件で、旧0.6件/100件を訂正した。
この再集計でモデル採用・shadow 運用の判断は変更していない。

native stream は `done:true` を受信するまで成功とせず、EOF・JSON途中終了は失敗となる。
`eval_count` 欠落時は `output_tokens`・tokens/sec を unknown (`null`) とし、
文字数による推定を実測列へ混ぜない。保存済み5本の raw にある実測 usage は変更しない。
`--extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'` で
ランタイム固有オプションを全リクエストへ一律に付与できる。

## 4. 依存関係・GPU 前提

- Python 3.9+ / 標準ライブラリのみ（追加依存なし）。`src/` の
  `docich.eval`・`docich.comment_classifier` を import する。
- 実機計測の前提: RTX 3060 12GB、`nvidia-smi` が使えること、推論ランタイム
  （llama.cpp `llama-server` / Ollama / vLLM 等）が OpenAI 互換 API を
  `http://127.0.0.1:8080/v1` 等で提供していること。
- モデルは 3〜8B 級 Q4/Q5（`Q4_K_M` 等）を想定。VRAM 見積もりと選定は別タスク。

## 5. テスト

```sh
env -u PYTHONPATH python3 -m pytest -q bench/tests/test_jev_bench.py
```

suite の contract 適合・匿名化漏れ無し・プロンプト組み立て・回答パース・
ダミー end-to-end（CSV 出力）・OpenAI ストリーミング（ローカル SSE モックで
TTFT/tokens の計測）を検証する。GPU も実モデルも不要。

## 6. 受け入れ確認（このスイートでの実測）

```
$ python3 bench/jev_bench.py --suite bench/jev_eval_v1 --backend dummy \
      --model dummy-1b --quantization none --seed 42 --runs 2 --warmup 2
run_dir=bench/runs/<stamp>_dummy-1b/default
cases=212 runs=2 model=dummy-1b quant=none backend=dummy
accuracy=0.6226 macro_f1=0.5155 coverage=1.0 parse_failures=0
ttft_p50_ms=43.0 total_p95_ms=124.90 tokens_per_second=104.3 peak_vram_mib=None
summary_csv=bench/runs/<stamp>_dummy-1b/default/summary.csv
```

ダミーモデルで end-to-end に走り `summary.csv` が出ることを確認済み。suite は
共有コーパス基盤 `PYTHONPATH=src python3 -m docich.eval build --suite bench/jev_eval_v1`
でもそのまま読める（train 62 / validation 21 / sealed_test 23 / critical 2）。

## 担当外

- RTX 3060 実機での本番計測・結果の考察・採用モデル決定は別タスクが受け持つ
  （カード `t_93c8260c` = 実機計測、`t_827803e3` = 結果分析・採否判断）。
  ここはその入力と計測手順までを整備する。

## 継承レビュー修正の検証範囲

OpenAI backendはCLIの4生成設定を実リクエストへ渡し、extra-bodyでの上書きを拒否する。
chat/templateの双方でstreamを要求し、JSON応答もContent-Typeで読み取る。
errorイベント・終端無しEOFは失敗として採点し、usage欠落時の実token数と速度はnull。
dummyのseedもCLI値を使う。`report.json`は全試行とrun別の集計を持つ。

保存native結果の再集計はsuite digestに加え、run/caseの件数・順序、warmup、gold、tagsを照合する。
rawのSHA-256と方法をmetadataに記録し、保存run別VRAMをpooled peakで補完しない。
CSVにもmissを含む `correct_fraction_all`（native集計では `correct_fraction_all_median`）を併記する。
公開の計算結果は演算後に小数12桁へ揃え、Python 3.11/3.14で同一ファイルを再現する。
歴史的なrawにはstream終端イベントが無いため、修正後の完了判定を遡及適用できない。
保存smokeの要求contextから実効contextを補完しない。OpenAI経由の過去のCLI記録だけでは、
旧版が送信しなかった生成設定の適用を証明できない。外部再計測は行っていない。

これらは#1974側のコード・保存証拠の訂正であり、#1933/#1934自体のブランチ・レビュー条件を
完了した扱いにはしない。main mergeには既存のVM自動配備経路があるため、mergeせずReadyで保留する。
