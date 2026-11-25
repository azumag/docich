# VERIFY-5594 — [半熟英雄] 卵と切り札を残して敗北: ch1 ヘルメス（battle_loss_with_resources）

- Issue: azumag/docich#5594。`battle_loss_with_resources`（confidence: high、
  fingerprint `2114e84c7599b48c` / occurrence `5b9dd27f0ac8b43f4229`）。
- 観測: ラン `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1511-status-panel-close`、
  ch1 2-3（493観測 / 108.7秒）。戦闘は ヘルメス vs クイーン @ けっかい、
  side=attack、HP ally 0 / enemy 28。
- issue の資源表示: 卵残数 1、切り札 選択
  [`ノリウツール`, `ノリウツール`, `フットバース`, `バグストーム`, `バグストーム`]、
  使用 [`フットバース`]、残り [`ノリウツール`, `ノリウツール`, `バグストーム`, `バグストーム`]、
  退却選択 False。
- 検証 base: 現行 main `a3970ddc7`（`hanjuku-chart-v1521-stall-60s-issue` 系）。
  観測版 v1511 の基点は issue コメント記載の `b33256a09`。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。read-only 検証であり、
  本番 SSH・本番入力・production code 変更は行っていない。

## 結論: **(b) 設計どおり・既存修正でカバー済み**

より正確には、issue の字面どおり「使える卵1回と切り札4枚を確実に残して敗北した」
という監査叙述は成立しないが、v1511 の切り札確認フローには攻撃へ戻りうる実欠陥が
あり、それは現行 main の PR #5619（v1520）で修正済みである。したがって新たな
実修正 PR は不要と判断する。卵側の誤読も PR #5605（v1514）で監査済みの対策が
入っている。

主因の整理:

1. 直接の敗北因はクイーン戦でヘルメスが HP 0 まで押し切られたことであり、
   使用可能な卵や退却行を見ながら意図的に捨てた証拠ではない。
2. 卵は、実画面で「たまごをつかうが使えない表示」へ進んだ記録がある。
   `egg_uses=1` は戦闘中消費を差し引かない台帳値で、敗北時に使える卵が1回残った
   実測ではない。
3. 切り札は5枚とも少なくとも選択入力まで行われている。`残り4枚` は
   `cards_selected - cards_used` の機械的差分であり、未使用4枚の live 実測ではない。
4. ただし v1511 では、選択済み・消費未確定の card flow が召喚画面で進行不能に
   なっても `こうげき` へ戻りえた。本件でも 11:52:33Z の announce retry 直後、
   11:52:34Z・36Z に攻撃へ戻っている。この型は #5538/#5612 系の frozen
   `card_flow` で、PR #5619 が「解放前は攻撃を保留し、有界観測後に unclassified
   として解放して残り札の揭示へ戻る」修正を main へ入れ済みである。
5. 退却未選択は owner 方針と矛盾しない。v1511 時点でも退却は一律禁止で、
   「切り札・卵を使い切った後もそのまま戦い切る」実装だった。旧い表現の
   「卵と切り札を使い切ってから退却」と照合しても、本件では少なくとも卵行は
   使用不能、切り札は選択試行済みであり、退却を選ばなかったこと自体は欠陥証拠に
   ならない。

残る限定: ノリウツール×2・バグストーム×2 が実際に発動したかどうかは、
issue 内包証拠だけでは確定できない。ただし「一度も使おうとしなかった」証拠でも
なく、v1511 で観測された未確定のまま攻撃へ戻る経路は既存修正済みである。
この検出限界をもって新規の (a) 実欠陥とは判定しない。

---

## 1. fingerprint / incident_id の再計算

現行 main `src/docich/hanjuku/month_audit.py` の `_fingerprint` は
`chapter|kind|signals` を sha256 し先頭16hexを取る。battle resource incident
では signals 配列全体ではなく `[event['kind']]` を渡す。本件の raw は:

```text
1|battle_loss_with_resources|battle_loss_with_resources
```

Python3 で再計算した先頭16hexは `2114e84c7599b48c` で、issue の fingerprint と一致した。

incident_id / occurrence は builder 固有の raw を使う:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
```

issue JSONの値を代入した raw は:

```text
g1190-ch1v9|1190|battle_loss_with_resources|egg,card|ヘルメス|I:0d4514:5724aed7|1795607579
```

再計算した先頭20hexは `5b9dd27f0ac8b43f4229` で、issue の occurrence /
incident_id と一致した。`now` は issue JSONの `created_at`
（`1795607579.510002`）の整数部である。

## 2. 敗北直前の時系列と意味

issue 内包の decision 列から、資源判断に関係する部分だけを UTC で並べると:

| 時刻 | 記録 | 意味 |
| --- | --- | --- |
| 11:52:30Z | `battle_summon_card_scroll` / `battle_summon_card_open` | 敵召喚獣戦で、通常攻撃より先に切り札行を開いた |
| 11:52:30Z | `battle_card_selected` | 切り札選択入力。記録上も「消費は未確定」 |
| 11:52:31Z | `battle_card_candidate` | 告知署名が未校正で、消費を確定せず画像収集 |
| 11:52:33Z | `battle_card_announce_retry` | 告知を確認できないため同じ札の再選択へ入った |
| 11:52:34Z / 36Z | `battle_egg_general_attack` | 消費未確定のまま通常攻撃へ戻った |
| 11:52:44Z | `situation_held`（卵） | 「たまごをつかうが使えない表示」として卵を諦め攻撃へ進む |
| 11:52:58Z | `battle_card_effect_confirmed` | 告知ではなく、メニューが閉じた後の敵HP低下からフットバース使用を確定 |
| 11:52:59Z | `battle_card_unclassified` / `battle_result` | 実使用告知なしの未分類を残し、HP表示から loss を確定 |

重要なのは、`battle_card_effect_confirmed` が「告知を後から読めた」記録ではない
ことである。現行 main `src/docich/hanjuku/flows/battle.py:714-735` は、告知待ちが
タイムアウトした札について、メニューが閉じた後の最初の完全な戦闘パネルで敵HPが
開幕時より下がっていれば使用確定とする代替レシートを示す。`flows/battle_menus.py:2258-2299`
にも、敵HP低下・札行消失・敵兵士減少を告知代替として使う同系の実装がある。

したがって本件の `battle_card_effect_confirmed` は、フットバースの効果が敵HPに
出たことを示す強い代替証拠ではあるが、同時に 11:52:34Z・36Z に白兵へ戻っている
以上、その敵HP低下が純粋に札効果だったか、白兵ダメージを含んだかまでは分離できない。
逆に言えば、この記録は「切り札告知が読めなかった」ことの反証ではなく、
告知未読を前提にした後付けの実測確定である。

## 3. 卵残数1は実残数ではない

v1511 の `battle_result` は `egg_uses` に `mem['egg_uses'][ally]` をそのまま出していた
（観測版 `b33256a09:src/docich/hanjuku/flows/battle.py:1881-1886`）。これは最後に
観測した台帳値で、戦闘中の卵選択・召喚消費・行の灰色化を差し引かない。

本件の decision 列には、戦闘終了前の `egg_battle_menu` で
「たまごをつかうが使えない表示のため卵を諦めてこうげきで応戦する」という
`situation_held` がある。v1511 の `flows/battle_menus.py:3116-3127` は、
`empty_egg` または `action == 'use_egg'` なのに白い `たまごをつかう` 行が
`screen.text` に無い場合にこの記録を出し、攻撃へ戻る。灰色行は白OCRから落ちる
前提であり、「白く使える卵行を見ながら使わなかった」経路ではない。

現行 main では `battle_result` が `egg_uses_source: 'ledger'` を付ける
（`src/docich/hanjuku/flows/battle.py:1881-1888`）。監査側も
`egg_uses_source == 'ledger'` なら卵資源として数えない
（`src/docich/hanjuku/month_audit.py:1280-1287`、PR #5605 / v1514）。
したがって卵側の誤検知は既存修正でカバー済みである。

## 4. 切り札「残り4枚」は未使用4枚の直接証拠ではない

監査の `_battle_resources` は、`cards_selected` から `cards_used` を差し引いて
`cards_remaining` を作る（`src/docich/hanjuku/month_audit.py:1288-1327`）。
このため「選択したが使用確定できなかった札」は、実際に発動していても
`残り` へ残りうる。監査自身も、告知署名未校正の選択は「使用を確認できない
検出限界」であって、使用可能札を無駄にした証明ではないとコメントしている
（`src/docich/hanjuku/month_audit.py:1416-1426`）。

本件では `cards_selected` に5枚すべてが入っている。少なくとも選択入力まで
行われており、「切り札を一度も使おうとせず敗北した」構図ではない。
フットバースについては `battle_card_effect_confirmed` で使用確定済み、
残るノリウツール×2・バグストーム×2は発動可否が未確定である。

## 5. v1511 の frozen card_flow と既存修正

v1511 の召喚画面側 `egg_battle_step` は、中断した `card_flow` のうち
`stage in ('menu','down','list')` かつ未選択のものだけを `summon_card_resume`
へ退避してクリアしていた（観測版 `b33256a09:src/docich/hanjuku/flows/battle_menus.py:3151-3167`）。
`stage == 'announce'` または `selection_planned` の flow への救済はなく、
真値の `card_flow` は reveal scroll / reopen gate を抑止したまま、攻撃分岐へ
fall-through しえた。

本件の 11:52:33Z announce retry → 11:52:34Z `battle_egg_general_attack` は、
この v1511 の弱点と同じ遷移である。フットバースが実際に効いていた可能性はあるが、
消費未確定のまま白兵を挟んだため、`battle_card_effect_confirmed` の敵HP低下は
札効果と白兵を完全には分離できない。

現行 main では PR #5619（v1520、squash commit `f2409ad08`）により、
召喚画面で進行不能になった `announce` / `selection_planned` flow を有界観測し、
解放前は `return []` で攻撃を確定しない。3観測で `_card_use_unclassified` へ
解放し、同じ観測から残り札の揭示へ戻る（`src/docich/hanjuku/flows/battle_menus.py:3207-3229`）。
PR #5607 は無限凍結の解放だけを狙ったが、#5619 が解放前の攻撃確定まで含む
自己完結修正として先にマージ済みである。

したがって、#5594 で見えた「未確定のまま攻撃へ戻る」実装上の問題は、
現行 main に既に入っている修正でカバーされる。

## 6. 退却未選択と owner 方針の照合

v1511 でも現行 main でも `_hero_retreat_menu` は退却を一律禁止し、
「切り札・卵を使い切った後もそのまま戦い切る」と記録する
（観測版 `b33256a09:src/docich/hanjuku/flows/battle_menus.py:704-711`、
現行 main 同所）。incident counts にも `battle_retreat_banned` がある。

つまり `退却選択: False` は owner 方針違反ではなく、現行の設計どおりである。
ユーザー提示の「卵と切り札を使い切ってから退却」との照合でも、

- 卵は実画面で使用不能と判断され、
- 切り札は少なくとも選択試行済みで、未使用と断定できる live 証拠がなく、
- そのうえで退却せず戦い切っている

ため、退却未選択を敗北の主因・実欠陥と見なす根拠はない。

## 7. #5592 / #5542 / #5555 との異同

| issue | 判定と結論 | #5594 との関係 |
| --- | --- | --- |
| #5592 | ch1 `zero_sortie`。召喚獣メニュー選択が月を占有する実欠陥で、PR #5608 / v1515 が `MONSTER_MENU_TOTAL_CHOICES=40` を追加 | 同じラン `g1190-ch1v9`・同じ v1511 で、`monthly_scan_hold` / `battle_grind` / `no_source` のシグナルは共有する。ただし #5594 の本体は出撃ゼロではなく battle resource 判定であり、主因は別。月占有は周辺症状にすぎない |
| #5542 | 卵のみの `battle_loss_with_resources`。結論 (c)、`egg_uses` は stale 台帳 | 卵の誤読構造は同じ。#5594 はさらに「たまごをつかうが使えない表示」の直接記録があり、卵側はより明確に誤検知。ただし切り札と frozen flow の問題が重なるため、#5542 と完全に同型ではない |
| #5555 | 卵＋クースカンの `battle_loss_with_resources`。結論 (c)、stale 台帳＋消費未確認の検知限界 | 切り札残存の機械的差分という点では最も近い。異なる点は、#5594 では複数札を選択しフットバースだけ `battle_card_effect_confirmed` で確定していること、クイーン戦であること、そして v1511 の frozen `card_flow` 型の攻撃遷移が見えること。この実装側の亜型は PR #5619 で修正済み |

## 8. 対応

- 新規の実修正 PR は作成しない。理由は、卵側の誤検知対策（PR #5605 / v1514）と、
  本件で問題になりうる frozen `card_flow` 解放前の攻撃防止（PR #5619 / v1520）が
  どちらも現行 main に入っているためである。
- 検証記録のみの PR も作成しない（owner 指示）。
- 残る検出限界（ノリウツール・バグストームの実発動可否）は issue 単体では確定不能だが、
  現行未修正の欠陥と結論づける証拠ではない。今後同型が v1520 以降で再発した場合は、
  `card_flow`・`cards_attempted`・`cards_unclassified` の有無を確認して再判定すべきである。
