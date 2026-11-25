# VERIFY-5517 — [半熟英雄] 出撃失敗: ch4 どうし

- 対象: docich#5517（`bug` / `auto-generated` の監査自動起票）/ fingerprint `2b7fd9309674d14f` / occurrence(incident_id) `7f955372c26651a4b542`
- 判定: `sortie_failed`（confidence `high`）/ 終端決定 `order_failed` / 理由コード `sortie_order_failed` / strategy_variant `chart_adjusted` / misses None
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1494-card-open-bounded-audit`
- 月: ch4 4-11（観測 1221回 / 256.6秒 / max gap 1.3秒 / ticks 109121–110341）
- step `4-B3` / 将軍 どうし（主人公、チャート上の `HERO`）/ 出撃元 フヌイヌム / 対象 四季王城(北東) / 目的 不明
- 理由: 代役待ち上限後も主人公が出撃元にいない
- 検証ベース: worktree `docich-5517` detached HEAD `d45197594`（`BOT_VERSION='hanjuku-chart-v1501-retreat-cards-unclassified'`）。観測版 v1494 は `0ecb80718`（bump）＋ `7254c5328`（main 取り込み・allowlist 整合）の系。`git diff 0ecb80718 HEAD` で `flows/deploy.py` は**差分なし**、`strategy/orders.py` は `_finish_order` の同一性補填（`425f1c38a`、#5365/#5382）のみ、`month_audit.py` の sortie 経路（`_sortie_event`/`SORTIE_TERMINAL`/`_sortie_reason_code`/`_track_sortie`/`_build_sortie_incident`/`_fingerprint`/`_incident_id`）は不変 — 現行 main の該当コードがそのまま観測版の機構を表す
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH・ゲーム入力なし）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 環境制約メモ

- VM worktree では `python3`・ハッシュ計算コマンドが実行拒否されたため、当該レーン単独では再計算できなかった。後段のレビューゲートで独立に SHA-256 を再計算し、下記の期待値との一致を確認した。
- issue 本文・incident JSON 全量は公開 GitHub issue ページの webfetch で取得済み（`world.garrison`・`world.sorties`・`launched_orders`・`sortie_history`・`decisions.before` まで確認可能）。ラン成果物（VM 側 `hanjuku_decisions.jsonl`/`hanjuku_frames/`）は未参照。

## 結論: **(b) 設計どおり — 検出・発火・取消の全段が契約どおりであり、「主人公が出撃元にいない」は実一覧の実測。世界状態（適格代役が他サイトウェーブに全投入・主人公は本城在城・出撃元在城者はサイト適格を満たさない）が取消を裏付ける**

発火は誤検知ではないが、下層に実欠陥も見つからない。`order_failed` はボス注文の設計済み有界フォールバック（代役待ち上限 → 実一覧で主人公在席を再確認 → 不在なら安全側取消）で、incident の世界状態が「実際に出せる攻撃手がこの時点で存在しなかった」ことを裏付ける。監査が攻撃系 `order_failed` を `sortie_failed`（high）として起票する契約とも整合し、#5509 型（終端 `situation_held`・同一性欠落）とも #5365 型（purpose 解決ギャップによる誤起票）とも別である。

## §1 fingerprint / incident_id の式照合

- fingerprint: `_fingerprint`（`month_audit.py` L3286-3288）= `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。sortie incident は `_build_sortie_incident` が `_fingerprint(mem.chapter, event.kind, [event.kind])`（L1219）を呼ぶため、理論 raw = **`4|sortie_failed|sortie_failed`** → 期待値 `2b7fd9309674d14f`。独立再計算 `sha256(raw)[:16] = 2b7fd9309674d14f` と一致。
- incident_id: `_build_sortie_incident`（L1214-1217）raw = `f"{runtime_id}|{generation}|{kind}|{reason_code}|{step_local}|{general}|{target}|{int(now)}"`。issue JSON の `created_at=1795570588.73522` より `int(now)=1795570588`、`step_local='4-B3'`（`step` に `:` が無いため `rsplit` でそのまま）、`general='どうし'`、`target='四季王城(北東)'` → 理論 raw = **`g1190-ch1v9|1190|sortie_failed|sortie_order_failed|4-B3|どうし|四季王城(北東)|1795570588`** → 期待値 `7f955372c26651a4b542`。独立再計算 `sha256(raw)[:20] = 7f955372c26651a4b542` と一致。
- issue の「occurrence」表記は `render_comment`/`render` が `record['incident_id']` を出す箇所（L4226/L4259/L4212）と整合 — incident_id そのもの。
- `misses: None` は `observed_metric.misses` を持たない `order_failed` レコードで `_bounded_int(None, …)` → None となる契約どおりの値（L1119）。

## §2 v1494 で `boss_companion_timeout → order_failed` が発火する機構

発火点は `flows/deploy.py` L2462-2473（v1494↔HEAD 同一ファイル、ボス注文の `general_list` 分岐）:

1. `present = _listed_generals(screen, mem, order)`（実一覧。歩行スキャン済みなら全員、そうでなければ可視行）から `candidates = _order_hero_alternatives(order, mem, present)` を計算。
2. `candidates` が空のボス注文は `_boss_companion_wait_expired`（`orders.py` L778-808、`BOSS_COMPANION_WAIT_TICKS=200`）で有界待機。未経過なら `_hold_guarded_sortie` が `boss_companion_required` を記録して `[B,B]` で閉じる（`deploy.py` L1848-1865）。**期限は step ごとの共有デッドライン**で、一覧を閉じてもリセットされない（docstring「a timeout must survive the general-list backout」）— 本件の `boss_companion_required`×3 → `boss_companion_timeout` はその設計どおりの経過。
3. 上限到達で `boss_companion_timeout` を記録（L804-807、issue の理由文と逐語一致）した上で実一覧を再読し、`NAME`（=`chart.HERO`='どうし'、`os_layer/world.py` L18）が `present` に無ければ `_finish_order(mem,'failed', deviation_reason='代役待ち上限後も主人公が出撃元にいない', …)`（L2467-2472、issue の理由文と逐語一致）。
4. `_finish_order`（`orders.py` L2775-2813）が `orders[step]='failed'` + `_record(mem,'order_failed',…)` を出し、`failed_local_step` で同一内容の即時再発行を止める。終端レコードの `general`/`target` は `_order_for_step` で解決した 4-B3 のチャート値（`hanjuku_chart.py` L708-711: `general=HERO, source=フヌイヌム, target=四季王城(北東), after=('all_captured',)`）。
5. 主人公が実一覧に**居た**場合の設計済み分岐も同じ塊にある: `fallback_hero=True` → 卵・修復・bound の安全条件を通れば `boss_companion_hero_fallback`（単独ボス出撃、L2521-2524）、通らなければ別理由の `order_failed`（L2483-2491）。つまりこの箇所は「代役がいなければ主人公単独」を**試みるが、主人公の在席を実一覧で要求する**二段構造。

計画層の上流にも同じ有界化がある: `_ready`（`orders.py` L885-915）が `_boss_companion_source`（L170-216、ch4 サイトは「近距離帯で適格な卵持ち代役が記録された城」を出撃元に選ぶ）を評価し、適格ソース無し・駐留記録ありなら `_boss_companion_source_wait_expired`（L814-845、200 ticks）で待ってから dispatch へ進む。`_follow_general`（L2273-2306）は `_boss_companion_source` が見つかれば `source_override` で出撃元をその城へ向け直し、見つからなければ古い override を捨ててチャートソース（フヌイヌム）に戻す — 本件は後者に該当。

`select_centered` → `castle_jump` との整合: `select_centered`（`map.py` L1240-1257）は SELECT が**主人公の城**へカーソルを中央揃えした実測で、観測者の位置確定。`castle_jump_open` の理由文「Xメニューの『しろ』からSELECTで飛ぶ」は hero_select ではない経路 — `_hero_select_jump_allowed`（`map.py` L1530-1546）は `_hero_castle(mem) == source` を要求するため、主人公がフヌイヌムに居る記録なら選ばれない。X→しろ 経路が選ばれた時点で主人公は別城（記録上アルマムーン）と認識されており、`boss_companion_timeout` での実一覧照合と矛盾しない。

## §3 「主人公が出撃元にいない」は実測の事実か

**実測の事実。** 3点の独立した証拠が一致する:

1. **incident の `world.garrison`（実一覧読みの記録）**: `フヌイヌム=[シェーブル, ラズベリー]`、`アルマムーン=[どうし]`。主人公の在城記録は本城側。
2. **実一覧の直接読み**: `NAME not in present` の `present` は `_listed_generals` = 画面の出撃一覧そのもの。ボス注文では `_planned_general_on_page` が `None` を返す（`deploy.py` L1090-1092）ため歩行スキャンが計画将軍のページで早期終了（`end='named'`）せず、`wrapped`/`stalled`/`limit` まで全走査を要求される。直前の保持ガード（L2453-2457）も `present is None`/重複/未測定名を排除するため、ここに到達した読みは一貫した一覧。
3. **ナビゲーション経路の傍証**: X→しろ の castle_jump（hero_select 不成立）＝主人公が出撃元に居る記録が無いことと整合。`arrival_unverified`・`castle_jump_confirm` はどちらも**カーソル位置**の話であり、主人公の所在とは別系列（castle_jump はカーソルを城メニューへ運ぶ機構で、将軍を移送しない）。`castle_jump_confirm` は `castle_verified` を立て「開いたメニューがフヌイヌムのもの」と照合済みにするだけで、誤判定の根拠にはならない。

残余の理論的可能性（`end='limit'` での走査打ち切り、または `set(present) ⊄ scan['names']` 時の可視ページ fallback）では主人公が未読ページに居た偽陰性があり得るが、`world.garrison` の記録と実一覧が一致しており、フヌイヌム在城2名はページ打ち切りの条件を満たさない。実害の可能性は極めて低い。

**取消の根拠側も実在**: サイト注文の代役資格は厳格（`_order_hero_alternatives` L76-78: 卵所持＋`hero_guard_short` 不可＋「主人公在席か卵持ちがもう1人残る」、さらに `fit`/`strong` フィルタ）。フヌイヌムの シェーブル/ラズベリー が `candidates` に入らなかったのはこの規則の適用結果で、全自城を走査する `_boss_companion_source` も `None` を返した（＝記録上どの城にも適格ペアが無い）。`world.sorties`/`launched_orders` が理由を裏付ける: 適格な卵持ちの ヘパイストス（4-B1→南西、arrived 1558t・戦闘中）と レモン（4-B2→南東、en_route 378t）は他サイトウェーブに投入済みで、残る在城者はサイト適格を満たさない。**この時点で実際に出せる攻撃手は存在しなかった**ため、取消は設計所望の安全側挙動（弱い/卵なしをサイトへ送らない、主人公をわざわざ出撃元まで動かさない）。

## §4 #5509 / #5365 との異同（終端型の切り分け）

| | 本件 #5517 | #5509 | #5365 |
|---|---|---|---|
| 終端決定 | `order_failed`（deploy 層取消、戦闘未到達） | `situation_held` + `attack_retry_exhausted`（実測3敗の打ち切り） | `order_failed`（`_move_general_pick` の spare 判定） |
| reason_code | `sortie_order_failed`（文言非一致の既定） | `sortie_retry_exhausted` | `sortie_no_spare` |
| 同一性 | 全項目解決（将軍・出撃元・対象あり、目的のみ不明） | 全項目「不明」に退化＝**欠陥** | purpose 未解決→move 除外をすり抜け＝**欠陥** |
| 型 | order_failed 型 | situation_held 型 | order_failed 型 |

- **#5509 とは別型・別欠陥域**: #5509 の実欠陥は battle.py 終端 `situation_held` レコードが同一性を落とす点で、本件は `order_failed` 終端かつ #5365 系修正（`425f1c38a` の `_finish_order` 補填、v1494 merge 系に搭載）＋ `_context_order` 経路で同一性が埋まっている。incident JSON の `sortie_event` が general/source/target を持ち `context_missing:false` なのが証拠。**終端 `situation_held` 型ではない**ため #5509 の家族には入らない。
- **#5365 とは「目的=不明」の扱いが異なる**: #5365 は `purpose='move'` の失敗が purpose 解決失敗で除外をすり抜けた起票層欠陥。本件はチャート注文（`hanjuku_chart.py` の 4-B3）に `purpose` フィールド自体が存在せず、「未指定は攻撃扱いで起票」は #5365 §6 が予告した設計どおりの挙動。ボス攻撃が起票されるのは監査契約上正しい（`SORTIE_NON_PURPOSES={move,house,repair,recall,defend}` に該当しない）。
- incident_id 式は #5509 と同型で、`general`/`target` が実値（`どうし|四季王城(北東)`）に入るため dedup 粒度も保たれている。

## §5 #5448 / #5437 / #5452 との異同・`order_dispatch_starved` の関係

- **#5448（boss_month_zero_sortie ch12、(a)・v1483 修正済み）**: kind・章・機構すべて別。あちらは暫定ボス行軍が吸収・野営化して月内ボス出撃ゼロになった停滞で、本件は月内に確定出撃 1・発射 2（4-B1/4-B2 ウェーブが実際に行军中）の通常の発射環境。v1483 の staging 機構は ch12 限定で ch4 サイトの代役/ソース機構（`_boss_companion_source`/`site_companion_reserve`/`_rearm_failed_site_steps`）とは別系。カバー範囲に入らないが、必要もない。
- **#5437/#5452（camp_recall_unconfirmed 系、(a)・#5452 は 0193891d3 で修正済み）**: 帰還 dispatch 検証窓の欠陥で、出撃発行層の本件と機構を共有しない。#5452 修正の diff（map.py 17行、いどうモード喪失）は本件経路に無関係。
- **`order_dispatch_starved` との関係**: `orders.py` L2125-2170、`ORDER_DISPATCH_STARVE_TICKS=60`。ボスフェーズで pending 指示が 60 tick 超 `order_start` に届かなかった記録で、ボスフェーズ限定で前景ワークを1回譲る設計（#5077/#5125）。本件では記録直後に `order_start 4-B3` が続いており、** starvation 記録→譲り→dispatch が設計どおり動いた**形。待機の主因候補は `_ready` のサイト系ゲート（`_season_site_waits_for_visible_skull`: 南西のドクロが見える間 北東 を待たせる優先順位、L752-772 — `season_king_battle_text`×12 は 4-B1 側の戦闘継続を示す）と代役ソース待ちで、いずれも有界化済みの設計機構。ボスフェーズの指示枯渇（#5448 系の停滞）とは「届かなかった後に実際に出撃ブロックまで進んだ」点で一線を画す。
- なお `sorties.4-B1: arrived(1558t)` のまま `season_king_battle_text`×12・未分類 `battle_result`×3 が出ている点は南西サイト戦闘の別件（本件の発火機構ではない）。

## §6 （結論が (b) のため欠陥修正はなし）補助的な観察

- `order_failed` レコードの `observed_metric`（`present[:8]`・`hero_present`）は `_sortie_event` が pickup しないため incident/issue へ伝播しない。今回は `world.garrison` で代替できたが、駐留記録が古い状況では「実一覧に何が見えたか」が issue から読めない。再発解析の効率としては小さな証拠ギャップ（`_sortie_event` の `observed_metric` 写し範囲の拡大余地）。
- 同一ステップの再挑戦は `_rearm_failed_site_steps`（`orders.py` L1894-1943、月内3回・600 tick クールダウン）で有界。再試行時に適格代役がウェーブから戻っていれば成功し、戻らなければ同じ `sortie_failed` が月単位で記録される — 監査が「サイトがまだ攻められていない事実」を拾い続ける限り、起票自体は契約どおり。

## 残件

- fingerprint / incident_id の sha256 再計算は本環境では不能。式と入力値は §1 のとおり — 手段のある環境での確認を残件とする（VERIFY-5509 と同条件）。
- 終端読みの `present` 実値（シェーブル/ラズベリーが卵非所持だったか、`unfit`/`hero_guard_short` 系のどれで落ちたか）はラン成果物 `hanjuku_decisions.jsonl` の `observed_metric` が必要で本検証では到達不能。いずれの枝でも `candidates` 空 → 有界待機 → 実一覧再確認 → 取消 の設計経路は不変で、結論に影響しない。
- `4-B1 arrived(1558t)` の南西サイト戦闘（未分類 `battle_result`）の推移は別件として残る。
