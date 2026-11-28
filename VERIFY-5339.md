# VERIFY-5339 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch11 エッグママ

対象: docich#5339 / fingerprint `b246fc5f40680931` / occurrence `d65b8b5089f8b164844a`
判定 `boss_month_zero_sortie` (confidence high) / 月 `ch11 18-5`、観測 1587回 / 355.2秒、max gap 1.4秒
ラン `g1190-ch1v9` / BOT_VERSION `v1447` / ボス `エッグママ` / 残り `ボス`
出撃: 確定 2 / 発射 6 / `order_start` 20 / `order_launched` 7 / 月内ボス出撃 0
シグネチャ: `boss_month_zero_sortie` + `monthly_scan_hold`（月次処理がマップを占有）
+ `battle_grind`（戦闘が長引く）+ `stuck_march`（行軍が停滞）

調査基盤: worktree `/home/hatch/work/docich-5339`、branch `verify/5339-boss-month-zero-sortie`。
行番号は指示どおり `origin/main` = `3a829da9d`（#5343 v1451 + #5355 反映、version
marker `hanjuku-chart-v1451-defense-wait-probe`）のツリー基準。
**観測ラン v1447 は #5310（v1443、merge `3e24879f0`）と #5308（v1446、merge
`57be9b382`）の両修正をすでに含む** — #5307・#5275 型の無制限 companion 待ちは
当該ランで有界化済みであり、本件は別の機構か別の状況要因である。
一方で **#5343（v1451、merge `2cbe3542f`）は観測バージョンに存在した飢餓系
欠陥を潰した後続修正**であり、本件と同じ「待ち系 + probe」領域を触る（後述補足）。
`gh` は本環境で実行が拒否され issue 本文・#5342 本文はプロンプト要点のみで判定。
`python3`/`pytest` の実行も拒否されたためテストは **未実施**。本番 VM SSH なし。
ラン成果物 `hanjuku_decisions.jsonl` / incident JSON / frames は VM 側にあり直接参照できない（「不足している証拠」節参照）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

---

## 結論: (d) 判定不能

**監査の発火は定義どおり正しいが、月内でボス注文が一度も発射されなかった機構の
帰属に必要な決定行がローカルに無く、候補機構の切り分けができない。**
#5307・#5275 と同型の無制限待ちは v1447 で既に有界化されており「既存修正で
カバー済み」(b) とは断定できず、残る候補はいずれも設計上の挙動として説明可能で
「実欠陥」(a) の確定にも至らない。

### 発火経路と数字の内訳（コード確認済み）

- `boss_month_zero_sortie` は `_close_month` で **月クローズ時点の `after_mem` に
  `_boss_phase` が立ち**（`month_audit.py:3360`）、かつ月内 `boss_departures==0`
  で発火する（`:3382-3384`、confidence high）。`_boss_phase`（`:507-541`）は
  「全通常城を保有 ∧ ボス site が残存」— **月途中の一時的な陥落・再奪還は監査の
  判定条件に入らない**（クローズ時点スナップショットのみ）。トラッカー
  `_track_boss`（`:2590-2650`）はフェーズ離脱で `state['boss']` を None に戻し
  再入で `started_at` を再計時するが、その `started_at`/`observations` は
  incident の `boss_month` 節（`:3364-3372`）に載るだけで kind の条件には使われない。
- 発射 6: `_departures`（`:2837-2853`）は `LAUNCH_DECISIONS`（`:103-110`）のうち
  `purpose=='move'` を除いた distinct step。`_boss_departures`（`:2856-2879`）は
  さらに `target ∈ boss_sites or purpose=='boss'` を要求する。
  `order_launched` は `_commit_departure` が `target=order['target']` 付きで
  出す（`flows/map.py:2964` → `orders.py:2521` の `order_`+state）ため、
  **commit 経路のボス発射は正しく boss_departures に載る**。したがって発射 6 は
  すべて非ボス目標 — ボスフェーズ中の非ボス・非 move 発射は実質
  (a) 月途中で失われた城の奪還（`purpose='retake'`、replan.py:1369）、または
  (b) フェーズ開始前（最後の通常城の陥落前）に出た攻撃、のどちらかである。
- `order_start` 20 / `order_launched` 7: dispatch→発射の配管は月内ずっと生きて
  いた（#5307 の `order_start 4`/`launched 0`＝ボス注文が dispatch すらしない
  無記録 `_ready` 停止とは型が違う）。13 件の start→非 launch の内訳は
  counts だけでは分からない（ボス注文の companion 有界待ち→timeout→
  hero fallback/fail の繰り返しもこの形を取り得る）。
- シグネチャの読み（`_signals` `:2917-2966`）:
  - `monthly_scan_hold`（`:2931-2933`）は `post_month_status`/`month_sub_step` の
    行が1件でもあれば立つ — **月次処理は毎月走るルーチンなので弱い識別子**。
  - `battle_grind`（`:2937-2938`）は戦闘決定 ≥20 — 月が戦闘（奪還・防衛戦）で
    かなり占有されていた実測。ボス戦記録が無い（#5342）ので全て非ボス戦闘。
  - `stuck_march`（`:2953-2956`）はクローズ時 `world.sorties` に
    `age_ticks > 3000` の行軍が残存 — 到着上限（`world.py` LONG_MARCH_TICKS=2400、
    ボス目標は L440-441 で cap）を超えても `_refresh_live_marches`（orders.py:1928-1930
    コメント）が生きた行軍として時計を戻し続けた状態。当該将軍は `busy` として
    ボス波の候補から除かれる（replan.py:1197）。
  - `no_source`（`:2949-2952`）が**出ていない** = クローズ時に少なくとも1城の
    駐留一覧が 2名以上 — ただし「名前が載る」と「free な非卵なし余剰が2名いる」
    は別（主人公ガード・busy・卵なし除外が掛かる、replan.py:1230-1238）。
  - `order_start_without_launch`（`:2923-2924`）が出ていないのは departures=6>0 で
    定義どおり。

### #5307 (b) との異同（検証観点1 前半）

同じ kind だが機構は違う。#5307（ch10、v1435）は `_ready` の companion ゲートが
無制限・無記録でボス注文を dispatch 前に止めていた型（order_start 4 / launched 0）。
#5310（v1443、merge `3e24879f0`）は `BOSS_COMPANION_WAIT_TICKS=200`（orders.py:704）の
共有締切を導入し、`_boss_companion_wait_expired`（:707-737、満了時
`boss_companion_timeout` を記録）と `boss_companion_source_wait_expired`
（:743-770、`boss_companion_source_timeout` を記録）で `_ready` の各ゲート
（companion-source :803-822、broken-hero/egg-depleted :823-832）を有界化した。
**v1447 のランではこの経路は有界であり、仮にボス注文がこれらのゲートに掛かっても
約200 tick（観測 ≈4.5/s で数十秒）ごとに timeout 記録か dispatch への前進が起きる**。
355 秒の月をこの経路だけで埋めるには複数ステップの連鎖失敗が要り、その各回に
timeout/failed 行が残るはず — 行が読めないため「同型機構の残存」とも
「設計どおりの有界挙動」とも断定できない。

### #5275 (a) との異同（検証観点1 後半）

#5275 は deploy 層 `boss_companion_required` の無制限ループ。#5308（v1446、
merge `57be9b382`）で有界化済み（deploy.py:2382-2384 の
`boss_companion_required`→`_hold_guarded_sortie`、同 200 tick 締切、満了で実一覧の
主人公を再確認する hero fallback か `failed`→再計画）。v1447 でも有界で、
上限到達ごとに `boss_companion_timeout`/`boss_companion_hero_fallback`/`order_failed`
系が残る。本件のシグネチャには companion 系の記載が無いが、issue 集計は
`_decision_counts` の `most_common(30)` 切り捨て（month_audit.py:2882-2885）で
低頻度行は落ち得るため、欠落を根拠に「到達していない」とも言い切れない。

### #5343（v1451、観測後の修正）との関係（補足）

`2cbe3542f` fix(hanjuku): 2将軍待ちを読み取り専用プローブで解除しない (#5343):
`_defense_gate`（origin/main replan.py:2122）が「`interim_candidates` 非空 →
`defense_wait` 解除」だったのを、候補が `roster_probe` だけなら待機を維持する
（:2151-2154, :2180-2183）よう修正。**v1447 では probe だけの候補があるたび
待機が解除され cycles が積み上がらず 'break' に到達しない欠陥が生きており**、
issue #5324（ch3、月内出撃ゼロ5か月）と同型の飢餓を本ランでも起こし得た。
`gate == 'hold'` は `_plan_pending` 非該当時に暫定 mint 全体を止める
（:3101-3103）ため、sole-starved（全城 ≤1 将軍）状況ではボス波 mint も止まる。

ただし本件への適用には限界がある:

- 'break' は `allow_sole=True` で1将軍城の出撃を許すが、`_boss_wave_pick` の
  `need=2`（:1200-1204）は `allow_sole` を見ない — **#5343 があってもボス波は
  2名揃わないと出ない**。ボス出撃ゼロの直接の救済にはならない。
- 本件は非ボス発射 6 があり「全出撃停止」（#5324 型）とは一致しない。
  sole-starved 説が成り立つには発射 6 をフェーズ前・奪還の活動として読む必要がある。
- probe 候補が `order_start` を消費し `order_launched` には至らない
  （roster_probe は一覧を開いてバックアウトし行軍しない、#5343 コミットの
  コメント「a read-only roster probe never marches」）点は、
  `order_start 20`/`launched 7` の開きの一部を説明し得る。

よって **#5343 は観測バージョンに存在した近縁の飢餓欠陥を潰す修正だが、本件を
カバーするとは断定できない** — 帰属には決定行が要る。

### #5342 との異同（検証観点3）

#5342 は `boss_phase_no_battle`（kind 別、`_track_boss` :2590-2650 —
フェーズが BOSS_NO_BATTLE_SECONDS 連続してもボス戦記録ゼロで発火する
**フェーズ横断**判定）で、月 18-6 / 別 fingerprint / 別 occurrence。本件は
月単位の `boss_month_zero_sortie`（クローズ時フェーズ + 月内発射ゼロ）。
**#5342 の発火は「同一フェーズが 18-5 末から 18-6 へ ≥600 秒連続し、ボス戦が
一度も起きなかった」ことを意味し、本件（ボス発射ゼロ → ボス戦ゼロ）と整合する**。
ただし #5342 は 18-5 のフェーズ被覆率を教えない（フェーズが 18-5 終盤に始まっても
18-6 で発火し得る）し、発射済みだが未到達のボス行軍でも no-battle は立つため、
mint 枯渇 vs dispatch 停止の識別にも使えない。

### 主因候補の切り分け（検証観点2）

order_start 20 / launched 7 / 発射 6 / ボス出撃 0 を説明し得る機構は少なくとも
4系統あり、シグネチャだけでは一意に定まらない:

1. **フェーズが月の一部しか被覆しなかった**（月途中に最後の通常城が陥落して
   フェーズ開始、または奪還チャーンでフェーズが flicker）。発射 6 はその前後の
   攻撃・奪還。この場合ゼロボス発射は不自然でなく (b)/(c) 的。
   識別子: incident の `boss_month.started_at`/`observations`（month_audit.py:3364-3372）。
2. **`_boss_wave_pick` が波を mint できなかった**（設計上の枯渇）。ボス波は
   `need=2`（replan.py:1200-1204 — ゲームが1将軍城の出撃を拒否）かつ
   free な既知将軍で、busy/`general_location_unknown`/主人公除外（:1197-1199）、
   `_boss_eggless` 除外（:1237-1238、:654-668 — 卵なし波は純損失のため不送）、
   HP 0 は最後尾（:1252）。全候補が落ちると probe を代わりに mint する
   （:1210-1226, :1274-1302, :1312-1316）。`chart_interim_hold` は「候補が
   完全にゼロ」の時だけ記録される（origin/main :2222, :2278）ため、probe が
   mint される限り**この枯渇経路は決定行にほぼ現れない**。stuck_march の
   >3000 tick 行軍が free 将軍を占有していた可能性と整合。(b) 的。
3. **ボス注文は mint されたが dispatch/発射へ進まなかった**。`boss_open` は
   ボス経路上の未保有城（`blocking`、replan.py:1124）が解消されるか
   `_retakes_stalled`（RETAKE_STALL_FAILS=3、:324/:359-366）まで閉じる
   （:1131-1139）— 奪還が続く限りボスは候補から落ちる（設計）。
   また `next_order` の各ゲート（`_ready` + `_source in owned` +
   `_source_list_open` + `_last_castle_held`/`_retake_reserved`/
   `_source_spare_missing` 非該当）で pending のまま滞留するなら
   ≥60 tick（ORDER_DISPATCH_STARVE_TICKS、orders.py:1839）で
   `order_dispatch_starved` が記録される（:1879-1924）— 決定行で判別可能。
   この型なら (a) 候補。
4. **ボス発射があったが attribution を欠いた**。`sortie_departed_observed`
   （deploy.py:120、`target=None`）と `sortie_launch_confirmed`
   （map.py:3156, :3245、target/purpose なし）は `_departures` には数えられるが
   `_boss_departures` の条件を満たさない — VERIFY-5307 §副次の既知の
   過少計の余地。発射 6 のどれかがこの経路のボス波なら (c) 的。

## 検出自体の正当性

発火条件（クローズ時フェーズ + 月内 boss_departures==0 + confidence high）は
実測どおりで、1587 観測 / 355.2 秒 / max gap 1.4 秒の密観測は信頼度 high を正当化する。
一方で `_boss_phase` がクローズ時点だけで評価されるため、**フェーズが月の大半を
被覆していなくても kind は同じく発火する** — `boss_month.started_at`/`observations`
が incident に添付される設計でトリアージ側が見分ける前提になっており、
監査ロジックの欠陥とは言えないが、kind 名が示すほど強い主張は持たない。

## 不足している証拠（(d) とした根拠）

- `hanjuku_decisions.jsonl` の 18-5 月の行: 20 件の `order_start` の
  `chart_step`/`target`/`purpose`（ボス target が含まれるか）、`order_dispatch_starved`
  / `boss_companion_source_timeout` / `boss_companion_timeout` /
  `boss_companion_hero_fallback` / `order_failed` / `chart_interim_hold` /
  `chart_interim_order`（ボス target 付き）の有無、および `lost`/`captured` の
  月途中変化（奪還 churn の有無）。これらで候補 1-3 は判別できる。
- incident JSON の `boss_month.started_at`/`observations` と `world.garrison` /
  `world.sorties`（stuck_march 該当行軍の target・経過、2名以上在城の城）。
- issue 本文の決定列（`gh` 拒否で未取得。`most_common(30)` 切り捨てのため
  完全な判別には生の jsonl が本質的に要る）。

## テスト

**未実施。** `python3`/`pytest` の実行が本環境で拒否された（VERIFY-5306/5307 と同条件）。
対象テスト `tests/test_hanjuku_month_audit.py` の存在は確認したが実行していない。

## 結論のまとめ

- (a) 実欠陥 → 機構を一意に帰属できないため断定不可。候補3（mint 済みだが
  dispatch/発射不可）が決定行で確認されればこちら。
- (b) 設計どおり/既存修正でカバー → 候補1・2（フェーズ部分被覆・設計上の
  mint 枯渇）は設計どおりだが、ランが v1447 で companion 系修正済みのため
  「既存修正でカバー」とは言えず、#5343 も need=2 を救わないため本件への
  適用は未確認。残機構が設計範囲内かは決定行次第。
- (c) 誤検知 → 発火条件自体は正しい。attribution 過少計（候補4）の余地は残るが
  確定の証拠も無い。
- **(d) 判定不能** → 上記の不足証拠を明記。`hanjuku_decisions.jsonl`（VM 側）の
  18-5 月行を取得できれば候補1-4 のいずれかへ収束する見込み。
