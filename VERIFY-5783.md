# VERIFY-5783 — [半熟英雄] キャンプ帰還未確認: ch9 キャロット

対象: azumag/docich#5783（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1588-corridor-probe-count`
月 `ch9 14-6`（観測 185回 / 55.3秒 / max gap 1.2秒）
fingerprint `83845131b227e2a9` / occurrence(incident_id) `26cd544a4eaf295d9a1e`
key `83845131b227e2a9/14-6/139307`
created_at `1795814157.9293802`

判定: **(b) 設計どおり** — `input_receipt_confirmed=True` は「帰還Aの物理送信 receipt が一致した」ことだけを示し、ゲーム側受理（帰還先ピッカー閉鎖）や到着を意味しない。本件は送信後に別行軍の戦闘が前面を取り、現行コードでは post-send interruption として `scene_covered` 扱いになる経路と一致する。被った後の `map` は「受理でピッカーが閉じた」のか「戦闘/場面でピッカーが消えた」のか区別できないため、現行設計は受理と断定せず、bounded window 終端で `arrival_unconfirmed` を保持し、宛先城の名簿再読みによる到着確認へ逃がす。宛先拒否 (`camp_recall_goal_failed`) には計上しない。この終端形は現行回帰テストで明示的に固定されている。

調査基盤: worktree `/home/hatch/work/docich-5783`（branch は `.git` メタデータ上 `verify/5783`、worktree の `BOT_VERSION` は `hanjuku-chart-v1595-boss-order-recovery`）。read-only 検証。コード変更なし、git の書き込み操作なし、`gh` 実行なし。issue 本文は公開 GitHub ページ経由で取得した。ラン `g1190-ch1v9` の本番 decision log は指示どおり参照していない（ローテーション済み前提）。

`handoff.md` 正本: `DOCICH_HANDOFF_PATH` の明示参照はなく、環境確認を兼ねた shell 実行が承認却下されたため**未読**。`handoff` スキルもこの環境では利用できなかった。GitHub と worktree の現行コードだけを根拠にした。

---

## 1. fingerprint / occurrence の式照合

### fingerprint

現行 `src/docich/hanjuku/month_audit.py` の式:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

camp 系 incident の呼び出しは `_build_camp_incident` で:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
```

したがって本件の fingerprint 入力は、issue JSON の `chapter: 9`、`kind/event: camp_recall_unconfirmed` から次の1行に確定する。

```text
9|camp_recall_unconfirmed|camp_recall_unconfirmed
```

期待される先頭16 hex は issue 値 `83845131b227e2a9`。
`signals` には `camp_arrival_unconfirmed` も入るが、fingerprint 呼び出しには `[event['kind']]` だけが渡され、reason_code は fingerprint 入力に含まれない。したがって同じ章の `camp_recall_unconfirmed` は reason_code 違いでも同一 fingerprint に集約される設計である。

### occurrence / incident_id

同じ `_build_camp_incident` の式:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{event['reason_code']}|{event.get('camp')}|"
       f"{event.get('general')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSON の値を代入すると、本件の occurrence 入力は次の1行に確定する。

```text
g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[129, 125]|キャロット|1795814157
```

期待される先頭20 hex は issue 値 `26cd544a4eaf295d9a1e`。
`created_at` が `1795814157.9293802` と issue JSON に残っているため、`int(float(now))` は `1795814157` と後追いで一意に復元できる。

### 再計算の成否

`python3` での sha256 再計算を試みたが、この実行環境では python3 実行が承認却下された。したがって**ハッシュ再計算は未実施**である。検品ゲートが上記2つの入力行を UTF-8 の sha256 で再計算し、それぞれ先頭16/20 hex を issue 値と照合すればよい。式と入力値の特定は完了している。

## 2. 現行コード上の終端経路

### 2.1 `input_receipt_confirmed=True` でも `arrival_unconfirmed` になる条件

現行 `src/docich/hanjuku/flows/recall.py` の `_recall_dispatch_step` では、次の3段階が分かれている。

1. **送信**: `_journal['_recall_inputs']` と recall state の `request_trace` が一致し、必要な A 入力数を満たすと `input_sent=True`。この時点で `camp_recall_input_sent` を記録する。ここで分かるのは「同じラン・判断ID・実画面の帰還A送信記録」だけであり、ゲーム側受理ではない。
2. **受理の代理証拠**: `input_sent` 後に `screen.kind == 'map'` を観測すると `picker_closed=True`。通常はこれが帰還先ピッカー閉鎖＝受理の代理証拠になる。
3. **到着**: 受理後は `camp_recall_dispatched` とし、到着は名簿の実読みで確認する。`input_sent` だが `picker_closed` が取れない終端でも、`arrival_unconfirmed` なら宛先城の `roster_read_due` をアームする。

終端条件は `picker_closed`、非 stale 6観測、または stale bound のいずれか。`input_sent` が真で `picker_closed` が偽なら status は `arrival_unconfirmed`、理由文は issue と同じ「帰還A送信後の受理・到着を確認できず、未確認として保持」になる。したがって `input_receipt_confirmed=True` と `arrival_unconfirmed` の併存は矛盾ではなく、送信・受理・到着を分離する設計どおりの状態である。

### 2.2 本件の画面遷移が示す post-send coverage

issue JSON の終端 metric は:

- `stage: await_dispatch` / `steps: 6`
- `status/resulting_event: arrival_unconfirmed`
- `input_receipt_confirmed: true` / `input_sent: true`
- `picker_closed: false` / `interrupted: false`
- `recent_screens: ['unknown', 'unknown', 'map', 'map', 'map', 'map']`
- terminal decision の screen は `map`

現行コードでは `scene_covered` が無ければ、`input_sent` 後の `map` で `picker_closed=True` が立ち、`interrupted` も `scene_covered` も無ければ `dispatched`（受理）になる。実際には `map` を見ても `picker_closed=False` のまま `arrival_unconfirmed` になっているため、現行コード上は **`scene_covered=True` が立っていた経路**に一致する。

この挙動は回帰テスト `tests/test_hanjuku_recall_scene_resume.py` で明示されている:

- `test_post_scene_map_is_not_misread_as_picker_acceptance_or_goal_refusal`
  - `scene_covered=True` のまま6回 `map` を渡す
  - `camp_recall_unconfirmed` が出る
  - `camp_recall_dispatched` は出ない
  - `camp_recall_goal_failed` は出ない
  - verification は `status=arrival_unconfirmed` / `picker_closed=False` / `scene_covered=True`
  - 宛先の `roster_read_due` がアームされる
- `test_plain_map_return_without_a_scene_still_accepts_the_dispatch`
  - coverage の無い通常 `map` は `dispatched` になる

つまり、本件と同じ見た目（送信済み・被り後の map・picker_closed false・unconfirmed）はバグではなく、誤受理・宛先拒否誤計上を防ぐ fail-closed 設計としてテスト固定されている。

### 2.3 戦闘との関係

`_camp_recall_step` は battle 系画面、または `mem['battle']` 中の画面で `_mark_recall_interrupted` を呼ぶ。`_mark_recall_interrupted` は、receipt 未確認なら `recall_interrupted` を残すが、送信済みまたは matching receipt が既に journal にある場合は `scene_covered=True` を残す。これは「A を再送すると受理済み帰還を二重送信しかねない」ための区別である。

issue の直前決定は次の順序である:

1. 21:15:49-52 `battle_survival_select` → `battle_card_selected` → `battle_card_candidate` → `battle_card_announce_retry` → `battle_card_unclassified` → `battle_card_remaining_unconfirmed` → 白兵継続
2. 21:15:57 `battle_result` / `sortie_outcome`（将軍ヘルメス、敗北）
3. 21:15:57 `camp_recall_unconfirmed`（帰還対象はキャロット）

したがって、少なくとも時系列上は、キャロットの帰還 dispatch 検証中にヘルメスの戦闘が前面を取り、その戦闘終了直後の `map` で bounded terminal に達した形である。`interrupted=False` は「横取りが無かった」意味ではない。post-send coverage では `recall_interrupted` ではなく `scene_covered` が使われ、terminal metric の `interrupted` は前者だけを見るためである。

### 2.4 `battle_card_unclassified` との関係

`battle_card_unclassified` は `src/docich/hanjuku/judgment/battle.py` の戦闘内カード消費確認ロジックだけで記録される。選択済みカードの使用告知を確認できないまま救済切り札一覧を閉じたため、消費確定せず `card_use_unclassified` として残すものである。recall state、`_mark_recall_interrupted`、`scene_covered`、`_recall_dispatch_step` を直接参照・更新しない。

したがって関係は**間接的・時間的なもの**にとどまる:

- 直接の原因: 戦闘が前景を取り、帰還ピッカー検証の画面証拠を覆ったこと
- `battle_card_unclassified`: その戦闘内でカード消費確認が取れなかった独立した不確実性。戦闘の長さや進行には関与し得るが、帰還未確認の状態機械へ直接作用しない

`battle_card_unclassified` を帰還未確認の原因決定として扱う必要はない。

### 2.5 監査 projection の限界（結論は変えない補助観測）

現行 `month_audit._camp_event` は `input_receipt_confirmed`、`picker_closed`、`interrupted`、`mode_lost`、`recent_screens` などを抽出するが、`scene_covered` / `scene_covered_tick` / `unsent_proven` / `stale_steps` / `resends` / `last_screen_kind` は抽出しない。`_summarize_flow` も `recall_verification` を `stage/status/steps` 中心へ縮めるため、issue JSON から `scene_covered` の実値を直接読めない。

本件ではそれでも、`map` 観測済みなのに `picker_closed=False` で終端した形から post-send coverage 経路を現行コード上たどれる。ただし、issue だけで第三者が再検証しやすいかという観点では projection が弱い。これは観測性の残件であり、今回の runtime 挙動の結論 (b) を覆すものではない。

## 3. #5722 / PR #5729 との異同

#5722 は:

- 判定 `camp_recall_failed`（失敗確定）
- 決定 `camp_boss_redirect_aborted`
- status `boss_jump_failed`
- `input_receipt_confirmed: False`
- ch9 boss redirect で、帰還先選択中の RECALL Y jump を二重起動し、per-redirect budget を使い切って abort した事前段階の欠陥

PR #5729（v1577、マージ済み）は、その事前段階に対して `recall_y_jump_transition_step` を追加し、active な RECALL Y jump がある `map_target` を再処理しないようにした。現行コードにも同ガードがあり、`_boss_redirect_dest_step` / `_boss_move_dest_step` / dest 段で使われている。

本件 #5783 は:

- 判定 `camp_recall_unconfirmed`（確認不能）
- 決定 `camp_recall_unconfirmed`
- status `arrival_unconfirmed`
- `input_receipt_confirmed: True`
- 帰還先は boss ではなく ch9 の集合先 `イデ！`
- Y jump の二重起動で abort する前段階ではなく、目的地確定 A の送信後に受理・到着を確認できない後段階

したがって #5729 の修正では**カバーされない別経路**である。しかも本件ランは `v1588` で、#5729 の `v1577` より後なので、#5722 型なら修正後に再発したことになるが、提示値はむしろ最初から別機構であることを示している。

## 4. 結論の分類

**(b) 設計どおり。**

理由:

1. `input_receipt_confirmed=True` と `arrival_unconfirmed` の併存は、送信・受理・到着を分ける現行契約の通常形である。
2. 送信後に戦闘/場面が覆った場合、後続 `map` を受理と誤認しないよう `scene_covered` で保持し、`picker_closed` を立てないのは現行の明示設計である。
3. その場合でも bounded window の終端では `camp_recall_unconfirmed` / `arrival_unconfirmed` を記録し、宛先 roster read を予約し、`camp_recall_goal_failed` には計上しない。回帰テストがこの exact shape を固定している。
4. `battle_card_unclassified` は戦闘内カード消費の独立した未確認であり、帰還 state を直接操作しない。
5. #5722/#5729 は boss redirect の Y jump 二重起動という送信前失敗であり、本件の送信後 `arrival_unconfirmed` とは別経路である。

誤検知 (c) ではない: 実際に受理・到着を確認できなかったという意味では監査の観測は真である。実欠陥 (a) とも判定しない: 現行コードはこの曖昧性を受理と断定せず、回復確認へ繋ぐために意図的に未確認保持する。判定不能 (d) でもない: 本番ログなしでも、issue JSON の `map` / `input_sent` / `picker_closed=false` / 直前戦闘と現行コード・回帰テストの対応から、少なくとも現行設計上の終端経路は特定できる。

## 5. 未決・残件

- sha256 の実再計算は python3 実行承認却下のため未実施。式と入力は §1 に記録した。
- 本番ログがないため、`scene_covered` を立てた正確な画面（battle 系画面そのものか、`mem['battle']` 中の map/text か）と、A がゲーム内で実際に受理されていたかどうかは確定できない。ただしどちらでも「受理を確認できない」のは真であり、現行設計の終端であるという結論は変わらない。
- issue projection が `scene_covered` を落とすため、今後同型を issue 本文だけで即座に切り分けるには監査抽出の強化が有用かもしれない。今回は read-only 検証なので修正しない。

記録: issue #5783 への結論コメントは検品ゲートが行うため投稿しない。本ファイルは worktree 内に残すが、commit/push/PR は行わない。

## 検品ゲート確認（Muse、2026-11-28 12:20 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("9|camp_recall_unconfirmed|camp_recall_unconfirmed")[:16] = 83845131b227e2a9、SHA-256("g1190-ch1v9|1190|camp_recall_unconfirmed|camp_arrival_unconfirmed|[129, 125]|キャロット|1795814157")[:20] = 26cd544a4eaf295d9a1e。いずれも issue の値と一致する。
- 結論 (b) 設計どおりは、送信・受理・到着を分離する現行契約と、post-send coverage 経路を明示固定する回帰テスト（test_post_scene_map_is_not_misread_as_picker_acceptance_or_goal_refusal）で裏付けられている。#5722/#5729 は送信前の Y jump 二重起動という別経路であり、本件をカバーしない。
