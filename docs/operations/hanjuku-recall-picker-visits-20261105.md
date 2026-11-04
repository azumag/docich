# 帰還ピッカーの往復検出が効かない（v397、2026-11-05）

## 症状

v396 で「同じ位置の再訪3回でフォールバック」を入れたが、実際の往復では
`loops` が0のままで、ピッカーが止まらなかった。

g1133（第6話、2026-11-05 02:43）の実測:

    cursor [175.0, 90.5] press left  still 1 loops 0
    cursor [175.0, 90.5] press down  still 2 loops 0
    cursor [73.0, 120.5] press right still 0 loops 0
    cursor [73.0, 120.5] press right still 1 loops 0
    cursor [73.0, 120.5] press up    still 2 loops 0
    cursor [175.0, 90.5] press left  still 0 loops 0   ← 同じ往復が続く

## 原因

カーソルは各城で2回同じ位置に留まってから次へ移る（3観測で1歩）。
v396 の履歴は「動いた位置」を並べる方式で、間に挟まる `still` の観測で
履歴を消していたため、同じ位置の再訪を一度も数えられなかった。

## 修正（v397）

- 位置ごとの訪問回数 `picker_visits` を持つ。8px以上動いたときだけ、
  その位置の回数を1つ増やす。
- いずれかの位置が `PICKER_LOOP_LIMIT`（3）回に達したら、v396 と同じ
  フォールバック（今いる城で確定）へ進む。
- 8px未満の細かい揺れは訪問と数えない（移動中のジッターで諦めない）。
- 記録は最大 `PICKER_VISIT_SLOTS`（12）位置に有界化する。

## 確認の仕方

- `bot_version` が v397 になったあと、往復中に `camp_recall_picker_step` の
  `loops` が 1,2,3 と増え、3で `camp_recall_picker_fallback` →
  `camp_recall_requested` に進むこと。
- `tests/test_hanjuku_recall_destination.py` が、各城で2回留まる往復でも
  3訪問でフォールバックすること、細かい揺れでは諦めないことを確認する。

## 未確認

- ピッカーの移動規則（道路沿いか、城の順序か）は未解明。今回は往復を
  有限回で打ち切ることだけを直した。
