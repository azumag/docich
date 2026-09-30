# 月menuとfieldのscan予算分離 v123

Base: `994521eae512169314b2e073e353aeeede3bb1b6` (v122).

## v121退行の根拠

月menuの無料名簿scanもfield用 `recruit_roster_attempts` を加算し、`_finish` が `house_scan_month/tick` を更新していた。月menuで2回未完了になると、fieldの再試行上限も2になり、その月の卵修理scanを抑止していた。

親からg514第2話4-8 step19361、4-9 step19659での同じ抑止状態が報告された。月menu2回、両attempts count2、recheck=true、house=Noneという状態。本人の実卵破損や名簿全体の不足を推測で変更しない。

## 最小修正

- 月menuは既存 `recruit_month_scan_attempts` のみを消費する。月内2回の有限上限は維持する。
- fieldは独立した `recruit_field_scan_attempts` を消費する。月内2回、200観測の再試行間隔、既存のphase/travel/session上限を保持する。
- 月menuの `_finish` はfieldの終了月/tickを変更しない。field終了はscope付き `house_field_scan` を保存し、field同士の待機/再試行判定に使う。
- 現ランv121/v122の共有counterは、同じ章/月の共有回数から月menu回数を差し引いてfield使用回数を移行する。既にfieldを2回使った状態は上限を保持する。月menu由来か不明な旧終了markerは、残るfield枠を抑止する根拠にしない。fieldの新counter作成後は完全に独立する。
- 章切替で新field counter/終了scopeを破棄する。v122主人公actor保持、v120卵修理の割込み予算/上限理由/receipt、募集の人数・賃金・予備金判断は維持する。

## 回帰と未確認

native pixelの `decide` で月menu→情報menu→実一覧/各本人status→最終行でDown後もcursor不変→未完了で閉じる、を2回実行し、fieldへ戻ると修理scanのXを開始、実ゼウスの破損statusを修理候補として保持することを検証する。月menu3回目を始めず、fieldも2回失敗後に終了し、次月に有限枠が戻る。

検証: `python3 -m pytest -q tests/test_hanjuku*.py` は1577 passed (38.96s)。`git diff --check` と正本handoffからのops brief build/check-sourceも成功。

同章/月の旧counter値（共有2/月menu2、既存field1/2を含む値）からの移行回帰を含む。旧field枠を無条件に0へ戻さない。

現ランの9名/8名scanが未完了になった直接理由は未確定。既存ログ末尾256KiBの限定読取では該当stepを取得できなかった。cursor下端不変などを原因として断定しない。本PRはscan完了の条件を緩めない。

VMはJSON状態/既存ログの限定読取だけ。画像複製・重い走査・新ログファイル・ゲーム入力・pause/restart/save/load・本番tracked編集なし。限定読取で同g514/v122 step19681、第2話4-9の自然進行を確認。修復派遣/支払い/修復成功、自然なv123再開は未確認。
