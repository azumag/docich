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

## broadcast view の制御capability

`tsuitate-view` は GameSwitch の世代session（tmux）で `python -m docich.tsuitate_view` として
動き、`/api/tsuitate` の応答を自分で `POST /beta-control status` から組み立てます。view が
制御APIへ到達できないと状態ラベルではなく「制御接続を確認できません」を返します。

tmux は session の環境を server から作るため、`TsuitateProgramViewAdapter._game_session_env()`
が `DOCICH_BETA_CONTROL_SECRET` / `DOCICH_BETA_CONTROL_URL`（`CAPABILITY_ENV_KEYS`）を
session へ明示的に渡します。`TsuitateCornerAdapter.run()` も同じ2 keyだけを
`runtime_environment()` で1局のスコープへ出し、WebUI の operator/viewer token は渡しません。

この2 keyが欠けると `/api/tsuitate` は `ok:false` を返し続け、adapter の readiness が成立せず、
switch は deadline 超過で rollback してコーナーは一度も active になれません。

配信ページ自体もブラウザ翻訳の対象外にします（`<html lang="ja" translate="no">` と
`<meta name="google" content="notranslate">`）。翻訳ツールバーは右上の枠に重なるため、
view 側の標準は [`broadcast-html-view-standard.md`](broadcast-html-view-standard.md) に従います。

## 読み上げ: コーナー紹介と感想戦

コーナーは view が表示を取った時点で**紹介**を1回、終局結果が確定した時点で**感想戦**を1回、
共通の overlay と音声queueへ出します（`tsuitate_narration`）。音声は catalog 行の
`audio_enabled`（本番は有効）、感想戦の生成は `[tsuitate_corner] review_agents` の明示opt-inです。
どちらも at-most-once（副作用の前に request id を保存）で、sink の失敗は件数カウンタに残し、
コーナーや GameSwitch を失敗させません。

紹介は固定文です。衝立将棋が「相手の駒が見えない状態で指す将棋」で、見えない駒の合法性を判定
できるのはサーバーだけ＝合法だと思った手が反則になるのが正常な流れであること、反則10回で
反則負け、持ち時間300秒＋1手3秒、終局後に公開棋譜から感想戦をすること、を読み上げます。

感想戦の入力は、終局時に**公開される**事実だけです。サイトは対局中の相手情報を通知せず、
`game:end` で公開棋譜・終局図・レート変動・相手の身元を初めて公開します。docich はその公開
リプレイ（`/games/<gameId>/__data.json`）を**終局後に1回だけ**読み（`tsuitate_review`）、
固定host・https・redirect禁止・本文/表/手数の上限つき・fail-closed で、次だけを取り出します。

- 勝敗・終局理由・手数・両者の考慮時間・反則数・レート変動・公開された相手名・成り/打ちの回数。

`verify()` が gameId・終局時刻・終局理由（分かる場合は勝敗も）を確定済み結果と照合し、一致
しなければ感想戦には使いません。**着手列・盤面は保存しません**: リプレイはその場で読み捨て、
corner state に残るのは生成した読み上げ文と `review`（`source` / `kind` / 手数）の有界カウンタ
だけです。配信 view には従来どおり盤面・持ち駒・棋譜を出しません。

`review_agents` が非空なら、検証済みの事実だけを入力に共通LLMで2〜3文を生成し、失敗・timeout・
安全でない出力（長さ・記号・URL・英語比率）は定型文へ fallback します。モデルには盤面を渡さず、
駒種・捕獲・隠し情報を推測しないよう指示します。**なぜ定型文へ落ちたか**は `review.model_failure`
（`none` / `unsafe` / `gate` / `timeout` / `rate_limit` / `provider` / `exception`）として state と
診断に残し、無言の fallback を追えるようにします。

### 感想戦の棋譜ショー（盤面＋棋譜＋一手ごとの表示）

感想戦は結果の読み上げではなく、**公開棋譜を追いながら一手ずつ盤面と棋譜を出して解説する**
ショーです。実装は `tsuitate_show`（payloadと進行）と `tsuitate_view`（表示）。

- **盤面の再構成**: 公開リプレイは指し手（USI）と終局SFENしか持たないため、標準初期局面へ
  指し手を適用して盤面を作ります（`shogi_board`）。ルールエンジンではなく**適用器**で、
  合法性・王手・詰みは判定しません。`reconstruct` が**サイトの `finalSfen` と一致しなければ
  盤面を出さず**、棋譜リストだけを表示します（fail-closed）。
- **payload**: corner が `<state_dir>/tsuitate_review.json` に1つだけ書きます（atomic・
  0600・上限256KiB・最大512手）。view の runtime identity（`runtime_id` / `generation`）で
  固定し、別世代の payload は表示しません。着手列・盤面はこのショーのためだけの一時データで、
  コーナー終了（世代終了）とともに消えます。
- **表示**: `tsuitate-view` はショーが有効なとき盤面（漢字の駒、後手は反転）と現在の手を
  中心にした棋譜リスト、その区間の解説文を出します。`/api/tsuitate` が現在の手数、
  `/api/tsuitate/kifu` が棋譜本体を返します。**対局中は従来どおり**盤面・持ち駒・棋譜を
  出しません（終局後にサイトが公開したものだけを、終局後のショーとして表示します）。
- **進行**: ゲームを最大6区間に分け、区間ごとにLLM解説（`chunk_commentary`、失敗区間は
  決定的な定型解説）を1本話し、その間 display は一手ずつ進みます。全体は
  `MAX_SHOW_SECONDS`（420秒）で有界。区間の切れ目では共有オーディオqueueの境界を待ちます。
- ショーの成否は `review.show`（`moves` / `board` / `commentary_ai` / `commentary_fallback` /
  `seconds`）に残し、コーナーや GameSwitch を失敗させません。

### 感想戦前のオーディオ境界待ち

感想戦は、共有オーディオqueue（`comment_queue` が解決する `comment_*.txt` /
`comment_*.playing`）が**完全に静かになるまで待ってから**読み上げます。再生中のコメントや
コーナー告知の途中に感想戦が割り込まないためで、コーナー入りの境界（#2236）と同じ判定を
出口側でも使います。

- 待ちは `REVIEW_BOUNDARY_WAIT_S`（既定180秒）で有界。**期限切れでも感想戦は読みます**
  （fail-open）。queue が無い・読めない場合も「静か」として即座に進み、コーナーや
  GameSwitch を塞ぎません。
- 待っている間は結果カードが画面に出たままです（コーナーが共有program枠を保持）。
- 音声が無効（catalog の `audio_enabled` が false）のときは待ちません（読み上げないため）。
- 結果は `review.boundary`（`quiet` / `timeout` / `unavailable` / `skipped`）として state と
  診断に残します。
- コーナー**入り**の境界（予想in-flight + audio queue 静穏、`rotation_wait_minutes` で有界）は
  既存の `corner_boundary` が担い、view系コーナーはそれを待ってから開始します。この節は
  その**出口**側の待ちです。

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
