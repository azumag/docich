# PR merge audio report

main へ pull request がマージされたとき、その変更内容を専門用語を避けた日本語1文にして
VM の audio-worker へ読み上げさせる owner-only workflow です。

## 対象とゲート

- `.github/workflows/pr-merge-audio-report.yml` は protected `main` への `push` だけで起動します。
- owner（`azumag` / actor ID `9018513`）、`github.triggering_actor == azumag`、
  `refs/heads/main`、`github.ref_protected == true` のすべてを要求します。それ以外は
  job ごと skip します。
- checkout は `main` の trusted copy だけを使い、pull request のコードは workflow 内で
  実行しません。`environment: vm-operations` の secrets は VM gateway への固定 script
  実行だけに使います。
- `ops/vm_actions/authorize_pr_merge_audio.py` が workflow 文脈（repository / owner /
  protected main / workflow path / event name）と push payload（`ref`、
  `before`/`after`、`forced`/`deleted`、`head_commit`）を完全一致で検証します。

## 読み上げ内容

次の優先順位で1文を作ります。

1. PR本文の `## 音声サマリー`（owner が書いた平易な1文）
2. owner設定のOpenAI互換APIによる自動要約（PRタイトル＋本文の概要）
3. PR本文の `## 概要` / `## 目的` / `## 変更内容` の最初の段落
4. クリーニングした PR タイトル（最終 fallback）

### 音声サマリー（任意）

PR本文に次のセクションを置くと、その内容を最優先で使います。AI は呼びません。

    ## 音声サマリー

    お金がたまったまま新しい兵士を募集できない問題を直しました。

文は `プルリクエストNで、` の後に続く形で書いてください。実際の読み上げは
`プルリクエスト4691で、お金がたまったまま新しい兵士を募集できない問題を直しました。`
になります。空・記号のみ・長すぎる場合は次の経路へ fallback します。

### AI要約（任意・owner設定）

- 既定では無効です。owner が次のリポジトリ変数と secret を設定したときだけ、OpenAI 互換の
  chat completions エンドポイントを1回呼びます。
  - `PR_MERGE_AUDIO_LLM_ENDPOINT`（例: `https://.../v1/chat/completions`、HTTPS のみ）
  - `PR_MERGE_AUDIO_LLM_MODEL`
  - `PR_MERGE_AUDIO_LLM_API_KEY`（secret。`vm-operations` environment にだけ置く）
- endpoint・model・key のどれかが空、または endpoint が HTTPS でなければ AI は呼ばず、
  `音声サマリー` → 本文概要 → タイトルで動作します。secret を削除すれば無効化できます。
- 入力は PR タイトルと `概要` / `目的` / `変更内容` の先頭段落（合計4KB以内）だけです。
  コードブロック・表・URL・HTML・チェックリストは送信前に除去し、トークン様文字列は
  `[redacted]` に置換します。
- `temperature: 0`、`max_tokens: 300`、HTTPタイムアウト20秒。リトライはせず、失敗は
  即 fallback します。料金・rate limit は owner が選んだ provider に依存します。
- 出力は日本語1文（40〜120字程度、200字上限）。Markdown・URL・制御/不可視文字・英語のみの
  文は拒否し、長すぎる場合は200字に丸めます。
- AI は GitHub Actions ランナー上で実行します。VM gateway の排他ロックを保持しないため、
  deploy や他の operator を待たせません。
- 一時停止はリポジトリ変数 `PR_MERGE_AUDIO_AI=off`。停止しても
  `音声サマリー` → 本文概要 → タイトルの順で動作します。
- GitHub Models は 2026-07-30 に retired のため使用しません。エンドポイントは owner が
  明示的に設定します。

### 文面

- 基本形は `プルリクエスト<番号>で、<要約>。` です。
- 最終文は240字以内・1行・制御/不可視文字なし。長い場合は `…` で丸めます。
- タイトル最終 fallback だけは現行の
  `mainにプルリクエスト<N>「<タイトル>」がマージされました。` を維持します。

## 出力と失敗時の扱い

- workflow の出力は `found`、`text_b64`、`source`（`override` / `ai` / `body` /
  `title`）、`reason`（固定 enum）、`chars` だけです。本文・プロンプト・AI応答は
  Actions log / stdout / stderr に出しません。
- AI の 401/403/404/429、timeout、空応答、不正JSON、日本語なしは
  fallback し、workflow は成功します。run log には `ai_http_429`、`ai_timeout`、
  `ai_invalid_response`、`ai_invalid_text`、`ai_empty` などの固定 reason だけが残ります。
- merged pull request が無い push（direct push など）は VM に接続せず success で skip します。
- audio_worker 不在・enqueue 失敗は現行どおり workflow 失敗です（AI とは独立）。
- 再実行すると AI を再呼び出しします（`temperature: 0` でも文面が変わり得ます）。
  enqueue 成功後の再実行は二重に読まれ得ます（重複抑制TTLは0）。

## VM 前提と失敗時の扱い

- `status docich production <current-main>` が `configured` で、production baseline が
  current main の reviewed ancestor（または、deploy が先行した場合は current main を
  ancestor に持つ reviewed descendant）であることを `check_operator_parity.py` で確認します。
  `--allow-newer-production` はこの workflow 専用の opt-in で、どちらの向きも
  `PARITY_DEPTH`（50 commit）以内に限定します。drift / recovery_required / 範囲外は
  fail-closed です。
- 同じ push は deploy workflow も起動します。deploy が gateway の排他ロックを保持している間、
  `status` は `gateway_busy` で即時拒否されるため、この固定理由だけを最大 18 回
  （前半 5 秒・後半 15 秒の上限付きバックオフ）再試行します。drift・認可エラー・
  gateway 不在など他の拒否は再試行しません。
- 連続 merge の待機中 run が捨てられないよう、workflow に共有 concurrency group を置かず、
  各 push が独立して実行されます。enqueue の実行は VM gateway の排他ロックで直列化されます。

## セキュリティ・プライバシー

- 送信するのは公開 PR のタイトルと概要の先頭段落だけです。差分・全文・コメントは送りません。
- トークン様文字列は送信前に redact し、プロンプト・応答・トークンはログに出しません。
- AI の API キーは repository secret（`PR_MERGE_AUDIO_LLM_API_KEY`）だけで管理し、
  workflow・コード・ログへ書かない。endpoint は HTTPS のみを受け付けます。
- `PR_MERGE_AUDIO_LLM_ENDPOINT` は owner だけが変更できる信頼済みの URL に限定してください。
  任意の URL を設定すると、そのホストへ API キーが送信されます。
- PR本文はデータとして `<change>` で区切り、固定指示と出力検証で prompt injection の
  影響を限定します。merge できるのは owner だけです。
- AIはランナー上で実行し、VM の AI runtime / lane / registry は変更しません。

## 検証

- `python3 -m pytest -q ops/vm_actions/tests/test_pr_merge_audio_report.py ops/vm_actions/tests/test_pr_merge_audio_report_script.py ops/vm_actions/tests/test_pr_merge_audio_summary.py`
- `python3 -m unittest ops.vm_actions.tests.test_pr_merge_audio_report ops.vm_actions.tests.test_pr_merge_audio_report_script ops.vm_actions.tests.test_pr_merge_audio_summary -v`
- workflow の policy は `test_pr_merge_audio_report.py` の `WorkflowPolicyTests` が検証します。
- AI の成功/429/timeout/不正応答/英語のみ/redaction は
  `test_pr_merge_audio_summary.py` が fake HTTP で検証します（CI は外部AIを呼びません）。
- 実際の読み上げは VM の audio_worker が稼働している必要があります。workflow run の成功は
  queue へ公開できたことまでで、再生完了・配信ミックスでの可聴は別途確認が必要です。
- merge 後の初回 run では `source=ai`（または fallback の `reason`）と
  `exec exit_code=0` を確認します。
