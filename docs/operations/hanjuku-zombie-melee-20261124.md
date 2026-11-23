# 半熟英雄: 相手がゾンビなら卵を使わず白兵で対処（v1425、2026-11-24）

## オーナー指示

> 半熟英雄　あいてがぞんびの場合はたいこうしてたまごを使わず白兵で対処する

「相手」は敵が召喚したモンスター（画面上の敵側パネル）、「たいこう」はその
召喚への応戦と解釈した。敵召喚獣が **ゾンビ** のときは、こちらの卵
（たまごをつかう）を温存し、白兵（こうげき）で対処する。

## 根拠

- `hanjuku_reference.MONSTER_SKILLS['ゾンビ']` は `ゾンビパンチ` と `ケアル`。
  ゾンビパンチは 0 ダメージ（将軍・エグモンとも）、ケアルは自己回復で、
  将軍を倒す即死・自爆の技能を持たない。卵で召喚してまで答える脅威がない。
- 原典チャート `games/hanjuku-sfc-speedrun/charts/7.md` の対ブラウニーも
  「白兵で倒す（ゾンビは召喚させ、もうこうげきでターン経過）」と白兵を指示。

## 変更

- `flows/battle_menus.py`
  - `MELEE_ONLY_SUMMONS = frozenset({'ゾンビ'})` を追加。
  - `egg_battle_step` で、実画面の敵召喚獣が **1体だけ**読めて名前が完全一致
    したときだけ `melee_only` を立てる。未知・複数・未読の敵へは広げない。
  - `melee_only` のときは `wants_egg=False`、`egg_action='attack'` を固定し、
    `summon_threat`（HP比・ダークエルフ）・`hero_vs_summon`・学習した
    卵使用・途中昇格より優先する。判断記録は `egg_battle` の
    `egg_need.rule='zombie_melee'` として残す。ホットロード等で既に
    `use_egg` が入っていた場合は、白兵へ戻したことを1回だけ記録する。
  - `battle['zombie_melee']` を毎観測で更新する（敵召喚獣が変われば消える）。
- `judgment/battle._defender_summon_preempt`: `zombie_melee` のときは
  おくのて（自傷抽選）を開かない。0ダメージの相手へ自傷リスクを賭ける理由が
  ないため。札・おくのての既存判断はゾンビ以外では不変。
- `BOT_VERSION` v1425-zombie-melee。

## 検証

- `tests/test_hanjuku_zombie_melee.py` 8 passed（実フレーム g684 の召喚戦
  パネルをゾンビへ差し替え、卵不使用・白兵、HP大差でも白兵、学習・
  ホットロードより優先、複数・未読・別モンスターでは適用しない、
  おくのて抑止を確認）。
- 関連: `test_hanjuku_summon_threat` ほか10ファイル 319 passed、
  `test_hanjuku_chart_bot` ほか 463 passed。

## 未確認・残リスク

- 実戦のゾンビ戦で、白兵がケアルを上回って倒し切れるか、何ターンで
  召喚が終わるかは未観測。次回の実戦ログで `egg_battle` の
  `zombie_melee` と戦闘結果を突き合わせる。
- こうげきが将軍と召喚獣のどちらへ入るかは実測していない。原典チャートの
  「もうこうげきでターン経過」はあるが、強制はせず既存の低HP条件に任せる。
