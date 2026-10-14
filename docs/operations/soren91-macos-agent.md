# Soren91 Mac agent の復旧と常駐管理

Meriken は VM の `MerikenCornerAdapter.DEFAULT_ENV_FILE` に設定された Mac HTTP
agent を使用する。agent停止や認証不一致は `start_failed` → rollback →
`corner_rotation=recovery_required / execution-error` を起こす。
配信全体のrestart、rotation台帳の手編集、休止設定の解除で代用しない。

## 既存の認証と起動設定

`tools/soren91_local_agent.mjs` を Node で起動する。現在の本番は
`config/games/soren91.toml` の `agent.enabled=true` なので `cdp-host` が必要。
古いMac READMEの `session` 既定をそのまま適用しない。
ffmpeg は SRT 対応版を指定する。ScreenCaptureKit/audio/virtual-display
helper がビルド済みで、GUIユーザーに必要な権限があることも確認する。

認証はVM呼出側の既存設定と一致させる。既存Macファイルが同名でも一致の証明には
ならない。tokenを新規発行したりVM設定を変更する前に、既存の安全な経路で照合する。
値をterminal、PR、ログ、argv、plistへ出さない。専用のowner-only 0600ファイルに
保存し、以下の非秘密JSONにはファイルの絶対パスだけを書く。

```json
{
  "repo": "/absolute/path/to/existing/soviet_now",
  "node": "/absolute/path/to/node",
  "ffmpeg": "/absolute/path/to/srt-capable/ffmpeg",
  "token_file": "/absolute/path/to/private/token-file",
  "host": "100.64.0.1",
  "port": 19191
}
```

## GUIセッション内のLaunchAgent

テスト・自己レビュー済みのdocich commitから実行し、配布元commitを記録する
（設定JSONはGitに入れない）。main統合後も配布scriptのSHA-256一致を確認する:

```sh
python3 ops/local/soren91_agent.py install --config /absolute/path/to/config.json
python3 ops/local/soren91_agent.py status --config /absolute/path/to/config.json
```

installerは `gui/<uid>` の存在を確認し、Aqua限定LaunchAgent
`com.docich.soren91-agent` を登録する。root daemonや非GUIセッションでは使わない。
script/configは `~/Library/Application Support/docich/soren91-agent/` へ保存するため、
元の開発worktreeを削除しても管理処理は残る。ゲームrepo・Node・ffmpegの指定先は維持する。
既存の待受・ロード済みservice・既存インストールがあれば上書きせず失敗する。

`KeepAlive` と30秒の起動間隔で、agent終了・Tailscale bind失敗から再起動する。
ログイン中にだけ動き、Macの電源断・スリープ中の到達性を保証しない。
nodeと子rendererはlaunchdのprocess group管理下に置く。作動中のrendererを
単なる疎通テストのためにrestartしない。HTTP応答が固まったプロセスの自動killは行わない。

子の任意ログは保存せず、`launchctl print gui/$(id -u)/com.docich.soren91-agent`
のPID/exit statusと `status` の固定JSONを診断に使う。
`status` は認証付きHTTP、backend、mode、running型を確認し、固定フィールドだけ返す。
VMのread-only diagnosticsは引き続きrotation/game-switchを観測する。
このサービスはVM worker/queueを追加しない。VMからの疎通はVMの既存認証で別途確認する。

停止/更新はrendererがidleであることを確認した上で:

```sh
launchctl bootout gui/$(id -u)/com.docich.soren91-agent
```

bootout後に旧PID/子プロセス終了を確認する。更新は既存script/config/plistを退避し、
installerを再実行する。恒久停止時は当該plistをLaunchAgents外へ移し、次回ログインの
RunAtLoadを止める。tokenの削除やVM/rotationの変更をこの操作に含めない。

## 復旧の完了条件

1. Macで`status=healthy`、VMでも既存認証の `/v1/status` が200。
2. renderer idle中に当該agent PIDだけを終了し、launchdによる新PIDと疎通回復を確認。
3. rotationが失敗予約で停止している場合のみ、同一requestのrollback/資源解放を確認し
   正規 `corner-rotation-operator recover-failed` を実行。
4. 次の自然なMeriken枠で起動・映像更新・終了復帰を確認する。health成功とライブ受入は別。

テスト: `python3 -m unittest discover -s ops/local/tests -v`。
