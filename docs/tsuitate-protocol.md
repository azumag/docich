# 衝立将棋: オフラインのプロトコル境界

公式仕様: https://beta.tsuitate.info/bot-api

これは対戦コーナーを実装するための最初の独立部分です。
`tsuitate_protocol.py` はネットワーク接続、認証、参加、投了、ゲーム起動を行いません。
既存ゲームの登録・抽選・切替・共通配信も変更しません。

Node.js版の共通brain・beta Socket.IO adapter・反復対局・結果からの候補更新は
[`workers/tsuitate-bot/ARENA.md`](../workers/tsuitate-bot/ARENA.md) に実装手順をまとめています。
このPythonモジュールはオフライン契約の基礎として保持し、Node版のgateでも同じ安全境界を検証します。

## 守る契約

- PlayerView の自分の駒・持駒・手番・次の手数・時計・反則数を型検証する
- 表示には許可された項目だけを新しく組み立て、追加フィールドや相手情報を転送しない
- 1局面で送信待ちは1手。ACK成功だけで盤面を更新しない
- 反則後も同局面で同じ手を再送しない。新しい反則数を確認してから別の手を許可する
- 切断・タイムアウト後の不明な手を盲目に再送しない。同じ盤面への再同期だけでは不明状態を解消しない
- 古い接続世代の通知、古い手数、後退した反則数を採用しない
- 現接続の不正な観測・局面矛盾では以前の盤面による着手を止め、再同期を要求する
- 終局を受信していない状態で「対局なし」を終局と推測しない
- 切替要求は将来の参加だけを止め、現在の対局の入力を止めない

## 終局結果の表示境界

`tsuitate_beta_control.project_game_result` は **対局終了後だけ** 公開する勝敗・終局理由だけを
allowlist から組み立てます。`outcome`（Bot視点）/ `reason` / `endedAt` / Botが観測した最終手数 /
結果の確度のみ。着手・盤面・持ち駒・相手情報は含みません。

- 値が壊れている場合は結果全体を `None` に落とします。stop / reconcile / lifecycle status は
  結果表示に依存しないため、表示の不備が対局を止めません（`playerView` と同じ方針）。
- `tsuitate_view` はさらに `state == "finished"` を再確認してから broadcast します。対局中は
  どこにも勝敗が出ません。
- `tsuitate_corner` は自身の runId が一致し `finished` のときだけ結果をコーナー状態へ保存します。
  別 run の結果は混入しません。

## コーナーの有界終了

`TsuitateCornerManager` は beta が active になってから `max_match_seconds`（既定1800秒）を超えると、
手動停止と同じ `stop` を1回だけ要求します。新規の終了経路は作らず、終局確認と元画面への復帰は
従来どおりです。経過時刻は `beta_active_since` として永続化するので、再起動で対局時間が伸びません。
`paused`（終局未確定）は従来どおり握り潰さず、operator の reconcile まで次のコーナーを待たせます。
timeout で生在对局を強制終了することはありません。

## このPythonモジュール単体に含まれないもの

USI の検証は構文のみで、合法手・勝率を保証しません。
Socket.IO のイベント接続、認証、queue leave の受理、表示、戦略、時計の経過補間、
曖昧な着手結果を解消できない場合の運用、結果保存、corner adapter は未実装です。
サーバー側の exactly-once 処理も保証しません。
実接続する前にこれらを別途実装・検証し、既存の認証・本番操作の承認条件に従います。

## 検証

`python3 -m pytest -q tests/test_tsuitate_protocol.py`

人工の入力データによる再接続、ACK/盤面通知の順序違い、重複、反則、終局、
不正型、未知項目除去の回帰です。実際の対局や勝利を確認したものではありません。
CI の retro-corner-regressions に同じテストを含めます。
