# 半熟英雄の右寄せ投影と左側観測カード

半熟英雄だけ配信側のcontain投影を右寄せする。元のRetroArch窓・内部解像度・AI観測・入力座標は変更しない。配信枠は `(0,90,960,540)`、映像の比率と表示寸法、上下・右の共通枠を維持する。他ゲームの中央配置は変更しない。

## 寸法と合成

`presentation.py --align right` は取得したnative窓寸法を、実際のBGRA→FFmpeg scaleで一度測定する。高さ540へのcontain後の幅を固定値で推測しない。例えば299×224の検証用入力は721×540となり、左余白は239pxになる。これは合成入力の測定であり、VMの実測寸法ではない。

既存のscaleとpadで全映像を含めた後、測定した左の黒余白だけをcrop対象から外し、ffplayの投影窓を映像幅に合わせて `x=left_gap` に置く。元ゲームの窓を縮小する処理ではない。ゲームの背面にある共通ブラウザーの `broadcastGameGap` 面が、その左余白へカードを描く。面の外枠は960×540のまま、描画iframeだけを観測した余白へcropする。映像とカードの境界は同じ `presentation.json` のcontent幅・content xを使う。

`presentation.json` のready/right/viewport/contentを、canonical active runtimeの識別子・世代・leaseを検証した上でSorenのread-only bridgeが取り込む。寸法不正、幅180px未満の余白、投影失敗、terminal、観測30秒超、別ゲームへの切替ではカードを出さない。移行中の旧left投影はSoren側だけ読み取り互換を残すが、新しいdocich起動はright投影を要求する。

## 表示する事実

左の拡張枠は新しい監視プロセスやモデル呼び出しを増やさず、既存のbounded read-only状態だけを使う。

- 現在状況: 画面状態、話数、ゲーム内年月、所持金、兵力。
- 戦績・戦闘: 勝敗、戦闘開始/終了、切り札確定、実測した城失陥。
- 出撃・計画: 成立/失敗、現在のchart step、保留中の処理。
- 卵: 観測済みの将軍ごとの残使用回数。
- 失った城、累計の占領拠点、駐留、行軍。
- 入力・観測: 実入力回数、観測回数、最終観測からの秒数。
- 交戦HPは一致する両軍の有効なHPを読めた時刻から10秒以内だけ表示する。古い/将来/不正な時刻、未観測の旧stateは隠す。
- 駐留は将軍一覧を実際に読めた時刻から30秒以内だけ表示する。移動による配置更新は新しい観測として扱わず、表示時刻を無効にする。
- 行軍は既存policyのbusy期間内の出撃を示し、成立未確認を含むことを明示する。到着の計画を実績として表示しない。
- 占領した拠点は累計記録であり、現在の保有城数ではない。現在数は推測しない。
- 18pxの本文を維持し、長い拠点・将軍名をカード内で折り返す。実DOMの高さでページを分け、10秒周期で全カードを巡回する。端末向けfeedの省略処理は、サニタイズ・上限制限済みの余白専用レコードに適用しない。

## 配備と確認

Sorenの左余白対応をmainへ統合してから親のgitlinkを更新し、親PR/CI/mainを通してcanonical VM gatewayで配布する。Sorenの共有表示serviceは同じ固定routeを使うが、左cropと拡張カードを読むため表示serviceだけの再読込が必要。親の `shared_overlay_reload_epoch` が変わった正規deployで固定helperを実行し、4面のreadiness・encoder/audio/ゲームidentityとプロセス起動時刻の維持を照合する。未準備・切替中・実測不一致は失敗として報告し、成功扱いしない。encoder・共通音声・ゲームを再起動しない。

投影プロセスは起動時に配置を読む。進行中の半熟試合は止めず、その試合が自然に終了して次に半熟が起動する境界で新しい配置が有効になる。旧投影にはrightの証拠がないため、新しい左余白契約として扱わない。自動切替を強制したり、確認用の試合を起動したりしない。

完了には合成ピクセル・Chromium描画に加えて、自然登場した半熟の実配信フレーム、四辺/スコア/案内、左カードとの非重複、観測値一致、切替後撤去、encoder/audio PID・起動時刻の維持、対象mainとVM現物のSHA-256照合が必要。配布のみ、fixtureのみでは実配信の完了としない。

## Runtime checklist

既存presentation状態の幾何情報と既存bot stateの表示専用観測時刻を使う。worker/queue/model/providerは追加・変更しない。表示面は共有overlayの固定route・幾何・readiness契約に登録し、既存shared-overlay diagnosticsでservice healthを確認する。秘密情報/本文ログ/任意execの公開は追加しない。投影・state fencing・期限・共有面・切替の回帰テストを実行する。


## 失敗記録と手動復旧投影の管理置換

`presentation_failed` の元ゲームが稼働し、同じ元窓を正規profileで表示する手動復旧ffplayがある場合、owner-only `recover_hanjuku_projection` をproduction/main・confirm=productionで実行する。元game/privateX/agent、共有encoder・音声・ユーザー休止を維持する。healthyな通常投影は対象外。

現在run/generation/lease、元game paneのPID起動時刻、元Xvfb/viewerの親とgroup、唯一の元窓、終了済み旧player group、手動ffplayの完全argv・UID・PID起動時刻・単独group・唯一windowと実geometryを照合する。未知・重複・変更・terminal・別epochは拒否。元stateをバイト単位でバックアップし、同runtimeの`presentation_recovery.json`を排他作成する。転送中はgame-switch lockを保持するが入力・休止は操作しない。

既存game tmux windowへgame所属guard paneを追加し、正規ffplayをその実子として起動する。元private窓から同filterで得たRGBと、新しい投影windowのRGBを有限時間で比較する。blank/fadeを拒否し、同source/geometryと情報のある最初の表示を確認してから、PIDfdで本人へ束縛した手動ffplayだけを終了する。新表示のsceneと実inputとの完全結合は別の受入であり、99%画素許容比較を同frame入力証明とは扱わない。

新guardとffplayは同じgame windowの物理子なので、既に生存する旧coordinatorの終了処理にも含まれる。元presenterのgroupsを水増しせず、guard/playerは`recovery`に別記する。投影のstartup/実行失敗では、元game anchorが生存する限りguard paneも生存する。旧adapterのany-dead判定で元ゲームを誤って終端へ追い込まない。投影失敗は固定receiptへ記録し、通常realignの拒否を維持する。

guard停止又は元game終了では、本人のPIDfd/起動時刻/argvを再照合して所有する投影だけを回収する。共有基盤や未知groupへsignalを送らない。正規operator後のrealign、初期表示・動く実映像、同gamePID、共有PID・音声・休止、旧cleanupの資源回収を別々に検証する。sourceSHA一致・visible window・ready記録だけで全受入とはしない。
