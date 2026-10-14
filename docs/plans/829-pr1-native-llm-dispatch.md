# #829 docich PR-1 — native LLM dispatch

2026-09-22、#829 §14-3 の実装。目的は `docich ai` と docich 内の
PAPER/コーナー改善から、`games/soviet_now` の `eloop_lib.sh` /
`lib/ai_generate.sh` を実行せず、ゲーム非依存の型付きディスパッチへ切り替える
ことである。コメント・ラジオの intake、prompt、分類、delivery は後続PRの責務で
あり、このPRだけで `docich chat` / `docich radio` のnative化や本番有効化を主張しない。

## 実装範囲

- `src/docich/llm/` に、agent契約、入力policy、provider adapter、queue lock、
  provider lock、rate-limit/failure backoff、secret-free telemetry、ordered
  fallback dispatcherを追加。
- `src/docich/ai_generate.py` を native backendへ切り替え。旧CLIのゲーム名は
  互換引数として受け取るが参照せず、promptはメモリ上のtyped requestで渡す。
- Codex / OpenCode系（opencode、opencode-go、OpenRouter、Vercel、AMD）/
  local OpenAI-compatible endpointをallowlist化。MiniMaxは退役済みとして拒否。
- `corner_improve.py` と `trading/ai_text.py` の生成入口を同じnative dispatchへ
  統一。real AI gate (`DOCICH_ALLOW_REAL_AI=1`) は維持する。
- `docich ai` のdry-runはproviderを起動せず、label・agent・解決model・backend
  のみを表示する。prompt本文、出力、stderr、credentialはtelemetry/sidecarへ
  書かない。

## 互換性・安全境界

| 旧契約 | native PR-1 |
|---|---|
| `COMMENT` timeout 90秒 | 同じ既定値 |
| `RADIO` timeout 240秒 | 同じ既定値 |
| Vercel非空allowlist時のみ有効 | `VERCEL_FREE_AGENTS` が空ならskip |
| rc=79の明示rate limitのみ長いbackoff | `AI_BACKOFF_SEC_ITEMS` / COMMENT・RADIO既定を使用 |
| 通常provider失敗は短いstreak backoff | `AI_BACKOFF_FAILURE_SEC` と上限を使用 |
| COMMENT lane / RADIO lane | 同じlane lock、無効化envも尊重 |
| queue give-up rc=92 / improve gate rc=91 | 固定failure kindとしてsidecarへ出力 |
| OpenCode transient failure retry | `OPENCODE_ABORT_RETRY` を尊重し、timeout/429は再試行しない |

provider adapterは固定argvまたは固定HTTP schemaだけを使い、shell評価を行わない。
生成結果はreasoning block、provider error、空出力、1 MiB超を拒否する。失敗理由は
allowlist enumへ丸め、自由な例外本文やpromptを状態・metricsへ保存しない。

## 検証

- native input policy、fallback、validator、backoff、sidecar、telemetry非漏えいを
  `tests/test_ai_generate.py` でmock検証。
- PAPER/コーナー改善呼び出し元と既存broadcast baselineを回帰検証。
- `compileall`、focused pytest、後続CIで全体pytestを実行する。
- 実API、実チャット、実ラジオ、VM配備、worker再起動はこのPRのテストでは行わない。
  マージ後のVM反映はowner-only workflow、status/diagnostics、必要な実機確認を
  別のrelease gateとして扱う。

## 後続作業

PR-2でsemantic decision / #882 transport、PR-3でnative comment coreを追加する。
`docich chat` / `docich radio` のlegacy依存を削除するまで、native LLM dispatchを
もってIssue #829全体の完了とはしない。
