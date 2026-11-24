# VERIFY-5353 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch11 ヘラ

- 対象: docich issue #5353 / fingerprint `4dfd4ad0f6d10426` / occurrence `03f2531393ef3eedba46`
- 観測: ch11 / 月 18-2、出撃: 確定 1 / 発射 3 / order_start 3 / order_launched 3、
  観測 651 回 / 132.2 秒 / max gap 0.7 秒
- シグネチャ: 切り札在庫があるのに非携行で出撃 / 切り札在庫を携行していない / 戦闘が長引く
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1450`（v1450。v1448 自動携行マージ済みの世代）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 検証ベース: origin/main `46d17f1a5`（v1455 = `hanjuku-chart-v1455-password-in-game-retry`、
  `src/docich/hanjuku/version.py:7` を `git show origin/main:` で確認）。本 checkout は
  `verify/5353-sortie-without-cards` @ `2cbe3542f`（v1451）。引用行番号は origin/main のもの。
  v1450→v1455 の `src/docich/hanjuku/` 差分は `month_audit.py` +3 行（型表示、PR #5364）と
  `battle_menus.py`/`password.py`/`replan.py`/`version.py` のみで、`strategy/kit.py`・
  `flows/deploy.py`・`strategy/execution.py`・`flows/map.py` のカード選択・出撃確認経路は
  ラン当時（v1450）と同一。したがって引用行番号はラン時コードにも一致する。
- gh は利用不可（実行環境で却下）。`git ls-remote`/`git merge --ff-only`（リモート参照・
  ブランチ更新）も却下。issue 本文・PR #5359 の差分は取得できず、依頼記載の要点と
  リポジトリ内の証拠だけで判定した。関連テスト（pytest）の実行も却下のため未実施。
- handoff 正本: `DOCICH_HANDOFF_PATH` 未設定のため未読（推測で補わない）。

## 結論: (a) 実欠陥 — #5319 と同じ欠陥クラス。ただし v1450 では自動携行（v1448）が介在するため、生存する喪失経路は #5319 より狭い

出撃確認で読まれた携行kitは空（または blind approve で未読）として実測され、在庫が正数の
切り札を1枚も携行せず発射した。これはオーナーが「欠陥」と判断したのと同じクラス
（attack・在庫放置・空携行、#4685/#4895。VERIFY-5319.md 参照）の挙動であり、「戦闘が
長引く」の同時シグナルは切り札不在の実害（実発射）と整合する。誤検知ではない。

**計画の有無について**: 本 issue は「計画あり」型である。`tests/test_hanjuku_month_audit.py:1688-1693`
（origin/main、PR #5364 で追加）が `docich#5353` を明示し「計画に札があるのに携行できていない型」
としてテストしている。incident の `planned_cards` は `chart_plan.orders[].cards` から取る
（`src/docich/hanjuku/month_audit.py:2382-2383`）。issue 本文の `計画cards:` 行そのものは
gh 不可で直接確認できないため、上記はリポジトリ内証拠に基づく前提である。

## 根拠となるコード箇所（origin/main v1455。v1450 と同一経路）

### 検証観点1: 検出は仕様どおりで誤検知ではない

- `month_audit.py:2277-2283` — `_positive_card_stock` は `card_stock` の正数のみを拾う。
- `month_audit.py:2309-2353` — `_sortie_carried_cards` はレア/強/卵落キット、発射record、
  `order_context[step].observed_metric.cards`、`world.launched_orders[step].departure_binding.cards`、
  同一stepの `sortie_confirm` record を実測kitとして読む。binding を持たない snapshot の
  `cards` は計画値の可能性があるため証拠にしない（`:2342-2345` コメント）。
- `month_audit.py:2356-2387` — `_sortie_cards_event` は在庫正数・実測携行空の launch record に
  発火。`planned_cards`（`:2382`）は計画表示であり、実携行の証拠には使わない。
- 集計の読み方: `確定` は当月 `month_sorties.launches`（`month_audit.py:2397-2398`）、
  `order_start`/`order_launched` は直前120秒の決定行カウント（`:2393-2395,2422-2423`）。
  `order_start 3 / order_launched 3 / 確定 1` は、窓内に同一stepを含む3回の出撃開始・
  発射記録があり、そのうち1件が roster 読みで確定したことを意味する。

### 検証観点2: fingerprint 同一は構造上の必然であり、機構同一の証拠ではない

- `month_audit.py:3032-3034` — `fingerprint = sha256(f"{chapter}|{kind}|{signals}")[:16]`、
  呼出しは `:2454-2455` で `chapter|sortie_without_cards|sortie_without_cards` 固定。
  ch11 の `sortie_without_cards` occurrence は全て `4dfd4ad0f6d10426` になる。
  #5319（カシュー）と #5353（ヘラ）が同一 fingerprint なのは章と kind が同じだからで、
  機構の同一性を意味しない（VERIFY-5319 と同じ指摘）。
- 別 issue として立ったのは、GitHub 側の `_find_issue`（`month_audit.py:4145-4146`）が
  fingerprint marker で既存 issue を探す仕様から、#5319 がクローズ済み等で見つからなかった
  可能性がある。gh 不可で状態は未確認（推測で断定しない）。

### 検証観点3: v1450 では自動携行（v1448, PR #5336）が介在し、喪失経路が #5319 より狭い

`strategy/kit.py` の `_deploy_cards`（origin/main:51-114）:

- `:53-61` — `_execution_bound` な order は `bound['cards']`（実測kit）をそのまま返す
  **早期 return**。この経路では `:108-113` の自動携行に到達しない。
- `:78-96` — `card_drop`（1 drop entry で計画の同札1枚を除去）・`kit_spent`・
  `card_zero_at_start`（`zero.cards` のうち `live_stock` 0/欠落のものを計画から除去;
  記録は `flows/map.py:2641-2642` の order_start 時）で計画札が落ちる。
- `:108-113` — `if not carried:` で `_auto_carry_safe_card`（`:117-146`）を1枚だけ追加。
  適格条件は「在庫正数・`kit_spent`/`card_drop` 未含有・`egg_drop == 0`」。
  カタログ上 egg_drop 0 は全32種中12種（`src/docich/hanjuku_reference.py:30-114`:
  デッドガン・ノリウツール・ブレイコウ・クースカン・ブラックホール・シュプレボイス・
  エンジェリン・バグストーム・キャトルミュー・ビッグウェイブ・ハリケーン・ファバード）。

v1435（#5319 のラン）では存在しなかった自動携行が v1450 では有効なため、#5319 で挙げた
「フィルタで計画全落ち → `deploy.py:2615-2616` 即B → 空確定」の単純経路は、適格な安全札が
あれば自動携行で `wanted` が非空に戻る。本件が空kitのまま発射したには、以下のいずれかが必要:

1. **bound 空kit 継承が自動携行をバイパス**（自動携行の適用外・構造的ギャップ）—
   先行の出撃確認が読み取れた空kitを `confirm_departure_order`（`execution.py:72-91`）で
   `departure_binding.cards=[]` に固定すると、live sortie（`execution.py:104-113` の
   en_route/arrived/launched_unconfirmed）または同 `confirmation_tick` の attempt 継続
   （`:96-103`）では `execution_order` が `_execution_bound` order を返し、
   `_deploy_cards:56-61` が `bound['cards']=[]` を早期 return。**自動携行は適用されない。**
   窓内の `order_start 3 / order_launched 3` と整合する反復機構。最初の空確定は
   「読めた空kitを `sortie_kit_mismatch` が承認」（`deploy.py:2769-2775`）か
   「`want==[]` と `got==[]` が一致する無記録の承認」（`:2733-2793` の else 経路）で起きる。
2. **適格な安全札が在庫に無かった** — 在庫12種に egg_drop 0 の札が無い、または全て
   `kit_spent`/`card_drop` 済みなら `_auto_carry_safe_card` は None（`kit.py:124-146`）を返し、
   `wanted=[]` → 即B → 確認で `want==got==[]` の一致 → 空発射。この部分は
   「卵落札を自動携行しない」v1448 の設計どおりだが、計画在庫札を失ったまま出撃する
   欠陥クラスの結果は変わらない（issue の在庫内訳は gh 不可で未確認）。
3. **自動携行札または計画札がパネル層で失敗** — 計画札が drop/zero フィルタで全落ちし、
   自動携行された安全札もパネルで `CARD_MISS_LIMIT=5` 読み不能で `card_dropped`
   （`deploy.py:555-590`、drop は `kit.py:129,165` で以後の自動携行・追加の対象外）になるか、
   パネル読取不能が `NORMAL_CARD_UNREADABLE_LIMIT=3` 続き `card_list_unreadable_depart`
   （`deploy.py:2554-2573`、attempt0 で B 1回のみ、picked 空のまま確認へ）→
   `sortie_confirm_blind_approve`（`:2753-2763`、非ボスで3観測後に既定A）または
   `sortie_kit_mismatch` で空kit承認（`:2769-2775`）。これらは 2026-11-10 オーナー承認の
   「取消より出撃優先」bounded escape で、発火はパネル測定失敗を意味する。

**どの経路が発火したかは、ラン `g1190-ch1v9` の `hanjuku_decisions.jsonl` 上の
`card_dropped`/`cards_autocarried`/`card_list_unreadable_depart`/`sortie_kit_mismatch`/
`sortie_confirm_blind_approve`/`card_zero_at_start` の記録が必要で、本環境（VM へ SSH 不可・
gh 不可）からは取得できない。** よって (a) の結論は「欠陥クラスの出撃が実測で発生し、
v1450 の自動携行下でも空発射が起きた」構造的判定であり、特定経路の確定ではない。

### 検証観点4: #5319・#5359 との関係

- **#5319 との異同**: 欠陥クラス（計画在庫札が採用層ではなく出撃実行層で失われる）と
  判定（a）は同じ。ただし機構は厳密には同定できない — #5319 は v1435 で自動携行がなく、
  計画全落ち → 即B の単純経路で説明できた。#5353 は v1450 で自動携行が有効なため、
  成立には観点3の (1) bound 空kit 継承、(2) 安全札不適格、(3) 携行札自体のパネル層失敗の
  いずれかが必要。同一 fingerprint は章・kind 一致による必然で機構の証拠ではない
  （観点2）。#5319 の修正案1・2（`card_zero_at_start` の stale 0 対策・通常注文の
  `card_drop` 復元）は v1455 でも未対応のまま残っている（`kit.py:78-96` が同一構造）。
- **#5359（v1454、Draft「空の planned-card 出撃を拒否」）との関係**: origin/main 未マージ、
  差分は gh/リモート参照ともに取得不可で未検証。条件付きの評価:
  - 「実効携行セット（want/got）が空の出撃を確認層または発射層で拒否する」実装なら、
    本件の全候補経路（bound 空kit 継承・安全札不適格・silent approve）を出口で塞ぐため、
    機構をカバーする。
  - 「計画cards が空の出撃だけを拒否する」実装なら、本件（計画あり型）は対象外であり、
    v1448 自動携行の設計（計画空でも安全札1枚携行）とも衝突し得る。
  - いずれにせよ `card_list_unreadable_depart`/`sortie_confirm_blind_approve` 系の
    オーナー承認 escape（出撃優先、2026-11-10）を拒否方向へ転じるかは差分次第で、
    ここでは断定しない。

## 修正案（実装は別タスク・結論 (a) に基づく）

1. **bound 空kit の再実行を塞ぐ**（`kit.py:53-61`）: `bound['cards']` が空で在庫正数の
   通常出撃では早期 return せず、自動携行・再計算へ進む。または `confirm_departure_order`
   （`execution.py:72-91`）側で、在庫正数の通常出撃に空 binding を作らない
   （空 binding は実測kit固定の意味を持たない）。
2. **silent approve の閉塞**（`deploy.py:2733-2793`）: `want==[]` かつ `got==[]` を
   「一致」として無記録承認する経路で、在庫正数・非 corridor・カードレス設計step
   （第9話等）以外なら、空のまま発射せず取消/再試行へ回す。#5359 がこの層の実装なら
   本件をカバーする。
3. **安全札不適格時の扱い**: `egg_drop==0` の適格札が無い場合に「空のまま発射」するか
   「卵落リスクのある札を携行」するか「出撃を見送る」かは v1448 設計（卵落としは避ける）と
   オーナーの「在庫放置・空携行は欠陥」判断が衝突し得るため、オーナー判断事項として残す。
4. **フィルタ全落ち時の再検証**（VERIFY-5319 案1・2 の継続）: `card_zero_at_start` の
   `live_stock` キー欠落を0扱いしない／計画kit全体が空になるとき一度だけ一覧スクロールで
   再証明する（`kit.py:91-96`・`deploy.py:2615-2616`）。通常注文の `card_drop` も台帳正数
   再証明で復元する（boss は `strategy/boss_cards.py:193-200` の `note_card_purchase` 復元あり、
   通常注文には無い）。
5. **監査側は変更不要**: 実測kit空・在庫正数の発射を拾うのは仕様どおり。v1454 の型表示
   （`month_audit.py:3756-3758`）で次回 issue から「計画あり/計画なし」が本文に出るため、
   本件のような型切り分けは今後は本文だけで済む。

## 手順上の注記

- read-only 検証。VERIFY-5353.md の作成以外のファイル編集・本番 VM 操作・push・PR・
  issue コメントは実施しない。
- gh はこの環境で利用不可（`gh issue view` 等が実行却下）。`git ls-remote`/`git fetch` 系も
  却下のため PR #5359（Draft）の差分・issue #5319/#5353 の open/closed 状態は未確認。
  issue 本文は依頼記載の要点に基づく。
- 関連テスト（`tests/test_hanjuku_auto_carry_safe_card.py`、`tests/test_hanjuku_month_audit.py`
  等）の pytest 実行は環境で却下されたため未実施。静的確認では
  `test_hanjuku_month_audit.py:1688-1693` が「#5353 は計画ありの非携行型」、
  `test_hanjuku_auto_carry_safe_card.py:11-32` が「egg_drop 0 のみ・spent/dropped 除外・
  在庫無しなら自動携行なし」の現行仕様を裏付けている。
- 発火経路の確定には `g1190-ch1v9` の `hanjuku_decisions.jsonl`（`cards_autocarried`/
  `card_dropped`/`card_list_unreadable_depart`/`sortie_kit_mismatch`/
  `sortie_confirm_blind_approve`/`card_zero_at_start` の有無）が必要。VM へは SSH しない
  方針のため未取得。
