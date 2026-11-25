# 総合戦力スコアで劣勢なら切り札を前倒しする（2026-11-25）

## オーナー指示

> 切り札を持っている場合、ピンチのとき以外に、相手の戦闘力と残っているHPと
> 残っている兵士の数を掛け算したスコアで、こちらのスコアが総合的に負けている
> 場合は優先的に切り札を使ってください。相手が卵を持っていてこちらが持って
> いない場合も同様に優先的に使うこと。開幕で卵落とし条件に合致する場合は
> 開幕すぐその該当の切り札を使うこと。

## スコアの定義

`judgment/battle.py` の `_melee_product_scores(cur)`:

```
score = 将軍の戦闘力 × 現在HP × 残兵士数
```

- 将軍の戦闘力は既存の `reference.general_strength(...)` の値。
- 現在HP・両軍の兵士数・現在性（`card_soldiers_current`）がすべて揃った通常将軍戦
  だけを計算する。未読・古い兵士数・HP不明・喚獣戦・一騎打ちは `(None, None)` で
  計算しない（fail-closed）。
- **残兵士0は掛け算で0になり「総合的に負けている」判定が消えるため、将軍個人の
  戦力として1人分と数える**（兵士1人以上は指示どおりその人数を掛ける）。この
  0人の扱いはオーナー確認待ちの解釈であり、実戦の結果で見直す。

`_score_pressure(mem, cur)` は `自軍スコア < 敵スコア` のときだけ真。ボス・
召喚獣戦・一騎打ち、および本人の卵の変身が先に必要な `_dying_with_egg` は対象外。

## 変更

- `judgment/battle.py`
  - `_melee_product_scores` / `_score_pressure` を追加。
  - `_card_damage_gate`: 卵落・致死・兵士消耗・制御の既存根拠を先に評価し、どれにも
    当たらない札でも `_score_pressure` が真なら `role='score_pressure_not_a_kill'` で
    許可する。対象は `_egg_race_card_advances` が認める効果（自傷なしで確実に敵将軍へ
    効く fixed / hp_scaled / soldier_clear / damage_and_stun / half_enemy_hp）だけ。
  - 制御札（クースカン/ブレイコウ）は、追撃の実表示・白兵追撃・半減後のHP優位が
    無い場合にだけ、この根拠で許可する。
- `flows/battle.py`
  - `pressure_reason` を「卵なし・敵に卵あり」または「総合戦力で劣勢」として一本化。
    圧力時はチャートの `when_hp_at_most` / `after_clash` を待たず、実携行が確認できて
    いる札（`egg_plan['cards']` の `available > 0`）を開く。`after_card` の連鎖後続は
    前札の実行を待つ。
  - `_carried_opening_tactics(..., pressure, pressure_reason)`: チャートが別の敵向けに
    指定した札も、圧力時は `pressure:` として携行札から開く。
  - 卵落が成立する札は従来どおり最優先（`eggdrop:` が rank 0、圧力札は rank 1）。
    開幕で卵落とし条件に合致する戦闘では、スコア劣勢でも卵落とし札が最初に開く。
  - `battle_card` に `card_pressure` を残し、卵なし・敵卵ありの押し込みは従来の
    `egg_race_pressure` も残す。

## 変えないこと

- 回復・退却・自傷（デッドガン・バグストーム・ファバード・ブラックホール）の既存ゲート。
- ボス専用キット、召喚獣戦・ボス戦・一騎打ちの既存判断。
- 卵落の優先順位、出撃時の携行、`planned_cards` の会計。
- 選択・計画を実使用・消費・命中・撃破の証拠として扱わない契約。

## テスト

新規 `tests/test_hanjuku_score_pressure.py`（14件）:

- スコア = 戦闘力 × 現在HP × 兵士数、兵士0は将軍1人分、未読は計算しない。
- 劣勢で真、優勢で偽、ボス・召喚・一騎打ち・卵の変身が先の将軍は偽。
- クースカンはスコア劣勢のときだけ `score_pressure_not_a_kill` で許可され、優勢時は
  従来の `control_has_no_observed_followup` のまま。
- チャートのHP条件未達でもスコア劣勢なら開き（`battle_card.card_pressure`）、優勢なら
  開かないこと。
- 開幕の卵落とし条件が成立する戦闘では、スコア劣勢でも `eggdrop:` の札が最初に開くこと。

既存テストの扱い:

- `tests/test_hanjuku_card_damage_gate.py` / `test_hanjuku_card_egg_reach.py` /
  `test_hanjuku_card_announce_retry.py` / `test_hanjuku_card_inventory_deadline.py` は、
  各ファイルの autouse fixture で `_score_pressure` を無効化し、通常のカードゲート・
  到達判定・再試行・カーソル境界の契約だけを固定する（新しい規則は
  `test_hanjuku_score_pressure.py` が持つ）。
- `tests/test_hanjuku_card_wear_down.py`: 兵士を1人も削れない一撃の契約は、自軍を
  スコア優勢にして固定する。
- ローカル: 関連ファイルと広めの半熟サブセットを実行し、今回の退行がないことを確認。

## 未確認・残件

- 自然ランでの勝率改善・カード消費の妥当性は未確認。実戦で `battle_card.card_pressure`
  と勝敗・残り札を照合する。
- 兵士0を「将軍1人分」と数える解釈はオーナー確認待ち。実戦で過剰使用が出る場合は
  0人を0のまま扱う（両軍0は引き分けで発動しない）変種へ切り替える。
- スコア劣勢の定義に閾値は置いていない。僅差でも劣勢なら開くため、カード消費が
  増える可能性がある。
- 防衛戦や出撃注文が結び付かない戦闘は、実携行の一覧を読む既存の救済経路
  （`_survival_needed` / `_defense_kit_read_window`）が必要時に開く。製品スコアだけを
  理由に実メニューを開く専用トリガーは実装していない（follow-up 候補）。
