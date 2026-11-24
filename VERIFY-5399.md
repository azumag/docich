# VERIFY-5399 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch4 四季王

対象: docich#5399 / fingerprint `a1de189ee8e2e040` / occurrence `fc18a40abe23f1a5235a`
判定 `boss_month_zero_sortie` (confidence high) / 月 `ch4 7-5`、観測 725回 / 1076.7秒 / max gap 5.8秒
ラン `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1460-password-entered-field`（v1460）
ボス: 四季王 / 残り: 四季王城(北東), (北西), (南東), (南西)
出撃: 月内ボス出撃 0 / launch_events 0 / `order_start` 8 / `order_launched` 0
ボスフェーズ: 観測 484回 / 開始 1795513949.6952746
issue 挙がりの決定行: `boss_companion_source` (4-B2) / `order_start` (4-B2, どうし →
四季王城(南東)) / `stale_active_released` / `order_dispatch_starved` ×4 /
ビーツのミヒラギアンへの移動出撃

調査基盤: worktree `/home/hatch/work/docich-5399`、branch `verify/5399-boss-month-zero-sortie`、
HEAD `4692ff34b`（v1467、`hanjuku-chart-v1467-status-panel-owner-unreadable-egg`、
`src/docich/hanjuku/version.py:7` で実測）。行番号はこのツリー基準。
観測ランは v1460 — 本検証の対象機構（`_ready` ゲート）に v1460→v1467 の差分は無い
（`git log -- src/docich/hanjuku/strategy/orders.py` の最新変更は v1446 `#5308`）。
read-only 調査で実装変更なし。`gh` バイナリの起動が環境で拒否され issue 本文・
incident JSON は未取得 — 依頼記載の決定行要約とローカルのコード・git 参照を根拠とした。
`python3`/`pytest` の起動も拒否されたためテストは **未実施**。本番 VM SSH なし。
ラン成果物 `hanjuku_decisions.jsonl` / incident JSON / frames は VM 側にあり直接参照できない。
VERIFY-5397（commit `566bbaed4`、git オブジェクトとして取得）を兄弟検証として併用した。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

---

## 結論: (a) 実欠陥 — #5397 と同一機構（計画層 `_ready` の四季王サイト companion ゲートの無制限・無記録待機）

監査の発火は定義どおり正しい（誤検知ではない）。月クローズ時にボスフェーズが
成立し、月内のボス発射が 0 である実測と一致する。停滞の機構は、兄弟案件
VERIFY-5397 が同一ラン・同一月・同一ボスフェーズについて実欠陥と判定した
**`orders.py:816-822` の `_ready` companion-source ゲートの ch4 サイト除外**と
同一であり、本 worktree（v1467）でも未修正のまま残っている。#5399 の決定行は
その同じ欠陥の「月集計側の観測形」で、別機構の証拠は提示されていない。
#5372 の order-issuance 層枯渇（`order_start` 0 型）でも、#5339 の
「決定行不足で帰属不能」型でもない — 決定行が `_ready` 層の companion
サイクルを直接示す。

### 発火経路と数字の内訳（コード確認済み）

- `boss_month_zero_sortie` は `_close_month`（`month_audit.py:3321`）で、
  クローズ時点の `after_mem` に `_boss_phase` が立ち（`:3360`、定義 `:507-541`）
  かつ `boss_departures == 0`（`:3382-3384`）で発火する。confidence high は
  `most_common(30)` を経ない `_confidence`（`:2969-`）が観測密度（725回/
  1076.7秒・max gap 5.8秒）から正当に出す。
- ch4 の `_boss_phase` は `chart.boss_sites(4)`（= `SOURCE_BOSS_SITES[4]`、
  四季王城4地点、`hanjuku_map_sources.py:95`）を残存対象とし、通常城
  （`castles(4)` からサイトを引いた集合 — `'ボス'` は `CASTLES[4]` に無い、
  `hanjuku_chart.py:466-476` が ch4 を skip）が全て自軍なら成立。
  残り4サイト全て未所有で発火 — issue の残りリストと一致。
- `boss_departures`（`:2856-2879`）は `LAUNCH_DECISIONS`（`:103-110`）のうち
  `purpose=='move'` を除き、`target ∈ boss_sites or purpose=='boss'` を要求。
  発射 0 は「サイトへの行軍自体が存在しない」ことを意味する。
- `order_start` は注文が active 化した時点で `flows/map.py:2664` が記録し、
  同一 step の再活性化でも再記録される（`:2623` で `active` を立て直すたび）。
  `order_launched` は `_commit_departure`（`map.py:2964`）→
  `_finish_order('launched')` が出す唯一の記録で、move 注文も含めて数える
  （`month_audit.py:3096` の `counts.get('order_launched')` は purpose を問わない）。
  **`order_start 8` / `order_launched` 0 は「8回の活性化が全て発射前に pending
  または破棄へ戻った」ことを意味する**（#5397 の phase 窓では 7 で一致）。

### 発火層（#5397 と同一）

- `orders.py:816-822`: `_ready` は `_is_boss_order` かつ `companion_source is
  None` かつ出撃元名簿既知で `return False` するが、`order.get('target') in
  chart.boss_sites(4)` が先に真になるため `_boss_companion_source_wait_expired`
  （`:743-770`、200 tick）に**到達しない**。短絡でタイマー自体 arm されず、
  `boss_companion_source_timeout` も記録されない — 無記録・無制限の待機。
- 同 `:823-832`: `_broken_hero_order`/`_boss_egg_depleted` ゲートの締切解放も
  `order.get('target') not in chart.boss_sites(4)` を要求し、サイト注文を除外。
- deploy 層は有界: `deploy.py:2393-2405`（general_list 段の
  `boss_companion_required` → 共有 200 tick 満了後に実一覧の主人公確認で
  `boss_companion_hero_fallback` か `_finish_order('failed')`）と
  `:2513-2519`（card_select/sortie_confirm 段）は全ボス注文に有界。
  `_ready` が落ち続ける限りこの有界段には届かない。
- この除外は **#5310（v1443）が意図的に残した領域**であり、テストのコメントも
  「Chapter-4 seasonal sites intentionally retain their separate measured
  egg-holder gate」（`tests/test_hanjuku_boss_companion_wait_bound.py:108-109`、
  対象は ch10）と明記する。VERIFY-5307 が記した「残存する非カバーは ch4 召喚
  ボス site のみ」の初観測がこの同一ランであり、#5397・#5399 はその両面。
- 卵持ち要件自体は deploy 側の `_order_hero_alternatives` サイト枝
  （`orders.py:61-89`）と `_boss_companion_source` のサイト枝（`:176-195`）に
  実装されており、`_ready` の永久ブロックが owner 意図の根拠ではない
  （VERIFY-5397 §4 と同じ判断）。

### order_start 8 → order_launched 0 の内訳（決定行から辿れる範囲）

提示された決定行とコードから、8回の活性化は以下のサイクルで説明できる:

1. **4-B2（どうし → 四季王城(南東)）**: `boss_companion_source`（`orders.py:2044`）
   は `_follow_general` が `_ready` 通過後（`next_order` `:2001-2005`）にしか
   記録しない。つまり少なくとも1回、`_boss_companion_source` が非 None
   （`best` 非空の城が見つかったか、本城名簿未読のショートカット
   `orders.py:190-193`）で `_ready` を通り、かつ発見城がチャート出撃元
   ネクスドリアと異なり `source_override` が立った。その活性化が
   `order_start` となった。実一覧で適格な卵持ち代役が読めなければ
   deploy の `boss_companion_required`（200 tick 有界）→
   `boss_companion_timeout` → 主人公不在/不安全なら
   `_finish_order('failed')`（`deploy.py:2398-2422`）で pending/failed へ戻る。
   **「すり抜け → order_start → 有界 deploy 保留 → pending 戻し」の反復**は
   VERIFY-5397 が同一フェーズで同定した観測形そのもの（7回と8回の差は
   フェーズ窓 vs 月窓の母数差）。
2. **`stale_active_released`（`map.py:1194 or :1205`）**: 非 corridor 型は、
   active step が再構築された `chart_plan` に含まれない時に解放される。
   `_mechanical_boss_plan`（`replan.py:2455-2574`、site_bosses 枝 `:2516-2548`）
   や interim 再計画で計画が組み替わるたび、進行中の試行が放棄される —
   発射に届かない `order_start` の1消費先。
3. **`order_dispatch_starved` ×4（`orders.py:1879-1924`）**: pending 注文が
   60 tick dispatch されない継続エピソードごとに1回記録（ボスフェーズでは
   前景ワークへ1 frame 譲る、発火は `boss_only_phase` `:1842-1860` — ch4 の
   サイトも対象）。`next_order` が何も選べなければ `map.py:2588-2590` で
   `order_dispatch_since` が reset され再 arm されるため、×4 は「pending が
   溜まっては空く」を4回繰り返した実測。**滞留の可視化であり原因ではない**が、
   `_ready` に阻まれたサイト注文が pending のまま溜まる本件機構と整合する。
4. **ビーツ → ミヒラギアンの移動出撃**: `replan.py:1638-1675` の companion
   ペアリング機構（`pending_sites` 全てで `_boss_companion_source is None`
   の時だけ mint される `purpose='move'` 注文、「ボス出撃の同伴者を揃える」）。
   **この記録自体が「サイト注文全てが companion 不在で pending」という
   `_ready` ブロック状態の直接証拠**であり、設計どおりの救済が実行された —
   しかし月内に発射へ結実しなかった。move 発射は `boss_departures`/
   `launch_events` の集計対象外（`purpose=='move'` 除外、`:2845, :2865`）。
5. 残る `order_start` の候補: フェーズ前の通常城攻略/奪還（月 725 観測のうち
   フェーズは 484 観測で、月前半 ~241 観測の非フェーズ部分がある）、他の
   4-Bx の同型すり抜け、interim probe（`roster_probe`/`source_probe` も
   `order_start` に乗る、VERIFY-5372 確定済み）。incident の
   `decisions.before` 行が無いため個別の帰属は留保（§「不足している証拠」）。

なお issue 要約の「ビーツの移動出撃 (`order_launched`)」と `order_launched` 0
の表記は厳密には矛盾する（move の `order_launched` 行も `counts` に入る）。
実際の移動発射が `sortie_departed_observed`/`sortie_launch_confirmed`/
`goal_dispatch` 等の別決定名で記録されたか、要約側の表記揺れのどちらかで、
incident の counts/行が無いと確定できない。判定には影響しないが残件とする。

### #5397（同一ラン・同一月・同一フェーズ）との異同

**同一機構。** #5397 は `boss_phase_no_battle`（フェーズ横断、600 秒閾値）で
発火し、本件は `boss_month_zero_sortie`（月クローズ時点のフェーズ + 月内
ボス発射 0）で発火した — **同じ停滞を 2 つの計器が別 kind で数えた**。
#5397 の `order_start` 7 はフェーズ窓内、本件の 8 は月全体で、母数の差は
窓の広さで説明できる。本件側にだけ現れる `order_dispatch_starved` ×4 と
ペアリング move はいずれも同じ `_ready` ブロックの副産物（滞留の計測と
設計どおりの救済試行）であって、別の欠陥層を示さない。

### #5339（boss_month_zero_sortie、(d) 判定不能）との比較

#5339（ch11 18-5、v1447）は「発火は正しいが機構の帰属に必要な決定行が無く、
候補4系統の切り分けができない」として (d)。本件と違う点は:

- **兄弟検証が同一フェーズの機構を確定している**。#5397 がこの同一ラン・
  同一月・同一フェーズを調べ、`_ready` の ch4 サイト除外を行番号つきで
  実欠陥と判定済み。本件の決定行（`boss_companion_source` /
  `order_dispatch_starved` / ペアリング move）は全てその機構と整合し、
  #5339 で切り分け不能だった候補（フェーズ部分被覆・mint 枯渇・dispatch
  停止・attribution 欠落）のうち「dispatch 停止」が行レベルで支持される。
- ch4 では `_boss_wave_pick` が構造的に起動しない（`'ボス' ∉ CASTLES[4]` →
  `boss_open` 常 False、`replan.py:1131-1139`）ため、#5339 の候補2（波の
  mint 枯渇）は本件では不成立 — サイト攻略はチャート注文系に全委託されており、
  その注文が `_ready` で止まる経路が一意に残る。

### #5307（boss_month_zero_sortie、(b) 設計どおり/既存修正でカバー）との比較

同じ `_ready` 層の companion ゲート欠陥だが、#5307（ch10）の発火経路は
**#5310（v1443）の共有締切でカバー済み**だったのに対し、本件は同修正が
**意図的に除外した ch4 サイト注文**という「非カバー領域の初観測」である。
観測形も違う: #5307 はボス注文の `order_start` が無い型（ボス注文が一度も
`_ready` を通らなかった）、本件は companion 発見/名簿未読のすり抜け経由で
`order_start` が出る型。同一ゲートの別観測形で、(b) とは判定しない — 
未マージの #5397 修正を「既存修正」とみなすかは判断が分かれるが、本 worktree
（v1467）に修正は**未反映**であり、ローカルの `fix/5397-boss-companion-site-wait`
ブランチも現時点で `orders.py` に対する差分を持たない（tip `ca1e84372`、
v1463 相当のスナップショット）。よって実欠陥として記録する。

### #5372（boss_phase_no_battle、order-issuance 層）との異同

#5372 は `order_start` 0 — 注文が発行段階で止まった「上流」欠陥
（sole-starved + `need=2` の `_boss_wave_pick` + probe 抑止）。本件は
`order_start` 8 / `order_launched` 0 で、注文は mint も dispatch もされている
「下流」停滞。層が違い、機構も別（#5372 は ch3 でサイト機構を持たない）。

## 検出自体の正当性

- 発火条件（クローズ時フェーズ + 月内 boss_departures==0 + confidence high）は
  実測どおり。725 観測 / 1076.7 秒 / max gap 5.8 秒の密観測は high を正当化する。
- `_boss_phase` はクローズ時点スナップショットのみで評価されるため、月途中の
  一時的な陥落・再奪還（lost churn）は条件に入らない — `order_dispatch_starved`
  の一部が通常フェーズ側エピソード（`boss_phase: False` 記録、`:1904-1916`）
  を含み得る点だけ注記する（incident の observed_metric が無いと個別同定不可）。
- 監査側の副次ギャップ（VERIFY-5397 と同じ、判定を左右しない）:
  `boss_names` が `SEASON_KINGS` を含まないためサイト戦の `battle_start` は
  名称一致では拾えず（`month_audit.py:587, 624-626`）、進行中サイト戦のみの
  ケースは `boss_phase_no_battle` の盲点。また本ツリーでは
  `boss_cell_missing`/`boss_pursuit_idle` が ch4 でも構造的に常時発火する
  （`:628-633`、ch4 は `boss_cell` を持たず `boss_pursuit` は ch9 専用）—
  シグナル列のノイズとして既知。`55b599db6`（fix(audit): scope moving-boss
  signals、v1463・remote branch `origin/fix/5397-static-boss-audit-signals-v1463`）
  がこの領域を触るが、本 worktree HEAD への反映の有無は今回の検証範囲外。

## §修正の方向（実装はしない）— #5397 修正との重なり

**対象コードは #5397 の修正と完全に同一であり、#5397 の修正がそのまま本件を
カバーする。** 別途の #5399 固有修正は不要。

- `src/docich/hanjuku/strategy/orders.py:816-822`: サイト注文の短絡
  （`order.get('target') in chart.boss_sites(4) or ...` の第一項）を外し、
  `_boss_companion_source_wait_expired`（`:743-770`）をサイト注文にも適用する。
  200 tick 締切後は既存の有界 deploy 経路（実一覧評価 → 代役/主人公
  fallback/bounded fail）へ進ませる。owner のサイト卵持ち要件は
  `_order_hero_alternatives` サイト枝（`:61-89`）と deploy 評価に残るため、
  `_ready` での永久ブロックだけを解消する改修で済む。
- 併せて `:829-831` の `_broken_hero`/`_boss_egg_depleted` ゲートのサイト除外も
  同締切（`_boss_companion_wait_expired`）で解放するか検討。
- 待機の可視化: サイト注文が `_ready` でブロックされている間、
  `order_dispatch_starved` とは別に「どのゲートが効いているか」の
  1回/エピソード記録を入れる（現状は無記録で監査の決定内訳から原因が読めない）。
- 全名簿が卵持ち不在で持続する場合の終了条件（上限後に site 注文を bounded
  fail → 卵回復/人員移動の interim 注文へ回す等）は owner 確認が要る設計変更。
  本件のペアリング move（ビーツ → ミヒラギアン）が月内に結実しなかった点は
  この終了条件設計の実測材料になる。
- 回帰テストの観点: `tests/test_hanjuku_boss_companion_wait_bound.py` の
  `test_strategy_companion_source_gate_expires`（ch10 固定）と同型で
  「ch4・通常城全所有・出撃元名簿既知・適格卵持ちなし」のサイト注文が
  `_boss_companion_source_wait_expired` 後に `_ready` を通ること、
  `boss_companion_source_timeout` が1回だけ記録されることを固定する。
  `tests/test_hanjuku_season_king_sites.py` の `_ready` 契約
  （`test_g1075_after_the_seventh_castle_has_a_site_sortie_ready` 等）は
  `garrison` 未設定でゲート不発のため修正の影響を受けない。
  `test_hanjuku_boss_dispatch_starved.py`、`test_hanjuku_boss_roster_reprobe.py`
  の既存契約（ペアリング/reprobe/滞留計測）は修正対象外のはず。
- #5397 修正（進行中）との差分: 修正対象は完全に同一で、本件が追加するのは
  「月集計 kind でも同じ停滞が発火した」という回帰観点のみ。監査側の
  kind 判定自体に変更は要らない。

## 不足している証拠（留保）

- `hanjuku_decisions.jsonl` の ch4 7-5 月の行: 8 件の `order_start` 全件の
  `chart_step`/`target`/`purpose`（4-Bx サイト注文の内訳と、フェーズ前の
  攻略/奪還・probe の混入）、`boss_companion_required`/`boss_companion_timeout`/
  `boss_companion_hero_fallback`/`order_failed`/`site_step_rearmed`/
  `boss_roster_reprobe`/`chart_interim_hold` の有無、`order_dispatch_starved`
  ×4 の `boss_phase` フラグと `pending` リスト、`lost`/`captured` の月途中変化。
  `most_common(30)` 切り捨てで低頻度行は落ち得る（`_decision_counts` `:2882-2885`）。
- incident JSON の `decisions.before`（直近30行）、`world.garrison`
  （卵持ち状況 — `_ready` ゲートを効かせた実測状態）、`boss_month` 節、
  `sorties.moves`（ビーツ移動の発射記録名の確定）。
- 仮に `order_start` にボス注文以外が多く含まれ、`boss_companion_required` 系が
  counts 内に無い場合でも、`_ready` ブロック自体はコード上未修正のまま残るため
  判定は変わらないが、停滞の内訳（すり抜けサイクル vs 純粋な `_ready` 長期
  ブロックの割合）の精度は上がる。

## テスト

**未実施。** `python3`/`pytest` の起動が本環境で拒否された（VERIFY-5306/5307/
5339/5372 と同条件）。静的なコード照合のみ。
関連テスト（実行すれば関係するもの、未実行）:
`tests/test_hanjuku_month_audit.py`（`boss_month_zero_sortie` kind）、
`tests/test_hanjuku_boss_companion_wait_bound.py`（`test_strategy_companion_source_gate_expires`、
deploy 3段の有界性）、`tests/test_hanjuku_season_king_sites.py`
（サイト `_ready`/キング撃破契約）、`tests/test_hanjuku_boss_dispatch_starved.py`
（滞留計測契約）、`tests/test_hanjuku_boss_roster_reprobe.py`（reprobe 機構）、
`tests/test_hanjuku_season_site_general.py`（companion 選定契約）。

## 結論のまとめ

- **(a) 実欠陥**: `orders.py:816-822` の ch4 サイト注文除外による無記録・
  無制限の companion-source 待機。#5397 と同一機構・同一フェーズを別 kind の
  計器が観測した兄弟案件。#5397 の修正（進行中）がそのままカバーする。
- (b) ではない: 修正は未マージで、本 worktree でも欠陥が残存（v1467 で実測）。
  deploy 層の有界化（#5308/#5310）は `_ready` を抜けた注文にしか効かず、
  本件の発火層をカバーしない。
- (c) ではない: 監査メトリクスと実挙動は一致（フェーズ + ボス発射 0 の実測）。
- (d) ではない: #5339 と違い、兄弟検証が同一フェーズの機構を行番号つきで
  確定しており、提示決定行は全てその機構と整合する。個別注文の内訳精度に
  残る留保は「不足している証拠」に記録した。
