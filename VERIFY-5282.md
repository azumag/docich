# VERIFY-5282 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch2 Mb1cbfe

- 対象: azumag/docich issue #5282 / fingerprint `43937629bb056205` / occurrence `bf8f4d44949c03a41cd7`（再発 `17e8031b3a567f8038a3`、ch2 3-10）
- ラン: `g1342-1fe0734c` / `hanjuku-chart-v1432-boss-companion-timeout`
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 検証ベース: 本 checkout の `src/docich/hanjuku/version.py:7` は `BOT_VERSION = 'hanjuku-chart-v1432-boss-companion-timeout'` — issue の観測ランと同一バージョンで、#5258（`63b8dc99d`「fix(audit): use measured sortie card receipts」）を含む。

## 結論: (a) 実欠陥 — 監査は実測どおり正しく検出しており、残る欠陥は「調整チャート指示が在庫フォールバックを受けない」計画層のギャップ

`order_launched` は日記帳裏付けの確定出撃であり、`sortie_confirm` で読まれた携行kitは空（または未読）として実測された。バグストーム3枚がフィールド在庫にあるのに1枚も携行せずウラノポリス（非ボス城）へ出撃したのは事実であり、#4685/#4895 でオーナーが「欠陥」と判断したのと同じクラス（ch2・attack・在庫放置・空携行）の挙動である。暫定指示では `_stock_cards` が最も在庫の多い札（バグストームを含む）を携行させるが、調整チャート（`A:` プレフィックス）の採用指示は `o['cards']` をそのままコピーするため、このフォールバックが届かない。

## 根拠となるコード箇所（本 checkout）

### 検証観点2: order_start 0 / order_launched 1 の読み方

- `src/docich/hanjuku/month_audit.py:2354` — `_build_sortie_cards_incident` は `_read_decisions(runtime, now-120, now, ...)` で**直前120秒**の決定行だけを集計する。`sorties.order_start`/`order_launched` は全期間の累計ではなく窓内カウントなので、`order_start 0` は「その step が order_start を経なかった」ことの証拠にはならない。Mb1cbfe の order_start は窓より前に記録されたと読むのが自然（出撃準備: 城ジャンプ→一覧→ドロップ→確認→目標ジャンプ→発射は120秒を超えうる）。
- `src/docich/hanjuku/strategy/orders.py:2430` — `order_launched` は `_finish_order` が `_record(mem, 'order_' + state, ...)` で動的に記録する（`order_launched` のリテラルはコード上に存在しない）。
- `src/docich/hanjuku/flows/map.py:2937-2959` — `_commit_departure` は `_departure_receipt_sent`（送信日誌の A 受領、`map.py:3285-3290`）＋ピッカー閉鎖という実測裏付けでのみ走り、`_finish_order(mem, 'launched', ...)`（:2945）で `order_launched` を出す。発射実体は `request['step']` = アクティブ指示 = Mb1cbfe。
- `src/docich/hanjuku/flows/map.py:200` — 別step `A:2806bf08:M25e288` の `order_launched_unconfirmed` は「送信未記録のままピッカーが閉じた」未確定経路。step が異なり別の出撃実体であり、`order_launched_unconfirmed` は `LAUNCH_DECISIONS`（`month_audit.py:103-110`）に含まれないため本シグネチャの判定対象でもない。Mb1cbfe の `order_launched` と同一実体ではない。
- 機構上の裏付け: `order_launched` に至るには `departure_request`（`map.py:2920`）が必要で、それはアクティブ指示の目標選択到達時にのみ作られる。アクティブ化は `map.py:2576` で `order_start` 記録（`map.py:2617`）と同時なので、order_start 先行は機械的に保証される。

### 検証観点1: #5249 / PR #5258 のカバー範囲

- fingerprint は `month_audit.py:2993-2995` で `"{chapter}|{kind}|{signals}"` のみ。章・kind が同じなら全 occurrence が同じ fingerprint を持つ。#5249 と fingerprint が一致するのは仕様であり、本件は別 run（g1342）・別月（3-5, 3-10）・別 incident_id の別 occurrence。
- `month_audit.py:4073-4074`（`_issues` は `--state open` のみ）+ `:4086-4091`（`_find_issue` は open issue だけを fingerprint 検索）— #5249 がクローズ済みなら新 occurrence は新規 issue になる。#5282 が新 issue として立った経路と一致。
- #5258 の修正本体 `_sortie_carried_cards`（`month_audit.py:2270-2314`）は、レア/強/卵落キット・発射record・`order_context[step].observed_metric.cards`・`world.launched_orders[step].departure_binding.cards`・同一stepの `sortie_confirm` record の `cards` を実測kitとして読む。本件ではこれら全てが空/欠落 → `carried=[]` は**実測に基づく正しい判定**。つまり #5258 の修正は本件経路をカバーし、正常に動作している。
- ただし #5258 は「監査が計画値を実測と誤認する」証拠欠陥の修正であり、シグネチャが検出する**実欠陥そのもの**（在庫放置の空携行出撃）を消すものではない。本件は修正済みの検出器が、修正されていない実挙動を正しく報告したもの。

### 検証観点3: 計画cards と在庫の食い違い

- 携行判定は計画を見ない。`month_audit.py:2343` の `planned_cards` は `chart_plan.orders[].cards`（計画表示用）で、在庫は `_positive_card_stock`（`:2238-2244`）＝ `card_stock` の正数のみ。計画cards を実携行の証拠に使わないのは docs/operations/hanjuku-month-audit.md:273-276 の明記どおり。
- 計画側の欠陥連鎖:
  - `src/docich/hanjuku_chart_worker.py:110-113` — プロンプトは「`card_stock` で在庫1以上と実測された札だけ」を指示するが、モデルが未遵守（ミックミー・ノリウツール×2 を在庫ゼロで計画）。
  - `src/docich/hanjuku_chart_adjust.py:279-282` — `_order` は `cards ∈ CARD_NAMES` と枚数だけを検査し、`card_stock` との照合はない。
  - `src/docich/hanjuku/strategy/replan.py:2903-2905` — `_adopt_plan` は `o['cards']` を verbatim で採用。`_stock_cards` を通さない。
  - `src/docich/hanjuku/flows/deploy.py:554-589` — `_drop_card` は選べない計画札を `CARD_MISS_LIMIT` 読み後に外して出撃継続（設計どおりの escape）。全予定札が外れると `want=[]` となり、sortie_confirm は実測空kit（`deploy.py:2683-2685`、`_empty_sortie_inventory` → `[]`）と一致 → そのまま発射へ進む（`deploy.py:2765-2807`）。

### 残る欠陥（実欠陥の中身）

- `src/docich/hanjuku/strategy/replan.py:862-908` — `_stock_cards` は「計画札が在庫になければ、在庫の多い札を携行させる」#4685/#4895 のフォールバック。コメント（:866-870）は本件と同じ事象を欠陥として記述: 「ch2 2026-11-19 sent 37/38 HP generals at アウスパジア ... while ノリウツール 21 and バグストーム 25 sat in stock, and both waves retreated」。
- `tests/test_hanjuku_stock_cards.py:15-17` — stock `{イッテツーン:0, ノリウツール:21, バグストーム:25}` で `_stock_cards(mem, ['イッテツーン','イッテツーン']) == ['バグストーム','ノリウツール']`。**非ボス attack へのバグストーム携行はオーナー承認済みの意図的挙動**（犠牲札でも空より良い、の方針）。
- しかし `_stock_cards` の適用箇所は暫定（interim）計画のみ（`replan.py:1378`, `:1704`）。採用済み調整チャート指示には届かず、本件 `A:2806bf08:Mb1cbfe`（`PLAN_PREFIX 'A:'` = `hanjuku_chart_adjust.py:117` + `execution_step` `:120-122`）はそのまま空で出撃した。
- なお deploy 側の強札補充 `_strong_card_names`（`deploy.py:479-499`）は非ボスで `STRONG_CARDS` のみを返しバグストームを足さない（`BOSS_SACRIFICE_CARDS`、ボス＋弱将軍限定 `hanjuku_reference.py:178-197`）。つまり出撃時点での後付け補充もない。残る欠陥は「計画層の在庫フォールバックが adjusted 指示に適用されない」ことであり、暫定指示と同じ `_stock_cards` 相当の適用（採用時またはドロップ後の在庫補填）が修正方向。
- 参考: 戦闘側もバグストームを認識済み（`hanjuku_reference.py:92-94`, `:1316`, `:1336-1337` `half_both_hp` / `self_harm`）。携行すれば使用判定の対象になる。

### 検証観点4: #5226 との異同

- #5226 は監査側の証拠欠陥（#5118 の実測kit読み取り消失）による (c) 誤検知。本件はその後継修正 #5258 が既に入った v1432 で、証拠経路は正常。検出は実測事実に基づく (a) 実欠陥で、異なる。
- 補足（検出器の仕様上の注意）: 監査仕様は「実測kitが空**または未読**（blind approve 等）の出撃」を発行する（`docs/operations/hanjuku-month-audit.md:276`）。blind 承認で未読だった場合でも本シグネチャは仕様どおり発火する。本件は `card_drop` で全予定札を外した後の出撃であるため、実測空kitとして整合する。

## 修正案（実装は別タスク）

1. 採用済み調整チャート指示にも `_stock_cards` 相当の在庫フォールバックを適用する（`_adopt_plan` または order 開始時）。暫定指示との対称性が最も素直。
2. もしくは `_drop_card` で全予定札が外れた時点で、正数在庫からの補填を検討する（deploy 側の後退経路）。
3. オーナーが「バグストームは非ボス出撃に携行不可」を改めて指示する場合は、逆に監査側 `_positive_card_stock` を purpose 別の携行可能札に絞る必要がある（その場合の再判定は (c)）。現行の暫定挙動（`_stock_cards` fallback + test_hanjuku_stock_cards:17）からは (a) が自然。

## 手順上の注記

- read-only 検証。VERIFY-5282.md の作成以外のファイル編集・本番 VM 操作なし。push・PR・issue コメントは実施しない。
