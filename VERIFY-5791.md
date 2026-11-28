# VERIFY-5791 — [半熟英雄] キャンプ帰還未確認: ch10 ベーグル

検証日: 2026-11-28
対象: azumag/docich#5791
worktree: `verify/5791` / base `origin/main` `50580f17f` / 現行 `BOT_VERSION = hanjuku-chart-v1597-chikujou-defer-reason`
範囲: read-only 検証。コード修正・commit・push・PR 化なし。本番 decision log はローテーション済みのため、issue 本文と現行 main のコードのみを使用。

## 結論

**(b) 設計どおり**

#5791 の `input_receipt_confirmed=True`・`picker_closed=False`・`stage=await_dispatch`・`steps=6`・`status=arrival_unconfirmed` は、現行 main に明示実装・回帰テストされている fail-closed 経路と一致する。issue 本文の直前決定にも `camp_recall_scene_covered`（送信済み帰還ピッカーが月初場面に覆われた）があり、送信済みの帰還Aについて、後続の `map` をピッカー閉鎖＝受理と誤認しないために `scene_covered` を保持した結果、`picker_closed` が立たず 6 観測で `arrival_unconfirmed` 終端になったと読める。

したがって、この issue が示す「送信後に受理・到着を確認できない」事実は真実で、誤検知ではないが、現行設計では重複送信を避けるため未確認として保持するのが意図された挙動である。#5789 の boss_move 再試行欠陥や #5788 の pre-dispatch Yジャンプ欠陥とは原因層が別である。

## fingerprint / occurrence の式と再計算結果

`python3` 実行が承認却下されたため、ハッシュ値の再計算は**未実施**である。検品ゲートで再計算できるよう、現行 main の式と入力値を以下に残す。

対象コードは `src/docich/hanjuku/month_audit.py`（`tests/test_hanjuku_month_audit.py` も `docich.hanjuku.month_audit` を import する現行パッケージ）である。

### fingerprint

現行 main の式:

```python
# _build_camp_incident
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])

# _fingerprint
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

#5791 の入力:

- `chapter = 10`
- `kind = event['kind'] = 'camp_recall_unconfirmed'`
- `signals = ['camp_recall_unconfirmed']`
- したがって raw は:

```text
10|camp_recall_unconfirmed|camp_recall_unconfirmed
```

- 期待値（issue本文）: `bcef4bcfdbc6633b`
- 再計算: **未実施（python3 実行却下）**

### occurrence / incident_id

現行 main `_build_camp_incident` の式:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{event['reason_code']}|{event.get('camp')}|"
       f"{event.get('general')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSON から読める入力:

- `runtime_id = 'g1190-ch1v9'`
- `generation = 1190`
- `event['kind'] = 'camp_recall_unconfirmed'`
- `event['reason_code'] = 'camp_arrival_unconfirmed'`
- `event.get('camp') = [138, 125]`（Python list の `str` では `[138, 125]`）
- `event.get('general') = 'ベーグル'`
- `now = created_at = 1795818768.522966` なので `int(float(now)) = 1795818768`
- したがって raw は:

```text
g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[138, 125]|ベーグル|1795818768
```

- 期待値（issue本文の occurrence / incident_id）: `da3d2f09397e6f51d039`
- 再計算: **未実施（python3 実行却下）**

## 現行 main で確認した経路

### 1. await_dispatch で止まる条件

`flows/recall.py` の `_request_recall` は、帰還A送信を要求した時点で `stage='await_dispatch'`, `steps=0` にし、送信・受理・到着は未確認として保持する。

`_recall_dispatch_step` では:

1. 送信 journal (`_recall_inputs`) の `request_trace` と A 件数が一致すると `input_sent=True` にし、`camp_recall_input_sent` を記録する。
2. `input_sent and screen.kind == 'map' and not scene_covered` のときだけ `picker_closed=True` にする。
3. `picker_closed` または非 stale の `steps >= 6` などで終端へ進む。
4. 終端の status は、`input_sent and picker_closed and not interrupted and not scene_covered` のときだけ `dispatched`、それ以外で `input_sent` なら `arrival_unconfirmed`、未送信なら `dispatch_unconfirmed` または `dispatch_preempted` になる。

#5791 は `input_sent=True`・`picker_closed=False`・`scene_covered` ありの形なので、6観測で `arrival_unconfirmed` になる現行実装上の経路そのものである。

### 2. `order_dispatch_starved` との関係

`order_dispatch_starved` は `strategy/orders.py` で、出撃計画の pending order が `order_start` へ届かないことの枯渇検出である。orchestrator は各観測の早い段階でこれを評価するが、camp recall の `_recall_dispatch_step` や終端 status 判定はこの値を参照しない。

recall 側での参照は、まだ recall が始まっていない (`state is None`) ときに新規 recall の開始を譲る gate だけである。つまり #5791 の `await_dispatch` 終端は `order_dispatch_starved` によって起きたのではなく、同一時間帯に出撃計画の枯渇と帰還確認が併存していただけである。issue 本文の `order_dispatch_starved` は出撃計画側の証拠であり、帰還A送信後の `arrival_unconfirmed` を直接説明するものではない。

### 3. `input_receipt_confirmed=True` でも到着確認へ進まない理由

`input_receipt_confirmed` / `input_sent` は sender journal 上で同一ラン・同一 request trace の A 送信を確認した意味にすぎない。ゲーム側の受理ではない。現行コードは受理の proxy を「帰還先ピッカーが閉じて `map` へ戻ること」に置き、到着は宛先 roster 実読みで追う。

ただし月初場面・戦闘などが送信済みピッカーを覆った場合、後から見えた `map` は「受理で閉じた」のか「場面で閉じた」のか区別できない。そのため `_mark_recall_interrupted` / `_recall_after_scene` は、送信 receipt がある場合に `scene_covered=True` を保持する。`scene_covered` があると:

- 後続の `map` で `picker_closed=True` にしない
- 再送もしない（受理済みなら重複帰還になるため）
- `arrival_unconfirmed` 終端でも `camp_recall_goal_failed` に目的地拒否として計上しない
- 宛先 roster read の due を予約する

この終端形は `tests/test_hanjuku_recall_scene_resume.py` の `test_post_scene_map_is_not_misread_as_picker_acceptance_or_goal_refusal` で明示固定されている（6回の map 後に `camp_recall_unconfirmed`、`camp_recall_dispatched` なし、`camp_recall_goal_failed` なし、`status == 'arrival_unconfirmed'`、`picker_closed is False`、roster due 予約）。

#5791 の直前決定には `camp_recall_scene_covered`（22:32:46Z）があり、その 2 秒後に `camp_recall_unconfirmed`（22:32:48Z）が出ている。これは上記テストが固定する設計経路と一致する。

### 4. 観測性の残件（結論は変えない）

- month_audit の `_camp_event` projection は `scene_covered` / `scene_covered_tick` を issue JSON の `camp_event` に写さない。issue 本文の `interrupted=False` だけを見ると scene cover が見えにくいが、直前決定表には `camp_recall_scene_covered` が出ている。#5783 の検証でも同じ観測性残件が指摘されている。
- `camp_world` / `boss_cell` が `None` なのは、終端 metric が boss redirect abort 用の補助情報と違ってこれらを十分に引き継がないためで、今回の `arrival_unconfirmed` 判定そのものには使われていない。
- #5791 の宛先は `ボス` である。`arrival_unconfirmed` 時に宛先 roster read due を予約する仕組みはあるが、未所有城の due は roster probe されない設計であり、`ボス` は自城 roster として読めない。したがって boss_move 宛の post-send 到着確認には追加の観測性上の限界がある。ただしこれは「送信済みピッカーが月初場面に覆われたため受理を推測しない」という本件の直接原因を欠陥化するものではない。

## 関連 issue との異同

### #5783（ch9 キャロット / camp_recall_unconfirmed）

現行コメントでは #5783 は **(b) 設計どおり** と結論済みだった（依頼文では「検証待ち」とあったが、公開 issue 上の最新コメントは結論済み）。

共通点:

- 同じ `camp_recall_unconfirmed` / `camp_arrival_unconfirmed`
- `stage=await_dispatch` / `steps=6`
- `input_receipt_confirmed=True` / `picker_closed=False` / `interrupted=False`
- 送信 receipt は物理送信だけを示し、受理・到着を意味しない

相違点:

- #5783 は ch9、宛先 `イデ！`、target_cell `None`。別行軍戦闘・text 系の前景で scene cover が inference される形。
- #5791 は ch10、宛先 `ボス`、target_cell `[139, 124]`。issue 直前決定に月初場面による `camp_recall_scene_covered` が明示されている。

結論として同じ post-send verification 経路である。

### #5788（ch10 15-2 / 帰還Yジャンプ失敗）

#5788 は **(a) 実欠陥** と結論済みだが、あれは帰還A送信前の pre-dispatch Yジャンプ residual 失敗である。#5791 は帰還A送信後の post-send verification 終端なので、直接の原因経路は別。実際 #5788 の検証コメントも #5791 を「A送信後の arrival_unconfirmed で、本件は A送信前の pre-dispatch 失敗のため別経路」と切り分けている。

### #5789（ch10 15-3 グレイ / camp_abandoned、再発コメントに 15-5 ベーグル）

#5789 は **(a) 実欠陥** と結論済み。欠陥は、健全な ch10 White House camp の解消経路である月次 `boss_move` が、戦闘・場面割り込みで recall flow を失うと同月内に再試行されない点である。`wait_recall` の再試行条件が `job['kind'] == 'recall'` に限られ、`boss_move` を再試行しない。

#5791 は同じ ch10 White House / boss_move パイプラインに隣接するが、判定は `camp_abandoned` ではなく `camp_recall_unconfirmed` であり、今回の証拠は「A送信後に scene cover があり、受理を推測せず fail-closed した」ものである。#5789 再発コメントのベーグルは 15-5 の `camp_abandoned` であり、本件 15-4 の `camp_recall_unconfirmed` と同じ将軍でも別月・別判定である。#5791 単体から、ベーグルが後に野営放置になった原因が同じかどうかは証明できない。

## 未決の点

- python3 実行が却下されたため、fingerprint / occurrence の sha256 再計算自体は未実施。式と入力値は上記のとおり特定済み。
- 本番 decision log はローテーション済みなので、送信Aが実際にゲームへ受理されたか、ベーグルがその後に移動を開始したか、15-5 の camp_abandoned へどう接続したかは実ログでは確認できない。ただしこれらは #5791 の終端分類が設計どおりであるという結論を変えない。

## 検品ゲート確認（Muse、2026-11-28 13:47 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("10|camp_recall_unconfirmed|camp_recall_unconfirmed")[:16] = bcef4bcfdbc6633b、SHA-256("g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[138, 125]|ベーグル|1795818768")[:20] = da3d2f09397e6f51d039。いずれも issue の値と一致する。
- 結論 (b) 設計どおりは、送信済み帰還ピッカーが月初場面に覆われた場合に受理を推測せず `arrival_unconfirmed` として fail-closed する経路が現行コードに明示実装・回帰テストされていることで裏付けられている。#5783 と同型。#5788（pre-dispatch Yジャンプ欠陥）や #5789（boss_move 再試行欠陥）とは原因層が別である。
