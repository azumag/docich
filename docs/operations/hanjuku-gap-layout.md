# 半熟英雄の左寄せ投影と観測カード

半熟英雄だけ配信側のcontain投影を左寄せする。元のRetroArch窓・内部解像度・AI観測・入力座標は変更しない。配信枠は `(0,90,960,540)`、映像の比率と表示寸法、上下・右の共通枠を維持する。他ゲームの中央配置は変更しない。

## 寸法と合成

`presentation.py --align left` は取得したnative窓寸法を、実際のBGRA→FFmpeg scaleで一度測定する。高さ540へのcontain後の幅を固定値で推測しない。例えば299×224の検証用入力は721×540、右余白は239pxになる。これは合成入力の測定であり、VMの実測寸法ではない。

既存のscaleとpadで全映像を含めた後、右の黒余白だけをcropし、ffplayの投影窓を映像幅に合わせる。元ゲームの窓を縮小する処理ではない。ゲームの背面にある共通ブラウザーの `broadcastGameGap` 面が、その右余白へカードを描く。新しい面の外枠は960×540で、描画部分は観測した余白に限る。映像とカードの境界は同じ `presentation.json` のcontent幅を使う。

`presentation.json` のready/left/viewport/contentを、canonical active runtimeの識別子・世代・leaseを検証した上でSorenのread-only bridgeが取り込む。寸法不正、幅180px未満の余白、投影失敗、terminal、観測30秒超、別ゲームへの切替ではカードを出さない。共通表示サービスが新しい面を提供する `gameGapEnabled` を送っている場合だけ、右の既存カードから重複詳細を取り除く。古いサービスとの組合せでは従来のカードが残る。

## 表示する事実

- 占領した拠点は累計記録であり、現在の保有城数ではない。現在数は推測しない。
- 駐留は将軍一覧を実際に読めた時刻から30秒以内だけ表示する。移動による配置更新は新しい観測として扱わず、表示時刻を無効にする。
- 交戦HPは一致する両軍の有効なHPを読めた時刻から10秒以内だけ表示する。古い/将来/不正な時刻、未観測の旧stateは隠す。
- 行軍は既存policyのbusy期間内の出撃を示し、成立未確認を含むことを明示する。到着の計画を実績として表示しない。
- 18pxの本文を維持し、長い拠点・将軍名をカード内で折り返す。実DOMの高さでページを分け、10秒周期で全カードを巡回する。端末向けfeedの省略処理は、サニタイズ・上限制限済みの余白専用レコードに適用しない。

## 配備と確認

SorenのPR/mainを確定してから親のgitlinkを更新し、親PR/CI/mainを通してcanonical VM gatewayで配布する。Sorenの共有表示serviceは新しい面のrouteと4面のreadiness契約を読み込むため、表示serviceだけの再読込が必要。親の `shared_overlay_reload_epoch` が変わった正規deployで固定helperを実行し、4面のreadiness・encoder/audio/ゲームidentityとプロセス起動時刻の維持を照合する。未準備・切替中・実測不一致は失敗として報告し、成功扱いしない。encoder・共通音声・ゲームを再起動しない。

投影プロセスは起動時に配置を読む。進行中の半熟試合は止めず、その試合が自然に終了して次に半熟が起動する境界で新しい配置が有効になる。旧投影にはleftの証拠がないため、追加カードを重ねない。自動切替を強制したり、確認用の試合を起動したりしない。

完了には合成ピクセル・Chromium描画に加えて、自然登場した半熟の実配信フレーム、四辺/スコア/案内、カードとの非重複、観測値一致、切替後撤去、encoder/audio PID・起動時刻の維持、対象mainとVM現物のSHA-256照合が必要。配布のみ、fixtureのみでは実配信の完了としない。

## Runtime checklist

既存presentation状態に幾何情報、既存bot stateに表示専用観測時刻を追加する。worker/queue/model/providerは追加・変更しない。新表示面は共有overlayの固定route・幾何・readiness契約に登録し、既存shared-overlay diagnosticsでservice healthを確認する。秘密情報/本文ログ/任意execの公開は追加しない。投影・state fencing・期限・共有面・切替の回帰テストを実行する。
