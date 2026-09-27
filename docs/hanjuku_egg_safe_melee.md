# 半熟英雄の卵リスク制御 — Phase 0（Issue #1180）

白兵戦の最後の通常入力を、敵卵の激突判定で制限する。
`hanjuku-chart-v11-egg-safe` は戦線の座標をまだ観測・校正していない。

| 確認できた条件 | モード | 入力 |
| --- | --- | --- |
| 卵なし、または激突判定なし | `power_mash` | 従来の A 3 frames + release 50 ms を4回 |
| 激突判定あり、敵不明、判定不明 | `egg_safe_hold` | なし |

カードのdue判定、after_clash/after_card、使用確認待ち、低HP時の救命・どうしの退却は、この通常入力より先に処理する。クイーンへの初回A連打は止まるが、自然な衝突で敵HPが減れば従来のafter_clash切り札へ進む。自軍卵・エグモン戦の操作は変更しない。HP 0と欠けたパネルでは入力しない。

## 静的データの根拠

- [ローカル正典README](../games/hanjuku-sfc-speedrun/README.md)の敵卵判定と[第10話](../games/hanjuku-sfc-speedrun/charts/10.md)の押し込み抑制。
- `hanjuku_egg_reference.py` は submodule `5e982942ec24fb559f58b29250560fd784e5ae5c` の `data/char.csv` からSFCのID 0〜127の卵有無・対将軍思考タイプを転記。128行とも[解析将軍表](https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggGeneral.html)と一致することを確認した。SFC外の追加行は使わない。
- [解析資料・敵将軍卵使用思考タイプ](https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggHero.html#GS_EggAI)に従い、0型=判定なし、1型=開幕/壁瀕死/激突、2型=開幕/壁瀕死/壁4倍数、3型=全4判定。壁瀕死は壁ダメージ後の生存HPが話数+2以下。Phase 0では壁接触やダメージを推定しない。
- 自軍城へ攻め込む敵は1型になる。`player_castle_defense` 引数でoverrideし、卵のない敵に卵を付与しない。policyは既存の攻防コンテキストを使用する。場所未分類なら通常/防衛の両方で一致する判定だけを既知とし、安全を推定しない。
- `CARD_IDS` は[切り札表のNo.欄](https://wikiwiki.jp/hjksfc/切り札)の0始まり番号。ブラッキー2 + クースカン13 + ファバード31 = 46で、開幕閾値48未満。既存の対応19種類を訂正する。価格・配列順・ダメージ表は番号の根拠にしない。

これらはruntimeでネットワークやsubmoduleを読む実装ではない。`egg_risk_flags` は敵の**判定能力**であり、戦闘中の卵残数・卵落下・開幕の実所持カード合計を観測した結果ではない。使用済みだから安全という推測も行わない。

## ログと検証の境界

各通常白兵判断の新しい `battle_melee` decisionに `egg_risk_flags`、`melee_control_mode`、`chapter`、`enemy`、`enemy_hp`、`ally_hp`、`a_frames_sent` を追加する。schema 1と既存のdecisionは維持する。

`a_frames_sent` は返却アクションのAフレーム数（0または12）を表す。既存のdecision記録は送信前の計画なので、**実際のキー到達の証拠ではない**。同じ `decision_id` の `action_plan.dispatch_status=planned_not_yet_sent` と実行側の記録を分けて扱う。毎判断記録し、初回の値を後続入力と取り違えない。

Phase 0は敵の卵使用や敗北を完全に防ぐ保証ではない。自然移動、壁接触、開幕カード合計、ボス兵士全滅などの別条件は残る。入力保留で押し負ける可能性もあり、実戦成績は未検証。

## Phase 1とrollback

実画面で接触帯を追跡し、左右・中央の校正とconfidence検証を終えるまで `front_x` や推測したpixel閾値、ASSIST_PULSEを追加しない。解析資料のゲーム内部座標式を画面座標として使用しない。未校正ではPhase 0のholdを維持する。

rollbackはこのPRのコード・テスト・CI・文書変更単位。新たな永続状態の移行、共通配信変更、runtimeサービス変更はない。ただし旧版に戻すと危険な敵への無条件A連打も復活する。PR作成で停止し、マージ・配備・再起動は別途の操作とする。

2026-09-27のPhase 1資料確認では、既存のローカル画像37枚と奥の手検証archive内5枚をparserで分類し、白兵画像を目視確認した。白兵として読めた4枚（`battle-baseline-probe.png`、`battle-capture-win.png`、`battle-fast-result.png`、`battle-live-win.png`）は全てだいじんとの稽古の決着時（片方HP 0）。archiveの5枚は通常/奥の手メニューだった。接触帯の時系列、卵持ちの敵・複数話数での位置、卵使用イベントとの対応を校正できず、Phase 1は未実装。元画像は既存のignored `run/hanjuku-name-evidence/` に保管し、ゲーム画像をPRへ添付していない。新たな本番観測・操作は実施していない。
