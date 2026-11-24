# VERIFY-5372 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch3 プリンス

- 対象: docich issue #5372 / fingerprint `6d87e146bd04514e` / occurrence `b642ebf054a371e60e0c`
- 判定: `boss_phase_no_battle`（confidence: high）、ch3 / 月 6-5、ボス プリンス
- ラン: `g1342-1fe0734c` / v1455。ボスフェーズ観測 598回 / 865.8秒。
- 出撃: 確定 1 / 発射 2 / `order_start` 0 / `order_launched` 0
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: worktree HEAD `46d17f1a5`（v1455、`hanjuku-chart-v1455-password-in-game-retry`、PR #5367 マージ済み。`src/docich/hanjuku/version.py:7` で実測）。行番号はこのツリー基準。
- `gh` バイナリの起動が本環境で拒否されたため、issue 本文・監査 JSON は未取得。
  依頼記載の要点とローカルコード、および同一ランを扱ったリポジトリ内の検証済み
  ドキュメント（VERIFY-5324 / IMPLEMENT-5082、git オブジェクトとして取得可）のみを根拠とした。
  `python3`/`pytest` の起動も拒否されたためテストは **未実施**。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: (a) 実欠陥 — #5082 と同一欠陥クラス（ボス注文が発行されない上流の枯渇）。#5342 とは別機構

`order_start` は deploy フローが注文の実行を始めた時点で記録される
（`src/docich/hanjuku/flows/map.py:2664`。`roster_probe`/`source_probe` の読取専用
注文でも記録されることは VERIFY-5324 で確定済み）。865.8 秒・598 観測の
ボスフェーズで `order_start` が **一切の注文種別で 0** ということは、停止箇所は
ボス注文の **発行** 段階であり、出撃（`order_launched` 0）・行軍・入城・戦闘開始の
どの段階にも到達していない。「発射したが届かない」#5342 とは別の層の欠陥である。

ボスフェーズでは通常城は全て奪取済みで残る標的はボスのみである
（監査側 `_boss_phase`、month_audit.py:507-542）。この局面で現行 main がボス注文を
一件も作れない機構が、コード上に複数確認できる。同一ラン g1342 は
docich#5324 で「全城が実測 1 将軍、国の将軍数 24 に対し在城把握 4」という
**sole-starved 膠着状態**にあったことがオーナー実測済みであり、本フェーズでも
同じ前提が成り立つ可能性が高い。その前提での停止経路:

1. **`_boss_wave_pick` が `need = 2` をハードコードしている**
   （`src/docich/hanjuku/strategy/replan.py:1204`）。各城に自由将軍が 2 人いなければ
   ボス波は一切 mint されない（走査は :1230-1263）。引数の `allow_sole`
   （:1169）は関数内で一度も参照されない **dead parameter** であり、
   `_defense_gate` の `'break'`（月次評価による単将軍解禁フォールバック）は
   ボス波に効かない。
2. **`_defense_gate` の `'hold'`/`'break'` どちらでも空のまま止まる。**
   `'hold'` は `_off_chart` が早期 return して mint 自体を行わない
   （replan.py:3101-3103）。`'break'` でも機械チャート路では
   `_mechanical_interim(allow_sole=False)`（:3063-3064）が呼ばれ、
   `interim_candidates` が空なら `chart_interim_hold`（no_candidates、
   :2215-2231）を記録して終わる。`_spares` が全城に守備 1 人を残す規則
   （owner 2026-11-06、:629-638）と `sender_min(3)=2`
   （`src/docich/hanjuku_chart.py:1303-1305`）により、1 将軍城からは誰も出せない。
3. **ボスフェーズ唯一の逃げ道 `_stale_boss_roster_probe` はボスを攻めない上に、
   持続的に抑止されうる。** `interim_candidates` で敵城が残っていないとき
   （replan.py:1924-1929）にだけ試される読取専用プローブで、次のいずれかで
   `None` を返す（:2042-2085）:
   - `country_general_count <= located` または未計測（:2047-2053）。
     `located` は `world.garrison` の **最新記録** の単純合計であり、古い
     読み取りに残った将軍名も数えるため、名簿が縮んだ実態より大きく出うる。
   - `_unblocked_boss_source_ready` が True（:2061 → :2021-2039）。この判定は
     「在城 2 名以上かつ自由 1 名以上」で、波の発射条件（自由 **2** 名）より
     弱い。在城 2 名のうち 1 名が行軍中/位置不明の城があると **波は出せない
     のにプローブも抑止される** —— 非対称の欠陥。
   - 全候補城の `garrison_tick` が `STALE_ROSTER_PROBE_INTERVAL`（300、:2018）
     未満、または `busy_sources` に含まれる（:2066-2077）。これは一時的抑止。
4. **`_mechanical_boss_plan` は ch3 を救えない。** 基本チャートのボス注文
   （3-B1 主人公 パタゴーヌ→ボス、hanjuku_chart.py:664-667）を束ねる経路だが、
   `chart.orders(3)` は speedrun chart 廃止済みのため `()` を返し
   （`orders()` → `measured_orders` は `speedrun_enabled` 時のみ、
   hanjuku_chart.py:1329-1354）、`boss_orders` が空で `return False`
   （replan.py:2474-2478）。ch3 のボス攻撃は暫定候補だけに依存する。
5. **発行済みでも pending 注文が出撃不能なら再 mint を抑止する。** pending 注文が
   あれば `_off_chart` は `_plan_pending` で早期 return（replan.py:3061-3062、
   `_plan_pending` は orders.py:922-948）。`live()`（orders.py:939-946）には
   `next_order` が持つ `general_override` の行軍中チェック（orders.py:2020-2021）
   が無いため、「名付けられた将軍が行軍中で開始できない」注文が pending のまま
   `_plan_pending` を満たし、他候補の mint を抑止し続けられる。ch3 のボス行軍は
   `FIXED_LIMIT_CHAPTERS`（world.py:334）により `BOSS_ARRIVAL_LIMIT=800` 観測
   （world.py:317）まで将軍が busy のまま残り、窓は本フェーズの 598 観測より長い。

なお、発射 2 件は `purpose=='move'` を除く出撃系決定（`LAUNCH_DECISIONS`、
month_audit.py:103-110、`_departures` :2837-2854）であり、`order_launched` が 0
なので `sortie_departed_observed`/`sortie_launch_confirmed`/
`sortie_launch_unconfirmed_kept` のいずれか —— フェーズ境界より前に発射された
行軍の出撃確認が窓内に落ちたものと読むのが自然である。

## #5342（ch11 18-6、(a) 実欠陥）との異同

- **層が違う。** #5342 は波が mint・発射された（`order_launched` 12）が、
  `_boss_wave_pick` が ch11 の gateway（hub タタールを介さないと後半に届かない
  構造）を無視して前半の城から出したため、到達せずボス戦 0 —— **下流**の欠陥。
  本件は `order_start` 0 で、注文が **作られない/始められない** **上流**の欠陥。
- **gateway 機構は ch3 には存在しない。** `GATEWAYS` は ch11 のみ
  （hanjuku_chart.py:1285-1292）。`_boss_wave_pick` が gateway を参照しない点は
  本 main でも同じだが（replan.py:1169-1317 に `chart.gateway` の参照なし）、
  ch3 では hub/front/back の概念自体が無い。
- **ボス城ラベルも扱いが違う。** ch3 の `'ボス'` は城テーブルの実測セル
  (306,141)（hanjuku_chart.py:126）で `boss_sites(3)` = `('ボス',)`、
  `boss_open`（replan.py:1131-1139）の `boss in castles` 条件を満たすため
  波の mint 経路は構造的に通る。ch11 の `gate['back']` には `'ボス'` が
  含まれない問題は ch3 では不成立。
- **到達性の制約も無い。** ch3 は road chapter（routes.py:21）だが、
  `routes.allowed`（routes.py:38-44）と `order_allowed`（:70-84）はボス宛を
  無条件許可し、`_march_blocked`（replan.py:31-35）は road 章で常に False。
  つまり ch3 では「出せる将軍がいればどの城からでもボスへ出られる」。
  欠陥は到達性ではなく出撃可能将軍の欠如側にある。
- 共通するのは「監査側の補助信号がノイズを含む」点のみ: `boss_pursuit_idle` は
  `boss_pursuit.step()` が ch9 以外で常に None を返す
  （`src/docich/hanjuku/flows/boss_pursuit.py:523-524`）ため ch3 では常時発火、
  `_build_boss_incident` が `_signals` に `departures=0` をハードコードして渡す
  （month_audit.py:634、VERIFY-5342 と同じ指摘）。

## PR #5375（owner、fix(audit): defer boss no-battle alert while boss sorties are live、v1456）との関係

- #5375 は **監査側の延期** であり、この checkout（v1455）には未マージ
  （git log に対応コミットなし）。live のボス行軍がある間はアラートを遅らせる
  調整であり、**ボス注文を一件も生み出さない実行層の機構には触れない**。
- 本件への適用範囲: 仮に発射 2 件がフェーズ中も live のボス行軍だったなら
  延期はこのアラートを抑えうる。しかし sole-starved 変種（live の行軍が無い）
  では延期の前提自体が成立せず、アラートは同様に発火する。
- したがって #5375 の有無にかかわらず、ボスフェーズ中に `order_start` が
  598 観測ゼロという実行層の状態は別途の欠陥として残る。逆に、
  「live 行軍を正しく待っているだけ」の亜種が真因だった場合でも、
  pending 注文 1 件が再 mint を抑止し続ける構造（orders.py:939-946 vs
  :2020-2021 の非対称）は実行層の設計ギャップである。

## 確実性の注記（残存する不確実性）

- incident JSON（`decisions.before` の最終 30 行、`signals`、`plan.orders`、
  `flow`）は `gh` 拒否と VM 非アクセスのため読めていない。どの変種が
  発火したかの同定には以下の決定行が必要:
  - `chart_interim_hold` / `chart_defense_wait(_eval)` → 候補ゼロの 'hold' 変種
  - `order_dispatch_starved`（orders.py:1879-1924、boss-phase pending 注文が
    60 観測超で記録）→ pending 注文の保留変種
  - `order_start`（probe のみでも記録）が本当に窓内 0 か → probe の抑止確認
  - `signals` の `no_source`（month_audit.py:2949-2952、max 在城 ≤1）→
    sole-starved の直接証拠
  - `world_map_open` / `post_month_status` / `battle_*` / `camp_*` →
    上流フロー占有の変種
- ただし、同定不能なのは変種の選択であって「ボス注文が発行されない実欠陥が
  main に存在する」点ではない。同一ランで #5324（VERIFY-5324.md、
  コミット 9d56b623c）が全城 1 将軍の膠着を実測しており、オーナー自身が
  #5082 の修正（IMPLEMENT-5082.md、コミット 3be65cc11、PR #5309、
  `boss_last_general` 例外、本 main 未マージ）をこの欠陥クラスへの対応として
  作成済みである。

## 判定詳細

- **(a) 実欠陥**: 600 秒超のボスフェーズで注文が一件も開始されないのは設計では
  ない（owner 2026-11-02/11-05 の設計意図はボスへの波の連続投入、replan.py:1118-1139）。
  `need=2` の不在時にボス注文が一切 mint されない機構が main に存在し、
  本ランの実測状態（#5324）とシグネチャが一致する。
- **(b) ではない**: #5324 修正（v1451、probe_only で `defense_wait` を解除
  しない、replan.py:2146-2183）は probe が **mint される** ケースの解除ループを
  直したが、probe が抑止される局面（`_stale_boss_roster_probe` → None）と
  `need=2` 自体は未対応。#5082 の修正（PR #5309）は同じ欠陥クラスを対象とするが
  v1455 未マージ。#5375 は監査側の延期で実行層を直さない。
- **(c) ではない**: 検出は正しい（`_boss_phase` の条件と 600 秒閾値は実測どおり
  満たした）。誤検知的なのは補助信号の `boss_pursuit_idle` のみ。
- **(d) ではない**: 上記の決定行があれば変種は確定できるが、停止層（発行）と
  実欠陥の存在は現行コードと同一ランの実測から確定できる。

## §6 修正案（実装はしない）

1. **`_boss_wave_pick` / `interim_candidates` のボスフェーズ枯渇に bounded 例外を
   入れる** —— PR #5309（#5082、`boss_last_general` 例外: ボスフェーズ +
   `boss_cell` readings≥2 + hold 60 観測超 + 未読城なし + 全実測城 `need=2` 未満、
   1 エピソード 1 注文）のマージがそのまま本変種の修正になる。本件が
   ch3 でも同じ条件でアームすることを確認（`boss_only_phase`、orders.py:1842-1860
   は全章共通）。
2. **`_unblocked_boss_source_ready`（replan.py:2021-2039）の非対称を直す** ——
   「在城 2 名かつ自由 1 名」を「波が実際に出せる（自由 2 名）」に揃え、
   波が出せないのに読取プローブを抑止しないようにする。
3. **`_defense_gate` の `'break'` と `allow_sole` のデッドコードを整理する** ——
   `_off_chart_mechanical` では `allow_sole=False` 固定（replan.py:3063-3064）かつ
   `_boss_wave_pick` 内で未使用（:1169）。ボスフェーズでは #5082 型の bounded
   単将軍波へルーティングするか、引数を実装に合わせて除去する。
4. **pending 注文による再 mint 抑止の非対称を直す** —— `_plan_pending.live()`
   （orders.py:939-946）に `next_order`（:2001-2022）と同じ行軍中
   general_override 判定を入れるか、行軍中に開始不能な注文の待機に上限を設け、
   他候補（probe・別 source の波）の mint を再開させる。
5. **監査側の残件**（VERIFY-5342 と同じ指摘、本件では助言のみ）:
   `boss_pursuit_idle` は追跡が動く章（ch9）以外では出さない
   （month_audit.py:632-633、`boss_pursuit.step()` :523-524）。
   `_build_boss_incident` が `_signals` に `departures=0` を渡す
   （month_audit.py:634）は実測の departures を渡す。
   `BOSS_NO_BATTLE_SECONDS=600`（month_audit.py:51）と ch3 ボス行軍の
   busy 上限 800 観測（world.py:317,334,418-450）の大小関係は #5375 の延期側で
   処理されることを確認する。
6. **テスト**: sole-starved ボスフェーズで `interim_candidates` が空になり続ける
   ケース（全実測・各城 1 将軍・`country_general_count<=located`、
   `enemies` なし）の再現と、例外注文が 1 件発行されることの確認。
   `_unblocked_boss_source_ready` の 1-busy-1-free 城でプローブが抑止されない
   こと。`test_hanjuku_boss_wave_solo.py`（need=2 要件）と
   `test_hanjuku_defense_wait*.py` の既存契約との整合。

## テスト

- `python3`/`pytest` の起動が本環境で拒否されたため **未実施**。
- 関連テスト（実行すれば関連するもの）:
  `tests/test_hanjuku_month_audit.py`（`boss_phase_no_battle`、:342）、
  `tests/test_hanjuku_defense_wait.py`、`tests/test_hanjuku_defense_wait_probe.py`
  （#5324 修正の契約）、`tests/test_hanjuku_boss_wave_solo.py`（`need=2`）、
  `tests/test_hanjuku_boss_wave_arrival_probe.py`、
  `tests/test_hanjuku_boss_dispatch_starved.py`、
  `tests/test_hanjuku_ch11_gateway.py`（#5342 対象機構）。
  未マージ側に `tests/test_hanjuku_boss_last_general.py`（#5082、3be65cc11）。

## 検証で使った根拠（本 worktree HEAD `46d17f1a5`、v1455 の行番号）

- `src/docich/hanjuku/month_audit.py`: `BOSS_NO_BATTLE_SECONDS` L51、
  `LAUNCH_DECISIONS` L103-110、`_boss_phase` L507-542、`_track_boss` L2590-2650、
  `_build_boss_incident` L612-732（`_departures` 呼出 L619、signals L627-635、
  sorties.order_start/order_launched L695-696）、`_departures` L2837-2854、
  `_signals` L2917-2966（`no_source` L2949-2952）、render 出撃行 L3677・
  boss_phase 行 L3982-3984
- `src/docich/hanjuku/strategy/replan.py`: `_march_blocked` L31-35、
  `interim_candidates` L1026-、mechanical `boss_path` L1076-1078、gateway 除外
  L1112-1117、`boss_open` L1123-1139、`_boss_wave_pick` L1169-1317
  （`need = 2` L1204、`allow_sole` 未使用、probe 群 L1210-1316）、
  boss wave ループ L1393-1433、`STALE_ROSTER_PROBE_INTERVAL` L2018、
  `_unblocked_boss_source_ready` L2021-2039、`_stale_boss_roster_probe`
  L2042-2085、`DEFENSE_WAIT_MAX_CYCLES` L2096、`_sole_starved` L2099-2114、
  `_defense_gate` L2122-2186（#5324 修正 L2146-2183）、`_mechanical_interim`
  L2189-2300、`_mechanical_boss_plan` L2455-2595（ch3 で return False L2474-2478）、
  `_mechanical_chart` L2598-2646、`_off_chart_mechanical` L3041-3066、
  `_off_chart` L3069-3172（'hold' 早期 return L3101-3103）
- `src/docich/hanjuku/strategy/orders.py`: `_order_hero_alternatives` L53-121、
  `_boss_companion_source` L170-207、`BOSS_COMPANION_*_TICKS` L704-770、
  `_ready` L773-848、`_orders`（interim 含む）L866-882、`_plan_pending` L922-948、
  `_source_list_open` L951-968、`boss_only_phase` L1842-1860、
  `ORDER_DISPATCH_STARVE_TICKS` L1839、`order_dispatch_starved` L1879-1924、
  `next_order` L1927-2023、`_source_spare_missing` L2378-2434、
  `_last_castle_held` L2900-2905
- `src/docich/hanjuku/strategy/routes.py`: `ROAD_CHAPTERS` L21、`allowed` L38-44、
  `order_allowed` L70-84、`PROBE_INTERVAL` L17
- `src/docich/hanjuku_chart.py`: `BOSSES[3]='プリンス'` L1198、
  `BOSS_CASTLES[3]='ボス'` L457、ch3 `'ボス': (306,141)` L126、
  `GATEWAYS`（ch11 のみ）L1285-1297、`CORRIDORS`（ch9 のみ）L1243-1246、
  `sender_min`/`guard_free` L1300-1318、`orders()` L1352-1354、
  `CHART_ONLY_CHAPTERS` L1329
- `src/docich/hanjuku/os_layer/world.py`: `SORTIE_BUSY_TICKS` L316、
  `BOSS_ARRIVAL_LIMIT` L317、`FIXED_LIMIT_CHAPTERS` L334、
  `march_arrival_limit` L418-450、`_en_route` L453-471、
  `LOCATION_UNKNOWN_TTL` L345
- `src/docich/hanjuku/flows/map.py`: `order_start` L2664、`_off_chart` 呼出
  L2559/L2585、`_order_start_due` L1120-1140
- `src/docich/hanjuku/flows/boss_pursuit.py`: `step()` の ch9 限定 L521-527、
  `track()` L153-171
- `src/docich/hanjuku/version.py`: v1455 L7
- 関連コミット（git オブジェクト）: `9d56b623c`（VERIFY-5324.md、同一ラン
  g1342・ch3 sole-starved 実測）、`2cbe3542f`（#5324 修正 v1451、main 反映済み）、
  `3be65cc11`（IMPLEMENT-5082.md + `boss_last_general` 例外、未マージ）、
  `179dd2323`（VERIFY-5342.md、ch11 ゲートウェイ欠陥）

## 手順上の注記

- read-only 検証。コード編集・本番 VM 操作・push・PR 作成なし。
- `gh` 不可のため issue 本文・監査 JSON・PR #5375 の差分は未取得。
  PR #5375 の内容は依頼記載（defer while boss sorties are live、監査側の延期）
  のみに基づく。決定行による変種の最終同定は incident JSON が必要（上記
  「確実性の注記」）。
- `python3`/`pytest` 起動が拒否されたためテスト未実行。行番号は本 worktree
  HEAD（46d17f1a5）のツリー基準。
