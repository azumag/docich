# 半熟英雄: 攻撃性能のない召喚獣メニューを1ターンで見切って戻す（v521）

## 事象（g1153 2026-11-08 17:45–17:47 JST）

第4話の城防衛で、ソーピニヨン（HP48）が卵で変身したゼウス（HP85）と交戦した。
敵は `battle_enemy_transform_observed`（17:45:26）で変身済み。味方の召喚獣は
とうめいにんげん（`イタズラがき` / `イナイイナイバア`）で、実表示の両技とも
攻撃データの対モ・対将ダメージが0だった。

決定ログ（`run-soren-live/runtimes/g1153-e5c7fa38/hanjuku_decisions.previous.jsonl`）では、
敵HPが85のまま、召喚獣HPが44 → 31 → 21 → 11 → 1と減った。

| 時刻 | 選択 | 召喚獣HP | 敵HP | 記録 |
|---|---|---|---|---|
| 17:45:26 | イナイイナイバア | 44 | 85 | `monster_menu_skill2`（味方が劣勢で効果付きの2技目） |
| 17:45:41 | イナイイナイバア | 31 | 85 | `monster_menu_skill2` |
| 17:45:56 | イタズラがき | 21 | 85 | `monster_skill_stall` / `monster_stall_skill1` |
| 17:46:19 | イナイイナイバア | 21 | 85 | `monster_skill_stall` / `monster_stall_both`（選択を維持） |
| 17:46:31 | イナイイナイバア | 11 | 85 | `monster_menu_skill2` |
| 17:46:45 | イナイイナイバア | 1 | 85 | `monster_menu_skill2` |

17:47:25 に敵将軍メニュー待ちへ移り、召喚獣は失われた。以降は白兵・切り札・退却の
既存経路に戻ったが、この戦闘で召喚獣が敵HPを1も減らせていない。

`monster_stall_both` の理由は「両方の技で敵のHPが変わらないが、召喚獣を戻すと将軍が出る
ため選択を維持する」で、攻撃性能のない技を選び続ける設計になっていた。既存の
`powerless`（ウゴカザル限定、g496）と異なり、攻撃データ上0でも他モンスターは対象外だった。

## 判断

攻撃データ（`hanjuku_reference.MONSTER_ATTACK_DATA`）の対モ・対将が両技とも0で、
実表示の2技がその表の2行と完全一致する味方召喚獣メニューは、技ごとに自分の1ターンだけ
試し、敵HPが動かなければ `たまごに もどれ` で戻す。

- 表の0は「効果なし」ではなく即死・効果技も含むため、静的データだけでは戻さない。
  実際に両技を1ターンずつ使い、敵パネルHPが動かないことを戻す条件にする。
- 比較用の既存予算（`MONSTER_STALL_TURNS = 2`）は攻撃技のある召喚獣に残す。0ダメージの
  両技に2ターンずつ使うのはHPを失うだけで、g1153では7ターン分を消費していた。
- 未読の技名・別名・未知の召喚獣・敵メニューは fail-closed で従来どおり。ランプキン、
  あけまつ、おめでとり、ちきゅうちゃん、バルーンフィンチ、ウゴカザルは既存の専用判断を
  維持する（`special_cycle` / `lampkin` / `chikyu` / `powerless` が先に決める）。
- カウントは召喚獣と敵の組・敵パネルHPに紐づく。同じ組で敵HPが動かないまま同じ召喚獣を
  再召喚した場合は、以前の実測を引き継いで即座に戻る（同じ戦闘で無効と分かっている技を
  再度試さない）。敵HPが変われば新しいカウントになる。
- v193/v200の「負傷した本人を敵へ露出させない」判断より優先する。無効な技を続けても敵HPは
  動かず、いずれ召喚獣が倒れて本人が出るため、露出を遅らせるだけで勝敗は変わらない。
  この優先は攻撃データ上0の両技と実測（両技各1ターンで敵HP不変）が揃った場合に限る。

## 実装

- `src/docich/hanjuku/judgment/events.py`: `_monster_menu_attackless(owner, ally, enemy, skills)`。
  味方メニュー・2技の完全一致・表の該当列（敵が召喚獣/ボスなら対モ、それ以外は対将）が
  すべて0のときだけ True。
- `src/docich/hanjuku/flows/battle_menus.py`: `MONSTER_ATTACKLESS_TURNS = 1`。
  `_monster_stall_switch(..., attackless=...)` が両技を使い切ったら `'retreat'` を返し、
  `monster_stall_return` を1回記録する。`monster_menu_step` は戻り行を確定し、
  理由に「両技とも攻撃性能がなく敵のHPも動かない」を残す。
- `src/docich/hanjuku/version.py`: `hanjuku-chart-v521-powerless-return`。
  `ops/vm_actions/collect_diagnostics.py` の固定版リストへ追加。

## 検証

- `tests/test_hanjuku_summon_powerless_return.py`（合成メニュー）
  - 17:45相当の並び（skill2 → skill1 → 戻る）と、戻り行のA入力
  - 攻撃技のある召喚獣は従来の2ターン予算と `monster_stall_both` を維持
  - 未読・別名・未知・敵メニューは対象外、特殊周期技は戻さない
  - 再召喚（HP上昇）でカウントを作り直す
- 既存: `tests/test_hanjuku_monster_skill_stall.py`、`tests/test_hanjuku_wounded_summoner.py`、
  `tests/test_hanjuku_live_summon_shield.py`、`tests/test_hanjuku_chart_bot.py`、
  `tests/test_hanjuku_generic_summon_stall.py`、`tests/test_hanjuku_monster_unchosen_turn.py`、
  `tests/test_hanjuku_monster_overtaken_turn.py`、`tests/test_hanjuku_hotload.py` が通過。

## 未確認・限界

- 17:45–17:47の召喚獣メニュー実フレームは保存されておらず、テストは合成メニュー。
  実画像の根拠は同じ戦闘の `decision-111.png`（ゼウス85 / ソーピニヨン48）と決定ログのみ。
- 戻した後に別の召喚獣で勝てたかは未確認。この戦闘は変身後の敵HP85に対して、戻した場合も
  将軍HP48で戦うため、勝敗の改善を保証しない。無効な技の反復を止める変更である。
- 本番の次回再発時に、`monster_stall_return` と戻り入力の実測を確認する。
