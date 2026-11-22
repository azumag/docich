# Cloudflare verified-Web / RADIO rollout: 終了コード

対象: `ops/vm_actions/reply_radio_cloudflare_rollout.sh`（PR #2720 の v4）。
この表は**診断用**であり、rollout 実行の承認や production capability の充足を意味しない。

| 終了コード | 判定・停止段階 |
| --- | --- |
| 0 | 事後検証まで成功 |
| 64 | 引数が渡された |
| 65 | `EXPECTED_SHA` が40桁の小文字hexでない |
| 66–68 | docich git checkout不在、HEAD不一致、追跡ファイルに変更あり |
| 69–71 | Soren .env 不正、再起動ヘルパー不足、.env 読み込み失敗 |
| 73 | Web Search account 未設定または不正 |
| 74 | Web Search 専用 credential 不足 |
| 75 | direct Workers AI account 未設定または不正 |
| 76 | direct credential 不足または token/file の選択不正 |
| 77 | 選択された direct token file が安全な通常ファイルでない |
| 78 | 実 Web Search + verified-body comment canary 失敗 |
| 79 | 実 JEV + Web Search + direct LLM RADIO canary 失敗 |
| 80 | .env の所有ブロック更新失敗 |
| 81 | radio worker 再起動失敗 |
| 82 | chat/kick worker 再起動失敗 |
| 83 | 再起動後の worker/PID/environment 検証失敗 |
| その他の非ゼロ（例: 1） | readiness の import/runtime 例外など、列挙外のインフラ障害。元の終了コードを維持する。 |

## 安全境界

- 73–77 と readiness の予期しない非ゼロは、外部 canary と .env 変更の**前**に停止する。原因調査時も credential 値・パスをログへ出力しない。
- 78–79 は .env 変更前の canary 段階で停止する。外部APIへの呼び出し自体は発生し得る。
- 80–83 は変更後の失敗を含むため、スクリプトの rollback trap が .env のバックアップ復元と worker 再起動を best-effort で試みる。rollback 成功を終了コードだけから断定しない。
- 実行には別途、保護された main の exact SHA、production capability、所定のレビュー・CI・運用承認が必要。Draft PR をマージして起動条件を進めない。
