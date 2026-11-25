# VERIFY-5505 — [半熟英雄] 卵を残して敗北: ch6 リーキ（battle_loss_with_resources）

- Issue: azumag/docich#5505。`battle_loss_with_resources`（confidence: high、
  fingerprint `cc4bdb93abdefdfe` / occurrence `0334adb2e2c323b857f7`）。
  ch6 10-9、観測 749回 / 947.3秒、ラン `g1342-1fe0734c` /
  `hanjuku-chart-v1492-boss-only-card-miss-eligibility`。
- 戦闘: リーキ vs ガルバンゾー @ カバルッサ、side=defense、outcome=loss、
  HP ally 0 / enemy 30。卵残数 4 / 切り札 選択[] 使用[] 残り[] / pending None /
  退却選択 False。出撃: 確定 0 / 発射 0 / order_start 0 / order_launched 0。
- 直前の決定（提示抜粋）: `battle_okunote_summon_unconfirmed` → preempt →
  last_resort → select ×2 → combat_return。
- 検証 base: 現行 main `522fab6a6`（v1498、本 checkout の HEAD = origin/main）。
  観測版は v1492 = `cb1e21dc8`（PR #5499 のマージコミット）。
  v1492→main の差分（`git diff cb1e21dc8 origin/main`）で本件関連コードに変更なし:
  `month_audit.py` の `_battle_resources` / `_battle_resource_event` /
  `_build_battle_resource_incident` / `_fingerprint` / incident_id 生成部に
  diff ヒットなし。`flows/battle_menus.py` は +8行（`SUMMON_CARD_OPEN_LIMIT`、
  #5523、召喚戦の切り札再オープン上限）のみで本経路と無関係。
  `judgment/battle.py` は差分自体なし。以降の行番号は現行 main 基準。
- **環境制約**: 本環境では `python3`・`sha256sum`・`openssl`・`gh`・
  `git checkout/branch/commit` がすべて拒否される（VERIFY-5318/5202 と同型）。
  したがって sha256 の再計算・issue 本文の取得・ブランチ作成・コミットは
  **未実施**。VERIFY-5505.md は worktree 直下に作成済みだが、
  `verify/5505-battle-loss-resources` ブランチへのコミットは
  この環境では実行できない。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の
  事実を根拠に調査。

## 結論: **(c) 誤検知（#5393 / #5318 と同型の stale 台帳系）**

issue の主張「使用可能な卵を残して敗北した」は、incident に内包された証拠では
成立しない。`battle_result.observed_metric.egg_uses` は実残数ではなく
`mem['egg_uses'][ally]` の最終観測台帳値であり、戦闘中の選択・消費では
減算されない明示的設計。一方で preempt / last_resort の両方が**実画面で
卵行が死んでいた（おくのてのみ有効、または召喚戦箱に白い卵行なし）ことを
前提条件に要求**するため、提示された決定列は「使える卵を見送った」ではなく
「卵行が使えないと測定されたため、設計された最終救済（奥の手抽選）を完走し、
敗北した」と整合する。#5393（同じく egg_uses=4、(c)）・#5318（(c)）・
#5246（(b)）・#5300 と同じ検知限界。

**残留する限定留保**: `battle_okunote_summon_preempt.observed_metric.
resources_basis`（`observed_okunote_only` / `summon_menu_egg_unavailable`）と
各メニューの `labels` は `hanjuku_decisions.jsonl` にのみ残り、issue JSON には
投影されない。`summon_egg_unavailable` は一度 latch すると解除経路がないため、
白い卵行の単発 OCR 脱落が永久 latch し得る理論上の (a) 候補は残る
（§4 参照）。ただし白い卵行＋劣勢なら独立した `use_egg` 途中昇格経路が残り、
「一度も白く読めなかった」ことと「使える卵を捨てた」ことはイコールでない。

---

## 1. fingerprint / incident_id の式照合（再計算は環境制約で未実施）

### fingerprint

`src/docich/hanjuku/month_audit.py:3174`（現行 main）:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では
`record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])`
（`_build_battle_resource_incident`、:1380）のため、本件の raw は

```
"6|battle_loss_with_resources|battle_loss_with_resources"
```

→ sha256 先頭16hex が `cc4bdb93abdefdfe` に一致するはずの式。
桁数（16）は一致。sha256sum / openssl / python3 すべて拒否のため
**再計算できず**。v1492（`cb1e21dc8`）でも同一式（:3112-3113）であることを
`git show` で確認済み。

### occurrence（incident_id）

`_build_battle_resource_incident`（:1375-1377）:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

入力: `runtime_id`（issue のラン `g1342-1fe0734c` 系）、`generation`（int）、
`kind`=`battle_loss_with_resources`、`resources`=`'egg'`（egg のみ）、
`general`=`リーキ`、`step`=battle_result の `chart_step`（issue 未記載）、
`now`=seal 時刻（不明）。20hex の桁数は一致するが、`step` と `now` が
issue だけでは再構成できず **再計算不能**。
なお `_incident_id(identity, chapter, month)`（:3169、`runtime_id|generation|
chapter|month`）は月次系 incident 用であり、本 kind では使われない点に注意。

## 2. v1492 時点の退却方針と本経路の関係

- オーナー判断の資源使い切りポリシー: `771faaa51`（v1360、#4999/#5014、
  「退却の前に卵と切り札を使い切る」）→ `b2bdac097`（v1384、#5101/#5123、
  札を使い切るまで退却しない）→ `e91428f7e`（v1414、#5230、**退却一律禁止**、
  卵戦の退却プローブ撤去）。すべて v1492 より前なので v1492 に含まれる。
- 本件は `side=defense`・城 カバルッサ（`hanjuku_chart.py:423` で ch6 城を確認）。
  防衛戦の人間用コマンドボックスにはそもそも `たいきゃく` 行が存在しない
  （docs/hanjuku_egg_safe_melee.md:162「たまごをつかう(y192) と きりふだ(y208)
  の2行のみ」）。退却方針の対象ではなく、loss 経路に `SAFETY_FLOOR_HP` 系の
  免責もない（VERIFY-5202 §3: loss にフロアが無いのは設計どおり）。
  `退却選択 False` も整合。
- #5202（`battle_retreat_with_resources`、PR #5208 でカバー）との異同:
  あちらは outcome=retreat で HP 数値文字列が即死帯ゲートを素通りした
  **起票側の分類バグ**。本件は outcome=loss で起票条件そのものは正当に
  評価されており、問題は `egg_uses` の意味（実残数 vs 台帳値）にある。
  別型。

## 3. 卵残し敗北としての整合 — 決定内訳のコード対応

提示された決定列をコードに対応付けると:

| issue の記述 | decision | 記録箇所・意味 |
|---|---|---|
| `battle_okunote_summon_unconfirmed` | 同名 | `flows/battle_menus.py:1783`。召喚戦中の `battle_menu` で `summon_reading`/`summon_monster_panel` が本人HP・敵召喚獣・コマンドを同一戦闘に照合できなかった記録。有界 B/down（limit 3、超過で `unconfirmed_exhausted`＋通常判定復帰） |
| preempt | `battle_okunote_summon_preempt`（推定） | `okunote_step`:1881 または `_defender_summon_egg_menu`:2713。`enemy_summon_observed` が unconfirmed 時点で既にラッチしているなら `battle_okunote_egg_preempt` は `not enemy_summon_observed` を要求するため不整合（judgment/battle.py:2562） |
| last_resort | `battle_okunote_last_resort` | `okunote_step`:1909。`_defender_last_resort` / `_defender_summon_last_resort` いずれも `okunote_only_observed` 必須（judgment/battle.py:2704, 2731）＋劣勢測定（hp≤88、召喚獣に押し負け） |
| select ×2 | `battle_okunote_select` | :2026。`choice=おくのて`（親）＋候補選択、または2回の抽選 |
| combat_return | `battle_okunote_combat_return` | `okunote.combat_return`（flows/okunote.py:187）。`candidate_chosen`（候補確定フェーズ）必須 → **候補選択→実パネル復帰まで完走した奥の手抽選が少なくとも1回** |

### 奥の手と卵使用の関係

おくのては不可逆の自傷抽選（`OKUNOTE_MAX_SELF_DAMAGE = 88`、
judgment/battle.py:2499）。オーナー判断により「卵・札が**実メニューで**
使えないときだけ」開く設計（`_defender_summon_preempt` docstring、
owner 2026-11-02/03/04）。発火条件 `resources_free` は:

```python
resources_free = (cur.get('okunote_only_observed')
                  or ((_own_egg_empty(mem, cur) or cur.get('summon_egg_unavailable'))
                      and (cur.get('summon_egg_unavailable') or not cur.get('planned_cards'))))
```

（judgment/battle.py:2672-2674）

台帳 `egg_uses=4` では `_own_egg_empty` は False（`observed_uses(4)>0` →
`_own_egg_empty` False、judgment/battle.py:2815-2833）。よって発火には
`okunote_only_observed`（実メニューで「おくのてだけ」が有効 =
たまご/きりふだ/たいきゃく 行が全て灰色で OCR 脱落）か
`summon_egg_unavailable`（召喚戦箱に `たまごをつかう` が白く読めない、
battle_menus.py:3243-3247）＝**実画面による卵行死亡の測定**が必要だった。

### 卵が使えなかった理由（在庫・選択・戦闘段階）

- **在庫**: 台帳4は `_own_egg_positive`/`_dying_with_egg` 系の判定を
  「卵あり」側に傾けるが、使用経路の起動条件ではない。
- **選択**: 提示窓（120秒・最終30行）に卵選択記録なし。`battle_result` まで
  `egg_uses` 台帳値は4のまま。
- **戦闘段階（本質）**: 卵使用経路は2系統 — (i) `egg_battle_step` の
  `use_egg`（`action='use_egg'` は `'たまごをつかう' in screen.text` の
  白行が前提、battle_menus.py:3091付近。灰色なら `situation_held`
  variant=`egg_unavailable`＋attack フォールバック＋`egg_battle_row_dead`。
  なお `situation_held` は issue 決定表の noisy 除外集合に入り
  month_audit.py:3789 で省略されるため、非表示は発火の反証にならない）、
  (ii) `_survival_menu` の卵行選択（labels に `たまごをつかう` 必要）。
  **両経路とも白い卵行の実測が前提であり、preempt/last_resort の発火は
  その否定（卵行が死んでいた実測）を含意する。**
  コード上の答えは「在庫は台帳上残るが、この戦闘の実メニューでは
  卵行が使えないと測定されたため、卵経路は起動しなかった」。

## 4. #5318（(c) 誤検知）との異同・誤検知の検討

- **同型**: 台帳値を実残数とみなす検知限界。`egg_uses` は出撃画面・
  野営再確認・ハウス情報・回復数量といった画面観測でしか更新されず
  （VERIFY-5318 §1）、守備将軍はそれらを踏まないため最終観測から任意に
  古くなりうる。`_egg_quantities_unknown` や `egg_zero_owners` を通る
  明示的0化・`egg_recheck` の dirty 化も `battle_result` metric には
  反映されない。
- **差異**: #5318 は side=attack・`battle_survival_unavailable` 型
  （救済メニューに卵行が無かった記録）。本件は side=defense・召喚獣戦・
  奥の手完走型で、卵行死亡が preempt/last_resort の**前提条件**として
  構造的に要求される分、「使える卵を見送った」主張はさらに弱い。
- **誤検知の可能性として残るもの**: `summon_egg_unavailable` は一度 latch
  すると解除経路がない（battle_menus.py:3247 に代入のみ、pop/clear なし）。
  白い卵行の単発 OCR 脱落で latch すれば `resources_free` がその戦闘中
  維持され、preempt が奥の手へコミットし得る。ただし (i) latch 後も
  `egg_battle_step` の `use_egg` 途中昇格は独立に動き（3053-3070、
  attack→use_egg の一方向昇格。`empty_egg` は台帳4では False）、
  (ii) 候補確定には `おくのて` 行の可読が必要。したがって実害は
  「白い行が継続して読めたのに latch が残った」狭い窓に限られ、
  issue 単体では死んだ行と区別できない。判別には
  `hanjuku_decisions.jsonl` の `battle_okunote_summon_preempt.
  observed_metric.resources_basis` および該当フレームの menu labels が要る
  — #5393 が留保したのと同じ境界。

## 5. 補足・未実施事項

- `battle_okunote_summon_unconfirmed` が送る有界 B/down（limit 3）は
  照合不能な召喚戦箱への介入であり、卵行を消費させる機構ではない。
- `combat_return` 後の melee 敗北（ally HP 0 / enemy 30）は、
  奥の手抽選が不発/自傷を含めても設計された帰結。奧の手使用は資源消費として
  カウントされないため「資源を残した」判定には現れない（奥の手は卵・札ではない）。
- `git checkout/branch/commit` 拒否のため `verify/5505-battle-loss-resources`
  ブランチ作成・本ファイルのコミットは**未実施**（VERIFY-5318 と同じ扱い）。
  `gh` 拒否のため issue #5505 本文の直接取得も未実施 — 決定列・数値は
  依頼提示値をそのまま用いた。
- `python3`/`sha256sum`/`openssl` 拒否のため fingerprint・incident_id の
  再計算は未実施。式と入力値の照合のみ（§1）。
