# 半熟英雄: 相手HP1で切り札が使われたらIssueを起票する（v1509、2026-11-25）

## オーナー指示

> また、再発しないように、HP1で切り札が使われたのをトリガーにしてissue発行するようにして

v1506 で、ボス戦以外の相手将軍HP1では切り札・奥の手・卵を使わず白兵で押し切る
ようにした。この規則が将来の変更で壊れたとき、勝敗だけでは気づけないため、
`month_audit` に再発検知を追加する。

## トリガー

`hanjuku_decisions.jsonl` の 1 レコードが次のすべてを満たすとき、1 件の
incident を seal する。

- `decision == 'battle_card'`（カードを開く判断が出た最初の永続レコード）
- `enemy_hp == 1`（数値文字列 `"1"` も許容）
- ボス戦ではない。判定は `judgment.battle._boss_battle` と同じ証拠を使う:
  `chart.BOSSES` の章ボス名、`reference` のボス将軍・ボスモンスター、
  ボス城、同一戦闘の `measured_boss_entry`。
- `card` が名前付きの文字列。

カードの選択・実使用告知は待たない。選択が届かず告知が校正されない場合でも、
「カードを開く判断が出た」こと自体が規則違反の再発だからである。

## 出力

- incident kind: `battle_card_at_hp1`
- signature / fingerprint: `battle_card_at_hp1`
- confidence: `high`
- auto_file: true（既存の `spawn_issue_filer` 経由で起票）
- Issue title:
  `[半熟英雄] ボス戦以外の相手HP1で切り札を使用: <札> (ch<章> <敵または城>)`
- 本文: 使用札、戦闘の ally/enemy/castle/side、敵HP1・自軍HP・敵兵士、
  時刻、画面種別、stage、resulting_event、カードダメージ判定の role、
  判断理由。末尾に v1506 の `_hp1_resource_hold` が効いていれば開かれない
  ことを明記する。
- 同じ fingerprint の open Issue があれば、再発コメントを追加する。
  コメント cooldown は 900 秒。

## 重複防止

`state['card_hp1_ids']` に incident_id（runtime・章・時刻・札・敵）を最大20件
保持し、同じ `battle_card` レコードを再観測しても二重に seal しない。
同じ戦闘で2枚目以降のカードが開かれれば、それぞれ別の occurrence になる。

## テスト

`tests/test_hanjuku_month_audit.py`

- 非ボスHP1の `battle_card` で incident が1件 seal され、title/body/comment に
  札と敵HP1が出る。同じレコードの再観測では増えない。
- ボス名・ボス城・HP2・`battle_card_selected`・札なしでは seal しない。
- `enemy_hp` が文字列 `"1"` でも検知し、不正な side/role は None、長い理由は
  200文字に丸める。

## 未確認・残件

- 自然ランでの起票は未確認。次回以降、同じ fingerprint の Issue が出た場合は
  `battle_card` の発生箇所（HP読取・ボス判定・カードフロー）を確認する。
- この監査は入力も方策も変えない。起票に失敗してもゲーム進行は止めない
  （既存の `month_audit_failed` 経路）。
