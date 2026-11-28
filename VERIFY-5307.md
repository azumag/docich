# VERIFY-5307 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch10 だいとうりょう

対象: docich#5307 / fingerprint `6d93ef834288cf25` / occurrence `9b61e2d3e85c7fc13b8c`
判定 `boss_month_zero_sortie` (confidence high) / 月 `ch10 16-10`、観測 1206回 / 229.9秒
ラン `g1190-ch1v9` / BOT_VERSION `v1435` / ボス `だいとうりょう` / 残り `ボス`
出撃: 確定 2 / 発射 2 / `order_start` 4 / `order_launched` 0 / 月内ボス出撃 0 / launch_events 0
フェーズ開始 `1795477140.092253` / 観測 4552回（複数月にまたがる）
issue 本文に挙がる決定: `camp_recall_interrupted`（帰還先の選択が場面で閉じた）、
`castle_jump_failed`、`general_jump`、`battle_start` / `battle_result`

調査基盤: worktree `/home/hatch/work/docich-5307`、HEAD `ae9ea33b1` = origin/main =
`hanjuku-chart-v1435-recall-dispatch-unattributed`、**ランと同一バージョン**（VERIFY-5306 と同条件）。
read-only 調査で実装変更なし。`gh` バイナリは本環境で起動が拒否され issue/PR 本文は未取得。
代わりに issue 要点（上記）とローカルのコード・git ref を根拠にした。修正ブランチは
`origin/fix/5307-boss-companion-stage-timeout-v1443`（= PR #5310 相当、コミット題に
`(#5307, v1443)` と明記、未マージのリモート ref）としてローカル差分で確認できた。
`python3`/`pytest` の実行も拒否されたためテストは **未実施**。本番 VM SSH なし。
ラン成果物 `hanjuku_decisions.jsonl` / incident JSON / frames は VM 側にあり直接参照できない（§5）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

---

## 結論: (b) 設計どおり、または既存の修正でカバー済み

**本件は #5275 と同じ「boss companion 待ち」系だが発火層が一段前で、
計画層 `_ready` ゲート（`orders.py:745-747`、無記録・無制限）がボス注文を
dispatch 前に止めていた型。owner 修正 #5310（ブランチ
`fix/5307-boss-companion-stage-timeout-v1443`、未マージ Draft）がその経路を
共有 200 tick 締切で端から端までカバーする。**

機構の帰属についての留保: 本検証はランの決定行を直接見られず、上記帰属は
(i) owner が incident 記録から診断して `#5307` 付きで修正を出した事実
（コミット題 `bound boss companion wait at every sortie stage (#5307, v1443)`）、
(ii) issue 挙がりの決定列との完全な整合性（§3 の対偶チェック）、の二本立て。
仮に incident の `before` 行に **ボス target の `order_start`** が残っていれば
ボス注文は dispatch 済み＝別機構となり再検討が要るが、その場合 `boss_companion_*`
系や `boss_companion_timeout` も counts に出るはずで、issue 列挙には無い。

### #5275 との異同（検証観点1）

同一「系」、**発火層は一段前**。#5275 は `deploy.py:2381` の general_list 段
`boss_companion_required` 無制限保留（注文は dispatch され一覧まで到達し、
`[b,b]` で pending へ戻るを繰り返した）。本件の帰属経路は `orders.py:745-747`
の `_ready` ゲート: `_boss_companion_source(order, mem) is None` かつ出撃元在城
既知なら `return False` —— **このゲートは `_record` を一切発行しない**ため、
ボス注文は `order_start` すら出さず静かに pending のまま月が閉じる。
`order_start` が 4 回あるのはボス注文ではなく暫定 probe/移動/代役フェリー等の
別注文（`map.py:2617` は step/source/target/purpose/roster_probe を記録するが、
issue 集計は件数のみ）。`order_launched` は `_finish_order('launched')`
（`orders.py:2421` 以降、`map.py` の `_commit_departure` 経由）でしか出ないので、
ボス注文未 dispatch なら 0 は機械的に保証される。

対偶チェック（issue の決定列が計画層停止と整合すること）:

- `boss_companion_required` 系が列挙に無い → general_list/card 段には**一度も到達
  していない**（到達すれば #5275 と同様 counts に残る）。計画層停止と整合。
- `chart_interim_hold`（`replan.py:2188,2244`）が無い → interim 候補は空でなく
  注文は mint されていた。`order_dispatch_starved`（`orders.py:1798-1838`、
  pending 注文が 60 tick 未 dispatch で発火）が無い → ready な注文の滞留も無く、
  probe 等が普通に dispatch され続けた。ボス注文だけが `_ready` で隠れていた、
  と読むのが最も整合的（上限 30 件の `most_common` 切り捨てで低頻度決定が
  落ちる可能性は残る — `month_audit.py` の `_decision_counts`、VERIFY-5276 記載の
  監査側既知欠陥）。
- `general_jump`（`map.py:2081` 以降、期限超過のボス進軍将軍の野営へ SELECT
  ジャンプ、章共通）は**前月以前に発射したボス行軍が overdue** である証拠 —
  ch10 でボス注文の発射自体は可能で、駐留が痩せた後半に `_ready` ゲートが
  効き始めた状態遷移と整合。`castle_jump_failed` / `camp_recall_interrupted`
  / `battle_*` はボス注文以外のフィールド作業（probe・帰還・戦闘）の記録で、
  dispatch 前停止では何も矛盾しない。
- `発射 2` は非ボス・非 move の `sortie_launch_confirmed`/`sortie_departed_observed`
  系（`month_audit.py:103-110` の LAUNCH_DECISIONS、ボス集計は `:2854-2869`
  で target ∈ bosses or purpose=='boss' を要求）で、ボス注文の存在・不在とは
  直交する。

### PR #5310 のカバー確認（検証観点2）

ブランチ `origin/fix/5307-boss-companion-stage-timeout-v1443`、
`origin/main..branch` = 2 commits（`93d61b950` + `f7c7ebbc2`）を diff 確認。
観測経路を**全段カバーする**:

- **計画層（新規、本件の発火層）**: `orders.py` に共有 `BOSS_COMPANION_WAIT_TICKS=200` /
  `_boss_companion_wait_expired`（分岐差分 +704 行付近）。`_ready` の companion
  ゲートは `_boss_companion_source is None && 在城既知` なら ch4 boss site 以外は
  `not _boss_companion_wait_expired → return False`。締切は**最初にブロックされた
  `_ready` 呼出で armed**（`wait[step]=now`）— 「start boss companion deadline
  before planner gate」の題どおり、計画層に永久に隠れる経路を消す。200 tick 後は
  ready になり dispatch へ進む。`_broken_hero_order`/`_boss_egg_depleted` 側の
  同型ブロックも同じ締切で解放。`expired[step]` は `_finish_order` まで latch
  （:2469 付近のクリーンアップ）するため段遷移でタイマーが振り出しに戻らない —
  VERIFY-5276 が指摘した #5279 の per-stage 再起動問題もこれで解消。
- **general_list 段**: `deploy.py:2380` 付近、上限後は実一覧の主人公を確認して
  単独 fallback（`boss_companion_hero_fallback`）、主人公不在 or 卵/修復ガード不適格
  なら `_finish_order('failed')` → 再計画。無制限 pending は消える。
- **card_select / sortie_confirm 段**: `deploy.py:2477` 付近の同型ガードに
  `and not _boss_companion_wait_expired` を追加 → 有界化。
- **map_target 段**: `map.py` から #5279 のローカル `BOSS_COMPANION_WAIT_TICKS` /
  `_boss_companion_wait_expired` を削除し shared 版を import — 到達時点で締切は
  前段からの経過で評価される（#5279 単体では計画層・一覧段に効かなかった点も
  この一本化でカバー）。
- 残存する非カバーは **ch4 召喚ボス site のみ**（`order.get('target') in
  chart.boss_sites(4)` では従来どおり無条件 `return False`）。本件は ch10 で非該当。

したがって **#5310 は本件の観測経路をカバーする**（マージ・デプロイ後に有効。
v1435 本番ではまだ無制限のまま → 同型再発はあり得るが、それは「修正未反映」で
あって検出・修正設計の誤りではない）。

### PR #5309（#5082 最後の1将軍例外）の適否（検証観点3）

**非該当。** #5309 が想定する型は「全城の空き将軍が 1 名以下で `need=2`
（`replan.py` の `_boss_wave_pick`、`sender_min` は ch9 以外 2 — `hanjuku_chart.py`）
を満たさず interim 候補が空 → `chart_interim_hold` で注文自体が mint されない」
ケース。本件は (i) `chart_interim_hold` が決定列に無く候補は mint されていた、
(ii) 停止層は mint 後の `_ready` ゲート —— つまり「注文は作られるが dispatch
できない」型で、#5082 の「注文が作れない」型とは別。仮に #5309 の例外注文が
出されても、それは companion 付きボス注文として同じ `_ready` ゲートを通るため、
本件型では #5309 単体では解消しない。

### PR #5279（map_target 段の主人公ガード有界化）の適否（検証観点4）

**非カバー（VERIFY-5275/5276 と同じ構造）。** main の `map.py:2749-2775` 付近に
ある #5279 の 200 tick 締切は `target_step` 内の主人公ガード 1 箇所のみで、
出撃 UI の順序（general_list → card_select → sortie_confirm → map_target）上
最後段。本件の経路は計画層 `_ready` で止まり `map_target` には到達しない。
なお #5310 はこの #5279 締切を shared 版へ置き換えて各段で共有するため、
#5310 マージ後は #5279 の有界化は実質的に内包される。

## 検出自体の正当性（誤検知ではないこと）

`month_audit.py` の `boss_month_zero_sortie` 判定（ボスフェーズ存在 +
`_boss_departures==0`）は実測ベースで正しく、確定 2（month_sorties ledger）/
発射 2（非ボス）/ ボス出撃 0 の読みも各カウンタの定義どおり。
**計器は正しい。異常（ボス注文が月内に一度も dispatch/発射されない）も実在する**
— それを起こしていたのが `_ready` 層の無制限 companion ゲートであり、#5310 が
その上限制御を入れる。誤検知 (c) ではない。

副次の監査観察（別件、判定を変えない）: `sortie_launch_confirmed`（`map.py:3137,
3226`）と `sortie_departed_observed` は `target`/`purpose` を行に載せないため、
これら単独の発射は `_boss_departures` がボス attribution できない。本件では
ボス注文未 dispatch が機構なので影響しないが、「未確認発射が後で confirmed に
なった」ケースでの boss 出撃過少計の余地として記録しておく。

## 検証観点ごとの対応表

| 観点 | 判定 | 根拠 |
|---|---|---|
| #5275 と同一機構か | 同系・一段前（`_ready` 層、無記録） | `orders.py:734-747`、`deploy.py:2380-2383, 2476-2481`、issue 決定列に companion 系・`chart_interim_hold`・`order_dispatch_starved` が無い |
| #5310 がカバーするか | **はい（全段）** | `orders.py` 共有締切 + `_ready` 解放、deploy 2 段の有界化、map_target への共有化 |
| #5309（最後の1将軍）が該当するか | いいえ | mint 停止型ではなく `_ready` ゲート停止型；例外注文も同ゲートを通る |
| #5279 がカバーするか | いいえ | map_target 最後段のみ；本件は到達しない |
| 監査は誤検知か | いいえ | `month_audit.py:103-110, 2835-2869`、フェーズ+ボス出撃0の実測どおり |

## 不足している証拠（判定の留保に使ったもの）

- `hanjuku_decisions.jsonl` の該当月行: 4 回の `order_start` の
  `chart_step`/`target`/`purpose`/`roster_probe`、および `boss_companion_timeout` /
  `boss_companion_required` / `order_dispatch_starved` の有無（counts は
  `most_common(30)` で切り捨てられ得る）。ボス target の `order_start` があれば
  本結論（計画層停止）は覆る。
- incident JSON の `world.garrison`（`_ready` ゲートを効かせた在城既知・代役不適格の
  実測状態）。issue 要点には未記載。
- gh/CI での #5310 テスト結果（ブランチには `test_hanjuku_ch9_boss_companion_timeout.py`、
  `test_hanjuku_hero_guard.py` の変更を含む）。本環境では pytest 実行が拒否された。

## テスト

**未実施。** `python3 -m pytest` の実行が環境で拒否された（VERIFY-5306 と同条件）。
コード読解による機構確認とローカル diff 比較のみ。対象ブランチ側には companion
timeout/hero guard の回帰テスト差分が含まれる。

## 結論のまとめ

- (a) 実欠陥 → 修正は既に存在（#5310 が未マージ Draft）。新規修正は不要で、
  v1435 現行で再発しうる点だけが残る。
- **(b) 該当**: 本件機構（`_ready` 層の無制限 companion ゲート）は #5310 の
  共有締切でカバー済み。#5275 同系だが発火層が計画層であり、#5310 が
  「deadline を計画ゲートの手前で開始する」ことで正しく塞ぐ。
- (c) 誤検知 → 否。監査メトリクスと実挙動は一致。
- (d) 判定不能 → 機構の行レベル確証は incident 決定行が要るが、owner 診断・
  修正の指名・決定列の整合が同一点に収束するため (b) とした（留保は上記）。
