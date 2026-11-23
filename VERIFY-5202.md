# VERIFY-5202 — [半熟英雄] 切り札を残して退却: ch6 セルリアク（battle_retreat_with_resources）

- Issue: azumag/docich#5202。`battle_retreat_with_resources`（confidence: high、
  fingerprint 2c20c2927eb496c3 / occurrence 23909cfb4124caf1502c）。
  月は ch6 8-10、ラン g1190-ch1v9 / hanjuku-chart-v1373-egg-receipt-settle。
- 検証 base: `verify/5202-battle-retreat` @ `bbad4f6e1`（v1422、main と同一ツリー）。
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の事実を根拠に調査。
- 検証方法: コード読み + git 履歴。**この環境では `python3` / `pytest` / `gh` を含む
  exec がすべて拒否されるため、監査への直接投入・テスト実行は未実施**
  （VERIFY-4999 / VERIFY-5026 / VERIFY-5046 と同じ制約）。
  判定は条件分岐の静的トレースと、同じ主張を検査する既存テストの表明との照合による。
  実行未確認を成功扱いしないため、その旨を明記する。

## 結論: **(b) 設計どおり・修正不要（PR #5208 で覆われている）**

PR #5208（commit `def73c106`、v1409）は現行 main（`bbad4f6e1` / v1422）に存在し、
#5202 と同じ形状（`battle_hero_retreat_select` / `choice='たいきゃく'` /
`ally_hp` が数値文字列 `'1'` / 選択済み・未使用・残りありの切り札）のレコードは、
監査コード上 **確定的に `None` を返して起票されない**。

---

## 1. 修正が main に存在することの確認

- `git log --oneline HEAD` 内で `def73c106`（`fix(audit): HP が数値文字列でも即死帯の
  退却を起票しない (#5202) (#5208)`）は HEAD の祖先（`git log` 一覧の 19 番目）。
  `git merge-base` / `git branch --contains` はこの環境では拒否されるため、
  log 出力による確認とした。
- 現行コード `src/docich/hanjuku/month_audit.py`:
  - `SAFETY_FLOOR_HP = 4`（L1158、PR #5132 / commit `1d5a80ebb` で導入。
    `judgment.battle.RETREAT_RESOURCE_FLOOR_HP` と同値であることはテストが保証）。
  - `_hp_value(value)`（L1161-1172、`def73c106` で追加）:
    bool → None。`str` かつ `value.strip().isdigit()` → `int(value.strip())` に正規化。
    その後 `_bounded_int(value, -1, 10**6)`（`_bounded_int` は `type(value) is int`
    のみ受理、L289-290）。
  - 即死帯ゲート（L1194-1196）: `outcome == 'retreat'` のとき
    `hp = _hp_value(metric.get('ally_hp'))`、`hp is not None and 0 < hp <= SAFETY_FLOOR_HP`
    → `return None`。
- `src/docich/hanjuku/version.py`: `BOT_VERSION = 'hanjuku-chart-v1422-ch9-oshiroga-aim'`
  （#5208 当時の v1409 から後続バンプ済み）。
- `ops/vm_actions/collect_diagnostics.py`: #5208 の差分はバージョン文字列リストへの
  `hanjuku-chart-v1409-audit-hp-floor-numeric` 追加のみ（投影対象列挙、監査ロジックではない）。

## 2. #5202 と同じ形状のレコードのトレース（静的、逐行）

投入レコード（issue の incident 形状を再構成）:

```python
{'decision': 'battle_hero_retreat_select', 'choice': 'たいきゃく',
 'general': 'セルリアク', 'enemy': 'ポワソン', 'castle': 'バビラリー', 'side': 'enemy',
 'observed_metric': {'ally_hp': '1', 'enemy_hp': 69, 'egg_uses': None,
                     'cards_selected': ['ノリウツール'], 'cards_used': [],
                     'cards_missing': [], 'card_pending': None}}
```

`_battle_resource_event`（month_audit.py L1175-）:

1. L1182-1185: `decision == 'battle_hero_retreat_select'` かつ `choice == 'たいきゃく'`
   → `outcome = 'retreat'`。
2. L1195: `hp = _hp_value('1')` → `'1'.strip().isdigit()` が真 → `int('1') = 1`
   → `_bounded_int(1, -1, 10**6) = 1`。
3. L1196: `hp is not None and 0 < 1 <= 4` → **`return None`**。
4. 呼出側 `_track_battle_resources`（L1322-1326）: `events` が空 → early return。
   `_build_battle_resource_incident` には到達せず、**起票されない**。

対照（`def73c106^` の旧コード）: 旧 L（現 1195 相当）が
`hp = _bounded_int(metric.get('ally_hp'), -1, 10**6)` で、`type('1') is int` が偽
→ `hp = None` → フロア判定自体をスキップ → `_battle_resources`（L1085-）で
`remaining = ['ノリウツール']` → `resources = ['card']` →
`kind = 'battle_retreat_with_resources'` → `_build_battle_resource_incident` で
`confidence = 'high'`（`cards_unconfirmed_only = False`: ノリウツールは
`hanjuku_reference.CARDS` で `egg_drop: 0` かつ `cards_unclassified` は
export されない、§5 参照）→ `auto_file = True`。**#5202 の high 起票と完全に一致**。

### 境界値の確認（`_hp_value` の受理域）

- `'1'`〜`'4'`（前後空白・ゼロパディング含む）→ 即死帯 → None（起票しない）。
- `'5'` 以上の数値文字列 → フロア外 → 資源があれば起票（int と同じ）。
- `'0'` / `'-1'` / `'1.0'` / float `1.0` / bool / 非数字 → `isdigit()` 偽または
  bool ガード → `_bounded_int` で None → フロアをスキップし従来どおり起票。
  `#5132` 以前の非 int 値の扱いと同一で、拡大したのは「数字文字列」のみ
  （範囲は PR の主張どおり最小限）。float や小数文字列が即死帯に来る経路は
  現状の生産者では想定外であり、実害は限定的と判断する。

## 3. HP を読む箇所の経路別整理

監査内で HP を起票判定に使うのは `_battle_resource_event` の即死帯ゲート
（L1194-1196）のみ。経路別:

| 経路 | レコード | HP ゲート | 扱い |
|---|---|---|---|
| 退却（選択） | `battle_hero_retreat_select` + `choice=='たいきゃく'` | `outcome='retreat'` → `_hp_value` | 文字列・int で同一 |
| 退却（ preempt ） | `battle_retreat_preempted_card` | 同上（同一 `if outcome == 'retreat'` ブロック） | 同上 |
| 敗北 | `battle_result` + `outcome=='loss'` | **HP ゲートなし**（#5132 導入時から非対称） | 資源があれば起票 |

- 損失経路にフロアが無いのは設計どおり: `SAFETY_FLOOR_HP` のコメント
  （L1153-1157）は「即死帯での札・卵の迂回を止めて退却するのは設計された
  安全弁」を根拠にしており、退却の正当化であって敗北の免責ではない。
  資源を残した敗北は従来どおり起票する（test_hanjuku_month_audit.py L702-713
  の既存表明どおり）。
- 発行イベントの表示フィールド `ally_hp` / `enemy_hp`（L1227-1228）は依然
  `_bounded_int` のまま: フロア超（例: `'20'`）で起票される退却イベントでは
  `ally_hp` が None 表示になる。起票判定への影響はなく cosmetic。
- その他の監査経路（`battle_menu_wasted` L2110-2214、camp 系 L1410 等）で
  `ally_hp` を読む箇所は、起票判定のゲートとしては存在しない
  （`battle_menu_wasted` は別 signature で本件の形状を再発行しない）。

## 4. 関連テストの状況（実行は環境制約により未実施）

- `tests/test_hanjuku_audit_safety_floor.py`（27 行、`def73c106` で拡張、HEAD に存在）:
  - `test_the_audit_floor_tracks_the_retreat_floor` — 監査と runtime のフロア定数一致。
  - `test_the_floor_holds_when_the_hp_arrives_as_a_numeric_string` —
    `ally_hp='1'`（文字列）と `1`（int）の両方で `_battle_resource_event` が None
    であることを表明。#5202 形状そのものであり、§2 のトレースと一致。
  - `test_a_retreat_above_the_floor_still_reports_the_leftover_card` —
    `ally_hp=20` で `battle_retreat_with_resources` + `cards_remaining=['ノリウツール']`
    を起票することを表明（過剰抑制でないことのガード）。
- `tests/test_hanjuku_month_audit.py` L702-797
  (`test_battle_resource_events_are_classified`): int 1/2/4 → None、5 → 起票、
  `battle_retreat_preempted_card` 経路、`cards_missing` / egg_drop 分類を網羅。
- `tests/test_hanjuku_issue_4999.py` L39-43: runtime 側 `_retreat_immediate_loss`
  （`ally_hp`: None→False, 0→False, 4→True, 5→False, 12→False）の既存表明。
- 実行: 本環境では `python3` / `pytest` の起動自体が拒否されるため未実行。
  テスト内容と現行コードの静的照合では、全表明は現行コードと整合する
  （§2 トレースが `test_the_floor_holds_...` の主張と同一条件・同一結果）。

## 5. 参考: `battle_card_announce_retry` / `battle_card_remaining_unconfirmed` と
       #5192/#5194/#5198 の `cards_unclassified` 監査穴

- 本件系列は「選択したが告知署名が未校正で消費を確定できない札」が
  `cards_remaining` として監査へ届いた形。`cards_unconfirmed_only` が True なら
  medium に落ちる（L1258-1261）が、#5202 は high で起票された。
- high になった理由（現行コードで確認）:
  1. `battle_card_remaining_unconfirmed`（`flows/battle_menus.py:567`）は
     記録のみで **`cur['cards_unclassified']` に追加しない**
     （追加するのは `_card_use_unclassified`、judgment/battle.py:152-196）。
  2. `battle_hero_retreat_select` の `observed_metric`（v≤1413 の emitter、
     `e91428f7e` で削除済み。`git show e91428f7e^:.../battle_menus.py` L401-419）
     には `cards_unclassified` が含まれない。現行の
     `battle_retreat_preempted_card`（`flows/battle.py:1395-1398`）の metric も
     含まない → **`metric['cards_unclassified']` が監査へ届かない**
     → `cards_unconfirmed_only = False` → high。
- つまり PR #5208 は「HP 文字列の即死帯」を塞いだだけで、この export 穴
  （#5192/#5194/#5198 の系統）**は塞いでいない**。同一系列が HP > 4 の退却で
  起きれば、依然として `battle_retreat_with_resources` が high で起票されうる。
  これは別 Issue の既知の穴であり、#5202 の判定には影響しない。

## 残存観察（本件の判定には影響しない範囲の記録）

- runtime 側の HP 読み取りは `type(hp) is int` のまま:
  `judgment/battle.py:1969`（`_retreat_immediate_loss`）、`:1526`
  （`_critical_survival_needed`）、`os_layer/commands.py:385`
  （`_human_menu_open` の `command_open_pending` ゲート）等。
  `cur['ally_hp']` が数値文字列なら **runtime の即死帯も同じく無効化される**。
  #5202 の系列には `battle_command_open_requested` が含まれ、このレコードは
  両 HP が正の int のときしか記録されないため、文字列化は系列の途中で起きた
  はず。現行・v1373 双方の `cur['ally_hp']` 代入経路（screen decode → int）は
  全て int のため、文字列の発生元は repo 単体では未確定
  （永続 `hanjuku_bot.json` 経由の旧値等の可能性）。監査がログを防御的に
  正規化する #5208 の方向は妥当であり、runtime 側の文字列受理の要否は別件。
- v1414（`e91428f7e`）で退却は一律禁止となり `battle_hero_retreat_select` の
  emitter は削除済み。監査側は旧ランの記録型を引き続き受理する設計で、
  `battle_retreat_preempted_card`（`flows/battle.py:1395`）は現行でも発行される。
  両方とも同一の `_hp_value` ゲートを通るため本修正でカバーされる。
- `ally_hp` が `None` / 非数字文字列のまま即死帯相当の退却だった場合は従来どおり
  起票される（「読めない」は即死帯の証拠にしない、#5132 の fail-open 設計の継続）。

## 判定根拠まとめ

- 修正の存在: `def73c106` が HEAD の祖先、`_hp_value` + ゲート差し替えが
  `month_audit.py` L1161-1172 / L1195 に現存。
- 挙動: §2 の逐行トレースで #5202 形状レコードは `None`（不起票）。
  修正前のコードでは同レコードが high で起票されることも同一パスで再現可能。
- 経路対称性: 2 つの退却経路は同一ゲートを共有。損失経路のゲート欠如は
  #5132 時点の設計（本件の範囲外）。
- テスト: `test_hanjuku_audit_safety_floor.py` が同一形状を直接表明
  （環境制約により実行は未実施、静的照合は整合）。
- cards_unclassified 穴: #5208 では未対処・別問題（§5）。

→ **(b) 設計どおり・修正不要**

## 補足（未実施事項）

- `python3` / `pytest` / `gh` の exec がこの環境では拒否されるため、
  監査への直接投入・ローカルテスト実行・issue #5202 へのコメント投稿は未実施。
  `git add` / `git commit` も拒否されるため、本ファイルは verify ブランチ上の
  未コミットの作業ツリー変更として残す（commit は別環境で行う）。
  結論コメント本文は本ファイルをそのまま転記可能な形で残す
  （投稿は gh が使える環境で行う）。
