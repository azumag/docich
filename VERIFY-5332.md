# VERIFY-5332 — [半熟英雄] 月内出撃ゼロ: ch11 出撃開始後に発射されない

## 対象

| 項目 | 値 |
|---|---|
| Issue | docich#5332「[半熟英雄] 月内出撃ゼロ: ch11 出撃開始後に発射されない」 |
| 判定 | `zero_sortie` (confidence: high)、signature `departure_failure` |
| 章 / 月 | ch11 / 月 17-12（当月中に召喚獣の長期戦: `monster_skill_stall`、`monster_menu_choice`=おうふくビンタ を観測） |
| run | g1190-ch1v9 / `hanjuku-chart-v1447-password-measured-order`（v1447） |
| fingerprint | `81b4920bab033144` |
| occurrence | `081af5fbf0aefa5b81db` |
| 観測 | 2993回 / 556.6秒 |
| 出撃 | `order_start`=12 / `order_launched`=0 |

## 調査基盤

- リポジトリ `azumag/docich`、worktree `/home/ubuntu/repos/docich`、ブランチ `verify/5332-zero-sortie`。read-only 検証のみ（コード変更なし）。
- 行番号は `origin/main` (`3a829da9d`, `BOT_VERSION='hanjuku-chart-v1451-defense-wait-probe'`, `src/docich/hanjuku/version.py:7`) のツリー基準。`python3 -V` = 3.10.12。
- 本番 VM への SSH は行っていない。`hanjuku_decisions.jsonl` の行単位証拠は VM 側にあり未取得（後述の留保に記載）。
- `handoff.md` は参照できず未読（`DOCICH_HANDOFF_PATH` 未設定）。本検証は issue 本文と `origin/main` のコードのみを根拠にする。
- 関連テスト実行: `PYTHONPATH=src:$HOME/py311shim python3 -m pytest tests/test_hanjuku_defense_wait_probe.py tests/test_hanjuku_month_audit.py -q` → **142 passed**（pytest 9.2.0 導入、`$HOME/py311shim/tomllib.py` を tomli で補完）。

## 結論

**(b) 設計どおり —— 発射ゼロは「城が空になる禁止出撃」の合法な阻害。12回の `order_start` は発射を目的としない検証/読取/中断される開始であり、#5324 の機構（probe 解除ループ）の記録的痕跡はない。**

### 結論の根拠（要約）

1. **発射不可能な状態だった**: 月末時点で自城3城（アルマムーン=シフォン、タタール=どうし、タプロベイン=ミュスカデ）はいずれも**在城1将軍**。`sender_min`=2（`src/docich/hanjuku_chart.py:1303-1305`、`guard_free` は ch9 のみ `:1308-1318`）に対し、`_source_spare_missing`（`src/docich/hanjuku/strategy/orders.py:2414-2418`）が `len(set(present)-unavailable)<2` の出撃元を pre-flight で遮断するため、通常の発射指示はどの pending step からも選べない。月末に pending のまま残った12 step（c11f31e2×7, e6c9732e×3, c007aa35×2）がその裏付け。`no_source` シグナル（`src/docich/hanjuku/month_audit.py:2949-2952`）はこの状態を正しく検出している。
2. **翌月に自然回復**: issue JSON の after 区間（686 obs）で recruit_join_announced=9 → departures=6、first_launch_seconds=126.2、recovered=true。待機→月次一括補充→再開という設計の挙動どおり。
3. **12回の order_start は発射目的でない**: `order_launched`/`sortie_departure_requested`/`order_done`/`order_failed` が0件。一覧に届いた確定的判断（`sortie_held_*`、`roster_probe_*`、`sortie_verification_held`、`order_precondition_lost`）も全て0件 —— 開始はいずれも一覧到達前に中断されるか、発射を伴わない検証/読取だった。

## 1. 監査ロジックの検証

| 主張 | 根拠 | 評価 |
|---|---|---|
| 当月の発射が0件 | `departures` は `LAUNCH_DECISIONS` 系カウント、`order_launched`=0 | 正しい。月次 joiner を除く真の発射は0件。ただし `order_start` は `departures` に含まれない（`map.py:2664` の `_record 'order_start'` は起票行為）ため「開始あり＋発射0」は VERIFY-5324 と同じく**signature 名 `departure_failure` の誤読可能性**を残す（`month_audit.py:271` 命名、`sortie_launched`/`sortie_departure_requested` だけが発射証拠） |
| 観測間の出撃状態 | `sorties`/`orders` のスナップショット | Maa714e（タタール→トリフェ, status `launched_unconfirmed`, 906t）は Z の月から残る待機済みレコードであり、当月の失敗ではない |
| `order_start`=12 の異常性 | `ORDER_START_BACKOFF`=12 obs（`flows/map.py:1117`）で同一 step の再開始を抑制 | 同1 step が集中再発した形跡は本文に無く、12回は別 step または backoff 超過後の再選定が想定される |
| フィンガープリント | `issue_rune_fingerprint` は signature + chart_digest（`month_audit.py:3032-3034`） | 設計どおり |

## 2. 経路の特定（`order_start` → 発射なしの経路）

`map.py` の dispatch 本体で `order_start` は `plan['active'] = order['step']`（`:2623`）直後の `_record`（`:2664`）で記録される。記録後に発射へ至らない経路を列挙すると:

- **最終防衛者の遮断（設計どおり、当該月の主因）**: `_last_castle_held`（`orders.py:2017`）と `_source_spare_missing`（`:2001-2002, 2378-2434`）で `next_order` が開始不可な step を除外。一覧でも `_hold_deploy`/`_hold_general_list_bounded`/`_keep_last_castle`/`_keep_sortie_defender`/`_keep_hero_guard`（`deploy.py:843-896, 1739-1896`）が続く。今月は遮断側の決定的な保留記録（`sortie_held_*`）すら残らない＝そもそも sortie シーケンスを開始した試みが0件。
- **中断（設計上、月を占有した正体）**: 防衛戦闘（`castle_defender_seen`=15, `battle_start`=15, `castle_defender_encounter`=5, `battle_no_attack`=87）・召喚長期戦（`monster_menu_choice`=22, `monster_skill_stall`）・月次イベント（`month_sub_step`=29, `month_event_prompt`=24, `month_event_wait`=52）が月を占有。`ORDER_START_BACKOFF` のコメント自体が「防衛/イベントで中断した開始が即座に再起動していた」設計を記述（`map.py:1124-1128`）＝開始→中断→再開始の往復は想定内。
- **検証/読取の開始（発射を伴わない）**: `launched_unconfirmed` な Z の sortie（Maa714e）は `_order_for_step` で verify-order として解決（`orders.py:889-899`）し、`_verify_sortie_source`（`deploy.py:76-133`）で一覧を読んで判定する。`sortie_verification_held` は reads≥3 でのみ記録（`:93-101`）し、reads は `sortie` レコードに累積するため、**一覧到達≤2回では sortie_verification_held も残らない**（`:101-113` の hold は `situation_held` のみ）。読取専用の `roster_probe`/`source_probe`（`orders.py:2381-2382, 2390-2392`）も `departures` に数えない。
- **保留と終了（記録あり、いずれも未発火）**: `_drop_orphan_active`（`orders.py:2060-2084` → `order_active_orphan`）、`_expire_spent_sorties`（`orders.py:1569+` → `sortie_spent_expired`）、`stale_active_released`（`map.py:1180-1208`）、`order_precondition_lost`（`map.py:2553`/`deploy.py:2248`）—— いずれも0件。

## 3. 12回の order_start の切り分け（焦点3）

結論から言うと: **完走した roster_probe は0件。通常の発射指示も0件。開始の大部分は `launched_unconfirmed` の verify、probe（=対象城の読取で止まる）の mint 結果、または参入将軍が未配置の間だけ選べる入城待ち城オーダー**のいずれかで、発射能力を持つ通常 sortie 指示ではない。理由:

- **roster_probe 完走0件**: `_is_roster_probe_step` は `source_probe`/`roster_probe`/`arrival_probe` フラグを判定（`orders.py:2298-2304`）し、probe が一覧到達して完走すると `roster_probe_complete`（来た道）または `roster_probe_incomplete`（未到達）を**必ず**記録して `_finish_order('done')`/`('failed')` に入る（`deploy.py:2308-2349`）。当月は `roster_probe_*`=0、`order_done`/`order_failed`=0 → **一度も probe が完走していない**。started-but-interrupted な probe は `order_start` だけ残るため、12回に probe の**開始**が含まれても完走記録は出ない。
- **interim 指示0件**: `_mechanical_interim` は mint のたびに `chart_interim_order` を記録（`replan.py:2189-2304`、mint は `:2334-2341`）。`chart_interim_order`=0 → 当月に I: step 系 interim の mint は0件（コード上 mint 位置は `:2296` 周辺で `chart_interim_order` 記録）。issue の `orders` 表にも `I:` step は無い。
- **機械計画の再採用で probe 系が mint されていた**: `chart_adjust_applied`=10（`replan.py:2994-3000` 記録、signature gate なし）、`plan_requests`=26、`mechanical_chart` は `interim_candidates` を source_probe/roster_probe として candidates に mint する（`:2641, 1903-2015`）。月末に残る Mcf35a3 の note `暫定: 膠着のためアルマムーンの将軍一覧を読んでからチリベットへ出撃する` は `probe_stuck_1`（`replan.py:2014`）/ `_stale_boss_roster_probe`（`:2084`）の定型文と一致 → 当月も同形式の読取専用オーダーが mint されていた。`_adopt_plan` は測定で在城1将軍の出撃元 step を `roster_probe` 化する（`:2943-2948`）ため、1将軍の3城に出す step は自動で読取専用になる。
- **参入将軍で一時的に開始可能だった step**: `_source_spare_missing` は「参入将軍が到着城に未配置」の間だけ `False` を返す（`orders.py:2411-2413`）。`monster_join`=3 が観測され、翌月の `recruit_join_announced`=9 が来る直前まで、加入将軍は未配置として残っていた可能性が高い。この期間だけ step を開始でき、一覧に届く前に中断される → `order_start` が増えて `order_launched`/`sortie_held_*` は増えない。
- **verify（launched_unconfirmed）の繰り返し**: Z の月の Maa714e は `orders` 表で `launched_unconfirmed`（`_source_spare_missing` :2379-2380 で sparing 免除、`next_order` の retries 経路）。`execution_claimed`（`execution.py:139-165`）で attempt が claim される間は `_order_for_step` が None を返し、`plan['active']` が unresolvable になっても記録なしに再選定（`map.py:2571-2623`）が起こりうる —— `map.py:2576-2580` は `launched_unconfirmed` 検証専用の pick。`sortie_verification_held` が残らないので verify 一覧到達は≤2回。

つまり12回は「Z の月末残りの verify の読取」と「機械計画が mint した probe 系」と「参入将軍で一時的に選べた step」の合計であり、**発射を目的とする通常 sortie 指示ではなかった**。issue 本文の `orders` 表に `order_start` の step が載っていないため、12回すべての step は確定できない（後述留保）。

## 4. #5324(a) との異同（焦点1）

| 観点 | #5324 (ch3) | 本件 (ch11) |
|---|---|---|
| 機構 | defense_wait が probe だけの candidates で解除され cycles がリセット、interim 経由で probe を mint し続けるループ | **同じループの記録的痕跡がない** —— `chart_defense_wait`/`_released`/`_eval`/`_escalated` 全て0件、`chart_interim_order`=0、`roster_probe_*`=0 |
| `order_start` の性質 | 6回すべて roster_probe（完走記録あり） | 発射目的でない開始（verify/probe mint/参入一時）であり、probe 完走記録は無い |
| 月の占有 | probe ループ自体が月を占有 | 防御・召喚戦闘と月次イベントが占有（`battle_grind` シグナル） |
| v1451 の関係 | v1451 (PR #5343) が `probe_only` 判定と `no_attack_leg` 時の本拠優先で直接治す | `_defense_gate`/`interim_candidates` は章非依存だが、本件の記録系列は v1451 が触れる経路を一度も踏んでいない → **本件は v1451 の直接の対象外。同型発生は v1451 で予防済み** |

## 5. #5264(b) との異同（焦点2）

- **共通**: 月は外部イベントで占有され、ゼロ発射は違反ではない。#5264 も本件も `zero_sortie` 検出は正しい。
- **相違**: #5264 は `defense_wait` の hold/cycles が健全に動く「待機」だった。本件は wait 記録自体が無く、開始→中断→再開始の往復が続いた。これは defense_gate の判断というより、dispatch 側（`map.py` の `_order`/`next_order`/`order_start`）が繰り返し起票された現象。

## 6. バージョン差分（v1447 → v1451）

観測 run は v1447。`origin/main` (v1451) との差分のうち本件関連:

- `b68f1fd20` (v1448): 自動携行系 — 本件無関係
- `cc2b2d969` (v1449): `_rearm_phantom_boss_steps` 修正（`orders.py:1693-1773`、#4832 家族）— `boss_step_rearmed`=0 で本件未発火
- `c01748172` (v1450): 検証系修正
- `2cbe3542f` (v1451, PR #5343): `_defense_gate` の `probe_only` 判定と `interim_candidates` の `no_attack_leg` 本拠優先 — 本件の記録系列に未発火
- `3e24879f0` (PR #5355): soviet_now 参照更新 — 無関係

→ **本件の観測機構に直接触れる変更はない**。

## 7. テスト実行結果

```
PYTHONPATH=src:$HOME/py311shim python3 -m pytest \
  tests/test_hanjuku_defense_wait_probe.py tests/test_hanjuku_month_audit.py -q
→ 142 passed in 12.38s
```

- `tests/test_hanjuku_defense_wait_probe.py`: v1451 (PR #5343) の `probe_only`/`no_attack_leg` 経路を固定する回帰テスト。全 pass。
- `tests/test_hanjuku_month_audit.py`: `departure_failure` signature、`no_source` シグナル、kind 判定の回帰テスト。全 pass。
- 本件専用の回帰テストは存在しない（実装変更なしのため追加していない）。

## 残る不確定点

- **12回の `order_start` の正確な step 内訳**: issue 本文の `orders` 表は月末時点のスナップショットで、`order_start` の行は含まれない。`hanjuku_decisions.jsonl`（VM 側）の行単位証拠があれば、12回すべての `chart_step`/`roster_probe` フラグを完全確定できる。本検証は VM SSH を禁止されているため未取得。
- **silent 反復の原因**: `plan['active']=None` を起こす全サイトを列挙したが、全て counted な決定を記録する。よって反復は (i) 中断後の合法再開、または (ii) `_order_for_step(active)→None`（`execution_claimed` の claim 不一致、`execution.py:139-165` のコメントが指す「zero sorties left while order_start repeated」の既知族、`orders.py:2379-2380` の `departure_binding` 矛盾）による unrecorded 上書きのいずれか。`(ii)` が支配的なら状態管理の記録上のギャップだが、発射ゼロの結論・判定は変わらない。
- **Verify 一覧到達回数**: `sortie_verification_held`=0 は reads≤2 を意味するだけで、12回のうち何回が verify だったかは分からない。

## 結論（まとめ）

(b) 設計どおり —— **発射ゼロは3城1将軍の合法な禁止出撃（`sender_min`=2 と `_source_spare_missing`）が原因で、翌月の加入将軍による自然回復も確認できる。12回の `order_start` は発射を目的としない検証/読取/中断される開始で、`departure_failure` signature が mislabel している。`#5324` の probe 解除ループ機構の記録的痕跡はなく、v1451 (PR #5343) の対象外（同型発生は予防済み）。**

### フォローアップ（実装しない）

- `order_start` の記録に `roster_probe`/`source`/`target` を添えて、監査の「出撃開始」の意味を読取専用・verify・実出撃試行で分離できるようにすると、`departure_failure` signature の精度が上がる（別 issue の扱い）。
- `hanjuku_decisions.jsonl` の行単位で `order_start` の step を出すか、`sortie.verification_reads` を issue JSON の `sorties` 行に含めると、今後の VERIFY で 12 回の内訳が確定できる。
