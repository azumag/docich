# VERIFY-5547 — [半熟英雄] 野営放置: ch9 バタール (3ヶ月)

対象: docich#5547（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1498-recall-battle-mode-lost` / 月 `ch9 8-6`（観測 1回）
occurrence(incident_id) `960e606b652bbf23b43d` / fingerprint `d09ee2cc6216092c`
created_at `1795583171.700812`（2026-11-25 05:06:11Z、#5545/#5546 と同一監査パス）

判定: **(b) 設計どおり** — 第9話 corridor 機械行軍モード（ボスフェーズ前）の
野営はオーナー指示（2026-11-11、v665 / #3936）により月次帰還の対象外であり、
#5545（ch9 カシス）・#5546（ch9 ソーピニヨン）と同一ラン・同一月・同一設計で
説明が完結する。本件の差異は **HP 1/99（瀕死）**・**卵 None（未装備）** の
2点だが、いずれも ch9 の月次タスク非積載条件（`_road_skips_checks` → `_tasks`
の `road` 分岐）が将軍の HP・卵を一切参照しないため結論を変えない。
監査の発火は契約どおりの記録であり、野営の実在は `camp_observed: true` で
裏取りできる。

調査基盤: worktree `/home/hatch/work/docich-5547`（`origin/main` =
`ef2a351b8` = v1512 `hanjuku-chart-v1512-attack-route-revalidation`、
detached HEAD）。依頼文は「現行 main（v1513 以降）」とするが、実測の
origin/main は **v1512** であり v1513 以降の版は存在しない（`version.py`
実測）。観測版は **v1498** = コミット `0193891d3`。`0193891d3..HEAD` で
`flows/post_month_status.py`・`flows/recall.py`・`flows/camp_recheck.py`・
`strategy/corridor.py`・`hanjuku_chart.py`・`os_layer/readers.py`・
`flows/house.py` に触れたコミットは**ゼロ**、`orchestrator.py` の
`_chapter9_field_recall` を含む camp 関連箇所にも差分なし（§6）。
read-only 検証: 本番 SSH なし / ラン成果物（`hanjuku_decisions.jsonl`・
`hanjuku_events.jsonl`・`hanjuku_frames/`）は VM 側で直接参照せず /
秘密情報なし / コード変更・push・PR なし / ゲーム入力なし。
根拠は issue 本文・コメント（GitHub 公開 issue ページ経由。`gh` は本環境で
実行拒否）、`origin/main` と `0193891d3` のコード、git history、
VERIFY-5545 / VERIFY-5546 / VERIFY-5522 記録。`handoff.md` 正本は
`DOCICH_HANDOFF_PATH` 未設定のため**未読**（AGENTS.md §1）。
sha256 再計算コマンドは本環境で実行が承認されなかったため、算式と入力値の
照合のみを記録し、再計算は**未実施**とする（§1）。

---

## 結論

**(b) 設計どおり。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L61）を閾値に
   `_track_camp_abandon`（L2674-2748）が 3 ヶ月以上 camp の将軍を検知し、
   `_build_camp_abandon_incident`（L1638-1727）が incident を起票する。
   バタールは `months = 3`・`first_month = '8-4'`・`first_tick = null` で、
   月境界加算（8-4 で新規 entry → 8-5、8-6 の月替わりで 1+1+1=3）による
   閾値到達と一致する。`camp_observed = true`・`hp 1/99`・
   `status_text = 'キャンプしています'` は名簿行の実測転記（
   `_build_camp_abandon_incident` L1674-1688）であり、静的雛形ではない。
   `_camp_generals`（L1592-1606）は `hp <= 0` の行を除外するが、
   hp=1 は生きた野営として**正当に集計対象**。

2. **8-6 時点の ch9 は corridor 機械行軍モードであり、野営の非回収は
   オーナー設計そのもの。**（#5545/#5546 と同一の世界状態・同一月）
   - `world.captured = ['ハヤク','タオ']`、`lost = []` → `_owned` =
     `{アルマムーン, ハヤク, タオ}`。
   - `chart.corridor(9)` = 12 城（`hanjuku_chart.py` L1251-1254、
     `'ボス'` は含まない）。`corridor_next(9, owned)` = `サヌト`
     （L1279-1285）→ `boss_phase` = False（`recall.py` L90-105）→
     `camp_stays` = False（L131-138）かつ `corridor.mechanical` = True
     （`strategy/corridor.py` L84-89）。
   - `plan.orders` は全件 `I:c9:` corridor step（`タオ>サヌト` launched ×2、
     `ハヤク>タオ` move/attack 系列）、`world.sorties` も en_route のみ
     —— 機械行軍が稼働中。
   - このモードでは `_road_skips_checks` = True（`post_month_status.py`
     L191-203）となり、`_tasks`（L225-308）の `road` 分岐（L234-238）が
     **全将軍を `continue`** して野営将軍に recall/locate/boss_move の
     いずれのタスクも積まない。オーナー 2026-11-11: 「キャンプになった
     将軍は仕方ないのでこの章は放置」（L193-197）。導入は v665 / #3936。
   - house 側も同じ設計で連動する: `_corridor_march`（`house.py`
     L1349-1358 = `corridor.mechanical`）なら卵調査・城巡回・修理派遣も
     行わず画面を閉じる（L1603-1606、オーナー 2026-11-11）。

3. **HP 1/99（瀕死）は結論を変えない —— 月次タスク層は HP で積載を
   判定していない。**（§3）
   - 瀕死将軍専用の帰還・回復タスクは存在しない。野営将軍の回復経路は
     `{'kind':'recall'}`（L282）→ 在城での自然回復（`house.py` L2501:
     「負傷またはHP未確認の将軍を無装備の卵修理へ派遣せず在城で回復を
     待つ」）の一経路のみ。
   - HP が使われるのは `_hp_rank`（L297-306、docich#3008）による
     recall/boss_move ジョブの**並び順**だけであり、タスク生成の条件
     ではない。road 分岐は並び順以前にキュー自体を作らない。
   - road 分岐内で唯一積まれる `{'kind':'repair'}` は
     `location=='castle' and broken`（卵破損の修理）を要求し、HP は
     見ない。バタール（location='camp'、broken=False）は要件外。
   - 仮に非 road 章だったとしても、`_boss_move_ready`（L135-146）は
     `hp > max_hp//2` を要求するため HP 1/99 ではボス再突入には
     ならず、`_boss_camp_holds_siege`（broken≠True のボス隣接野営は
     包囲免除）側に留まる設計。

4. **卵 None（未装備）も結論を変えない。**（§4）
   - `_tasks` の野営分岐は `egg` を参照しない。卵関連の条件は `broken`
     （卵破損 → repair）のみで、`egg=None`（装備なし）と `broken=False`
     は整合する（壊れる卵が存在しない）。
   - 卵の有無が効くのはボス波の編成（`replan.py` L1345-1346、
     `_boss_eggless`: 卵なしの波は必敗として除外）と戦闘中の召喚判断
     であり、野営の帰還経路・積載条件とは独立。
   - したがって卵 None は「バタールがボス波の駒として使えない」ことを
     示す情報であって、野営放置の判定・設計には影響しない。

5. **回収経路は設計上存在し、本件の滞留はその適用条件にまだ達していない。**
   - フィールド野営帰還（#3958）: `_chapter9_field_recall`
     （`orchestrator.py` L73-101）が `corridor.mechanical` なら True を
     返し、アイドルな map フレームを `camp_recall_step` へ渡す。ただし
     起動するのは**フレーム内にテントが見えた場合だけ**。バタールの
     `castle_candidate = 'ボス'` は `marker_castle` が
     `MAP_CASTLE_TOLERANCE = 96`（`readers.py` L458）内で実測ボスセルへ
     束縛した位置にあり、回廊カメラビューには映らない。決定内訳に
     `camp_hunt: 1`（非検出の記録）があり、帰還系決定は 120 秒窓に
     0 件 —— 「機構が動いて失敗した」形ではなく設計内の非可視野営。
   - ボスフェーズ（corridor 全 12 城確保後）: `camp_stays` = True となり
     `_tasks` L239-240 が野営を `continue` し、フィールド野営フローが
     `boss_redirect_goal`（`recall.py` L108-128: boss_phase ∧
     `moving_boss_cell(9)` 非空 ∧ `wave_stale` でない）でボス最新セルへ
     再指向する。本件時点は corridor 未完（サヌトが敵）のため未到達。

## 1. fingerprint / incident_id の式照合

| issue の値 | 対応算式（`ef2a351b8` = 現行 main 上の行番号） | 照合 |
|---|---|---|
| 判定 `camp_abandoned` / confidence `high` | `_build_camp_abandon_incident` L1638-1727、`CAMP_ABANDON_MONTHS=3` L61、`'confidence': 'high'` L1669 | 一致 |
| 継続 3ヶ月 / 初回 8-4 / first_tick null | `_camp_age_months`/`_camp_month_starts` L1609-1635、`_track_camp_abandon` L2674-2748 の月境界加算（L2692-2701） | 一致。8-4 新規 entry（months=1）→ 8-5 → 8-6 で 3 |
| HP 1/99 / 卵 null / broken false / `キャンプしています` / last_tick 163968 | 同 L1674-1688（statuses row を転記） | 一致。row tick 163968 は月 tick 166512 より 2544 古い（前月の名簿読み、#5545/#5546 と同程度） |
| castle_candidate `ボス` | 同上（`marker.castle_candidate`）＋ `is_boss_castle`（`hanjuku_chart.py` L1409） | 一致。実測ボスセルへ束縛（#5545 カシスと同型） |
| 出撃 確定7 / 発射4 / order_start9 / order_launched4 | 同 L1689-1695 | 一致（#5545/#5546 と同一月スナップショットの同一値） |
| signals 4 件 `camp_abandoned` / `camp_abandon_3months` / `battle_grind` / `no_source` | 同 L1649-1653（基本 2 個＋`_signals()` L3197-、最大 8 個） | 一致（#5545/#5546 と同一の 4 件） |
| `flow: {}` | `_summarize_flow` L441-481 → context.flow 空を転記 | 一致（帰還未起動。§5） |
| incident_id `960e606b652bbf23b43d` | 同 L1720-1722: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | 算式・入力（`g1190-ch1v9\|1190\|camp_abandoned\|バタール\|None`）と一意に対応。**再計算は本環境で実行拒否のため未実施** |
| fingerprint `d09ee2cc6216092c` | 同 L1724-1725 → `_fingerprint` L3319-3321: `sha256("9|camp_abandoned|camp_abandoned")[:16]` | 算式・入力が一意に対応。**再計算未実施**。`chapter|kind|primary` のみで決まるため #5545/#5546 と同一値になることは式上必然 |
| issue タイトル格式 | `_issue_title` L4589: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

補足: `first_tick` が null のため incident_id の raw 文字列は Python
f-string の `None` レンダリングを含む（`...|バタール|None`）。
incident_id は将軍名を含むため 3 件それぞれ別値
（カシス `d05e5c434f1b69576d1a` / ソーピニヨン `40e792ed1f529ddeaeb5` /
バタール `960e606b652bbf23b43d`）となるのが式どおり。

## 2. #5545（カシス）・#5546（ソーピニヨン）との異同

| 観点 | #5545 カシス | #5546 ソーピニヨン | **#5547 バタール** |
|---|---|---|---|
| ラン / generation / 月 | g1190-ch1v9 / 1190 / ch9 8-6 | 同左 | 同左（同一監査パス、同一 created_at） |
| 継続 / 初回 | 3ヶ月 / 8-4 | 3ヶ月 / 8-4 | 3ヶ月 / 8-4 |
| HP / 卵 / 壊れ | 73/73・いっぱつエッグ・False | 48/48・イビルエッグ・False | **1/99・None・False** |
| 城候補 | ボス | 不明（null） | ボス |
| flow | `{}` | `{}` | `{}` |
| 最終観測 tick | 163950 | 164019 | 163968 |
| fingerprint / incident_id | `d09ee2cc6216092c` / `d05e…761a` | `d09ee2cc6216092c` / `40e7…aeb5` | `d09ee2cc6216092c` / `960e…b43d` |
| 結論 | (b) | (b) | **(b)** |

差異が結論を変えない理由:

- **HP 1/99**: ch9 road モードの `_tasks` は「将軍ごとの分岐条件」として
  HP を一切参照しない。月次タスク非積載は将軍の状態（HP・卵・城候補）に
  無条件の road 設計であり、「瀕死だから救済されるべき」という経路が
  コード上そもそも存在しない（§3）。ゲーム内リスクとして
  HP 1 の野営将軍が敵に遭えば落とされうる（`_tasks` L303-304 の
  docich#3008 コメントが同構造のリスクを記録）は残件として残るが、
  「この章は放置」のオーナー指示は HP で条件分けされていない。
- **卵 None**: 帰還経路の条件に卵は使われない（§4）。ボスフェーズ移行後の
  ボス再指向（`boss_redirect_goal`）も個々の `egg`/`castle_candidate` を
  見ない章レベル条件。卵なしでボス波の駒になれないのは別層（replan の
  `_boss_eggless`）の話で、野営放置判定とは独立。
- **城候補 ボス**: #5545 カシスと同型。ボス城の実測セルへ束縛＝ボス隣接の
  期限切れ行軍野営であり、ボスフェーズ移行後の集合・包囲メンバーとして
  設計と整合する。ch9 では `_boss_camp_holds_siege` 自体が road 分岐の
  `continue` より先に評価されないため、判定の主因ではない（#5546 §3）。

## 3. HP 1/99 の瀕死野営の扱い

- **月次帰還・回復経路の有無**: 野営将軍を回復させる専用経路は
  `{'kind':'recall'}` のみ（`post_month_status.py` L282）。帰還後は在城で
  自然回復する（`house.py` L2501 の文言どおり「在城で回復を待つ」が
  負傷将軍の既定処置）。HP 回復を目的とする task kind は存在しない。
- **repair 分岐との関係**: `_tasks` road 分岐内の唯一の積載
  `{'kind':'repair'}`（L234-237）は `location=='castle' and broken` を
  要求する**卵破損の修理**であり、HP 回復ではない。バタールは
  location='camp'、broken=False のため対象外。
- **road 分岐での打ち切り**: `road` が True の各将軍ループは
  repair 判定の直後に `continue` する（L234-238）。瀕死将軍への
  帰還・回復経路は**そもそもタスクとして定義されておらず**、定義されて
  いる経路（recall）は road 分岐で打ち切られる —— すなわち「瀕死だと
  別扱い」という設計がこの層には存在しない。
- **仮定評価（非 road 章だった場合）**: 城候補 `ボス` かつ broken=False
  → `_boss_camp_holds_siege` = True で包囲免除が先に立ち、`_boss_move_ready`
  は `hp 1 > 99//2` を満たさず boss_move 不可 → 「免除を保ったまま留まる」
  終端。recall に回るのは包囲免除の期限切れ（`BOSS_CAMP_EXEMPT_MONTHS`
  超過）か城候補がボスでなくなった場合。ch9 ではこの評価自体に到達しない。
- **補助線**: `_camp_generals` は `hp <= 0` を検知対象から除外する
  （L1602-1604）。HP 1 は「生きた野営」として監査対象に残る最小値であり、
  監査が瀕死を見落としていたわけではない。

## 4. 卵 None の意味

- **データ上の意味**: 名簿行の `egg` は装備中の卵名（いっぱつエッグ・
  イビルエッグ等）で、None は**卵未装備**の実測値。#5545/#5546 と同じ
  読み経路で HP/max_hp・状態文が実測できており、卵欄だけ未読になった
  形跡はない（`camp_observed: true` で行全体が観測済み）。
- **帰還・召喚経路との関係**:
  - 月次帰還（recall）: `egg` を条件に使わない（§3）。
  - 卵修理（repair）: 対象は `broken` な卵。卵未装備は broken になりえず
    （壊す卵が無い）ため、卵修理・家の贈り物経路とは無関係。
  - ボス波編成: `replan.py` の `_boss_eggless`（L750-764）が卵なし将軍を
    ボス波から外す（「an eggless wave is a pure loss」L1345-1346、
    オーナー 2026-11-05「ボス戦のダメージは卵のもの」）。バタールは
    ボスフェーズに到達しても波の駒としては使われない —— ただしこれは
    編成側の選別であり、野営の帰還・再指向条件ではない。
- **結論への影響**: なし。卵 None は「バタール個人の資産状態」の情報で、
  ch9 road 設計（月次タスク非積載）と帰還経路のいずれにも接続しない。

## 5. flow.recall_verification の有無と意味

- **本件には無い**（`"flow": {}`、issue JSON 実測）。`recall_verification`
  は `_recall_dispatch_step` の受理契約（input_sent ∧ picker_closed ∧
  !interrupted ∧ !scene_covered → `status:'dispatched'`、`recall.py`
  L2591-2708 付近）を通過したときだけ `_flow` に書かれる層。本件は
  road モードで帰還タスクが積まれず、フィールド側もテント非可視で
  帰還未起動のため、この層の記録が無いのは設計と整合する。
- 仮に存在しても `_summarize_flow`（`month_audit.py` L441-481）は
  `recall_verification` を汎用ループ（L469-480）で `stage/kind/status/
  result/attempts/reads/steps` のみに縮小し、`general`・`goal`・`camp`
  を落とすため、バタール本人の帰還かどうかの帰属はできない
  （#5431・#5545・#5546 と同じ投影限界）。本件では flow が空なので
  帰属問題自体が発生しない。

## 6. v1498 → 現行 main（v1512）の差分カバー範囲

- 依頼文は「現行 main（v1513 以降）」とあるが、実測の origin/main は
  `ef2a351b8` = **v1512**（`version.py`: `hanjuku-chart-v1512-attack-route-
  revalidation`）。v1513 以降の版は存在しない。
- `git log --oneline 0193891d3..HEAD` で対象ファイル群への変更:
  - `flows/post_month_status.py`・`flows/recall.py`・`flows/camp_recheck.py`・
    `strategy/corridor.py`・`hanjuku_chart.py`・`os_layer/readers.py`・
    `flows/house.py`: **変更ゼロ**。
  - `orchestrator.py`: 変更ありだが diff に camp / `_chapter9_field_recall`
    を含む行なし（grep で 0 件）。
  - `month_audit.py`: 変更あり（`#5180` stuck-march 監査の到着済み出撃除外、
    `v1509` ボス戦以外 HP1 切り札起票、`v1461` battle_result 生存一覧公開、
    `#5500` ボス突入直後の無戦闘誤報抑止、`#5525`/`#5530` 決定カウント
    全件化）。diff の `+`/`-` 行に `camp` を含むものは **0 件** ——
    camp_abandoned 検知系（`_track_camp_abandon`・
    `_build_camp_abandon_incident`・`_camp_generals`・`_camp_age_months`・
    `_camp_month_starts`・`_fingerprint`・incident_id 式）は両版で同一。
- したがって **camp_abandoned の検知機構と ch9 の帰還経路に v1499-v1512
  の変更は入っていない**。本件をカバーする修正 PR は存在しないが、
  そもそもカバーを要する欠陥ではない（結論 (b)）。

## 7. 残件（read-only では確定不能、判定 (b) の限界）

判定 (b) は「監査レコードの実在性」と「滞留がオーナー設計で説明完結する」
点への結論であり、以下は VM 側ラン成果物が無いと確定できない。

- バタールが `castle_candidate='ボス'` の位置へ野営化した**直接経緯**
  （回廊行軍の敗北野営か、旧計画のボス指向行軍の停滞か）。
  `hanjuku_decisions.jsonl` の `sortie_outcome`/`sortie_history`・
  `battle_result`（バタール名）、8-4 以前の `camp_found`/`own_camps` 記録。
  HP 1/99 であることから戦闘敗北・退却由来の可能性は高いが、証跡なしでは
  確定不能。
- 8-4〜8-6 の各月でバタールのテントがフレーム内に映ったか
  （映っていた場合に `camp_recall_step` が起動しなかったか）。決定ログの
  `camp_found`/`camp_recall_*` 系の有無。
- `first_tick: null` の経緯 —— `camp_status_seen` に tick なしは
  camp_recheck の名前付き野営確認を経ていないことを示唆する
  （`camp_recheck.py` L353-397、#5546 §8 と同じ観察）。
- **瀕死野営の固有リスク**: HP 1 の将軍が野営中に敵と遭遇すれば
  永久喪失しうる（`_tasks` L303-304、docich#3008 が HP 20→10→2 の
  野営将軍が「met and killed」になりうると記録）。road 設計はこの
  リスクを飲み込む（「仕方ないのでこの章は放置」は HP 非条件）。
  瀕死将軍だけでも救うべきかは設計判断の範囲であり、コード欠陥ではない。
- ボスフェーズ移行後（corridor 全制覇）にバタールが `boss_redirect_goal`
  で実際に再指向されたか、あるいは卵なし・瀕死のまま野営に残ったかの
  追跡（次以降のラン観測）。卵なし将軍はボス波の駒にできないため、
  再指向されても実戦参加は別層の編成判断に依存する。
- 監査ポリシーの要否: `_track_camp_abandon` には ch9 機械行軍（road 設計）
  への免除が存在しない。同一ラン・同一月に 3 件（#5545/#5546/#5547）の
  設計どおり野営が連続起票されており、「設計どおりの野営を毎回 issue 化
  することの是非」は監査ポリシーの問題として DSH 判断に委ねる
  （VERIFY-5420 §6 / VERIFY-5545 §8 / VERIFY-5546 §8 と同じ取り扱い）。

## 8. 他の判定を採らない理由

- **(a) でない**: 野営将軍への月次帰還タスク非積載は `road` 設計そのもの
  で、HP 1/99・卵 None を特別扱いする経路がコード上存在しない以上、
  「瀕死を救わない」のは欠陥ではなく設計の無条件適用。例外形・未解決
  goal・機構失敗の証跡は incident に無い（flow 空、recall 系決定 0、
  camp_hunt は設計内の非検出記録）。
- **(c) でない**: 野営は実在（`camp_observed`・実測 HP/状態文）し、
  3 ヶ月の算定も契約どおり。「野営していないのに起票」ではない。
- **(d) でない**: 滞留の説明がコード内オーナー指示（v665/#3936 の
  road skip → boss phase で camp_stays＋ボス再指向）で一意に完結する。
  HP・卵の差異は判定条件に接続しない（§3/§4）ため、新たに VM 側証跡を
  要する未確定点は残件扱いで十分であり、判定は保留しない。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`, `battle_grind`, `no_source`
- confidence: `high`（固定、L1669）
- incident_id: `960e606b652bbf23b43d`（式: `sha256("g1190-ch1v9|1190|camp_abandoned|バタール|None")[:20]`、再計算未実施）
- fingerprint: `d09ee2cc6216092c`（式: `sha256("9|camp_abandoned|camp_abandoned")[:16]`、再計算未実施。#5545/#5546 と同一値は式上必然）
- 結論: **(b) 設計どおり** — 第9話 corridor 機械行軍モードの野営放置は
  オーナー設計（v665/#3936 road skip → boss phase で camp_stays＋ボス再指向）。
  HP 1/99・卵 None は ch9 の非積載条件・帰還経路条件のいずれにも接続せず、
  #5545/#5546 の結論を変えない。検知は契約どおり、実欠陥なし、
  カバーする既存修正は不要。

記録: issue #5547 への結論コメント投稿は検品ゲートに委ねる。本レーンは
コード変更・push・PR を行わない（read-only 検証のみ）。
`git checkout -b` / `git branch` / `git add` / `git commit` は本環境で
実行が承認されなかったため、本ファイルは worktree 直下に**未コミット**の
まま残す（VERIFY-5522・VERIFY-5545・VERIFY-5546 と同じ状況・同じ手順）。
拾う場合は `verify/5547-camp-abandoned` ブランチを切って本ファイルだけを
コミットすればよい（push は不要）。
