# VERIFY-5812 — [半熟英雄] 月内出撃ゼロ: ch12 月次処理がマップを占有

検証日: 2026-11-28
対象: azumag/docich#5812 / run `g1190-ch1v9` / generation 1190 / ch12 19-7 / bot `hanjuku-chart-v1597-chikujou-defer-reason`
範囲: read-only 検証。コード修正・コミット・PR 化はしない。

## 結論

**(a) 実欠陥**。

`zero_sortie` の検出は正しい。個々の月次処理・戦闘処理・house scan は bounded でも、`post_month_sortie_floor` が「マップを出撃計画へ明け渡す」と判断した後も、実際の優先順位では house scan などの housekeeping が map planner より先に走る。`order_start` 前には launch-priority latch が存在しないため、house 側は出撃を見送り対象として認識できず、明け渡したはずの map frame を消費できる。現行 main（`hanjuku-chart-v1607-chikujou-noncontiguous`）でもこの優先順位の穴は残っている。

## ハッシュ再計算

`python3` の実行承認が 2 回とも却下されたため、SHA-256 の数値再計算は **未実施** である。検品ゲートで再計算できるよう、現行 main の式と入力値を残す。

現行 main `src/docich/hanjuku/month_audit.py`:

- incident/occurrence:
  - `_incident_id(identity, chapter, month)` lines 3540-3542
  - raw: `f"{identity.get('runtime_id')}|{identity.get('generation')}|{chapter}|{month}"`
  - hash: `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]`
- fingerprint:
  - `_fingerprint(chapter, kind, signals)` lines 3545-3547
  - raw: `f"{chapter}|{kind}|{'+'.join(signals)}"`
  - hash: `hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]`
  - `_build_incident` lines 3721-3724 は `signals=[primary]` で呼ぶ。

本件の入力値:

- fingerprint raw は **`12|zero_sortie|monthly_scan`** のはず。
  - chapter は数値 12 だが f-string で `12` になる。
  - kind は `zero_sortie`。
  - 本件は `order_start` 0 なので departure_failure 系の primary にならず、signals の `monthly_scan_hold` / `monthly_scan_yield` から primary は `monthly_scan`。
  - 期待値: issue の `bbf112c182d10da1`。
- occurrence raw は **`g1190-ch1v9|1190|12|19-7`** のはず。
  - issue JSON の identity は runtime_id `g1190-ch1v9` / generation 1190。
  - 期待値: issue の `8cf4e9ed45f9d390937e`。

構造的には issue 本文の値は現行 builder の入力と一致するが、ハッシュ値そのものはここでは再計算できていない。

## 根拠

### 1. issue の証拠は「出撃計画が始まらない」ことを示す

issue 本文/埋め込み JSON の月内値:

- ch12 19-7、観測 1087 回 / 242.5 秒 / max gap 0.7 秒、confidence high。
- 出撃: confirmed 0 / departures 0 / `order_start` 0 / `order_launched` 0。
- 月内 counts: `order_dispatch_starved` 4、`order_failed` 1、`boss_order_no_start` 4、`post_month_sortie_floor` 1、`post_month_battle_resume` 5、`post_month_location_confirmed` 29、`post_month_status_close_probe` 27、`battle_start` 5、`defense_observed` 5、`chikujou_scroll` 28、`month_sub_open` 2。
- 月末 30 件では、04:16:30Z に `post_month_sortie_floor` が記録され、**同じ観測**で `house_scan_started` が続く。その約 7 秒後に `house_deferred`、さらに concert/yes-no へ進み、04:16:40Z 頃に月が閉じる。`order_start` は一度も出ない。
- 翌 5 分窓では `order_start` 8 / `order_launched` 4 / departures 3 / recovered yes / first launch 87.3 秒。月が明けると同じ run で出撃開始へ到達できる。

この対称性から、出撃不能な計画や人員不足だけの月ではなく、月内の foreground ownership が dispatch を遅らせたことが読み取れる。

### 2. 現行 main でも floor の「明け渡し」は sortie 専用になっていない

現行 main の制御順序:

1. `orchestrator.py` は各観測の早い段階で `order_dispatch_starved(mem)` を計測する（lines 1935-1938）。
2. `post_month_status.step(...)` は map 上で、当月の成立出撃が無く、`floor_yields` 上限内で、`_orders.next_order(mem) is not None` なら `post_month_sortie_floor` を記録して `None` を返す（`flows/post_month_status.py` lines 1853-1880）。ここで `None` は「月次処理が画面を所有しない」という意味。
3. しかし orchestrator はその後も boss_pursuit / map_registration / house / card_census を先に試し、最後に `map_flow.map_step` へ落ちる（`orchestrator.py` lines 2453-2485, 2566-2567）。
4. house 側が出撃を見送るのは `active_sortie_launch_priority(mem)` が返すときだけである（`flows/house.py` lines 1471-1478）。
5. その latch は `map_step` が order を選び、`order_start` を記録するときに `arm_sortie_launch_priority(mem, order)` で立つ（`flows/map.py` lines 2734-2746）。つまり本件のように `order_start` 0 の間は latch が無い。
6. したがって floor が返した map frame を house scan が先に取れる。issue の `post_month_sortie_floor` と `house_scan_started` が同時刻なのは、この経路と一致する。

これは個々の bounded 処理の存在自体ではなく、「明け渡す」と記録した frame が実際には出撃計画へ優先的に渡らない統合欠陥である。

### 3. `order_dispatch_starved` / `order_failed` / `order_start` 0 の意味

現行 main の `order_dispatch_starved`（`strategy/orders.py` lines 2535-2580）:

- `_dispatch_pending` が返す pending/retry order がある間、`order_dispatch_since` から 60 tick（約 90 秒）経つと記録する。
- boss-only phase では True を返し、繰り返し foreground work を 1 frame 譲らせる。通常 phase でも記録だけは行う。
- counter は pending が消える、`next_order` が開始可能な order を見つけられない、または `order_start` で dispatch されると解除される。

したがって `order_dispatch_starved` 4 は「pending 指示が長時間 `order_start` に到達しなかった」ことを示す。単独では startability を完全には証明しないが、本件では少なくとも floor 発火時に `_orders.next_order(mem) is not None` が成立している（floor 条件そのもの）ことと、翌 5 分で 8 回 `order_start` することから、開始可能な仕事がなかった月とは読めない。

`order_failed` 1 と `order_start` 0 は矛盾しない。`order_failed` は当月に選ばれた order に限らず、前月から active を持ち越した attempt が当月内で terminal になれば記録されうる。本番 decision log はローテーション済みで、本件の `order_failed` の step/原因は特定できない。ただし `order_start` が当月 0 なら、その失敗は当月の dispatch 成功を意味しない。

### 4. #5790 / #5806 は本件をカバーしない

- #5770 / PR #5790（v1593）: 切り札一覧 unreadable fallback と empty-kit guard が衝突し、`order_start` 後に `card_list_unreadable_depart` / `sortie_kit_empty_cancel` / `order_failed` を繰り返す欠陥。本件は `order_start` 0 で、card_select / sortie_confirm / departure 系へ到達していない。発生点が前段なのでカバーしない。
- #5801 / PR #5806（v1597）: `chikujou_deferred_sortie` 後の築城 skipped を `chikujou_skip_unexplained` と誤記しないよう、監査の理由名を区別する修正。出撃 dispatch の優先順位を変える修正ではない。しかも 19-7 の月内 counts には `chikujou_deferred_sortie` が無く、launch latch も立っていない。本件の `order_start` 0 を説明・解消しない。
- 現行 main にある `month_sub` open budget（`sortie_started_month` が当月でない限り 6 回で B へ戻す）は、本件の `month_sub_open` 2 では発動しない。また v1607 の築城 scan 改善は築城の1 consumer を減らしうるが、floor 後の house/card_census/monthly 割り込み優先順位そのものは閉じていない。

### 5. #5818 / #5811 との異同

- #5818（同 run、ch12 19-11）: 同じく `post_month_sortie_floor` / `post_month_battle_resume` / battle 長引きを含むが、`order_start` 5 であり signature は「出撃開始後に発射されない」。castle jump や sortie attempt に入った後で発射へ到らない後段経路。本件は `order_start` 0 で dispatch 前に止まる前段経路なので、同じ月次占有シグナルを含んでも同一 root cause とは断定できない。翌 5 分で recovered yes / first launch 89.5 秒な点は、月次境界後に解放される構図を補強する。
- #5811（同 run、ch12 19-3、アルファルファ）: `order_start` 1 後に出撃一覧の本人・カーソルを確認できず `order_failed` になった個別 attempt 失敗。`post_month_battle_resume` が直後に走るなど共通の割り込み構図はあるが、本件のように dispatch へ到達しない問題とは別段階。直接の原因ではないが、同じ run で月次・housekeeping が出撃経路へ割り込む背景証拠になる。

## 未決の点

- SHA-256 の数値再計算は未実施（上記の式と入力値を検品ゲートで再計算すること）。
- 本番 decision log はローテーション済みのため、`order_failed` 1 の step/理由、`order_dispatch_starved` 4 回それぞれの `boss_phase` 値、月前半の正確な占有配分は不明。
- 月次ステータス・築城・戦闘・house scan のどれをどの程度削るべきかは修正設計の範囲外。少なくとも floor が sortie へ渡すと宣言した frame を、launch latch 未成立でも dispatch 優先にする仕組みが必要と考える。

## 参照した現行 main の主な箇所

- `src/docich/hanjuku/month_audit.py` lines 3423-3479, 3540-3547, 3653-3727
- `src/docich/hanjuku/orchestrator.py` lines 1935-1938, 2431-2485, 2558-2567
- `src/docich/hanjuku/flows/post_month_status.py` lines 1807-1880
- `src/docich/hanjuku/flows/map.py` lines 2652-2668, 2734-2746
- `src/docich/hanjuku/flows/house.py` lines 1471-1482
- `src/docich/hanjuku/strategy/orders.py` lines 2519-2580, 2583-2688
- `src/docich/hanjuku/flows/month.py` lines 493-546, 835-852

## 検品ゲート確認（Muse、2026-11-28 19:44 JST、現行 main 59bfc0850）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|zero_sortie|monthly_scan")[:16] = bbf112c182d10da1、SHA-256("g1190-ch1v9|1190|12|19-7")[:20] = 8cf4e9ed45f9d390937e。いずれも issue の値と一致する。
- 結論 (a) 実欠陥を維持する。現行 main のコードで次の4点を直接確認した。
  - `post_month_sortie_floor` は、当月に成立した出撃が無く、`floor_yields` 上限内で、`_orders.next_order(mem) is not None` のときに記録され、月次処理が画面を所有しない意味で `None` を返す。
  - orchestrator はその後も boss_pursuit / house を先に試し、最後に `map_flow.map_step` へ落ちる。house 側が出撃を見送るのは `active_sortie_launch_priority(mem)` が返すときだけである。
  - その latch は `map_step` が order を選び `order_start` を記録するときに `arm_sortie_launch_priority` で立つ。`order_start` 0 の間は latch が無い。
  - zero_sortie の primary 信号は `('monthly_scan', ('monthly_scan_hold', 'monthly_scan_yield'))` で、fingerprint の primary `monthly_scan` は現行 builder の通りである。
- 本件の時系列（`post_month_sortie_floor` と同じ観測で `house_scan_started` が続き、約 7 秒後に `house_deferred`、その後 concert/yes-no へ進んで月が閉じ、`order_start` は一度も出ない）は、この優先順位の穴と一致する。翌 5 分窓で `order_start` 8 / `order_launched` 4 になることから、開始可能な仕事がなかった月とは読めない。修正は Devin Web レーンへ割り当てる。
