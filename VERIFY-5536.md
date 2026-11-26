# VERIFY-5536 — [半熟英雄] 卵を残して敗北: ch8 キッシュ（battle_loss_with_resources）

- Issue: azumag/docich#5536。`battle_loss_with_resources`（confidence: high、
  fingerprint `c0fd484eed6de483` / occurrence `da3a464bf902c3003f11`）。
  ch8 7-7、観測 1053回 / 218.9秒、ラン `g1190-ch1v9` / generation 1190 /
  `hanjuku-chart-v1497-boss-audit-live-sortie-defer`。
- 戦闘: キッシュ vs ラビオリ @ ながはま、side=attack、outcome=loss、
  HP ally 0 / enemy 67。卵残数 4 / 切り札 選択[] 使用[] 残り[] /
  退却選択 False。
- 直前の決定（提示抜粋）: `battle_survival_unavailable`
  （「読み取れる未試行の救済手段がないため白兵へ戻る」）→ `battle_power`（A連打）。
- シグネチャ併記: `battle_loss_with_resources_with_egg` のほか
  「出撃開始後に発射されない」「戦闘が長引く」「出撃元の将軍がいない」。
- 検証 base: 現行 main `757f24fcd`（v1509、本 checkout の HEAD = origin/main）。
  観測版は v1497 = `2fe1b66f8`（PR #5375 のマージコミット。`0193891d3~1` の
  `version.py` が `hanjuku-chart-v1497-boss-audit-live-sortie-defer`）。
- **環境制約**: 本環境では `python3`・`sha256sum`・`gh`・
  `git checkout/branch/switch/add` がすべて拒否される
  （VERIFY-5505/5318/5202 と同型）。sha256 の再計算、issue 本文の直接取得、
  `verify/5536-battle-loss-resources` ブランチの作成・コミットは
  **未実施**。`git log/show/diff/status` 等の読み取り系は実行可。
  VERIFY-5536.md は worktree 直下に作成済みだが、ブランチへのコミットは
  この環境では実行できない（VERIFY-5505 §5 と同じ扱い）。
  決定列・数値は依頼提示値を使用。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の
  事実を根拠に調査。

## 結論: **(c) 誤検知（#5393 / #5318 / #5505 と同型の stale 台帳系）**

issue の主張「使用可能な卵を残して敗北した」は、incident に内包された
証拠では成立しない。`battle_result.observed_metric.egg_uses` は実残数ではなく
`mem['egg_uses']['キッシュ']` の最終観測台帳値であり、戦闘中の選択・消費・
破損では減算されない明示的設計（§2）。一方 `battle_survival_unavailable` は
**実際に開いてパースできた戦闘メニューで、白い `たまごをつかう` 行が
存在しなかった**ことの実測記録であり、台帳値 4 では `_own_egg_empty` は
False（§3）。読める白い卵行をスキップするコード経路は v1497 にも現行 main にも
存在しないため、「使える卵を見送った」とは証明できない。#5393（同じく
キッシュ・attack・egg_uses 台帳値・survival_unavailable、結論 (c)）、
#5318（attack・survival_unavailable、(c)）、#5505（defense・奥の手型、(c)）
と同じ検知限界。

**残留する限定留保**: `battle_survival_unavailable.observed_metric.labels`
（卵行が「灰色で落ちた」のか「OCR が白行を読み落とした」のかの区別）と、
当該戦闘での `battle_survival_select` / `egg_recheck` 所属 / `egg_battle` 系
記録は `hanjuku_decisions.jsonl` にのみ残り、issue JSON には投影されない
（VERIFY-5318 検品ゲート追記と同じ確認）。白い卵行の単発 OCR 脱落が
`rescue['exhausted']` を latch し卵を一度も試さず敗北する理論上の (a) 窓は
残るが、(i) `exhausted` 後も ally HP≤12 で `battle_survival_critical_recheck`
による最後の実メニュー再観測が1回ある（flows/battle.py:1197-1210）、
(ii) 白い卵行を測定した場合は保存済みの卵0判断すら画面優先で覆す経路がある
（battle_menus.py:1330-1347）ため、「継続して白く読めたのに使わなかった」
証拠は issue 単体では構成できない。

---

## 1. fingerprint / incident_id の式照合（再計算は環境制約で未実施）

### fingerprint

`src/docich/hanjuku/month_audit.py:3312-3314`（現行 main）:

```python
def _fingerprint(chapter, kind, signals):
    raw = f"{chapter}|{kind}|{'+'.join(signals)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

battle resource incident では
`record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])`
（`_build_battle_resource_incident`、:1509）のため、本件の raw は

```
"8|battle_loss_with_resources|battle_loss_with_resources"
```

→ sha256 先頭16hex が `c0fd484eed6de483` に一致するはずの式。
桁数（16）は一致。`python3` / `sha256sum` ともに拒否のため **再計算できず**。
v1497（`2fe1b66f8`）でも同一式であることを `git diff 2fe1b66f8..HEAD` で
確認済み（該当行に diff ヒットなし）。

### occurrence（incident_id）

`_build_battle_resource_incident`（:1504-1507）:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

入力: `runtime_id`（ラン `g1190-ch1v9` 系）、`generation`=1190、
`kind`=`battle_loss_with_resources`、`resources`=`'egg'`（卵のみ —
タイトル「卵を残して敗北」と `battle_loss_with_resources_with_egg`
シグナルが裏付け）、`general`=`キッシュ`、`step`=`battle_result` の
`chart_step`（ch8 ながはま攻撃のチャート step は `8-K1` —
hanjuku_chart.py:854-857）、`now`=seal 時刻。20hex の桁数は一致するが、
`step` の確定値と `now` が issue だけでは再構成できず **再計算不能**。
なお月次系用の `_incident_id(identity, chapter, month)`（:3307）は本 kind
では使われない。

## 2. 卵残数 4 の出所と `battle_survival_unavailable` 判定の食い違い

### 監査側: 台帳値（最終観測値）、実残数ではない

- `flows/battle.py:1881-1882` — `battle_result` の `observed_metric.egg_uses`
  は `(mem.get('egg_uses') or {}).get(cur.get('ally'))` の生台帳値。
  `observed_uses` / `egg_memory.current_uses` 照合も `egg_recheck` dirty
  ラッチの考慮もされない。
- `mem['egg_uses'][name]` の書き込み点は実画面観測のみ:
  `flows/prompts.py:890-901`（出撃画面のたまご行）、`flows/camp_recheck.py:484-495`、
  `flows/house.py:839`、`flows/egg_recovery.py:73,327`（回復数量）、および
  `judgment/battle.py:3023-3035` `_egg_quantities_unknown` の一括無効化。
  **選択・召喚・破損による減算経路はコード全体に存在しない**
  （`_own_egg_empty` docstring :3003-3006「Selection alone is not a use
  receipt」; `egg_memory.current_uses` docstring :130-131「battle and direct
  ledger updates can spend an egg without refreshing the record」）。
- したがって「この戦闘・以前の戦闘で卵を選択→消費→未再観測」または
  「卵が破損したが回数だけ残った」場合、`egg_uses=4` のまま敗北記録が
  作られる。監査が数えるのは**台帳値であって実残数ではない**。

### 判定側: 実メニュー labels の実測

- `_survival_menu`（`flows/battle_menus.py:718-850`、v1497 `2fe1b66f8` と
  同一を `git show` で確認済み）の卵選択は3分岐とも
  `'たまごをつかう' in labels` が前提（:730-731 の `egg_ready`、:821、:826-828）。
  `labels` は `_options(screen)`（os_layer/readers.py:45-47）の白文字 span。
  消費済み・使用不能の卵行は灰色で描かれ OCR から落ちる
  （os_layer/screen.py:1545-1548「A general whose egg is spent draws
  たまごをつかう greyed out, so it is not read」）。
- `battle_survival_unavailable`（:846-849）は全分岐が外れたときだけ記録。
  台帳 4 なら `_own_egg_empty` は False（judgment/battle.py:3002-3020:
  `observed_uses(4)>0` → False）。`rescue['egg_attempted']` は :861 でのみ
  立ち、必ず直後の `battle_survival_select`（:871）と対になる。
  → 発火時点で「`たまごをつかう` が labels に無い」か「先に卵選択済み」の
  どちらか。提示抜粋に `battle_survival_select` は無い（120秒窓・最終30行;
  それより前の選択は窓外の可能性は残る — §5）。
- 食い違いの構造: **監査は stale な台帳を数え、判定はその場の実メニューを
  測った**。両者が参照する証拠が違うだけで、コード上の矛盾ではない。

## 3. `summon_egg_unavailable` ラッチ（#5505 留保事項）の関与: **なし**

- ラッチの設定箇所は `flows/battle_menus.py:3268-3271`（v1497 では :3243-3247、
  同一コードを `git show` で確認）で、条件は
  `battle.get('side') == 'defense'` かつ敵召喚獣行が1行観測
  （`r.name in _MONSTER_SKILLS`）かつ切り札残なし かつ
  `(empty_egg or 'たまごをつかう' not in screen.text)`。
- 本件は **side=attack** であり、敵は将軍名 `ラビオリ`
  （hanjuku_reference.py:331・召喚獣ではない）のため、ラッチは構造的に
  立ち得ない。併せて `_defender_summon_preempt`（judgment/battle.py:2817-2873）
  も side=attack では `_own_egg_empty` を要求し（:2848）、台帳4では不発。
- ラッチに解除経路がない点は #5505 と同じ（代入のみ・pop なし、`mem['battle']`
  の破棄で戦闘単位の寿命）だが、**本件の発火経路ではない**。

## 4. オーナー方針・退却・即時敗北バンドとの関係

- 方針の系譜: 2026-11-22「退却する前に卵と切り札を使い切る」（v1360系、
  #4999/#5014）→ 2026-11-23「退却一律禁止」（v1414 `e91428f7e`、#5230）。
  v1497 時点では後者が有効で、`_hero_retreat_menu`（battle_menus.py:701-711）
  は常に `battle_retreat_banned` を記録して白兵へ戻す。`retreat_selected
  False` はこれと整合。
- 退却行の提示: side=attack の通常戦闘メニューには `たいきゃく` 行が
  ある（`_BATTLE_COMMANDS`、os_layer/screen.py:1053）。ただし一律禁止の
  ため選択自体は行われず、方針の現行適用は「退却して残す」ではなく
  「資源を使い切って戦い切る」。監査の `battle_loss_with_resources` が意味を
  持つのは「本当に使える資源を残したか」の一点のみであり、本件では
  実メニューが使える卵行を提示しなかった（§2）。
- 即時敗北バンド: 監査側の `SAFETY_FLOOR_HP=4` 免除は
  `outcome == 'retreat'` にのみ適用（month_audit.py:1382-1385）。loss には
  フロアが存在しないのは設計どおり（VERIFY-5202 で確立）。判定側の
  `RETREAT_RESOURCE_FLOOR_HP=4`（judgment/battle.py:1401）は早期救済の
  開放を止めるだけで、`_survival_needed`（critical ≤12、:1743-1747）は
  HP≤4 でもメニューを開ける。HP 推移の実値は決定ログ未取得のため未確認だが、
  `battle_survival_unavailable` が記録された時点でメニュー開放・読取の機会が
  あったことは確定しており、結論に影響しない。

## 5. #5505・#5393 との異同

- **同型部分**: 卵残数が stale 台帳値である点、切り札3項目が空（卵のみの
  資源判定）、side=attack（#5393/#5318 と同じ。ch12 キッシュ #5393 と
  同一将軍）、敗北直前に `battle_survival_unavailable` が実メニューを測った点。
- **`battle_survival_unavailable` で切り分けが変わるか**: 変わらない。
  同記録は「開いてパースできたメニューに使える卵行が無かった」ことの実測で、
  #5505 の `okunote_only_observed` / `summon_menu_egg_unavailable`（防御側の
  構造的要件）に相当する「実画面による卵行死亡の測定」を攻撃側で担う。
  ただし灰色行と OCR 脱落は区別できないため、理論上の (a) 窓（単発誤読→
  `rescue['exhausted']` latch）は残る。これは #5393 §2・#5318 §5 と同一の
  留保で、issue 単体では消せないが (a) を構成もしない。
- 差異: #5505 は防御側・召喚獣戦・奥の手完走型で卵行死亡が preempt の
  前提条件として組み込まれていた。本件は攻撃側の通常白兵で、卵行死亡の
  実測は `battle_survival_unavailable` の labels のみに依存する。

## 6. 併記シグネチャの帰属

`record['signals']` は `_build_battle_resource_incident`（:1438-1444）で
`[kind] + [kind_with_resource...] + _signals(context, counts, ...)` の順に
構成される。issue の「シグナル」欄はこの SIGNAL_LABELS 描画（:4465,4476）。

| 表示ラベル | signal 名 | 由来 | 同一戦闘との関係 |
|---|---|---|---|
| 卵/切り札を残して敗北 | `battle_loss_with_resources` | event kind | 本 incident 本体 |
| 卵を残して敗北 | `battle_loss_with_resources_with_egg` | resources=['egg'] | 同上 |
| 出撃開始後に発射されない | `order_start_without_launch` / `launch_confirmation_missing` / `departure_cancelled` のいずれか | 直近120秒の決定カウント（`_signals` :3203-3208） | 集計上の併記。計画・出撃系の文脈で、同一戦闘の属性ではない |
| 戦闘が長引く | `battle_grind` | `battle_count >= 20`（120秒窓の `battle_*`/`monster_menu_choice` 行数、:3217-3218） | この敗北戦闘の A連打・メニュー開閉が窓を埋めた可能性が高い（実質的に同一戦闘由来の集計） |
| 出撃元の将軍がいない | `no_source` | world.garrison の最大在城 ≤1（:3229-3232） | ワールド文脈の併記で、戦闘そのもののサインではない |

すなわち「同一戦闘に属するサイン」は `battle_grind` だけが高確度で、
他は月次・計画文脈の集計上の併記。

## 7. 現行 main（v1509）での変更有無

`git diff 2fe1b66f8..HEAD` で本件関連機構の差分:

- `month_audit.py`: `_battle_resources`/`_battle_resource_event` は切り札側の
  multiplicity・`cards_unconfirmed_only`（`cards_attempted` を証拠へ追加、
  #5198）の変更のみ。**卵側の検出式（`egg_uses` 生台帳値→資源）に変更なし**。
  `battle_card_at_hp1` 新 kind（v1509）、boss entry grace（v1497後）は無関係。
- `flows/battle_menus.py`: `battle_card_remaining_unconfirmed` ledger（#5198）、
  `_hp1_resource_hold`（v1506）のみ。`_survival_menu` の卵分岐・
  `battle_survival_unavailable`・`summon_egg_unavailable` ラッチに diff なし。
- `judgment/battle.py`: `_egg_race_pressure` / `_score_pressure`（v1505/1507、
  切り札前倒し）と `_hp1_resource_hold`（v1506）。本件は切り札非携行・敵HP67
  のためどちらも不発。**`_own_egg_empty`/`_own_egg_positive`/survival 判定に
  変更なし**。
- `flows/battle.py`: 上記に伴う呼び出し追加と `battle_result` metric への
  `cards_attempted` 系 export（#5198）。`egg_uses` 出力部に変更なし。

→ **本 incident と同型の起票は現行 main でも再発しうる**。切り札専用の
`cards_unconfirmed_only`→medium 降格に相当する「卵の消費確定不能は検知限界」
扱いが卵側に存在しないのは VERIFY-5318 §6 で既出の構造的な穴であり、
本件もその再発例（#5300/#5246/#5318/#5393/#5505 に続く6件目相当）。

## 検証範囲・手順上の注記

- read-only 検証。`git show`（v1497 ツリー）・`git diff 2fe1b66f8..HEAD`・
  現行ツリー読取の静的照合。VM の `hanjuku_decisions.jsonl` / フレーム画像・
  issue #5536 本文は未取得（`gh` 拒否）。pytest は `python3` 拒否のため未実行。
- 本ファイルは worktree 直下に作成済み。`git branch/switch/add` 拒否のため
  `verify/5536-battle-loss-resources` ブランチ作成・コミットは**未実施**
  （VERIFY-5505/5318 と同じ扱い。git 書き込みが許可される環境でコミットする）。
  push・PR・issue コメントは実施しない。
