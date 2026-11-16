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
（設定JSONはGitに入れない）。main統合後も配布scriptのSHA-256一致を確認する。
installerはsystemボリューム上のpython3で実行する（外部ボリュームのpythonは
LaunchServices経由のappから到達できず、`python-on-removable-volume`で拒否する）:

```sh
python3 ops/local/soren91_agent.py install --config /absolute/path/to/config.json
python3 ops/local/soren91_agent.py status --config /absolute/path/to/config.json
```

installerは `gui/<uid>` の存在を確認し、Aqua限定LaunchAgent
`com.docich.soren91-agent` を登録する。root daemonや非GUIセッションでは使わない。
script/configは `~/Library/Application Support/docich/soren91-agent/` へ保存するため、
元の開発worktreeを削除しても管理処理は残る。ゲームrepo・Node・ffmpegの指定先は維持する。
既存の待受・ロード済みservice・既存インストールがあれば上書きせず失敗する。

installerは同時に `Soren91Renderer.app`（bundle id `com.docich.soren91-renderer`）を
作成し、LaunchAgentの `ProgramArguments` は `open -W -n <app>` になる。appの
Mach-Oランチャーが `service.py run` を子プロセスとして起動する。この構成は
画面収録の許可範囲（次節）のためのもので、`service.py run` を直接起動して代用しない。

## 画面収録の権限（TCC）は app 単位で付与する

macOSの画面収録（`kTCCServiceScreenCapture`）は、TCCがそのプロセスの
「responsible」と判定した実行体に紐づく。nodeやpythonを直接親にすると、
**同じnodeバイナリを使う任意のプロセスに許可が及ぶ**。2026-10-21のnode更新後に
画面収録が実効で拒否され、10/22以降Merikenが全滅した際、nodeへの付与は
範囲が広すぎるため採らない（2026-11-16 オーナー判断）。

現在の構成では、LaunchServicesが起動した `Soren91Renderer.app` がresponsibleになり、
その子（python → node → capture/audio helper）が同じidentityを継承する。実測:

- 変更前: `responsible={identifier=node, ...}` → nodeへの付与が必要（広すぎる）
- 変更後: `responsible={identifier=com.docich.soren91-renderer, ...}`

運用:

1. システム設定 → プライバシーとセキュリティ → **画面収録** で
   **Soren91 Renderer**（`com.docich.soren91-renderer`）だけをONにする。
   node / python / ターミナルには付与しない。
2. 一覧に無い場合は、次回のキャプチャ試行で出る許可ダイアログを承認するか、
   `Soren91Renderer.app` を追加する。
3. app bundleはad-hoc署名（Developer IDなし）。ランチャーやbundleを再ビルドすると
   TCCの許可が無効になり再許可が必要になり得る。再ビルド時は再付与して確認する。
4. 直接実行（`service.py run`、`node ...`）ではapp identityにならないため、
   コーナー実行経路で使わない。

attributionの確認（キャプチャ試行の直後）:

```sh
log show --last 3m --style compact 2>/dev/null \
  | grep AUTHREQ_ATTRIBUTION | grep -i soren91
```

`responsible={TCCDProcess: identifier=com.docich.soren91-renderer, ...}` なら正しい。
`identifier=node` のままなら、LaunchAgentが`open -W -n <app>`になっているか
（`plutil -p ~/Library/LaunchAgents/com.docich.soren91-agent.plist`）と
appの署名identity（`codesign -dv --verbose=4 <app>`）を確認する。

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
# bootoutは open(1) を止めるが、LaunchServicesが起動したappは残り得る。
# appのランチャーはSIGTERMを子プロセスへ転送して終了する。
pkill -TERM -f "Soren91Renderer.app/Contents/MacOS/Soren91Renderer"
```

bootout後に `open` と app の両方が消えたこと、`tools/soren91_local_agent.mjs`・
Chrome・仮想ディスプレイの子プロセスが残っていないことを確認する。更新は既存の
script/config/plist/appを退避してからinstallerを再実行する。appを再ビルドした場合は
画面収録の再許可が必要になり得るため、前節のattribution確認まで行う。恒久停止時は
当該plistをLaunchAgents外へ移し、次回ログインのRunAtLoadを止める。
tokenの削除やVM/rotationの変更をこの操作に含めない。

## 復旧の完了条件

1. Macで`status=healthy`、VMでも既存認証の `/v1/status` が200。
2. renderer idle中に当該agent PIDだけを終了し、launchdによる新PIDと疎通回復を確認。
3. キャプチャ試行時のattributionが `com.docich.soren91-renderer` であること。
4. rotationが失敗予約で停止している場合のみ、同一requestのrollback/資源解放を確認し
   正規 `corner-rotation-operator recover-failed` を実行。
5. 次の自然なMeriken枠で起動・映像更新・終了復帰を確認する。health成功とライブ受入は別。

テスト: `python3 -m unittest discover -s ops/local/tests -v`。
