# 気象庁 地震XML parser（非放送）

## この単位の範囲

気象庁のJMA電文XMLを1文書ずつ読み、文書の識別情報・発表状態・本文テキスト・
時刻・震度要素の有無を保持する純粋parserを追加した。parserは外部通信、定期取得、
timer、queue、通知、音声、配信設定を持たず、入力XMLから値を返すだけである。
`Control.Status` は `通常` / `訓練` / `試験` / 未知に分類する。取消は `InfoType` と
`Control.Status` を保持し、`InfoType=取消` を取消として識別する。これは放送適格性の
判定ではなく、後段が明示的な規則を決めるための解析結果である。

`Serial` は要素がない場合も空欄の場合も数値を補わない。要素の有無と値を別々に保持する。
EventIDやSerial単独では重複判定しない。同じ発表者・Control.Title・Head.Title・InfoKind・EventIDの
文書について、完全に同じ入力byte列なら重複、発表日時が前の文書より古ければstale、
それ以外でbyte列が変われば更新とする。同じEventID・Serialで本文が変わる公式例を回帰に使う。
XMLの整形差も更新と判定するため、将来canonicalizationを採用する場合はその仕様と
重複キーを別途決める必要がある。

鮮度判定は呼出側が必ず `now` と正の `max_age` を渡す。`ReportDateTime` のみを基準にし、
期限内・期限超過・未来・発表日時なしを返す。既定の鮮度期限は設けず、現在時刻を内部で
読み取らない。本文に震度要素がない取消では、震度を推定または補完しない。

読み上げ原稿は作らない。下流で値を使う前に、運用で対象とする情報種別のallowlist、
必要項目、情報の更新/取消時の扱い、鮮度期限を決める。

## 観測震度と地域の純粋抽出

`extract_jma_observed_intensity_xml` は次の2種の `Body/Intensity/Observation` を読む。
電文コードはXML中のファイル名から読み取らず、下記のControl/Headの組合せで識別する。
`InfoKindVersion` は公式fixtureで確認した `1.0_0` / `1.0_1`、`InfoType` は `発表` / `訂正` を
抽出対象とする。未知の組合せ・version・形態は `unsupported` としてenvelopeを保持する。

| 電文 | Control.Title | Head.Title | InfoKind | 既知の観測MaxInt |
| --- | --- | --- | --- | --- |
| VXSE51 | 震度速報 | 震度速報 | 震度速報 | 3, 4, 5-, 5+, 6-, 6+, 7 |
| VXSE53（近地） | 震源・震度に関する情報 | 震源・震度情報 | 地震情報 | 1, 2, 3, 4, 5-, 5+, 6-, 6+, 7 |

情報全体・Pref（都道府県）・Area（細分区域）のMaxIntを個別に保持する。
PrefとAreaのCode・Name、CodeDefineに示されたコード体系、Reviseも保持する。
`CodeDefine` の `Pref/Code=地震情報／都道府県等` と
`Pref/Area/Code=地震情報／細分区域` を検証し、EEW用の府県予報区コードと混同しない。
コードは文字列のまま保持し、`03` を数値の3に変えない。コード表の全項目との照合や
コードから地域名を検索する処理はなく、XMLにないCode/Nameを推測しない。
Code/Nameの欠損・空欄、同じ地域コードの重複、単数要素の重複はエラーとする。

MaxIntの状態は `known` / `missing` / `unknown` を区別する。前後空白を除いた原文字列を
保持し、既知値だけ `code` を返す。欠落・空欄では原文字列とcodeがNone、未知値では
原文字列を残してcodeはNoneとする。震度0、5弱以上未入電、不明、NaN等を通常の観測震度へ
変換しない。VXSE53ではPref/AreaのMaxIntが省略される公式仕様があるため、親・子の最大値、
Headline、Forecastの値から補完・集計しない。Reviseから震度変化も推定しない。

抽出結果に元の `JmaEarthquakeReport` を必ず付ける。訓練・試験・未知のStatusを保持した
まま抽出し、通常電文として再分類しない。`availability=present` はObservation要素が
あるという意味だけであり、放送・表示の適格性や全数値の完全性を保証しない。
取消は `cancelled` とし、入力に古いIntensityが残っていても観測行を返さない。
対象電文でもIntensity/Observationがなければ `missing`、MaxIntはNone、地域行は空になる。

市町村・観測点の値とCondition、長周期地震動、震源座標・深さ・規模、遠地情報、
EEWのForecast及びリアルタイム震度は今回の対象外。特に市町村の「震度５弱以上未入電」を
地域のMaxIntへ移さない。今回の2電文対応はparserの読取範囲であり、放送対象の選択ではない。

根拠は[JMA公式解説資料の整理表](https://xml.kishou.go.jp/jmaxml_20260826_manual_list.pdf)の
2025-12-17版 `地震火山関連_解説資料.pdf`（[公式資料ZIP](https://xml.kishou.go.jp/jmaxml_20260826_Manual%28pdf%29.zip)）
のⅠ.ヘッダ部、Ⅱ.31-1〜2（震度速報）、Ⅱ.33-4〜6（震源・震度に関する情報）である。

## 固定fixture

回帰fixtureは気象庁の[技術資料とサンプル一覧](https://xml.kishou.go.jp/tec_material.html)に
掲載された2026-09-17版[公式JMA XMLサンプル](https://xml.kishou.go.jp/jmaxml_20260917_Samples.zip)
から、そのまま複製した歴史的文書である。fixtureの出典、元ファイル名、coverageは
`tests/fixtures/jma_earthquake/README.md` に記録した。いずれも現行速報ではなく、テスト以外で
イベント通知に利用しない。

回帰は、訓練＋空のSerial、配信試験、EventIDとSerialを保った本文更新、震度要素のない
取消（予報・警報・地震動予報・震度速報）、明示的な鮮度境界、発表日時なし、壊れたXML・
他namespace・地震以外の文書を対象にする。観測震度の4公式fixtureを加え、先頭0を持つ
地域コード、親子の個別MaxInt、予測値やHeadlineからの補完禁止、欠落/未知値、訓練/試験、
更新と鮮度の保持も確認する。否定回帰は公式fixtureをメモリ上で変形したテストであり、
実在した別の公式電文と主張しない。既存CIの専用pytest stepがこのテストファイルを実行する。

## 次のgate

1. 独立レビューでPref/Area抽出、観測値の欠損・未知・Status保持、envelopeの既存契約を確認する。
2. 市町村/観測点・Condition・予測震度を扱う場合は、それぞれの原情報を区別する契約を別途レビューする。
3. 運用上の対象電文、対象地域の選好、放送閾値、表示項目、鮮度上限、未来時刻と訓練/試験/取消の下流動作を決める。
4. 取得・timer・通知/音声・配信・本番設定への接続は別の変更単位とし、独立レビューと承認の後に扱う。

このPRでは、外部取得・放送・配信・本番の実行確認を行わない。
