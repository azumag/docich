# VERIFY-5500 — docich#5500「[半熟英雄] ボスフェーズでボス戦闘ゼロ: ch5 だいまおう」

- 判定: `boss_phase_no_battle` (confidence high) / run `g1342-1fe0734c` / 観測版 `hanjuku-chart-v1491-card-abandon-retreat-ban` (= commit `231ec8fbd`)
- 検証基盤: origin/main (`2fe1b66f8`, v1497)。当該版の挙動は `git show 231ec8fbd:...` で確認
- ch5 10-7 / ボス だいまおう / 対象 ボス / 継続 600.5秒 / ボス戦闘 0 / battle_start 0 / ボスHP観測 False
- 出撃: 確定 1 / 発射 1 / order_start 2 / order_launched 0 / launch_events 2

## 結論: (a) 実欠陥 — 監査側の証拠欠落（ボス突入の実測をボス戦闘証拠として数えない）

**監査の発火時点で、実測済みのボス城突入が進行中だった。** issue の決定末尾に
`attack_observed A:9532359d:M5cd2b4`（ピスタチオ、23:12:37Z、screen `boss_attack_started`、
reason「実測済みのボス城突入文と出撃将軍が一致」）があり、`plan.attack` は
`entry_evidence='measured_boss_entry'` の状態のまま ~50 秒後（23:13:27Z = 継続 600.5秒）に
アラートが発火した。`_boss_battle_seen` / `_rows_boss_battle` は `battle_start`・ボス名の
`sortie_outcome`・`boss_defeated`/`boss_victory`・ボスHP のみを証拠とし、**実測済みのボス城
突入（attack_observed / boss_attack_started / plan.attack の measured entry evidence）を
数えない**（v1491 month_audit.py L507–622, L2628 付近）。ボス戦闘は突入文 → 戦闘パネル描画の
順で開くため、突入実測から `battle_start` までの間（本件では発火時点で ~50秒）に監査が
走ると「ボス戦闘ゼロ」と誤判定される。実欠陥の所在は発行層・行軍層ではなく **監査の
証拠層**。

**#5375（現行 main の live-sortie defer, `_has_live_boss_attack_sortie`）は本件をカバーしない。**
あの defer は v1491 には存在せず、現行 main であっても対象 status は
`{en_route, launched, launched_unconfirmed}` のみ。ボス城突入の観測と同時に sortie は
`_bind_sortie` で `status='arrived'` に書き換わる（orders.py `_bind_sortie`）ため、**defer の
保護が突入観測と同時に外れ**、突入 ~50秒後の発火は現行 main でも同じく発生する。

**ゲーム側の出撃パイプラインは動いていた**（#5372・#5342 と層が違う）:

- `order_start 2` の内訳: ボス波2本 — `M5cd2b4`（ピスタチオ、カルーン→ボス）と
  `M1aa3fe`（キッシュ暫定→ボス、カルーン）。2本とも出撃確認（sortie_confirm）→
  目的地ピッカ（map_target → y_jump_open、target=ボス）まで到達。
- `order_launched 0` の理由: M5cd2b4 は **未確認送出**（`sortie_launch_unconfirmed_kept`
  系 — Y ビューで ▲ を確認できなかった経路。LAUNCH_DECISIONS のコメントにある g1190/#4826
  と同型）で発射され、`order_launched` が記録されなかった。実発射は `launch_events=2`
  （未確認送出 + 23:12:12Z の `sortie_launch_confirmed` — M1aa3fe のカルーン将軍一覧
  読取りでピスタチオ不在を確認した遡及確定）と整合。
- ピスタチオ波の行進は ~8分かかったが **実際にボス城へ到着し、突入文を実測した**
  （到達不能ではない）。
- 2本目 M1aa3fe は 23:11:57Z に order_start し、hero_priority_selected（ブカティーニ
  代替将軍）、cards_autocarried（安全札1枚携行）、card_pick、sortie_confirm 23:12:33Z、
  y_jump_open 23:12:36Z まで進行。直後の 23:12:37Z にピスタチオのボス突入が割り込み、
  目的地ピッカが中断（出撃は未発射のまま plan.active に残存）。
- **停滞段階 = 目的地ピッカの Y ジャンプ段**（picker・発行・行軍ではなく）。1本目は同段を
  越えたが送出が未確認、2本目はボス突入割り込みで同段が中断された。
- フェーズ窓 [23:03:26Z, 23:13:27Z] は 10-6→10-7 の月境界（10-7 開始 ≈23:05:26Z）を
  跨ぎ、chikujou 系 ~40件・bargain/buy・month_sub_open 等の月次家計フローが窓の
  半分程度を占有。フェーズ開始 tick 80383 は最後の通常城（クロチェット）陥落 tick と
  一致（sortie_history: ピオーネ won クロチェット @80383）。
- `stuck_march` シグナルは古い `A:bf0719e8:Mc8eff7:arrived(4550t)` 記録由来のノイズ。
- `order_start_without_launch` シグナルは既知の監査ノイズ: `_build_boss_incident` が
  `_signals(context, counts, 0, 0, battle_starts)` と departures を 0 固定で呼ぶ
  （v1491 month_audit.py:650）。実際の departures=1 を反映しない。

## §1 fingerprint / incident_id 照合

- fingerprint: `_fingerprint` = `sha256("{chapter}|{kind}|{'+'.join(signals)}")[:16]`。
  boss incident は signals `['boss_no_battle']` のみ → raw `"5|boss_phase_no_battle|boss_no_battle"`
  → 期待値 `f44ef960268a0c6b` と式・入力が一致。
- incident_id: `_boss_incident_id` = `sha256("{runtime_id}|{generation}|boss|{chapter}|{started_at}")[:20]`
  → raw `"g1342-1fe0734c|1342|boss|5|1795561406.6032991"`（started_at は incident JSON の
  `boss_phase.started_at`）→ 期待値 `3e4f442ebebc41a3a8ed`。
- **sha256 再計算は本環境で拒否されたため未実施**（式と入力値の照合のみ）。

## §4 先行インシデントとの異同

- **#5372（ch3、(a) ORDER-ISSUANCE）**: order_start 0 で発行層の欠陥。本件は order_start が
  あり、2本ともピッカ段まで進行、1本は実発射・実到達。層が全く違う。
- **#5342（ch11、(a) _boss_wave_pick gateway 無視）**: ゲートウェイ経路のため波が到達不能。
  本件 ch5 は道路・ゲートウェイ制約なし、波は実際にボス城へ突入した。到達不能ではない。
- **#5276（(a) 無制限同行待ち → PR #5308）**: 同行者機構は本件では正常動作
  （hero_priority_selected でブカティーニ代替を即時選択、待機レコードなし）。
- **#5309（#5372 修正、未マージ）**: 発行層の修正で、本件経路（発行済み・突入実測済み）を
  カバーしない。**#5375（マージ済み、現行 HEAD）も 'arrived' 遷移でカバー外** — 上記参照。

## §6 修正の方向（監査側）

1. `_track_boss` / `_build_boss_incident`（v1491 month_audit.py L624 付近・L2628 付近）で、
   **実測済みボス城突入をボス戦闘証拠として数える**: 窓内 rows の `attack_observed`
   （ボス対象）・`boss_attack_started`、あるいは live `plan.attack` の
   `entry_evidence='measured_boss_entry'` を `_boss_battle_seen`/`_rows_boss_battle` と同等か、
   「突入後 grace（例: 突入実測から N 秒間は発火しない）」として扱う。突入 → battle_start
   の間に発火する現行の穴を塞ぐ。
2. `_has_live_boss_attack_sortie`（#5375, 現行 main）を `arrived` 直後・`plan.attack` 生存中も
   対象に含める。現状は突入観測と同時に `status='arrived'` となり defer が外れるため、
   本件と同一の ~50秒差発火が main でも再現する。
3. （副次的ノイズ）`_build_boss_incident` の `_signals(..., 0, 0, ...)` の departures 固定 0 を
   実測 departures/launch_events へ直し、`order_start_without_launch` の誤シグナルを止める
   （#5342/#5372 でも指摘済みの同型）。

## 証拠・参照

- v1491 `src/docich/hanjuku/month_audit.py`: `BOSS_NO_BATTLE_SECONDS`=600, `_boss_phase`,
  `_boss_battle_seen`, `_rows_boss_battle`, `_build_boss_incident`(L624), `_boss_incident_id`(L622),
  `_track_boss`(L2628), `_signals` hardcoded `0,0` (L650), `LAUNCH_DECISIONS` (L103–110 付近)
- `src/docich/hanjuku/flows/battle.py`: boss 突入文の `attack_observed` + `_bind_sortie` →
  `plan.attack` に `entry_evidence='measured_boss_entry'`
- `src/docich/hanjuku/strategy/orders.py`: `_bind_sortie` で `status='arrived'` 化
- 現行 main `month_audit.py`: `_has_live_boss_attack_sortie`（#5375, `2fe1b66f8`）、
  `BOSS_LIVE_SORTIE_STATUSES={en_route,launched,launched_unconfirmed}`
- 決定タイムライン（issue JSON より）: 23:11:57Z order_start M1aa3fe → 23:12:09
  castle_jump_confirm → hero_priority_selected ブカティーニ → egg_uses_seen →
  cards_autocarried → card_select sortie_input 群 → card_pick → 23:12:33 sortie_confirm →
  23:12:36 y_jump_open(target=ボス) → 23:12:37 attack_observed M5cd2b4 ピスタチオ
  （boss_attack_started）→ 以後レコードなし → 23:13:27 incident

## 環境メモ

- `DOCICH_HANDOFF_PATH` 未設定のため handoff.md は未読（AGENTS.md §1 の規定どおり）。
- `gh` は本環境で拒否。issue 本文は WebFetch で取得（全文 `/tmp/...` に展開済み）。
- sha256 再計算・python3 は環境拒否。`git show`/`git log` は実行可。
