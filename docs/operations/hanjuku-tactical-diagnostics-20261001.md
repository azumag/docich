# 半熟英雄・第1話の限定診断

ナキューメラだけ未攻略の理由を調べるため、既存owner-only diagnosticsへ読み取り専用のbot記録投影を追加する。現在の実所有や実在将軍を断定するものではない。

- active Hanjuku/ready、runとbot decision traceの4項目一致、bot更新30秒以内、terminalでないことを必須とする
- canonicalを前後で照合し、世代切替・stale・未来時刻・巨大/不正JSON・symlinkは情報を出さない
- 出力は第1話の固定8城名の部分集合と件数/booleanのみ。将軍名、任意文字列、path、runtime ID、lease ID、ログ本文は出力しない
- 未読の駐留はunknownのまま。移動中/所在不明の将軍を待機人数へ数えない
- 400観測内の記録された行先予約だけを表示。未確認行先や到着は推測しない
- 追加情報で既存JSON予算を圧迫する場合はoutput_omittedへ縮退

既存のゲーム戦略、入力、保存、再起動、タイマーは変更しない。primary私用handoffはこの環境に存在せず未読。バナー操作なし、ops briefの再生成/配布は主張しない。独立レビューは行わない。
