# VERIFY-5391 — [半熟英雄] 月内出撃ゼロ: ch4 出撃開始後に発射されない

- 対象: docich#5391 / fingerprint `f097be01bf69d359` / occurrence `d0a0b4627df75158981e`
- 判定 `zero_sortie` (confidence high) / 月 `ch4 6-12`、観測 485回 / 752.2秒
- 出撃: 確定 0 / 発射 0 / `order_start` 2 / `order_launched` 0
- ラン: `g1342-1fe0734c` / `hanjuku-chart-v1459-merchant-listed-unknown`

調査基盤: worktree `/home/hatch/work/docich-5391`、HEAD `7a3c45a96`（v1459、
issue 観測ランと同一バージョン。`git fetch origin main` 不可のため、依頼時点の
main 先頭と一致する HEAD を base とした）。`src/docich/hanjuku/version.py:7`
= `BOT_VERSION = 'hanjuku-chart-v1459-merchant-listed-unknown'` で確認。
read-only 調査で実装変更なし。`gh` バイナリは本環境で起動が拒否され issue 本文
（月内の決定一覧・前月の計画・signals 列挙）は未取得。代わりに依頼提示の要点
（タイトル・sorties 行）とローカルのコード・git ref・作業記録を根拠にした。
本番 VM SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / incident JSON は
VM 側にあり直接参照できない。`handoff.md` 正本は `DOCICH_HANDOFF_PATH`
未設定のため未読と記録する（AGENTS.md §1）。

---

## 結論: (b) 設計どおり、または既存の修正でカバー済み

**監査の検出自体は正確（誤検知ではない）: 月初から完全観測の 752.2 秒間に
敵向け出撃が確定・発射とも 0 で、`order_start` が 2 件あったのは事実。
ただし `order_start` は読み取り専用の roster/source プローブでも記録される
ディスパッチ起点であり、「開始したが発射されない」を生む経路は現行コード上、
すべてオーナー承認済みの設計（防衛待ち・護衛ガード・プローブ・有界キャンセル）
で構成できる。#5324 の実欠陥部分（defense_gate の probe-only 解放と本拠プローブ
不可）は v1451（PR #5343、コミット `2cbe3542f`）で修正済みで本ラン v1459 に
搭載済み。#5332（最終防衛 blocking、設計どおり判定）と同じ「防衛規則が出撃を
止める」系であり、単月のゼロは有界の待機プロトコル内の挙動と判定する。**

機構の帰属についての留保（VERIFY-5307 と同方式）: 本検証は issue 本文の
`decisions.counts` / `plan` / `world` スナップショットを直接見られない。上記
帰属は (i) `order_start` 2 件という低頻度が護衛・待機プロトコルの抑制と整合する
（無界ループならバックオフ込みでも毎月数十件になる）、(ii) 現行コードで
`order_start` まで到達し得る注文種別の全列挙が設計済み経路に閉じる、の二本立て。
仮に本文の counts が `order_failed` / `sortie_kit_empty_cancel` 主軸であれば
§5 の別機構となり再検討が要る。

## 1. 判定経路の確認（現行 main の行番号）

- `month_audit.py:15-22` — メトリクス定義: `confirmed`（月内確定出撃）と
  `departures`（`LAUNCH_DECISIONS` を持つ chart step の種類数）がともに 0 で
  月初から観測 → `zero_sortie`。
- `month_audit.py:103-110` — `LAUNCH_DECISIONS` = `order_launched` /
  `sortie_departed_observed` / `sortie_launch_confirmed` /
  `sortie_launch_unconfirmed_kept`。`order_done`・`order_failed`・
  `order_pending` は出撃として数えない。
- `month_audit.py:2923-2924` — signal `order_start_without_launch` は
  `counts['order_start'] > 0` かつ `departures == 0` で発火。本件
  order_start 2 と整合。
- `month_audit.py:271-272`（`PRIMARY_SIGNALS`）→ `:186-199`
  （`PRIMARY_LABELS`）→ `:4055-4056` — タイトル「出撃開始後に発射されない」は
  signature `departure_failure` に対応。`plan_starved`（`plan_all_settled` /
  `no_orders`）より先に `departure_failure` が優先されるため、締め時点の
  `context.plan.orders` は空でなく全件 settled でもない（＝pending か
  in-flight の注文が残っていた）ことが signature から機械的に導ける。
- `month_audit.py:2969-2994` — confidence high の条件: started_clean・章変更
  なし・月ジャンプなし・ledger整合・max_gap ≤60s・752s ≥120s・485回 ≥20・
  coverage ≥0.9・context あり。
- `month_audit.py:3382-3392` — kind 選択。`boss_month is not None` なら
  `boss_month_zero_sortie`/`_unverified` が優先されるが、本件は汎用
  `zero_sortie` ⇒ `_boss_phase`（`:507-541`）が None ⇒ **ch4 6-12 時点で
  通常城は未制圧**（=通常征服フェーズ。ボスフェーズ限定ロジックは対象外）。
- `order_launched` の emit は `_finish_order(mem,'launched')` 1 系統のみ
  （`strategy/orders.py:2502-2521` の `_record(mem, 'order_' + state)`、
  実体は `map.py:2956-2999` の `_commit_departure` が送信日誌＋ピッカー閉鎖の
  実測裏付けでのみ呼ぶ）。`order_start` は `map.py:2664-2668` で dispatch 時に
  無条件記録（`roster_probe` フラグ付きで probe かどうかも残る）。つまり
  「order_start>0 かつ order_launched=0」は「dispatch には進んだが
  `_commit_departure` に至らなかった」ことを意味し、probe 終了・ガード取消・
  in-flight 月跨ぎのいずれかで機械的に説明できる。

## 2. `order_start` 後に発射へ至らない経路の全列挙と設計属性

`(a) 実欠陥` とするには「設計を超えて出撃が失われた」経路が必要だが、現行
コードで `order_start` から `order_launched` へ至らない分岐は以下の
設計済み経路に閉じる。

1. **読み取り専用プローブ（最有力）**: `roster_probe`/`source_probe` 注文は
   dispatch され `order_start` を出すが、`deploy.py:2335-2360` で一覧を読み
   `roster_probe_complete`/`_incomplete` + `_finish_order('done')` で終了。
   発射しないのが仕様。枯渇月に dispatch できる唯一の注文種:
   - `orders.py:2378-2434` `_source_spare_missing` — 在城記録が待機将軍2人未満
     の実出撃注文は **dispatch 前に弾く**（= `order_start` すら出ない）。
     `roster_probe` は `:2381-2382` で明示的に免除。
   - `replan.py:2145-2153` `_defense_gate` — 候補が probe のみのとき wait を
     解除せず維持（#5324 の実欠陥修正、v1451）しつつ probe 自体は通す
     （`:2180-2183`）。
   - `replan.py:1896-2015` — 全城が1将軍の膠着に対し `country_general_count >
     located` なら `probe_stuck_1`（stale な城の名簿を開く読み取り専用注文）を
     mint する（#4689/#5153 由来。v1451 で攻撃ルートなし城・本拠優先に拡張、
     `docs/operations/hanjuku-defense-wait-probe-20261124.md`）。
   - 2 件という頻度とも整合: `STALE_ROSTER_PROBE_INTERVAL=300` tick の
     城別クールダウン（`replan.py:2018`, `:1979-1982`）+ `_order_start_due`
     の 12 観測バックオフ（`map.py:1117-1140`）で、485 観測の月に probe が
     2 回走る程度が自然。無界ループならもっと多い。
2. **護衛ガード取消（#5332 と同系）**: 実注文が一覧段まで進んでも、実リストが
   1将軍なら `_keep_sortie_defender`（`deploy.py:1847-1874`、移動元に待機将軍を
   残す規則）・`_keep_hero_guard`（`:1877-1907`）・`_keep_last_castle`
   （`:1750-1776`、最終城を空にしない）が `_hold_guarded_sortie` で pending へ
   戻す。オーナー規則そのもの（g421 の全滅ゲームオーバー回避が起源）。
   取消後は `_observe_garrison`（`deploy.py:2334`→`:1540` 以降）が在城記録を
   実測に更新するため、同じ出撃元の実注文は以後 `_source_spare_missing` が
   dispatch 前に止め、`order_start` の繰り返しは起きない — 低頻度と整合。
   同一注文の連続再開は `_sortie_retry_hold`（`house.py:483-522`、同一
   シグネチャのキャンセル直後再送を ~100 tick 延期）でも抑えられる。
3. **`defense_wait` の有界待機（プロトコル本体）**: `replan.py:2099-2186`
   `_sole_starved`（全城が自由将軍 ≤1）成立時は `chart_defense_wait` 記録の
   うえ `hold` で LLM 要求・暫定出撃を止める（呼出し `replan.py:3101-3103`）。
   `cycles` は月次評価で加算され、`cycles >= 2` または `cycles >= 1 かつ
   見込み将軍なし`で `'break'`（`:2184-2185`）→ `_mechanical_interim(
   allow_sole=True)`（`:3168`）が 1将軍の城からでも出撃を出す。つまり待機は
   最大でも ~2-3 月の有界で、単月のゼロは設計された回復待ちの内側。
   `orders.py:2233-2256` `_defense_break_allowed` が break 後の落城を学習して
   同城からの再 break を禁止する安全弁も設計済み。
4. **その他の有界キャンセル**: `sortie_kit_empty_cancel`（`deploy.py:2780-2795`、
   v1456/#5380「携行札なしの出撃を承認しない」、計画札あり・実携行 0 が上限に
   達したとき `order_failed`）。step 単位で `failed` になるため連続再試行しない。
   ボス注文の `_hold_locked_boss`（`deploy.py:1799-1808`）、
   `boss_companion_required`（`:2393-2404`）等も同様にオーナー承認の有界
   ホールド。
5. **月跨ぎ in-flight**: `order_start` した注文が月末時点で進行中なら
   `order_launched` は翌月側に出る。月末近くの開始なら監査上は「開始したが
   発射されない」に見えるが、月単位集計の境界効果であり欠陥ではない。

## 3. 検出自体の評価（誤検知ではない理由）

- `zero_sortie` の本体判定（`confirmed==0 && departures==0 && high`）は事実。
  probe の `order_start` が `order_start_without_launch` signal を立てるため
  シグネチャ表示は「出撃開始後に発射されない」になるが、probe を実出撃の開始と
  区別しないのは監査の既知の粗さであって、月内の実出撃ゼロという記録自体は正しい
  （probe 月も「敵向け出撃が無い月」として起票するのが監査の仕様:
  `month_audit.py:1-13`、evidence-only）。
- `auto_file` は `zero_sortie && high` で true（`:3137-3138`）。防衛待ち月を
  抑止する機構は監査にはなく、設計上も「証拠として起票して人が機構を見る」
  分担になっている（`docs/operations/hanjuku-month-audit.md`）。
- 副次的に `no_source` signal（在城記録の最大在員 ≤1、`:2949-2952`）が
  この状態では同時に立ち得るが、primary は `departure_failure` で確定
  （`PRIMARY_SIGNALS` の優先順、`:252-279`）。

## 4. 過去の同型 issue との異同（検証観点）

- **#5324（defense_gate の probe-only 解放、実欠陥→v1451 修正済み）**:
  同一ラン g1342 の ch3 で、全城1将軍のまま probe-only 候補が待機を解除して
  `cycles` が積まらず 5 か月ゼロ出撃が続いた事案（
  `docs/operations/hanjuku-defense-wait-probe-20261124.md`）。
  本件はその修正（`replan.py:2151-2159` の probe_only 除外＋`:1959-1999` の
  no_attack_leg/本拠プローブ拡張）を**含む v1459 での観測**なので、#5324 の
  実欠陥機構そのものはカバー済み。残るのは設計された待機期間中のゼロ月で、
  `cycles` が月次で進み `break` へ到達するかぎり有界。
- **#5332（最終防衛 blocking、設計どおり）**: 最終城・護衛規則が出撃を止めた
  月を「設計どおり」と判定した先例。本件の最有力機構（全城枯渇でプローブのみ
  走る、または実リストで護衛ガードが取消）は同じ防衛規則ファミリーで、
  オーナー規則の動作として同列に扱うのが自然。
- **#5264/#5257/#5236 等の zero_sortie 系**: 本リポジトリ内に #5264/#5257/
  #5236 を直接引く記述は見つからず、個別比較は保留。本件は signature が
  `departure_failure` で、`order_start=2` が probe/ガード系の低頻度と整合
  する点が特徴（`plan_starved` や `monthly_scan` 等の別 signature の月とは
  機構が違う）。
- fingerprint 仕様（`month_audit.py:3032-3034` =
  `"{chapter}|{kind}|{'+'.join(signals)}"` の sha256 先頭16桁）より、ch4 の `zero_sortie` ×
  `departure_failure` は全 occurrence で `f097be01bf69d359` に一致する。
  本 issue が新規起票なのは、同一 fingerprint の open issue が存在しなかった
  ことを示す（`_find_issue` は open のみ検索、VERIFY-5282 §観点1 と同じ構造）。

## 5. (a) 実欠陥へ傾け得る未確認事項（次の確認で閉じる）

- issue 本文 `decisions.counts` の取得（gh 不可のため未確認）:
  - `order_done` / `roster_probe_complete` が主体 → probe 経路確定、(b) 維持。
  - `chart_defense_wait` / `chart_defense_wait_eval` → 待機プロトコル内、(b)。
  - `sortie_held_last_castle` / `sortie_held_source_defender` → #5332 型、(b)。
  - `order_failed` / `sortie_kit_empty_cancel`（v1456）が主体 → 別機構として
    再検討（v1456 の空携行取消が計画側の名付けと連鎖して全注文を潰した可能性。
    ただし `want` は `_drop_card` で選べない札が縮むため、継続的に
    `want>0 && carried==[]` に留まるのは狭い条件 — `deploy.py:2744-2801`）。
  - `order_dispatch_starved` → pending 注文が 60 観測超 dispatch されなかった
    傍証（`docs/operations/hanjuku-month-audit.md:192-217`）。
  - 上限 30 件の `most_common` 切り捨てで低頻度決定が落ちる監査側の既知の
    粗さ（VERIFY-5276 記載）にも留意。
- 翌月（7-1 以降）に `order_launched` が出てゼロが解消していれば、有界待機の
  回復として (b) が裏強化される。逆に同 fingerprint で occurrence が積み上がる
  場合は `break` 未到達＝回復プロトコルが機能していない可能性として再検討。

## 手順上の注記

- read-only 検証。VERIFY-5391.md の作成以外のファイル編集・本番 VM 操作なし。
  push・PR・issue コメントは実施しない。
- 投稿経路・スキャンについて: 本ファイルはローカル成果物であり GitHub への
  投稿は行わない（検品ゲートのため）。
