# 半熟英雄の次回開始

共通コーナー実行口 `CornerExecutionCoordinator.execute()` は、前の program slot の
終了後、半熟英雄の開始判断を `hanjuku_resume.prepare_start()` へ渡す。
停滞と gameover は自然 rotation、手動 `start-hanjuku`、`resume-hanjuku-latest` で共通の方針を使う。
それ以外の確認可能な手動停止は、通常開始と明示 resume の従来の区別を保つ。

| 最新実行の検証結果 | 通常開始（自然・手動） | 明示 resume |
| --- | --- | --- |
| `screen_stalled` / `input_stalled` | 到達済み boundary receipt の保存を復元 | 同左 |
| `game_over` | 新規開始。過去の停滞保存を選ばない | 同左 |
| 検証済み保存を持つ、終端理由のない手動中断 `suspended` | 従来どおり新規開始 | 現世代の検証済み保存を復元 |
| owner が保存なし停止を許可した、終端理由のない `manual_forced_stop` | 従来どおり新規開始 | 現状は検証済み保存がないため拒否。下記の互換性の衝突についてレビュー判断待ち |
| 初回で進行記録が存在しない | 従来どおり新規開始 | 拒否 |
| 保存欠損・不正、最新実行不明・未完了、停止認可不正、旧形式の保存だけが残る | 要復旧。古い保存や新規開始へ fallback しない | 同左 |

最新実行は runtime の generation で決める。保存の mtime は順序の根拠にしない。
成功した開始 request receipt も照合し、最新 runtime のファイルが失われていても旧保存へ戻らない。
ただし queued start/switch の「既に起動中です」という既知の no-op 成功は、未起動の予約 runtime を
最新実行に数えない。request・operation・generation・from/to・結果の形を照合して区別する。
`active_runtime` がない旧成功 receipt 一般を捨てることはしない。
同じ最新 generation の runtime が複数あれば拒否する。
`hanjuku_run.json` と `retroarch_boundary.json` の game、runtime、generation、lease、
終端理由、frame を照合し、`hanjuku_run.terminal()` の既存の終端証拠検証を使う。
保存を使う場合は receipt の slot-zero basename、SHA-256、サイズに一致し、
既存の RZIP/RASTATE core decoder が受理するファイルだけを候補にする。

選択した保存は既存の非公開 `hanjuku-resume/<request UUID>.state` へ封印する。
新規開始の判断も同じ request に記録する。再試行で別の保存や別の開始判断を選ばない。
候補 runtime への claim と、ロード後の paused core の再保存一致確認を経てから AI を起動する。
新規開始の receipt は `start_mode=fresh` として扱い、保存の LOAD や旧 bot policy の移植は行わない。

旧版で未 claim の mtime 選択予約は自動移行せず、要復旧として拒否する。
既存の claim 済み予約に対する `hanjuku_resume_recovery` は維持する。
復旧の派生 attempt は元の logical request と封印保存を引き継ぎ、共通開始口でも選び直さない。

初版で追加した「`suspended` の通常開始も自動復元」「`manual_forced_stop` の通常開始も拒否」は
明示要件に必要な変更ではなかったため、確認可能な手動停止の通常開始を従来の新規開始へ戻した。

互換性と安全性が衝突する例は、g478 に停滞保存があり、後続 g514 が保存タイムアウトで
`manual_forced_stop` になった場合の明示 resume。旧版の全 runtime mtime 選択では g478 を
復元し得るが、最新世代を越えた古い停滞保存への巻戻しになる。現状はこの分岐の拒否を変更せず、
例を依頼元へ提示して判断を待つ。また g514 の `suspended` 保存が壊れている場合に通常開始を
自動 fresh とする互換性も、「不正保存を黙って初期化しない」という安全性要件とは両立しない。
この場合も要復旧として、既知の正常な停止と分ける。
この変更の検証は合成保存と fake process による offline テストで行う。
本番配備、実ゲームのロード、VM 診断 dispatch、再起動、配信操作は含まない。
