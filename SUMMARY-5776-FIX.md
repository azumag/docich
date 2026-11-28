# docich#5776 残件修正サマリ

## 変更ファイル

- `src/docich/hanjuku/flows/battle.py`
- `src/docich/hanjuku/flows/battle_menus.py`
- `src/docich/hanjuku/judgment/battle.py`
- `src/docich/hanjuku/version.py`
- `ops/vm_actions/collect_diagnostics.py`
- `ops/vm_actions/tests/test_collect_diagnostics.py`
- `tests/test_hanjuku_issue_5776.py`（新規）
- `SUMMARY-5776-FIX.md`（本ファイル）

なお、この worktree には `VERIFY-5776.md` は存在しなかったため、依頼文に転記された残件4点を正として実装した。

## 修正内容

### 1. 退却 open が未選択 card flow を落とす経路を塞いだ

`src/docich/hanjuku/flows/battle.py` に `_retreat_waits_for_card_path()` を追加し、`_hero_retreat_open()` の退却メニュー開放より前に適用した。

- 未選択の `card_flow`（`menu` / `down` / `list` など）がある間は、退却 open を行わず、card flow 側の既存の有界な menu/list/announce ライフサイクルを優先する。
- 選択済み flow は従来どおり `selection_planned` / `cards_selected` で識別し、既存の bounded grace と preempt 分岐を維持する。
- 開幕フローが選択前に unclassified へ落ちた場合は、`card_open_retry_pending` を残し、既存の `CARD_OPEN_ATTEMPTS` 上限内だけ退却 open を遅らせる。再開された `_begin_card_flow()` でこの pending を消す。無制限再試行ではない。
- `card_damage_rescue_needed` と `card_damage_list_blocked` がある survival 再確認についても、`opens < 12`、`pending_opens < 3`、`cards_checked < CARD_SPEND_ATTEMPT_LIMIT` の既存予算内に限り、退却 open より survival reopen を優先する。
- HP <= 4 の即敗北帯では最初に `False` を返し、従来どおり退却側が未選択 flow を中断できる安全弁を維持した。

### 2. survival list の候補 filtering で retire を効かせた

`src/docich/hanjuku/flows/battle_menus.py` の `_survival_card_list()` で、候補列挙時にも `_card_refusal_retired()` を適用した。

- これまでは最終選択直前だけ撤回が効き、先に `_card_gate_refused()` → `_card_gate_close()` へ進んでいた。
- 低 HP 帯の撤回条件を満たす札は `eligible` に入るため、critical_card_spend（HP <= 12 かつ boss/summon）が無くても survival 経路で選択へ進める。
- high HP 帯では従来どおり拒否・一覧 close・再評価 signature の記録を行う。

### 3. 低 HP 帯の撤回対象 role を広げた

`src/docich/hanjuku/judgment/battle.py` の `CARD_REFUSAL_RETIRED_ROLES` を以下へ拡大した。

- `nonlethal_egg_risk`（従来）
- `control_has_no_observed_followup`
- `damage_or_egg_risk_unclassified`

撤回条件自体は従来どおり `ally_hp * 3 <= start_ally_hp` の有界な低 HP 判定。ただし `ally_hp <= 0` の死亡パネルでは撤回しないようにした。

併せて以下を同じ判定へ揃えた。

- `_critical_safe_card()`：観測済み一覧札が低 HP 撤回対象なら退却・卵より先の card 経路として扱う。
- `_card_gate_alternatives()`：chart list の代替候補でも低 HP 撤回対象を選択肢に残す。
- `_card_damage_recheck_resources()`：probe budget 消費後の最終再確認で、低 HP 撤回により選択可能になった札を `selectable` として扱う。これにより、拒否後に HP が下がった場合でも survival reopen が retreat open に遮られない。

### 4. バージョン更新

`BOT_VERSION` を `hanjuku-chart-v1595-boss-card-retreat-residual` へ更新した。同文字列を以下へ追加した。

- `ops/vm_actions/collect_diagnostics.py` の bot_version allowlist
- `ops/vm_actions/tests/test_collect_diagnostics.py` の固定値テスト

## 追加した回帰テスト

`tests/test_hanjuku_issue_5776.py` に以下を追加した。

1. 低 HP 帯で3 role が撤回され、`boss_only_balmung`・高 HP・HP0 は撤回されないこと。
2. survival list が低 HP 撤回対象の `nonlethal_egg_risk` 札を閉じずに選択すること。
3. 低 HP 帯の上界を超える場合は従来どおり拒否して閉じること。
4. 退却 open が未選択 card flow を中断せず、`battle_step` でも flow が残ること。
5. 開幕失敗後の bounded retry が有界内だけ退却より先に再開され、`_begin_card_flow()` で pending が消えること。
6. HP <= 4 の即敗北帯では従来どおり退却 open が未選択 card flow を中断できること。
7. 低 HP 撤回で可能になった damage-gate recheck が、退却より先に survival menu を開くこと。

## ローカル検証状況

関連テストの実行を次のコマンドで試みたが、この作業環境では exec ツール呼び出しが拒否され、ローカル実行はできなかった。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tests/test_hanjuku_issue_5776.py \
  tests/test_hanjuku_card_interrupted_release.py \
  tests/test_hanjuku_issue_4958.py \
  tests/test_hanjuku_issue_4999.py
```

したがってローカルでの pytest 結果は未確認。実装は静的な読み合わせと既存契約の確認にとどまる。`tests/test_hanjuku_card_interrupted_release.py` と `tests/test_hanjuku_issue_4958.py` 自体は編集していない。git / gh / SSH / 本番 VM アクセスは行っていない。

## DSH 設計質問

### Q1（残件3）: `damage_or_egg_risk_unclassified` の低 HP 撤回範囲

04:30:08 の実拒否 role は本番ログなしには確定できないため、今回は `damage_or_egg_risk_unclassified` と `control_has_no_observed_followup` も低 HP 帯の撤回対象へ広げた。ただし `damage_or_egg_risk_unclassified` には、ダメージ推定不能だけでなく、画面読み取り不能・召喚状態未確定などデータ整合性寄りの原因も含まれる。

設計判断を確認したいのは、低 HP 帯ではこれらを一律に撤回してよいか、それとも unreadable/HP0/召喚未確定に起因する場合だけは拒否を残すべきか。現実装では `ally_hp <= 0` だけは撤回しないが、それ以外の unclassified 原因は低 HP 帯で撤回する。

### Q2（残件4）: boss 判定なしの scope

`#5777` の説明は「ボス戦では拒否を撤回」だが、現行 `_card_refusal_retired()` には boss 判定がなく、boss / non-boss を問わず低 HP なら撤回する。今回も owner 判断があるまで boss 限定へは変更せず、この scope を維持した。

設計判断を確認したいのは、この boss/non-boss 共通の低 HP 撤回が意図的な scope 拡大なのか、それとも boss 限定へ戻すべきか。

### Q3（補足）: HP <= 4 と既に開かれた一覧の境界

今回は battle 画面での退却 open について、HP <= 4 の即敗北帯では従来の安全弁（未選択 flow 中断・退却優先）を維持した。一方、owner 決定済みの「退却のために一覧を閉じない」契約は変えていないため、既に card list へ入っている場合は list 側の選択手順が残る。この境界（battle 画面では即退却、開済み list は閉じない）を正式な線引きとしてよいか確認したい。
