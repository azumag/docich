# PAPER コーナー中のコメント返信

PAPER コーナー（メイン画面 `paper-view`）の表示中、視聴者の「お金増えた？」「儲かった？」に
対して、ソ連ゲームのスコア・建国統計を現在の状況として答えてしまい、コーナー自身の損益を
答えない問題への修正（2026-11-19）。

## 契約

- 返信プロンプトのゲーム状態メモは、docich が所有するゲーム別 profile
  （[`game_profile.py`](../../src/docich/comment/game_profile.py)）から生成する。
  `paper-view` の profile は [PAPER コーナー専用モジュール](../../src/docich/comment/paper_corner_profile.py)。
- 事実の出所は `<state_dir>/trading/status.json` の `performance_summary` と資金参照値。
  読み取り専用で、通常ファイル・256KiB 以下・`mode=paper` のときだけ採用する。
  symlink、破損、非 paper、過大ファイルは「未取得」として扱う。
- 答える内容は、累積損益（評価込み）が元手より増えているか減っているか、本日の確定損益、
  含み損益。金額は円に丸め、符号を付ける。累積損益が無い（評価待ち）ときは 0 円と偽らない。
- 返信はまず PAPER 取引（仮想資金・模擬売買）だと明言し、そのうえで数字を答える。
  実口座・実発注はなく、実収入ではないことにも触れる。
- 同じ数字を画面メモ（ui_memo）にも短く出す。カテゴリ別テンプレートのうち
  chitchat と card_gacha / raid / sing_request は game_state スロットを持たないため、
  疑問符のない短い反応が chitchat に分類されても、画面メモから損益を答えられるようにする。
- 最終更新が 180 秒より古い場合は参考値として扱い、最新の損益と断定しない。
- ソ連ゲームのスコア・建国・戦略改善の話とは混ぜない。

## 限界

コーナー開始時点の損益スナップショットは保存していないため、「このコーナーの開始から
いくら増えたか」だけを分離しては計算しない。表示中の累積損益（実験開始からの評価込み）と
本日の確定損益を使う。

## 検証

`python3 -m pytest -q tests/test_comment_paper_profile.py tests/test_comment_game_profile.py tests/test_comment_contexts.py`

- 損益がプラス／マイナス／評価待ち／古い／未取得の各ケースで、事実と返答ルールが変わること。
- launcher `bin/docich-comment-profile paper-view` が `DOCICH_STATE_DIR` または
  `DOCICH_GAME_SWITCH_CANONICAL_FILE` の親から status を読むこと。
- Soren のコメント生成シェルが使う各スロット（game_state / ui_memo / game_rules / ops_lines）が
  1500 文字・禁止文字の制約内であること。
- general_question と chitchat の実テンプレートに、それぞれのスロット経由で損益が入ること。

実 LLM の返信品質、本番 VM の status 鮮度、Soren worker への反映は、このテストでは確認しない。
