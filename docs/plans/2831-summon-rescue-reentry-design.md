# #2831 / #2863 / #2864 単一基準設計 (救済再評価)

## 0. スコープと PR 構成

- 単一基準コミットを先頭に 1 本: 救済判定の共通述語を
  `src/docich/hanjuku/judgment/battle.py` に追加し、#2831/#2863/#2864 を統合。
- その後に回帰テスト (a)〜(d) 同梱 (native 画面再現)。
- #2836/#2844 は候補評価段に載せる (上位互換)。
- 処理順序: ①戦闘開始 → ②潮目変化 (召喚戦ループ含む) → ③戦闘境界 (reset_battle_controls)。

## 1. 問題 (g1059)

- 06:27 月次で卵全回復済み (在庫不足ではない)。
- 06:29:54 戦闘開始時に救済手段を確認 (一度きり)。
- 06:30〜06:36 の召喚戦は「きゅうしゅう」反復選択のみ。
- 06:36:24 1 戦目終了 (敗北)。#2864 ガード拒否文言なし。
- 06:36:29 2 戦目開始、不利判定あり。観測継続。
- 原本 #2831 の城攻め二連戦実例は証跡が取れない可能性が高い → native 再現で担保。

## 2. 単一基準述語

### 2.1 シグネチャ (レイヤリング遵守)

```
_rescue_decision(mem, cur, context) -> dict
```

- `mem`, `cur` のみ参照。**screen 引数なし** (judgment 層は画面非依存)。
- 画面観測は flows 側で抽出し `context` に渡す。
  `context = {'owner': <メニュー所有者 'ally'/'enemy'/None>,
              'ally_hp': int|None, 'enemy_hp': int|None,
              'panel': {ally,enemy}, 'summon': <reading or None>,
              'okunote_choices': <observed menu choices or None>}`
  `owner` は「現在の召喚獣メニューの所有者」を flows が識別したもの。
- 戻り値: `{'action': 'use'|'conserve'|'unknown', 'candidate': str|None,
  'reason': str, 'guard_rejected': [str]}`

### 2.2 評価タイミング (3 段)

| 段 | 呼び出し元 | 内容 |
|---|---|---|
| ①戦闘開始 | flows/battle.py battle_start 後 | _egg_plan + _survival_state 初期化。候補集合確定 |
| ②潮目変化 | flows/battle_menus.py monster_menu_step 冒頭 / battle_menu_step / egg_battle_step | HP/戦況悪化で再評価。召喚戦ループ中も含む |
| ③戦闘境界 | flows/battle.py reset_battle_controls | 1 戦目使用が 2 戦目を不当に抑制しない (carryover 解除) |

### 2.3 双方向

- 劣勢・危険 + 候補あり → `use` (#2831/#2864)
- 明らかに有利 → `conserve` (#2863)
- 未観測 → `unknown` (どちらにも倒さない。#2102 再発防止)

### 2.4 候補モデル (2 層)

**第 1 層: 手段レベル候補** (mem/cur の既存述語で判定)
- 卵: `_own_egg_needed(mem, cur)` / `_own_egg_positive` / egg_uses
- 切り札: `_card_damage_gate` / `_critical_safe_card` / planned_cards
- 退却: `_hero_retreat_needed` / `_default_retreat_needed`
- 奥の手: `_defender_last_resort` / `_defender_summon_last_resort` / `_defender_egg_window`

**第 2 層: 奥の手を開く場合の技単位事前確認** (self-damage 分類)
- 奥の手固有の自傷技は `os_layer/screen.py::OKUNOTE_SELF_DAMAGE_CHOICES`
  (`frozenset({'ヤケクソ'})`) で明示分類 (#2835 パターンに倣う)。
- `_is_self_damage_skill` はモンスター技用であり、奥の手の 'ヤケクソ' を
  捕捉できないため、別定数で分類する。
- 判定対象は flows が `context['okunote_choices']` 経由で渡した実観測の
  メニュー選択肢であり、静的タプルそのものではない。

**last_resort の semantics 維持**:
- `last_resort` は低 HP でも奥の手を開く「認可」(リスク受諾)。
- 本設計では last_resort を「guard_rejected 候補」として全拒否に巻き込まない。
- last_resort が成立する局面では第 2 層の自傷分類をスキップし、奥の手を開く。

**ガード (候補を絞る)**:
- 第 2 層で自傷系を除外 (guard_rejected に理由記録)。
- 安全候補が 1 つでも残れば `use`。
- 全候補拒否時のみ `unknown` + decision log 記録 + B で白兵へ戻る。

### 2.5 有界性

- `RESCUE_REENTRY_LIMIT = 2` (`HERO_SURVIVAL_REOPEN_LIMIT` 由来, judgment/battle.py:1184)
- `RESCUE_REARM_LIMIT = 2` (同上)
- ラッチ: `egg_retreat_tried` / `okunote_last_resort_recorded` / `survival.egg_attempted`

## 3. okunote フローの再構成

### 3.1 低 HP 拒否分岐 (battle_menus.py:757-765) の置き換え

現行: hp unread or <= MAX_SELF_DAMAGE かつ last_resort/egg_preempt 以外 → 全拒否 + exhausted=True。

新設計: §2.4 の 2 層モデルに従い、候補集合を先に列挙、自傷系のみ除外、
安全候補が残れば使用、全拒否時のみ unknown + 記録 + B。exhausted は「この戦闘で諦めた」に限定。

### 3.2 egg_window 待ち (battle_menus.py:728-736) と exhausted の再解除

(C) 根拠: same-frame 再試行防止の bounded ガードとして exhausted を立てる (維持)。
再解除条件 (潮目変化、bounded):
- ally_hp が前回評価時より `GENERAL_CRITICAL_RETREAT_HP // 2` 以上低下、または
- `_melee_balance` が不利に転じた、または
- `_survival_needed` が新たに真になった。
- 再解除回数は `RESCUE_REARM_LIMIT` (=2) で bounded。
- 再解除時は `battle_rescue_rearmed` を decision log に記録。

### 3.3 reset_battle_controls と defender successor

- reset_battle_controls で卵系状態の carryover を検出し `battle_rescue_carryover_cleared` を記録。
- defender successor 遷移時は必ず reset を通す。

## 4. 接続点

- `judgment/battle.py`: `_rescue_decision(mem, cur, context)` + `_rescue_rearm(mem, cur, context)` 追加
- `flows/battle_menus.py`: okunote 分岐置き換え (§3.1)、egg_window 待ち (§3.2)、monster_menu 再評価ガード
- `flows/battle.py`: battle_start 後の初期評価、reset_battle_controls での carryover 解除
- decision log: 新規 kind 4 つ

## 5. 新規 decision kind と登記 (D)

- `battle_rescue_reentry`
- `rescue_decision`
- `battle_rescue_rearmed`
- `battle_rescue_carryover_cleared`

登記先: `hanjuku_commentary.py` の SPOKEN/required 集合 + kind 列挙テスト。PR で同時更新。

## 6. 回帰テスト (native 画面再現)

(a) 城攻め二連戦: 1 戦目使用 → 2 戦目で候補に入る
(b) 召喚戦中の不利 → 再評価発火 (g1059) + re-arm 経路カバー
(c) 低 HP でも安全候補ありなら使用 (#2864)
(d) 明らかに有利なら温存 (#2863)
(e) g1067 (満HP・優勢 + グリンボー最小35 vs 敵残HP上限25, lethal=false): 
    ゲート却下が rescue_needed をラッチせず、満HP・優勢で退却ガードが発動しない
    (#2896)。カード使用 or 戦闘継続で終わること。
各否定ケース + decision log 追跡可能性。

## 7. 着手見込み

- 実装開始 2026-10-28 午後。
- テスト + PR 2026-10-29。本番反映は次回ゲーム起動から。
- (C)(D) は PR レビューで最終確認。

## 8. 観測ソースの設計判断 (#2864 残作業, 2026-10-28)

### 8.1 採用方針: 候補優先 (candidate-first) を維持 (#2102 整合)

- 救済が必要な低HPでは、観測済みの安全候補 (自傷系以外) がある場合のみ奥の手を開く。
- 自傷系候補 (OKUNOTE_SELF_DAMAGE_CHOICES = {'ヤケクソ'}) は候補集合から除外する (flows 選択段でも除外)。
- 候補未観測 (メニュー未表示) の場合は開かず、`battle_okunote_risk_declined` を記録して白兵へ戻る (g482 実測との整合)。
- 代替救済 (退却等) は低HPで確実に到達させる経路を別途維持する (#2864 option (ii) の観点)。
- 拒否理由は `battle_okunote_risk_declined`、受諾は `battle_okunote_risk_accepted` として decision log に記録する。

### 8.2 前提の検証 (プールの自傷は1つのみ)

- OKUNOTE_SELF_DAMAGE_CHOICES は {'ヤケクソ'} のみ (os_layer/screen.py)。
- OKUNOTE_CHOICES の候補は重複なしで3件提示される (g482 実測)。
- よって候補集合に自傷系が含まれても、安全候補が残る場合は開封後の選択で自傷系を除外できる。
- 選択時は candidates から自傷系を除外し、残った安全候補のうち最も効果の高いものを選ぶ (flows/battle_menus.py 選択段)。
- 候補未観測時は開かず、代替救済経路に委ねる (g482 整合)。

### 8.3 descope 後の実態との整合 (#2831 option A)

- #2831 rescue re-entry は descope 済み。judgment/battle.py に `_rescue_decision` は存在しない。
- 救済判定は flows/battle_menus.py の候補優先 (candidate-first) モデルで実装済み。
- `_rescue_rearm` (tide change re-arm) と carryover 検出 (reset_battle_controls) は維持。
- #2863 (conserve) は未実装のため OPEN 維持。本方針と同一の判断基準で一体設計する (follow-up)。
