# VERIFY-5300 — [半熟英雄] 卵を残して敗北: ch10 レモン

- 対象: azumag/docich issue #5300 / fingerprint `99978f677cd94c6b` / occurrence `1176c681e8d16e4b3bc1`（コメントの再発 occurrence `5f2c11dc7a98916037fe` も同型）
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1435-recall-dispatch-unattributed`
- 戦闘: レモン vs リディア（城マカリア、side: attack）、loss、HP ally 0 / enemy 72、卵残数 4 / 切り札 選択 ['イッテツーン']・使用 []
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `ae9ea33b1`

## 結論: (b) 設計どおり — 卵はこの戦闘で召喚へ実使用済み。残数は消費前の帳簿値で、切り札未使用・退却なしも既存の有界・禁止設計どおり

#5246 (VERIFY-5246.md、(b) 判定) と同じクラス。シグネチャ「卵を残して敗北」は字義どおり真だが、
卵はこの戦闘（または同一交戦の直前フェーズ）で味方召喚獣**ちきゅうちゃん**を実際に出しており、
22:52:34Z の `monster_menu_choice`（skill2 = ハルマゲドン、自爆）がその証拠。
監査の「卵残数 4」は召喚消費を反映しない帳簿上の直前観測値であり、
その後に卵を再使用しなかったのは「使用済みで卵行が灰色」+ `egg_recheck` ラッチの結合による
設計どおりの fail-closed。切り札「イッテツーン」の未確認選択→missing、
`battle_survival_unavailable`、退却選択 False もいずれも現行設計の出力。

## 根拠となるコード箇所（ファイル・行、main @ ae9ea33b1）

### 1. 卵経路は走った — ちきゅうちゃんを召喚し、ハルマゲドン（自爆）を選択（検証観点1・3）

- issue の決定記録: 22:52:34Z `monster_menu_choice`
  「召喚獣の技メニューで『ハルマゲドン』を選択: 自爆は将軍と兵士を確定で全滅させるため、
  敵将軍だけの今ハルマゲドンで決着させる」、22:52:35Z `enemy_general_menu_wait`
  （egg_battle_menu で敵将軍の操作待ち）。**味方召喚獣が場に出ている = 卵を 1 回使用済み**。
- `src/docich/hanjuku_reference.py:864-867` — `ちきゅうちゃん`: skill1 だいじしん / skill2
  ハルマゲドン（自爆）。`hanjuku_reference.py:1151` の同名エントリと一致。
- `src/docich/hanjuku/flows/battle_menus.py:3386-3390` — `chikyu` 分岐: 「敵将軍だけの今
  ハルマゲドンで決着させる」は skill2 選択の定型理由で、issue の記録と逐語一致。
- `src/docich/hanjuku_egg_reference.py:95` — `GENERAL_EGGS['レモン'] = (True, 2)`:
  卵保有将軍。監査の「残数 4」は `state/egg_memory.py` の `_FULL_USES = 4`
  （未使用フル卵）と一致する観測値で、召喚 1 回消費を差し引かない **stale な直前帳簿**
  （`_egg_recheck` の設計: 「a selection is not a used summon」＝選択時点で局所デクリメントを
  発明しない。再観測は状態画面のみで、敗北により機会なし）。
- 自爆を選んでもリディアが enemy_hp 72 で残ったのは、選択=発動の確定ではない
  （召喚獣が行動前に落とされる／敵側の行動が先に解決する可能性）ため。
  選択記録が証明するのは「卵を撃った」ことのみ。

### 2. 切り札「イッテツーン」: 選択→告知未確認→missing の経路（検証観点1）

決定列は以下のコード経路と 1 対 1 で対応する:

- 22:52:44Z `battle_card` → `battle_card_selected`（`battle_menus.py:1773-1796`:
  list 段で選択入力を予定し `cards_selected` に追加。消費は未確定）。
- 22:52:45Z `battle_card_candidate`（`battle_menus.py:1881-1886`: 告知署名が未校正のため
  消費を確定せず、画像収集のみ）。
- 22:52:46Z `battle_card_announce_retry`（`battle_menus.py:1818-1875`:
  `announce_ticks > CARD_ANNOUNCE_LIMIT` で、敵 HP 低下・兵士減少・行消失 2 フレームの
  いずれの実測レシートも無かったため、owner 2026-11-02 設計どおり同じ札を
  **有限 1 回だけ**再選択する段へ戻る）。
- 22:52:47Z `battle_card_open_unclassified`（`judgment/battle.py:152-181`:
  `selection_planned` が False のまま操作が戻った＝実一覧で未選択。
  携行札を差し引かず `card_open_failures` を +1 し `CARD_OPEN_ATTEMPTS` 未満なら
  tactic を `tactics_done` から外して再試行を許可）。
- 22:52:48Z `battle_card_missing`（`battle_menus.py:1701-1744`: 再び読めた実一覧に
  イッテツーン行が無い → `cards_missing` へ記録し `card_flow` を閉じ白兵継続。
  `deviation_reason='chart_card_unavailable'`）。消費済みで行が消えた場合も同じ
  missing 経路に落ちる設計（`flows/battle.py` コメント: 「a consumed row falls into the
  missing-card path」）。**監査は `cards_missing` を remaining から除外するため
  （`month_audit.py:1136-1140`、owner 決定 #5117）、この札は「残した切り札」として
  起票されていない** — 起票リソースは `egg` のみで、記録形は正しい。

### 3. 召喚後に卵経路が塞がれた機構（検証観点2 — #5246 との異同、egg_recheck ラッチ）

- `judgment/battle.py:2773-2783` `_own_egg_positive` — 将軍が `mem['egg_recheck']` に
  含まれると False。「a selection dirties the old count until this owner is read」。
  `strategy/economy.py:508-516` `_egg_recheck` は卵選択で dirty にし、
  `flows/battle_menus.py:2804` の卵メニュー経路でも呼ばれる。
  ちきゅうちゃんを出した = 卵選択済み → レモンは dirty ラッチ中。
  解除は名前行の再読のみ（`flows/prompts.py:897-898`、`flows/camp_recheck.py:470-471`
  等）で、敗北により再読機会が無かった（#5145/#5246 と同じ機構。`egg_recheck` は
  `state/regions.py:154` で lifetime=`run` のため前フェーズから持ち越される）。
- `judgment/battle.py:2786-2802` `_dying_with_egg` — `_own_egg_positive` を要求 → False。
- `judgment/battle.py:2152-2186` `_egg_before_retreat` — `_own_egg_positive` と
  `not _own_egg_empty`・未試行を同時要求 → False（`battle_egg_before_retreat` が
  記録されなかった直接因）。
- `MONSTER_STALL_RETURN_TURNS`（`battle_menus.py:2856`、#5246 の召喚獣帰還設計）は
  本件では関係しない: ちきゅうちゃんは停滞帰還ではなく自爆技の選択で場を離れている。
  ただし「召喚が無効と分かれば再召喚しても実測を引き継ぐ」設計
  （`docs/operations/hanjuku-powerless-summon-return-20261108.md`）の結論は同じ。

### 4. `battle_survival_unavailable` の判定は正しい（検証観点3 — 救済メニューに卵は実質含まれていなかった）

- `battle_menus.py:361-493` `_survival_menu` — 22:52:48Z `battle_survival_select` の
  1 回の選択後、22:52:49Z に `exhausted=True` で白兵へ戻る。
- 卵枝（`battle_menus.py:373-374`）: `たまごをつかう in labels and not egg_attempted
  and not _own_egg_empty`。`_own_egg_empty`（`judgment/battle.py:2737-2755`）は帳簿 4 で
  False だが、**使用済みの卵行は灰色で OCR から落ち `labels` に入らない**
  （`battle_menus.py:2579-2583`「A spent egg greys the row out and drops it from OCR」、
  同 259-260、1890-1899 の disabled_item_box 除外）。召喚済みの本戦では卵行は
  読めない = 選択不能。
- 卵選択時は `egg_pending` + `egg_wait <= 3` 観測の有界待機に入る（:365-369, :503-513）。
  :48 選択→:49 unavailable は約 0.9 秒・高々 2 観測で `egg_wait > 3` に届かず、
  かつ卵メニュー画面では `_survival_menu` が走らないため、select が卵だった系列は
  成立しない。=> **:48 の select は「きりふだ」** で、実一覧に使える救済札が無い
  （`battle_card_missing` でイッテツーンは missing 確定済み）→ `cards_exhausted` →
  B で戻り → 卵行も灰色 → `battle_survival_unavailable` に整合。
- つまり監査が見た「救済手段なし」は設計どおりの実判定: 救済メニュー上、
  卵は使用済みで灰色・切り札は missing/一覧に無し、で未試行の手段が実際に無かった。

### 5. 退却選択 False（検証観点4 — #5202/#5142 との異同）

- `battle_menus.py:344-354` `_hero_retreat_menu` — **退却は一律禁止**
  （owner 2026-11-23、PR #5230 / v1414 以降、本ランの v1435 に含まれる）。
  `battle_retreat_banned` を 1 戦闘 1 回記録するだけで `None` を返し、
  退还行は絶対に選択しない。
- `flows/battle.py:1423-1436` `battle_hero_retreat_open` は「退却の可否を確認」の記録を
  残すが、実メニュー側で禁止判定が先に走るため選択には至らない。
  issue の :50/:51 の 2 回の `battle_hero_retreat_open` はこの「確認のみ」設計の記録。
- #5202（VERIFY-5202: 即死帯の退却は監査が起票しない）・#5142（即死帯の正当な退却）は
  **禁止前の時代の話**で、いずれも「退却在りき」の判定。本件では HP 0 まで白兵が
  続いたのは禁止設計どおりであり、比較対象としては「退却しない」が正解。
- なお `month_audit.py:1266` の `retreat_selected` は `hero_retreat_selected` メトリクスを
  読むだけで、禁止以後は常に False になりうる（現状そのまま）。

### 6. cards_unclassified 系監査欠陥（#5192/#5194/#5198/#5199）との関係（検証観点5）

- 本件のイッテツーンは `cards_unclassified` ではなく **`cards_missing`** 行き。
  `battle_card_announce_retry` で `selection_planned` が False に戻った後の
  `_card_use_unclassified` は「未選択の中断」枝（`judgment/battle.py:158-181`）で
  `cards_unclassified` に積まない設計（`cards_unclassified` へ積むのは同:182-206 の
  「選択済みだが告知未確認」枝のみ）。
- `month_audit.py:1136-1140` が `cards_missing` を remaining から除外するため、
  監査出力は `cards_selected: ['イッテツーン']` / `cards_remaining: []` /
  `cards_unconfirmed_egg_drop: []` / `cards_unconfirmed_only: false`。
  **「選択したが使用未確認」のまま残存扱いされる #5198 型のカバレッジ漏れは
  発生していない**（missing は「携行していない/消費済みで行が消えた」の確定扱いで、
  remaining に残らない）。
- `cards_unconfirmed_only` 降格条件（`month_audit.py:1255-1257`）は切り札残存のみを
  対象とし、卵のみの資源では high のまま起票されるのは #5246 検証時と同じ設計。

## 補足（欠陥ではないが改善余地のある観測限界）

- 監査の「卵残数」は戦闘中の召喚消費を差し引かない直前観測値。`monster_menu_choice` /
  egg_battle 系記録がある月は「この戦闘で卵を召喚へ使用済み」と監査側で注記できると
  トリアージが速くなる（VERIFY-5246 と同じ提言）。
- ハルマゲドンを選んだ ちきゅうちゃん は「敵将軍だけ」を狙ったが、実際はリディアが
  残った。自爆が将軍を確定全滅させる前提での選択ロジック（`battle_menus.py:3386-3390`）
  と実結果の乖離は、選択=発動確定ではないゲーム側の要因であり、本検証の範囲外。

## 手順上の注記

- read-only 検証。VERIFY-5300.md の作成以外のファイル編集・本番 VM 操作なし。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue コメント）は
  `ops/vm_actions/secret_scan.py`（リポジトリ内同等スキャン）または同等のローカル
  秘密パターンスキャンで clean を確認してから投稿する（AGENTS.md §3.1 フォールバック）。
  正本 `handoff.md` は参照未設定のため未読として記録。
