# 半熟英雄: 重複記録の抑止がJSON往復で効いていなかったのを直す (v400)

## 事象 (2026-11-05 実測)

v398 で「同じ判断は 1 回だけ記録する」ようにしたが、実機では効いていなかった。

- `chart_adjust_applied`: 11 分間に 24 件。うち 5 件以上が**同じ request_id と同じ step 列**
  (`bf8c3dee6398cea3` / `["A:bf8c3dee:M594ff8"]`)。
- `world_map_owners`: 1 時間に 147 件で、異なる旗の組み合わせは 9 種類だけ。
- live の policy (`hanjuku_bot.json`) の `chart_adjust_signature` は
  `["bf8c3dee6398cea3", ["A:bf8c3dee:Ma21426", "A:bf8c3dee:M22c018"]]`、
  `world_map_signature` は `[["アブダレ", "own"], ...]`。**どちらも list**。

原因は署名の型。コードは `tuple(...)` で作って比較していたが、policy は観測ごとに
JSON へ保存され次の観測で読み戻される。JSON は tuple を list にするため、
保存値と tuple の比較が毎回不一致になり、同じ内容でも記録されていた。

## 変更

- `os_layer/world.py`: `signature = tuple(sorted(flags.items()))` →
  `signature = [[k, v] for k, v in sorted(flags.items())]`。
- `strategy/replan.py`: `signature = (rid, tuple(...))` →
  `signature = [rid, [o["step"] for o in orders]]`。
- どちらも JSON 往復後も同じ値になる list だけを使う。判定条件・記録内容は変えない。

## 検証

- `tests/test_hanjuku_decision_dedupe.py` に JSON 往復テストを追加:
  `json.loads(json.dumps(mem))` を挟んでから同じ入力を 2 回適用し、記録が 1 件のままであること。
  署名が変わった計画は 2 件記録されることも確認する。
- 修正前のコードではこの 2 件が失敗し (2 failed)、修正後は 5 passed。
- 実機での `chart_adjust_applied` / `world_map_owners` の減少は v400 稼働後に確認する (未確認)。

## 残件

- 実機で減少を実測する (次ラウンド)。
- 第 6 章ボスセル (818,184) と第 7〜12 章のボスセルは未検証のまま。
- 第 6 章は通常 11 城を制圧済みでボス解禁。ボス隊の編成 (各城 1 将軍のため余剰 2 人が必要) を
  待っている段階で、ボス突入はまだ実測していない。
