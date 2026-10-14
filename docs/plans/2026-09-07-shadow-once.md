# Shadow単発起動

主担当が実装・検証・自己レビューを直接担当する。サブエージェントは使用しない。

1. owner-only gatewayから使う固定CLIをdocichに置く。固定runtime `/home/ubuntu/soren`、既存pause所有者、idle/pid0、既存worker不存在、review済み分析gateを起動前に確認する。
2. 入力manifestは履歴path/hash・scores・game/turn・短縮予算を固定する。コマンド・モデル・provider・環境変数・任意pathを受け取らない。入力とruntimeコードを検証し記録する。
3. kernel leaseとspawn directory lockをジョブ終了まで保持。systemd transient service（User=ubuntu）で実行し、shadowの実効値を確認してreadonly化してから固定workerをsourceする。戦略本体/helpers/設定/.envはOS側もread-only。配信サービス/通常spawnerは呼ばない。
4. RuntimeMaxSec、KillMode=control-group、TimeoutStopSecで所有する子processだけ回収。gatewayの900秒より短い最大600秒とする。失敗・状態変化・中断を成功扱いせず、receiptに記録し、未知変更をrollbackしない。
5. 負例テスト、模擬子processを使う制御テスト、Linux systemd実機の無害なprobeを行う。実モデルサイクルとは区別する。PR/CI/自己レビュー後に統合する。
