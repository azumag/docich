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
| owner が保存なし停止を許可した、終端理由のない `manual_forced_stop` | 従来どおり新規開始 | 本人承認の限定例外として、古い検証済み保存への巻戻しを許可 |
| 初回で進行記録が存在しない | 従来どおり新規開始 | 拒否 |
| 保存欠損・不正、最新実行不明・未完了、停止認可不正、旧形式の保存だけが残る | 要復旧。古い保存や新規開始へ fallback しない | 同左 |

最新実行は runtime の generation で決める。保存の mtime は順序の根拠にしない。
成功した開始 request receipt も照合し、最新 runtime のファイルが失われていても旧保存へ戻らない。
過去の世代へ遡るのは、確認可能な保存なし手動強制停止後の明示 resume だけに限る。
ただし queued start/switch の「既に起動中です」という既知の no-op 成功は、未起動の予約 runtime を
最新実行に数えない。request・operation・generation・from/to・結果の形を照合して区別する。
`active_runtime` がない旧成功 receipt 一般を捨てることはしない。
同じ最新 generation の runtime が複数あれば拒否する。
`hanjuku_run.json` と `retroarch_boundary.json` の game、runtime、generation、lease、
終端理由、frame を照合し、`hanjuku_run.terminal()` の既存の終端証拠検証を使う。
保存を使う場合は receipt の slot-zero basename、SHA-256、サイズに一致し、
既存の RZIP/RASTATE core decoder が受理するファイルだけを候補にする。

選択した保存は既存の非公開 `hanjuku-resume/<request UUID>.state` へ封印する。
承認された巻戻しでは、復元元の `source_boundary` と、例外の根拠になった最新の保存なし停止
`rewind_from_boundary` を同じ request に記録する。
新規開始の判断も同じ request に記録する。再試行で別の保存や別の開始判断を選ばない。
候補 runtime への claim と、ロード後の paused core の再保存一致確認を経てから AI を起動する。
新規開始の receipt は `start_mode=fresh` として扱い、保存の LOAD や旧 bot policy の移植は行わない。

旧版で未 claim の mtime 選択予約は自動移行せず、要復旧として拒否する。
既存の claim 済み予約に対する `hanjuku_resume_recovery` は維持する。
復旧の派生 attempt は元の logical request と封印保存を引き継ぎ、共通開始口でも選び直さない。

### AI開始前に失敗した自動再開の復旧（#2760 / #2807）

自動slotで保存をclaimした後にswitchがrollbackすると、`claimed` が残り、
以後の開始は `another checkpoint request is pending` で拒否される。
WebUIの「最新セーブから再開」はこの状態を検出し、既存の復旧検証を通過した場合に
同じ封印保存を一度だけ再試行する。既存の `check-recover-hanjuku-resume` →
`recover-hanjuku-resume` operatorも使用できる。保存を削除したり、通常timerに
無条件の再試行や要求の取消を追加したりしない。

対象は `terminal-boundary-v1 / resume / automatic / explicit_resume=false` のclaim。
同一requestの `rolled_back` receipt、runtime側claimの完全一致、AI開始記録の不在、
presenterの停止とtmux資源解放、安定したcanonical、保存・ROM・coreのdigest一致を
既存writer lockの下で再検証する。ユーザーの休止設定と競合予約も引き続き拒否条件とする。
元のpendingが残っている場合に加え、slot解放済みの場合は空の予約枠と、
元requestのrollback receipt・停止済みpresentation・tmux資源不在を要求する。
保持数に上限のある `failed_corners` / `released_requests` の記録が消えていても、
lock下で新しい半熟の成功・未完了receipt、実行記録、保存がないことを確認する。
新しい進行、判定不能な状態、別ownerが稼働中の場合は拒否する。

再試行はoperatorによる手動予約となるが、元requestの完全な記録を
`startup_recovery.original` に保存し、logical UUID・保存・source boundaryを保つ。
別コーナーが再利用した終了済みownerファイルは変更しない。途中の書込みが失敗しても
同一fingerprint・派生attemptで再実行でき、claim後の再実行は新しい試行を作らない。

最初の派生attempt自身がAI開始前にrollbackした場合、WebUIの明示的な再開操作に限り、
そのattemptのclaimとrollback receiptを照合し、同じlogical request・封印保存を使う
2回目の派生attemptを1回だけ予約する。前のclaimを非公開復旧記録に保持する。
新規playの痕跡、資源残存、新しい半熟進行、競合予約、ユーザー休止があれば拒否する。
通常timerによる自動再試行や3回目以降の自動生成はしない。

復元画面が章番号を含まない美女のおねだり画面の場合、年・月・所持金と3つの価格付き
選択肢すべてが読めたときだけ、章不明のまま既存の贈り物処理へ引き継ぐ。
既存の1000G境界と待機上限は変更せず、画面から章・支払い済み状態を推測しない。

read-only diagnostics の `hanjuku_resume.status=detached_claim` は、安定したcanonicalから
外れたclaimとruntime側記録の一致を示す。`reservation_fingerprint` を既存operatorの
`expected_reservation` に渡す。これは復旧可能性や資源解放の証明ではなく、
check操作で上記条件を再検証するための識別子である。公開するのは固定statusとdigestだけで、
保存bytes・パス・request/lease IDを返さない。

レビュー・CI・protected main・正規配備後に、最新診断、check、recover、
同じ保存のロード後core照合、AI開始、継続・終了とslot解放を別々に検証する。
再試行経路の修正だけでは、元の起動失敗原因が解消したことや本番復旧を意味しない。

追加の自動復元は停滞だけを対象とし、確認可能な正常手動停止後の通常開始は従来の新規開始を保つ。

オーナー承認（2026-10-17）により、g478 に検証済み保存が残り、後続 g514 が保存タイムアウトの
`manual_forced_stop` になった後に明示 resume を選んだ場合は、g478 への巻戻しを許可する。
最新の停止 receipt と manual marker の identity、保存なし停止認可、save timeout を確認してから、
直前世代の同じ boundary/save 検証へ進む。連続する確認済みの保存なし停止も同様に扱う。
古い保存の欠損・改変・不正や、途中の不明な世代では止め、さらに古い保存へ黙って fallback しない。
途中に検証済み gameover があれば、その新規開始方針を維持し、それ以前の停滞保存は復活させない。
通常開始と最新 gameover 後の fresh は変えず、ロード後の core 一致確認も省略しない。
この再開方針の承認は、今すぐ VM や実ゲームを操作する許可には広げない。

`suspended` 保存自体が壊れている場合は、通常開始でも不正保存を黙って初期化せず要復旧とする。
これは確認可能な正常停止の fresh と分ける。
この変更の検証は合成保存と fake process による offline テストで行う。
本番配備、実ゲームのロード、VM 診断 dispatch、再起動、配信操作は含まない。
