# VERIFY-5248 — [半熟英雄] キャンプ帰還未確認: input_sent=False / stage・steps 不記録

対象: docich#5248（監査自動起票）/ ラン `g1190-ch1v9` /
`hanjuku-chart-v1419-hero-move-backoff` / 月 `ch9 11-9`（観測 768 回 / 192.4 秒 /
max gap 0.8 秒）
判定: `camp_recall_unconfirmed` (confidence: high)、シグネチャ
「キャンプ帰還未確認 / 帰還到着を確認できない」
camp_event: 将軍 不明 / 野営 None / 帰還先 不明 / stage 不明 / steps None /
input_sent False / interrupted False
fingerprint `83845131b227e2a9` / occurrence `d9deb11dab6af1a67549`

調査基盤: worktree `~/work/docich-5248`（ブランチ `verify/5248-camp-recall`）。
read-only 調査。コード・issue 記載事実のみ。本番 VM SSH・GitHub アクセスなし。
handoff 正本はこの環境で参照経路を確認できず未読（AGENTS.md §1 に従い現行コードを
根拠とする）。

---

## 結論

**(a) 実欠陥。** ただし欠陥は二層に分けて確定する。

1. **発火そのものは設計どおり**: `camp_recall_unconfirmed` の終端は
   `await_dispatch` 中に送信 receipt が 6 観測以内に証明できなかった
   `dispatch_unconfirmed` 経路であり、未送信の検出としては契約どおりに動いた。
   監査が high で起票した判断は正当（異常シグナルとして実在する）。
2. **記録形と分類は実欠陥**: 本件は「帰還先なし（goal=None）の要求」を
   「帰還**到着**を確認できない」と署名して起票しており、issue 本文が
   stage/steps/resulting_event/野営を欠いて失敗段階を判別できない。
   #5233 が指摘した「トリアージ不能な記録形」と同族かつ新たな面を持つ。
   さらに `input_sent=False` は「本当に送られなかった」と
   「送ったが receipt が束縛できなかった」の両方を含み、記録だけでは
   区別不能であり、後者なら実際に受理された帰還を未確認に降格する
   誤分類になりうる。

root cause（入力が agent を出なかったのか、出たが receipt 化に失敗したのか）の
一意確定にはラン成果物 `hanjuku_events.jsonl` が必要で、read-only の
リポジトリ検証の範囲外。ただし §3 の記録形・分類欠陥は原因行に依存せず
コード上で証明できるため、結論は (a) で確定する。

---

## 1. `camp_recall_unconfirmed` が出る経路の特定（検証観点 1）

`camp_recall_unconfirmed` の記録点は全リポジトリで
`src/docich/hanjuku/flows/recall.py:2351` の 1 か所だけであり、呼び出し元は
`stage == 'await_dispatch'` の場合のみである:

- `recall.py:1496-1497`（`_camp_recall_step` 経由、map/text/unknown/yes_no 系画面）
- `map.py:575-576`（world_map 系画面の recall 分岐）

したがって終端記録が出た時点で、フローは**必ず** `_request_recall`
（`recall.py:2257-2286`）を通って `camp_recall_requested`
（`resulting_event='planned_not_yet_sent'`、L2281-2285）まで到達している。
「帰還のどの段階で状態が失われたか」への答え:

- 状態は失われていない。`stage='await_dispatch'`・`steps>=6` は
  `flow.recall_verification`（`recall.py:2331-2334`）に全量保持され、
  incident レコードの `flow` にも入る（`month_audit.py:849` +
  `_summarize_flow` L456-467 が status/stage/steps を拾う）。
- **issue 本文だけ**がそれを見せない。終端の `observed_metric`
  （`recall.py:2351-2354`）は `castle`・`input_sent`・`picker_closed`・
  `interrupted`・`general` のみで、`stage`/`steps`/`target`（野営）/
  `resulting_event` を含まない。監査側 `_camp_event`
  （`month_audit.py:786-791`）は `observed_metric` しか読まないため、
  本文の「stage: 不明 / steps: None / 野営: None」は**常にこの形になる**
  （`camp` キー自体が metric に存在しないので野営は全件 None）。

### `input_sent=False` の意味

`_recall_dispatch_step`（`recall.py:2289-2388`）の終端分岐:

```
accepted           = input_sent and picker_closed and not interrupted → camp_recall_dispatched
arrival_unconfirmed = input_sent が真で受理も未証明                  → camp_recall_unconfirmed
dispatch_unconfirmed = input_sent が偽                               → camp_recall_unconfirmed
```

`input_sent` は `_journal['_recall_inputs']`（送信 journal の receipt）が
`request_trace` と一致し `a_inputs` 個以上確認できたときだけ立つ
（`recall.py:2291-2301`）。receipt の生成鎖は:

1. `persist()` が `await_dispatch` 入り時に `request_trace`
   （decision_id・frame_sha256・planned_at・run identity）を刻印
   （`brains/hanjuku/bot.py:157-161`）。
2. agent が `adapter.act` で物理送信し、その後
   `hanjuku_run.action_sent` が `input_sent` 行を `hanjuku_events.jsonl` へ
   書く（`adapters/retroarch.py:442-461` → `hanjuku_run.py:452-473`）。
   行の `decision_id`/`decision_frame_sha256` は送信時点の
   `hanjuku_bot.json` の `decision_trace` から取られる。
3. 次観測で `recall_input_receipts` が journal 末尾 64 KiB を走査し、
   decision_id・frame・`planned_at <= at`・`buttons==['a']`・`hold_ms==100`
   に一致する送信を数える（`bot.py:446-460` → `_journal_pad_inputs`
   L409-443）。

つまり `input_sent=False` の確定条件は「**要求観測以後の 6 回の
dispatch 観測で、trace と一致する input_sent 行が一度も見えなかった**」
（`steps>=6`、L2305）。設計契約どおり
（`tests/test_hanjuku_camp_receipts.py:167-175`）。

### `帰還先: 不明`（castle=None）が経路を絞る

終端 metric の `castle` = `state['goal']`。None になるのは
`_request_recall` に goal 未解決で入った場合だけで、候補は:

| 経路 | 場所 | 備考 |
|---|---|---|
| 崖離脱 A（`escape_world` 到達） | `recall.py:776` | **明示的に `goal=None`**。この A は帰還ではなくフィールドカーソルの崖離脱確定 |
| 崖迂回 A（`escape_cell` 到達） | `recall.py:1061` | `state.get('goal')` 未設定なら None |
| picker fallback（goal 未解決） | `recall.py:1997` | `state.get('goal')` が None のまま確定 |
| hero/fort の屋根確定（goal 未解決） | `recall.py:2025` | `state.get('goal')` が None のまま確定 |

map.py 側の `_request_recall` 呼び出し（L723・L838・L991）は必ず城名を
束縛する（`under`/`goal`/`chart.home_castle`。`home_castle` は未登録章でも
`HOME_CASTLES[1]` を返す、`hanjuku_chart.py:1221-1223`）ため、
**castle=None は map.py ピッカー経路を除外する**。上記はすべて
`[pad('a')]` 単発（`a_inputs=1`）なので、receipt が 1 件でも見えれば
`input_sent` が立つはずだった。

ch9 ボスフェーズ月で出撃フロー（ヘルメス→ドンドン）完走直後の野営帰還、
かつ 帰還先不明 という記録形から、**最も可能性が高いのは崖隣接野営への
離脱 A（escape_move 系）または goal 未解決の強制確定**である。
いずれにせよ「A が journal 証明されなかった単発入力」であることは確定。

## 2. #5233 との異同（検証観点 2）

| 項目 | #5233 | #5248 |
|---|---|---|
| `input_sent` | True（送信 journal 証明あり） | False（receipt 未出現） |
| 失敗段階 | 送信**後**: 割り込みが `recall_interrupted` の射程外で post-send 分類に落ちた | 送信**前**: `camp_recall_requested` と `camp_recall_input_sent` の間で receipt 鎖が切れた |
| `interrupted` | （割り込み実在・未標記が問題） | False（標記機構上は正しい、§4） |
| 機構 | post-send の割り込み分類の欠陥 | pre-send の dispatch 証明ロス |

→ **別機構**。`_mark_recall_interrupted` は `stage=='await_dispatch'` かつ
`input_sent` 未設定の間だけ刻印される（`recall.py:448-450`）ため、本件の
窓（送信証明前）はマーカーの主対象内だが、マーカー刻印の契機は戦闘系画面
（`recall.py:1214-1216`・`1231-1232`）への遷移であり、本件はそれが無かった。

ただし**記録形としては同族**: 両件とも `camp_recall_unconfirmed` 一種に
畳まれ、issue 本文からは失敗段階が読み取れない。#5233 の「送信後の
分類欠陥」とは反対端（送信証明そのものの欠落）であり、#5233 の修正の
射程には入らない。

## 3. #5147 との異同 と `interrupted=False` の正しさ（検証観点 3）

#5147 は**誤標記**（実到着が未確認へ降格）だった。本件の `interrupted`
フラグは機械的には正しい: 終端が `_flow['recall_interrupted']` を pop して
見つからなかった（`recall.py:2306-2307`）だけであり、マーカーは

- 戦闘系画面遷移時（`recall.py:1214-1216`）、
- `mem['battle']` 保持時（`recall.py:1231-1232`）、
- scene abandon 時（`orchestrator.py:957`）

にのみ立つ。ただし**カバレッジには穴がある**:

- `await_dispatch` 中の非戦闘シーン（text/event/yes_no 系）はマーカー対象外。
  `recall.py:1466` の `_mark_recall_interrupted` 呼び出しは外側条件
  （L1460 `stage != 'await_dispatch'`）と内側条件（L448 `== 'await_dispatch'`）
  が矛盾し**恒常的に no-op**（デッドコード）で、text 系中断は刻印されない。
- ゆえに `interrupted=False` は「戦闘系の割り込みが無かった」ことは保証するが
  「割り込みが無かった」ことは保証しない。

ただし本件では `input_sent=False` が主シグナルであり、割り込みの有無は
副次的（割り込みがあっても `status` は `dispatch_unconfirmed` のまま；
`interrupted` は `_arm_recall_skip` の待ち時間選択にのみ効く、
`recall.py:2336-2338`）。#5147 型の「誤標記で実到着を降格」とは向きが違う。

## 4. 記録形の問題 と high 起票の妥当性（検証観点 4）

**#5233 と同じ「トリアージ不能な記録形」の新たな面**が確認できた:

1. **失敗段階の誤署名**: `_camp_reason_code`（`month_audit.py:737-753`）は
   `event`・`reason` の連結文字列に `'到着'` があれば
   `camp_arrival_unconfirmed` を返す（L749-750）。終端の reason
   （`recall.py:2355`「帰還の本人・移動・**到着**を確認できず…」）は
   dispatch_unconfirmed / arrival_unconfirmed 共通文なので、
   **送信前の失敗が「帰還到着を確認できない」として署名される**。
   issue シグネチャが失敗段階を誤記述する実例が本件。
2. **`resulting_event` の消失**: `_camp_event` は `resulting_event`
   （`'dispatch_unconfirmed'`）を camp_event に保持する
   （`month_audit.py:784`）が、issue 本文（L3737-3741）には出力されない。
   `input_sent: False` からの推論でしか dispatch を読めない。
3. **goal-less 要求の誤起票**: escape_move の A は帰還ではなく崖離脱の
   フィールド移動確定（`recall.py:772-777` のコメント自身が明記）だが、
   その送信ロスが「キャンプ帰還未確認」の incident として起票される。
   また goal=None では `roster_read_due` も登録されない
   （`recall.py:2271` `if goal:`）ため「到着」は原理的に確認不能であり、
   帰還失敗系の集計を汚す。
4. **receipt 境界の誤判定可能性**: 物理送信されても
   `input_sent=False` になりうる経路が残っている:
   - `action_sent` が `trace={}` にフォールバックする
     （`hanjuku_run.py:462-468`）: `hanjuku_bot.json` の `read_record` が
     AdapterError（超過サイズ等、コメントが実測例を引用）または
     run identity 不一致のとき、journal 行は `decision_id=None` で書かれ
     receipt に永久に一致しない。
   - `_valid_request_trace` の identity ゲート（`bot.py:398-406`）:
     generation/lease が要求と受領の間で変わると receipt は常に None。
   - journal 欠落・symlink → `_journal_pad_inputs` は None を返し、
     `recall.py:2291` の `or {}` で「0 件」として扱われる。
   - `adapter.act` 内で `self._act`（物理送信）成功後に
     `action_sent` が例外を起こす（`retroarch.py:459-461`）と、
     送達済み入力が journal 証明なしで残り、かつ後続アクションも
     ループごと落ちる（`agent/loop.py:160-185`）。
   これらは「本当に未送信」（adapter の terminal hold `retroarch.py:452-453`、
   fence 喪失、`_act` 例外、agent の非 0 終了時 drop `bot.py:757-758`）と
   記録上区別できない。

**high 起票は正当**: `dispatch_unconfirmed` は「bot が決定した入力が
6 観測以内に journal 証明されなかった」実在の異常であり、監査が拾う
契約自体は正しい（`_build_camp_incident` L835/L877 で `auto_file=True`）。
問題は起票の是非ではなく、**起票された記録が失敗段階を誤署名し、
再現調査に必要な stage/steps/status/camp を本文へ出さない**点にある。

## 5. 残存不確定点（read-only では確定不能、判定には影響しない）

- A が agent を物理的に出たか。一意確定にはラン成果物
  `hanjuku_events.jsonl`（要求時刻前後の `input_sent` 行とその
  `decision_id`/`decision_frame_sha256`）とアクションログが必要。
  - `input_sent` 行が無い → 未送信（adapter/agent 側の drop・hold）。
  - 行はあるが `decision_id` が null/不一致 → 送達済みだが receipt 不可
    （`action_sent` の trace フォールバック実欠陥）。
- 要求元が escape_move 系か否かは、`camp_recall_requested` の
  `observed_metric.escape_move`（`**fields` 経由で記録される、
  `recall.py:2283`）で判別可能。

## 6. 修正候補（実装はしない、検証の結論として）

1. 終端 `observed_metric` に `stage`・`steps`・`target`・`status`
   （= resulting_event）を含める（`recall.py:2351-2354`）。監査の
   `_camp_event` は既にこれらを読む形がある（`month_audit.py:786-791`）。
2. `_camp_reason_code` に `dispatch_unconfirmed` 専用のコードを分ける
   （reason 文字列の `'到着'` 部分一致に依存しない。`month_audit.py:737-753`）。
3. issue 本文のキャンプ帰還節に `resulting_event`（= status）を出す
   （`month_audit.py:3737-3741`）。
4. escape_move の A を `camp_recall_*` の incident 系から分離するか、
   少なくとも `escape_move` フラグを終端 metric まで通す
   （`recall.py:776`・`1061`）。
5. `action_sent` が `trace={}` で journal 行を書く場合、行に
   `trace_missing: true` を残す（`hanjuku_run.py:462-473`）。送達済みなのに
   dispatch_unconfirmed になる経路を検出可能にする。
6. `recall.py:1466` のデッド呼び出し（await_dispatch では恒常 no-op）を
   除去するか、text 系割り込みにも刻印を広げる設計を別途検討。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py:2351`（`_recall_dispatch_step` 終端、唯一の記録点） |
| input_sent False | `recall.py:2293-2318`、receipt 未一致で `status='dispatch_unconfirmed'` |
| interrupted False | `recall.py:2306-2307`（`recall_interrupted` マーカー未検出） |
| stage 不明 / steps None | `observed_metric` に未含有（`recall.py:2351-2354`）→ `month_audit.py:788-789`→`3740` |
| 野営 None | 終端 metric に `camp` キーが無い（`recall.py:2351-2354`）→ `_camp_event` L773-775 |
| 帰還先 不明 | `state['goal']` が falsy の要求（`recall.py:776`/`1061`/`1997`/`2025` のいずれか） |
| シグネチャ「帰還到着を確認できない」 | `_camp_reason_code` L749-750 が reason の `'到着'` に一致（dispatch でも同じ署名になる） |
| confidence: high / auto_file | `_build_camp_incident` L835・L877 |
| 直前の出撃フロー | `sortie_departure_requested` 等は map.py の出撃経路。recall はその後に独立して開始 |
| `camp_recall_input_sent` 欠落 | `recall.py:2295-2302`、receipt 一致時のみ記録 |
