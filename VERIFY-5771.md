# VERIFY-5771 — [半熟英雄] 月内出撃ゼロ: ch8 月次処理がマップを占有

検証日: 2026-11-27
対象: azumag/docich#5771 / incident `675b09741cd4a02417ff` / ラン `g1190-ch1v9` / generation `1190` / ch8 `9-8`
範囲: read-only 検証。本ファイルの作成のみ。コード修正・コミット・PR・issue コメントは行わない。

## 結論

**(b) 設計どおり**

ただし、題名の「月次処理がマップを占有」は本件でも因果ラベルとして誤解を招く。監査が示す `zero_sortie`（月内に確定出撃・発射・`order_start` が 0）は issue の数値どおり成立し、誤検知ではない。一方、現行コード上は月次ステータス処理が「開始可能な出撃指示がある」マップを横取りしないように明示的に譲る設計であり、本件の証拠は `order_start` へ到達しなかった原因を月次処理そのものではなく、開始可能な計画が無かった/再構成途中だったことへ向けている。

## ハッシュ再計算

現行 main `src/docich/hanjuku/month_audit.py` の zero_sortie 系 builder を確認した。

- incident id は `_incident_id(identity, chapter, month)`:
  - raw: `runtime_id|generation|chapter|month`
  - `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]`
  - 本件入力: `g1190-ch1v9|1190|8|9-8`
  - 期待値: `675b09741cd4a02417ff`
- fingerprint は `_build_incident` で `_fingerprint(observed.get('chapter'), kind, [primary])`:
  - raw: `chapter|kind|'+'.join(signals)`
  - `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]`
  - 本件入力: `8|zero_sortie|monthly_scan`
  - 期待値: `6ab2fa34e0cc9f7a`

**再計算は未実行**。検証環境で python3 実行が承認却下されたため、ローカルで sha256 を再計算できなかった。式と入力値を上記へ明記する。なお issue の活動欄には、登録時に同じ raw 値から再計算一致済みとの記録があるが、本検証では独立再計算として扱わない。

## 現行コードから確認した経路

### 1. `monthly_scan_hold` は「月次処理が存在した」だけで立つ信号であり、占有の因果証明ではない

`month_audit.py` の `_signals` は、月次ステータス系または month_sub 系の decision/flow があれば `monthly_scan_hold` を追加する。時間割合や、それがなければ出撃できたかは評価していない。さらに `_primary_signal` は `monthly_scan` を `battle` より先に選ぶため、本件のように両方あれば題名は自動的に「月次処理がマップを占有」になる。

本件の決定内訳には確かに `month_sub_open` 2、`post_month_status_*`、`post_month_sequence_finished` がある。しかし同時に `month_done` / `post_month_status_complete` / `post_month_sequence_finished` が記録されており、月次処理は未完のまま月を食い尽くした形ではない。この点は #5759 と同じで、タイトルの因果ラベルは過剰である。

### 2. 月次ステータス処理は、開始可能な出撃指示があるマップを譲る設計

`flows/post_month_status.py` には docich#4642 以来の sortie floor がある。当月の敵城向け確立出撃がまだ 0 で、`orders.next_order(mem)` が開始可能な指示を返す間は、月次確認を延期してマップを出撃計画へ譲る。出撃試行が無いまま延期だけ続く場合は、帰還・修理タスクで出撃元を作るため月次確認へ戻る bounded な設計になっている。

orchestrator でも `post_month_status.step` は通常の `map_step` より先に呼ばれるが、この譲渡条件に合えば `None` を返してマップを解放する。したがって、本件で `order_start` が 0 だったことは「月次処理が常に優先して出撃を塞いだ」こととは違う。少なくともマップ到達時に `next_order` が開始可能と判断されなかったか、マップ到達前に戦闘・月初/季節イベントなどの前景が続いたかのどちらかであり、issue の証拠だけでは月次占有の欠陥を示さない。

### 3. 戦闘は foreground を取るが、月の消費原因としては #5759 の訂正どおり扱う必要がある

#5759 のオーナー訂正では、戦闘中はゲーム内時間が止まるため、「戦闘が月を食う」という説明は誤りとされた。本件でも `battle_grind` は `battle_*` または `monster_menu_choice` の件数が 20 以上という機械的条件で立っているにすぎない。本件は召喚獣メニュー、`enemy_general_menu_wait`、`monster_skill_stall` などを含む長い防衛戦が見えるが、これだけで月内時間を消費したとは言えない。

ただし戦闘は `next_order` が実行される `map` 観測を遅らせる。`plan_starved_rearm` のコメントにも、`next_order` は map 観測でしか走らず、戦闘やメニューが続くと計画が止まる旨が明記されている。つまり戦闘はゲーム内月を直接消費しないが、bot の dispatch 機会を遅らせる副次因にはなる。本件では月末近くに防衛戦・召喚戦・夏イベントが続き、翌 5 分でも回復していないため、単純な瞬間的な遅延とも断定できない。

### 4. `plan_starved_rearm` は計画枯渇の検出と再武装であり、出撃開始そのものではない

`orchestrator.py` の `plan_starved_rearm` は、active な計画がなく、pending 計画も startable でないと判断した場合に、`PLAN_STARVE_TICKS` 経過後に `replan._off_chart(mem)` でフィールド計画を組み直す。本件では月内冒頭近くに `plan_starved_rearm` が 1 回記録されている。これは計画が枯渇・停止していたことの証拠であり、戦闘や月次画面の最中でも計画だけは再構成する設計どおりの救済である。

一方、これは `order_start` ではない。実際の発射にはその後の map 観測で `map_step` → `next_order` → `order_start` → castle/menu/target picker → `order_launched` が必要である。issue の `plan` スナップショットでは `chart_plan.orders` が空で、多数の `orders` 状態だけが残っている。`chart_interim_hold` も 1 件あるため、少なくとも一時点では deterministic な暫定候補を作れない/全て settled と判定されていた。詳細な保留理由はローテーション済みの本番 decision log が無いため再現できない。

### 5. 出撃しない優先順位は安全側の設計である

`orders.next_order` では、対象が既に自軍でないか、出撃元が自軍か、source list が開けるか、最後の将軍を残すか、retake 予約・余剰将軍・行军中の将軍などをゲートしている。さらに interim 候補側でも low-HP 攻撃の抑制、出撃元を空にしないルール、行軍不能経路の抑制などがある。したがって、城ごとの人数だけ見ると出撃可能に見えても、HP・卵・位置未確認・経路・名簿の鮮度などにより合法な候補が無い状態は設計上あり得る。

本件の world state は `ながはま(10)` / `あるまむーん(8)` など一見十分な在城を示し、`しまばら` は未奪取のままである。しかし issue 本文には各将軍の HP/卵/位置確度、名簿の完全性、候補がどのゲートで落ちたかが無い。このため「プランナが明らかに合法な出撃を拒否した実欠陥」とまでは証明できない。逆に、月次処理や戦闘が出撃開始を直接塞いだ証拠も無い。現行コードの優先順位から説明できる範囲では、開始可能な計画が無かったため出撃しなかった安全側動作、すなわち設計どおりと判定する。

## #5759 / #5770 との異同

### #5759 との異同

- 同じ点: どちらも題名は `monthly_scan` だが、`monthly_scan_hold` は月次処理が一件でもあれば立つ信号であり、主因の因果証明ではない。#5759 の結論でもこのラベルは誤解を招くとされた。
- 違う点: #5759 は全保有城が 1 名将ずつで、最後の 1 将軍を出させないオーナー決定により合法な出撃元が無かった。`no_source` も立っていた。本件は `no_source` が無く、在城数だけ見ると出撃元はあるため、#5759 と同じ根拠をそのまま適用できない。
- それでも本件は、post_month_status が開始可能な出撃へ譲る設計と、計画枯渇の証拠から、月次占有の実欠陥とは言えない。

### #5770 との異同

- #5770 は同じランの ch8 `8-1` で、`order_start` 8 / `order_launched` 0。将軍一覧に計画の将軍が見え、card list / empty-kit guard の統合矛盾で同じ失敗が繰り返されたため、検証コメントは **(a) 実欠陥**。
- 本件は `order_start` 0 / `order_launched` 0 で、#5770 の発射パイプライン閉ループには一度も入っていない。`card_list_unreadable_depart`、`sortie_kit_empty_cancel`、`order_failed` の繰り返しは本件の月内内訳に見えない。
- したがって本件を #5770 と同じ実欠陥として扱う根拠は無い。ただし同じランで先行する発射失敗が計画状態を悪化させた可能性は残る。本番ログが無く、本検証ではその因果は確定できない。

## 未決の点

- python3 実行が承認却下されたため、fingerprint / occurrence の独立 sha256 再計算は未実施。式と入力値は上記のとおり。
- `chart_interim_hold` が `no_candidates` と `all_settled` のどちらだったか、およびどの安全ゲートが interim candidate を落としたかは、issue の末尾 decision だけでは判別できない。本番 decision log / frames はローテーション済み。
- 翌 5 分でも回復していないため、より長期の計画停止が後続月でどうなったかは本件だけでは分からない。ただしこれは本件の月内 zero_sortie を月次占有の実欠陥とする証拠にはならない。

## 参照した現行コード

- `src/docich/hanjuku/month_audit.py`: zero_sortie 判定、`_signals`、`_primary_signal`、`_incident_id`、`_fingerprint`、`_build_incident`
- `src/docich/hanjuku/flows/post_month_status.py`: sortie floor / idle-yield reclaim / scan ownership
- `src/docich/hanjuku/orchestrator.py`: owner priority、`plan_starved_rearm`
- `src/docich/hanjuku/flows/map.py`: `next_order` 実行、`order_start` 記録、`sortie_started_month`
- `src/docich/hanjuku/strategy/orders.py`: `next_order` の source/target/spare/retake/readiness gates
- `src/docich/hanjuku/strategy/replan.py`: deterministic interim candidates と low-HP/source/spare gates
- `src/docich/hanjuku/flows/month.py`: 出撃未着手月の month-sub 上限（owner 2026-11-28、現行 main の後続 mitigation。 本件の `month_sub_open` は 2 で上限 6 未満）

## 検品ゲート再計算（Muse、2026-11-28 07:46 JST）

- incident id：`sha256("g1190-ch1v9|1190|8|9-8")[:20]` = `675b09741cd4a02417ff` — issue 値と一致。
- fingerprint：`sha256("8|zero_sortie|monthly_scan")[:16]` = `6ab2fa34e0cc9f7a` — issue 値と一致。
- 式は `month_audit.py` の `_incident_id`（L3516〜）と `_fingerprint`（L3521〜）の現行実装で確認。primary `monthly_scan` は PRIMARY_SIGNALS（L288）の `monthly_scan_hold` / `monthly_scan_yield` 由来。
