# VERIFY-5509 — [半熟英雄] 出撃失敗: ch6 M67c2d1

- 対象: docich#5509（`bug` / `auto-generated` の監査自動起票）/ fingerprint `a38efe096f217e31` / occurrence(incident_id) `004326d5b7509c87a07a`
- 判定: `sortie_failed`（confidence `high`）/ 決定 `situation_held` / strategy_variant `attack_retry_exhausted` / 理由コード `sortie_retry_exhausted`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1492-boss-only-card-miss-eligibility`（= merge commit `cb1e21dc8`「ボス専用札を通常戦の card_miss 監査から除外 (#5499)」で `BOT_VERSION` 一致確認）
- 月: ch6 10-11（観測 206回 / 298.4秒 / max gap 4.3秒 / ticks 84201–84406）
- step `A:cd6fa651:M67c2d1` / issue 表示では将軍・出撃元・対象・目的いずれも不明
- 検証ベース: worktree `/home/hatch/work/docich-5509` detached HEAD `89324e141` = `origin/main`（`BOT_VERSION='hanjuku-chart-v1499-adjusted-stock-fallback'`）。`git diff cb1e21dc8 origin/main -- src/docich/hanjuku/flows/battle.py` は空（v1492 と現行 main で同一ファイル）。month_audit.py の sortie 経路（`_sortie_event`/`_sortie_reason_code`/`SORTIE_TERMINAL`/`_build_sortie_incident`）も v1492→main で不変（差分は boss incident 系 #5375/#5500 と card_miss 系のみ）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH・ゲーム入力なし）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 環境制約メモ

- `gh`・`python3`・`sha256sum` などハッシュ計算・外部 CLI が実行拒否される（VERIFY-5444 と同制約）。issue 本文と incident JSON 全量は公開 GitHub issue ページの webfetch で取得済み。sha256 の再計算は不能のため、式と入力値の照合のみ記録し、再計算を残件とする。

## 結論: **(a) 実欠陥 — ただし検出・発火自体は契約どおり。欠陥は終端 `situation_held` レコードが出撃の同一性（将軍・出撃元・対象・目的）を落とす証拠層**

事象は実在する: step M67c2d1（暫定注文・アルテミス/アルマムーン→イカリア attack）は `world.sortie_history` が裏付ける3度の実測敗北（target=イカリア lost: セルリアク@tick82419・デーメーテール@84081・アルテミス@84406）を経て `retries[step]=3` に達し、設計済みの打ち切りで `failed` 化された（§2）。行軍→突入→戦闘→敗北まで全て実測されており誤検知ではない（§3）。#5444 の発射未観測型とも #5351 の `zero_sortie` とも別層（§5）。

一方で、終端レコード `situation_held`（battle.py L1754）は `chart_step`・`strategy_variant`・`observed_metric={retries,outcome,enemy_hp,ally_hp}` しか持たず、記録時点で `_order_for_step`・`cur` から得られる将軍・出撃元・対象・目的が欠落する。結果として issue は全項目「不明」となり、incident_id の raw も `None|None` に退化して dedup 粒度が落ちる。同一 incident の `world.launched_orders` には完全な同一性が残っている（§3）ため、これは観測不能ではなく**伝播漏れ**である。v1493（#5365、commit `425f1c38a`）が `_finish_order` の failed 経路に同型の補填を入れた（orders.py L2788-2795）が、battle.py の `situation_held` 経路は対象外で、現行 main でも未修正。

## §1 fingerprint / incident_id の式照合

- fingerprint: `_fingerprint`（month_audit.py L3286-3288）= `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。sortie incident は `_fingerprint(mem.chapter, event.kind, [event.kind])`（L1219）→ 理論 raw = **`6|sortie_failed|sortie_failed`** → 期待値 `a38efe096f217e31`。式・入力は issue と一致。**本環境では再計算不能のため未照合**（VERIFY-5528 で別環境が同式 `6|sortie_without_cards|sortie_without_cards` の一致を確認済みの先例あり）。
- incident_id: `_build_sortie_incident`（L1214-1217）raw = `f"{runtime_id}|{generation}|{kind}|{reason_code}|{step_local}|{general}|{target}|{int(now)}"`。issue JSON の `created_at=1795567292.4297726` より `int(now)=1795567292`、`step_local='M67c2d1'`、general/target は §3 の欠落により `None`（f-string で `None` 文字列化）→ 理論 raw = **`g1342-1fe0734c|1342|sortie_failed|sortie_retry_exhausted|M67c2d1|None|None|1795567292`** → 期待値 `004326d5b7509c87a07a`。構成要素は全て既知で外部再計算可能。
- 再発コメント occurrence `747bbd02dbf1ea6d48e9` の理論 raw = `g1342-1fe0734c|1342|sortie_failed|sortie_retry_exhausted|M82dfd8|None|None|<コメント側 created_at>`。fingerprint が `chapter|kind|kind` のみを含む式のため同一 `a38efe096f217e31` となるのは契約どおりで、incident_id は step_local（M82dfd8）と時刻が異なり別値 — 式と整合。

## §2 v1492 で `sortie_retry_exhausted` が起こりうる機構

発火点は `src/docich/hanjuku/flows/battle.py` L1717-1757（v1492 `cb1e21dc8` と現行 `89324e141` で差分なし）:

1. 条件: `outcome == 'loss'` かつ `cur['side'] == 'attack'` かつ step あり かつ `castle not in captured`（自軍の攻撃側敗北を戦闘終了HP表示から実測した場合のみ）。
2. `retries[step] += 1`。`<=2` なら `orders[step]='pending'` に戻し、実在庫があれば開幕イッテツーン override（最大2枚）を付けて `order_retry` を記録 → 再出撃。
3. `retries[step] == 3` で `orders[step]='failed'` + `drop_retired_step_context` + `_record('situation_held', strategy_variant='attack_retry_exhausted')`。
4. 監査側 `_sortie_event`（month_audit.py L1091-1094）が variant `attack_retry_exhausted`/`boss_retry_exhausted` の `situation_held` を `('sortie_failed','failed')` に写し、`_sortie_reason_code`（L1062-1063）が `sortie_retry_exhausted` を返す。docs/operations/hanjuku-month-audit.md L116「戦闘再試行の上限で指示失敗」どおりの契約。
5. 打ち切りの設計根拠: コードコメント（L1749-1751: `launched` のまま残すと終わった出撃が行軍中に見え、後続と計画外判断が待ち続ける）と docs/hanjuku_script_bot.md L393（v198: イッテツーン2枚の再攻撃を2回まで、3敗目で failed 化して計画外へ渡す）。

本 incident では `world.sortie_history` が target=イカリア lost ×3 を裏付け、step が `pending` に戻るたび別将軍（セルリアク→デーメーテール→アルテミス）で再発行された推移と整合する。打ち切られた M67c2d1 は「出撃できなかった」のではなく**出撃して3敗した**点が #5444 型と決定的に異なる。

## §3 決定内訳の整合性と出撃段階の特定（前提訂正: 出撃は開始・到達している）

issue 見出しの「確定 0 / 発射 0 / order_start 0 / order_launched 0」は、`_build_sortie_incident` が直近120秒窓の決定件数（`_read_decisions(runtime, now-120, now)` 由来の counts、L1129-1131）と月次 `month_sorties.launches` を見るためで、この月に確定した新規発射が無かったことを示すに留まる。出撃失敗の証拠ではない。

- step M67c2d1 の実体（incident 自体が証明）: `world.launched_orders['A:cd6fa651:M67c2d1']` = `{general: アルテミス, source: アルマムーン, target: イカリア, purpose: attack}`。`decisions.before` に `attack_observed`（00:41:09Z、将軍 アルテミス、「のりこんだ表示」）→ `battle_start`/`battle_egg_plan`/`castle_defender_seen`/`battle_card` → 00:41:32Z に `battle_result` + `sortie_outcome`（アルテミス）+ `general_location_unclassified`（敗北将軍の所在破棄、battle.py L1639-1647 の loss 経路）+ 終端 `situation_held`。つまり停滞段階は計画・発行・ピッカー・行軍のいずれでもなく**戦闘結果段階**。
- `monster_menu_choice`「いばらのむち」・`experience_result` は別 step `M4df44f`（ココット、暫定 attack→アルブラーカ）の召喚戦に属し、M67c2d1 の失敗と混同する要素ではない（M4df44f は出撃先ピッカー中にこの戦闘が割り込み `order_launched_unconfirmed` となったが、部隊は実際に出て戦闘中 = `launched_unconfirmed(13t)`）。
- 敗北の原因層までは incident から確定できない: `battle_card`「調整チャート既定: 検証済み戦術のない携行切り札を開幕使用」・`battle_egg_plan`・`battle_retreat_banned`/`battle_hero_retreat_open` が記録され、切り札を使って敗北した形跡はあるが、敵編成・HP差の実測は incident に無い。イカリア3連敗が「固すぎる目標への無謀な再攻撃」か「惜敗」かはラン成果物なしには判定不能 — ただし設計上3敗で打ち切って計画外判断へ委ねるのが意図であり、実際に `Maed199: pending`・`M4df44f`・`M82dfd8` が残って戦役は継続している。
- 直前の世界状態: `captured=[]`・`lost=[カバルッサ]`・駐留はアルマムーン2名のみ・所持金149。カバルッサは sortie_history 上 奪取3・失1 の奪い合いで、ch6 の膠着（`battle_grind` シグナル、120秒窓の `battle_*` 決定24件）と整合。膠着自体の戦略的欠陥は本 incident 単体では確定できない。
- 切り札節「出撃確認の実測レシートなし。非携行とは断定できず、自動Issue化しない」は card_miss 系の保守的 fallback で、attack_retry_exhausted 終端レコードにキット receipt が無いことと整合する副次情報（発火とは無関係）。

## §4 owner 再発コメント（occurrence `747bbd02dbf1ea6d48e9`）との異同

- コメント側: `failed / sortie_retry_exhausted / M82dfd8`、月 観測406回 / 571.4秒。`M82dfd8` は本 incident の `world.launched_orders`/`world.sorties` で `セミヨン/アルマムーン→カバルッサ/retake/en_route(108t)`。**別 step・別将軍・別目的の出撃が同じ `attack_retry_exhausted` で打ち切られた再発**であり、同一 fingerprint は fingerprint が `chapter|kind|kind` のみを含む式の当然の帰結。incident_id は step_local・時刻が異なり別値。
- 意味: ch6 10-11 の同月内に攻撃系 step が2連続で retry 上限到達（イカリア attack 3敗 + カバルッサ retake の上限到達）。同一機構の再観測であり別事象ではない。コメント側の完全な incident JSON は未取得（要約のみ）のため、M82dfd8 側の3敗内訳は未確認。

## §5 #5444・#5351 との異同

- #5444（VERIFY-5444、(d) 判定不能）: 決定 `sortie_launch_failed`、理由コード `sortie_launch_not_observed`、misses=3。**発射後の部隊▲・名簿が未観測のまま推定巻き戻し→上限失敗**という発射観測層の事象（そこで将軍・対象は observed_metric に記録されていた）。本件は発射・行軍・突入・戦闘が全て実測され、敗北がHP表示から確定した**戦闘結果層**の事象で、発火機構・理由コード・証拠の質が全て別。
- #5351（VERIFY-5351、(d) 判定不能）: `zero_sortie`（月内出撃ゼロ）、防衛待ちの文脈。kind・fingerprint・機構すべて別。共通点は同一ラン `g1342-1fe0734c`（当時は v1450・ch3）のみ。
- 同じ「出撃失敗」タイトルでも reason_code で層が分かれる: `sortie_launch_not_observed`（発射未観測）/ `sortie_order_*`（発行・メニュー系）/ `sortie_retry_exhausted`（実測敗北の再試行上限）。本件は最後者。

## §6 修正の方向（実欠陥部分）

- `battle.py` の2箇所（boss 側 L1710 / attack 側 L1754）の `_record(mem, 'situation_held', ...)` に、L1667 で既に取得済みの `boss_order = _order_for_step(mem, step)`（`_order_for_step` は `launched_orders` まで fallback する、orders.py L968-984）と `cur` から `general`（`cur['ally']` または `boss_order['general']`）・`source`・`target`（`boss_order['target']`、無ければ戦闘の `castle`）・`purpose` を渡す。v1493（#5365）が `_finish_order` に入れた補填（orders.py L2788-2795）と同型で、`_record`（decision_log.py L11-19）は任意 kwargs をレコードへ載せ、`_sortie_event`（L1102-1105）がそれを拾う既存経路にそのまま乗る。
- 補完の副次効果: incident_id raw の `general|target` が `None|None` から実値になり dedup 精度が上がる、issue の「将軍・出撃元・対象・目的 不明」表示と切り札節の receipt 判定が改善する。
- 代替/追加策: `_sortie_event` 側で `context.world.launched_orders[step]`（本 incident に実在）から back-fill する方法もあるが、ラン側の記録が正になるのが本筋のため battle.py 側を推奨。
- 記録順序は問題にしない: `drop_retired_step_context`（L1753）の後でも `world.launched_orders` は残る（本 incident で実測）。

## 残件

- fingerprint / incident_id の sha256 再計算は本環境では不能。式と入力値は §1 のとおり — 手段のある環境での確認を残件とする。
- イカリア3敗の個々の戦況（敵編成・HP・卵/札効果）と、イカリア・カバルッサ奪い合いの膠着が戦略層の欠陥かどうかは、ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_frames/`（VM 側 `g1342-1fe0734c`）が必要で本検証では到達不能。
- 再発側 `747bbd02dbf1ea6d48e9`（M82dfd8）の完全 incident JSON はコメント要約のみのため、3敗の内訳は未確認。
