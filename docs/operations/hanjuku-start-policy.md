# 半熟英雄の次回開始

共通コーナー実行口 `CornerExecutionCoordinator.execute()` は、前の program slot の
終了後、半熟英雄の開始判断を `hanjuku_resume.prepare_start()` へ渡す。
自然 rotation、手動 `start-hanjuku`、`resume-hanjuku-latest` は同じ終了理由の方針を使う。

| 最新実行の検証結果 | 次回開始 |
| --- | --- |
| `screen_stalled` / `input_stalled` | 到達済み boundary receipt に記録された保存を復元 |
| `game_over` | 新規開始。過去の停滞保存を選ばない |
| 検証済みの手動中断 `suspended` | 進行を保護するため保存を復元 |
| 初回で進行記録が存在しない | 通常開始は新規開始。明示 resume は拒否 |
| 欠損、不正、不明、未完了、保存なし強制停止、旧形式の保存だけが残る | 開始を拒否して要復旧。古い保存や新規開始へ fallback しない |

最新実行は runtime の generation で決める。保存の mtime は順序の根拠にしない。
成功した開始 request receipt も照合し、最新 runtime のファイルが失われていても旧保存へ戻らない。
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

手動中断と保存なし強制停止の次回方針は、今回の停滞／gameover の明示依頼では未指定だったため、
前者は検証済みの進行を保持し、後者は自動初期化を避ける扱いとした。
この変更の検証は合成保存と fake process による offline テストで行う。
本番配備、実ゲームのロード、VM 診断 dispatch、再起動、配信操作は含まない。
