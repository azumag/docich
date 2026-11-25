# VERIFY-5603 — card_miss ch4 ビクトリア / バグストーム

検証対象: docich#5603（run `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1513-duel-decline-funded`、ch4 3-5、ソーピニヨン vs ブカティーニ、城ビクトリア、side attack、12:52:46Z）。

## 結論

**(b) 既存修正でカバー済み** — v1513 時点では `card_flow` 凍結型の実欠陥だが、PR #5619 / v1520 が本経路をカバーしている。新規の未修正欠陥ではないため、修正 PR は作らない。

## 識別子の照合

現行 `month_audit.py` の式で再計算し、issue の値と一致した。

- fingerprint: raw `4|card_miss|バグストーム+ビクトリア` → `16b84040bd163c02`
- occurrence: raw `g1190-ch1v9|4|1795611166|バグストーム` → `a4af528cc3de2cffc6eb`

## 主因の特定

issue の証拠は次の通り。

- 画面: `egg_battle_menu`
- scrolls: 不明（`battle['summon_card_scroll']` 未作成）
- cursorY: 176
- 表示文言: `こうげきもうこうげき`（`きりふだ` なし）
- 携行札候補: バグストーム
- empty_egg=false / summon_egg_unavailable=false / enemy_summon_observed=true

v1513 の `_summon_card_row_hidden` は、先頭で `not cards_left or battle.get('card_flow') or 'きりふだ' in screen.text` なら揭示対象外とする。本件は `cards_left` にバグストームがあり、`egg_battle_menu` 上の `こうげき` 文言なので、`card_flow` が無ければ揭示 scroll が先に走るはずである。それでも scroll カウンタが作られず `こうげき` 確定まで達したため、揭示ゲートを止めた真値の `battle['card_flow']` が残っていたことがコード上強制される。

v1513 の `interrupted_flow` 救済は `stage in ('menu','down','list')` かつ `selection_planned` / `read_kit` を持たない flow だけを解放する。`announce` / `selection_planned` 系や `read_kit` の進行不能 flow は真値のまま残り、残りの携行札の揭示 scroll と再オープンを抑止し続けた。v1513 の emit には `card_flow` 証拠が無いため、正確な stage と凍結していた札名は issue 単体では確定できないが、#5538 と同じ `scrolls: 不明` の frozen `card_flow` 家族と判定できる。

バグストームは `BOSS_ONLY_CARDS`（バルムンクのみ）ではない。両軍半減の犠牲札であることは選択評価に影響し得るが、「開かずに攻撃する」ことの設計上の正当化にはならない。

## 既存修正との照合

- PR #5619（merge commit `f2409ad08`、v1520）は現行 main `886c2b8d9` の ancestor。現行コードでは `read_kit` flow を fail-closed に閉じ、`announce` / `selection_planned` flow の解放前は `こうげき` を確定せず、3観測で unclassified として解放して同じ観測から残り札の揭示 scroll へ戻る。emit・監査・issue 本文へ `card_flow` 証拠も追加済み。
- PR #5605 は敗北時残存資源の台帳値誤計上の修正で、本件経路ではない。
- PR #5615 は #5600 型（`scrolls=3` の揭示枯渇）の修正で、本件の `scrolls=不明` 型ではない。
- PR #5629 は召喚獣戦後の通常戦闘メニューで希少戦術なしでも携行札を開く補強であり、本件の直接原因ではない。
- PR #5607 は同一家族の先行修正だったが、レビュー記録どおり #5619 に置き換えられた。

## 検証

現行 main `886c2b8d9` で関連回帰テストを実行:

```text
PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q \
  tests/test_hanjuku_boss_egg_row_log.py \
  tests/test_hanjuku_summon_battle_carried_card.py \
  tests/test_hanjuku_month_audit.py \
  -k 'frozen_announce_flow or interrupted_defense_kit_read or carried_card or card_miss_projection'
9 passed, 188 deselected
```

## 残存する未確定事項

- 凍結していた `card_flow` の正確な stage と札名、発生元画面遷移は v1513 の issue 証拠だけでは確定できない。確定には run `g1190-ch1v9` の `hanjuku_decisions.jsonl` が必要。
- 実機 runtime での再発有無は read-only 検証範囲外。
- issue 本文の「卵なし・奥の手不可」は監査テンプレートの定型文であり、本件の測定値（empty_egg=false）とは区別する。
