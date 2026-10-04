# 各ゲームコーナーのステータス情報（2026-10-04）

共有配信表示のデータ入口はSorenの `lib/docich_corner_stats.py`、テキスト生成は
`status_dashboard.py`、共有配信の表示は `overlays/direct_broadcast_overlay.html`。
親docichはcorner state、確定結果、canonical runtimeを所有する。
WebUIの `/api/corners` は別の投影で、今回の変更対象ではない。

この棚卸しはmainのコードと現行PRを照合したもの。本番の現行フレーム、稼働プロセス、
データ鮮度の実測は行っていない。ローカルChromiumの確認は合成fixtureである。

## 表示と観測源

| コーナー | 既存の表示・データ源 | 第一群 / 次の候補 |
| --- | --- | --- |
| Soren | score history、戦略比較、観測ログ。Soren #579の計器表示がmainにある | 既存の情報を維持 |
| nInvaders / nSnake / Bastet / Moon Buggy / Pac-Man | `retro_corner{,_manual}.json`、`scores/<game>.jsonl`、`resolver/improve_log.jsonl`。全期間の成績・推移・ランキング | **第一群**: 今回成績、結果時刻、残り試合/時間、記録された次段階・理由 |
| 半熟英雄 | runtimeの `hanjuku_run.json` / `hanjuku_bot.json`、canonical identity。城・兵力・戦闘・駐留・行軍など | 戦略は別担当。今回変更なし |
| NetHack | corner state、RunStoreのcurrent/run/history。遠征番号・状態・score・turns・depth | 観測時刻・次判断の表示候補。intro parser #1674・復帰の別担当と調整し、重複実装しない |
| Soren91 / Meriken | `soren91/tmp/summaries/game_*.json` の確定順位、corner state | 現runの進捗・次行動・順位観測時刻が候補 |
| JEV | corner state、`tmp/jev_player/runs/*/report.json`。既存表示は中断を含み得るreported scoreと明示 | 別担当領域。今回変更なし |
| PAPER | corner state、`trading/status.json` のworker・資金・positions・fills | worker更新時刻とfill観測の古さが候補。live eligibilityは変更しない |
| Weather | `weather_corner.json` とweather adapter。既存共有stats bridgeのcorner一覧には専用weather投影がない | 次の優先候補: weather-owned値を専用投影し、Soren成績へのfallbackを区別。producer接続・cue同期の観測範囲を確認して進める |

## 第一群の値契約

- `Session` はこの開始時刻以降の、同gameかつ未来でない確定結果。canonicalの
  runtime IDがcornerの `rotation_runtime_id` と一致しない場合、終了時刻による
  確定窓もない記録は今回成績として扱わない。startingの開始要求時刻は実行開始の
  証明にならないため、今回成績を未確認にする。
- missing/unreadable/partial/時刻欠落の履歴と、読める空の履歴を区別する。
  未確認を0試合・残り全試合と補完しない。既存の全期間記録は別扱いで維持する。
- `Result` のUTC時刻と経過秒は**最新の確定結果の時刻**。ファイルmtimeや表示生成時刻を
  ゲーム観測時刻とみなさない。古い結果も経過秒をそのまま示す。
- `Plan` は記録された切替待ち・復帰待ち・要復旧・目標到達後の完了待ち・期限超過後の
  完了未確認を区別する。残り試合・時間は予定であり、終了成功や次入力を断定しない。
- `Reason` は固定allowlistの終了/エラー分類だけ。例外本文、任意JSON、paths、秘密を表示しない。
  理由がない場合は結果記録だけでlive scoreを観測していないと明示する。
- 全期間のKPIを `HISTORY BEST` / `HISTORY RECENT 30` とし、今回成績と区別する。
  raw端末/従来HTMLの既存推移・分布・ランキング出力を維持する。

共有rendererは4行の情報を折り返し表示し、追加の左端線・装飾を導入しない。
枠配置、ゲーム映像の寸法・入力・戦略・終了判定・復帰制御を変更しない。

## 担当・競合と次工程

2026-10-04確認時点で、Sorenの現行open PRにこの変更ファイルの重複はない。
docich #1580（表示v4）と #1248（TwiCa共通化）は古いSoren gitlink差分を持つため、
統合時は累積mainの参照を維持する。status worktree
`codex/corner-stats-shared-output` は過去の共有統計対応で、現行作業として上書きしない。
primaryの既存dirtyは保全する。

半熟英雄戦略は別担当。NetHack #1674・retro復帰、JEV #1648 / Soren #580、
ついたて #1670、TwiCa overlayはこのPRへ取り込まない。

1. Sorenの第一群表示PRをレビューし、最新HEADのCIを確認する。
2. Soren統合後に、親のgitlinkをそのmain SHAへ追随して親CIを確認する。
3. 本番反映と自然コーナーの受入れは別の承認範囲で実施する。
4. 次の表示群はweather専用投影を優先し、Meriken/NetHack/PAPERの時刻・次判断へ広げる。

独立レビュー・本番反映・再起動・実ゲーム入力・バナー/音声操作は未実施。
