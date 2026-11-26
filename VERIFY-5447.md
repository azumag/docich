# VERIFY-5447 — [半熟英雄] 出撃キャンセル: ch4 ポセイドン

- 対象: docich#5447（`bug` / `auto-generated` の監査自動起票）/ fingerprint `a405d8bd47eb4897` / occurrence(incident_id) `40cec0610e3cf344981d`
- 判定: `sortie_cancelled`（confidence `high`）/ 決定 `sortie_cancelled_observed` / 理由コード `sortie_unit_stayed`
- シグナル: `sortie_cancelled` / `sortie_unit_stayed` / `order_start_without_launch` / `departure_cancelled` / `camp_recall_loop` / `stuck_march`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1477-boss-camp-recall`（コミット `911ffbb55`、PR #5442 で `src/docich/hanjuku/version.py` の `BOT_VERSION` を実測確認）
- 月: ch4 8-11（観測 304回 / 456.5秒 / max gap 5.4秒 / 303 ticks / started_clean）
- 出撃: ポセイドン（計画将軍はどうし・代役出撃）/ step `4-B2` / アルマムーン → 四季王城(南東) / purpose 記録なし
- 検証ベース: worktree `/home/hatch/work/docich-5447` detached HEAD `f0c65d6a7`（`BOT_VERSION = 'hanjuku-chart-v1489-discharge-last-general'`。ランの v1477 から +12。関連修正 v1478(#5449)/v1480(#5453)/v1481(#5450)/v1483(#5451)/v1487(#5467) の差分を直接確認済み）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照不可。ただし同ランの実機 bot state fixture `tests/fixtures/hanjuku/g1342-ch4-bot-state-20261125-0117-no-companion.json`（v1480 同梱・incident 約4分後の実測スナップショット）が repo 内にあり本検証の決定的証拠となった）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 環境制約メモ

- 本環境では `gh`・`python3` 等の外部 CLI が実行拒否される（VERIFY-5431/5444/5324 と同制約）。issue 本文は公開 GitHub issue ページの webfetch 取得で代替し、incident JSON 全量を取得済み。
- fingerprint 理論値: `sha256("4|sortie_cancelled|sortie_cancelled")[:16]`（`month_audit.py` `_fingerprint` L3105-3107、sortie 経路は signals=`[event['kind']]` の1要素のみ L1108）。**本環境では再計算不能のため未照合**。期待値 `a405d8bd47eb4897` との一致確認は手段のある環境での残件とする。
- incident_id 理論値: raw = `runtime_id|generation|kind|reason_code|step_local|general|target|int(now)` の sha256[:20]（L1103-1106）。issue の `created_at = 1795536774.9126134` から `int(now)=1795536774`、sortie_event の target=null → 期待 raw は **`g1342-1fe0734c|1342|sortie_cancelled|sortie_unit_stayed|4-B2|ポセイドン|None|1795536774`**。構成要素は全て既知で外部再計算可能（本環境では不可）。

## 結論: **(a) 実欠陥 — v1480（PR #5453）・v1487（PR #5467）で修正済み**

発火は監査契約どおりで誤検知ではない（§1）。incident が記録した「取消観測」は**実際に起きた事象**であり、しかも v1480 の修正コミット自身が診断に使った「ch4 四季王(南東)=4-B2 の出撃が止まる」実欠陥チェーンの**まさにトリガーイベント**である（§2）。fixture とコードコメントが incident の時刻・step・将軍を直接名指ししており、(d) ではなく (a) で確定できる。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- `_sortie_event`（`month_audit.py` L974-1012）: 決定 `sortie_cancelled_observed` は `SORTIE_TERMINAL`（L937-941）により `('sortie_cancelled','cancelled')`、理由コードは `sortie_unit_stayed` 固定（L949-950）→ issue の kind/理由/シグネチャと一致。監査仕様（`docs/operations/hanjuku-month-audit.md` L97-114）の「出撃元の将軍一覧に将軍が残り、出撃取消を観測」と同義。
- 起票抑止（L1422-1433、docich#5136）: 月内確定発射があれば再試行可能な取消は起票しない。本件は `sorties.confirmed = 0`（月内ゼロ）のため抑止条件を満たさず high で起票された → 契約どおり。
- signals（L2990-3039）: `order_start_without_launch`（order_start 1・departures 0）、`departure_cancelled`（sortie_cancelled_observed ≥1）、`camp_recall_loop`（camp 系決定 13件 ≥10）、`stuck_march`（`world.sorties['4-B4']` age_ticks 3183 > 3000）→ issue のシグナル列と一致。
- `order_launched 0` / `departures 0` は `decisions` 集計が直前120秒窓（`_read_decisions` L1018-1019）だけを見るためで、「月内に発射手続きが無かった」証拠ではない（VERIFY-5317/5444 と同じ読み方）。

### 2. 実際に起きたこと — fixture が全経路を確定する

incident と repo 内 fixture `g1342-ch4-bot-state-20261125-0117-no-companion.json`（incident の ~4分後の実機スナップショット）を照合すると、経路は一意に決まる:

1. **tick 63658（~16:07 UTC）— 4-B2 の出撃試行が目標決定前に中断**。fixture の `sorties['4-B2']` は `target: null` / `planned_target: '四季王城(南東)'` / `status: 'cancelled'` / `tick: 63658`。`target` が null のまま残るのは `_commit_departure`（発射コミット、`target` を書く）ではなく **未確定経路**（`map.py` L177-184: 出撃先ピッカーが目標決定・journal送信前に閉じた `launched_unconfirmed` 記録）だけ。incident の監査シリアライズは `target or planned_target` フォールバック（`_summarize_world` L409）で南東を表示していた。evidence 欄 `actual_general: ポセイドン` / `planned_general: どうし` / `strategy_variant: substitute_general` / cards `[クースカン]`（v1448 自動携行が選んだ補助札）。`source_override['4-B2']='アルマムーン'`（fixture L4287）が、order_start の `source=ネクスドリア`（チャート注文の計画値）と sortie 記録の `source=アルマムーン`（実出撃元=代役の在城城）の差を説明する。
2. **tick 63868（16:12:54 UTC、incident 時刻そのもの）— 検証往復で取消を実測**。orders `launched_unconfirmed` のままの step が再選ばれ（`map.py` L2578-2583）、`order_start` → `castle_jump` → アルマムーン将軍一覧（`garrison_seen`）→ ポセイドン在城を確認 → `_verify_sortie_source`（`deploy.py` L157-202）が `status='cancelled'`・order `pending` 戻し・`sortie_cancelled_observed` を記録。**名簿の実読みによる在城証拠であり、blind 期限切れの推定巻き戻し（#5444 の論点）ではない**。
3. **取消ゲート武装 → 代役枯渇（実欠陥の本体）**。同決定は `_record_sortie_cancelled_observed`（orders.py L415-439）経由で `cancelled_sortie_gates['4-B2']`（fixture L166-174、tick 63868・month 8-11・ポセイドン・アルマムーン）を武装し、`_sortie_cancelled_observed_suppressed`（orders.py L266-321、`SORTIE_CANCELLED_RETRY_TICKS=200`）が同一月内の即再試行を抑止した。**この抑止待機中に駐屯回し注文 `I:d1b35d:4b0345d7`（fixture L5234-5241: 「敵城に近いネクスドリアを厚くするためポセイドンをアルマムーンから移動」、`garrison_rotate: true`、status `launched`）が唯一の適格代役ポセイドンを別城へ運び、4-B2 の代役が全城でゼロになった。**
4. **修正側が本 incident を直接名指し**。`site_companion_reserve`（orders.py L1946-1972、v1480）の docstring:「g1342 2026-11-25 01:12:59: the only eligible companion (ポセイドン) sat at アルマムーン while 4-B2 waited out a cancelled-departure gate, and the garrison rotation moved him to ネクスドリア」— 01:12:59 JST は本 incident の `sortie_cancelled_observed`（16:12:54 UTC = 01:12:54 JST）の直後であり、**本件は v1480 診断対象のゲート武装イベントそのもの**。fixture の `I:d1b35d:4b0345d7` が移動実行中であることと整合する。

### 3. 兄弟 issue との切り分け

- **#5444（(d) 判定不能）**: `sortie_failed` 側終端で、「真の不発」か「名簿証拠なしの blind 期限切れ推定巻き戻し」かが incident 内で決着しなかった。本件は cancelled 側で、名簿の実読みが在城を直接証明している（推定経路ではない）ため同じ不確実性を持たない。
- **#5226（(c) 誤検知）**: `sortie_without_cards` 系で「実際には札を携行していた」証拠読み取り欠落。本件は kind が異なり、かつ出撃そのものが成立していない（`target: null`）ため携行実測の問題自体が発生しない。誤検知ではない。
- **#5317（(b)）との切り分け（切り札節）**: issue テンプレの切り札節（在庫 None / 携行なし / 計画cards None / 型「計画が空（自動携行の対象・v1448 以降）」）は副次情報。#5317 は「明示的 cardless order がそのまま出撃した」話で、本件は出撃不成立のため無関係。なお v1448（#5336）の自動携行は**適用済み**: fixture の `order_context['4-B2'].expected_metric.cards = ['クースカン']`（計画が空の step に対し補助札が選定されている）。発射未成立のため携行の有無は失敗原因ではない。
- **#5060（chikujou_cancelled ch1）**: 築城キャンセルという別終端の家族。出撃系の理由コードと機構を共有しない。

### 4. バージョンカバレッジ（v1477 → HEAD v1488/v1489）

| 版 | PR | 内容 | 本件への当てはまり |
|---|---|---|---|
| v1477 | #5442 `911ffbb55` | 月次帰還（boss camp recall） | ラン版そのもの。incident の `flow.recall_verification` dispatched・camp_recall_* 決定列どおり稼働。発火は v1477 適用後。 |
| v1478 | #5449 `c3e460401` | ch4 サイト出撃元を最短距離優先（`_boss_companion_source`/`_follow_general` の source_override 更新） | 緩和層。ゲート待機中の代役移動そのものは防がない。 |
| v1480 | #5453 `034387473` | `_rearm_failed_site_steps`（ドクロが見える failed サイト step を月内3回・600tick で pending 戻し）+ `site_companion_reserve`（pending 四季王 step の最適格代役を駐屯回し/防衛再配置から除外） | **本件機構を直接修正**。`site_companion_reserve` は replan.py L1784・1826 で駐屯回しの busy 集合へ組み込み済み（HEAD で確認）。`plan.orders` の `4-B1: failed`（ドクロ生存中ずっと failed で再武装経路なし）も同修正の対象。 |
| v1481 | #5450 `4f7d93e59` | 月次帰還の hint 配置・goal 未解決フォールバック | 非該当（本件は帰還 hint の失敗ではない）。 |
| v1483 | #5451 `a4af58faa` | ch12 戦略層（主人公をボス近くの拠点へ先に進める） | 章限定で非適用。 |
| v1487 | #5467 `ad9c5c7df` | 南西/南東四季王の3点: 閉鎖地点を boss_move/recall 目標から除外 / `site_unconfirmed_rearm`（検証不能なサイト出撃を月内2回まで pending 戻し、城メニュー誤読検証のカウント修正）/ `site_end_rearm`（サイト行軍が目標外の城への在城で終わった実測を pending 戻し） | **同じラン・同じ 4-B1/4-B2 家族の補完修正**。本件の sortie 記録がまさに `launched_unconfirmed`（目的地未確定）だった系であり、検証が読み取れない局面の出口を作る。v1480 と合わせて現行 HEAD は本機構をカバー済み。 |

- まとめ: **本件が属する欠陥機構（取消ゲート待機中の代役枯渇 + 検証不能サイト step の放置 + failed サイトの再武装欠落）は HEAD で全て修正済み**。v1480 コミットは同ランの fixture を同梱し、v1487 も同ランの実測（02:08-02:31 JST の 4-B1/4-B2）を根拠にしている。

### 5. 残件（確定済みだが記録として残す）

- fingerprint / incident_id の sha256 再計算: 本環境では外部 CLI が拒否されるため未実施。期待 raw 文字列は上記に記載（構成要素は全て既知で外部照合可能）。
- tick 63658 の初回試行を中断させた具体的画面（防衛戦割り込み等）: `hanjuku_decisions.jsonl` の 120 秒窓より前の行にあるが VM 側成果物のため未確認。直近の `battle_okunote_select`/`battle_result`/`post_month_battle_resume`/`camp_recall_battle_reset` 決定列と、v1487 が記述する「出撃先選択中の防衛戦で中断」パターン（4-B1 の実例）から戦闘系割り込みが最有力だが、結論には影響しない。

## まとめ

- 監査の発火・分類・シグナル: 契約どおり（誤検知ではない）。`sortie_cancelled_observed` → `sortie_cancelled`/`sortie_unit_stayed` の写像、月内発射ゼロによる抑止解除、信号の発火根拠は全てコードと一致。
- 実質判定: **(a) 実欠陥** — incident は「4-B2 南東出撃の取消観測（名簿実測）→ 取消ゲート武装 → 待機中に駐屯回しで唯一の代役ポセイドンが移動され step が飢餓」という実欠陥チェーンの起点イベント。`orders.py` L1949 の `site_companion_reserve` docstring が日時・step・将軍を名指しで本 incident を参照している。
- 修正状況: v1480（#5453）が代役保護と failed サイト再武装を、v1487（#5467）が検証不能サイト出撃の pending 戻し・戻り行軍の実測・閉鎖地点除外を main にマージ済み。再発は現行 HEAD では防止される。
- 検証ブランチへの記録コミットのみ。issue コメント・push・コード変更なし。
