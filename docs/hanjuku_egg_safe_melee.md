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

## 保持のHP予算（g530 09:52 防衛戦）

数の上限だけでは保持中の失血が見えない。g530 09:52 ハドリバーグ防衛戦（90 vs シェーブル27、防衛＝退却不可）は7回目の保持で81まで下がった後、数の上限8が81→73の失血を許し、打ち切りの押し込みで発火した敵判定C（モーグリ召喚）を73で迎えて戦死した（死亡時モーグリ残4HP）。

- 保持は開始HP（`start_ally_hp`）に対する失血が `max(3, 開始HP // 10)` に達したら打ち切って白兵へ進む。予算到達で `forced` が立ったら `melee_forced` と同じく戦闘を通しで押し続ける。数の上限 `MELEE_HOLD_LIMIT` は変更しない。
- g530では予算9が81で発火し、73ではなく81でモーグリ戦へ入れる（8HP保存）。与ダメ約10/秒・被弾約3.3/秒の逆算では保存5HPで勝ち切れるため余裕は3HPの僅差であり、実戦成績は未検証。
- `start_ally_hp` か `ally_hp` が整数でない判断は予算を計算せず、数の上限だけに退避する。
- `battle_melee` decisionに `hold_hp_budget` と `hold_hp_bled` を追記する（schema 1と既存fieldは維持）。

## 防衛戦の2行アイテムボックス（g534 07:51 ロックフォール防衛戦）

救済（`_survival_menu`）が観測できない画面では、Bで開いた回数だけ盲目的にBを送り、開いた後は `unknown` として扱ってしまう。

- 防衛戦の人間用コマンドボックスには たいきゃく 行がなく、`たまごをつかう`(y192) と `きりふだ`(y208) の**2行のみ**。3行ボックス（176/192/208）より1行下がり、2行とも無効表示の灰色 (106,105,106)。
- 白い行が残る既存の2行ボックス（`_boss_command_rows`、g508 y192/208・g510 y196/212）は既に `battle_menu` として読める。灰色の2行だけは `hidden_battle_commands`（3行すべて必須）にも `_human_commands`（light行のみ）にも該当せず、`text` も空なので `unknown` になる。
- 実測 g534 07:51 ロックフォール防衛戦（`battle_start` 70 vs 57、`clash_position=false`＝`power_mash` で通常どおり押し込んでいた）: 救済の初回B（07:51:09.4）から白兵復帰（07:51:11.7）までの**3.8秒**、`pending_opens` による盲目的B×3と、メニューオープン中の観測 `unknown` に送られた盲別のAで消費した。その間 どうし 40→22、ロックフォール 57→49。白兵復帰後は 22/49→4/12 まで戻ったがどうしが先に倒れた。同一の交換レート（白兵時 どうし -4.2/秒・敵 -8.7/秒）で40/57の時点から押し続けていたと仮算すると敵が先に倒れ残約12HPの勝利 — **この仮算は未検証**。失ったのは救済そのものではなく「観測できない窓での入力機会」である。
- `Screen.disabled_item_box` を追加。`parse()` の灰色読み取りで2行ペア（192=たまごをつかう / 208=きりふだ、196/212も許容）を検出し、**騎士カーソルを要求する**（灰色だけではメニューと推定しない既存契約を維持）。`classify_text` はこれを `battle_menu` として扱う。`hidden_battle_commands` は再利用しない（`okunote_step` へルーティングされ `down` を送り続けるため。`OKUNOTE_CHOICES` にたまご・きりふだはなく競合しない）。
- 効果: 観測1回で `_survival_menu` → labels空 → `battle_survival_unavailable` + `exhausted` + `pad('b')` が完結し、メニューオープン中の盲別のAと再オープンを止める。救済が実際にある（白い行）場合はもともと `battle_menu` なので変化しない。

## Phase 1とrollback

実画面で接触帯を追跡し、左右・中央の校正とconfidence検証を終えるまで `front_x` や推測したpixel閾値、ASSIST_PULSEを追加しない。解析資料のゲーム内部座標式を画面座標として使用しない。未校正ではPhase 0のholdを維持する。

rollbackはこのPRのコード・テスト・CI・文書変更単位。新たな永続状態の移行、共通配信変更、runtimeサービス変更はない。ただし旧版に戻すと危険な敵への無条件A連打も復活する。PR作成で停止し、マージ・配備・再起動は別途の操作とする。

2026-09-27のPhase 1資料確認では、既存のローカル画像37枚と奥の手検証archive内5枚をparserで分類し、白兵画像を目視確認した。白兵として読めた4枚（`battle-baseline-probe.png`、`battle-capture-win.png`、`battle-fast-result.png`、`battle-live-win.png`）は全てだいじんとの稽古の決着時（片方HP 0）。archiveの5枚は通常/奥の手メニューだった。接触帯の時系列、卵持ちの敵・複数話数での位置、卵使用イベントとの対応を校正できず、Phase 1は未実装。元画像は既存のignored `run/hanjuku-name-evidence/` に保管し、ゲーム画像をPRへ添付していない。新たな本番観測・操作は実施していない。
