# VERIFY-5206 — [半熟英雄] 出撃失敗: ch7 ヘパイストス

対象: docich#5206（監査自動起票）/ ラン `g1190-ch1v9` / generation 1190 /
`hanjuku-chart-v1373-egg-receipt-settle` / 月 `ch7 10-6` /
fingerprint `d7c98398d36c8c17` / occurrence `4a2beced32b59fd02e0a` /
step `A:d48340a4:M2d025c`（ヘパイストス: ベンゴディ→レオナール、目的 不明）
調査基盤: worktree `~/work/docich-5206-verify`（ブランチ `verify/5206-sortie-failed`）。
read-only 調査。コード・issue 範囲のみ。本番 VM SSH なし。
handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本は未読（AGENTS.md §1 に基づき未読と明記）。
ラン成果物（`hanjuku_decisions.jsonl` 等）は本 worktree に存在しないため、issue 提示値と
現行コードの構造解析で判定している（§8 の残存不確定点を参照）。

---

## 結論

**(b) 設計どおり・修正不要。**

`sortie_failed`（reason_code `sortie_launch_not_observed`、confidence high）は、
step `A:d48340a4:M2d025c` の**出撃コミット（送信ジャーナル済み A＋出撃先選択の
クローズ）が 2 回とも部隊の出現を生まず**、有界再試行予算（`LAUNCH_MISS_LIMIT = 2`）
を使い切って注文を `failed` にした、設計どおりの終端である。
監査の検出・分類・信頼度は正しい。誤検知ではない（少なくとも 1 回目の未発射は
構造的に実証できる。§4）。

- **`発射 3 / order_start 0 / order_launched 0` は矛盾ではない。** 表示の食い違いは
  `_decision_counts` の `Counter.most_common(30)` 切り詰めが原因（VERIFY-5162 で
  報告済みの同一 artifact）。misses=2 が構造上要請する 2 件の `order_launched` 行は
  窓内に確実に存在するが、決定種別が 30 種を超える混雑月（戦闘・卵・月次・
  テキスト送りが重い）では上位 30 から落ちて 0 と表示される。§2。
- **`misses: 2` は「A を 2 回押した」ではなく「出撃未確認の巻き戻しが 2 回」**。
  g1097 系の「送信済み A＋ピッカークローズ≠発射」を吸収するために設計された
  発射プローブ（marker 調査 → blind → 出撃元名簿待ち → 有界巻き戻し）が
  2 回作動した結果で、記録条件自体は設計どおり。§3。
- **#5153 / #5190 / #5191 / #5213 の家族ではない。** probe-loop（#5153）でも
  「合法的に出撃源ゼロ」（#5190/#5191/#5213）でもなく、g1097 以来の
  「コミット済み出撃が実部隊を生まない」発射観測系の正しい作動。§6。
- **max gap 0.6 秒は部隊マーカー見逃しの原因になりえない。** プローブは壁時計ではなく
  全体マップ調査イベントを数える。行軍中の部隊▲は表示が持続するため、観測間隔は
  検出可否に関係しない。§5。
- **現行 HEAD でも同シグネチャは再発しうるが、それは意図された挙動**（本当に
  発射しない出撃を検出・有界再試行・失敗化・起票する機構）であり、#5182
  （probe-loop 修正）の対象外。修正済みの欠陥ではなく、設計上の終端報告。§7。

---

## 1. 監査ロジックの検証（issue 数値との対応）

`src/docich/hanjuku/month_audit.py`:

| 数値・事実 | コード | 検証結果 |
|---|---|---|
| `sortie_failed` / `failed` | `SORTIE_TERMINAL`（L882-886）: `sortie_launch_failed` → `('sortie_failed','failed')` | 分類は設計どおり |
| reason_code `sortie_launch_not_observed` | `_sortie_reason_code`（L892-893）: `sortie_launch_failed` → 固定値 | 一致 |
| `misses: 2` | `_sortie_event`（L933-953）: `observed_metric['misses']` を抽出 | `launch_miss[step]` の最終値と一致（§3） |
| `確定 0` | `month_sorties.launches`（`_build_sortie_incident` L967-968）| `purpose='move'` 除外・`sortie_launch_unconfirmed_kept`・`sortie_departed_observed` では加算されない（`flows/map.py` L3077-3081）ため、発射 3 と両立 |
| `発射 3` | `_departures`（L2768-2784）: 窓内の**全行**を走査し、`LAUNCH_DECISIONS`（L103-110: `order_launched`/`sortie_departed_observed`/`sortie_launch_confirmed`/`sortie_launch_unconfirmed_kept`）のうち `purpose='move'` 以外を `chart_step` 単位で重複排除して数える | 実際の発射系イベントの数で、3 step が発射系記録を持つことは正当 |
| `order_start 0` / `order_launched 0` | `sorties.*`（L1018-1019）← `_decision_counts`（L2813-2816）= `Counter.most_common(30)` | **top-30 切り詰めの artifact**（§2） |
| 観測 1305 / 113.9 秒 / gap 0.6 秒 | `MAX_GAP_SECONDS = 60`、`_month_summary`（L978-986） | 減点なし → high は正当 |
| 監査窓 120 秒 | `_build_sortie_incident` L963: `now-120 .. now` で `hanjuku_decisions{,.previous}.jsonl` を読む | 月全体（113.9 秒）が窓内に収まる |

**判定: 監査の検出・分類・信頼度は設計どおり正しい。**

## 2. 「発射 3」対「order_start 0 / order_launched 0」の切り分け

2 つの値は集計方法が異なり、矛盾しない:

- **`発射`（=`sorties.departures`）** は `month_audit.py` L2768-2784 の
  `_departures(rows)` が窓内の**全決定行**を走査して数える。
- **`order_start` / `order_launched`** は同 L1018-1019 が `_decision_counts(rows)`
  （L2813-2816）から読む値で、これは `Counter.most_common(30)` の結果——
  **頻度上位 30 種の決定名だけを残す**。

本件ではこの切り詰めが単なる可能性ではなく**構造的に確実である**:

1. `misses: 2` は `launch_miss[step]` が 2 に達したことを意味し、
   `_rollback_unverified_launch`（`flows/map.py` L3139-3140）または
   `orders.py` L1553-1554 の計上が **2 回**必要。
2. 各巻き戻しは blind/kept な `launch_probe`（または未確定 sortie）を前提とし、
   それらは `_commit_departure`（`flows/map.py` L2882-2887）でしか作られない。
   `_commit_departure` は `order_launched` を必ず記録する（`_finish_order`、
   L2842-2844 → `strategy/orders.py` `order_launched` 経路）。
3. よって窓内に **少なくとも 2 件の `order_launched` 行が存在する**はずだが、
   `counts` には 0 と出ている → これらの行は `most_common(30)` で落ちたと確定。
   （`order_start` も同様に、ロールバックで pending に戻った注文の再ディスパッチ
   で再記録される — `flows/map.py` L2584。）

113.9 秒の月に戦闘系（`monster_menu_choice`・`egg_battle`・`battle_*`）・月次
（`event_text_advance`・`post_month_*`）・map 系の決定種別が 30 を超えるのは、
同じラン（`g1190-ch1v9`）で (b) 判定済みの VERIFY-5191 でも観測されている
混雑状態と整合する。**この食い違いは監査側の表示上の既知 artifact であり、
出撃記録そのものの欠損ではない。** VERIFY-5162 で同一 artifact（counts が
ちょうど 30 件で発射系決定が落ちる）が報告・実証済み。

## 3. `misses: 2` の意味と `sortie_launch_not_observed` が記録される条件

`launch_miss[step]` は step 単位の「出撃未確認巻き戻し」カウンタ。加算経路は
3 つで、いずれもコミット済み出撃（送信ジャーナル一致の A＋出撃先選択クローズで
`_commit_departure`、後述）が**陽性の発射証拠を出さなかった**場合に限る:

| 経路 | 条件 | 証拠力 |
|---|---|---|
| (i) `flows/map.py` L3018-3019（blind プローブ期限切れ） | marker 調査が blind 化（敵城なら `LAUNCH_PROBE_READS=5` 回、**自城移動なら 1 回**、L3044-3047）→ `LAUNCH_PROBE_ROSTER_TICKS=200` tick 名簿なし、目標非ボス・章が `PORTRAIT_MARCH_CHAPTERS={9}` 外（ch7 は対象外） | **推定**（名簿未読での旧来巻き戻し。docstring L2953-2960 が「▲なし≠未発射」と明記） |
| (ii) `settle_launch_probes_from_roster`（L3103-3106） | 出撃元の将軍一覧の読み切りに将軍が**まだいる** | **実測**（未発射の直接証拠） |
| (iii) `orders.py` L1526-1583（`_observed_march_end`） | 月次ステータスで将軍の野営・在城を実測（行軍していない）かつ blind/kept プローブ存在 | **実測** |

巻き戻しごとに `misses[step]+=1`、将軍を出撃元 garrison へ台帳上戻し、注文を
`pending` へ戻す（L3139-3155, L3175-3178）。`misses[step] >= LAUNCH_MISS_LIMIT(2)`
（調査マーカーが単一のときは ×3 = 6、L3164-3166）で `orders[step]='failed'` +
`sortie_launch_failed`（deviation_reason『出撃確定後に部隊が現れない』、
L3167-3174 / orders.py L1573-1578）。

**コミットの成立条件**（`observe_sortie_transition` L115-134）:
送信ジャーナルの request_trace と一致する A receipt（`_departure_receipt_sent`）
があり、かつピッカーが `map` / `unknown` 画面へ遷移した時に限り
`_commit_departure` が走る（`unknown` は曖昧 fade として 3 回まで待機、L120-123）。
戦闘割り込み（`SORTIE_INTERRUPTING_BATTLE_KINDS`）は `launch_interrupted_by_battle`
として別記録され、コミットにはならない（L167-180）。

## 4. 実際に起きたこと（構造的に確定できる範囲）

`misses: 2` の成立から、issue 提示値とコード構造だけで次が**証明できる**:

1. step `M2d025c` は **2 回出撃をコミットした**（`order_launched` 2 件が窓内に存在。
   §2 の通り表示は top-30 で落ちている）。
2. 2 回目のコミットは、ベンゴディの**実画面の将軍一覧からヘパイストスを選択**
   できたことを意味する（出撃ピッカーは実一覧にない将軍を選べない。代替将軍が
   送られた場合は失敗レコードの general がその名になる — issue はヘパイストスを
   記録）。
3. よって **1 回目の「発射」は本当に部隊を出さなかった**（実際に出撃していれば
   一覧に居ない）。少なくとも 1 回の**真の未発射**が確定 → 本 issue は誤検知ではない。
4. 2 回目は、経路 (ii)/(iii) なら実測で未発射が確定、経路 (i) なら未発射・発射の
   どちらもあり得る推定巻き戻し。ただし経路 (i) の推定は「1 回目が本当に
   出なかった」観測系列と整合する側に働く。

残る不確定は「**なぜ送信ジャーナル済みの A が部隊を生まなかったか**」で、
これは g1097（「コミット 3 件が一切 ▲ を生まなかった」、コードコメント
L2878-2881）以来この機構が存在理由とする既知の UI 曖昧性である。
想定されるゲーム側原因（いずれもボットの事前検査を通過する）:

- 出撃先・条件に対するゲーム側の拒否（一覧では選択可能でも出撃確定で拒否される
  条件 — 出撃元残留人数・目的地条件等は実一覧ベースでしか検査できない）。
- 出撃先選択後の確認画面（OCR 不能で `kind=unknown` になりうる、
  `docs/operations/hanjuku-confirm-double-a-20261109.md`）の未受理。
- ピッカー中の戦闘・月次割り込み（issue の月内決定列に `egg_battle`・
  `monster_menu_choice`・`event_text_advance` が見える混雑月）。

重要なのは、**同一 120 秒窓内に発射系の決定記録（`LAUNCH_DECISIONS`）を残した
step が 3 つある**（発射 3）ことである。本 step の purpose が 'move' でなければ
その `order_launched` 行も departures に含まれるため、少なくとも 2 つは他 step
である。発射系機構（送信ジャーナル・コミット・観測）が当該月に他の注文でも
作動していたことはこれらの記録自体が示しており、本件は送信系全般の
グローバルな入力欠陥ではなく、この step に限った事象と整合する。

## 5. max gap 0.6 秒と部隊▲見逃しの検討

- `MAX_GAP_SECONDS = 60` に対し 0.6 秒なので監査側の減点はない（正当）。
- 発射プローブは壁時計ではなく**全体マップ調査イベント**（`reads` カウンタ、
  `world_map_due` で要求）を数える。部隊▲は行軍中は表示が**持続**するため、
  「0.6 秒の隙間に一瞬だけ出た」という見逃しは原理的に起きない。
- 敵城向け出撃では出撃演出が 3 回目の調査以降に▲を描くことがあるため
  `LAUNCH_PROBE_READS=5` に拡大済み（L2897-2902 のコメントと定数）。
- 自城への `move` は**▲を一切描かない**ことが実測済み
  （`tests/test_hanjuku_launch_marker_own_target.py` L1-6 の g1163 記録）で、
  その場合は調査 1 回で blind → 出撃元名簿が唯一のレシート。
- したがって gap は本件の原因ではない。関連するのは調査回数・blind 化・
  名簿到着・巻き戻し経路のみ。

## 6. 家族の切り分け

- **#5153（probe-loop）ではない。** #5153/#5182（`a58f9162a`）は「行軍中の将軍を
  未配置扱いして roster probe を 72 回再発行」する無限ループで、`replan.py`
  L1910-1919 が in-flight 将軍を located_names に加えて修正済み。本件は
  probe の再発行ではなく **step 単位の有界巻き戻し予算（2）の使い切り**で、
  終端に達して停止する設計。ループではない。
- **#5190/#5191/#5213（合法ゼロ出撃源）ではない。** あの族は「実測在城が
  sender_min 未満で合法出撃が物理的に起票できない」世界状態の帰結
  （#5190 (b) plan_starved / #5191 (b) boss_retry_exhausted / #5213 (b)
  order_start 0）であり、**出撃コミット自体が起きない**。本件は 2 回コミット
  まで到達しており、出撃源・将軍は実在・実選択可能だった。
- **発射観測欠陥（g1133/g1163/g1190-ch9 の誤巻き戻し）でもない。** あの族は
  「実際に出撃したのに ▲ 非検出で巻き戻した」誤認（ch6/ch9 は行軍部隊が
  肖像描画）。本件は §4 の通り 1 回目の未発射が構造的に確定しており、
  「出撃したのに巻き戻した」には当てはまらない（2 回目が経路 (i) の推定
  巻き戻しだった可能性は残るが、その場合でも本件の「出撃失敗があった」
  という中核主張は真のまま）。
- **整理**: 本件は **g1097 系 — 「送信ジャーナル済み A＋ピッカークローズが
  部隊を生まない」既知の UI 曖昧性** であり、発射プローブ→有界巻き戻し→
  失敗化→起票という設計の応答がそのまま働いた。

## 7. 現行 main での再発可能性と既存修正のカバー

- v1373（ラン時）→ 本 worktree HEAD で、発射プローブの機構定数・blind/名簿待ち・
  巻き戻し・miss 予算（`map.py` L2890-2913, L2943-3179 / `orders.py` L1526-1609）
  は**不変**。同じ状況（コミット→未観測×2）なら同シグネチャは再発するが、
  それは「本当に出撃しない出撃を検出する」設計の正常作動である。
- `18fe058d5..HEAD` の関連修正との対象関係:
  - #5182/`a58f9162a`（#5153 stuck roster probe 修正）: 対象外（§6）。
  - v1417/`38cf843b0`（#5237）: `_hold_unrouted_sortie` で probe/attack の経路
    ゲート通過、`roster_probe` の冷却（`arrival_probe_tick`）追加 — 発射 miss
    予算には触れていない。
  - v1419/`b3a54a4ad`（#5247）: 主人公 1 名城の move 拒否の跨ぎ抑制
    （`hero_move_refused`/`note_cancel`）— 拒否系の類縁だが、本件の step は
    主人公ではなく、コミット後の未観測失敗なので対象外。
  - 監査側: `d792fe7f0`/`a532519e2`/`22482d946`（発射系 evidence の拡充）と
    #5172/`9206d30c7`（発射成立月の単発 `sortie_order_unaccepted` を medium 化）。
    本件は `sortie_launch_failed` かつ `confirmed=0` で medium 化条件を
    構造上満たさず、high 起票は設計どおり（month_audit.py L987-1002）。
- **結論: 既存の修正でカバーされる欠陥ではないが、再発するとしてもそれは
  設計の終端報告であり、機構として直すべき欠陥は検出されなかった。**

## 8. 残存不確定点（判定を (a)/(c) に変えうる未確認証拠）

本 worktree にラン成果物がないため、以下は issue 提示値から確定できない。
いずれも `hanjuku_decisions.jsonl`（runtime `g1190-ch1v9`）で確認可能:

1. **2 回の巻き戻しそれぞれの経路** — `sortie_launch_unverified` 行の
   `reason`/`resulting_event`（『将軍一覧にまだ出撃した将軍がいる』=実測 vs
   roster 待ち期限切れ=推定）と、`sortie_record_observed_ended` /
   `roster_probe_complete` / `garrison_seen`(ベンゴディ) の有無。
   両方推定巻き戻しかつ 2 回目が実際に発射していたなら、発射観測側の
   偽陰性（blind 期限切れ経路の限界）として (a) 側に再評価の余地がある。
2. **step の目的** — `M2d025c` は `replan.py` L2697-2720 の
   `'M'+sha1('ベンゴディ>レオナール:'+purpose)[:6]`。issue の
   `world.launched_orders`/`plan` スナップショットの purpose フィールドで
   move/attack が一意に決まる（move なら自城移動で▲非描画・調査1回、
   attack なら調査5回）。
3. **直前決定列の内容** — `decisions.before`（最終 30 行）の
   `sortie_departure_requested`/`sortie_departure_input_sent`/
   `launch_interrupted_by_battle`/`sortie_confirm` 系の有無。
   `egg_battle`/`monster_menu_choice` が送信直後に並んでいれば、割り込みによる
   未受理が直接裏付けられる。
4. **ベンゴディの roster_read_due / roster_probe 起票** — blind 待ち中に
   名簿読取が mint・実行されたか（非ボス章で roster_probe が作られるのは
   敵城向け計画の中で `_interim_source`/`_boss_wave_pick` が
   `(ベンゴディ, MOVE_ANY_GENERAL)` を返した場合に限る、
   `replan.py` L1210-1226・L1336-1343・L1410-1417）。

## 9. 再現の手がかり

- `misses: 2` の再現: `tests/test_hanjuku_issue_2841.py` L109-128
  （`_rollback_unverified_launch` を同 step に 2 回→ 2 回目で `failed` +
  `sortie_launch_failed`）がそのままテスト化されている。
- 自城移動の blind 化: `tests/test_hanjuku_launch_marker_own_target.py`
  L25-37（own-target は 1 調査で blind→名簿待ち）。
- 実機での確認観点: ベンゴディの将軍一覧を最後まで読んだ時に
  ヘパイストスがいる（→真の未発射で本判定どおり）かいない（→2 回目は
  実発射の巻き戻しで観測側の偽陰性、発射プローブの推定経路の限界として
  follow-up の価値あり）か。

## 付録: issue 数値 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| `sortie_failed`/`failed`/`sortie_launch_not_observed` | `month_audit.py` L882-893 |
| `misses: 2` | `flows/map.py` L3139-3140 + L3164-3174（`LAUNCH_MISS_LIMIT=2`、L2913）/ `orders.py` L1552-1578 |
| `確定 0` | `_month_sorties`/`_note_sortie_launch`（`flows/map.py` L3061-3081: move 除外・kept/departed_observed 非加算） |
| `発射 3` | `_departures` L2768-2784（全行走査・step 重複排除・move 除外） |
| `order_start 0`/`order_launched 0` | `_decision_counts` L2813-2816（`most_common(30)` 切り詰め、VERIFY-5162 と同一 artifact） |
| コミット条件 | `observe_sortie_transition` L115-134（ジャーナル receipt＋map/unknown 遷移、fade 3 迄） |
| 発射プローブ | `map.py` L2882-2887（武装）・L2943-3058（marker/blind/名簿待ち/kept）・L3084-3127（名簿清算）・L3130-3179（巻き戻し） |
| `sender_min`（ch7=2、1 将軍城からは出撃不可のゲーム制約） | `hanjuku_chart.py` L1300-1305、`_move_general_pick` の事前取消 `deploy.py` L943-947 |
| ch7 城一覧（ベンゴディ・レオナールは通常城、ボス城は `ボス`） | `hanjuku_chart.py` L426-428・L455-459・L1365-1366 |
| 機械 step の命名 | `replan.py` L2686-2724（`M`+sha1(`source>target:purpose`)） |
| g1097/g1133 系の背景 | `docs/operations/hanjuku-launch-roster-evidence-20261105.md`、コードコメント `map.py` L2878-2881・L2953-2960・L3044-3046 |
| 直前の戦闘割り込みの記録 | `SORTIE_INTERRUPTING_BATTLE_KINDS`/`launch_interrupted_by_battle`（`map.py` L51-53・L167-180、v1371 で導入済み＝本ランに存在） |
