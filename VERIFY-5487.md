# VERIFY-5487 — [半熟英雄] 月内出撃ゼロ: ch1 月次処理がマップを占有

- 対象: docich#5487 / fingerprint `f29ac36b2bedefde` / occurrence(incident_id) `9eb53c159cdb391fc4e9`
- 判定: `zero_sortie`（confidence `high`）/ signature `monthly_scan` / signals `monthly_scan_hold` + `battle_grind` + `no_source`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1489-discharge-last-general`
- 月: ch1 1-7（観測 3543回 / 649.0秒 / max gap 0.8秒 / coverage 0.993 / started_clean）
- 再発コメント: ch1 1-9 / occurrence `398e0437026d3d494ee4`
- 検証ベース: `origin/main` = `b3fb0bec0e3d0368b8a0727cf22748797cda4af2`（`BOT_VERSION` は同じく `hanjuku-chart-v1489-discharge-last-general`）
- 検証範囲: read-only。本ファイル以外のコード変更なし。本番 SSH・VM 操作なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 埋め込み JSON と現行コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり — #5463 / v1485 修正対象の再発ではない**

発火自体は監査契約どおりで誤検知ではない。ただし ch1 1-7 の占有機構は、#5463 で修正済みになった「読み取り専用 probe がボスロックで再活性化するループ」ではなく、既に (b) 家族として確立している **「合法的に出撃源ゼロ」**（`_sole_starved` / `sender_min=2` / `chart_defense_wait` 系列）だった。

したがって切り分けは次のとおり。

- **v1485（PR #5468）は v1489 に祖先として含まれる**が、本件 ch1 1-7 の機構は v1485 の対象外である。
- **fingerprint レベルでは v1489 で再発している**（同じ `1|zero_sortie|monthly_scan`）。しかし **root cause レベルでは #5463 の再発ではない**。
- ch1 1-7 は月内に開始可能な実出撃注文が存在せず、月境界後 37.8秒で出撃へ復帰した。これは出撃フロー停滞ではなく、1将軍城を空にしない守備制約と月次処理・防衛戦の占有が重なった設計上の待機である。
- ch1 1-9 の再発コメントは同一 fingerprint の別月を確かに示すが、compact comment には埋め込み JSON がなく、#5463 型か合法出撃源ゼロ型かを独立に帰属できる情報はない。「翌5分 回復 no」だけでは現行コメント形式では `after.complete` / `early` が分からず、ch1 1-7 の結論を覆す根拠にはならない。

## 1. 発火の正当性とハッシュ照合

監査契約（`docs/operations/hanjuku-month-audit.md`）では、`zero_sortie` は閉じた月について `confirmed == 0` かつ `departures == 0` で、月を開始から完全観測した場合に発火する。現行コードでも `month_audit.py` L3475 が同じ条件である。

issue の提示値は契約を満たす。

- confirmed 0 / departures 0 / order_start 0 / order_launched 0 / moves 0。
- 観測 3543回、649.0秒、max gap 0.8秒、coverage 0.993、started_clean。
- confidence reasons は空で `high`。
- signals は `_signals`（`month_audit.py` L2997-3046）の条件に一致する:
  - `post_month_status_close_probe` など月次処理の記録または flow 状態で `monthly_scan_hold`（L3009-3013）
  - battle 系 decision 137件で `battle_grind`（L3017-3018）
  - 全城の在城数最大 1 で `no_source`（L3029-3032）

fingerprint / incident_id を現行コードの式で再計算し、いずれも一致した。

- `_fingerprint`（`month_audit.py` L3112-3115）:
  `sha256("1|zero_sortie|monthly_scan")[:16]` = `f29ac36b2bedefde`
- `_incident_id`（同 L3107-3110）:
  `sha256("g1190-ch1v9|1190|1|1-7")[:20]` = `9eb53c159cdb391fc4e9`
- ch1 1-9 コメント:
  `sha256("g1190-ch1v9|1190|1|1-9")[:20]` = `398e0437026d3d494ee4`
- 比較対象 #5463 ch1 1-5:
  `sha256("g1190-ch1v9|1190|1|1-5")[:20]` = `519c6a1876ad8ecbeb48`

## 2. v1485 は v1489 に含まれるが、本件機構はカバー対象外

`git merge-base --is-ancestor` で確認すると、v1485 の修正コミット `8c346ca01` は現在の main と v1489 コミット `3402a6944` の両方の祖先である。issue のラン時刻（ch1 1-7 開始 2026-11-24T19:15:26Z）も v1489 マージ後であり、incident evidence の `bot_version` は `hanjuku-chart-v1489-discharge-last-general` そのものである。

v1485（PR #5468）が直したのは次の2点である。

1. `orders._boss_target_unlocked` で `roster_probe` / `source_probe` をボスロックから免除する（現行 `orders.py` L683-693）。
2. `orchestrator.plan_starved_rearm` で、通常城未制圧の間は機械ボス計画のボス step を active にしない（現行 `orchestrator.py` L664-675）。

#5463 では、この欠陥によって `plan_map_activate` → `castle_jump` → `boss_target_locked` → pending へ戻るループが月内に繰り返された。

一方、#5487 ch1 1-7 の埋め込み JSON では、同じループを構成する決定がすべて 0 である。

| decision | #5463 ch1 1-5 | #5487 ch1 1-7 |
| --- | ---: | ---: |
| `boss_target_locked` | 7 | 0 |
| `plan_map_activate` | 7 | 0 |
| `castle_jump_open` | 6 | 0 |
| `castle_jump_confirm` | 5 | 0 |
| `source_not_under_cursor` | 4 | 0 |

さらに #5487 では `plan.active` は `None`、監査投影の `chart_plan` は `orders=[]` / `source=None` / `request_id=None` で、plan の pending ステータス（`A:ff1dcf73:M1d83df` / `M4a2241` / `M6542e7`）は少なくとも採用済み chart_plan の注文オブジェクトを伴わない残存ステータスである。`next_order`（`orders.py` L2173-2270）は chart/interim/pending retry を走査するが、仮にそこへ残る攻撃注文があっても、後述の1将軍城ガードで停止するため結論は変わらない。

以上から、v1485 が直した probe/boss-lock ループは ch1 1-7 に現れていない。**v1485 は正しく入っているが、そもそも本件の占有機構ではない**。

## 3. ch1 1-7 の実体: 合法的に出撃源がない

月終了時の世界状態は次のとおりだった。

- 自城: アルマムーン / キカンドン / ジョンリギ / ナキューメラ
- 在城: アルマムーン `どうし` / キカンドン `ゼウス` / ジョンリギ `クミン` / ナキューメラ `ヴィーナス` — **全城ちょうど1名**
- 未制圧の通常城: ゴーメン / スペンソニア / カストーラ（ch1 の城リストは `hanjuku_chart.py` L411-413）
- `world.sorties` は空。`launched_orders` に `A:55234f40:M662d2b` が残るが、ステータスは `launched` であり pending retry ではない。

ch1 の `sender_min` は 2（`hanjuku_chart.py` L1339-1342; guard-free は ch9 のみ、L1344-1353）。`interim_candidates` も `next_order` も、この条件を満たさない1将軍城を通常の出撃元にしない。`_source_spare_missing` は `_reserve_source_guard` 対象について自由将軍数 2 未満を止める（`orders.py` L2651-2707）。

この状態は `_sole_starved`（`replan.py` L2231-2251）に一致し、`_defense_gate` は「出撃元に将軍2人以上を残す制約で候補0件。2人目の参入を待つ」という hold を設計どおり返す（`replan.py` L2254-2318）。過去の検証でも同型は (b) と判定されている。

- VERIFY-5257: 「合法的に出撃源ゼロ」家族、`_sole_starved` → `chart_defense_wait`、結論 (b)
- VERIFY-5264: probe 完走後に `chart_defense_wait` へ入る同型、結論 (b)

月内 decision でも `chart_defense_wait` はまだ記録されていないが、それは月次・戦闘で `_off_chart` の評価へ十分に進まなかったことと矛盾しない。重要なのは、評価の有無ではなく、**どの経路でも開始可能な実出撃注文が存在しない**ことである。

占有の主な内訳も、この合法的待機と矛盾しない。

- battle 系 decision 137件、`monster_menu_choice` 88件、`battle_draw_hold` 18件。`battle_grind` の発火は契約どおり。
- `plan_starved_rearm` は4回あるが、これは戦闘・月次で map へ出られない間に計画を組み直す既存の補助経路（`orchestrator.py` L642 以降）であり、#5463 のボス step 再活性化ループではない。
- 月次処理は `post_month_status_close_probe` / `month_sub_open` / house 系として観測され、`monthly_scan_hold` の信号条件は満たす。しかし実害の主因は「月次処理が実行可能な出撃を潰した」ことではなく、そもそも合法な出撃源がなかったことである。

## 4. 翌月の回復が設計どおりであることの裏付け

incident の `after` は `complete: false` / `early: true` で、実測窓は 165.0秒だった。それでも次が記録されている。

- decisions 241
- departures 2
- launch_events 3
- recovered true
- first_launch_seconds 37.8

最初の出撃系列は、`A:b5c6ea46:M8c8457` でクミンがカストーラへ向かい、`sortie_input` → `card_pick` → `sortie_confirm` → `y_jump` → `sortie_departure_requested` → `order_launched` へ進んでいる。これは出撃フロー自体が恒久的に壊れていなかったことを示す。

#5463 では翌5分が「出撃 0 / 回復 no」だったのに対し、ch1 1-7 は月境界後に実出撃へ復帰している。回復までの時間差はあるものの、VERIFY-5257 と同じく、2人目の将軍・月次処理・名簿確認の後に合法候補が再びできたという読みが自然である。

なお v1489 自体（PR #5481、コミット `3402a6944`）は強制解雇画面で最後の将軍を解雇しない変更と所持金表示の読み取り修正であり、`sender_min`・`chart_defense_wait`・`next_order` の出撃可否を変えるものではない。したがって v1489 導入で本件機構が新規に作られたとは読めない。

## 5. ch1 1-9 再発コメントとの関係

ch1 1-9 のコメントは次を確かに示す。

- 同一ラン `g1190-ch1v9` / generation 1190 の別月 ch1 1-9 で、high confidence の `zero_sortie` が再度記録された。
- incident_id `398e0437026d3d494ee4` は現行式で再計算一致する。
- fingerprint も同じ `f29ac36b2bedefde` なので、監査上は同一 issue への再発コメントとして正しい。

一方、root cause の同一性は示せない。理由は fingerprint が `chapter|kind|primary_signal` だけから作られるためで、#5463 型の probe/boss-lock ループも、本件の合法出撃源ゼロも、どちらも primary が `monthly_scan` なら同じ fingerprint になる。

さらに recurrence コメントは `render_comment`（`month_audit.py` L4005 以降）の compact 形式で、埋め込み JSON・decision counts・world/plan がない。「翌5分: 出撃 0 / 回復 no」とあるが、compact comment には `after.complete` / `early` / window の実長が出力されない。ch1 1-7 自体も `complete: false` / `early: true` の 165.0秒窓だったため、「翌5分」という表示だけから完全な300秒不回復とは断定できない。

したがって ch1 1-9 は **同一シグネチャの再発**ではあるが、現時点のコメント情報だけでは **#5463 修正の再発とも、合法出撃源ゼロの継続とも断定できない**。少なくとも ch1 1-7 の検証結論 (b) を覆す証拠ではない。今後切り分けるには、recurrence comment に `after.complete` / `early` と主要 decision counts（`boss_target_locked` / `plan_map_activate` / `castle_jump_*` / `chart_defense_wait*`）または縮小 JSON を残す必要がある。

## 6. なぜ (a)・(c)・(d) ではないか

- (a) 実欠陥ではない: #5463 型の決定列が皆無で、合法出撃源ゼロという既存の (b) 家族の条件が incident JSON から直接成立する。月後に実出撃へ復帰している。
- (c) 誤検知ではない: 月内 confirmed/departures/order_start/order_launched がすべて 0 で、監査は契約どおり発火した。シグネチャ名は粗いが、signals の発火条件自体は満たす。
- (d) 判定不能ではない: ch1 1-7 については world/plan/decision counts と現行コードの照合で機構を特定できる。ch1 1-9 の root cause は compact comment だけでは不明だが、それは本 incident の判定を不定にするものではない。

## 残件

- ch1 1-9 を独立検証するには、対応する sealed incident JSON か、少なくとも decision counts / world.garrison / plan.chart_plan / after.complete が必要である。現行の compact recurrence comment だけでは #5463 型との切り分けができない。
- 監査 fingerprint は primary signal だけを使うため、同じ fingerprint でも root cause が違う。今後の検証効率には、fingerprint または再発コメントへ `no_source` / probe-loop 指標を残す改善余地がある。
- 本検証ではコード変更しない。上記は改善提案の記録であり、本 PR の実装対象ではない。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `monthly_scan` / signals: `monthly_scan_hold` + `battle_grind` + `no_source` / confidence: `high`
- fingerprint: `sha256("1|zero_sortie|monthly_scan")[:16]` = `f29ac36b2bedefde`
- incident_id: `sha256("g1190-ch1v9|1190|1|1-7")[:20]` = `9eb53c159cdb391fc4e9`
- recurrence incident_id: `sha256("g1190-ch1v9|1190|1|1-9")[:20]` = `398e0437026d3d494ee4`
