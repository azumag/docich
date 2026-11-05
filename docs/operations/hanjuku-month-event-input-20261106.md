# 月一イベント中のコマンド送り（あけまつ、v465、2026-11-06）

## 症状

半熟英雄 g1133 で、59年1月の月一イベント（あけまつ）の会話が進まず、Aが約150秒に1回しか
送られなかった。月一イベント中のコマンド送りができていない状態。

## 実測（本番 g1133、JST）

- 02:57:49 に新年イベントの会話が開始（`おめでとうございます! あたらしい ぼん くれ しょうがつ`）。
- 03:18 まで約21分、画面は kind `text` / phase `dialogue` のまま。
- `hanjuku_decisions.jsonl`: step 104020〜104123 の101観測連続で `planned_actions: []`。
  101観測ごとに `no_input_fallback` がAを1回だけ送る（約150秒に1回）。
- 同時刻の `hanjuku_bot.json`: `post_month_status` は `stage=task_menu`、
  `scope=[6, "59-1"]`、`steps=701` から増加。
- 03:03 に `post_month_status_unverified`（`observation_budget`）で `stage=blocked` になり、
  以後はAすら送られない。
- 画面: `screenshots/latest.png`（02:55）は「おめでとうございます!…」、03:06 の実画面キャプチャは
  「あけましてー!」。約11分で1行しか進んでいない。通常のテキスト送りなら数秒で終わる長さ。

## 原因

`post_month_status.step` の `task_menu` は、map / main_menu / 将軍一覧以外の画面をすべて
`[]`（入力なしで待機）で返す。新年イベントの会話は kind `text` なので、この owner が毎観測
claim して通常のテキスト送り（A）を抑止した。さらに待機中も `steps` を加算するため、
900観測で `blocked` に落ち、以後は恒久的に無入力になった。

将軍一覧・個別ステータス・城ステータスも kind `text` になり得るため、単純な「textなら手放す」
では既存の読み取り経路を壊す。名簿は `house.roster(screen)` で判別する必要があった。

## 変更（#3495 / #3496、v465-dialogue-yield）

- `flows/post_month_status.py`: kind `text` のうち、将軍一覧（`house.roster(screen)` が
  名前を返す）でも `status` / `wait_repair` / `wait_recall` の所有画面でもないものは
  `None` を返して所有を手放す。台詞は通常の A 送りで進む。
- 手放す画面では `steps` を加算しない（イベントの長さで確認予算を消費しない）。
- `BOT_VERSION` v465（`hanjuku-chart-v465-dialogue-yield`）。
- 本docと追加の回帰テストは別PR。修正本体は #3496。

## 検証

- 独立に実測した停止フレーム（03:06 の実画面キャプチャを 256x224 に縮小）で
  `classify=dialogue` / kind `text` を確認。`stage=task_menu` の状態で
  `decide` が A を返し、`steps` を消費しないことを確認。
- 追加した回帰テスト（`tests/test_hanjuku_post_month_status.py`）:
  - `task_menu` がイベント会話を手放し、steps を消費せず、map で task menu に戻る
  - `task_menu` は将軍一覧（kind text）を従来どおり所有する
  - `blocked` は会話を手放すが、map は保留し続ける
  - `decide` が実画面同型の会話フレームで A を返す
- 本番 g1133 は v465 の自動デプロイ後、battle 593 まで通常進行していることを確認
  （2026-11-06 03:35 時点）。

## 未確認・残件

- 次回の月一イベントで、Aが観測ごとに送られ会話が数十秒で終わることを実測する。
- 解放条件は `task_menu` 以外のメニュー系 stage にも及ぶ。将軍一覧は `house.roster` で保護されるが、
  hand や名前が読めない一覧フレームは解放対象になる。手元の実フレーム（`tests/fixtures`）には
  そのような一覧ページが無く、退行は未観測。
- `locate_reset` は text を B で閉じる stage だが、解放後はフェーズ既定の入力になる。
  実フレームでは城メニュー（kind `castle_menu`）経由なので未観測。
