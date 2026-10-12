# NetHack終了後の復帰失敗とrollback (#1969)

## 2026-10-09の事象と区別

08:39 JSTのowner-only診断で、元NetHackコーナーは08:13に終了復帰を開始した後、
`nethack -> sorengame` readiness timeoutを経て08:23にrollbackされていた。
canonicalはNetHack generation 650、SorenもfreshなMOVE/生存runnerを報告していた。
「終了画面を見落とした」だけではなく、失敗した復帰候補の停止未確認と、
死亡済みの前ゲームを新規起動するrollbackが組み合わさった事象として扱う。
この観測だけでSoren readiness timeout自体の根因までは確定していない。

## 修正契約

- **未停止候補がある間はrollbackで別ゲームを起動しない。** candidate/retiringを
  忘れず、previousも残してfailedにする。recoverも先に候補とretiringの停止を確認する。
  以前の「cleanup_pendingのままrolled_back/ready」は、この失敗経路では認めない。
- **停止済みNetHackはセーブがあるときだけ再開する。** optional adapter capability
  `can_restore_stopped_runtime(deadline, cancel)` は明示的なTrueだけを許可する。
  NetHackは同playerの非空・非symlinkのセーブが一つだけある場合に許可する。
  通常の明示startは変更しない。死亡後の新規冒険は復旧の副作用として起動しない。
- **Sorenの失敗候補を停止不能にしない。** fresh-startで旧ACKが消えた場合、canonicalの
  candidate/retiringとgame/runtime/generation/leaseが完全一致し、別Soren ownerがない時だけ、
  固定identity由来のcleanup requestを既存brokerへ送る。ゲーム入力や強制killは行わない。
  正常な試合境界を待ち、既存stop-after-boundaryでゲーム専用資源だけを停止する。
  再試行は受理済みrequest/deadlineを再利用し、別要求や不明状態を引き継がない。
- **遠征終了とコーナー復帰成功を分離する。** restoreが例外で失敗した場合も、元runtimeの
  request-boundな`nethack_boundary.json`がprocess終了を証明し、canonical previousと一致する時だけ、
  同じrun IDの遠征記録を終了/保存状態へ更新する。cornerはfailedのまま。
  xlogがなければended_unknownであり、死因・スコアを捏造しない。
- queuedの復帰は完了告知しない。新runへのpointer差し替えはRunStoreのロック内で拒否する。

## 検証と本番確認

`tests/test_nethack_terminal_lifecycle.py` は候補停止失敗、再試行、保存再開、死亡済みsource、
Soren owner/request競合、終了証拠の取り違え、未知・大きすぎる・symlink/FIFOの証拠、
復帰例外の保持、queued告知抑止を隔離fixtureで確認する。
既存coordinatorテストは未停止candidateを残したrollbackを成功扱いしない契約へ更新する。

この修正は、既に旧版が起動したNetHackや進行中Sorenを自動killする移行処理ではない。
既存障害にはfreshなowner-only診断と正規recoverの判断が必要。期限切れ/foreign request、
セーブのないpreviousなどは証拠を残してfailedに留め、台帳手編集やblind restartをしない。
本番受入は死亡→元遠征のterminal記録→単一ゲーム復帰、および候補停止不能時に
新規NetHackを作らないことを、同一request/runtimeと実プロセスで照合する。
