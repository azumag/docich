# VERIFY-5782

対象: azumag/docich#5782「[半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: バグストーム (ch9 ボス)」

## 結論

**(d) 判定不能**

ボス戦終了時に `_boss_card_left_unused` がバグストームを未使用候補として検出したこと自体は、issue の emitter 条件と整合する。ただし、この issue は `hanjuku-chart-v1575-ch9-cliff-camp-recall` 由来で、#5749 / PR #5753（v1583）で追加された自己完結証拠がない旧形である。実携行 provenance、開封有無、damage gate、戦闘結果、終了HP、瀕死/劣勢、敵召喚の有無が残っていないため、「使うべき状況で開封・選択できなかった実欠陥」と「自傷札を温存する / 実際には携行していなかっただけの非欠陥」を切り分けられない。

## ハッシュ再計算

`python3` の実行は2回とも承認却下されたため、ハッシュ値そのものは未計算。検品ゲートが再計算できるよう、現行 main `src/docich/hanjuku/month_audit.py` の式と入力値を残す。

### fingerprint

現行 main の `_track_card_miss` は次を使う:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), 'card_miss',
                                     [card, str(row.get('castle') or '')])
```

`_fingerprint` は:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw.encode('utf-8')).hexdigest()[:16]
```

issue の chapter は監査レコード上の整数 `9`（表示時だけ `ch9`）、card は `バグストーム`、castle は `ボス` なので、再計算入力は:

```text
sha256("9|card_miss|バグストーム+ボス")[:16]
```

issue 記載値: `799a4fd2f33a357a`

### occurrence / incident_id

現行 main の `_track_card_miss` は次を使う:

```python
raw = '|'.join(str(part) for part in (
    identity.get('runtime_id'), mem.get('chapter'), int(float(at)), card))
incident_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue の runtime_id は `g1190-ch1v9`、chapter は整数 `9`、card は `バグストーム`。
issue 開票日 2026-11-27 と時刻表示 `20:40:43Z` から、対象秒は `2026-11-27T20:40:43Z`、Unix time `1795812043` と読める。`_clock` は `datetime.fromtimestamp(..., UTC)` の時刻だけを表示し、`int(float(at))` は同じ秒内の小数を切り捨てるため、再計算入力は:

```text
sha256("g1190-ch1v9|9|1795812043|バグストーム")[:20]
```

issue 記載値: `b31d0116405f9bd41cef`

なお `gh` コマンドは指示により実行していない。issue 本文は公開 GitHub ページで確認した。

## 現行 main で確認した経路

### 1. この issue の `battle_end` は「攻撃へ進んだ判断」の直接記録ではない

現行 main には2系統の `battle_boss_card_missed` がある。

- 召喚戦メニュー内で `こうげき` 行を実際に選択するとき、携行残があり未使用なら `battle_menus.py` がその場で記録する。こちらは画面・文言・スクロール・卵状態・カーソル等を伴う。
- もう一方は `flows/battle.py` の `battle_end` にある事後検出で、`_boss_card_left_unused` がボス性と未使用残候補だけを見て記録する。本 issue は画面 `battle_end` であり、理由も「ボス戦で携行切り札を使わないまま戦闘が終了した」なので、後者である。

したがって本 issue は「卵なし・奥の手不可のときに切り札を開かず攻撃へ進んだ」というメニュー判断を直接証明しない。ボス戦が終わった後に、少なくとも候補として残っていた札を検出した、というだけである。

### 2. `card_flow: なし` は「一度も開かなかった」証拠ではない

旧形の emit には card_flow 履歴がない。`card_flow: なし` は emit 時点で active な `card_flow` を投影できなかったこと以上の意味を持たず、過去に一覧を開いて拒否された、選択されたが使用未確認だった、中断されて flow が解除された、といった経路を排除できない。

現行 main では `_card_miss_evidence` が `card_flow`、`card_flow_opens`、`card_flow_history`、開封/終了HP、damage gate、携行 provenance、瀕死/劣勢の材料を残す。監査側 `_card_miss_confidence` も、これらが欠ければ `low` / `auto_file: False` にし、証拠完備かつ敗戦・gate 許可・瀕死/劣勢・実携行 provenance がある場合だけ `high` / `auto_file: True` にする。本 issue のように証拠が無い形は、現行 main では high 自動起票にならない。

### 3. 現行 main でのバグストーム使用可否は状況依存で、単純な「使うべき」ではない

- バグストームは `hanjuku_reference.py` で「両軍の将軍、兵士のHP半減」の自傷札であり、`BOSS_SACRIFICE_CARDS` に属する。
- ボス wave の補強では `reference.sacrifice_general` に合格する弱い将軍だけがバグストーム候補を得る。バトウラは HP55・戦闘11・卵なしで、しきい値（最大HP50以下かつ戦闘5以下）を満たさないため、通常の犠牲札対象ではない。
- `SURVIVAL_CARDS` にもバグストームは入っていないため、救済メニューの通常候補として自動選択される札ではない。
- 一方で `_card_damage_gate` には、ボス/召喚戦で瀕死 (`_critical_survival_needed`) の場合に限り `critical_card_spend` として通常は拒否される札を許可する経路がある。したがって瀕死・劣勢だったなら使用評価が必要だった可能性を排除できず、非瀕死だったなら温存に近い。どちらだったかは本 issue から分からない。
- `_carried_opening_tactics` は実際の出撃 binding / launched snapshot に基づく confirmed carried cards を開幕使用しようとするが、`_carried_card_left` は `planned_cards` も残存候補にする。旧形には provenance がないため、「実携行だったのに開かなかった」のか「計画札を事後候補として数えただけ」なのかを分けられない。

### 4. せいめいたい戦であることは分かるが、敵召喚獣戦かどうかは分からない

`せいめいたい` は `BOSS_GENERALS` に含まれる ch9 のボス将軍（HP500）で、現行 main の `_boss_tactics_allowed` は ch9 ではパネルの `せいめいたい` / `ハデス` 読取をボス札使用の根拠にする。しかし issue の `enemy_summon_observed=unknown` は旧 emit の証拠不足であり、敵が卵を使ったか、卵戦メニューが開いたかは分からない。バトウラは参照表上は卵なしだが、奥の手が使えたかどうかも issue だけでは分からない。

## 同型検証との異同

### #5747（バグストーム ch1 けっかい）

#5747 は同じ旧形の `battle_end` card_miss で、(d) 判定不能とされた。理由は、旧 emitter が `cards_left` と `screen: battle_end` 程度しか残さず、監査が無条件に high で起票していたためである。本件は札は同じバグストームだが、章 ch9・城 ボス・敵 せいめいたい・将軍 バトウラ・時刻 20:40:43Z であり、#5747 の ch1 けっかい/クイーン/ヴィーナス/11:39:45Z とは別戦闘である。判定不能の構造は同じである。

### #5749 / PR #5753

#5749 は #5747 を受けた証拠拡充フォローアップで、PR #5753 は v1583 としてマージ済み。現行 main には既に反映されており、battle_end emit の自己完結証拠追加、bounded flow/gate/provenance、監査 confidence の tier 化が入っている。これは将来の判定を可能にする修正であり、v1575 で既に発火した本件の欠損証拠を遡って補うものではない。

### #5774（キャトルミュー ch2 ボス）

#5774 は v1585 以降の新形で、戦闘結果 loss、終了HP 0 vs 90、瀕死/劣勢、damage gate allowed、実携行 provenance、card_flow 履歴が揃っていた。そのため「ゲート評価後に選択へ進まず退却で一覧を閉じた」実欠陥として (a) になり、#5776/#5777 で修正済みと整理された。本件は v1575 の旧形で同等の時系列証拠がなく、#5774 の (a) をそのまま適用できない。札・章・将軍も異なる。

### #5781（ゼンマイン ch9 ボス、20:40:18Z）との関係

#5781 は同じ run `g1190-ch1v9`、同じ ch9 / 12-10 / 城 ボス / 敵 せいめいたい / side attack で、本件より25秒前（20:40:18Z）に終わっている。ただし #5781 の将軍はバタール、札はゼンマインで、本件はバトウラ、バグストームである。1つの戦闘には1人の自軍将軍が対応するため、両者は同一戦闘の別観測ではなく、別の battle_end である。近接したボス波状攻撃の連続した2戦闘、または同じ攻勢に属する別戦闘だった可能性はあるが、issue 本文だけではその上位の战役単位までは分からない。

#5781 も v1575 旧形であるため、検証コメントでは同じく (d) 判定不能になっている。本件も証拠構造が同じで、別経路の新 evidence は存在しない。

## 他の判定にできない理由

- **(a) 実欠陥** とするには、少なくとも実携行 provenance、敗北、瀕死/劣勢または使用必要性、gate 許可、開封/選択に進まなかった flow 履歴が必要である。本件にはいずれも無い。
- **(b) 設計どおり** とするには、バグストームを温存すべき非瀕死・非危険状態、または使用不適格が実測で分かる必要がある。バトウラが通常の sacrifice 対象でないことは温存方向の材料だが、瀕死ボス戦の `critical_card_spend` 例外を排除できない。
- **(c) 誤検知** とするには、計画札だけを数えた、実際には携行していなかった、開封後に正当拒否された、勝利/無傷だった、などの証拠が必要である。現存する issue だけでは確認できない。

## 未決の点

- 本番 decision log はローテーション済みで、issue 本文と現行 main だけでは実際の出撃 binding、カード一覧、HP遷移、敵召喚、奥の手可否を復元できない。
- ハッシュ値は `python3` 実行承認却下のため未計算。上記の式と入力値から検品ゲートで再計算する必要がある。

## 検品ゲート確認（Muse、2026-11-28 11:46 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("9|card_miss|バグストーム+ボス")[:16] = 799a4fd2f33a357a、SHA-256("g1190-ch1v9|9|1795812043|バグストーム")[:20] = b31d0116405f9bd41cef。いずれも issue の値と一致する。
- 結論 (d) 判定不能は、v1575 の旧形 issue に実携行 provenance・開封有無・gate 評価・戦闘結果/HP が残っていないことと、本番 decision log がローテーション済みであることで裏付けられている。#5781（ゼンマイン、20:40:18Z）とは将軍・札が異なり同一戦闘の別観測ではなく、別の battle_end である。
