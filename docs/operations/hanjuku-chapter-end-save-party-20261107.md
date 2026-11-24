# 半熟英雄: 章末セーブ確認＋先発隊で次章を確定する（v513, 2026-11-07）

## 事象

2026-11-07 22:58 のオーナー報告「半熟が、第二話に進んでるのに第一話と勘違いしていて進まない」。
実プレイ中の g1153 は第2話の先発隊を選択した後も方策メモリの章が 1 のままで、HUD に
「2わ」が出る 22:56:20 まで第1話の座標・注文を使っていた。状態表示も第1話のままだった。

## 実測（g1153, 2026-11-07）

| 時刻 | 実画面・記録 |
|---|---|
| 20:34 | 新規開始（g1147 の checkpoint から resume、chapter 1） |
| 22:41:29–22:48:30 | ボス城（けっかい）でクイーン戦。`battle` は `enemy=クイーン` のまま |
| 22:48:34 | yes/no「セーブ2 セーブ3 セーブをしゅうりょうして…よろしいですかな?」。ボットは `unclassified_prompt` で A。**章は 1 のまま** |
| 22:48:37 | 先発隊の選択「つぎのたたかいにそなえて…」（header は `2ねん12のつき48G`、章数字なし） |
| 22:48:45 | 先発隊3人（ゼウス・ヴィーナス・ココット）を確定 |
| 22:52:52 | ボス戦の結果メニューで `chapter_1_boss_defeated` を記録（章送りはまだ） |
| 22:53:03–22:55:53 | 次章の地図（HUD・文字なし、frame-076〜082）。入力なし 174 秒 |
| 22:56:20 | 月メニュー HUD「2わ 3ねん 1のつき 526G」でようやく `chapter_seen` 2 |

同じ並びは過去にも実測されている。

- g1059 2026-10-28 05:21:42「セーブをしゅうりょうして…」→ 05:21:45 先発隊 → 05:28:30 に
  敵将軍ペコリーノで `chapter_seen` 3（第3話）。直前の戦闘メモリは `enemy=にせヒーロー`。
- g478 2026-09-30 00:31:19 同セーブ確認 → 00:37:45 に敵将軍ピオーネで `chapter_seen` 2。

## 原因

章の確定材料が HUD の「Nわ」、次章にしかない城名、次章以降に初登場する敵将軍に限られていた。
章末セーブ確認と、その直後に始まる先発隊の選択はどちらも実画面で読めていたが、章の材料として
使われていなかった。先発隊は「章開始の画面」であるのに、確定は後続の材料待ちになっていた。

## 変更（v513 `hanjuku-chart-v513-chapter-end-save-party`）

- `flows/prompts.py`: yes/no の本文に「セーブ」と「しゅうりょうして」の両方を読んだときだけ
  `mem['chapter_end_save'] = {'chapter': 現在の章, 'tick': 観測tick}` を記録（`prompt` decision の
  `strategy_variant='chapter_end_save'`）。この時点では章を進めない。
- `flows/starter.py`: 先発隊画面（`starter_list` / `starter_confirm`）でマーカーを消費し、
  次のすべてを満たすときだけ `_enter_chapter(chapter+1, evidence='chapter_end_save_party')` する。
  - マーカーの章が現在の章と一致する
  - その章のボスが戦闘メモリにある、`boss_defeated == chapter`、またはボス城が確保済み
  - マーカーから 600 観測（約15分）以内
- 章替わりで `chapter_end_save` を破棄（`strategy/campaign.py` の章スコープ初期化へ追加）。
- `version.py`: v513 へ更新（ホットロードは観測ごとなので、次の観測から反映される）。

フェイルクローズ: 章末セーブだけ、先発隊だけ、ボス未確認では章を進めない。ゲームオーバー後の
再開先発隊が同じ画面を出しても、その章のボス戦がメモリに残っていなければ進めない。

## 検証

- 追加: `tests/test_hanjuku_chapter_end_save_party.py`（9件）— 既存の合成フレームで、セーブ確認の
  記録だけでは章が動かないこと、先発隊で章が 1→2 になり、章スコープ（orders / map_registration）が
  初期化されること、章末セーブなし・ボス未確認・期限切れ・別章のマーカーでは進まないこと。
- 既存: starter / chart bot / resume / trade / bijin / prompt 系のテストを実行（下記「実行結果」）。
- 実配信での次章送り（第2話→第3話）はこの変更のマージ・配備後に観測する。未観測なら未確認と記録する。

## 残件（本変更の対象外）

- g1153 22:53:03–22:55:53 の 174 秒無入力（次章の地図で HUD・文字・カーソルが読めない）は、
  章の誤認とは別の地図ローカライズ問題。今回の変更で章は 22:48:37 に 2 になるが、同じ無入力が
  再発する可能性は残る。frame-076〜082 を根拠に別途調査する。
- 章の結果メニューが先発隊より後に来る順序はそのまま（変更しない）。

## 実行結果

```
python3 -m pytest -q tests/test_hanjuku_chapter_end_save_party.py
9 passed
python3 -m pytest -q tests/test_hanjuku_starter*.py tests/test_hanjuku_chart_bot.py \
  tests/test_hanjuku_home_label.py tests/test_hanjuku_general_repair_budget.py
383 passed
python3 -m pytest -q tests/test_hanjuku_prompt_outer_frame.py tests/test_hanjuku_trade_guard.py \
  tests/test_hanjuku_trade_refusal.py tests/test_hanjuku_resume*.py tests/test_hanjuku_chart_chapters.py \
  tests/test_hanjuku_bijin_guard.py
393 passed, 4 errors（test_hanjuku_resume_failed_owner.py の pytest_plugins 併走時のみ。
単独実行では 4 passed。変更と無関係の既存の選択依存）
```
