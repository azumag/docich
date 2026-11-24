# VERIFY-5317 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch3 リースリング

- 対象: azumag/docich issue #5317 / fingerprint `2edf3097c9746049` / occurrence `560819df4cf7a814b9d3`
- ラン: `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1435-recall-dispatch-unattributed`
- 月: ch3 4-7（観測 492回 / 767.2秒 / max gap 3.6秒）
- 検証範囲: read-only（本ファイル作成以外の編集・コミット・push・PR・本番 VM 操作なし）
- 検証ベース: worktree `/home/hatch/work/docich-5317` を `origin/main` の `dd6ed0fbec1f264bfae66b0a67ba4c54a368a595`（feat(hanjuku): 築城 skip の理由記録, v1444, #5320）に detached で作成。PR #5304（v1442、未マージ）の `_stock_cards` 修正は含まない（現行 main に `_adopt_plan` フォールバックは存在しない）。
- 検品ゲート訂正（2026-11-24 11:2x、Muse）：本ファイルの初版は「PR #5304 の保護が main v1444 にある」と記していたが誤り。#5304 は未マージ（OPEN・CONFLICTING）で、現行 main の `_adopt_plan()` は `cards: list(o['cards'])` を verbatim で採用し、`_stock_cards` フォールバックを持たない。結論 (b) 自体は変わらない（下記参照）。

## 結論: (b) 設計どおり・修正不要 — 本件は #5282 の (a)（在庫フォールバック未適用）とは別機構の「明示的 cardless order」

検出自体は監査仕様どおり。在庫にバグストーム×1があり、attack 出撃の実測携行kitが空だったため `sortie_without_cards` が high で発火した。しかし本件の `planned_cards` は `[]`（調整チャートが最初からカードを指定していない cardless order）であり、#5282 で実欠陥と判定された「計画されたカードが在庫になく、フォールバックも届かず空になった」経路ではない。cardless order が空kitのまま出撃するのは、チャートが `[]` を計画し `_adopt_plan()` が verbatim で採用する設計どおりの結果である。なお PR #5304（v1442、未マージ）のフォールバックは条件の最初が `cards` 非空のため、仮にマージされても cardless order には適用されず、本件の結果は変わらない。ゲーム実装の修正は不要。

## 判定

- (b) 設計どおり・修正不要。シグナルは監視ワークフローの分類として仕様どおり発火しており、コード側の fail-closed や adjusted-plan の在庫フォールバックに誤りはない。
- 実装フォローアップは本件では発生しない。もしオーナーが「在庫1枚でも attack には常に切り札を携行すべき」と方針を変更する場合は別 issue で扱う（本検証では方針変更を前提に実装しない）。

## 機構の切り分け

### 監査側（検出は正しい）

- `docs/operations/hanjuku-month-audit.md:271-284` — `card_stock` に正数があり attack/retake/boss の出撃が実測kit 0枚で成立すれば `sortie_without_cards` を保存する仕様。move/house/repair/defend/recall は対象外。計画 `chart_plan.orders[].cards` は実携行の証拠にしない。
- issue の `sortie_event`: step `A:c8161fdd:M269be0` / 将軍 リースリング / 出撃元 アルマムーン / 対象 グリン / purpose `attack` / inventory `{'バグストーム': 1}` / carried `[]` / planned_cards `[]` / 決定 `order_launched`。仕様の発火条件（attack・正数在庫・実測空kit）を満たす。
- `src/docich/hanjuku/month_audit.py:2270-2314` の `_sortie_carried_cards` は実測kit（rare/strong/dropper 補助キット、発射record、`order_context[step].observed_metric.cards`、`world.launched_orders[step].departure_binding.cards`、同一stepの `sortie_confirm`）だけを読む。本件はそれらが空で `carried=[]` は実測に整合（#5258 の証拠経路は正常）。
- `order_start 0` / `order_launched 1` は直前120秒窓のカウントであり、`order_start` が無かった証拠ではない（#5282 の VERIFY と同じ読み方）。

### 計画側（本件の核心: `planned_cards=[]`）

- 本件の `plan.chart_plan.orders` は3件（ヘーゼル move コラダイン、デーメーテール attack グリン、リースリング attack グリン）で、いずれも `cards` フィールドを持たない。調整チャート worker がカードを指定せず cardless で回答した結果として `planned_cards=[]` になっている。
- `src/docich/hanjuku_chart_adjust.py:279-282` — `_order()` は `cards = raw.get('cards') or ()` で欠落/空を cardless order として受理する（CARD_NAMES と枚数だけを検証）。設計上 cardless は有効な指示である。
- `src/docich/hanjuku/strategy/replan.py:2901-2905` — `_adopt_plan()` は採用時に `cards: list(o['cards'])` をそのままコピーする。本件では `[]` のまま採用された。
- PR #5304（v1442、未マージ）の差分は `_adopt_plan()` に `if (cards and o.get('purpose') != 'move' and not o.get('source_probe') and not o.get('roster_probe') and not chart.is_boss_castle(o['target'])): cards = _stock_cards(mem, cards)` を追加するもの。最初の条件 `cards` が空のため cardless order はフォールバック対象外。PR body も「Preserve explicit cardless orders, staging moves, and boss-target kits.」と明記しており、cardless を保持するのは意図的な設計である。**ただし #5304 は未マージ（OPEN・CONFLICTING）のため、この条件は現行 main には存在しない。** 現行 main で cardless が保持されるのは `_adopt_plan()` の verbatim 採用（`cards: list(o['cards'])`）によるものであり、#5304 の有無で本件の結果は変わらない。
- つまり本件の空kitは「在庫フォールバックが届かなかった」のではなく「cardless order が cardless のまま実行された」結果である。

### deploy 側

- `src/docich/hanjuku/flows/deploy.py:554-589` — `_drop_card()` は選択できない予定札を `CARD_MISS_LIMIT` 読み後に外して出撃を継続する（設計どおりの escape）。#5282 はここで全予定札が外れ `want=[]` になった。本件は最初から `want=[]` で、外された札は存在しない。
- `deploy.py:2683-2685` の `_empty_sortie_inventory` → `[]` と `deploy.py:2765-2807` の発射継続は、cardless order に対して設計どおり動いただけである。

## 既検証シグネチャとの突き合わせ

- #5282（fingerprint `43937629bb056205`、ch2 3-5、将軍 —、`planned_cards=['ミックミー','ノリウツール','ノリウツール']`、在庫バグストーム×3）: 計画札がすべて在庫ゼロで、`_adopt_plan` が verbatim 採用 → deploy で全札 drop → 実測空kit。結論 (a) 実欠陥「調整チャート指示が在庫フォールバックを受けない計画層のギャップ」。修正 PR #5304 / v1442。
- #5317（本件、fingerprint `2edf3097c9746049`、ch3 4-7、将軍 リースリング、`planned_cards=[]`、在庫バグストーム×1）: フォールバックされる元のカード指示が存在しない。`_stock_cards` の適用有無が結果を変えず、PR #5304 の修正対象外。同じ `sortie_without_cards` シグネチャ・同じ generation だが、章・将軍・fingerprint が異なり重複ではない（起票側の重複判断どおり）。
- #5249 / #5258: 監査が計画値を実測と誤認する証拠欠陥の修正。本件では #5258 の経路が正常に働き、実測空kitを正しく検出している。誤検知 (c) ではない。
- 再発の扱い: PR #5304 マージ後のランでも、cardless attack が正数在庫下で発火すれば本シグネチャは再び issue になる。これは (a) の再発ではなく仕様どおりの監視结果为、監査側の誤発火でもない。

## 関連

- #5282 / VERIFY-5282.md — 同シグネチャの (a) 実欠陥と修正 PR #5304（v1442、未マージ）
- #4685 / #4895 — `_stock_cards` の在庫フォールバック（「計画札が在庫になければ在庫の多い札を携行」、非ボス attack へのバグストーム携行はオーナー承認済み）
- docs/operations/hanjuku-month-audit.md「切り札在庫があるのに非携行で出撃（v1410）」

## 受入条件

- 結論 (b) を VERIFY-5317.md と issue #5317 コメントへ記録する。
- read-only 検証のみ。コード変更・コミット・push・PR 化・本番 VM 操作はしない（検品ゲートはメイン Muse）。
- 方針変更（cardless attack も在庫から補填する）が欲しい場合は別 issue で、chart worker の cardless attack 禁止または `_adopt_plan` の条件拡張を検討する。本件では実施しない。
