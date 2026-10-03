# 半熟英雄の卵リスク制御 — Phase 0（Issue #1180）

白兵戦の最後の通常入力を、敵卵の激突判定で制限する。
`hanjuku-chart-v11-egg-safe` は戦線の座標をまだ観測・校正していない。

| 確認できた条件 | モード | 入力 |
| --- | --- | --- |
| 卵なし、または激突判定なし | `power_mash` | 従来の A 3 frames + release 50 ms を4回 |
| 敵の卵を落として召喚不能になった（`enemy_egg_dropped`） | `power_mash` | 同上。ぶつかり合いの青ゲージのA連打を消費する |
| 激突判定あり、敵不明、判定不明 | `egg_safe_hold` | なし |

カードのdue判定、after_clash/after_card、使用確認待ち、低HP時の救命・どうしの退却は、この通常入力より先に処理する。クイーンへの初回A連打は止まるが、自然な衝突で敵HPが減れば従来のafter_clash切り札へ進む。自軍卵・エグモン戦の操作は変更しない。HP 0と欠けたパネルでは入力しない。

## 静的データの根拠

- [ローカル正典README](../games/hanjuku-sfc-speedrun/README.md)の敵卵判定と[第10話](../games/hanjuku-sfc-speedrun/charts/10.md)の押し込み抑制。
- `hanjuku_egg_reference.py` は submodule `5e982942ec24fb559f58b29250560fd784e5ae5c` の `data/char.csv` からSFCのID 0〜127の卵有無・対将軍思考タイプを転記。128行とも[解析将軍表](https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggGeneral.html)と一致することを確認した。SFC外の追加行は使わない。
- [解析資料・敵将軍卵使用思考タイプ](https://triplequotation.web.fc2.com/Analyze/SFC_EggHero/EggHero.html#GS_EggAI)に従い、0型=判定なし、1型=開幕/壁瀕死/激突、2型=開幕/壁瀕死/壁4倍数、3型=全4判定。壁瀕死は壁ダメージ後の生存HPが話数+2以下。Phase 0では壁接触やダメージを推定しない。
- 自軍城へ攻め込む敵は1型になる。`player_castle_defense` 引数でoverrideし、卵のない敵に卵を付与しない。policyは既存の攻防コンテキストを使用する。場所未分類なら通常/防衛の両方で一致する判定だけを既知とし、安全を推定しない。
- `CARD_IDS` は[切り札表のNo.欄](https://wikiwiki.jp/hjksfc/切り札)の0始まり番号。ブラッキー2 + クースカン13 + ファバード31 = 46で、開幕閾値48未満。価格・配列順・ダメージ表は番号の根拠にしない。
- `CARDS` は gcgx の [kirihuda](https://gcgx.games/hanjuku/kirihuda.html) と wikiwiki 切り札表が一致する**全32札**を保持する（owner 2026-10-03）。各件は `id` / `general_damage` / `monster_damage` / `boss_damage` / `soldier_damage` / `egg_drop` / `price` / `effect`。`EGG_DROP_VALUES` はそこから導出する。旧`CARDS` にあった将軍戦ダメージの未使用10件（ダイチスイム・ブラッキー・フットバース・グリンボー・ノリウツール・クースカン・ゼンマイン・ファバード・マグネガキン・ミックミー）は正典値へ置き換えた。

これらはruntimeでネットワークやsubmoduleを読む実装ではない。`egg_risk_flags` は敵の**判定能力**であり、戦闘中の卵残数・卵落下・開幕の実所持カード合計を観測した結果ではない。使用済みだから安全という推測も行わない。

## 卵ディニアル計画と青ゲージ（2026-10-03）

将軍（最大HP）と切り札（卵落・将軍戦ダメージ・ID）を内部データとして組み合わせ、戦闘ごとに「卵を使わせない／落とさせる」方針を先に決める。`_egg_plan` は戦闘開始時に次の項目を算出し、**卵を使える能力（`threat`）が真の敵だけ** `battle_egg_plan` に記録する。

- `carried` と `id_sum`: 実際の携行札（`card_override` / `rare_card_kit` / `strong_card_kit` を含む）とそのID合計。合計47以下で開幕卵を抑止する（`deny_opening_egg`）。
- `max_hp_sum` と `threshold`: `general_max_hp(enemy) + ref_ally_hp`。`卵落 > max_hp_sum mod 16` の札だけを `droppers` とする。
- `dropper`: 指揮できる**唯一**の卵落札。同値なら将軍戦ダメージが小さい札を選び、主力のダメージ札を温存する。
- 出撃時の追加携行は**変更しない**（出撃時敵の最大HPが未判明のため `max_hp_sum` が算出できない）。

`_egg_drop_tactics` は `*_tactics`（チャート）より**後**、`*_strong_card_tactics` より**前**に評価する。つまりチャートがその札を指す戦闘では卵ディニアル側は発火せず（`charted` 集合で除外、HPゲートと after_clash を壊さない）、チャートが持たない札だけを開幕に使う。tactic の `tactic_id` は `eggdrop:{step}:{card}`、`egg_drop_only=True`、`open=True`。

落下の証拠は `fast_chain` と同じ水準で、**「選択済み ＋ 選択後の敵HP低下 ＋ 敵が生還」の3点**。`_watch_egg_drop`（選択前HPを記憶）→ `_egg_drop_confirm` → `battle_egg_dropped` → `cur['enemy_egg_dropped']=True`。HP 0（倒れた）や根拠がない戦闘では確定しない（fail-closed）。`_survival_card_list` が救済で卵落札を選ぶ時も同じ監視を始める。

`enemy_egg_dropped` が真の時だけ `_melee_step` の `safe` が成立し、`power_mash`（A 3 frames + release 50 ms を4回＝12 A frames）へ移る。reason に「敵の卵を落として召喚を封じたため、青ゲージをA連打で消費して押し込む」を追記し `enemy_egg_dropped` を `battle_melee` に残す。`_unarmed_clash_risk` も同フラグで外す。**青バーそのものの画素検出は未実装**（実機計測待ち）で、現状は卵の脅威が消えた局面だけを予測で全力A連打する。これと出撃時の dropper 携行拡張は follow-up Issue に分離する。

## 自軍卵の温存（兵士数も判定、2026-10-03）

オーナー依頼「たまごつかわなくても勝てそうな相手にもガンガン使っていてもったいない」。確認済みの前提は **判定＝HPだけでなく兵士数も見る／兵士数の読み取り＝戦闘中の兵士スプライト計測**。

`_own_egg_needed(mem, battle, general_reading)` が `egg_battle_step` の先頭で要る/不要を決め、`mem['egg_needed']` に保持する。**次の3条件すべて**が成り立つ時だけ卵を温存する（片でも欠ければ従来どおり `use_egg`、fail-closed）。

1. 強い将軍ではない（`_strong_enemy`、shogun.html の評・ボスは恒 True）。
2. 白兵の合戦力が敵の **7割超**。合戦力 = `HP + 10×兵士`（gcgx battle.html「兵士は1人ずつHP10を持っている」）。兵士は**両側が整数で読めた時だけ**加算し、片側欠ければ HP 比較へ退ける（`rule='soldier_force' / 'hp_only'`）。
3. 自軍のHPが **最大の7割超**（`general_reading=(hp, max)`、`ally_wounded`）。30/82 のように自軍が大破していれば敵将軍の絶対値HPが低くても温存しない。読み取れない時も温存しない。

- **比較対象は敵将軍（`battle['enemy_hp']`）**。召喚モンスターHPは含めない（実測149〜240に対し自軍最大82で、温存が一度も成立せず課題が消えるため）。`tests/test_hanjuku_survival.py` の「monster HP is not the enemy general's」と同じ方針。
- **兵士スプライト計測**（`hanjuku_screen._field_soldiers`）: `SOLDIER_BAND=(0,40,256,150)` の背景上位4色を**完全一致**（tolerance なし）で除き、8連結BFS。**上端・左端・右端のみ**エッジ除外（下端はボックスに切られる兵士を残すため除外しない）。1体ぶん＝面積55..260かつ bbox 26x26以内。側ごとに面積261..900 かつ 60x60以内の成分（＝重なり）が出たら**その側を `None`** にして判定側へ渡さない。返り値は `(味方, 敵)`＝**左/右**で、HPパネル（敵が左）と**左右反転**する。誤差は ±1〜2 体で、7割基準には30%の余裕がある。スプライト色は将軍・兵種・アニメで変化し両側に出るため、**側の判定には位置（centroid x < 128）だけ**を使う。
- 読み取りは `screen.battle is not None` のフレームのみ（`egg_battle_menu` / `monster_menu` では `_battle()` が `None`＝上書き禁止、直近の通常戦闘フレーム値を保持）。実測で `_field_soldiers` 本体 ≈11.7ms。
- **途中昇格は一方向**: 温存中に条件を崩すと `mem['egg_action']` を `use_egg` へ切り替え `egg_battle_use_egg` を記録。回復しても `attack` へは戻さない。
- **g460 の退却ゲート**: 温存中（`egg_needed=False`）は先に白兵で戦い、退却を試さない。卵が必要で札も卵も無い局面だけ従来どおり B（1戦闘1回、防衛戦不可）。
- 既存の「HPが敵の7割超なら温存」単独では、30/82 が敵29を上回るため**大破中でも卵を温存する**結果になる。オーナー確認（2026-10-03）で**条件3を追加して解消**し、`test_fierce_preserves_available_egg_cards_and_requires_known_cursor` を含む既存テストは意図的な変更なし。
- 検証: 合成Canvasの編隊描画（`tests/test_hanjuku_egg_hold.py` 全11件）と実機フレーム12枚（味方6・敵1など、`egg_battle_menu` は計測しない）。実機での連続運用成績は未検証。

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
