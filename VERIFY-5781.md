# VERIFY-5781 — card_miss ゼンマイン (ch9 ボス) read-only 検証

## 結論

**(d) 判定不能**

issue の事象は「ボス戦終了時にゼンマインが未使用として残った」ことを emitter が検出した可能性までは確認できるが、
現行 main の判定に必要な「実携行 provenance、開封有無、damage gate、戦闘結果/HP、瀕死・劣勢、card_flow 履歴」が
issue に残っていない。本番 decision log はローテーション済みで参照できないため、実欠陥・設計どおり・誤検知を
切り分けられない。

ただし、現行 main の設計では、ゼンマインを実際に携行し、実一覧で選択可能なボス戦で一度も開封・選択せずに
終わったなら、それは設計どおりではなく実欠陥側の事象である。問題は、この issue だけでは「実際に携行していた」
ことも「開封や選択へ進めなかった」ことも確定できない点にある。

## ハッシュ再計算

`python3` 実行は承認却下されたため、ハッシュ値そのものは再計算できていない。検品ゲートで再計算できるよう、
現行 main の式と入力値を残す。

### fingerprint

現行 main の `src/docich/hanjuku/month_audit.py` では、card_miss の fingerprint は次の2段で計算される。

- `_track_card_miss`: `_fingerprint(mem.get('chapter'), 'card_miss', [card, str(row.get('castle') or '')])`
  （`month_audit.py` 3903-3904 行）
- `_fingerprint`: `raw = f"{chapter}|{kind}|{'+'.join(signals)}"` を UTF-8 で sha256 し、先頭16 hex
  （同 3543-3545 行）

したがって本件の入力は次のとおり。

```text
chapter = 9
kind = card_miss
card = ゼンマイン
castle = ボス
raw = "9|card_miss|ゼンマイン+ボス"
expected = b7c0161f62f4f4d5
```

`mem.get('chapter')` が int の 9 でも f-string / `str` 変換後の raw は `"9"` になる。`ch9` ではない。

### occurrence / incident_id

現行 main の `_track_card_miss` は次の式である（`month_audit.py` 3827-3830 行）。

```python
raw = '|'.join(str(part) for part in (
    identity.get('runtime_id'), mem.get('chapter'), int(float(at)), card))
incident_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue は 2026-11-27 開票で、本文の時刻は 20:40:18Z。したがって `int(float(at))` の入力は
`1795812018`（2026-11-27 20:40:18Z）と読める。入力は次のとおり。

```text
runtime_id = g1190-ch1v9
chapter = 9
at = 1795812018
card = ゼンマイン
raw = "g1190-ch1v9|9|1795812018|ゼンマイン"
expected = bc96604d55a20f9bca06
```

## 現行 main で確認した経路

### 1. 本件の画面 `battle_end` は事後検出であって、召喚獣メニューで攻撃へ進んだ直接証拠ではない

現行 main では、ボス戦終了時に未使用の携行札があると `flows/battle.py` の `battle_end` が
`battle_boss_card_missed` を記録する（`flows/battle.py` 2080-2092 行）。判定部 `_boss_card_left_unused`
は、ボス性、`cards_used` が空であること、残存札だけを見る（同 1776-1809 行）。

一方、「卵なし・奥の手不可のときに切り札を開かず攻撃へ進んだ」直接の判断は、召喚獣戦メニュー側で
`こうげき` 行を実際に選択したときだけ記録される経路である（`flows/battle_menus.py` 3617-3671 行）。
その経路なら画面は召喚獣戦メニューであり、`scrolls`・`rearms`・`empty_egg` なども emit される。

本件は `screen = battle_end` で、`empty_egg=unknown`、`summon_egg_unavailable=unknown`、
`enemy_summon_observed=unknown`、scroll/rearm も不明。したがって、issue 本文の定型文にある
「卵なし・奥の手不可」は本件では独立には裏付けられていない。少なくとも、敵召喚獣戦で攻撃へ進んだ
直接証拠としては扱えない。

また `せいめいたい` は現行参照データで `BOSS_GENERALS` に含まれるボス将軍であり（`hanjuku_reference.py`
1383-1388 行）、最大HP 500（同 546 行）である。これは通常の敵召喚獣そのものではなく、ボス将軍パネル側の
相手として扱われる。

### 2. `card_flow: なし` は「一度も開かなかった」証拠ではない

`card_flow` の表示は、emit 時点で active な flow があるかだけを見る。renderer は dict でなければ
`なし` と出し（`month_audit.py` 4671-4677 行）、履歴は別項目 `card_flow履歴` に出す（同 4769 行）。

完了・中断・解放された flow は `cur['card_flow']` が None になるため、battle_end 時点で `なし` でも、
過去に開封した・開封を試みた・gate で拒否された可能性は残る。特に本件の bot version は
`hanjuku-chart-v1575-ch9-cliff-camp-recall` であり、後述の v1583 の証拠拡充より古い。この version では
`card_flow_history` / `card_flow_opens` / damage gate / provenance が issue に残らないので、
「記録なし」と「未観測・未開封」を切り分けられない。

### 3. ゼンマインはボスに効く札であり、現行設計では確認済み携行札を開幕で使う

ゼンマインは参照データで boss damage 50（`hanjuku_reference.py` 68-69 行）であり、
`BOSS_DAMAGE_CARDS` に含まれる（同 316-327 行）。したがって、バグストームのような自傷・条件付きの札とは
性質が違い、ボス戦で使うこと自体は設計に沿う。

現行 main の `flows/battle.py` では、実携行を確認できた未指定の札について `_carried_opening_tactics`
が開幕使用 tactic を追加する（`flows/battle.py` 572-676 行）。戦術ループは due な tactic に対して
`_begin_card_flow` し、B で戦闘メニューを開く（同 1073-1214 行）。

damage gate も、ボス将軍への正の固定ダメージを「非致死だが削りになる」として許可する経路がある
（`judgment/battle.py` 781-797、810-931 行）。実一覧でカーソルが読め、gate が許可すれば、
`card_list_step` は `select` イベントを残して A を送る（`flows/battle_menus.py` 2216-2270 行）。

このため、実際にゼンマインを携行し、一覧にもあり、ボス戦として認識されていたのに一度も開封・選択
しなかったなら、現行設計では実欠陥である。ただし、それらの前提を本 issue は証明できない。

### 4. 反対に、誤検知になりうる経路も残る

`_carried_card_left` は `cur['planned_cards']` を最初に見て、なければ confirmed kit を見る
（`flows/battle_menus.py` 1278-1293 行）。`planned_cards` は「この戦闘で使える計画札」であって、
古い issue には departure_binding などの実携行 provenance がない。chapter 9 では cursor-picked sortie で
binding が欠ける場合を補う特別経路があり（`flows/battle.py` 594-635 行）、binding 欠落時に planned と実携行が
ずれる可能性を排除できない。

現行 main の監査は、この問題を避けるため `carried_card_provenance` を要求し、
`planned_cards` / `order_plan` だけの provenance では high にしない（`month_audit.py` 3775-3782 行）。
しかし本件を生成した v1575 はその証拠を残していないため、実携行だったのか、計画だけの幻影だったのかを
後から判定できない。

## 過去の同型検証・修正との異同

### #5747 / #5749 / PR #5753

#5747（バグストーム ch1）は同じく battle_end の card_miss で、結論は (d) 判定不能だった。
理由は、emitter がボス性と未使用札だけを見て、監査が無条件 high で起票し、戦闘結果・HP・敵召喚・
provenance・damage gate・flow 履歴がなかったことである。

follow-up #5749 / 修正 PR #5753 は、その証拠不足を直した。現行 main では、

- `battle_end` / 召喚戦メニューの emit に outcome、開封/終了 HP、critical 材料、敵召喚、provenance、
  damage gate、flow 履歴を入れる
- 監査は証拠欠損を low、証拠完備でも非敗戦・gate 不許可・要否未証明を medium とし、
  敗戦 + gate 許可 + 瀕死/劣勢 + 実携行 provenance のときだけ high/auto_file にする

という構造になっている（`month_audit.py` 3760-3802、3805-3905 行、`_card_miss_evidence` は
`judgment/battle.py` 322-365 行）。

本件は v1575 で発火しており、PR #5753 の v1583 より古い bot version である。したがって、
#5753 は「次の issue から判定可能にする」修正ではあるが、本件そのものの証拠不足を遡って解消しない。
現行 main なら、この issue と同じ unknown だらけの投影は high/auto_file にならない。

### #5774 / PR #5777

#5774（キャトルミュー ch2）は v1585 で証拠が残り、(a) 実欠陥と判定された。その原因は、
damage gate 評価後に退却判断が切り札一覧を閉じ、選択へ進まなかった2段経路だった。

本件とは次の点で異なる。

1. 札が違う。ゼンマインは通常の boss damage 50 の札であり、レアイベント札キャトルミューではない。
2. 章が違う。現行 main の `_hero_retreat_open` は、chapter 9 かつ非主人公の将軍では退却確認を開かない
   （`flows/battle.py` 1493-1502 行）。本件の ally バタールは非主人公なので、#5774 の
   「退却のために card flow / list を閉じる」経路はそのままでは適用できない。
3. 本件には #5774 のような `gate_evaluation` 連続、`open:-/survival_menu`、開封/終了 HP、
   damage gate role/allowed の履歴がない。

PR #5777 は退却のための一覧クローズを削除し、低HP時の `nonlethal_egg_risk` 拒否を撤回した
（現行コードでは `flows/battle_menus.py` 2143-2145 行の契約コメント、`judgment/battle.py` 960-979 行）。
これは #5774 型には有効だが、本件が同型だったか、別経路（実携行の誤認、開幕 tactic 非成立、
unclassified 系 gate refusal、一覧/カーソル未読など）だったかを示す証拠は本件にない。

## 判定不能とする具体的な理由

- **(a) 実欠陥としない理由**: 実携行 provenance、開封有無、選択有無、gate 評価、戦闘結果/HP がない。
  実際に携行し選択可能だったなら実欠陥だが、その前提が未証明。
- **(b) 設計どおりとしない理由**: ゼンマインはボスに効く札で、確認済み携行札は開幕使用する設計。
  「温存が正しかった」とは言えない。
- **(c) 誤検知としない理由**: 計画札だけの誤検知である可能性はあるが、binding があって実際に
  2枚携行していた可能性も排除できない。誤検知と断定する証拠もない。
- したがって **(d) 判定不能**。

## 追加の観察

issue の `携行札候補: ゼンマイン, ゼンマイン` は複数枚の計画/候補があったことを示すが、古い issue には
provenance がないため、実際に2枚携行していた確証にはならない。現行 main の emitter でも
`cards_left` は planned list 投影であり、実携行の強い証拠は `carried_cards[*].provenance` 側で見る設計である。

また、base chart の ch9 ボス注文 `9-B1` は `マグネガキン, マグネガキン, ファバード` であり
（`hanjuku_chart.py` 1008-1011 行）、ch9 専用の `_TACTICS` もない（同 1269-1272 行）。したがって
ゼンマイン2枚が候補になった経路は、base chart の明示戦術ではなく、調整/interim/override/実携行補充などの
別経路だった可能性が高い。issue には step と provenance がないため、この候補が実携行を表すのか、
binding 欠落時の計画投影なのかをさらに絞り込めない。

本件は #5777 マージ直後の時刻に開票されているが、decision を生成した bot version は v1575 であり、
#5753（v1583）や #5777（v1586/v1587）を含む version ではない。version 表記を根拠に、これらの修正が
この発火時点の実行コードへ反映済みだったとは扱わない。

## 検証範囲外・未実施

- 本番 decision log はローテーション済みのため参照していない。
- `python3` 実行は承認却下されたため、sha256 の再計算とテスト実行は未実施。
- gh コマンド、git 書き込み、commit/push/PR 化は実施していない。
- 変更はこの `VERIFY-5781.md` のみ。

## 検品ゲート確認（Muse、2026-11-28 11:19 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("9|card_miss|ゼンマイン+ボス")[:16] = b7c0161f62f4f4d5、SHA-256("g1190-ch1v9|9|1795812018|ゼンマイン")[:20] = bc96604d55a20f9bca06。いずれも issue の値と一致する。
- 結論 (d) 判定不能は、v1575 の issue に実携行 provenance・開封有無・gate 評価・戦闘結果/HP が残っていないことと、本番 decision log がローテーション済みであることで裏付けられている。#5753（v1583）の証拠拡充は次の issue から判定可能にするもので、本件の証拠不足を遡って解消しない。
