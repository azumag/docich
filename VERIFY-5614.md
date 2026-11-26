# VERIFY-5614 — card_miss ch8 ノリウツール

## 結論

**(a) 実欠陥 — scroll 枯渇型。現行 main には残余経路が残る。**

ただし、同じ残余経路を修正する実装・回帰テスト済みの PR が既に存在する: **PR #5652**（open、`verify/5632`）。本検証では重複する修正 PR を新たに作らず、残件は **PR #5652 のマージ**とする。

## 1. 対象とハッシュ照合

- issue: azumag/docich#5614
- 判定: `card_miss` / confidence `high`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1515-monster-menu-total-cap`
- 章・月: ch8 / 13-11
- 戦闘: カシス vs ブラウニー / 城 おかざき / side `attack`
- 画面: `egg_battle_menu` / scrolls `3` / cursorY `176`
- 表示文言: `こうげきもうこうげきたまごをつかう`
- 携行札候補: ノリウツール, クースカン, ミックミー

現行 `src/docich/hanjuku/month_audit.py` の式で sha256 を再計算し、両方一致した。

- fingerprint raw: `8|card_miss|ノリウツール+おかざき`
  - sha256 先頭16 hex: `973e78559356ed41` — issue と一致
- occurrence raw: `g1342-1fe0734c|8|1795618352|ノリウツール`
  - sha256 先頭20 hex: `540f09624f71adf44cb0` — issue と一致
  - `1795618352` = 2026-11-25 14:52:32 UTC（issue の時刻と一致）

## 2. 観測版での主因

観測版 `hanjuku-chart-v1515-monster-menu-total-cap` は commit `02e9c4fb6`（PR #5608、2026-11-25 23:06 JST）のマーカーで、本件は同日 23:52:32 JST に発生した。PR #5615 の merge は 2026-11-26 01:51 JSTなので、本件は **#5615 適用後の再発ではなく、#5615 導入前の発生**である。

観測版の `egg_battle_step` では、召喚獣戦で携行札が残り `きりふだ` が見えない場合だけ、

```text
scrolls < SUMMON_CARD_SCROLL_LIMIT (=3) なら down
```

としていた。`scrolls == 3` に達した後は再開機構がなく、そのまま通常の攻撃分岐へ落ち、`こうげき` を確定して `battle_boss_card_missed` を emit する。したがって本件の直接原因は、**切り札行揭示スクロール 3 回を使い切った後に、描画途中・入力抜けへ対する再開がないまま攻撃へ進んだこと**である。

`scrolls=3` かつ表示文言に `きりふだ` がない点は、#5612 / #5574 の `scrolls=0` で見られた frozen `card_flow` 型とは別である。凍結 flow では `battle['card_flow']` が真値なので `_summon_card_row_hidden` が先頭で reveal を止め、同じ形で scroll 枯渇へ進む主経路ではない。

## 3. 現行 main での再現確認

現行 main `c99ba52c3`（`hanjuku-chart-v1533-egg-unused-measured`）で、本件と同じ最小状態を合成した。

- `planned_cards=['ノリウツール','クースカン','ミックミー']`
- `summon_card_scroll=3`
- `egg_battle_menu`
- text `こうげきもうこうげきたまごをつかう`
- cursorY 176
- side `attack` / castle おかざき

結果:

```text
acts: [pad('a')]
battle_summon_card_exhausted
battle_egg_general_attack
battle_boss_card_missed card=ノリウツール
observed_metric.cards_left=['ノリウツール','クースカン','ミックミー']
observed_metric.scrolls=3
observed_metric.card_flow=None
observed_metric.cursor=176
```

また関連テストを実行し、現行仕様がこの残余経路を固定していることを確認した。

```text
PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q \
  tests/test_hanjuku_boss_egg_row_log.py \
  -k 'fresh_scroll_exhaustion or exhausted_card_scroll_re_arms'
=> 2 passed, 16 deselected
```

`test_a_fresh_scroll_exhaustion_marks_evidence_before_attacking` は、初回枯渇で `battle_summon_card_exhausted` を記録したうえで攻撃し、`battle_boss_card_missed` を emit する現行 main の挙動を明示している。

## 4. PR #5615 / #5619 / 関連 issue との異同

### PR #5615（merged）

`_summon_card_rearm` を追加し、scroll 枯渇後にも 8 観測の間隔を置いて最大 2 回の揭示再開を試みる。方向は本件の原因に対応するが、現行 main の実装では初回枯渇時には tick を置いて `False` を返し、re-arm は後の観測まで始まらない。その猶予中に攻撃へ進めるため、**#5615 だけでは本件と同じ card_miss を防げない**。実際、§3 の再現では re-arm 前に `pad('a')` と `battle_boss_card_missed` が出た。

### PR #5619（merged）

`scrolls=0` / frozen `card_flow` 型の修正であり、本件の `scrolls=3` scroll 枯渇型ではない。本件をカバーしない。

### #5612（検証完了）

同ラン・同じノリウツールだが、`scrolls=0` で post-open frozen `card_flow` 型。PR #5619 が対象。本件とは別経路。

### #5574（検証完了）

ch11 ノリウツール。同じく `scrolls=0` の announce 凍結 `card_flow` 亜型で、マージ済み PR #5619 がカバーする。本件とは別経路。

### #5613（検証完了）

`sortie_without_cards` であり、戦闘内 card_miss ではない。卵落 0 でない札だけの在庫を携行可能と誤認した監査誤検知で、PR #5636 がカバーする。本件とは別問題。

## 5. 既存の修正 PR #5652 との関係

open PR #5652（`verify/5632`、v1534）は #5632 で同定された残余経路、すなわち **re-arm 猶予中に攻撃できる問題**を修正する。

同ブランチで §3 と同じ合成状態を実行した結果:

```text
acts: []
battle_summon_card_exhausted のみ
battle_egg_general_attack なし
battle_boss_card_missed なし
```

つまり初回枯渇では入力を保留し、re-arm の回復機会を残す。re-arm 上限到達後は `battle_summon_card_reveal_failed` へ分離され、card_miss として誤って起票しない設計になっている。本件の主因を直接カバーする。

## 6. 最終判定

- 発生当時の v1515 では、scroll 枯渇後に再開せず攻撃した実欠陥だった。
- 現行 main でも、PR #5615 の re-arm 回復前に初回枯渇の観測で攻撃できるため、本件と同じ card_miss は再発し得る。
- したがって結論は **(a) 実欠陥**。
- 追加実装が必要だが、同じ修正内容の実装・回帰テストが既に **PR #5652** に存在するため、検証記録だけの PR や重複修正 PR は作成しない。残件は **PR #5652 のマージ**。
