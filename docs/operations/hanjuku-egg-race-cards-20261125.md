# 卵なし・敵卵ありのとき、ピンチ前から切り札で押し切る（2026-11-25）

## オーナー指示

> こっちが卵を持っていなくて相手が卵を持っていて、切り札をこっちが持っている時、
> ピンチでなくても切り札を積極的に使い、卵を使わせないうちに倒すようにしたい。

自軍の卵が無く、敵が卵を使える状態では、HPが減ってから札を開くのでは敵の召喚が先に
来る。チャートのHP・激突の時期を待たず、実携行の切り札で敵将軍を先に倒しにいく。

## 条件（fail-closed）

`judgment/battle.py` の `_egg_race_pressure(mem, cur)` は、次のすべてを満たす戦闘だけを
「卵の競争」とみなす。静的な卵の有無は**能力**であり、実際に使える/使った卵の証拠では
ないため、名前付きの観測だけを根拠にする。

1. 将軍名と `side`（attack / defense）が読めている。ボス・イベント一騎打ちではない。
2. 自軍の卵が無い: 名前付きで観測した残数0（`_own_egg_empty`）、または初期データが
   卵なし（`GENERAL_EGGS`）。名前付きの正数観測があれば解除し、卵の再確認待ち
   （`egg_recheck`）と未読は対象外。
3. 敵が卵を使える: `enemy_egg_triggers(...).has_egg` と `_egg_threat(...)` がともに真
   （思考タイプ0の「卵を持つが判定を持たない」敵は対象外）。防衛戦では
   `player_castle_defense=True` の上書きを使う。
4. この戦闘で召喚・変身・卵落ち未確認をまだ観測していない（`egg_battle`、
   `card_summon_observed`、`enemy_summon_observed`、`enemy_transform_observed`、
   `enemy_egg_drop_expected` が無い）。敵将軍HPが正の整数である。

## 変更

- `judgment/battle.py`
  - `_egg_race_pressure(mem, cur)`: 上記の条件。
  - `_egg_race_card_advances(estimate)`: 自傷でなく、確実に敵将軍へ効く効果
    （`fixed` / `hp_scaled` / `soldier_clear` / `damage_and_stun` / `half_enemy_hp`）だけを
    「前進」とみなす。回復・退却・自傷（デッドガン・バグストーム・ファバード・
    ブラックホール）は対象外。
  - `_card_damage_gate`: 卵落・致死・兵士消耗の従来根拠を先に評価したうえで、
    どれにも当たらない札も卵の競争中だけ `role='egg_race_not_a_kill'` で許可する。
    制御札（クースカン/ブレイコウ）は、追撃の実表示・白兵追撃・半減後のHP優位が
    無い場合にだけ、この根拠で許可する。
- `flows/battle.py`
  - `_carried_opening_tactics(..., pressure=)`: 圧力時は、チャートが**別の敵**向けに
    指定した札でも、実携行の札を開幕札として追加する（`tactic_id='pressure:...'`、
    `egg_race=True`）。
  - 戦術ループ: 圧力時は、チャートの `when_hp_at_most` / `after_clash` がまだ成立して
    いなくても、実携行が確認できている札（`egg_plan['cards']` の `available > 0`）を
    期限として開く。`after_card` の連鎖後続は前札の実行を待つ。
  - 卵落が成立する札は従来どおり最優先。圧力札は卵落の次、チャートの検証済み札より
    先に並べる。`battle_card` には `egg_race_pressure=True` を残す。

## 変えないこと

- 卵落（`_egg_plan` / `_egg_drop_tactics`）の優先と、その未確認証拠の扱い。
- ボス戦の専用キット（バルムンク等）と `BOSS_ONLY_CARDS` の制限。
- 召喚獣戦・ボス戦の既存判断（`docs/operations/hanjuku-summon-card-20261125.md`）。
- 回復・退却・自傷の既存ゲート、およびデッドガン等の自爆札。
- 実使用告知・消費・命中・撃破・卵落の証拠。選択と計画は従来どおり受領証ではない。
- 出撃時の携行（`_deploy_cards`）と `planned_cards` の会計。

## テスト

`tests/test_hanjuku_egg_race_cards.py`（新規 22 件）:

- 条件: 初期卵なし／観測0は真、正数観測・未読・再確認待ち・思考タイプ0・ボス・
  召喚後・side不明は偽、防衛は真。
- 効果: 半減・確実ダメージは前進、ダメージ0・自傷・回復・不明は対象外。
- ゲート: クースカンは卵の競争中だけ `egg_race_not_a_kill` で許可され、卵がある
  通常戦では従来の `control_has_no_observed_followup` のまま。バグストームは不許可。
- 戦術ループ: HP条件未達のチャート札が圧力時だけ開き（`battle_card` と
  `card_flow`）、卵がある場合は開かず従来の救済メニューへ進むこと。`after_card` の
  後続は待つこと。実携行が未確認なら開かないこと。
- `_carried_opening_tactics`: 別の敵向けのチャート札でも圧力時は `pressure:` で
  追加され、同じ敵向けなら重複しないこと。

関連既存テスト（カードゲート・兵士消耗・制御連携・卵なし壁リスク・携行札・
ボス札・救命・退却）も同時に実行し、すべて成功した。旧挙動を固定していた次の
テストは、新しい規則と両立する形へ更新した。

- `tests/test_hanjuku_control_melee.py`: 半減のHP境界は、本人の実卵を観測した
  状態で固定する（卵なし押し込みは新規テストが持つ）。
- `tests/test_hanjuku_issue_2896.py`: g1067 は卵なし・敵卵ありで札を許可する
  新挙動を確認し、卵を観測した場合の従来拒否と退却しない契約は同じテストで維持。
- `tests/test_hanjuku_card_egg_reach.py`: ヘラ vs アポロンの到達計算は、本人の
  実卵を観測して卵の競争の対象外にして固定する。

## 未確認・残件

- 自然ランでの勝率改善・召喚前撃破は未確認。実戦では `battle_card` の
  `egg_race_pressure` と、その後の敵召喚・勝敗を突き合わせて確認する。
- 制御札を追撃保証なしで使うため、半減後に白兵で押し負ける型は残る。使った
  カードの実使用告知・消費は従来どおり別の証拠で確認する。
- 防衛戦の実メニュー読み（`_defense_menu_kit`）が未読の戦闘は、実携行が
  確認できないため圧力でも開かない（fail-closed）。
