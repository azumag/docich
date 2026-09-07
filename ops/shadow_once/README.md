# 手動のshadow単発実行

`run.py` は既存pauseを保持したまま、入力を固定して改善workerを1件だけ起動するowner用CLIです。タイマー・自動再試行・配信サービス再起動・通常のspawner呼び出しはありません。最大840秒で、既存gatewayの900秒制限内に終了確認を収めます。モデル/provider/恒久予算/.envは変更しません。

## 起動前提

- Linux systemd（`systemd-run --expand-environment=no`対応、VMは実測済み）、system managerへの既存の非対話sudo権限が必要。権限不足を別経路へ迂回しません。
- 分析gateの固定manifestにある3ファイルのSHA/mode一致、review済みspawn lease protocol、idle/pid0、旧worker不存在、`tmp/state/step5-founding-20260906T201809Z` 所有のpauseが必要です。違えば停止します。
- manifestの配置先は `/home/ubuntu/soren/tmp/state/shadow-once-input-<識別子>.json` のみ。JSONはversion=1、budget_seconds（1〜840）、analysis_seconds（総予算以下）、game_num、turns、inputsの固定schemaです。inputsは1〜64件のpath（game_history内の単純なjsonl名）、sha256、scoreです。実際の選択バッチから作成・照合し、架空の値や過去runの状態を現在の証明にしません。CLIはscoreの意味的正しさまでは証明しません。
- コマンド、モデル名、環境変数、権限変更、任意出力pathをmanifestに指定できません。

owner専用 `VM operations / exec / production / ref=main` のcommandとして使用します（作業ディレクトリはdocich）。

```sh
python3 ops/shadow_once/run.py check /home/ubuntu/soren/tmp/state/shadow-once-input-<識別子>.json
python3 ops/shadow_once/run.py run /home/ubuntu/soren/tmp/state/shadow-once-input-<識別子>.json
```

`check` は静的な前提のみ確認し、モデルを呼びません。実効shadowの確認はサービス内で設定を読み込んだ後、worker開始前に行います。enforceなら拒否し、shadowをreadonly/exportしてからworker snapshotをsourceします。固定検証では、レート制限済み無料候補と遅いOmenで解析枠を失わないよう、既存OpenCode経路の contributor 2候補、`deepseek-v4-flash` の順へ改善候補を固定し、ピーク時の差し替えも無効化します。OpenCodeのedit/writeは候補sandbox内だけ許可し、`external_directory` は固定で拒否します。worker自身も既存設定を読みますがreadonlyな値は上書きできません。設定読込失敗時は起動拒否です。

## 実行境界と終了確認

kernel leaseとspawn directory lockを終了まで保持します。uuid付きsystemd serviceでUser=ubuntu、NoNewPrivileges、ProtectControlGroups、KillMode=control-group、RuntimeMaxSec、TimeoutStopSecを使用します。戦略本体/helpers、core、strategy、prompt、.env、pause、起動snapshotをservice内でread-onlyにし、入力履歴はhash検証済みコピーをbind read-onlyで元pathへ見せます。これらの制限はそのジョブだけに適用され、配信側のファイル操作権限は変えません。

標準出力/エラーは公開せず破棄し、既存workerは従来のprivate診断を使用します。各runはtmp/state/shadow-once-*へ入力・worker snapshot・operator-result.jsonを保存します。資格情報をコピーしません。.env内容はoperator側で読み取らず、stat不変とread-only mountで扱います。

正常/失敗時ともunitの終了とcgroup空を確認します。元の戦略/helpers/config、.env stat、配信PID/active、pauseを照合し、不一致は成功にしません。終了したworkerが書いた自分のPIDのprogressだけをidleへ戻し、他者のPIDは変更しません。未知差分や他者の状態をrollbackしません。外部からoperator自体をSIGKILLした場合はfinally処理を保証できないため、unitの期限で停止した後にreceipt・stateを手動確認し、再実行前に解消してください。pauseは解除しません。

`finished`やlauncher_rc=0は候補採用を意味しません。analysis_hold/reject、shadow拒否、利用制限、timeout等は既存状態から分類し、それ以外はunclassified_no_applyです。候補生成・隔離検証の証明は当該workerのanalysis-checkとisolated_runner receiptを別途照合します。本番戦略はshadowで変更しません。

## 検証範囲

ローカルでmanifest/前提の拒否、shadow固定、終了時の所有権、timeout/子process残存判定をテストします。VM上では合成ファイルだけを使い、実際のservice_command/superviseでread-only・固定入力・PID記録・正常終了・2秒timeout・setsidしたsleep子process回収を確認しました。systemdによるシェル変数の先行展開で一度exit81になった負例も再現し、`--expand-environment=no`で修正しました。実モデルを使った1サイクルはまだ実行していません。

再実行可能な無害なVM検証: `python3 ops/shadow_once/probe_systemd.py`。合成rootだけを使い、実行unit内でBubblewrapの起動も検証します。既存の配信・モデル・資格情報には触れません。結果のfixtureディレクトリを保持します。
