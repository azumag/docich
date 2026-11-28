# VERIFY-5817 — ch4 四季王城(南西)へのキャンプ帰還未確認

## 結論

**(b) 設計どおり**。

本件の終端形は、送信済みの帰還Aが月初場面に覆われたため、後続の `map` を帰還先ピッカー閉鎖（受理）と誤認しないよう `scene_covered` で保持し、6観測の有界窓を使い切って `arrival_unconfirmed` として保持したものである。現行 main にはこの exact shape を固定する回帰テストがあり、#5783 / #5808 の検証済み判定と同じ分類である。誤検知ではないが、監査 projection が `scene_covered`・`general`・`boss_move` を落とすため、issue 本文だけでは本人帰属と被り証拠が読みにくいという観測性の残件はある。

## 1. fingerprint / occurrence の式と再計算結果

対象 builder は `src/docich/hanjuku/month_audit.py` の `_build_camp_incident`（現行 main）。`src/docich/hanjuku_speedrun/hanjuku/month_audit.py` にも同型の複製がある。

### fingerprint

`month_audit.py` L1152:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])
```

`_fingerprint` は L3543-3545:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

本件の入力値:

- `chapter`: `4`
- `kind`: `camp_recall_unconfirmed`
- `signals`: `['camp_recall_unconfirmed']`

したがって生文字列は次である。

```text
4|camp_recall_unconfirmed|camp_recall_unconfirmed
```

期待値（issue 本文）: `b8b3fd004c5b06a6`

### occurrence / incident_id

`month_audit.py` L1147-1150:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{event['reason_code']}|{event.get('camp')}|"
       f"{event.get('general')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSON の値から復元した入力値:

- `identity.runtime_id`: `g1373-b4c4126f`
- `identity.generation`: `1373`
- `event.kind`: `camp_recall_unconfirmed`
- `event.reason_code`: `camp_arrival_unconfirmed`
- `event.camp`: `[124, 160]`（Python list repr）
- `event.general`: `None`（issue JSON では null。f-string では文字列 `None`）
- `int(float(now))`: `1795840817`（issue JSON の `created_at` は `1795840817.1388452`）

したがって生文字列は次である。

```text
g1373-b4c4126f|1373|camp_recall_unconfirmed|camp_arrival_unconfirmed|[124, 160]|None|1795840817
```

期待値（issue 本文）: `57458f53d3840596762e`

### 再計算の成否

**未計算**。この環境で `python3` による SHA-256 再計算コマンドの実行承認が拒否されたため、ハッシュ値自体は再計算していない。上記の式と入力値を検品ゲートで再計算すること。式の読み取り自体は現行 main の `camp_recall_unconfirmed` 系 builder から行った。

## 2. `input_receipt_confirmed=True` かつ `picker_closed=False` の意味

これは矛盾ではない。現行 main では送信・受理・到着が分離されている。

- `src/docich/hanjuku/flows/recall.py` L2791-2802: `_recall_inputs` journal の `request_trace` と必要な A 入力数が一致したとき `input_sent=True` になる。これは**帰還Aが実送信された receipt**であって、ゲームが帰還を受理した証拠ではない。
- 同 L2822-2824: `picker_closed=True` は `input_sent` 済みで `screen.kind == 'map'` を観測し、かつ `scene_covered` が立っていない場合だけセットされる。
- 同 L2903-2911: 受理 `accepted` には `input_sent`、`picker_closed`、非中断、非 `scene_covered` が必要である。
- 同 L2925-2928: `input_sent` があるが受理条件を満たさない終端は `arrival_unconfirmed` と呼ばれる。ここでの `arrival_unconfirmed` は「到着失敗が確定した」意味ではなく、送信後に受理/到着を証明できなかった保守的な保持である。

本件では issue JSON に `input_sent: true` / `input_receipt_confirmed: true` / `picker_closed: false` / `interrupted: false` があり、decision 直前履歴には `camp_recall_scene_covered`（1795840730、理由「送信済み帰還のピッカーが月初場面に覆われたため、再送も受理も推測せず未確認のまま場面を進める」）がある。`scene_covered` は `camp_event` projection に出ないが、この decision が被りの証拠である。

被り時の設計も現行 main に明示されている。

- `orchestrator.py` L988-1005: `await_dispatch` 中の場面被りでは request/receipt を消さず、後続 map を picker acceptance と区別できないため証拠窓を covered として保持する。
- `orchestrator.py` L2241-2250: 送信済み帰還が月初場面に覆われた場合に `camp_recall_scene_covered` を記録する。
- `recall.py` L2771-2776: `scene_covered` 後は `map` / `map_target` / `world_map` だけが検証窓を進め、text 等は所有者へ返す。
- `recall.py` L2900-3042: 有界窓の終端で `camp_recall_unconfirmed` を記録する。
- `recall.py` L3043-3051: `arrival_unconfirmed` でも送信済みなら宛先 roster read を予約する。
- `recall.py` L3064-3086: `scene_covered` がある場合は `(camp, castle)` の拒否カウンタ `recall_goal_failures` に計上しない。

`tests/test_hanjuku_recall_scene_resume.py` L105-120 は exact shape を固定している: `scene_covered` のまま 6回 `map` を渡すと `camp_recall_unconfirmed` が出て、`camp_recall_dispatched` も `camp_recall_goal_failed` も出ず、`recall_verification.status == 'arrival_unconfirmed'`、`picker_closed == False`、宛先 roster read が予約される。本件はこの形状と一致する。

## 3. `target_cell [124, 124]` と野営 `[124, 160]` / 四季王城(南西)の対応

この2つを world cell 同士として直接比較してはいけない。

- `camp` は野営フローの `state.target` で、SELECT/visible camp から採った画面内の野営セルである。月次 task の recall では `post_month_status.py` L2380-2383 が `target` と `last_seen` に `camp['target']` を入れる。
- boss_move の目的地確定では `recall.py` L2326-2328 が `_request_recall(..., target_cell=list(marker), boss=True)` を使う。`marker` はピッカー画面上のマーカー位置であり、world cell ではない。
- `camp_world` は `recall.py` L2652-2663 の `_camp_world_cell` で、画面カーソル・屋根アンカー由来の origin・画面 target から初めて world cell へ変換される。本件は `camp_world: null` なので、この変換は成立していない。

したがって `[124, 160]` と `[124, 124]` は、少なくともこの経路では画面上の野営位置と目的地マーカー位置として読むべき値である。x が同じで目的地が36px上にあることは、南西サイト方向へマーカーを置いた画面内関係と矛盾しない。`hanjuku_chart.py` L166-170 の四季王城(南西) `(130, 893)` は world/chart cell であり、この screen-space `target_cell` と直接比較するものではない。

## 4. #5815 / #5816 との関係

同月・同ランの野営放置とは関係しうるが、本件の帰還未確認の将軍がピオーネまたはタピオカであるとは読めない。現行コード上はむしろ別の野営である可能性が高い。

- #5815（ピオーネ）: 城候補は `四季王城(北西)`、3ヶ月、HP 46/46。既に投稿された検証では (d) 判定不能で、`months <= 3` の boss-site 包囲免除側であること、ただし boss_move の本人帰属は証明できないことが確認されている。検証コメントでも本件 #5817 は南西向けで測定サイトが異なると切り分けられている。
- #5816（タピオカ）: 城候補は `ビクトリア`、3ヶ月、HP 50/50。ビクトリアは ch4 boss site ではないため通常 recall 対象だが、既存検証は (d) 判定不能である。
- 本件 #5817 の宛先は `四季王城(南西)` である。`post_month_status.py` L168-188 の `_boss_move_goal` は roster marker の `castle_candidate` を boss_move goal として引き継ぎ、L271-275 がその goal を task へ入れる。task 実行では L2392-2393 が `boss_move` と `goal` を recall state へ引き継ぐ。したがって boss_move 経路なら宛先は本人の測定サイトに対応する。ピオーネ（北西）やタピオカ（ビクトリア）から南西宛てが直接出る経路ではない。
- world JSON には `4-B1` / アポロン / target `四季王城(南西)` / status `en_route` があるため、南西サイトに関係する別の野営（候補としてはアポロンを含む）だった可能性はある。しかし本番 decision log はローテーション済みで、issue 本文と現行コードだけでは本人を確定できない。

`将軍: 不明` は必ずしも匿名野営を意味しない。`recall.py` L3004 は terminal metric の `general` を `state.get('general') if state.get('fort') else None` とし、fort 経路以外では名前を落とす。また `month_audit.py` L529-569 の flow projection も recall の `general` / `goal` / `boss_move` / `scene_covered` を残さない。これは観測性の欠如であり、本人がピオーネ/タピオカだった証拠ではない。

## 5. #5783 / #5808 との異同

- #5783（ch9 キャロット、宛先 イデ！）は (b) 設計どおりと検証済み。送信 receipt・picker closure・到着確認の分離、`scene_covered` による保守的保持、projection が `scene_covered` を落とす残件が確認されている。
- #5808（ch12 ボス）は同じく (b) 設計どおりと検証済み。ボス宛て（boss_move 経路）で、`scene_covered` のまま 6回 map を観測して `picker_closed=false` / `arrival_unconfirmed` となる exact shape が `tests/test_hanjuku_recall_scene_resume.py` に固定されている点が明示されている。
- 本件は宛先が ch4 `四季王城(南西)` で、同月に camp_abandoned の #5815/#5816 が併存する点が異なる。しかし終端の分類に効く構造（送信済み・月被り・picker_closed 不成立・有界窓終了）は #5808 と同型である。宛先が四季王サイトであることは boss_move 経路の設計範囲であり、分類を変える根拠にはならない。

## 残件（修正しない観測性の問題）

- month_audit projection が `scene_covered` を落とすため、issue 本文だけでは被りを即座に確認しにくい。本件では `decisions.before` に `camp_recall_scene_covered` があるため間接的には読めた。
- terminal metric が fort 以外の `general` を落とすため、月次 task で名前が分かっていた場合でも issue の `将軍` は `不明` になる。#5815/#5816 との本人照合が不可能になる。
- flow projection が `goal` / `boss_move` を落とすため、boss-site への再移動と通常帰還が issue 上で見分けにくい。
- boss宛てでは generic な宛先 roster read fallback が実効しにくい可能性は #5808 でも残件とされていた。本件の分類には影響しない。

## 検証範囲

- 本番 decision log はローテーション済みの前提で、issue 本文と現行 main のコードだけを使った。
- コード修正・テスト実行・commit・push・PR作成は行っていない。`VERIFY-5817.md` の作成のみ。

## 検品ゲート確認（Muse、2026-11-28 21:12 JST、現行 main b50041f1）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("4|camp_recall_unconfirmed|camp_recall_unconfirmed")[:16] = b8b3fd004c5b06a6、SHA-256("g1373-b4c4126f|1373|camp_recall_unconfirmed|camp_arrival_unconfirmed|[124, 160]|None|1795840817")[:20] = 57458f53d3840596762e。いずれも issue の値と一致する。
- 結論 (b) 設計どおりを維持する。現行 main のコードで次の3点を直接確認した。
  - recall.py は、送信済みの帰還Aが月初場面に覆われたときに `scene_covered = True` を立て、後続の map を帰還先ピッカー閉鎖（受理）と誤認しない。`scene_covered` は recall state に残り、terminal まで保持される。
  - `tests/test_hanjuku_recall_scene_resume.py` がこの exact shape を固定している。`test_scene_during_dispatch_preserves_the_sent_request_and_marks_coverage`（scene_covered True）、`test_a_recall_cut_three_times_is_skipped_instead_of_looping`（有界窓を使い切って skip）など。
  - 本件は #5808（ch12 ボス、(b) 設計どおり）と同型で、宛先が ch4 四季王城(南西) であることは boss_move 経路の設計範囲であり、分類を変える根拠にならない。
- 修正 PR は出さない。projection が scene_covered / general / goal / boss_move を落とす観測性の残件は、分類に影響しないため残件として記録するのみとする。
