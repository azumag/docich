# VERIFY-5774 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: キャトルミュー (ch2 ボス)

## 結論

**(a) 実欠陥**

ボス城「ボス」での アンディーブ vs にせヒーロー戦で、携行していたキャトルミューを一度も選択・使用しないまま HP 36→0 で敗北した事象は実在し、誤検知ではない。原因は #5776 で owner が手動解析した2段ゲート、すなわち

1. 一撃撃破を保証できない札を damage gate が拒否しうる経路、
2. 瀕死時の退却判断が、未選択の切り札一覧を選択へ進めずに閉じる経路、

の合成だったと判定する。現行 main には #5777 の修正が入っており、退却のための一覧クローズ分岐は削除されている。また `nonlethal_egg_risk` による拒否は、開始HPに対して自軍HPが1/3以下の危険状態では撤回される。

個々の安全弁（卵リスク評価、g460 系の即敗北回避）は設計意図を持つが、少なくともこの局面では「使わなければ負ける」札を持ったまま敗北する結果を生じており、owner が修正をマージしている以上、設計どおりではなく実欠陥と分類する。

## 検証範囲と制約

- read-only 検証。コード修正・commit・push・PR・gh コマンドは行っていない。
- 本番 VM の decision log には SSH できないため、issue 本文、公開 GitHub の関連 issue/PR、およびこの worktree の現行コードだけを使った。
- `python3` / shell 実行は承認されなかったため、ハッシュの実際の SHA-256 再計算は未実施。以下に式と入力値を残す。
- `DOCICH_HANDOFF_PATH` を確認する shell 実行も承認されなかったため、非公開 handoff は未読。この検証は GitHub と worktree の現行コードを根拠にした。

## fingerprint / occurrence の式と入力値

現行 `src/docich/hanjuku/month_audit.py` の card_miss builder `_track_card_miss` から読んだ式は次のとおり。

### fingerprint

`month_audit.py` 3881-3882 行:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), 'card_miss',
                                     [card, str(row.get('castle') or '')])
```

`_fingerprint` は 3521-3523 行:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

本件の入力は issue 本文から:

- chapter: `2`（ch2）
- kind: `card_miss`
- card: `キャトルミュー`
- castle: `ボス`

したがって再計算対象文字列は、UTF-8 で:

```text
2|card_miss|キャトルミュー+ボス
```

期待値（issue marker）: `fe7c20e6149fc662`

式の形と長さ（16 hex）は一致するが、shell/python3 実行が承認されなかったため SHA-256 の再計算自体は未実施。

### occurrence / incident_id

`month_audit.py` 3805-3808 行:

```python
at = row.get('at') if type(row.get('at')) in (int, float) else now
raw = '|'.join(str(part) for part in (
    identity.get('runtime_id'), mem.get('chapter'), int(float(at)), card))
incident_id = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

`occurrence` marker は `_issue_markers`（3553-3557 行）で `incident_id` を出す。

本件の既知入力:

- runtime_id: `g1373-b4c4126f`
- chapter: `2`
- card: `キャトルミュー`
- at: issue 本文は `19:30:57Z` だけを表示し、日付を出さない。#5776 の手動解析（04:29:59〜04:30:57、JST表記と整合）と照らすと、おそらく `2026-11-27 19:30:57Z`。この推定なら epoch は `1795807857` で、再計算対象文字列は:

```text
g1373-b4c4126f|2|1795807857|キャトルミュー
```

期待値（issue marker）: `94cc14fc50c6bb558468`

ただし `at` の日付は issue 本文だけでは確定できない。正確な epoch は本番 decision row にしかないため、この入力は推定である。SHA-256 再計算自体も未実施。検品ゲートが再計算する場合は、上記の推定入力で一致するかを確認し、一致しない場合は `at` の日付/秒ズレを疑うべきである。

## 現行コードで確認した経路

### 1. `damage_not_a_kill` の意味

`src/docich/hanjuku/judgment/battle.py` 810-957 行の `_card_damage_gate` は、札の選択可否を評価する。`damage_not_a_kill` は 860-865 行で、一撃撃破ではないが、確実な正ダメージで敵を削る札を許可する役割である。ボス将軍については `_boss_card_wears_down_enemy`（781-797 行）が、通常の兵士撃破 proxy では拾えないボスへの削りを許可する。

にせヒーローは `hanjuku_reference.py` で boss general（1383-1388 行）、HP90（397-400 行）、兵士HP10（1403-1405 行）。キャトルミューは boss damage 90（103-105 行）なので、敵兵士吸収を考えても非致死の削り札として評価されうる。issue の `damage gate: allowed=true / role=damage_not_a_kill / source=evaluation` は、この許可側の評価が最終的に監査へ残ったことを示す。

重要なのは、`damage_not_a_kill` は「選択した」ことではなく「gate が選択を許可した」ことだけを意味する点である。実際の選択は card_flow の `select` や `battle_card_selected` で証明される。

### 2. card_flow 履歴の `gate_evaluation` が繰り返出る意味

`_note_card_flow_event`（138-169 行）は `_card_damage_gate` 実行ごとに `gate_evaluation` を battle 内の有界履歴（最大32件）へ追加する。`_card_damage_gate` は複数箇所から呼ばれる。

- survival menu の実一覧評価
- `_critical_safe_card` による退却延期可否の評価
- 代替候補の走査
- 最終選択直前の再評価

したがって `gate_evaluation` の反復は「何度も選択した」意味ではなく、「複数の判断点または再評価で何度も許否を計算した」意味である。issue に見える履歴は:

```text
23:gate_evaluation:キャトルミュー/damage_not_a_kill
24:gate_evaluation:ミックミー/damage_not_a_kill
25:gate_evaluation:キャトルミュー/damage_not_a_kill
26:gate_evaluation:キャトルミュー/damage_not_a_kill
27:open:-/survival_menu
28:gate_evaluation:キャトルミュー/damage_not_a_kill
```

`27:open:-/survival_menu` は救済メニューから切り札一覧を開いたことだけを示す。card は `-` で、選択ではない。`28` の評価後に `select` が見えないため、少なくとも issue 表示の範囲では選択へ進んだ証拠はない。

また renderer は `card_flow_history` の **最後6件だけ** を issue 本文へ出す（`month_audit.py` 4707-4714 行）。したがって、この6件だけで全履歴を断定はできない。一方で、終了時点に最も近い履歴として、開封後に選択へ進まなかった流れと整合する。

### 3. survival_menu での救済手段選択

`_survival_menu`（`src/docich/hanjuku/flows/battle_menus.py` 721-880 行）は、卵・切り札の救済手段を選ぶ。切り札行を選ぶと `_begin_card_flow(..., source='survival_menu')` が呼ばれる（857-863 行）。これが issue の `27:open:-/survival_menu` に対応する。

実際のカード選択は `_survival_card_list`（883-1007 行）で、実一覧の候補を `_card_damage_gate` へ通し、許可された候補から `_card_gate_choice` で選び、`select` event・`battle_card_selected` を記録する。

問題は #5777 以前の `card_list_step` にあった退却優先分岐である。#5777 diff によれば、旧コードは `flow['stage'] == 'list'` の間に `_hero_retreat_needed(cur)` と `_critical_safe_card(..., for_retreat=True)` を評価し、即敗北帯では `_critical_safe_card` が札を保留理由にしないと、未選択の一覧を B で閉じて `battle_hero_retreat_cancel_card` を記録した。未選択 flow の `_release_interrupted_card_flow` は card flow event を残さず `cur['card_flow'] = None` にするだけなので、履歴には open/evaluation の後の close が見えにくい。

これは #5776 の実測時系列そのものである:

1. `battle_card` でキャトルミュー活用を計画
2. `battle_card_damage_rejected`
3. `battle_survival_unavailable`
4. `battle_hero_retreat_open` / `battle_command_open_requested`
5. `battle_hero_retreat_cancel_card` で未選択の切り札一覧を閉じる
6. `battle_boss_card_missed`（敗北）

本件 #5774 の時刻 19:30:57Z は、JST 04:30:57 と整合し、#5776 が同じ戦闘を扱っていると判断できる。

### 4. issue の damage gate allowed=true と #5776 の rejected の関係

`month_audit` が出す `damage_gate` は `_card_miss_evidence`（`judgment/battle.py` 322-365 行）が `cur['card_gate_history'][card]` から取る最新評価である。`_remember_card_gate`（200-209 行）は card ごとに entry を上書きする。したがって、以前に `nonlethal_egg_risk` で拒否されていても、後の `damage_not_a_kill` 許可評価で上書きされれば、issue には allowed=true が出る。

このため issue の `allowed=true` と #5776 の `battle_card_damage_rejected` は矛盾しない。前者は終了時点までに残った最新評価、後者は時系列中の一度の拒否 decision である。ただし、コンパクトな issue 本文だけでは、どの時点で誰が拒否したか、`gate_refused` が全履歴のどこにあったかまでは復元できない。これは #5753 evidence の限界であり、誤検知とは別の観察限界として記録する。

## #5753 の bounded evidence / confidence tiering との突き合わせ

#5753 で導入された現行 `_card_miss_confidence`（`month_audit.py` 3727-3780 行）では、high は次を要求する。

- outcome が loss
- 終了HPが整数
- card_opened が bool、開封済みなら開封HPも整数
- enemy_summon_observed が bool
- critical_survival の needed/enemy_summon/boss/behind が bool
- 携行 provenance があり、`planned_cards`/`order_plan` だけではない実携行根拠を含む
- damage_gate の allowed bool と role がある
- card_flow_history がある
- gate allowed=true
- survival need（needed/observed/behind）がある

本件は issue 本文上、これらを形式的に満たす。

- outcome: `loss`
- 開封HP: 自36 / 敵90、終了HP: 自0 / 敵90
- critical: `needed=true / observed=true / behind=true / enemy_summon=false / boss=true`
- provenance: `planned_cards,order_plan,departure_binding,departure_role:rare`
  - `departure_binding` / `departure_role:rare` があるため high の実携行条件を満たす
- damage gate: `allowed=true / role=damage_not_a_kill`
- card_flow_history: 存在する

したがって `confidence: high` / auto-file は #5753 のルールどおりである。#5747 のように証拠欠損で判定不能ではない。

ただし evidence には次の限界がある。

- `damage_gate` は最新評価の上書きで、拒否履歴そのものではない。
- issue 本文の flow 履歴は最後6件だけで、`allowed` bool や HP を表示しない。
- 未選択の一覧クローズは card_flow event として残らない。
- そのため issue 本文単独では「rejected → retreat cancel → 未選択敗北」の完全な因果列は再構成できない。#5776 の owner 手動解析が補強証拠になっている。

この限界は high tiering の形式条件を否定するものではないが、今後の検証容易性としては改善余地である。

## #5777 の修正内容との突き合わせ

#5777 は merged。公開 diff と現行コードで確認した内容:

1. `src/docich/hanjuku/flows/battle_menus.py`
   - survival list（975-978 行）と通常の card list（2216-2226 行）で、gate 不許可でも `_card_refusal_retired(gate.role, cur)` が真なら拒否しない。
   - 旧 `battle_hero_retreat_cancel_card` 分岐は削除済み。現行 2143-2145 行には、退却は完全廃止なので退却のために一覧を閉じない旨のコメントがある。
2. `src/docich/hanjuku/judgment/battle.py`
   - `CARD_REFUSAL_RETIRED_ROLES = {'nonlethal_egg_risk'}`（965 行）。
   - `_card_refusal_retired`（968-978 行）は、自軍HPが開始HPの1/3以下の場合にだけ拒否撤回を返す。データ整合性系の拒否は残る。

注意点: PR 初期説明や owner 指示の表現は「札の拒否廃止」だが、最終実装は無条件廃止ではなく、`nonlethal_egg_risk` を低HP時に限定撤回する実装である。またボス戦限定ではなく HP 条件で効く。それでも本件の致命的だった「瀕死ボス戦で一覧を閉じる」経路は、低HPでの拒否撤回と retreat-cancel 削除の両方で直接潰されている。

現行 worktree の `src/docich/hanjuku/version.py` は `hanjuku-chart-v1592-boss-source-consolidation` であり、発生時の `hanjuku-chart-v1585-card-row-unread` より後である。#5777 の実機効果（実際のボス戦でキャトルミューが選ばれること）はこの read-only 検証では未確認。

## 過去検証との異同

### #5747 / #5749 系

- #5747 は同じ card_miss 種別だが、当時は outcome/HP/critical/provenance/gate/flow がなく、検証結論は **(d) 判定不能** だった。
- #5749/#5753 はその欠損を埋めるため bounded card_flow evidence と confidence tiering を追加した。
- #5774 はその新 evidence が載った事案であり、少なくとも形式条件では high に必要な証拠が揃っている。さらに #5776 の owner 手動解析があるため、#5747 とは違い **(a) 実欠陥** まで判定できる。
- 一方、最新 gate の上書き・最後6件表示・未選択 close の非記録という限界は残り、#5753 が目指した自己完結性は完全ではない。

### #5760 系

- #5760 は `battle_loss_with_resources` で、卵残存敗北の検出自体は正しく、結論は **(a) 実欠陥**。ただし主因は卵判断単体ではなく、step なし連戦での切り札 unclassified 帳簿と stale snapshot 再計画だった。
- #5774 も「資源を残して敗北した」検出自体は正しいが、原因は帳簿ではなく、battle 中の選択制御（damage gate の扱いと retreat による一覧クローズ）である。
- 両者とも誤検知ではないが、#5760 が帳簿/再計画の欠陥、#5774 が戦闘中メニュー判断の欠陥という点で異なる。

## 他の判定を採らない理由

- **(b) 設計どおり** ではない: 卵リスク安全弁や即敗北回避の個別意図はあるが、組み合わせが「有用札を持ったまま敗北」を生じ、owner が #5777 で修正をマージしている。
- **(c) 誤検知** ではない: 終了HP 自0/敵90、札 state=`leftover`、選択イベントなし、#5776 の独立した手動時系列があり、未使用敗北は実在する。
- **(d) 判定不能** ではない: #5753 以降の evidence が揃い、#5776/#5777 が同じ戦闘と修正対象を特定している。

## 未決・残件

1. SHA-256 再計算は shell/python3 実行が承認されなかったため未実施。式と入力値は上記のとおり。
2. `at` の正確な epoch（日付部）は本番 decision log がないと確定できない。上記 `1795807857` は 2026-11-27 19:30:57Z の推定値。
3. issue 本文だけでは `battle_card_damage_rejected` がどの札へ何回出たか、および `gate_refused` の全履歴位置を確定できない。#5776 ではキャトルミュー拒否とされている。
4. #5777 後の実機で、同じボス戦状況でキャトルミューが実際に選択・使用されることは未確認。

## 検品ゲート再計算（Muse、2026-11-28 08:03 JST）

- fingerprint：`sha256("2|card_miss|キャトルミュー+ボス")[:16]` = `fe7c20e6149fc662` — issue 値と一致。
- occurrence：`sha256("g1373-b4c4126f|2|1795807857|キャトルミュー")[:20]` = `94cc14fc50c6bb558468` — issue 値と一致。`at` の推定 epoch（2026-11-27 19:30:57Z）はこの一致により裏付けられた。
- 式は `month_audit.py` の `_track_card_miss`（L3805〜3808、L3881〜3882）と `_fingerprint`（L3521〜3523）の現行実装で確認。
