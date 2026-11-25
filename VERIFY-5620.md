# VERIFY-5620 — [半熟英雄] 切り札を残して敗北: ch5 ポセイドン（battle_loss_with_resources）

- 対象: azumag/docich#5620。`battle_loss_with_resources` / signature `battle_loss_with_resources_with_card` / confidence `high`。
- fingerprint `2ccb70e080533ee6` / occurrence `8b48d760c16e0d713c8a`。
- 観測: ラン `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1519-sortie-close-retry`、ch5 5-3。
- 戦闘: ポセイドン vs カシス @ バスカビル、side=attack、HP ally 0 / enemy 20。卵残数 0、切り札 選択 [`クースカン`, `ミックミー`] / 使用 [] / 残り [`クースカン`, `ミックミー`]、退却選択 False。
- 検証 base: 現行 main `d814ad1f672797d29704dd0a7dd1e83e8580ea79`（hot-load marker は `hanjuku-chart-v1525-sortie-cards-eligible`）。
- 観測版の引用基点: `2df350ee6`（`BOT_VERSION = hanjuku-chart-v1519-sortie-close-retry`）。この marker commit は現行 main の ancestor ではなく、同番号 v1519 の並行 suffix があるため、本記録では「観測 marker が指すコード」としてだけ使う。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。run 本体の `hanjuku_decisions.jsonl` / frames は VM 側のため直接参照せず、issue 内包 JSON・git オブジェクト・現行/観測版コードを照合した。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — カード側の「選択済み・消費未確認」証拠を落とす台帳/監査欠陥が現行 main に残る**

ただし結論の内訳は重要で、次の2つを分ける。

1. **観測版 v1519 にあった frozen `card_flow` 解放前の攻撃確定は実欠陥だったが、その部分は PR #5619 / v1520 で修正済み**である。16:35:22Z の `battle_card_selected` → `battle_card_candidate` → 16:35:24Z `battle_egg_general_attack` は、この修正が対象にした post-open frozen flow と同じ形である。
2. **#5620 にはもう1つ、PR #5619 ではカバーされない前段の欠陥がある**。最初の札（`クースカン`）は announce retry 後の `battle_card_list_unclassified` で `card_flow` だけがクリアされ、`cards_unclassified` / `cards_attempted` へ未確認証拠が残らない。そのため現行 main の監査でも、全残存札が実は消費未確認でも `cards_unconfirmed_only=False` と計算され、high confidence で起票されうる。

したがって本件は単なる誤検知ではないが、「2枚とも確実に未使用だった」ことを示す証拠でもない。実欠陥として残るのは、**未確認カードの証拠欠落により、medium downgrade されるべき card-side incident を high 起票しうる監査/台帳側の欠陥**である。修正 PR は本検証では作らない。

## 1. fingerprint / occurrence の再計算

現行 main `src/docich/hanjuku/month_audit.py:3341-3343` の `_fingerprint` は、

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw)[:16]
```

である。battle resource incident では `month_audit.py:1523` が signals 配列全体ではなく `[event['kind']]` を渡す。本件の raw は:

```text
5|battle_loss_with_resources|battle_loss_with_resources
```

Python3 で再計算した sha256 先頭16hexは **`2ccb70e080533ee6`** で、issue の fingerprint と一致した。

occurrence / incident_id は `month_audit.py:1518-1521` の raw:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{event['kind']}|{','.join(event['resources'])}|"
       f"{event.get('general')}|{event.get('step')}|{int(float(now))}")
sha256(raw)[:20]
```

issue 内包 JSONの値を代入すると:

```text
g1190-ch1v9|1190|battle_loss_with_resources|card|ポセイドン|A:bf0719e8:M6d12d0|1795624544
```

再計算した sha256 先頭20hexは **`8b48d760c16e0d713c8a`** で、issue の occurrence / incident_id と一致した。`1795624544` は `created_at=1795624544.6227221` の整数部である。

なお fingerprint は chapter/kind/kind だけの粗いキーであり、`with_card`、将軍、城、出撃シグナルは入らない。`order_start_without_launch` などの付随シグナルが issue 表にあっても fingerprint へは反映されない仕様どおりだった。

## 2. 資源判定と時系列

issue 内包 JSONでは、`cards_selected` の順序は [`クースカン`, `ミックミー`]、`cards_used` は空、`cards_unconfirmed_egg_drop` は [`ミックミー`]、`cards_unconfirmed_only` は False である。`cards_selected` は選択入力ごとの append 順であり、直前30行へは1回目の `battle_card_selected` が収まっていないが、counts では `battle_card_selected:2` / `battle_card_candidate:2` なので、16:35:16Z より前に1枚目の選択と candidate 記録があったことが分かる。

資源判断に関係する時系列は次のとおり。

| 時刻 | 記録 | 検証上の意味 |
| --- | --- | --- |
| 16:35:16Z より前 | `battle_card_selected` / `battle_card_candidate`（counts 上1回目） | 1枚目 `クースカン` の選択入力と、告知署名未校正による消費未確定 |
| 16:35:16Z | `battle_card_announce_retry` | 告知を確認できないため同じ札の再選択へ戻った。つまりこの時点で1枚目は「選択済み・消費未確認」 |
| 16:35:18Z | `battle_card_list_unclassified` | 再一覧を確定できず、`card_flow` をクリアして B/B で戻る。ここで1枚目の未確認証拠が落ちる |
| 16:35:21-22Z | `battle_summon_card_scroll` x3 → `battle_summon_card_open` | flow が消えたため、残り札として2枚目 `ミックミー` を開いた |
| 16:35:22Z | `battle_card_selected` / `battle_card_candidate` | 2枚目の選択入力。これも消費未確定 |
| 16:35:24Z | `battle_egg_general_attack` | 2枚目の flow が announce/selection_planned のまま凍っているのに通常攻撃へ戻った（v1519 の欠陥） |
| 16:35:44Z | `battle_card_unclassified` → `battle_result` | 戦闘終了時に残っていた2枚目だけが `_card_use_unclassified` で `cards_unclassified` へ入り、その直後に loss が記録される |

`クースカン` は `egg_drop=0`、`ミックミー` は `egg_drop=2` である（`src/docich/hanjuku_reference.py:63-75`）。したがって `ミックミー` だけは卵落とし型として構造的に消費確定不能だが、`クースカン` は `cards_unclassified` か `cards_attempted` に入らない限り medium downgrade の根拠にならない。

## 3. `cards_unconfirmed_only` / medium downgrade の照合

現行 main の `_battle_resources` は `cards_selected - cards_used` を `cards_remaining` とし、別途 `cards_unconfirmed_egg_drop`・`cards_unclassified`・`cards_attempted` を集める（`src/docich/hanjuku/month_audit.py:1279-1335`）。`_remaining_cards_are_unconfirmed` は、残存札の各 copy がこれらの evidence group のいずれかで説明できる場合だけ True を返す（同 `1261-1276`）。`_battle_resource_event` はその結果を `cards_unconfirmed_only` へ入れ（同 `1424-1426`）、True なら confidence を medium へ降げる（同 `1472-1475`）。

本件で監査が見えた状態は次である。

- remaining: `クースカン`, `ミックミー`
- egg-drop による構造的 unconfirmed: `ミックミー`
- 戦闘終了時に `_card_use_unclassified` へ入ったのは、残っていた2枚目だけ（事実上 `ミックミー`）
- 1枚目 `クースカン` は `battle_card_list_unclassified` で flow が消え、`cards_unclassified` にも `cards_attempted` にも入らない

このため `クースカン` だけが evidence の無い「plain card」扱いになり、`cards_unconfirmed_only=False`、confidence high になった。現行 main で同じ metric を `_battle_resource_event` へ通す probe でも、記録どおり `cards_unclassified=['ミックミー']` では False、もし1枚目も `cards_unclassified` に入っていれば True になることを確認した。

```text
as-recorded-unconfirmed-only False ['ミックミー']
if-first-card-booked-unconfirmed-only True
remaining ['クースカン', 'ミックミー']
```

つまり high 起票は、単に「plain card が残った」ことを測ったのではなく、**plain card に見えた `クースカン` の未確認証拠を上流が落とした結果**である。

## 4. 現行 main に残る証拠欠落の mechanisms

現行 main `src/docich/hanjuku/flows/battle_menus.py:2181-2187` は、札一覧を確定できない場合に `battle_card_list_unclassified` を記録し、そのまま `cur['card_flow'] = None` にして B/B で戻る。ここでは `cards_unclassified` を更新しない。

この分岐は、開札前の未選択 flow では正しい。一方、本件のように announce retry で `stage='list'` / `selection_planned=False` へ戻った後だと、flow の札は既に `cards_selected` に入っている。それでも同じ分岐で flow だけが消えるため、選択済み・消費未確認であることが battle end の metric へ伝わらない。

現行 main での最小再現 probe でも、`cards_selected=['クースカン']`、`announce_retried=True` の flow を unreadable list で4フレーム待たせると、出力は B/B、`card_flow=None`、`cards_unclassified=[]` のままだった。記録は `battle_card_list_unclassified` だけである。

`battle_end` は active flow が `stage == 'announce'` のときだけ `_card_use_unclassified` を呼ぶ（`src/docich/hanjuku/flows/battle.py:1646-1647`）。本件の1枚目は既に flow が None なのでここにも現れない。`battle_result` は `cards_unclassified` / `cards_attempted` を metric へ出す構造自体はある（同 `1899-1918`）が、上流で ledger に入らなければ監査へ届かない。

さらに `_battle_resource_event` の `battle_event` には `cards_unclassified` と `cards_attempted` が投影されていない。レンダラには「使用未分類（告知未校正）」を出す行がある（`src/docich/hanjuku/month_audit.py:4333-4341`）が、event に字段が無いため実際には空になる。これは起票後の検証可能性も下げている。

## 5. PR #5619 / v1520 と他の修正との関係

| 修正 | 本件への適用 |
| --- | --- |
| PR #5605 / v1514（stale egg ledger を残存資源にしない） | 卵側だけ。本件は `egg_uses=0` なので主因ではなく、カード側の evidence 欠落を直さない |
| PR #5615 / scroll rearm（scrolls=3 枯渇型） | 非該当。本件は scroll x3 の後に `battle_summon_card_open` まで到達している |
| PR #5619 / v1520（frozen `card_flow` 解放前は攻撃を保留） | **2枚目側の遷移はカバー**。召喚画面に残った announce/selection_planned flow を3観測保留し、`_card_use_unclassified` へ解放して攻撃を確定しない。ただし **1枚目は list_unclassified で既に flow が消えているためカバー外** |
| PR #5629 / v1522（希少戦術が無くても召喚獣戦で携行札を開く） | 開札機会を増やすが、本件は既に開札済み。未確認 evidence の保存や medium downgrade を直さない |
| PR #5633 / v1524（自城では切り札を使わない） | 本件は side=attack なので非該当 |
| PR #5636 / v1525（plan-empty sortie 監査の eligible stock） | `sortie_without_cards` 側。本件の battle resource / card ledger ではない |

PR #5619 の現行実装は `src/docich/hanjuku/flows/battle_menus.py:3222-3244` にある。`stage == 'announce'` または `selection_planned` の flow が召喚画面で進行不能なら、3観測までは `return []` で攻撃を保留し、上限で `_card_use_unclassified` へ解放する。回帰テスト `test_a_frozen_announce_flow_holds_attack_then_releases` を現行 main で実行し `1 passed` を確認した。

したがって、16:35:22Z の2枚目選択 → 16:35:24Z 攻撃という部分だけなら v1520 以降では再発しにくい。しかし、16:35:18Z に1枚目の flow を evidence なしで破棄する経路は v1520 の diff 対象外であり、現行 main でも文言どおり unchanged である。

## 6. 観測版 v1519 と現行 main の引用差分

| 対象 | v1519-sortie-close-retry（`2df350ee6`） | 現行 main（`d814ad1` / marker v1525） | 差分 |
| --- | --- | --- | --- |
| fingerprint式 | `month_audit.py:3324-3326` | `month_audit.py:3341-3343` | 同じ式 |
| occurrence式 | `month_audit.py:1509-1515` | `month_audit.py:1518-1524` | 同じ式 |
| unconfirmed集計 | `month_audit.py:1252-1267`, `1270-1326` | `month_audit.py:1261-1276`, `1279-1335` | 行番号は約+9。ロジックは同じ |
| `cards_unconfirmed_only` | `month_audit.py:1415-1417` | `month_audit.py:1424-1426` | 同じ evidence group。上流欠落への対策なし |
| list_unclassified | `battle_menus.py:2168-2174` | `battle_menus.py:2181-2187` | 行番号は約+13。`card_flow=None` で ledger へ残さない点は同じ |
| 召喚画面の interrupted flow | `battle_menus.py:3151-3167` | `battle_menus.py:3199-3246` | v1519 は未選択 menu/down/list のみ退避し announce 救済なし。現行は PR #5619 の hold/release あり |
| battle end の unclassified | `battle.py:1634-1635` | `battle.py:1646-1647` | active announce flow のみ救済する点は同じ |
| battle_result metric | `battle.py:1888-1907` | `battle.py:1899-1918` | `cards_unclassified` / `cards_attempted` emit は両版にある |
| card_miss の `card_flow` 診断 | なし | `month_audit.py:3505-3511`, `4153-4156` | PR #5619 で追加。ただし card_miss 側であり、battle_loss_with_resources の evidence 欠落は直さない |

## 7. 具体的な修正方向

実装はしないが、欠陥を塞ぐ修正方向は次のとおり。

1. `battle_card_list_unclassified` で flow をクリアする前に、`flow['card']` が既に `cards_selected` にある、または `announce_retried` 済みの場合は、開札前失敗ではなく「選択済み・消費未確認の中断」として `cards_unclassified`（または監査が同じ evidence group として読む専用 ledger）へ保存する。
2. 既存 `_card_use_unclassified` をそのまま使うと、`stage='list' and selection_planned=False` は開札前失敗扱いになるため、そのまま呼ぶのは不適切。選択済み中断を明示する helper / 引数を分け、開札前の既存テスト（`cards_unclassified` に入れない）を壊さないこと。
3. `battle_event` projection に `cards_unclassified` / `cards_attempted` を残し、issue 本文でも downgrade 根拠を検証可能にする。
4. 回帰テストは「announce retry 後に unreadable list → `battle_card_list_unclassified` → battle_result」で、残存札が egg-drop/unconfirmed evidence で全部説明できる場合に confidence が medium になること、現状は high のままであることを固定する。あわせて `battle_card_list_unclassified` が開札前 flow では従来どおり `cards_unclassified` を汚さない対照テストが要る。
5. `cards_selected` 全件を一律 unconfirmed 扱いにはしない。plain card の high 起票を残す現行設計（例: `test_plain_card_loss_stays_high_confidence`）があるため、直すべきなのは「retry/中断済みだった事実の evidence が消える」一点である。

## 8. なぜ (b)(c)(d) ではないか

- **(b) ではない**: frozen flow への攻撃は PR #5619 / v1520 でカバーされるが、list_unclassified による1枚目の evidence 欠落と監査 high 起票は現行 main に残る。
- **(c) ではない**: v1519 で実際に、消費未確定 flow を残したまま通常攻撃へ戻った記録がある。また現行監査の high/medium 判定を狂わせる再現可能な証拠欠落がある。単なる監査の読み違いではない。
- **(d) ではない**: 2枚が実際に発動したかは確定できないが、結論に必要な「未確認 evidence が落ち、high 起票される機構」は issue 内包時系列と現行コードだけで確定できる。実発動の有無は残る検出限界であり、今回の (a) 判定の前提ではない。

## 検証メモ

- 実行した検証: sha256 再計算、issue 内包 JSON 照合、観測版 `2df350ee6` と現行 main `d814ad1` の静的照合、現行 main での `_battle_resource_event` probe、announce retry 後の unreadable list probe、PR #5619 回帰テスト1件。
- `test_a_frozen_announce_flow_holds_attack_then_releases`: `1 passed`。
- ドキュメントのみの追加のため、実装変更や本番操作は行っていない。
