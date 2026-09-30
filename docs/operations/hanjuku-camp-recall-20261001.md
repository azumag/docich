# キャンプ帰還のカーソルと送信証拠 v124

Base: `8cb955e7b5137c17dcbb50619d7b574e854d0939`。v123のscan境界、v122の主人公actor、v120修理予算、容量gitlink631a268を保持。

## 確認した欠陥と証拠

保存済みg514/gen514の13314〜13317、13472〜13475はtext画面でDown3→A。menu内の文字存在だけを条件とし、カーソルを照合していなかった。13318/13476のreasonとA/wait700/Aは`world_map_step`の帰還専用dest分岐に一致する。y_jump_openは13320の次の攻撃指示。旧コードはA計画時にrecallを消し、hero経路で旧sortieをrecalledへ変更していた。

既存input_sentは指示送信の証拠であり、本人・受理・到着の証拠ではない。g514のmenu原画像は保持されていない。既存2026-09-29孤立probeの`recall-open.png`は実R ring付き全島picker。RGB SHA-256 `b15b5935f9af5777fc82607b84f8483457494398ce3e52a456fcdb3f60a4e5a2`。純粋再生でworld_map、実cursor(127,150.5)、アルマムーンown、既存A/wait700/Aを確認。g514原画像と混同しない。既存実`hm-menu.png`も手cursorからmenu_toが動くが、これは2項目の城menuでキャンプmenu全体の正解画像ではない。

## 最小変更

- 完全なcamp menu文字と実handから既存menu_toで1入力ずつ動かし、実きかん位置だけA。保存downsは確定根拠にしない。hand未知3回は既存B退出、全stageの既存90観測上限維持。
- selector種類・座標・自軍旗条件・既存入力手順は維持。A計画はawait_dispatchへ記録し、主人公旧出撃をrecalledや帰還en_routeへ推測更新しない。帰還pickerを別chart出撃のtarget遷移に使わない。
- command botは既存eventsの末尾64KiBだけを読み、現在identityと保存request_traceの一致、同decision_id/実画面SHA、予定後timestamp、実A入力を照合する。旧cached controllerが既に書くinput_sentを使い、新controller反映・restartは不要。重複行・別ID/画面/lease/世代・古い時刻・symlinkは確認根拠にしない。
- 送信待ちは最大6観測。map帰還と送信記録でも受理/到着を断定せず、dispatch_unconfirmed又はarrival_unconfirmedとして通常操作へ戻る。帰還直後のunknown/yes_noに追加Aを出さない。再検出抑止400観測はキャンプだけ、通常移動/戦闘/修理を止めない。
- 次の必要cursor/request/未確認画面は既存decision-000〜119のringだけへ保存。新ログ領域・画像領域・常駐監視は作らない。

## 検証と残件

同じcursorの未反映Downを確定に使わない、未知hand有限退出、実picker/自軍旗、実senderの2入力照合、旧/別identity/画面/時刻/重複/読み取れないtail、誤った旧hero/status更新、別chart出撃と混同しないことを回帰。半熟全1592passed39.40s。最後のreaderをstrict64KiBにした変更は関連217回帰成功(8.86s)、巨大な先頭/部分行とsymlinkを含む。

実将軍本人、指示受理、移動、入城は未確認のまま。`recall_verification`は到着を主張しない単一の未確認記録であり、占有する操作FSMではない。g514の自然menu/dispatch/arrivalと先発隊3名・速度改善は残件。house.py/卵修理入口/共通配信/controller/watchdogは変更せず、VMは読取だけ、ゲーム入力やresetなし。
