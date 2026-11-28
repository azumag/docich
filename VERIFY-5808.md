# VERIFY-5808 — [半熟英雄] キャンプ帰還未確認: ch12 ボス

対象: azumag/docich#5808（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1595-boss-order-recovery`
月 `ch12 18-8`（観測 1184回 / 270.3秒 / max gap 0.9秒）
fingerprint `0b1f418d2b3f507c` / occurrence(incident_id) `cae60146c2ecee98a17f`
key `0b1f418d2b3f507c/18-8/228412` / created_at `1795835325.314646`

判定: **(b) 設計どおり** — `input_receipt_confirmed=True` は「帰還Aの物理送信 receipt が一致した」ことだけを示し、ゲーム側受理（帰還先ピッカー閉鎖）や到着を意味しない。本件は送信後にボスフェーズの戦闘・テキスト場面が前面を取り、現行コードでは post-send coverage として `scene_covered` 扱いになる経路と一致する。被った後の `map` は「受理でピッカーが閉じた」のか「戦闘/場面でピッカーが消えた」のか区別できないため、現行設計は受理と断定せず、bounded window 終端で `arrival_unconfirmed` を保持する。宛先拒否 (`camp_recall_goal_failed`) には計上しない。この終端形は現行回帰テストで明示的に固定されている。

帰還先が通常の自軍城ではなく ch12 のボス (`boss_move`) である点と、月内に `order_dispatch_starved` 1回がある点は #5783 と異なる。しかし、どちらも送信前失敗ではなく、送信後の受理・到着確認不能を説明するものであり、`camp_recall_unconfirmed` の終端分類を変えない。ボス宛てでは generic な宛先 roster read fallback が実効しにくい観測性残件はあるが、これは本件の未確認保持そのものが実欠陥であることは示さない。

調査基盤: worktree `/home/hatch/work/docich-5808`、branch は `.git` メタデータ上 `verify/5808`、worktree の `BOT_VERSION` は `hanjuku-chart-v1595-boss-order-recovery`。read-only 検証。コード変更なし、git の書き込み操作なし、`gh` 実行なし。issue 本文は公開 GitHub ページ経由で取得した。ラン `g1190-ch1v9` の本番 decision log は指示どおり参照していない（ローテーション済み前提）。

`handoff.md` 正本: `DOCICH_HANDOFF_PATH` の明示参照を確認しようとした shell 実行が承認却下されたため**未読**。GitHub と worktree の現行コードだけを根拠にした。

## 1. fingerprint / occurrence の式照合

### fingerprint

現行 `src/docich/hanjuku/month_audit.py` の式（L3545-L3547）:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

camp 系 incident の呼び出しは `_build_camp_incident`（L1152）で:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
```

したがって本件の fingerprint 入力は、issue JSON の `chapter: 12`、`kind/event: camp_recall_unconfirmed` から次の1行に確定する。

```text
12|camp_recall_unconfirmed|camp_recall_unconfirmed
```

期待される先頭16 hex は issue 値 `0b1f418d2b3f507c`。`signals` には `camp_arrival_unconfirmed` も入るが、fingerprint 呼び出しには `[event['kind']]` だけが渡され、reason_code は fingerprint 入力に含まれない。したがって同じ章の `camp_recall_unconfirmed` は reason_code 違いでも同一 fingerprint に集約される設計である。既存の `VERIFY-5437.md` も、同じ式と ch12 の同種別入力から同じ fingerprint `0b1f418d2b3f507c` を記録しており、式面の一致を補強する。

### occurrence / incident_id

同じ `_build_camp_incident`（L1147-L1150）の式:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{event['reason_code']}|{event.get('camp')}|"
       f"{event.get('general')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSON の値を代入すると、本件の occurrence 入力は次の1行に確定する。

```text
g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[140, 127]|None|1795835325
```

期待される先頭20 hex は issue 値 `cae60146c2ecee98a17f`。`created_at` が `1795835325.314646` と issue JSON に残っているため、`int(float(now))` は `1795835325` と後追いで一意に復元できる。`camp` は Python list の `str()` 表現で、カンマ後の空白を含む `[140, 127]`。`general` は issue JSON で null / 画面表示では「不明」であり、f-string では `None` になる。

### key

`key 0b1f418d2b3f507c/18-8/228412` は `_month_key`（fingerprint / month / first_tick）の形と一致する。month_summary の `first_tick` は issue JSON で `228412`、月は `18-8` であり、構造上の矛盾はない。

### 再計算の成否

`python3` での sha256 再計算は、この実行環境では python3 実行が承認却下されたため**未実施**である。検品ゲートが上記2つの入力行を UTF-8 の sha256 で再計算し、それぞれ先頭16/20 hex を issue 値と照合すればよい。式と入力値の特定は完了している。

## 2. 現行コード上の終端経路

### 2.1 `input_receipt_confirmed=True` でも `arrival_unconfirmed` になる条件

現行 `src/docich/hanjuku/flows/recall.py` の `_recall_dispatch_step` では、次の3段階が分かれている。

1. **送信**: `_journal['_recall_inputs']` と recall state の `request_trace` が一致し、必要な A 入力数を満たすと `input_sent=True`。この時点で `camp_recall_input_sent` を記録する。ここで分かるのは「同じラン・判断ID・実画面の帰還A送信記録」だけであり、ゲーム側受理ではない。
2. **受理の代理証拠**: `input_sent` 後に `screen.kind == 'map'` を観測すると `picker_closed=True`（L2822-L2824）。通常はこれが帰還先ピッカー閉鎖＝受理の代理証拠になる。
3. **到着**: 受理後は `camp_recall_dispatched` とし、到着は名簿の実読みや camp verify で確認する。`input_sent` だが `picker_closed` が取れない終端でも、`arrival_unconfirmed` なら宛先の `roster_read_due` をアームする（L3043-L3051）。

終端条件は `picker_closed`、非 stale 6観測、または stale bound のいずれか。`input_sent` が真で `picker_closed` が偽なら status は `arrival_unconfirmed`、理由文は issue と同じ「帰還A送信後の受理・到着を確認できず、未確認として保持」になる（L2900以降、L3036-L3042）。したがって `input_receipt_confirmed=True` と `arrival_unconfirmed` の併存は矛盾ではなく、送信・受理・到着を分離する設計どおりの状態である。

### 2.2 本件の画面遷移が示す post-send coverage

issue JSON の終端 metric は:

- `stage: await_dispatch` / `steps: 6`
- `status/resulting_event: arrival_unconfirmed`
- `input_receipt_confirmed: true` / `input_sent: true`
- `picker_closed: false` / `interrupted: false` / `mode_lost: false`
- `recent_screens: ["unknown", "map", "map", "map", "map", "map"]`
- terminal decision の screen は `unknown`

現行コードでは `scene_covered` が無ければ、`input_sent` 後の `map` で `picker_closed=True` が立ち、`interrupted` も `scene_covered` も無ければ `dispatched`（受理）になる。実際には `map` を5回見ても `picker_closed=False` のまま `arrival_unconfirmed` になっているため、現行コード上は **`scene_covered=True` が立っていた経路**に一致する。

この挙動は回帰テスト `tests/test_hanjuku_recall_scene_resume.py` で明示されている:

- `scene_covered=True` のまま6回 `map` を渡すと `camp_recall_unconfirmed` が出る
- `camp_recall_dispatched` は出ない
- `camp_recall_goal_failed` は出ない
- verification は `status=arrival_unconfirmed` / `picker_closed=False` / `scene_covered=True`
- coverage の無い通常 `map` は `dispatched` になる

つまり、本件と同じ見た目（送信済み・被り後の map・picker_closed false・unconfirmed）はバグではなく、誤受理・宛先拒否誤計上を防ぐ fail-closed 設計としてテスト固定されている。

### 2.3 戦闘・テキスト場面との関係

`_camp_recall_step` は battle 系画面、または `mem['battle']` 中の画面で `_mark_recall_interrupted` を呼ぶ（L1299-L1318）。`_mark_recall_interrupted`（L454-L477）は、receipt 未確認なら `recall_interrupted` を残すが、送信済みまたは matching receipt が既に journal にある場合は `scene_covered=True` を残す。これは「A を再送すると受理済み帰還を二重送信しかねない」ための区別である。await_dispatch 中のテキスト場面でも同じ coverage 化がある（L1546-L1549）。

issue の月内決定は `event_text_advance` 17、`monster_menu_choice` 8、`battle_egg_general_attack` 3、`battle` 系を含み、ボスフェーズの戦闘/テキストが前景だったことを示す。`interrupted=False` は「横取りが無かった」意味ではない。post-send coverage では `recall_interrupted` ではなく `scene_covered` が使われ、terminal metric の `interrupted` は前者だけを見るためである。

### 2.4 ch12 ボス宛て `boss_move` との関係

ch12 の boss castle label は `ボス` で、通常の移動ボス章（ch9）ではない。本件の goal `ボス` は、post-month status で HP が十分な siege-holding camper を boss へ再移動させる `boss_move` 経路（#3505）と一致する。`recall.py` の `_boss_move_dest_step` は boss への RECALL Y jump を経て目的地を確定し、その後の A 入力を `_request_recall` の await_dispatch で検証する。

issue JSON で `target_cell` は `[140, 127]`、camp も `[140, 127]` だが、これは操作対象の野営セルであり、ボス宛先セルではない。`boss_cell: null` / `camp_world: null` は ch4 のような測定 boss cell を必要としない固定ボス章では異常とは断定できない。直前決定に `y_jump_open` / `y_jump_confirm` が各2回あり、boss Y jump の前段階は少なくとも完了側へ進んでいる。本件の terminal はその後の送信後検証段階である。

### 2.5 `order_dispatch_starved` の位置づけ

`order_dispatch_starved` は、ボスフェーズで開始可能な指示が `order_start` に届かないまま上限を超えたことを示す。`recall.py` では、**新しい** recall/hunt の開始時には starved boss-phase dispatch へ map を譲る（L1328-L1332）。一方、既に in-flight の recall は自分の menu/map flow を保持する。

したがって本件では、`order_dispatch_starved` はボスフェーズの前景輻輳を示す補助証拠ではあるが、帰還 state machine を直接壊す欠陥ではない。未確認の直接理由は、送信後の画面証拠が戦闘/テキストに覆われ、post-send map を受理と断定できないことである。

### 2.6 ボス宛ての roster read fallback（残件としての観測性）

`_request_recall` は goal がある通常 recall なら `roster_read_due[goal]` を登録する。しかし戦略側の due probe は `routes.roster_read_due` の城が `owned` に含まれる場合だけ probe order 化する（`replan.py` L2071-L2073）。`ボス` は自軍城ではないため、goal が `ボス` のままでは、この generic な宛先 roster read は実効しない可能性が高い。

ただし、これは本件の `arrival_unconfirmed` を実欠陥へ変える根拠ではない。`boss_move` は post-month status の task retry として bounded に再キューされる設計が回帰テストにある（`test_boss_move_task_is_requeued_within_the_bounded_budget`）。送信が実際には受理されていたなら将軍は camp ではなく行軍/城内へ変わり、未受理なら boss_move task が残る。それでも、ボス宛てでは「宛先 roster read が到着 receipt」という通常 recall の説明がそのまま成立しないため、観測性・回復導線の残件として区別して記録する。

## 3. #5783 との異同

#5783 は:

- 同じラン `g1190-ch1v9` / generation `1190`
- 判定 `camp_recall_unconfirmed` / reason `camp_arrival_unconfirmed`
- `input_receipt_confirmed=True` / `picker_closed=False` / `interrupted=False`
- 送信後に別行軍の戦闘が前面を取り、post-send `scene_covered` の終端形
- 検証結論は (b) 設計どおり

本件 #5808 との共通点:

- kind / reason / terminal status / input receipt / picker_closed / interrupted の組合せが同じ
- `map` 観測後も picker_closed が立たないため、現行コード上は `scene_covered` が必要
- 送信・受理・到着の分離設計に従う fail-closed 保持
- `camp_recall_goal_failed` への誤計上を避けるべき形状

相違点:

- #5783 は ch9 14-6、宛先は自軍城 `イデ！`、general は `キャロット`
- 本件は ch12 18-8、宛先は `ボス`、general は null / 不明
- 本件は `boss_move` 経路であり、通常の帰還先 roster read fallback がそのまま効きにくい
- 本件月には `order_dispatch_starved` 1回があり、ボスフェーズの foreground congestion がより強い
- fingerprint / occurrence の入力値も異なるため、同一 incident の重複ではない

結論として、本件は #5783 と同じ機構の設計どおり終端でありつつ、宛先がボスであるため回復観測の弱さは #5783 より大きい。

## 4. #5722 / PR #5729 との異同

#5722 は:

- 判定 `camp_recall_failed`（失敗確定）
- 決定 `camp_boss_redirect_aborted`
- status `boss_jump_failed`
- `input_receipt_confirmed: False`
- ch9 boss redirect で、帰還先選択中の RECALL Y jump を二重起動し、per-redirect budget を使い切って abort した**送信前段階**の欠陥

PR #5729（v1577、マージ済み）は、その事前段階に対して `recall_y_jump_transition_step` を追加し、active な RECALL Y jump がある `map_target` を再処理しないようにした。現行コードにも同ガードがある。

本件 #5808 は:

- 判定 `camp_recall_unconfirmed`（確認不能）
- 決定 `camp_recall_unconfirmed`
- status `arrival_unconfirmed`
- `input_receipt_confirmed: True`
- 直前決定に `y_jump_open` / `y_jump_confirm` があり、少なくとも本件 terminal は Y jump 完了後の送信後検証段階
- ch12 の固定 boss への `boss_move` であり、ch9 boss redirect の二重起動経路ではない

したがって #5729 の修正対象では**カバーされない別経路**である。本件ランは `v1595` で #5729 の `v1577` より後だが、提示値は同修正の再発ではなく、最初から送信後の不確実性保持を示している。

## 5. 監査 projection の限界（結論は変えない補助観測）

現行 `month_audit._camp_event` は `input_receipt_confirmed`、`picker_closed`、`interrupted`、`mode_lost`、`recent_screens` などを抽出するが、`scene_covered` / `scene_covered_tick` / `unsent_proven` / `stale_steps` / `resends` / `last_screen_kind` は抽出しない。`_summarize_flow` も `recall_verification` を `stage/status/steps` 中心へ縮めるため、issue JSON から `scene_covered` の実値を直接読めない。

本件ではそれでも、`map` 観測済みなのに `picker_closed=False` で終端した形から post-send coverage 経路を現行コード上たどれる。ただし、issue だけで第三者が再検証しやすいかという観点では projection が弱い。これは観測性の残件であり、今回の runtime 挙動の結論 (b) を覆すものではない。

## 6. 結論の分類

**(b) 設計どおり。**

理由:

1. `input_receipt_confirmed=True` と `arrival_unconfirmed` の併存は、送信・受理・到着を分ける現行契約の通常形である。
2. 送信後に戦闘/場面が覆った場合、後続 `map` を受理と誤認しないよう `scene_covered` で保持し、`picker_closed` を立てないのは現行の明示設計である。
3. その場合でも bounded window の終端では `camp_recall_unconfirmed` / `arrival_unconfirmed` を記録し、`camp_recall_goal_failed` には計上しない。回帰テストがこの exact shape を固定している。
4. ch12 ボス宛ては `boss_move` の送信後検証であり、#5722/#5729 の boss Y jump 二重起動による送信前失敗とは別経路である。
5. `order_dispatch_starved` はボスフェーズ混雑の補助証拠であって、本件の帰還 state machine を直接破壊した証拠ではない。

誤検知 (c) ではない: 実際に受理・到着を確認できなかったという意味では監査の観測は真である。実欠陥 (a) とも判定しない: 現行コードはこの曖昧性を受理と断定せず、二重送信と宛先拒否誤計上を避けるために意図的に未確認保持する。判定不能 (d) でもない: 本番ログなしでも、issue JSON の `map` / `input_sent` / `picker_closed=false` / ボスフェーズの前景決定と現行コード・回帰テストの対応から、少なくとも現行設計上の終端経路は特定できる。

未決・残件:

- `scene_covered=True` は issue projection に出ないため直接証拠ではなく、画面遷移からの強い推定である。本番 decision log はローテーション済みで裏取りできない。
- ボス宛て `boss_move` では generic な `roster_read_due[goal]` が実効しにくい。boss_move task retry が実際の回復導線だが、本件 occurrence が後に解消したかはログなしでは確認できない。
- 監査 projection が `scene_covered` を落とすため、issue 本文だけの再検証性は弱い。観測性改善の余地はあるが、本件の分類は変えない。

## 検品ゲート確認（Muse、2026-11-28 18:35 JST、現行 main 4d5917dbd）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|camp_recall_unconfirmed|camp_recall_unconfirmed")[:16] = 0b1f418d2b3f507c、SHA-256("g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[140, 127]|None|1795835325")[:20] = cae60146c2ecee98a17f。いずれも issue の値と一致する。
- 結論 (b) 設計どおりを維持する。現行 main のコードで次の3点を直接確認した。
  - `recall.py` の `_recall_dispatch_step` は、送信済みで `screen.kind == 'map'` かつ `scene_covered` が立っていない場合にだけ `picker_closed = True` とする。`scene_covered` が立っている場合、後続の `map` は受理証拠にしない。
  - 受理（`accepted`）は `input_sent` と `picker_closed` に加えて `not interrupted` かつ `not scene_covered` を要求する。送信後に戦闘・テキスト場面が前面を取った場合、`_mark_recall_interrupted` は送信済みなら `recall_interrupted` ではなく `scene_covered = True` を残す（二重送信を避ける区別）。
  - 回帰テスト `tests/test_hanjuku_recall_scene_resume.py` は、`scene_covered=True` のまま6回 `map` を渡すと `camp_recall_unconfirmed` が出て `camp_recall_dispatched` も `camp_recall_goal_failed` も出ない形を明示的に固定している。本件の終端形（送信済み・被り後の map・`picker_closed` false・`arrival_unconfirmed`）はこの設計どおりの形である。
- ch12 ボス宛て `boss_move` 経路である点と月内の `order_dispatch_starved` 1回は、#5783 との相違点として記録されているが、送信後の受理・到着確認不能という終端分類を変えない。ボス宛てでは generic な宛先 roster read fallback が実効しにくい観測性残件はあるが、本件の未確認保持そのものが実欠陥であることは示さない。
- 監査 projection が `scene_covered` を落とすため、issue 本文だけの再検証性は弱い。これは観測性改善の余地であり、本件の分類は変えない。修正 PR は割り当てない。
