# VERIFY-5246 — [半熟英雄] 卵を残して敗北: ch9 ヘパイストス

- 対象: azumag/docich issue #5246 / fingerprint `f601c5186ce59c70` / occurrence `d7401f205551ddacc880`
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1417-stall-refresh`
- 戦闘: ヘパイストス vs バラクーダ（城タオ、side: attack）、loss、HP ally 0 / enemy 76、卵残数 4 / 切り札なし
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `8a219f65d`

## 結論: (b) 設計どおり — 卵はこの戦闘で実際に使用済み。残存分は既存の fail-closed 設計の結合で消費されなかった

issue のシグネチャ「卵を残して敗北」は字義どおりには真（敗北時に卵の残り使用回数が残っていた）だが、
「卵経路が走らなかった」という含意は誤り。**卵は実際に使われており、味方召喚獣 なると
（ぎざぎざ / うずまき）が約 15 秒間戦ってから設計どおりの停滞判定で戻されている。**
戻った後の約 6 秒間（16:20:47 → 敗北 :53）に卵を再使用できなかったのは、owner 承認済みの
有界・fail-closed 設計の結合によるもので、監査の high 起票は owner 方針の字義に対して正しい。

降格条件 `cards_unconfirmed_only`（month_audit.py:1266-1269）は切り札専用であり、
卵のみの資源では high のまま起票されるのは設計どおり（誤検知ではない）。

## 根拠となるコード箇所（ファイル・行、main @ 8a219f65d）

### 1. 卵経路は走った — 味方召喚獣が出撃し、有界の停滞判定で戻った（検証観点1 前半）

- issue の決定記録: `monster_menu_choice`（ぎざぎざ → うずまき → ぎざぎざ → たまごにもどれ）、
  `monster_skill_stall` ×3、`battle_enemy_transform_observed` ×3。
  `monster_menu_step` の技選択が走った時点で味方召喚獣が場に出ている = **卵を 1 度使用済み**。
  （召喚獣は切り札では出ない。たまごキャリー経由の卵召喚。）
- `src/docich/hanjuku/flows/battle_menus.py:2856` — `MONSTER_STALL_RETURN_TURNS = 6`。
- `battle_menus.py:2868-2940` — `_monster_stall_switch`: 両技で合計 6 自ターン敵 HP を
  動かせない場合に召喚獣を戻す有界設計。issue の :47 の
  「両技とも6ターン敵のHPを動かせないため、これ以上削られる前に召喚獣を戻す」と一致。
  バラクーダ HP 76 に対し ぎざぎざ(2,2,1)/うずまき(0,0,1)（`hanjuku_reference.py`
  MONSTER_ATTACK_DATA 'なると'）では実効が無かった。
- `docs/operations/hanjuku-powerless-summon-return-20261108.md` — 「同じ組で敵HPが動かないまま
  同じ召喚獣を再召喚した場合は以前の実測を引き継ぐ」。つまり仮に :49 で再召喚が成功していても
  停滞カウントを引き継いで即座に再帰還する設計であり、この組合せで残卵を実消費する意味は薄い。

### 2. 戻った後に卵経路が二度と走れなかった機構（検証観点1 後半）

召喚獣帰還後の約 6 秒間に、卵を再度選ぶ経路は以下の通りすべて設計上ふさがっていた:

- `judgment/battle.py:2152-2187` `_egg_before_retreat` — `not _summon_class_enemy(cur)`
  （:2222-2234、`enemy_summon_observed` ラッチ）かつ `_own_egg_positive` を要求。
  **#4606 設計**: 観測済み召喚/大型敵に対しては同じ不安定コマンド枠の再探査で退却を遅らせない。
  本件は敵の変身/召喚が観測済み（`battle_enemy_transform_observed` ×3、
  `flows/battle_menus.py:3055` で記録）なので常に False。
- `judgment/battle.py:2773-2785` `_own_egg_positive` — 将軍が `mem['egg_recheck']` に
  含まれると False。「a selection dirties the old count until this owner is read」。
  `strategy/economy.py:508-516` `_egg_recheck` は卵選択（survival・indep・egg_battle の
  たまごをつかう選択、battle_menus.py:503/1171/2804）で dirty にする。
  味方召喚を出した = 卵選択済み → dirty ラッチ → 状態画面の再読がない限り False。
  敗北で再読機会が無かったためラッチは解除されない（#5145 と同じ機構）。
- `judgment/battle.py:2786-2802` `_dying_with_egg` — `_own_egg_positive` を要求 → False。
- `judgment/battle.py:1534-` `_general_egg_rescue_recheck` — egg_battle /
  card_summon_observed / egg_recheck 等のラッチで同様に塞がれる。
- `battle_menus.py:361-520` `_survival_menu` — 唯一残る汎用経路。
  :48 `battle_card`（開幕切り札を試行 → 手持ちなし）→ :49
  `battle_hero_retreat_cancel_card`（切り札一覧を閉じる）→ :49 `battle_survival_select` →
  :51 `battle_survival_unavailable` → 白兵 → :53 敗北。
  - 卵枝（:373-374）: `たまごをつかう in labels and not egg_attempted and not _own_egg_empty`
    → ledger は 4 で `_own_egg_empty` False なので、行が読めれば選択可能だった。
  - :49 の select は たまごをつかう または きりふだ のいずれか（記録の observed_metric に
    label は出ない）。卵を選んだ場合は `_egg_recheck` + `egg_attempted` + `egg_pending` が
    立ち（:503-513）、`egg_wait <= 3` 観測の有界待機（:365-369、約 2 秒）で召喚が開始されず
    `egg_pending` 消滅 → `egg_attempted` が残って卵行は二度と選べない → :51 unavailable に整合。
    切り札を選んだ場合も手持ちカード 0 なので一覧で確定できず :51 unavailable に整合。
  - いずれにせよ有界の救済メニューは 1 回だけ走り、敗北までの ~4 秒で二度目の open は起きなかった
    （opens<12 / pending_opens<3 の有界枠内、`_egg_before_retreat` と同一の有界性）。
- `battle_menus.py:344-360` `_hero_retreat_menu` — 退却一律禁止（owner v1414）、
  `battle_retreat_banned` 記録のみ。`flows/battle.py:1348` 付近の ch9 非主人公将軍の
  早期 return（owner 2026-11-09）も同方向。**「退却選択: False」は禁止設計どおり。**
- `battle_menus.py:1673-1690` `battle_hero_retreat_cancel_card` — 未選択の切り札一覧を
  閉じて緊急行動を優先、という記録も設計名どおり。

**召喚獣戦フェーズでの卵経路到達性**: 召喚獣戦（monster_menu）中は将軍メニュー自体が出ないため
卵経路は構造上到達不可。到達可能なのは帰還後の将軍メニューであり、そこでは上記の結合で
有界 1 回しか試せなかった。

### 3. 「卵残数 4」の内訳（検証観点2）

- `src/docich/hanjuku_egg_stock.py:11` `observed_uses` — 0..5 の**実測**残り使用回数を受理。
- `src/docich/hanjuku/state/egg_memory.py:30` `_FULL_USES = 4` — 4 = 未使用のフル卵。
- `month_audit.py:1079-1140` `_battle_resources` が `observed_metric.egg_uses` を読み、
  `battle_result`（`flows/battle.py:1774`）時点の `mem['egg_uses'][将軍名]` をそのまま
  「卵残数」として出力（表示名は month_audit.py:3711 付近）。
- したがって 4 は監査側の計数ではなく**実測帳簿の値**だが、戦闘中の召喚消費を反映しない
  **stale な直前観測値**。`_egg_recheck` の docstring どおり「a selection is not a used
  summon」であり、選択時点で局所デクリメントを発明しない設計。再観測は状態画面のみ。
  ヘパイストスは敗北したため再観測は起きず、実際の残数は 4 より少ない（召喚 1 回消費で ~3）。
- `hanjuku_egg_reference.py:82` `GENERAL_EGGS['ヘパイストス'] = (True, 3)` —
  卵保有将軍（タプル後半は思考タイプで残数ではない）。

### 4. 同系検証との異同（検証観点3）

| issue | シグネチャ | 判定 | 機構 |
| --- | --- | --- | --- |
| #5198 | card、loss | (a) 実欠陥 | 監査の降格経路 `cards_unconfirmed_only` が survival 一覧 settle（`battle_card_remaining_unconfirmed`）を `cards_unclassified` に積算しないカバレッジ漏れ |
| #5145 | egg+card、retreat | (b) | `egg_recheck` dirty ラッチで `_own_egg_positive` False → 卵未使用（fail-closed 設計） |
| #5144 | egg、loss（ch8 ヘパイストス） | 同系 | 同シグネチャ、本件と同じ卵残存敗北クラス |
| #5193 | card、loss | (b) | 切り札系の設計どおり |

本件との差分: (i) 卵のみ・切り札なし（一覧を開いても選択対象が無かった）。(ii) **卵は実際に
召喚へ使用済み**で、監査の「残数 4」は消費前の観測値。(iii) 卵の再使用を止めたのは
`egg_recheck` 単独ではなく `enemy_summon_observed`（#4606）+ dirty ラッチ + 有界
survival メニューの結合。#5198 のような「設計された降格経路が発火しない」穴は無い
（卵には `cards_unconfirmed_only` 相当の降格経路がそもそも存在しないため、
high 起票は設計された出力）。

### 5. owner 方針との整合（検証観点4）

- owner 方針「退却・敗北の前に卵と切り札を使い切る」に対し、残り使用回数は実際に残った
  → シグネチャは真であり、high 起票は降格条件に当たらない。
- ただし「使わなかった」のではなく「1 回使い、実測で無効と判明して戻し、限られた窓内で
  有界再試行したが開始できなかった」系列であり、無駄遣いを避ける別の owner 設計
  （停滞帰還・再召喚時の実測引継ぎ・退却禁止）と「使い切る」の方針が競合する角ケース。
  ボットは資源を使う行動（召喚・切り札一覧・救済メニュー）をすべて試行しており、
  怠慢に資源を温存した系列ではない。

## 補足（欠陥ではないが改善余地のある観測限界）

- 監査の「卵残数」は常に戦闘前の直前観測値で、戦闘中の召喚消費を差し引かない。
  「この戦闘で卵を召喚へ使用済み（monster_menu_choice / egg_battle 系記録あり）」を
  監査側で注記できればトリアージが速くなる（現状は決定記録を読んで初めて分かる）。
- 召喚帰還→敗北の短い窓では、卵再使用は汎用 `_survival_menu` のみに残り、
  `egg_pending` の 3 観測有界待機が切れると `egg_attempted` で卵行は二度目を選べない。
  再召喚自体が実測引継ぎで即帰還する設計のため実害は小さいが、「残り卵を盾としてでも
  撃ち切る」方針に変える場合は owner 判断が必要。

## 手順上の注記

- read-only 検証。VERIFY-5246.md の作成以外のファイル編集・本番 VM 操作なし。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue コメント）は
  同等のローカル秘密パターンスキャンで clean を確認してから投稿する（AGENTS.md §3.1
  フォールバック）。正本 `handoff.md` は参照未設定のため未読として記録。
