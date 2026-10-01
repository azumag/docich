# 終了済み実画像による初回baseline（2026-09-29）

固定Issue #1339へのChatGPT GitHub connectorコメントから、Actionsの候補一覧・明示IDの
export・artifact downloadまで実測した。ユーザーの鍵操作・ゲーム入力・配信/音声操作は不要。
認識器の基準はmain `7a40e340ae4ac72e5d2ef424fe2521a77b6e2161`。認識器の変更は行っていない。

## 取得と出典

| 用途 | Runtime | Actions run / attempt | Artifact ID |
|---|---|---|---|
| 候補一覧 | — | [36558036323 / 1](https://github.com/azumag/docich/actions/runs/36558036323) | 11028535697 |
| 調整用baseline | g464-4425b813 | [36558217979 / 1](https://github.com/azumag/docich/actions/runs/36558217979) | 11028416035 |
| 別ラン確認用baseline | g460-6041c5e9 | [36558226839 / 1](https://github.com/azumag/docich/actions/runs/36558226839) | 11028401113 |
| 停止局面調査 | g462-a1a34ce8 | [36558740739 / 1](https://github.com/azumag/docich/actions/runs/36558740739) | 11028331841 |

artifact ZIP digest、manifest、終了identity、各PNG SHA-256、RGB SHA-256を照合した。
1日artifactなので上記ダウンロードの永続性は保証しない。代表11枚の原PNG、目視ラベル、
出典のarchive/PNG/RGBハッシュを `ops/vm_actions/tests/fixtures/hanjuku/` に固定した。
ユーザーの判断に従いゲーム画面は平文で扱い、ROM/セーブ/認証情報は含めない。

## 固定Issueだけによる最終受入

結果の自動返信を追加した [#1347](https://github.com/azumag/docich/pull/1347) は
main `670526b92997dad80e4475bcc40ee3ff87047b90` に統合され、
[canonical deploy 36560363615](https://github.com/azumag/docich/actions/runs/36560363615) が成功した。
このmainで再度connectorからコメントし、Issueのbot返信に載ったIDだけで
artifact一覧取得・downloadを完了した。ユーザーによる鍵やCLIの操作はない。

| 操作 | Issueの結果返信 | Actions run / attempt | Artifact ID |
|---|---|---|---|
| list | [5889105738](https://github.com/azumag/docich/issues/1339#issuecomment-5889105738) | [36560489716 / 1](https://github.com/azumag/docich/actions/runs/36560489716) | 11029449695 |
| export g464-4425b813 | [5889129606](https://github.com/azumag/docich/issues/1339#issuecomment-5889129606) | [36560620508 / 1](https://github.com/azumag/docich/actions/runs/36560620508) | 11029474945 |

両runの取得・検証・返信・後片付けは成功。export artifactのSHA-256は
`c7b284fd91365ce1e4bcd36bccc4090dc7cc9f2fb91d9a762641c660544d441c`、
内側evidence ZIPは `3356d4ac2ac2ce406bb2c6740f214ba49bfcf985bfa8b200f91571a1c618e632`。
検証済みのZIPは初回baselineで使ったものとバイト単位で一致した。

## baselineの結果と限界

3ランで623画像（g464: 198、g460: 239、g462: 186）を取得した。
g464のRGB重複除去後183画像から6件ごとに31画像、g460の202画像から7件ごとに29画像を
選択した。g464の1枚は未ラベルとし、合計59画像に105項目の目視正解を付けた。
小さい一覧だけでは札名・手カーソルの行を読み違えるため、差の出た文字は原画像を
nearest-neighborで3〜6倍に拡大して再確認した。parserの予測から正解を生成していない。

| ラン | 採点画像 | 採点項目 | 表示値一致 | 不在一致 | 誤った値 / 偽検出 / 保留 |
|---|---:|---:|---:|---:|---:|
| g464 | 30 | 46 | 25 | 21 | 0 / 0 / 0 |
| g460 | 29 | 59 | 40 | 19 | 0 / 0 / 0 |
| 合計 | 59 | 105 | 65 | 40 | 0 / 0 / 0 |

採点対象はkind・selected・将軍名・HP。hand/cursor/marker/menu_cursorの座標精度を網羅した
結果ではない。合計は画像単位ではなく項目単位。ランを通した認識率・勝率を意味しない。
画像リングは全履歴を保存しないため、g464のログ参照1912件、g460の3582件のRGB SHAには
対応画像が残っていなかった。画像のない局面の正否や頻度は推測しない。

ラベル訂正を隠さないため、次を記録する。g464 `decision-102` の「ハリケーン」は
拡大画像で「ノリウツール」と確認。g460 `decision-000/021` は末尾の「ン」を見落とし、
正しくは「クースカン」。`decision-042` は「フットバース」、`decision-107` も
手が指しているのは「フットバース」だった。最初の不一致5項目はいずれも転記ミスであり、
認識器の改善前後差として数えない。

この標本から、全画面の手色集約や固定x offsetの変更を正当化する誤認識は確認できなかった。
認識閾値やROIを変更せず、今後の変更が今回確認できた読み取りを壊さない実画像回帰を追加した。
別ラン確認用データを変更の調整に再利用する場合は、次の独立した確認ランを別に取得する。

## g462の停止局面

終了RGB SHA `b404246030ca4b38f9725694fe956b4afbd8ec3889703dadac531f88ebdab8ae` は
`frame-100/101/103/104.png` と一致した。画面は「これいじょうのぞうちくはできませんぞ!!」と
「どのしろをぞうちくなさいますか?」、選択は「アルマムーン」。ログ末尾には同じRGB SHAへの
Aの `action_plan` と実際の `input_sent` があり、300.128秒後に `screen_stalled` で終了した。
当時のbot記録はv79/v80である。

現行v81にはこの同一事象の修正[#1345](https://github.com/azumag/docich/pull/1345)が既に含まれる。
現行parserも城名と復帰判定に使う文言を読めることを実画像回帰で確認した。
本調査は入力を送信していないので、実ゲーム上のB退出・復旧成功の再実測とは扱わない。
