# 採用済み計画が全滅したとき暫定出撃が止まる（g1107、2026-11-03）

## 症状

第5話（g1107）で、本拠地に将軍が9人待機し、落ちた城が2つある状態のまま、
月が 44-6 から 44-7 へ進んでも出撃が1件も始まらなかった。
読み取り専用 diagnostics の2時点（07:24Z / 07:33Z、約9分・447観測）で、
`captured_count` 6・`castle_loss_count` 2・`garrison`（アルマムーン9／オトラント1／
バスカビル1）・計画の `pending` 4件がすべて変化せず、`sorties` にも新しい行が増えず、
`active` は null のまま、画面はフィールドのマップだった。

## 原因

`strategy.replan._off_chart` は、速度無視チャート（スピードラン）が無効な章で
`_off_chart_mechanical` を呼ぶ。そこでは `_plan_pending(mem)` が偽のとき
`_mechanical_chart` へ進むが、この関数の先頭が

    if (mem.get('chart_plan') or {}).get('request_id') == rid:
        return

だった。採用済み計画の request id が現在の状況と一致していると、
**その計画に開始できる指示が1つもなくても**何もせず返る。
`map_step` は `next_order` が None のとき `_off_chart` を呼び、それでも None なら
入力を返さないので、結果として毎観測「待つ」だけになる。

request id は章・占領城・計画外の指示状態から作るため、城を取れない限り変わらない。
つまり出撃できない状態が、出撃しないと変わらない request id によって固定される。
速度無視チャート側の経路には「採用済み計画が尽きたら暫定出撃1件に落とす」処理が
既にあり、機械側の経路だけが抜けていた。

g1107 の計画が機械計画でないことは、月が替わっても計画が破棄されていないことから
切り分けた（機械計画は digest の変化＝月替わりで破棄され、同じ内容からは同じ step 名で
作り直される）。

## 修正（v377）

`_mechanical_chart` の早期 return を、同じ状況の計画が既にある場合の
`_mechanical_interim` 呼び出しに置き換えた。計画そのものは残す（新しい状況で
request id が変わったときにだけ置き換わる）。暫定出撃が作れない場合は従来どおり
`chart_interim_hold` を記録する。

## 確認の仕方

`bot_version` が v377 になったあと、採用済み計画の全指示が開始できない状況で:

- `chart_interim_order` が出て、`sorties` に `I:` の行が増える。
- マップ画面で入力を返さない観測が続かない（`active` が null のまま固定されない）。
- 計画は消えない（`chart_plan` の step が同じ状況の間は維持される）。

回帰テストは `tests/test_hanjuku_plan_exhausted_interim.py`。
conftest はテスト全体でスピードランを有効にするため、このテストだけ明示的に無効化する。
