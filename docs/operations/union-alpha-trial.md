# Union Alpha の1週間限定優先設定

## 目的と期間

既存のモデルチェーンを保存したまま、期間中だけ次の2経路を先頭へ追加する。

1. OpenCode Go: `opencode-go/union-alpha`
2. OpenRouter: `openrouter/stealth/union-alpha`
3. 既存チェーン（順序・個別設定を維持）

期間は **2026-09-17 22:00 JST 以上、2026-09-24 22:00 JST 未満**
（UTC: 2026-09-17 13:00〜2026-09-24 13:00、604800秒）。
これは固定の運用期間であり、再起動・再配備で延長しない。
期限後は新しいUnion Alpha試行を開始せず、既に実行中のリクエストは完了させる。
配備が遅れた場合、実際の利用時間はこの期間より短くなる。

## 対象と維持する契約

- Sorenの共通生成チェーン、分類のprimary/fallback、戦略改善、直接チェーンを持つ放送処理。
- docichのPAPER台本・改善、market PAPER、retroの有効なAI呼び出しは、既存の
  `src/docich/ai_generate.py` → Soren `ai_generate_list` の共通経路で適用する。
- 空設定によって停止している改善やコーナーを、この設定だけで有効化しない。
- NetHackの評価済みcommand manifest、半熟英雄の単一backend、自由戦略の単一
  text-only HTTP backend、self-repairのroot-owned単一モデルpolicyは、モデル
  **チェーンではない**ため書き換えない。ツール権限・canary承認・sandboxを維持する。
- 既存チェーンの設定自体は書き換えず、実行時の優先設定として合成する。
  Web UI/TOMLの基底チェーン表示と、実際に試行するチェーンは期間中異なる。
- 2経路の失敗・backoffは独立して扱い、失敗した場合は既存候補へ進む。

## 事前確認（本番反映の証拠ではない）

2026-09-17、ローカルOpenCode `1.18.27` のカタログで両モデルIDを確認。
Go版のカタログ単価はinput/output/cacheとも0、OpenRouterの公開models APIは
prompt/completionとも0。料金・提供継続を将来まで保証するものではない。

空の一時ディレクトリ、全ツールdeny、1 step、共有無効、外部plugin無効で
各モデルに固定の短い応答を要求した疎通確認では、両方ともrc=0・期待した応答を得た。
認証は既存CLIに任せ、資格情報を読み出したりコピーしたりしていない。
本番VMのプロバイダー設定・利用可能性は別途確認が必要。

## 配備と完了条件

通常の [owner-only control plane](../../ops/vm_actions/README.md) を使用する。
Soren PR → review/tests/CI → Soren main → docich gitlink PR →
review/tests/CI → docich main → canonical deploy の順。

既存設定の既定値を変えず共有関数を更新する場合、chat/YouTube/Kickは通常tick、
improve daemonは次ループ、soren_loopは次試合、単発ジョブは次起動で再sourceする。
旧子ジョブには親の再sourceだけでは反映されない。radioは既存deployの対象限定
再起動とruntime signature監視を持つ。

**poll_workerは通常tickで再sourceしないため、対象限定reloadが必要。**
`ops/vm_actions/reload_poll_worker.py`を既存post-deploy workflowへ追加し、
ownerのmain push配備成功後だけ固定helperを呼ぶ。PID/cwd/argv/開始時刻/trapを
検証してpidfd経由でUSR1を一度送り、同一プロセスのreloadログ到達を待つ。
pause/不在は起動せずskip。任意`exec`の許可は拡張しない。ログ到達はモデルの
実効利用の証明ではなく、未確認なら全チェーン反映は未完了とする。
ゲーム・配信・共通音声の再起動で代用しない。

[read-only diagnostics](runtime-diagnostics.md) で次を確認する。

- 配備済みdocich SHA、Soren gitlink、tracked drift。
- required workerのdown/stale/duplicateの有無、配信・音声PIDの維持。
- `ai.recent_events` のprovider/model/winner/failureで実際に選ばれた経路。
  配備成功だけ、または全体のattempt件数だけで2経路の疎通を成功扱いしない。
- 期限前後の新規試行と既存fallbackへの復帰（まずfake-clockテスト、実期限後は運用診断）。

## Runtime checklist

- worker registry / health / queue: worker追加やlane変更なし。pollへの初回反映は別途確認。
- telemetry: 既存`ai_stats`のattempt/winner/failとprovider別agent識別子を維持。
- diagnostics: 既存provider/model欄を使用。secret/raw prompt/生成本文の出力を追加しない。
- regression: 期間境界、Go→OpenRouter→既存fallback、個別設定、ピーク順序、
  期限越し待機、IDの保持と不正文字拒否を検証する。
- deploy: sourceの照合とresident process反映を区別し、未確認事項をPRへ記録する。
