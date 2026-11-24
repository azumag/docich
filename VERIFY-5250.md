# VERIFY-5250 — [半熟英雄] キャンプ帰還未確認: ch2 アルマムーン

対象: docich#5250（監査自動起票）/ ラン `g1342-1fe0734c` / generation 1342 /
`hanjuku-chart-v1419-hero-move-backoff` / 月 `ch2 2-4`
判定: `camp_recall_unconfirmed` (confidence: high)、reason_code
`camp_arrival_unconfirmed`（シグネチャ「帰還到着を確認できない」）
camp_event: 将軍 不明 / 野営 None / 帰還先 アルマムーン / input_sent False
fingerprint `4e88973e821e6304`

調査基盤: worktree `~/work/docich-5250`（branch `verify/5250`、HEAD `7b6539872`
＝ v1431）。read-only 調査。コードと issue 提示値、兄弟検証 VERIFY-5233 /
VERIFY-5248（`~/work/docich-5233`、`~/work/docich-5248`）を根拠とする。
本番 VM SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl`
/ frames は VM 側にあり、本検証では直接参照できない（§5 の残件）。
`handoff.md` 正本参照は未設定のため未読と記録する（AGENTS.md §1）。
pytest / gh は未使用。判定はコード読解と issue 提示値による。

バージョン差: `git diff b3a54a4ad..HEAD`（v1419 → v1431）で
`flows/recall.py`・`brains/hanjuku/bot.py`・`hanjuku_run.py`・
`flows/camp_recheck.py` は無変更。`month_audit.py`・`map.py`・
`post_month_status.py` の差分は築城・出撃系のみでキャンプ帰還 dispatch
終端・監査分類に触れない。**現行 HEAD の読解はラン時点（v1419）の契約に
そのまま適用できる。**

---

## 結論

**判定（`input_sent=False` → `dispatch_unconfirmed` → 未確認として保持し
有限な操作を終了、high で起票）は設計どおりであり正しい。**
ただし **#5248 と同族の記録形欠陥** を持ち、#5250 では帰還先が束縛された
ケースとして新たな副作用（`camp_recall_blocked` への計上）を確認した。

1. **未確認保持は契約どおり**: 終端は `_recall_dispatch_step`
   （`flows/recall.py` L2289-2388）。`stage='await_dispatch'` 中に送信
   receipt（`_journal['_recall_inputs']`、sender journal との照合）が
   6 観測以内に証明されなければ `status='dispatch_unconfirmed'`
   （L2317-2318）となり、`camp_recall_unconfirmed` を記録して
   `mem['uncertain']=True`・`_arm_recall_skip` で有限終了する
   （L2331-2355）。設計文書の契約そのもの
   （`docs/operations/hanjuku-camp-recall-20261001.md` L16: 「送信待ちは
   最大6観測…dispatch_unconfirmed又はarrival_unconfirmedとして通常操作へ
   戻る」）で、テストも同じ契約を固定している
   （`tests/test_hanjuku_camp_receipts.py` L167-175:
   `test_missing_dispatch_receipt_is_bounded_and_never_retries_a`）。
   「送れた」と楽観せず未確認のまま残すのは、この契約では正しい方向の
   失敗である。
2. **起票の是非も契約どおり**: `CAMP_TERMINAL`
   （`month_audit.py` L728-733）が `camp_recall_unconfirmed` を拾い、
   `_build_camp_incident` が `auto_file=True`（L877）で high 起票する。
   「決定した入力が 6 観測以内に journal 証明されない」は実在の異常
   シグナルであり、監査が拾う設計は正当。
3. **ただし記録形は既知の欠陥**（#5248 と同族。詳細は §3）:
   実 status は `dispatch_unconfirmed`（**送信証明の未取得**＝送信側の
   問題）なのに、`_camp_reason_code`（L737-753）が理由文中の「到着」に
   一致して `camp_arrival_unconfirmed`（「帰還**到着**を確認できない」）
   と署名する。issue が失敗段階を誤記述し、stage/steps/status/野営を
   本文へ出さないため issue 単体ではトリアージ不能。
4. **#5250 固有の新たな面**: 帰還先が束縛されている（アルマムーン）ため
   - `roster_read_due['アルマムーン']` は**要求時点で既に予約済み**
     （`_request_recall` L2279-2280）。帰還先名簿読みプローブという
     到着証拠経路は設計上動いていた（#5248 の goal=None では予約すら
     なかった点と異なる）。
   - `camp_recall_blocked['アルマムーン'] += 1`（L2320-2330）。
     送信側（城非依存）の失敗を「その城が受け取れなかった」学習として
     章単位で計上する。receipt 鎖の失敗が原因なら健全な城への誤帰属に
     なりうる（§4）。
5. 根本原因（A が agent を物理的に出なかったか、出たが journal 束縛に
   失敗したか）の一意確定にはラン成果物 `hanjuku_events.jsonl` が必要で、
   read-only 検証の範囲外。ただし §3 の記録形・§4 の帰属欠陥は原因行に
   依存せずコード上で証明できる。

---

## 1. 監査ロジックの検証（issue 値との対応）

| issue の値 | コード | 検証結果 |
|---|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py` L2351-2355（`_recall_dispatch_step` 終端の唯一の記録点。呼出は `await_dispatch` のみ: L1497 / `map.py` L596） | 一致 |
| kind `camp_recall_unconfirmed` / outcome `unconfirmed` | `CAMP_TERMINAL`（`month_audit.py` L728-733） | 一致 |
| reason_code `camp_arrival_unconfirmed` | `_camp_reason_code`（L737-753）。`resulting_event='dispatch_unconfirmed'` には一致せず、理由文「…移動・**到着**を確認できず…」（L2355、dispatch/arrival 共通文）の「到着」で決まる | **誤署名**（実 status は送信側の失敗。§3-1） |
| `input_sent: False` | `_camp_event`（L790）が observed_metric の `input_sent` を転記。終端分岐で `input_sent` が偽 → `status='dispatch_unconfirmed'`（L2317-2318） | 一致。receipt 未証明の終端 |
| `interrupted: False` | 終端が `_flow['recall_interrupted']` を pop して見つからなかった（L2306-2307）。マーカーは戦闘系画面遷移（L1214-1216）・`mem['battle']`（L1231-1232）・scene abandon（`orchestrator.py` L957）でしか立たない | 機構上正しい（ただし text 系割り込みは刻印されない既知の穴、§3-6） |
| 将軍: 不明 | 終端 metric の `general` は `state.get('general') if state.get('fort') else None`（L2354）→ fort 以外の帰還では構造的に常に None | 構造的必然（英雄帰還でも NAME が記録されない非対称: L2298 との不整合、§3-5） |
| 野営: None | 終端 metric に `camp`/`target` キーが無い（L2351-2354）→ `_camp_event` L773-775 は常に None | 構造的必然 |
| 帰還先 アルマムーン | `state['goal']` が転記される。`HOME_CASTLES[2]='アルマムーン'`（`hanjuku_chart.py` L443-450） | goal 束縛の要求であることを証明（§2） |
| 理由文 | `recall.py` L2355 の `reason` そのまま | 一致。この文言は unconfirmed 終端固有 |
| stage: 不明 / steps: None | 終端 metric に stage/steps が無い → `_camp_event` L788-789 が欠損のまま転記 | 構造的必然（§3-2） |
| confidence: high / auto_file | `_build_camp_incident`（L835, L877） | 契約どおり |
| fingerprint `4e88973e821e6304` | `_fingerprint(chapter, kind, [kind])`＝章+kind のみ（L2993-2995） | 章別1issue集約の設計どおり |

## 2. 経路の特定 — goal 束縛が示す要求種別

`input_sent=False` かつ `goal='アルマムーン'` の要求は、`_request_recall`
（L2257-2286）に**帰還先を束縛して**入った経路に限られる:

- `_finish_hero_recall`（L2253-2254: hero/fort の屋根・Yジャンプ到達確定）
- 帰還ピッカー確定（`map.py` L743 / L858 / L1011 — いずれも
  `[pad('a'), wait700, pad('a')]` の **2-A 要求**、`a_inputs=2`）
- picker fallback / 屋根確定（L1997 / L2030、goal 解決済みのもの）
- ボス再出撃（L2064 / L2096、goal はボス城 — ch2 のボス城は 'ボス' であり
  アルマムーンではないため**除外**）
- 崖離脱系の `goal=None` 経路（L776 / L1061 / L1997 / L2025 の goal 未解決
  枝）は castle が記録されるため**除外** — #5248 の主仮説（escape_move の
  goal-less A）は本件では成立しない。

つまり本件は「**アルマムーンへの帰還を実選択して予定した単発または2連の A
が、ジャーナル照合で一度も証明されなかった**」終端である。

`input_sent` の証明鎖（v1419 時点と同一、§0 の diff で確認）:

1. `persist()` が `await_dispatch` 入りで `request_trace`
   （decision_id・frame_sha256・planned_at・run identity）を刻印
   （`brains/hanjuku/bot.py` L160-161）。
2. `adapter.act` が物理送信し `hanjuku_run.action_sent` が `input_sent` 行を
   `hanjuku_events.jsonl` へ書く（`adapters/retroarch.py` L459-461 →
   `hanjuku_run.py` L469-473）。
3. 次観測で `recall_input_receipts` が journal 末尾 64KiB を走査し
   decision_id・実画面 SHA・`planned_at <= at`・`buttons==['a']`・
   `hold_ms==100` を照合（`bot.py` L409-460 → `_journal['_recall_inputs']`
   格納は `orchestrator.py` L1645）。
4. 終端待ちは 6 観測（L2305）。

「他に本人・移動・到着を確認する手段がなかったか」への答え:

- **本人**: この終端では `general` は fort のみ転記（§1）。request 記録
  `camp_recall_requested` の observed_metric（`'camp': state['target']`、
  `'hero_intended'`、`**fields`＝boss/fort/escape_move 等、L2281-2285）
  には識別情報があるが、incident は終端 metric しか写さないため
  issue 単体では判別不能（記録形欠陥、§3-2）。
- **移動**: フィールド上に帰還ユニットのマーカーは存在せず、設計上
  観測不能（`hanjuku-camp-recall-20261001.md` L16「実将軍本人、指示受理、
  移動、入城は未確認のまま」が明示的契約）。
- **到着**: `roster_read_due['アルマムーン']` は要求時点で予約済み
  （L2279-2280。`RECALL_READ_DELAY=180` tick 後）。corridor probe
  （`strategy/corridor.py` `_due_probe` L791-826）が帰還先の名簿を読み
  `world.garrison` を更新する経路は設計上動いていた。ただし読み結果は
  garrison 更新に留まり、匿名帰還では「この帰還の到着」へ照合する者が
  いない（blocked 解除・incident 訂正へ反映されない）。
  さらに `input_sent=True` を要件とする本人名簿再読み
  （`camp_recheck.py` `_arrival_request` L866-888: `input_sent` かつ
  status が `arrival_unconfirmed`/`dispatched` のみ受理）は
  dispatch_unconfirmed を**対象外**にする設計で、「送れたかもしれないが
  証明できない」ケースの本人読みは意図的に行わない。
- **named 帰還の救済**: この帰還が月次処理の named job（`post_month_status`
  の `wait_recall`、L2190-2239）なら、`_recall_was_dispatched`（L1316-
  1331）が `input_sent` 偽で偽を返し、`RECALL_TASK_RETRIES=2`（L99）以内
  または `RECALL_INTERRUPT_GRACE=240` tick 内の割り込みなら同月内に
  job を再キューする（`post_month_recall_requeued`）。設計上の回復経路は
  存在する（匿名の野営帰還なら対象外で、400 tick 抑止後の野営再検出→
  再帰還が実質の回復経路）。

**判定「未確認として保持」は正しい**: journal が送信を証明しない以上、
受理済み `dispatched` にする根拠がなく（`picker_closed` も未観測）、
到着確認経路は要求時に既に武装されていた。終端が「送信を証明できなかった」
ことをそのまま保持したのは契約どおりの正直な記録であり、誤検知ではない。

## 3. 記録形・帰属の欠陥（実欠陥 — #5248 と同族＋新たな面）

1. **失敗段階の誤署名**: `_camp_reason_code`（L749-750）は
   `resulting_event='dispatch_unconfirmed'` を持つ終端を
   `camp_arrival_unconfirmed`（「帰還到着を確認できない」）と署名する。
   共通の理由文（L2355）中の「到着」への部分一致が原因で、
   **送信側の失敗が到着失敗として issue に出る**。#5248 §4-1 と同一の
   欠陥が本件でも実発火した。
2. **トリアージ不能な記録形**: 終端 observed_metric（L2351-2354）は
   castle/input_sent/picker_closed/interrupted/general のみで、
   `stage`・`steps`・`status`（= resulting_event）・`target`（野営）・
   期待 `a_inputs`（1 か 2 か — 2-A picker 経路では片方だけ届いた
   ケースが切り分けられる）・実受領数を含まない。`_camp_event` は
   metric しか読まないため issue の「stage: 不明 / steps: None /
   野営: None」は全件同じ形になる（#5233 §1・#5248 §1 と同一）。
3. **receipt 境界の曖昧性**: `input_sent=False` は「本当に未送信」
   （adapter 側の terminal hold `retroarch.py` L452-453、fence 喪失、
   `require_input_open` 閉塞、decide→act 間の agent 再起動/終了で
   アクション列が喪失）と「送信済みだが receipt 不整合」
   （`action_sent` の `trace={}` フォールバック `hanjuku_run.py` L462-468、
   run identity ドリフト `bot.py` L398-406、journal 欠落→`None`→`or {}`
   で0件扱い `recall.py` L2291、`_act` 成功後に `action_sent` が例外
   `retroarch.py` L459-461）を記録上区別できない（#5248 §4-4 と同一）。
   2-A 要求（`a_inputs=2`）の場合、片方だけ journal に残った「1件一致」
   でも `input_sent` は立たず、部分一致の証拠は incident に残らない。
4. **（#5250 固有）`camp_recall_blocked` への計上**: goal が束縛された
   dispatch_unconfirmed は `blocked[goal] = min(3, +1)`（L2320-2330）を
   発火する。契約意図は「その城がこの野営の帰還を受け取れなかった」学習
   （オーナー報告 g1190、tests/test_hanjuku_camp_recall_blocked.py /
   test_hanjuku_visible_skip_blocked.py が固定）で、2 回で
   `_recall_goal` ランキング（`orders.py` L2661-2669）と
   `_visible_own_castle`（`recall.py` L499-503, L537-549）から降格、
   lifetime は章単位（`regions.py` L40）。**だが `input_sent=False` は
   送信側（城の選択に依存しない）の失敗であり、receipt 鎖の失敗が原因
   なら健全な城へ誤帰属される**。解除は `recall_in_castle_observed`
   （`camp_recheck.py` L554-561）が named verification
   （`verification['general']==actor`）に限るため、**匿名帰還の誤計上は
   章末まで誰も戻さない**。
5. **将軍名の非対称**: `camp_recall_input_sent` は
   `state.get('general') or (NAME if state.get('hero') else None)` で本人を
   記録する（L2298）のに、unconfirmed 終端は fort のみ（L2354）。
   英雄帰還でも「将軍: 不明」になり、同一帰還の前後記録で本人情報が
   不連続になる。
6. **（従来どおり）text 系割り込みの刻印欠落**: L1466 の
   `_mark_recall_interrupted` 呼び出しは外側条件
   （L1460 `stage != 'await_dispatch'`）と内側条件
   （L448 `== 'await_dispatch'`）が矛盾し恒常 no-op。本件では
   `interrupted=False` は「戦闘系割り込みが無かった」ことの証明に留まり、
   text 系の前景奪取は保証しない（#5248 §3 と同一）。

これらは「未確認と判定したこと」の誤りではなく、**起票される記録が
失敗段階を誤署名し、再現調査に必要な証跡を残さず、送信側失敗を城属性へ
帰属する**という、#5248 が指摘した欠陥群の goal 束縛版での再確認である。

## 4. #5233 / #5248 との異同（検証観点 2）

| 項目 | #5233 | #5248 | **#5250** |
|---|---|---|---|
| ラン | g1342-1fe0734c | g1190-ch1v9 | **g1342-1fe0734c** |
| 章・月 | ch1 1-6 | ch9 11-9 | **ch2 2-4** |
| chart | v1414 | v1419 | **v1419** |
| `input_sent` | True | False | **False** |
| 実 status | `arrival_unconfirmed` | `dispatch_unconfirmed` | **`dispatch_unconfirmed`** |
| 失敗段階 | 送信**後**: 戦闘割り込みが `recall_interrupted`（input_sent ゲート L448-449）の射程外で interrupted=False に落ちた | 送信**前**: receipt 鎖が切れた。goal=None で escape_move 仮説が最有力 | **送信前**: receipt 鎖が切れた。**goal=アルマムーン束縛**で escape_move を除外 |
| 帰還先 | アルマムーン | 不明（None） | **アルマムーン** |
| 副作用 | interrupted=False → `base=None` → 400 tick 抑止（target 無しなら全野営グローバル抑止） | `camp_recall_blocked` 非発火（goal None でガード L2320）・`roster_read_due` 未予約 | **`blocked['アルマムーン'] += 1` 発火**・`roster_read_due` 予約済み |
| fingerprint | （章1+kind） | `83845131b227e2a9`（章9+kind） | `4e88973e821e6304`（章2+kind） |

- **#5233 との関係**: **別機構・別段階。** 同一ラン g1342 だが別章。
  #5233 は「送信は証明されたが受理観測を戦闘に奪われた」post-send の
  分類ギャップ、本件は「送信証明そのものが得られなかった」pre-send の
  receipt 喪失。`input_sent` ゲートを挟んだ表裏の関係であり、#5233 の
  修正案（post-send 割り込みマーカー）は本件の契機（receipt 不在）を
  救えない。**重複ではない。**
- **#5248 との関係**: **同じ `dispatch_unconfirmed` 家族だが別実例。**
  別ラン・別章・別 fingerprint。#5248 の最有力仮説（escape_move の
  goal-less A が帰還 incident に誤起票）は **goal 束縛により本件では
  除外**され、逆に本件は goal 束縛時にしか起きない
  `camp_recall_blocked` 計上と `roster_read_due` 予約という#5248に
  無かった挙動面を示す。根本問題（receipt 境界・記録形）は共有するため
  **重複ではなく、同一根本問題の別発現としてリンクすべき**。

## 5. 残件（read-only では確定不能、判定には影響しない）

一意確定にはラン成果物が必要:

- `hanjuku_decisions.jsonl`: `camp_recall_requested` の observed_metric
  （`'camp'`/`'hero_intended'`/boss/fort/escape_move/`a_inputs`）で要求種別
  （英雄・野営・砦・可視移動・picker 経路）と期待入力数を判別可能。
- `hanjuku_events.jsonl`: 要求 decision_id 前後の `input_sent` 行で
  - 行なし → 真に未送信（adapter hold / gate / agent 再起動）。
  - 行あり `decision_id=null` → `action_sent` の `trace={}` フォールバック
    （送達済みだが証明不能、実欠陥の発火実例）。
  - 行あり別 decision_id → 別決定への誤束縛。
- `hanjuku_decisions.jsonl` / `hanjuku_run.json`: `roster_read_due`
  ['アルマムーン'] プローブの実行有無と garrison 変化で、真の到着/未到着
  （＝ A が実際に受理されていたか）を判定可能。

## 6. 修正候補（実装はしない、検証の結論として）

1. 終端 `observed_metric` に `stage`・`steps`・`target`（野営）・
   `status`（= resulting_event）・期待 `a_inputs`・実受領数を含める
   （`recall.py` L2351-2354）。監査の `_camp_event` は既に stage/steps を
   読む形がある（`month_audit.py` L788-789）ので、issue の
   「stage: 不明 / steps: None / 野営: None」を解消できる。issue 本文の
   キャンプ帰還節に `resulting_event` も出力（L3763-3772）。
2. `_camp_reason_code` に `dispatch_unconfirmed` 専用コードを分ける
   （`resulting_event` を先に見て理由文の「到着」部分一致に依存しない、
   `month_audit.py` L737-753）。送信側失敗と到着失敗を issue 署名で
   区別可能にする。
3. `action_sent` が `trace={}` で journal 行を書く場合、行に
   `trace_missing: true` を残す（`hanjuku_run.py` L462-473、#5248 の
   案5と同一）。「送達済みだが dispatch_unconfirmed になる」経路を
   ラン成果物から検出可能にする。
4. `camp_recall_blocked` 計上の見直し（#5250 由来の新項目）:
   `input_sent` 未証明は城固有の証拠ではないため、少なくとも
   - roster probe で帰還先 garrison に当該将軍（または新規在城）を観測
     した場合に `blocked[goal]` を解除する経路を**匿名帰還にも**持つ、
   - または計上時に status/理由を保持して誤計上の事後説明を可能にする、
   - または dispatch_unconfirmed を blocked の根拠から外し、
     「受理証拠のある拒否」（例: picker 残留観測）に限定するかを設計判断
     とする。現状の匿名帰還では誤計上が章末まで解除不能。
5. 終端 metric の `general` を `camp_recall_input_sent` と同じ束縛
   （`state.get('general') or (NAME if state.get('hero') else None)`、
   L2298 対 L2354）に揃える。英雄帰還の本人が incident で不明にならない。
6. （#5248 から引き継ぎ）`recall.py` L1466 のデッド呼び出しを除去するか、
   await_dispatch 中の text 系割り込みにも刻印を広げる設計を別途検討。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py` L2351（終端、唯一の記録点） |
| input_sent False | `recall.py` L2291-2318、receipt 未一致で `status='dispatch_unconfirmed'` |
| interrupted False | `recall.py` L2306-2307（`recall_interrupted` マーカー未検出） |
| 帰還先 アルマムーン | `state['goal']` 束縛の要求（`_finish_hero_recall` / picker 確定系）、`HOME_CASTLES[2]` |
| stage 不明 / steps None / 野営 None | 終端 metric に未含有（`recall.py` L2351-2354）→ `month_audit.py` L788-789 → L3770-3771 |
| 将軍 不明 | 終端 metric の general が fort 限定（`recall.py` L2354） |
| reason_code `camp_arrival_unconfirmed` | `_camp_reason_code` L749-750 が理由文の「到着」に一致（dispatch でも同じ署名になる） |
| confidence: high / auto_file | `_build_camp_incident` L835・L877 |
| `camp_recall_input_sent` 欠落 | `recall.py` L2295-2302、receipt 一致時のみ記録 |
| （副作用）`blocked['アルマムーン'] += 1` | `recall.py` L2320-2330（dispatch_unconfirmed かつ goal 束縛） |
| （副作用）400 tick 再検出抑止 | `_arm_recall_skip` → `_filter_single_recall_skip`（L268-345）。target 束縛なら対象野営のみ、無しならグローバル抑止 |
| （緩和）`roster_read_due['アルマムーン']` 予約 | `_request_recall` L2279-2280（要求時点で実行、送信証明の成否に無関係） |
