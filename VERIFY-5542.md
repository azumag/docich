# VERIFY-5542 — [半熟英雄] 卵を残して敗北: ch9 カシュー（battle_loss_with_resources）

- Issue: azumag/docich#5542。`battle_loss_with_resources`（confidence: high、
  fingerprint `f601c5186ce59c70` / occurrence `374c46dec06bf8d95b20`）。
  ch9 8-4、観測 573回 / 132.2秒、ラン `g1190-ch1v9` / generation 1190 /
  `hanjuku-chart-v1498-recall-battle-mode-lost`。
- 戦闘: カシュー vs エストラゴン @ ハヤク、side=attack、outcome=loss、
  HP ally 0 / enemy 66。卵残数 4 / 切り札 選択[] 使用[] 残り[] /
  pending None / 退却選択 False。
- 検証 base: 現行 main `b33256a09`（`hanjuku-chart-v1511-status-panel-close`）。
  観測版 v1498 は `0193891d3`（`hanjuku-chart-v1498-recall-battle-mode-lost`）。
- read-only 検証。本番 SSH・本番入力・production code 変更は行っていない。
  VM 側の `hanjuku_decisions.jsonl` / フレーム画像は直接参照せず、issue 本文に
  埋め込まれた監査 incident JSON・観測版/現行コード・関連 VERIFY を照合した。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。AGENTS.md §1 に従い
  未読と記録し、GitHub と現行コードを根拠にした。

## 結論: **(c) 誤検知 — 卵残数 4 は stale 台帳系で、敗北時の実残数・使用可能行の証拠ではない**

issue の中核証拠である `卵残数: 4` は、敗北時に使える卵が 4 回残っていたことの
実測値ではない。現行 main でも `battle_result.observed_metric.egg_uses` は
`mem['egg_uses'][ally]` の最終観測台帳値をそのまま出し、戦闘中の卵選択・
召喚消費・破損では減算されない（§2）。したがって #5505 / #5536 / #5393 と
同じく、「台帳に正数が残っている」ことを「実残数がある」と読み違えた
stale 台帳系の誤検知である。

本件ではさらに、incident の直前120秒集計に `monster_menu_choice`:8、
`monster_skill_stall`:2、`battle_survival`:2、`battle_survival_select`:2、
切り札選択・効果確認系の記録がある。`monster_skill_stall` はコード上
owner=`ally` の味方召喚獣ターンでのみ emit される。ただし時系列には
`battle_result`:3 / `battle_start`:3 / `attack_defender_successor`:2 があり、
05:00:40Z に前の守備将軍を倒した後の継続戦闘へ移行し、05:00:42Z に
エストラゴン戦で敗北している。召喚獣技の理由文が将軍「ロックフォール」を
参照している一方、監査対象の敗北敵はエストラゴンなので、この味方召喚証拠は
**最終のエストラゴン戦そのものではなく直前の守備将軍戦へ帰属する**。
それでも少なくとも同じ攻城連続戦の直前段階で卵召喚が成立しており、台帳値 4 が
敗北時の実残数でないことは強く裏付ける。最終戦で何回分残っていたかまでは
issue だけでは確定できないが、「4回の使用可能卵を確実に残した」という
high-confidence 主張は成立しない。

`summon_egg_unavailable` latch は side=attack の本件では構造的に不発であり、
#5505 の防御側 latch 型ではない（§4）。`battle_survival_unavailable` は本 incident の
counts に存在せず、#5536 のような「実メニューで卵行が読めなかった」直接証拠も
ない（§3）。一方で、読める白い卵行を認識しながら使わなかった証拠もない。
したがって行動欠陥 (a) とは判定せず、監査誤検知 (c) とする。

---

## 1. fingerprint / incident_id（occurrence）の照合

### fingerprint

現行 main `src/docich/hanjuku/month_audit.py:3319-3321`:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では `_build_battle_resource_incident` が
`_fingerprint(mem.get('chapter'), event['kind'], [event['kind']])`
を使う（現行 main `month_audit.py:1509`、v1498 も同じ）。したがって raw は:

```text
9|battle_loss_with_resources|battle_loss_with_resources
```

Python3 で再計算した先頭16hexは `f601c5186ce59c70` で、issue の fingerprint と
一致した。`signals` 配列全体（`battle_loss_with_resources_with_egg` や
`battle_grind`）は fingerprint 入力ではない。

### incident_id / occurrence

月次一般の `_incident_id(identity, chapter, month)`（現行 main
`month_audit.py:3314-3316`）はこの kind では使われない。battle resource incident は
builder 内の固有式（現行 main `month_audit.py:1504-1507`）を使う:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue JSONの値を代入した raw は:

```text
g1190-ch1v9|1190|battle_loss_with_resources|egg|カシュー|I:c9:アルマムーン>ハヤク:1|1795582842
```

再計算した先頭20hexは `374c46dec06bf8d95b20` で、issue の occurrence /
incident_id と一致した。`now` は issue JSONの `created_at`
（`1795582842.2882931`）の整数部である。

## 2. 卵残数 4 は実残数ではなく最終観測台帳値

- 現行 main `flows/battle.py:1881-1886` は `battle_result` の metric に
  `egg_uses: (mem.get('egg_uses') or {}).get(cur.get('ally'))` をそのまま載せる。
  v1498 でも同じ行である。
- `state/egg_memory.py:127-144` の `current_uses` docstring は、battle や
  direct ledger update が卵を消費しても record refresh なしに ledger が古く
  なりうることを明示する。`battle_result` はこの reconcile 済み値すら使わず、
  raw の flat ledger を出す。
- 卵選択側は `strategy/economy.py:508-515` の `_egg_recheck` で将軍を再確認待ちに
  登録し、古い回数を dirty 扱いするだけで減算しない。
  `judgment/battle.py:3002-3006` の `_own_egg_empty` docstring も
  「Selection alone is not a use receipt」であり減算を捏造しないと明示する。
  逆に `judgment/battle.py:3038-3048` の `_own_egg_positive` は、
  `egg_recheck` 中の将軍について False を返し、古い正数を使用可能証拠にしない。
- owner 文書 `docs/operations/hanjuku-egg-conservation-20261015.md` も
  「卵を選択しただけで残数を減らしたり、選択を実消費と断定したりしない」
  という設計を固定している。

つまり `egg_uses=4` は最後に画面から読めた回数であり、戦闘中に召喚へ使われた
分を控除した実残数ではない。敗北すると名前行・出撃画面・野営再確認などの
再観測機会がないため、古い 4 がそのまま incident に残る。

## 3. `battle_survival_unavailable` の判定根拠と本件での位置づけ

`battle_survival_unavailable` は現行 main `flows/battle_menus.py:718-850` の
`_survival_menu` で、実際に開いた救済メニューの白文字 labels を見て、

1. 切り札を開く条件、
2. 白い `たまごをつかう` 行を選ぶ条件、
3. defense 限定の卵殻割り bait

のいずれも成立しなかったときだけ `:847-849` で記録される。台帳値そのものではなく、
実画面のメニュー行を根拠にする記録である。`labels` には灰色行が落ちるため、
使用済み・使用不能の卵行は通常ここに現れない。

ただし **本 incident の counts に `battle_survival_unavailable` は存在しない**。
#5536 と違い、本件では「最終戦で実メニューを開いたが卵行が読めなかった」
直接証拠はない。代わりに counts には `battle_survival`:2 と
`battle_survival_select`:2 があるが、最終30行の投影には現れず、直前120秒の
複数戦闘をまたぐ集計値にすぎない。`battle_survival_select` の
`observed_metric.label` も issue projection に出ないため、卵を選んだのか
切り札を選んだのかを issue だけで確定できない。

重要な帰属制限:

- 05:00:08Z〜05:00:34Z の `monster_menu_choice` / `monster_skill_stall` は
  05:00:40Z の最初の `battle_result` より前であり、理由文は将軍
  「ロックフォール」への実効ダメージを参照する。
- 05:00:40Z に `attack_defender_successor` が記録され、同時刻の
  `battle_start` が監査対象の継続戦闘（敵エストラゴン）を開く。
- 最終の `battle_result` は 05:00:42Z、screen_kind=map で、間に survival メニューの
  行は投影されていない。

したがって、味方召喚・survival 系の counts を最終エストラゴン戦の証拠として
そのまま使うことはできない。ただし、同じ攻城連続戦の直前守備将軍戦で
卵召喚が成立したことは、台帳値 4 が最新実残数でない独立証拠になる。

## 4. `summon_egg_unavailable` latch の関与: **なし（side=attack）**

現行 main `flows/battle_menus.py:3268-3271` で latch が設定される条件は、

- `battle.get('side') == 'defense'`
- 敵召喚獣行が1行観測
- 切り札残なし
- `empty_egg` または白文字 text に `たまごをつかう` がない

である。本件の `battle_event.side` は `attack` であり、 latch は構造的に
設定されない。v1498 でも同じ条件だった。したがって #5505 で留保された
「defense 側で一度 latch すると解除経路がない」型には帰属しない。

## 5. owner 方針・退却・即死帯との関係

- owner 方針の系譜は「退却する前に卵と切り札を使い切る」（#4999系）から、
  2026-11-23 の「退却一律禁止」へ進んでいる。現行 `_hero_retreat_menu`
  （`flows/battle_menus.py:701-711`）は `battle_retreat_banned` を記録し、
  切り札・卵を使い切った後もそのまま戦い切る。
- 本 incident でも `retreat_selected` は False であり、退却して資源を残した
  事象ではない。counts の `battle_retreat_banned`:2 もこの方針と整合する。
- `RETREAT_RESOURCE_FLOOR_HP=4`（`judgment/battle.py:1401`）は、即死帯では
  切り札待ち・遠回りを止める counterweight である。卵は生存手段なので
  危険時の確認対象だが、実メニューを開くには画面遷移と入力時間が要る。
- 最終のエストラゴン戦は投影上 05:00:40Z の `battle_start` から 05:00:42Z の
  `battle_result` まで約2秒で、途中 HP・メニュー開閉の詳細は issue にない。
  この短い継続戦で使用可能な白い卵行を見ながら意図的に捨てた証拠は構成できない。

## 6. 併記シグネチャと counts の解釈

incident の counts は直前120秒全体であり、単一戦闘の証拠ではない。本件では特に、

- `battle_result`:3 / `battle_start`:3
- `attack_defender_successor`:2
- `battle_card_effect_confirmed`:1
- `monster_skill_stall`:2

が複数の守備将軍との連続戦を示す。切り札効果確認や味方召喚は、監査対象の
最終 `battle_result` だけに帰属させられない。`battle_grind` もこの複数戦闘の
A連打・メニュー操作を集計したもので、root cause の独立証拠ではない。

## 7. v1498 と現行 main の差分

観測版 v1498 (`0193891d3`) と現行 main (`b33256a09`, v1511) を比較した。

- `_fingerprint` / `_incident_id`: 関数本体は同一。ただし本 kind の occurrence は
  builder 固有式であり、こちらも入力・出力の再計算が一致した。
- `_survival_menu`、`_egg_before_retreat`、`_own_egg_empty`、`_own_egg_positive`:
  関数本体は v1498 と現行 main で同一。
- `battle_result` の `egg_uses` 出力行: v1498 と現行 main で同一。
- `summon_egg_unavailable` latch の defense 条件: v1498 と現行 main で同一。
- `_battle_resources` / `_battle_resource_event`: 切り札 multiplicity と
  `cards_attempted` を `cards_unconfirmed_only` の証拠に加える変更はあるが、
  卵を raw `egg_uses` 正数で資源扱いする部分は不変。本件は切り札配列が空なので
  この差分は結論を変えない。
- v1505/v1507 の切り札前倒し、v1506/v1509 の HP1資源温存・監査は本件の
  敵HP66・切り札空の経路に直接関係しない。

したがって **現行 main でも同型の stale 台帳起票は再発しうる**。既存の修正で
カバー済みではないが、本検証の結論は実装欠陥ではなく監査の検知限界である。

## 8. 改善候補（本検証では実装しない）

結論は (c) なのでゲーム動作の修正は不要である。監査精度の follow-up 候補は:

1. `battle_result` metric に `egg_recheck` 所属、味方召喚成立の有無、
   最後に観測した卵行状態を bounded export する。
2. incident projection の decision rows に `battle_survival_select.observed_metric.label`、
   `battle_survival_unavailable.observed_metric.labels`、必要最小限の enemy/step/
   battle instance を残し、継続戦闘での帰属を可能にする。
3. 卵だけの `battle_loss_with_resources` では raw `egg_uses>0` を high confidence の
   live残数として扱わず、切り札の `cards_unconfirmed_only` に相当する検知限界
   として降格・注記するか owner 判断を求める。
4. `attack_defender_successor` を挟む複数 battle では、直前戦闘の resource use を
   最終 battle へ機械的に帰属しない一方、同じ攻城連続戦として参照できる戦闘系列IDを
   監査へ出す。

## 検証メモ

- 実行した確認: issue embedded incident JSON の読解、fingerprint / occurrence の
  Python3 再計算、v1498 `0193891d3` と現行 main `b33256a09` の関連関数差分、
  関連 VERIFY（#5393/#5505/#5531/#5536/#5470）との静的照合。
- ドキュメントのみの追加のため pytest は実行しない。秘密情報は記録していない。
- 本ファイル追加以外のコード変更・本番操作・ゲーム入力は行っていない。
