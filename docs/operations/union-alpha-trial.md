# Union Alpha の明示チェーン設定

## 目的と正本

Union Alpha は期間に応じて実行時に注入せず、設定のモデルチェーンに明示する。
設定上の順序は次のとおり。追加後も既存候補の相対順序を維持する。

1. `opencode-go:union-alpha`（CLIモデルID: `opencode-go/union-alpha`）
2. `openrouter:stealth/union-alpha`（CLIモデルID: `openrouter/stealth/union-alpha`）
3. 既存チェーン

**2026-09-24の自動停止は行わない。** 旧実装の固定期間
（2026-09-17 13:00 UTC〜2026-09-24 13:00 UTC）による注入・期限判定を廃止する。
再起動、日付の経過、待機越しによって候補を隠れて追加・削除しない。
利用を止める場合は、正規の設定変更・配備手順でチェーンから外す。

## 対象と維持する契約

- Sorenの共通生成・改善・ピーク優先順位などのチェーン既定値は
  `games/soviet_now/core/config.sh` を正本とする。用途別の明示設定は尊重し、
  Union Alphaを含まないカスタムチェーンへ実行時に強制追加しない。
- docichのPAPER台本・改善は `config/docich.soren-live.toml` の
  `paper_corner.script_agents` / `improve_agents` に2経路を明示する。
  `src/docich/ai_generate.py` → Soren `ai_generate_list` はその指定を渡す。
- 空のretro改善やmarket PAPERの継承・停止設定、各enable値は変更しない。
  明示チェーンの追加を理由に、停止中コーナーやAIジョブを有効化しない。
- 単一`AGENT`は単一IDのまま維持し、CSVを詰め込まない。NetHackの評価済み
  command manifest、半熟英雄の単一backend、自由戦略の単一text-only HTTP
  backend、self-repairのroot-owned単一モデルpolicyも書き換えない。
  ツール権限・canary承認・sandboxは維持する。
- Web UIの `DEFAULTS` にある `AI_COMMON_AGENTS`、`MODEL_IMPROVE_LIST`、
  `PEAK_HOURS_AGENT_PREFERENCE` をSorenの既定値と同期する。
  UIが表示する設定チェーンと実行側の設定チェーンを一致させ、期間限定の
  隠れたprefixを持たせない。空欄の継承や明示カスタム値は保持する。
  DEFAULTSは未上書き時の値（Vercel A→B既定値を展開）であり、稼働workerの
  環境や実際のwinnerを証明する表示ではない。ピーク並べ替え・backoffは別途適用される。

## 翻訳の試行上限

`COMMENT_TRANSLATION_MAX_ATTEMPTS=4` は **Union Alpha 2経路 + 既存2候補**までの
上限であり、既存チェーン全長の試行を保証しない。先行候補が成功すればそこで終了する。
従来の上限2より遅延が増えるのは先行候補が失敗した場合のみ。用途別に上限を変える場合は
Web UIで正整数を明示する（空欄は既定4）。カスタムチェーンにも同じ上限が適用される。

## 提供終了・失敗時

2経路の失敗・backoffは独立して扱い、通常のfallback/backoffで既存候補へ進む。
提供終了やモデル未検出を理由とした特別な期限停止は設けない。
ただし **rc=79は既存のrate-limit契約であり、モデルnot foundを必ず検出・分類する
保証ではない**。プロバイダー/CLIの実際の失敗分類とreturn codeに従う。
認証エラー、モデル未検出、一過性失敗をすべて79と断定しない。
既存telemetryとsanitized diagnosticsで結果を確認し、候補の除去は明示設定で行う。

## 事前確認（本番反映の証拠ではない）

2026-09-17、ローカルOpenCode `1.18.27` のカタログで両モデルIDを確認。
Go版のカタログ単価はinput/output/cacheとも0、OpenRouterの公開models APIは
prompt/completionとも0。料金・提供継続を将来まで保証するものではない。

空の一時ディレクトリ、全ツールdeny、1 step、共有無効、外部plugin無効で
各モデルに固定の短い応答を要求した疎通確認では、両方ともrc=0・期待した応答を得た。
認証は既存CLIに任せ、資格情報を読み出したりコピーしたりしていない。
本番VMのプロバイダー設定・利用可能性は別途確認が必要。

## 配備と完了条件

**本変更の開発・PR作成では本番操作を行わない。**
VMの実効チェーン、worker再起動、提供終了時の実機fallbackは別途検証が必要。
VMバナー・音声も本タスクの権限外のため未操作。以下は承認後に行う手順であり、実施記録ではない。

通常の [owner-only control plane](../../ops/vm_actions/README.md) を使用する。
Soren PR → review/tests/CI → Soren main → docich gitlink PR →
review/tests/CI → docich main → canonical deploy の順。
[Production deployment contract](../../AGENTS.md#9-production-deployment-contractdocich-control-plane)
に従い、手動コピーやtracked file直接編集で代用しない。

### 明示済み `.env` の更新（承認後のみ）

VMでは `AI_COMMON_AGENTS` / `RADIO_AGENTS` / `RADIO_PREPASS_AGENTS` /
`COMMENT_AGENTS` / `COMMENT_TRANSLATION_AGENTS` / `PEAK_HOURS_AGENT_PREFERENCE`
が明示設定済みという引き継ぎがある。配備前に現行状態を再確認する。
**既定値変更だけではこれらを上書きしない。** 特にピーク優先順位を旧値のままにすると、
ピーク時の並べ替えでUnion Alphaが優先を失う。

1. 許可されたWeb UIの `GET /api/config` で対象キーの `value` / `in_env` /
   `effective` と `env_mtime` を取得する。allowlist外の `.env` 内容や資格情報は
   読み出し・出力しない。用途別の既存候補順序・休止設定を保存する。
2. 変更前バックアップを、承認済みの設定バックアップ機構で確保する。APIも更新時に
   `.env.bak.<ns>` をmode 0600で作るが、失敗しても更新を継続する実装なので、
   API成功だけでバックアップ成功とみなさない。復元可能性を確認できなければ停止する。
   バックアップの内容をGit・PR・Actions出力へコピーしない。
3. 各対象チェーンの既存候補を保持し、Union Alphaの2経路を重複なく先頭へ置いた
   完全な値を作る。`PEAK_HOURS_AGENT_PREFERENCE` も同じ2経路を先頭へ追加する。
   `PUT /api/config` に `{"values": {対象キー: 完全な値}, "expected_mtime": 取得した整数,
   "confirm": true}` を渡す（これは形の説明であり、そのまま送信するJSONではない）。
   `expected_mtime` の代わりに同じ整数の `If-Match` を指定してもよい。
   既存UIの認証・CSRF保護を使い、認証情報やHTTP headerを記録しない。
   409なら再GETして並行更新を照合し直す。競合検査を外して上書きしない。
4. 補助chainの `RADIO_JIJI_RESEARCH_AGENTS` / `RADIO_FACT_CHECK_AGENTS` /
   `COMMENT_CLASSIFIER_AGENTS` / `COMMENT_CLASSIFIER_EDIT_AGENTS` も確認する。
   キー欠落時はconfig既定のUnion Alpha付きchain、明示空文字時は従来の単一モデル親を
   順に継承する。UIの空欄保存はこの明示空文字を保持する。単一`*_AGENT` /
   `*_FALLBACK`にはCSVを入れない。翻訳上限は `COMMENT_TRANSLATION_MAX_ATTEMPTS=4`。
5. APIはbest-effortでreload通知する。**`.env` の非空明示値は再sourceするworkerの
   reloadで更新されるが、HTTP 200は通知先全workerへの反映証明ではない。**
   行削除による既定値復帰やconfig既定変更は完全再起動と区別する。
   再GETで保存値を確認し、対象workerの `prepass agents=` 等の許可された
   sanitizedログ・実効設定で通常時とピーク時の順序を確認する。
   下記の完全再起動と実測が完了するまで、本番反映完了とは扱わない。

**`core/config.sh`の既定値変更には対象workerの完全再起動が必要。**
`${VAR:-default}` は既に取り込まれた旧値を保持するため、USR1/HUP reloadや
通常tickの再sourceだけでは新しい既定値にならない。radio/chat/improve daemon等の
対象resident processと子ジョブを確認し、認可された対象限定操作で反映する。
`.env` に明示値があればそれが優先されるため、既定値との違いも確認する。

既存のpoll限定helper `ops/vm_actions/reload_poll_worker.py` はUSR1 reload用であり、
**新しいconfig既定値の反映を保証しない**。pollを含め必要なworker完全再起動の
正規経路がない場合は未完了として扱い、任意execやSSHで迂回しない。
旧子ジョブには親の再sourceだけでは反映されない。配信・共通音声・ゲーム全体の
再起動で代用せず、休止中・不在のworkerを勝手に起動しない。

[read-only diagnostics](runtime-diagnostics.md) と許可された確認で次を検証する。

- 配備済みdocich SHA、Soren gitlink、対象ファイルのSHA-256とtracked drift。
- required workerのdown/stale/duplicate、対象workerの新PID・起動時刻・実効設定、
  旧子プロセス終了、配信・共通音声PIDの維持。
- UI表示、明示設定、workerの実効チェーンの一致。
- `ai.recent_events` のprovider/model/winner/failureで実際に選ばれた経路。
  配備成功だけ、または全体のattempt件数だけで2経路の疎通を成功扱いしない。
- Go→OpenRouter→既存fallback、カスタム設定・ピーク順序の保持、
  9/24以降も設定どおりで期限による追加・削除がないこと（まずローカル回帰）。

## Runtime checklist

- runtime registry / manifest / worker health / queue registry: worker追加やlane変更なし。
  PID・pause・lock・state契約を維持し、pollを含む既定値の反映は別途確認。
- structured telemetry: 既存`ai_stats`のattempt/winner/failとprovider別agent識別子を維持。
  rc79（rate-limit）/91（gate give-up）/92（queue give-up）の意味を変更しない。
- diagnostics coverage / secret-redaction: 既存provider/model欄を使用。
  secret・token・prompt本文・生成本文・HTTP header・環境変数の公開を追加しない。
- regression tests: 明示chainとUI既定値の同期、Go→OpenRouter→既存fallback、
  カスタム値、空欄の継承・停止、単一AGENT、ピーク順序、期限なし、ID保持を検証する。
  docich設定/UIテストとSoren runtimeテストの結果は区別する。
- deploy影響: sourceの照合とresident process反映を区別し、最新HEADの必須CI・
  review・merge・VM同期・実測の未確認事項をPRへ記録する。
