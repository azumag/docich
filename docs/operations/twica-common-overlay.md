# TwiCa: 配信共通の前景オーバーレイ

関連: docich #1236 / soviet_now #518 / docich #1227。
これは独立レンダラーだけの試作ではなく、既存Soren配信ランナーとの接続、旧iframeの実停止、二段階の所有権移行、運用コマンドと結合テストをまとめた実装である。**コードの配備だけでは有効化しない。**

## 映像と責務

`X11のゲーム + 既存の共通レール → TwiCa RGBA前景を一度だけ合成 → 既存H.264/AAC/字幕/loopback relay`

- Sorenの`direct_stream.sh`は親側で準備済みのときだけ、FFmpeg executableを`bin/docich-twica-ffmpeg`に差し替える。元の`lib/direct_stream.py`は変更せず、再接続、状態、停止、字幕、音声同期、出力先を維持する。
- アダプターは能力検査を元FFmpegへそのまま渡す。本エンコードにだけ独立FDのRGBA入力を追加する。stdinの`q`、stdoutのprogress、stderr、音声入力は奪わない。字幕フィルターは前景合成後へ移す。
- 停止は既存ランナーの`q → SIGINT → SIGKILL`と猶予時間を維持する。native FFmpegの親である監視プロセスがアダプター専有pipeのEOFを監視し、SIGKILLされたアダプターに代わってnativeをkill・waitする。progressのEOFはnative回収後に到達し、`pipeline.json`の`encoder_pid`はnativeのPIDを保つ。
- `docich-twica-common.service`は共通配信側が所有する。一つのブラウザページがTwiCaのイベント購読・キュー・演出・効果音を保持する。ゲームstateやpresenterの起動停止を参照しない。
- カード元のviewportは配信と同じ大きさ。最終映像のXを幅の1/3だけ加算し、中心を5/6へ動かす。最終画素への丸めによる最大0.5px以外、Y・寸法・倍率を変えない。右サイドバー内への縮小で代用しない。
- 白も黒も抜かず、RGBAのalphaを使用する。半透明演出、白いカードを維持する。OSのウィンドウ順序に依存せず、共有fullscreenを前面化しない。
- 元から画面外にはみ出すほど大きい演出は最終viewport境界で切れる。既存演出を勝手に縮小して隠す実装にはしない。実カード・長い名前・複数枚は本番受入で確認する。

## モジュール・状態

| 所有者 | 実装 | 状態 |
|---|---|---|
| 配信アダプター | `twica_ffmpeg.py`、`twica_encoder.py` | `pipeline.json`、`pipeline.lock` |
| 共通レンダラー | `twica_service.py`、`twica_renderer.py` | `renderer.json`、`service.lock` |
| RGBA受け渡し | `twica_overlay.py` | RAM上の`frame.rgba`、`publisher.lock` |
| 所有権操作 | `twica_operator.py` | `control.json`、`operator.lock` |
| 旧ブラウザ停止 | Soren `lib/twica_legacy_owner.mjs` | `legacy-*.json` |
| 固定運用 | `ops/vm_actions/twica_common.py` | 成功/固定理由コード、固定status投影 |

制御の既定場所は`/home/ubuntu/docich/run/twica-common`、フレームは`/dev/shm/docich-twica-<uid>`。`run/`はGit管理外。制御ディレクトリは0700、JSON・checkpoint・frameは0600。リンク・特殊ファイル・他ユーザー所有・過大入力を拒否する。PIDはboot IDと開始tickで照合し、再利用されたPIDを生存証拠にしない。

`DOCICH_TWICA_STATE_DIR`を変える場合は親操作・Sorenゲーム/共有プロセス・レンダラーに同じ値を設定する。`DOCICH_TWICA_FRAME_DIR`もレンダラーと配信側で一致させる。フレームの定常書き込みはRAM上で行い、VMディスクを毎秒数十MB書き換えない。既定1280×720・15fpsの未圧縮フレームは約55MB/sであり、RAMコピー・PNG描画のCPUコストは残る。

## 単一所有権と障害

`prepare`は元のlegacy表示を維持し、接続のない共通ブラウザの事前検査を準備する。いきなり旧表示を止めない。

`activate`は、更新済みのgame/shared guard、共通service、FFmpeg入力の準備を確認してから、次の順序で移行する。

1. 新世代の`owner=none`を原子的に記録。
2. 生存中の全guardが旧iframe/bufferを**削除**し、共通レンダラーも購読を閉じたことを、同じ世代のfresh heartbeatで確認。
3. `owner=common`にし、新しい共通ページと実フレームのfresh状態を確認。

タイムアウト・不明な生存クライアントは成功扱いせず`none`で止める。renderer停止をlegacy復活の条件にしない。再起動時は新しいpublisherが古いフレームを消し、古いカードを引き継がない。通常のページreloadにも古いTwiCaのinit scriptを残さない。

`rollback`も一旦`none`で全員の停止を確認した後、指定したgame/sharedの片方だけをlegacy ownerにする。共通ページとlegacyの同時購読を作らない。`prepare`後でも本番有効化前は旧構成そのものを維持するため、元からあったlegacyの二系統はその時点では残る。

有効化後のゲーム切替はcontrolを変更しない。ページ・購読・音声が継続する。renderer更新が止まると約1秒で透明フレームへ退避し、更新を待ってエンコードを止めない。renderer自身の復旧は2〜30秒のbackoffで同じ共通所有権のまま行う。

ブラウザstorage/sessionStorageは専用のprivate checkpointに限定して保存し、正常再起動時に復元する。checkpointは診断出力・Actions artifact・Gitへ含めない。ゲーム切替中の欠落/重複は一つのページを維持して防ぐ。ただし**任意の瞬間のプロセス強制終了を跨ぐ完全なexactly-once配送まで保証する仕組みではない**。初回handoffはTwiCaがidleの境界を確認して行い、旧ページのメモリ内キューをコピーしたとは扱わない。

## 本番へ導入する手順

この変更はSorenとdocichの対応するPRを一緒にレビューする。Sorenのmain統合後、docichのgitlinkをその統合SHAへ更新し、親mainの正規VM deploy経路で配備する。未マージの参照のままproductionへ有効化しない。tracked driftを無条件で上書きしない。

有料Desktop Commanderは不要。既存owner-only gatewayを使う**手動専用**workflow `TwiCa common overlay operator`を追加する。既存secret・environment・main/protected/actor検査を保持し、任意コマンド入力や公開stdoutの経路を追加しない。

### 前提

- Linux、既存のSoren FFmpeg配信ランナー、同一ホスト/UID、PulseAudio bus `soren_null`。
- `SOREN_DIRECT_TWICA_OVERLAY_URL`は既存`.env`から受け取る。URLをCLI引数や公開ログへ複製しない。
- 共通serviceに使うPython venv/Playwright/Chromiumが必要。`prepare`は専用`run/twica-env`へ依存を導入するが、OSパッケージや既定音声sink、モデル、権限を変更しない。不足ライブラリがあれば事前検査が失敗し、表示は切り替えない。
- 更新済みSoren guardが**実行中のゲーム/共有ブラウザ双方で読み込まれていること**。古いプロセスへコードを配っただけではguardは追加されない。既存の正規ライフサイクルでの更新が必要。単なるiframe reloadでは不十分。
- ゲームブラウザが停止中と確認済みのときに限り`shared_only=true`で共有側だけを要求できる。未確認のクライアントを無視するフラグではない。

### 実行

1. `operation=prepare`、`confirm=production`。依存・専用service・private policyを準備し、旧表示を維持する。serviceは空白ブラウザの起動/撮影能力だけ検査して待機し、まだTwiCaへ購読しない。
2. 更新済みguardとレンダラー待機を確認する。固定statusでlegacy heartbeat不在やstaleなら先へ進まない。
3. **初回だけ** `operation=arm`、`confirm=production`、`confirm_stream_restart=true`。起動済みFFmpegには後から入力を追加できないため、既存の正規`direct_stream.sh stop`と監督ループを用い、対象encoderを一度再接続する。supervisor・子孫関係・pause状態・guardとrendererの準備が不明なら、停止前に拒否する。配信全体のservice、Xvfb、ゲーム、音声workerは再起動しない。
4. TwiCaの演出/待機キューがidleであることを確認し、`operation=activate`、`confirm=production`、`confirm_idle=true`。これは配信encoderを再起動しない。
5. `operation=status`と配信の実画像/実音声を確認する。必要なら`restart-renderer`で共通serviceのみ復旧する。元に戻す場合は`rollback`、`confirm_idle=true`、`legacy_role=game`または`shared`。

`enable`はprepare→arm→activateをまとめる操作だが、guardが読み込まれていないと停止前に拒否する。初回のencoder再接続とidle確認の両方を明示する必要がある。既にarm済みなら再接続しない。**この実装作業では、本番のarm/activateを実行しない。**

rollback後も透明な合成入力は残す。入力を除去するためだけにencoderを再起動しない。ポリシーファイルやprivate lockの手動削除をrollbackとして使用しない。

## 診断・受入

固定component registryは`ops/vm_actions/twica_common_registry.json`。既存start_allのWORKERSへ別systemd serviceを混ぜず、専用health operationで診断する。全体の`collect_diagnostics.py`には新項目を追加していない。既存のproduction execと同様にActionsには生のhelper出力を公開しない。workflow成功は専用チェックの成功を示すだけで、視聴側の画面確認そのものではない。

`status`はowner、policyの妥当性、pipeline準備、renderer状態、guard件数/鮮度、legacy購読件数、frame鮮度だけを返す。フレーム、URL、Cookie、環境変数、メッセージ本文、checkpointは返さない。common選択中に出力の証拠が足りなければ運用helperは非0終了する。

CIは次を一つの変更として検証する。

- 制御・private path・世代・PID・二段階handoff・rollback・無承認restart拒否。
- 実Chromiumのalpha、白、半透明、アニメーション。
- 実Xvfbの不透明なゲームウィンドウを6パターン切替し、最終FFmpeg画素で前景表示/rails維持を確認。renderer停止後も同じencoderが出力を続ける。
- ローカルHTTP/SSE fixtureでgame/shared/commonの実ブラウザを使い、8回の背景ページreloadで一つの購読と連続イベントを維持する。
- 既存字幕/音声map/出力先、stdin q/progress/exitの互換性。既存Sorenのshared/direct/proxy/lifecycleテスト。

合成fixtureは本物の半熟英雄・Soren91・NetHackを起動したテストではない。TwiCa本番イベント、効果音が既存Pulse busで一度だけ流れること、実ゲームの連続進行、CPU/メモリ増分、長時間安定性は、許可された本番切替後の受入として別途実測する。ソース一致・CI成功・配備・ランタイム有効化・視聴側の確認は別々に記録する。

ローカルのauthoring環境はHTTP navigationがブラウザの管理ポリシーで拒否されるため、`DOCICH_TWICA_NETWORK_TESTS=1`の2件はネットワーク利用可能なCIで必須実行する。管理ポリシーは解除しない。オフラインの実Chromium alphaと実X11/FFmpegテストはローカルでも実行する。

### Lossless capture cost reduction

The common renderer uses one persistent CDP session on its existing Chromium
page. It sets the transparent page background once per document and requests
viewport PNG with `optimizeForSpeed=true`, then keeps the existing RGBA decoder,
atomic publisher and start-of-capture TTL. Resolution, frame cadence, alpha,
animation playback, queue ownership and audio routing stay unchanged. Capture
failure still clears the frame and follows the existing bounded recovery.

`tools/benchmark_twica_capture.py --scenario blank|card --frames 60` compares the
old general screenshot API and the dedicated capture on offline synthetic pages.
It emits timings, CPU totals and pixel equivalence only; no production URL,
subscription or saved image is used. Child CPU includes browser/driver startup
and shutdown. Measure production component and host CPU separately: fixture
latency reduction alone does not establish VM headroom.
