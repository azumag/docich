# 新記録クリップ

新記録の確定はdocichのローカル台帳、クリップの作成・公開確認は既存SorenのTwitch経路が所有する。
本変更はゲーム入力・戦略・切替・配信制御を変更しない。

|コーナー|判定する記録|今回の適用|
|---|---|---|
|Soren本編|終了確定scoreの厳密最大（既存best保持）|既存経路を維持。ソ連建国も別トリガー|
|NInvaders / Snake / Bastet / Moon Buggy / Pacman|wrapperが保存した終了確定scoreの厳密最大|共通ローカル台帳から投入|
|半熟英雄|証拠・実行世代に結びついた確定cleared話数の厳密最大|既存progressのみ観測。初期best=既存予想台帳または設定seed（既定1）|
|NetHack|xlogにpoints / maxlvl / achievementがある|比較指標の選択待ち|
|91人対戦|scoreと順位がある|比較指標・確定結果の選択待ち|
|PAPER|損益・資本・期間に依存|比較基準の選択待ち|
|天気|競技記録なし|対象外|

スコア・時間・順位・クリアは相互に換算しない。未定義metricは拒否する。
半熟の章N表示はN-1話クリア。12話クリアは既存の最終ボス勝利証拠に従う。
曖昧な新ゲーム検出や世代不一致を新記録にしない。

コンソールの開始時に既存の全終了履歴を基準へ取り込み、古いクリップを再生しない。
履歴が無い場合は初回値を基準にし、それ以降の厳密更新から作成する。同点・0へのリセットでbestを下げない。
探索・評価・Moon BuggyのA/B結果は配信記録へ取り込まない。

`[record_clips] enabled=true` の構成だけがローカルキュー投入を有効化する。
台帳はstateディレクトリの`records/`。bestと未送信イベントを同じatomic/fsync書込で確定し、キュー障害は次のローカル記録処理で再送する。
lock取得前に`records/pending/`へ確定スコア候補をatomic作成・fsyncする。lock競合は待たずに戻り、次の記録処理・履歴seedで候補を回収する。
候補を取得時刻順に台帳へ確定してから削除するため、再起動時の履歴最大値が未処理の新記録を基準値へ吸収しない。
同イベントの再送は元の取得時刻を保持し、遅延した記録を新鮮なクリップ要求へ変えない。
実行identityはevent IDのハッシュ入力だけに使い、公開メッセージへ入れない。
同イベントのキューファイルとSoren側receiptを同じIDで照合する。

配送契約はSorenの`tools/record_clip_queue.py`と`tools/clip_receipt.py`に依存する。
Soren側のPRを先にレビュー・統合し、docich側でそのgitlinkを同期してから同時に正規配備する。
未統合のfeature commitを本番へ直接配布しない。

Sorenは既存の`TWITCH_CLIP_TOKEN`（未設定時`TWITCH_BOT_TOKEN`）と`clips:edit`を使用する。
新しい資格情報・scope・課金・サービスは追加しない。公開範囲は既存のTwitch公開クリップ＋チャットURLのみ。
新記録クリップはBlueskyへ送らない（既存のソ連建国投稿だけ維持）。

[Twitch Create Clip仕様](https://dev.twitch.tv/docs/api/reference/#create-clip)では、素材はAPI呼出前約85秒＋後約5秒、既定公開は末尾の最大30秒。
今回の経路は既存の切り取り設定を維持する。前後秒数を厳密には指定・保証しない。
新記録から20秒を超えた未作成イベントは別場面を切り取らず`expired`で記録する。
受付後はIDを保存し、GETのみで最大10分の後追い確認を行う。
各tickは最大3処理。未作成POSTを期限順に先行し、両種別がある場合は受付済みGETへ最低1枠を残す。
GETは最終試行時刻順に巡回し、未確認IDや短時間の新記録連続で相手の配送を占有しない。
確定したAPI一時拒否だけ最大3回、結果不明のPOSTは自動再作成しない。

receiptの`accepted`は生成受付、`ready`はIDと公開URLの確認、`unknown`はPOST結果不明。
`unconfirmed`はID保持・公開未確認。`expired` / `disabled`は未作成。
`done/`にあることだけで公開成功とは判断しない。receiptの`ready`とURLを照合する。
公開確認後のチャットキュー追記は別の配送で、クラッシュ時のチャット欠落を再POSTで補わない。
receiptはキューの短期cleanupから独立し、再送防止に保持する。

検証はmock HTTP・合成終了記録のみ。実Twitch作成、ゲーム再読込・切替・再起動、本番配布・音声バナーは実施していない。
wrapper環境は次の自然起動で有効になる。レビュー後の配備・反映時期は独立レビュー担当が調整する。
