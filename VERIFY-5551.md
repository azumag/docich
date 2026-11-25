# VERIFY-5551 — [半熟英雄] 卵を残して敗北: ch6 ヘパイストス（battle_loss_with_resources）

- Issue: azumag/docich#5551。`battle_loss_with_resources`（confidence: high、
  fingerprint `cc4bdb93abdefdfe` / occurrence `9a61b454dde867e6c30b`）。
  ch6 12-1、観測 453回、ラン `g1342-1fe0734c` / generation 1342 /
  `hanjuku-chart-v1499-adjusted-stock-fallback`。
- 戦闘: ヘパイストス vs バタール、城 不明 / side 不明、outcome=loss、
  HP ally 0 / enemy 10。卵残数 4 / 切り札 選択 [`クースカン`, `ノリウツール`]
  使用 [`クースカン`, `ノリウツール`] 残り [] / 退却選択 False。
- 検証 base: 現行 main `17af7ecba`（v1513、本 checkout の HEAD = origin/main）。
  観測版は v1499 = `127168a54`（`493a46663~1`。`493a46663` が version.py を
  `hanjuku-chart-v1499-adjusted-stock-fallback` → `v1500-boss-wave-stock-cards`
  へ bump しているため、その親が観測版ツリーに一致）。
- **環境制約**: 本環境では `python3`・`sha256sum`・`openssl`・`gh`・
  `git checkout/branch/commit` がすべて拒否される
  （VERIFY-5505/5536/5318/5202 と同型）。したがって sha256 の再計算、
  issue #5551 本文の直接取得、`verify/5551-battle-loss-resources`
  ブランチの作成・本ファイルのコミットは **未実施**。`git log/show/diff` 等の
  読み取り系は実行可。決定列・数値は依頼提示値を使用
  （issue の直前決定抜粋は提示されていないため、§4 は構造照合＋既知の留保）。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の
  事実を根拠に調査。

## 結論: **(c) 誤検知（#5393 / #5318 / #5505 / #5531 / #5536 と同型の stale 台帳系）**

issue の主張「使用可能な卵を残して敗北した」は、incident に内包された証拠では
成立しない。`battle_result.observed_metric.egg_uses` は実残数ではなく
`mem['egg_uses']['ヘパイストス']` の最終観測台帳値であり、戦闘中の選択・消費・
破損では減算されない明示的設計（v1499 も現行 main も同一出力部、§2）。
切り札は選択2枚がともに使用確定済みで残り [] — 資源判定は卵のみ。
side / castle がともに None（路上戦）のため、防衛戦専用の
`summon_egg_unavailable` ラッチや defender 系奥の手 preempt は構造的に不発であり、
卵行の生死は `_survival_menu` の labels / `indep_menu_action=use_egg` の
screen.text / `_white_egg_available` 実測だけが測る。これら全経路は
**白い `たまごをつかう` 行を実測した場合に必ず卵選択（または use_egg 昇格）へ
進む**構造であり、読める白行をスキップするコード経路は v1499 にも現行 main にも
存在しない。したがって「使える卵を見送った」とは証明できない。

**残留する限定留保**: `battle_survival_unavailable.observed_metric.labels` /
各メニュー観測の labels と当該フレームは `hanjuku_decisions.jsonl` /
`hanjuku_frames/` にのみ残り、issue JSON には投影されない。白い卵行の単発 OCR
脱落が `rescue['exhausted']` を latch し卵を一度も試さず敗北する理論上の (a) 窓は
残るが、(i) `exhausted` 後も ally HP≤12 で `battle_survival_critical_recheck`
による最後の実メニュー再観測が1回ある（flows/battle.py:1197-1210）、
(ii) 白い卵行を測定した場合は保存済みの卵0判断・indep=attack 判断すら画面優先で
覆す経路がある（battle_menus.py:1332-1347）ため、「継続して白く読めたのに
使わなかった」証拠は issue 単体では構成できない。#5393/#5318/#5505/#5536 が
留保したのと同じ境界。

---

## 1. fingerprint / incident_id の式照合（再計算は環境制約で未実施）

### fingerprint

`src/docich/hanjuku/month_audit.py:3319-3321`（現行 main）:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では
`record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])`
（`_build_battle_resource_incident`、:1509）のため、本件の raw は

```
"6|battle_loss_with_resources|battle_loss_with_resources"
```

→ sha256 先頭16hex が `cc4bdb93abdefdfe` に一致するはずの式。
桁数（16）は一致。**issue 報告値が #5505（ch6・同 kind）の fingerprint と
完全一致**しており、同一 raw 入力に同一出力が出る式上の整合として裏付けられる。
`python3` / `sha256sum` / `openssl` すべて拒否のため **再計算できず**。
v1499（`127168a54`）でも同一式（`git show` で :3286-3289 を確認）。

### occurrence（incident_id）

`_build_battle_resource_incident`（:1504-1507、v1499 では :1478-1481、同一式）:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

入力: `runtime_id`（ラン `g1342-1fe0734c` 系）、`generation`=1342、
`kind`=`battle_loss_with_resources`、`resources`=`'egg'`（卵のみ —
切り札残り [] のため 'card' は resources に入らない。タイトル
「卵を残して敗北」と整合）、`general`=`ヘパイストス`、
`step`=battle_result の `chart_step`（路上戦では行軍中の sortie step に束縛。
issue 未記載）、`now`=seal 時刻。20hex の桁数は一致するが、`step` と `now` が
issue だけでは再構成できず **再計算不能**。
なお月次系用の `_incident_id(identity, chapter, month)`（:3314）は本 kind
では使われない。

## 2. 卵残数 4 の出所 — #5505 と同じ stale 台帳系

### 監査側: 台帳値（最終観測値）、実残数ではない

- `flows/battle.py:1882`（現行 main）/ v1499 では :1797 —
  `battle_result` の `observed_metric.egg_uses` は
  `(mem.get('egg_uses') or {}).get(cur.get('ally'))` の**生台帳値**。
  `egg_memory.current_uses`（dated record との min 照合）すら通さない。
- `mem['egg_uses'][name]` の書き込み点は実画面観測のみ:
  `flows/prompts.py:918-930`（出撃画面 `card_select`/`sortie_confirm` のたまご行）、
  `flows/camp_recheck.py:484-495`、`flows/house.py:839`、
  `flows/egg_recovery.py:73,327`（回復数量）、および
  `judgment/battle.py:3023-3035` `_egg_quantities_unknown` の一括無効化。
  **選択・召喚・破損による減算経路はコード全体に存在しない**
  （`_own_egg_empty` docstring :3003-3006「Selection alone is not a use
  receipt」; `egg_memory.current_uses` docstring :130-131）。
- したがって「この戦闘・以前の戦闘で卵を選択→消費→未再観測」または
  「卵が破損したが回数だけ残った」場合、`egg_uses=4` のまま敗北記録が
  作られる。監査が数えるのは**台帳値であって実残数ではない**。
  路上戦の将軍は出撃画面・野営・ハウス・回復を踏まないため最終観測から
  任意に古くなりうる — #5505/#5318 と同じ構造。

### 判定側: 実画面・実メニューのみが卵使用を開放する

- `_own_egg_empty`（judgment/battle.py:3002-3020）: 台帳4 →
  `observed_uses(4)>0` → False（卵あり扱い）。ゆえに卵使用経路の
  **起動条件は満たす**が、実際の選択は全経路で白い行の実測が前提:
  - `_survival_menu`（battle_menus.py:718-850）の卵分岐は3箇所とも
    `'たまごをつかう' in labels`（白文字 span、os_layer `_options`）が前提
    （:730-731 `egg_ready`、:821、:826-828）。消費済み・破損の卵行は灰色で
    OCR から落ちる。全分岐が外れたときだけ
    `battle_survival_unavailable`（:846-849）を記録。
  - `indep_menu_action='use_egg'`（battle_menus.py:1545-1570）:
    `_behind` または combat_unknown で卵対処へ独立判断するが、:1556 で
    `'たまごをつかう' not in screen.text` なら `egg_row_dead` 記録（
    `situation_held` variant=`egg_unavailable`）して B で白兵へ戻る。
  - `_white_egg_available`（battle_menus.py:1332-1347）: 白行を実測すれば
    `egg_zero_owners` からの解除と indep attack→use_egg 昇格が行われる。
- **読める白い卵行をスキップする経路は存在しない。** 台帳4なのに卵が
  使われなかったとすれば、実画面で卵行が死んでいた（灰色/消費済み/破損）
  か、狭い OCR 脱落窓のどちらかであり、issue の証拠では前者を否定できない。

## 3. side 不明（路上戦）が切り分けるもの

issue の「城 不明 / side 不明」は `battle_result` の `castle=None` /
`side=None` に対応（month_audit.py:4298-4299 の `or '不明'` 描画）。
`judgment/battle.py:43-56` で `side=None, castle=None, step=<en_route>,
context='unclassified_location'` と返される**路上戦**（g1163 キールと同型）:
行軍中の将軍が城以外で敵将軍と遭遇した戦闘。step は行軍中の sortie step。

side=None により以下は**全て構造的に不発**:

| 経路 | 条件 | 本件での帰結 |
|---|---|---|
| `summon_egg_unavailable` ラッチ（battle_menus.py:3268-3271、v1499 :3244-3247） | `battle.get('side') == 'defense'` | side=None → 立たない。**defense 限定ラッチは適用外** |
| `_defender_summon_preempt`（judgment/battle.py:2843-2858） | `side not in ('defense','attack')` → return None | 不発 |
| `_defender_last_resort`（:2883-2891） | `side != 'defense'` → False | 不発 |
| `_defender_summon_last_resort`（:2918-2922） | `castle in chart.castles` | castle=None → 不発 |
| `_defender_egg_window`（:2743-2750） | `side != 'defense'` → None | 不発 |
| `egg_break_bait`（battle_menus.py:829-840） | `side == 'defense'` + castle | 不発 |

敵 バタールは `GENERAL_EGGS` で `has_egg=False`（hanjuku_egg_reference.py:110）
のため敵召喚獣は出ず、`egg_battle` / `card_summon_observed` 系の卵行死亡測定も
文脈外。つまり **#5505 で卵行死亡の構造的要件を担っていた防衛・召喚獣・奥の手
preempt の系は本件には一切存在しない**。卵行死亡の実測を担うのは
`_survival_menu` の labels、`indep_menu_action=use_egg` の screen.text、
`_white_egg_available` のみ — #5536（side=attack・survival_unavailable 型）に
近い形だが、本件は side=None でさらに狭い。

## 4. 切り札・退却方針・battle_result 決定・rescue メニューの関係

- 切り札: 選択 [`クースカン`, `ノリウツール`] がともに `cards_used` 入り
  （使用確定済み。`cards_used` は実使用の確認済み台帳であり、
  `_remaining_cards` の差分で残り []）。クースカン=敵将軍・兵士HP半減、
  ノリウツール=自HP半分ダメージ（hanjuku_reference.py:57-65）、いずれも
  卵落札でも召喚札でもない。**切り札側の「残し」は無い** — オーナー方針
  「卵・切り札を使い切る」の切り札側は完走済み。
- 退却方針: 2026-11-23 退却一律禁止（v1414 `e91428f7e`、#5230）が v1499 に
  含まれる。`_hero_retreat_menu`（v1499 `battle_menus.py:701-713` と同一を
  `git show` で確認）は常に `battle_retreat_banned` を記録して白兵へ戻す。
  `退却選択 False` は整合。よって監査が問うのは「退却して残した」ではなく
  「使える資源を残して死んだ」かのみ。
- `battle_result` の決定: `battle_end`（flows/battle.py:1614-1642、
  v1499 :1545-1557 と同一）はパネル消失2連続観測後に発火し、
  `ally_hp==0 and enemy_hp not in (None,0)` で `outcome='loss'`。
  ally 0 / enemy 10 は測定値として **loss は正しく決定されている**。
- rescue メニュー開示: 救済メニューは戦闘パネルが生きている間だけ開く。
  `_survival_needed`（judgment/battle.py:1416-1456）は hp≤12
  （`GENERAL_CRITICAL_RETREAT_HP`、:1391）等で True。ally HP が最終的に 0
  なので、途中で危険帯を通過した観測があれば `battle_survival_unavailable`
  （卵行なし実測）または `battle_survival_select`（卵行あり→選択）の
  いずれかが残るはず — だが issue 提示には決定抜粋がなく、どちらが記録
  されたかは `hanjuku_decisions.jsonl` でのみ確認できる（未取得）。
- `battle_survival_critical_recheck`: `exhausted`/予算消化後に hp≤12 の観測が
  あれば一度だけ実メニュー再観測（flows/battle.py:1197-1210）。HP が
  一度に 13→0 へ落ちた場合や `rescue['egg_attempted']` 済みの場合は不発。
  有無は issue 単体では確定できない — #5536 §2/#5393 と同じ留保。
- 卵行 OCR 欠落の可能性: #5393/#5318/#5505/#5536 と**同型**。
  単発の白行 OCR 脱落 → `rescue['exhausted']` latch → 卵未試行敗北の
  理論上の (a) 窓は残るが、上記の再観測・画面優先 override により
  「継続して白く読めたのに使わなかった」証拠は issue から構成できない。

## 5. 現行 main（v1513）での変更有無

`git diff 127168a54..HEAD`（v1499→v1513）で本件関連機構:

- `month_audit.py`: `_battle_resources` / `_battle_resource_event` は切り札側の
  multiplicity・`cards_unconfirmed_only`（`cards_attempted` を証拠へ追加、
  #5198）の変更のみ。**卵側の検出式（`egg_uses` 生台帳値→資源）に変更なし**。
  `battle_card_at_hp1` 新 kind（v1509、cd6d7a24d/#5581）は無関係。
  `stuck_march` シグナル修正（977370e01/#5180）は集計併記のみ。
  `_fingerprint` / incident_id 生成部 / `_build_battle_resource_incident` /
  `_track_battle_resources` に diff ヒットなし。
- `flows/battle.py`: `_egg_race_pressure` / `_hp1_hold_step`（v1505/1506）、
  battle_result metric への `cards_attempted` 系 export（#5198）。
  **`egg_uses` 出力部・outcome 決定・side/castle 伝播に変更なし**。
- `flows/battle_menus.py`: `_survival_card_list` の `cards_unclassified` 台帳
  （#5198）、`_hp1_resource_hold` フック3箇所（v1506）。
  `_survival_menu` の卵分岐・`battle_survival_unavailable`・
  `egg_battle_step`・`summon_egg_unavailable` ラッチに diff なし。
- `judgment/battle.py`: `_egg_race_pressure` / `_score_pressure` /
  `_hp1_resource_hold` / `_boss_battle`（v1505-1507）。本件は切り札使い切り・
  敵HP10・非ボス路上戦のため hp1_hold（敵HP1限定）は不発。
  **`_own_egg_empty`/`_own_egg_positive`/survival 判定/`summon_egg_unavailable`
  関連に変更なし**。
- その他: boss wave stock cards（#5544）、deploy route revalidation（#5348）、
  await_dispatch 関連 verify 等 — 本経路と無関係。

→ **本 incident と同型の起票は現行 main でも再発しうる**。切り札専用の
`cards_unconfirmed_only`→medium 降格に相当する「卵の消費確定不能は検知限界」
扱いが卵側に存在しないのは VERIFY-5318 §6 / VERIFY-5536 §7 で既出の
構造的な穴であり、本件もその再発例（#5300/#5246/#5318/#5393/#5505/#5531/
#5536 に続く7件目相当）。

## 6. #5505 との異同まとめ

- **同一 fingerprint**: chapter=6 + kind 一致で raw 入力が同じ — 同一
  fingerprint の再発として式上整合（§1）。
- **同型部分**: `egg_uses` が stale 台帳値である点、卵のみの資源判定、
  オーナー退却一律禁止下での敗北起票。
- **差異**:
  - #5505 は side=defense・城カバルッサ・召喚獣戦・切り札非携行・奥の手完走型
    で、卵行死亡が preempt/last_resort の**前提条件**として構造的に要求されていた。
  - 本件は side=None・castle=None の**路上戦**で、防衛・召喚獣系の卵行死亡
    測定は全て不発（§3）。卵行死亡の実測は survival/indep/白行 override のみに
    依存し、切り札2枚は使用確定済み。
  - ゆえに「使える卵を見送った」主張は #5505 よりさらに弱い — 構造的に
    卵行死亡を証明する経路が少ない一方で、白行を読める経路をスキップする
    コードも存在しない。

## 検証範囲・手順上の注記

- read-only 検証。`git show`（v1499 = `127168a54` ツリー）・
  `git diff 127168a54..HEAD`・現行ツリー読取の静的照合。
  VM の `hanjuku_decisions.jsonl` / フレーム画像・issue #5551 本文は未取得
  （`gh` 拒否）。pytest は `python3` 拒否のため未実行。
- `git checkout/branch/commit` 拒否のため `verify/5551-battle-loss-resources`
  ブランチ作成・本ファイルのコミットは**未実施**（VERIFY-5505/5536/5318 と
  同じ扱い。git 書き込みが許可される環境でコミットする）。push・PR・
  issue コメントは実施しない。
- issue 提示に直前決定抜粋がないため、§4 の生存メニュー記録
  （`battle_survival_unavailable` / `battle_survival_select` / `egg_unavailable`
  の有無）は構造照合で代替し、jsonl 直接確認は未実施と明記する。
