# IMPLEMENT-4999 — [半熟英雄] 退却の前に卵と切り札を使い切る (v1360)

- Issue: azumag/docich#4999 / 判定 `battle_retreat_with_resources`（ch1 シナモン、VERIFY-4999.md）
- オーナー判断（2026-11-22）: 退却する前に、卵と切り札を使い切ること。
- Base: origin/main 06018b89（worktree、未コミット。commit/push/PR/fetch/rebase なし）
- バージョン: `hanjuku-chart-v1360-spend-resources-before-retreat`
- 先行 unit (devin-4999) の未コミット差分を引き継ぎ、精査・補完・テスト追加を実施。

## 方針

監査が「残り資源」として数えるもの（`cards_selected` − `cards_used` の差分、未使用の卵）を、
退却行（たいきゃく）の確定と退却メニュー開放の前に、有界な使用手順で消費し切る。

1. **選択済み告知の有界 grace**（flows/battle.py `_hero_retreat_open`）:
   危険HPで `card_flow.stage == 'announce'` かつ `selection_planned` のとき、即時 preempt
   せず `retreat_card_grace < RETREAT_CARD_GRACE_LIMIT(6)` の間だけ既存の告知
   ライフサイクルを待つ（`battle_retreat_card_wait`、初回のみ記録）。実際には
   告知フロー自身の 2 観測 bound が先に効き、使用は確定 (cards_used) または
   未分類 (cards_unclassified) で正直に確定する。grace 消費後・即死帯では
   従来の `battle_retreat_preempted_card` 経路へ戻る。
2. **残り札の有界な使用試行**（`_retreat_cards_to_spend`）:
   選択済み・使用未確認の札（監査の残り札定義と同一: `month_audit._remaining_cards`）
   があれば、退却メニュー開放の前に救済メニュー（survival）を最大 1 回の再試行
   まで開く（`battle_cards_before_retreat`）。opens<12 / pending_opens<3 /
   menu_ticks<=12 / cards_checked<3 の既存 bound で遠回りは必ず止まる。
3. **一覧の再選択（retry）**（`_survival_card_list` 内 `cards_spend_retry`）:
   退却待ちの間だけ、実一覧に残る「使用未確認の同名札」に 1 回だけ再選択を与える。
   2 回一致した実一覧の読み取り規則（count_readings>=2）はそのまま適用し、
   ゆらぐ一覧を再選択の根拠にしない。
4. **未選択の一覧札も対象**（`_critical_safe_card(..., for_retreat=True)` 拡張）:
   実一覧 `listed_cards` に未 attempt・未 selected の札があり `_card_damage_gate` が
   許可するなら（致死・制御・卵落・回復を問わず、回復は傷があるときのみ）退却を
   遅らせる。選択されていない札は監査の残り札に載らないが、オーナー判断の
   「切り札を使い切る」に合わせて使用対象に含める。
5. **卵**（既存 `_egg_before_retreat` をそのまま利用）:
   本人の未試行・正残数の卵は、既存通り退却より先に実メニューで召喚可否を確認する。
   卵の変身は「消費」ではなく生存手段そのものなので、即死帯の HP でも
   `EGG_ATTEMPT_LIMIT(2)` 内で有効のままとする（整合確認済み・変更不要）。
   `_hero_retreat_open`・`_hero_retreat_menu` の両方で切り札経路より先に評価される。

## 安全側の線引きと根拠

- `RETREAT_RESOURCE_FLOOR_HP = 4`（`_retreat_immediate_loss`）:
  g460（ココット 24→12→0、カードメニュー待ちで死亡）の教訓。白兵 1 接触で
  10HP 以上が観測されているため、HP<=4 では次の接触がほぼ致命。ここでは
  告知待ち・使用遠回りを打ち切り、従来どおり即時 preempt → 退却を優先する。
  `card_list_step` の緊急キャンセル（`battle_hero_retreat_cancel_card`）も
  この帯では従来どおり発火する。卵のみ例外（変身が生存手段なため）。
- `RETREAT_CARD_GRACE_LIMIT = 6`: 退却を止めて告知を待つ上限。実際は
  announce フロー自身の bound（白兵画面 2 観測で未分類確定）が先に効くため、
  上限到達はメニュー描画が壊れた場合の保険。
- `cards_spend_retry`（1回）+ `cards_checked < 3` + 救済メニューの
  opens/pending_opens/menu_ticks 既存 bound: 遠回りの総量は必ず有限。
- `hero_retreat.opens >= 3`・`unavailable`・`exhausted`: 従来の退却側 bound が
  変わらず優先される。
- 防御戦・ch9・event_duel は従来のガードが先に効く（対象外・不変）。

## 変更ファイル

- `src/docich/hanjuku/version.py`: v1360 へバンプ。
- `ops/vm_actions/collect_diagnostics.py`: chart 許可リストへ v1360 を 1 行追加。
- `src/docich/hanjuku/judgment/battle.py`: `RETREAT_RESOURCE_FLOOR_HP` /
  `RETREAT_CARD_GRACE_LIMIT` 追加、`_retreat_immediate_loss` /
  `_retreat_remaining_cards` / `_retreat_cards_to_spend` 新規、
  `_critical_safe_card` に for_retreat 拡張（ゲート許可全札 + floor 打ち切り）。
- `src/docich/hanjuku/flows/battle.py` `_hero_retreat_open`: 選択済み告知の
  有界 grace（`battle_retreat_card_wait`）と、残り札の survival 使用遠回り
  （`battle_cards_before_retreat`）。
- `src/docich/hanjuku/flows/battle_menus.py`:
  - `_hero_retreat_menu`: 未確定 card_flow を正直に確定（unclassified）してから、
    残り札があれば `たいきゃく` より先に survival へ回す。
  - `_survival_card_list`: 退却待ちの使用未確認同名札へ `cards_spend_retry` の
    1 回再選択。
  - `card_list_step`（**本 unit で追補**）: `battle_hero_retreat_cancel_card` の
    判定を `for_retreat` 基準へ更新し、`flow.survival` の残り札使用一覧は
    floor 以上では閉じない carve-out を追加。先行差分のままでは、危険HPで
    survival 一覧が毎回このキャンセルに潰され、使用遠回りが dead code に
    なっていた（for_retreat 拡張とも矛盾していた）。
- `tests/test_hanjuku_issue_4999.py`（新規）: 下記「テスト」参照。
- `tests/test_hanjuku_issue_4958.py`: for_retreat の全札拡張・grace 待機・
  使用試行消化後の退却を新規則で固定（#4958 の非欠陥結論は維持）。
- `tests/test_hanjuku_survival.py`:
  - `test_hero_retreat_cancels_unselected_cards_and_preempts_critical_pending_card`:
    未選択一覧のキャンセルはゲート不許可札（バルムンク）に限定して固定し、
    危険HPの pending 札は grace → preempt の遷移を固定。
  - `test_critical_hp_preempts_unconfirmed_card_for_every_general`:
    preempt は即死帯（HP4）で固定。

## テスト

新規 `tests/test_hanjuku_issue_4999.py`（要請の4ケース + 単体固定）:

- `_retreat_immediate_loss` の境界（None/0/4/5/12）。
- `_retreat_remaining_cards` が監査と同一の多重集合差分であること。
- `_retreat_cards_to_spend` の bound（retry 済み / exhausted /
  cards_exhausted / cards_checked>=3 / remaining 空）。
- 残り札あり → 退却メニューではなく survival 開放（battle_cards_before_retreat）。
- 退却メニュー手前でも survival 遠回り → 実一覧で残り札を選択
  （cancel_card に潰されないことの固定 = card_list_step の追補）。
- 使用未確認の同名札 → 1 回の spend retry（cards_spend_retry に記録）。
- retry 消化後 → たいきゃく 選択（battle_hero_retreat_select）。
- 救済メニュー枯渇 → 残り札があっても退却へ落ちる。
- HP4 以下 → preempt + survival を開かない（パネル側・メニュー側）。
- grace の上限（RETREAT_CARD_GRACE_LIMIT 消費後は preempt）。

### 実行結果

- ローカル実行: 本環境では python3 / pytest の起動を含むコマンドが
  拒否され（先行 unit の VERIFY-4999.md と同じ制約）、pytest も
  `python3 -m compileall` も実行できていない。**実行未確認を成功扱いしない**。
  実装は差分の静的追跡と既存テストとの分岐照合に限定した。
- 想定される影響範囲は既存テストを全件 grep で照合済み（announce/critical HP/
  退却経路を持つのは test_hanjuku_survival と test_hanjuku_issue_4958 の
  計3テストのみで、いずれも新規則へ更新済み。hp<=3 の既存固定テストは
  floor 内のため不変の見込み）。
- CI のフルスイートでの確認を推奨（本 worktree は未コミットのまま引き渡し）。

## 残課題

- `planned_cards`（チャート予定だが未選択の札）は「残り札」に含めない。
  監査定義（選択−使用差分）とオーナー報告の事案（選択済みが残った）に
  限定した。未選択の予定札まで使い切るかは別判断。
- `cards_selected` は再選択のたびに追記されるため、同一物理札を複数回選ぶと
  監査の残り札カウントが実在庫を上回りうる。使用未確認と消費済みを監査だけで
  区別できない既知の限界（VERIFY-4999 §残課題）と同じ構造であり、本件の範囲外。
- grace (`retreat_card_grace`) は戦闘単位の累計で、札ごとにリセットしない。
  複数札を立て続けに選んだ場合の合計待機量を抑える設計選択。
