# VERIFY-5233 — [半熟英雄] キャンプ帰還未確認: ch1 アルマムーン

対象: docich#5233（監査自動起票）/ ラン `g1342-1fe0734c` / generation 1342 /
`hanjuku-chart-v1414-retreat-ban` / 月 `ch1 1-6`（観測 288・432.7 秒・max gap 3.7 秒）
調査基盤: worktree `~/work/docich-5233`（branch `verify/5233-camp-recall`、HEAD
`38cf843b0`＝v1417 #5237 マージ済み）。read-only 調査。コード・issue 提示値のみ。
本番 VM SSH なし。`handoff.md` 正本参照は未設定のため未読と記録する（AGENTS.md §1）。
pytest / gh は未使用。判定はコード読解と issue 提示値による。

---

## 結論

**(a) 実欠陥。** ただし欠陥は「未確認と判定したこと」ではなく、
**`input_sent` 後に割り込んだ防御イベント（`defense_observed` → `battle_start`）が
`recall_interrupted` 機構の射程外にあり、帰還が「割り込みのなかった通常失敗」として
記録・クールダウンされる**ことにある。

- 判定そのもの（`camp_recall_unconfirmed` / reason `camp_arrival_unconfirmed` /
  `input_sent=True` / `interrupted=False`）はコードの定義上**すべて正しい**。
  ピッカーが閉じた（受理された）証拠が無い以上、「受理済み dispatched」にはならず、
  「送信済み・到着未確認」は正直なラベルであり誤検知ではない。
- しかし、A 送信記録（ジャーナル照合）は「パッドの A が出た」ことを証明するだけで、
  その A が目的地ピッカーを確定したことは証明しない。確定 A がピッカーに届く前に
  防御メッセージが前景を取れば、A はメッセージ送りに食われる——まさに
  `_mark_recall_interrupted` が想定した「the A advances that scene instead of
  confirming the destination」（recall.py L435-450）と同じ因果で、**1 観測分遅れただけで
  マーカーが立たない**。input_sent=True / interrupted=False はこの隙間を示す。
- その結果、戦闘に確認窓を奪われた帰還が (i) `interrupted=False` の記録になり、
  (ii) 再試行ホールドが `FORT_RETRY_TICKS` ではなく既定の長い抑止
  （`target` の無い帰還では**全野営に対する 400 tick のグローバル抑止**）になり、
  (iii) incident の observed_metric に stage/steps が無く、issue 上
  「stage: 不明 / steps: None」となる——トリアージ不能な記録形になる。
- `castle_jump_failed` は本件の原因ではない。別フロー（出撃注文の主人公城リター
  ンジャンプ）が帰還の Y ビューに前景を奪われて自壊した**被害者側の記録**であり、
  その迂回が帰還到着確認を妨げたわけではない。到着確認を奪ったのは防御戦闘である。

---

## 1. 監査ロジックの検証（issue 値との対応）

`src/docich/hanjuku/month_audit.py`:

| issue の値 | コード | 検証結果 |
|---|---|---|
| `camp_recall_unconfirmed` → outcome `unconfirmed` | `CAMP_TERMINAL`（L728-734） | 一致。`_recall_dispatch_step` の終端記録を拾う設計どおり |
| reason `camp_arrival_unconfirmed` | `_camp_reason_code`（L737-753）: `resulting_event=='arrival_unconfirmed'` → 到着未確認 | 一致。`status='arrival_unconfirmed'` は `input_sent=True` かつ受理未証明の専用ステータス（recall.py L2316-2318） |
| `input_sent: True` | `_camp_event`（L756-794）が observed_metric の `input_sent` を転記 | 一致。ジャーナル照合で `sent` が確認された（recall.py L2291-2302） |
| `interrupted: False` | 同上、`interrupted` フィールド | 一致（後述 §3。契約上正しいが情報を欠く） |
| `stage: 不明 / steps: None` | `_camp_event` が `stage`/`steps` を observed_metric から読む | **observed_metric に stage/steps が含まれない**（recall.py L2351-2354: castle/input_sent/picker_closed/interrupted/general のみ）→ issue で不明になるのは必然 |
| 理由文「帰還の本人・移動・到着を確認できず未確認として保持し、有限な操作を終了」 | recall.py L2355 の `reason` そのまま | 一致。この文言は `camp_recall_unconfirmed` 終端固有 |
| confidence: high | `_build_camp_incident`（L797-878） | 観測 288・gap 3.7 秒で高信頼は正当 |

**判定: 監査の分類・信頼度・フィールド転記は設計どおり。** 「unconfirmed」は
受理証拠（picker_closed）が無い限り正しい。ただし observed_metric が stage/steps
（と後述の「割り込み後送信」区別）を持たないため、incident 単体では「静かな未確認」と
「戦闘に確認窓を奪われた未確認」が区別できない。

## 2. 時系列の再構成（決定列 ↔ コード経路）

issue の決定列をコードのオーナー順と突き合わせる:

1. **`y_jump_open`**: 帰還フローが `y_route` に入り、`_start_y_jump`
   （`os_layer/navigation.py` L587-604）が `mem['y_jump']`（step=`'RECALL'`、
   goal=`アルマムーン`）を作って Y を送出。
2. **`castle_jump_failed`（`strategy_variant='hero_select'`,
   `why='hero_select_no_field'`）**: 帰還の Y ジャンプとは**無関係な別所有者**。
   出撃注文側の `castle_jump`（mode `hero_select`＝主人公の城へ SELECT で飛ぶ
   リターンパス）が SELECT を送ったあと、`map.py` L2003-2010 が
   `HERO_SELECT_WAIT_LIMIT` 内に 'map' フィールドフレームを確認できず失敗を記録し、
   記録理由どおり「X メニューのしろ一覧で飛び直す」フォールバック
   （`_castle_jump_y_fallback` 周辺）へ移る。
   **なぜフィールドを確認できなかったか**: `castle_jump_step` は orchestrator.py
   L2121 で `camp_recall_step`（L2175）・`world_map_step`（L2301）より**先**に
   呼ばれる。帰還の Y ビューが開くと、帰ってくるフレームは 'map' ではなく
   'world_map'/'map_target' であり、hero_select の「SELECT 後のフィールド復帰」待ちは
   それを受理できないまま年齢が尽きる。つまり**帰還の Y ジャンプが英雄 SELECT ジャンプの
   確認窓を食った**関係であり、帰還の被害ではなく出撃ジャンプ側の自己回復記録。
3. **`world_map_owners`**: `castle_jump` が失敗で `mem['castle_jump']` から除かれたあと、
   'world_map' フレームが `world_map_step`（map.py 経由 `_y_jump_step`）に届き、
   所有者旗を読んでアルマムーンへカーソルを運び `y_jump_confirm` →
   `y_jumped['RECALL']='アルマムーン'` がセットされる。
4. （抜粋に無いが）ピッカー復帰フレームで `_y_route_step`（recall.py L1092-1110）が
   `y_jumped['RECALL'] == goal` を検出 → `_finish_hero_recall`（L2253）→
   `_request_recall`（L2257-2286）: `stage='await_dispatch'`、古い
   `recall_interrupted` をクリア、`request_trace` を予約し **確定 A** を返す。
   送信は sender ジャーナルに記録され、次の dispatch 観測で `_journal_pad_inputs`
   照合（`brains/hanjuku/bot.py` L398-460）が `decision_id` + `decision_frame_sha256`
   + `at >= planned_at` を照合 → `input_sent=True`（recall.py L2295-2302、
   `camp_recall_input_sent` 記録）。
5. **`defense_observed` → `battle_start` → `battle_power`**: 目的地側（または帰路）で
   敵襲が発生し「せめこまれました」→ 防御戦（battle.py L2109-2124 で
   `defense_observed` 記録・A 送出）。戦闘系画面は帰還の所有 kind
   （orchestrator.py L902-927: `'map','map_target','text'`＋await_dispatch 時は
   `'unknown','yes_no'`）に含まれず、`mem['battle']` が立つ間 dispatch ステップは
   step を消費しない。しかし戦闘の前後（防御メッセージ・フェード・不明画面）は
   'text'/'unknown'/'yes_no' として帰還所有に入り `steps` を消費する
   （`_recall_dispatch_step` L2290）。
6. **終端**: `picker_closed` は `input_sent` 後の 'map' フレームでのみ立つ
   （L2303-2304）。防御シーンに覆われて 'map' が帰ってこないまま `steps >= 6`
   （L2305）で終端 → `accepted=False` → `input_sent=True` より
   `status='arrival_unconfirmed'`（L2317）→ `camp_recall_unconfirmed` 記録
   （L2351-2355）+ `mem['uncertain']=True` + `roster_read_due['アルマムーン']`
   を `RECALL_READ_DELAY` 後に予約（L2356-2364、#5127）→ 月次監査が incident 化。

## 3. 検証観点 2: defense_observed → battle_start は到着確認を奪ったか

**奪った。** ただし「帰還を中断した」のではなく「**受理の観測窓を奪った**」が正確な構造。

- `picker_closed` の唯一の証拠は A 後の 'map' フレーム。防御メッセージ→戦闘パネル
  →戦闘経過→戦闘終了の間 'map' は返らない。終端上限 6 step は所有フレーム
  （text/unknown/yes_no 含む）で消費され、戦闘中に未確認終端へ到達する。
- A がピッカーを確定したかどうかは観測不能になった二択:
  - (i) A が先に確定 → ピッカー消滅（受理）→ 将軍は行军中。`roster_read_due` /
    `recall_verification`（status `arrival_unconfirmed` で保持、L2331-2334）を
    通じて camp_recheck の `_arrival_request`（camp_recheck.py L866-888、
    `input_sent` かつ `arrival_unconfirmed`/`dispatched` を許容）が名簿実読みで
    到着を確認/タイムアウトさせる。
  - (ii) A が防御メッセージに食われた → ピッカーは不確定のまま前景喪失（ゲーム側は
    行軍不成立）。到着読みは「まだ野営にいる」を読み、後続の到着タイムアウト・
    ゴール失敗ペア記録（L2365-2387）・再帰還で収束する。
- `interrupted` マーカーが立たなかった理由: `_mark_recall_interrupted`（L434-450）は
  `stage=='await_dispatch' and not input_sent` のときだけ `_flow['recall_interrupted']`
  を書く。**設計上「A がまだ送られていない間の割り込み」専用**であり、本件は A 送信が
  ジャーナル確認済みだったため立たない。テストもこの契約を固定している
  （`tests/test_hanjuku_recall_interrupt.py` L22-28: `input_sent` 済みはマークしない）。
- **ここが残存ギャップ**: マーカーの存在意義は「防御メッセージ等が確認 A を食う」
  という同一の疑念（テスト L37-46 の実例 g1139 カルーン）に対する救済で、それが
  `input_sent` の前後で不連続になる必然性はない。`input_sent` は `at` タイムスタンプで
  「送信された」ことを証明するだけで、A が届いた画面（`frame_sha256`≠
  `decision_frame_sha256` のケースあり）は照合対象外。つまり送信〜受理観測の窓に
  割り込まれた本件は**同じ被害なのに `interrupted=False`・長いホールド側**に落ちる。

### `interrupted=False` が招く副作用

`_arm_recall_skip`（L268-299）の `base` は `fort`/`boss`/`interrupted` のときだけ
`FORT_RETRY_TICKS`。`interrupted=False` の通常終端は `base=None` → 既定
`RECALL_SKIP_TICKS`（400）。さらに `state['target']`（野営セル座標）を持たない帰還
（英雄帰還など）は `_remember_recall_target` が `recall_skip_target` を立てられず、
`_filter_single_recall_skip` L335-338 の fail-closed 分岐により
**400 tick のグローバル抑止（全可視野営の帰還を封じる）**になる。
コメント L285-289 が「割り込まれた dispatch は野営座標を持たないことがあるため
グローバルでも短く保つ」と明言している通り、これは割り込みケース専用に設計された
救済であり、**送信後割り込みが `interrupted` に入らないためその救済から漏れる**のは
設計意図との不整合である。本件が英雄帰還か野営帰還かは issue 値からは判別できず
（`general` は fort のみ転記）、野営帰還なら抑止は該当野営にスコープされるため
副作用は軽いが、英雄帰還なら別野営まで 400 tick 封じる。

### issue の `stage: 不明 / steps: None` の説明

`camp_recall_unconfirmed` の observed_metric（L2351-2354）に stage/steps が無いため、
`_camp_event` はそれらを欠いたまま incident 化する。結果として「6 step 窓のどこで
尽きたか」「await_dispatch の何段階だったか」が incident 単体では読めない。

## 4. 検証観点 1: castle_jump_failed の経路と帰還への影響

- 経路: 出撃注文のリターン（hero_select モード）が `SELECT` で主人公の城へ飛ぶ
  → フィールドフレームが `HERO_SELECT_WAIT_LIMIT` 内に返らない →
  `castle_jump_failed`（map.py L2003-2010、`why='hero_select_no_field'`）→
  「X メニューのしろ一覧で飛び直す」フォールバック。
- 「SELECT 後にフィールドを確認できない」直接の原因は、確認窓の中に帰還の
  Y ビュー（'world_map'/'map_target' フレーム）が入ったこと。dispatch 順
  （orchestrator L2103-2121: 飛行中帰還の map/map_target → general_jump →
  castle_jump → … → camp_recall L2175 → world_map L2301）では `castle_jump` は
  帰還より先にフレームを消費するが、消費したフレームが 'map' でなければ
  hero_select の待ち条件を満たせず自壊する。
- **帰還到着確認への影響: なし。** `castle_jump_failed` は `y_jump_open` と
  `world_map_owners` の間に挟まっており、効果は「帰還の Y ジャンプの steering が
  一時的に飢餓した」ことのみ。`mem['castle_jump']` が失敗で外れた後に
  `world_map_owners`→確定→`await_dispatch` まで帰還は完走している。
  帰還を「未確認」にしたのはその後の防御戦闘であり、castle_jump の迂回経路
  （X メニューしろ一覧）は帰還の受理証拠とは無関係（帰還の受理は目的地ピッカーの
  開閉で測り、X メニュー遷移は測らない）。

## 5. 検証観点 3: 既知判定との関係

| 既知ケース | シグネチャ | 本件との関係 |
|---|---|---|
| **#5147**（判定 (a) 偽陽性、修正未着手） | `picker_fallback`→`await_dispatch` 直後、stale な 'map' フレームで `_mark_recall_interrupted` が発火 → 真の到着が `arrival_unconfirmed` へ格下げ | 方向が**逆**。本件は `interrupted=False` かつ「割り込み自体は本当にあった」。#5147 は「無い割り込みを立てた」、本件は「ある割り込みを立てられない」という、同じ `input_sent` ゲート（L434-450）の表裏。#5147 の修正（stale フレーム対策）が入っても本件は変わらない |
| **#5252 / #5154**（fort 滞留家族） | `stage='fort_status'`・`steps=91`・`camp_recall_aborted` | 別決定・別ステージ。本件は `camp_recall_unconfirmed`（await_dispatch 終端）であり、fort ステータス読みの滞留とは無関係 |
| **#4978/v1350**（dispatched 分離） | `input_sent`+`picker_closed` → `camp_recall_dispatched` に改善 | 本件は `picker_closed` 不成立のためその救済対象外。設計上「受理未観測は unconfirmed」が正しい |
| **#5127/v1387**（`roster_read_due`） | `arrival_unconfirmed` でも `input_sent` あれば目的地名簿を有界読み | **本ラン（v1414）に導入済み**。帰還先アルマムーンの名簿再読みは既に予約されており、到着/未到着の最終判定は設計上カバーされている |
| **#5134**（battle_return リセット） | `to_camp`/`menu` ステージの戦闘復帰時に観測予算をリセット | 本件は `await_dispatch` であり対象外（同じ「戦闘×帰還」族だが別ステージの修正） |

## 6. 検証観点 4: 現行 main での再発可能性と既存修正のカバー

- **再発しうる。** 本件のシグネチャ（`input_sent=True` → 防御/イベント割り込み →
  `picker_closed` 不成立 → `arrival_unconfirmed`）を生む経路は HEAD（v1417,
  `38cf843b0`）時点で不変:
  - `_mark_recall_interrupted` の `input_sent` ゲート（L434-450）
  - dispatch 終端の 6 step 有界窓（L2305）と所有 kind（orchestrator L921-927）
  - `interrupted=False` 時の `base=None` → 400 tick 抑止（L2336-2338, L282-284）
- v1414 → v1417 の差分（#5237）は `y_route` の `きかん` フォールバック撤去と
  `hero_focus` の goal 未解決リトライ化であり、**dispatch 終端・割り込みマーカー・
  ホールド武装には無変更**。つまり現行 main でも同じ incident が起票されうる。
- 既存修正でカバー済みの部分:
  - 受理済みの誤判定（#4978）
  - 受理後の名簿実読み（#5054）と送信済み未確認への名簿予約（#5127）
  - `dest`/`menu`/`to_camp` の戦闘復帰リセット（#5134）
  - `きかん` 経路の廃止（#5105/#5108、v1417 で完全撤去）
- **カバーされていない部分**: `input_sent` 済み `await_dispatch` への後着割り込み。
  `recall_interrupted` は立たず、incident は `interrupted=False` で起票され、
  （target 無しなら）全野営 400 tick 抑止が発火し、incident には stage/steps も残らない。

## 7. 修正案

`interrupted` の意味（「A 未送信の割り込み」）は既存テストが固定しているため維持し、
**送信後割り込みを別フラグとして観測する**:

1. `await_dispatch` 中に戦闘・イベント前景が現れたとき、`input_sent` の有無に関わらず
   マーカーを立てる。hook は `observe_jump_foreground`
   （`os_layer/navigation.py` L243-260、全画面で呼ばれ、既に注文側 Y ジャンプへ
   `foreground_interrupted` を立てている）または `battle.py` の戦闘終了処理
   （`forget_target_placement` 呼出部）に `_mark_recall_post_input_interrupted`
   相当を追加: `stage=='await_dispatch'` なら
   `_flow['recall_post_input_interrupted']=tick`（`input_sent` 前なら従来どおり
   `recall_interrupted` で足りる）。
2. `_recall_dispatch_step` 終端で `post_input_interrupted = interrupted or
   _flow(mem).pop('recall_post_input_interrupted', None)`:
   - `accepted`/`status` の判定は**変更しない**（受理未観測なら
     `arrival_unconfirmed` のまま。送信後割り込みでも受理を楽観視しない）。
   - `_arm_recall_skip` の `base` を `interrupted or post_input_interrupted` で
     `FORT_RETRY_TICKS` にする。防御に確認 A を食われた可能性のある帰還を、
     割り込み前ケースと同じ短いホールドで再試行させ、target 無し帰還の
     400 tick グローバル抑止を防ぐ。
   - `camp_recall_unconfirmed` の observed_metric に `stage`・`steps`・
     `post_input_interrupted` を加え、incident が「戦闘に窓を奪われた未確認」を
     自明に示せるようにする。
3. （任意・別件）`observe_jump_foreground` が注文側の `foreground_interrupted` を
   立てるのと同じ場所で、帰還の `mem['y_jump']`（step=='RECALL'）へも同様の
   マーキングを検討。本件の `castle_jump_failed` のように前景所有者の競合で
   ビューが途中で飢餓する場合の可視化に使える。

## 8. 未確認点（判定には影響しないが残件）

- 当該 A がピッカー確定前に防御メッセージに食われたか、先に確定して受理済みだったかは、
  コードと issue 提示値だけでは原理上判定不能（`roster_read_due` の名簿実読み結果＝
  到着/未到着が本質的な判定子）。ラン成果物 `hanjuku_decisions.jsonl` /
  `hanjuku_events.jsonl`（run `g1342-1fe0734c`、occurrence `37f5e373b436e1f2019b`）
  があれば、`camp_recall_input_sent` の直後のフレーム kind 列で一意に決まる。
- 本件の帰還が英雄帰還（`hero_focus`→`y_route`）か野営帰還（キャンプ検出→
  `y_route`/ピッカー経路）かは incident 値から判別できない（`general` は fort のみ
  転記）。後者なら抑止は該当野営スコープで副作用は軽微、前者ならグローバル抑止の
  副作用が本線となる（§3）。
- pytest 未実行。`tests/test_hanjuku_recall_interrupt.py` 等の契約は読解のみで確認。
- `handoff.md` 正本は参照未設定のため未読（AGENTS.md §1 に従い通常開発で進行）。
