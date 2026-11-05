# Y凍結で枯渇したジャンプ予算を冷却後に1回だけ再許可する (v436、2026-11-05)

## 症状 (実測)

- 現行 g1133 (6章 53-10) のボス出撃 A:bf8c3dee:M256898 (オケアナ→ボス) が出撃元へ歩き出せず停滞。
- policy 実測: cursor [392,144] で cursor_still.n=35、uncertain=true、source_miss=4、
  y_jumps map 計数=6/6 (Y_JUMP_LIMIT)、y_jump_last={mode:map, moves:3, why:cursor_stationary}、tick なし。
- v434 (閉じたYジャンプのブロックを5分で失効) を含む v435 運用中でも解消しなかった。

## 原因

- cursor_stationary 失敗時に _y_jump_step が当該 step/mode の y_jumps を Y_JUMP_LIMIT(6) に固定し、
  注文再開までYジャンプを灌止している (Y連打防止) 。
- v434 の _jump_block_expired は y_jump_last のトグルガードしか緩和せず、
  予算ガード (y_jumps[key] >= Y_JUMP_LIMIT) が冷却後の再試行を再び阻止していた。
- active が変わらないため注文再開時のカウンタクリアも起きず、歩行推定凍結と相まって出撃未発生が続いた。

## 変更

- navigation._want_y_jump: 同一 step/mode の cursor_stationary かつブロック期限切れのとき、
  y_jumps[key] を Y_JUMP_LIMIT-1 に戻して1回だけ再試行を許可し、y_jump_block_retry を記録する。
  再度 cursor_stationary で閉じれば再び予算不足に戻るためY連打は起きない。
- 古い記録 (tick なし) は初回参照で打刻され、そこから5分後に再試行可能になる。
- BOT_VERSION v435 → v436。ボットは観測ごとにホットロードされるため再起動不要。

## 検証

- test_hanjuku_y_jump_interrupted / test_hanjuku_yjump_budget / test_hanjuku_nav_continuity: 16 passed。
- 保守囲り (boss_target_jump / jump_interruption / locked_boss_target /
  target_jump_settle / departure_request): 80 passed。
- 実機値のシミュレーション: 初回参照で打刻・保留維持、Y_JUMP_BLOCK_TICKS 後に True +
  予算 6→5 + y_jump_block_retry 記録を確認。
- 実機 g1133 が v436 をホットロード済みを hanjuku_bot.json で確認。

## 残件

- 再試行は打刻から約5分後 (約Y_JUMP_BLOCK_TICKS)。近いマップフレームで y_jump_open
  (オケアナ / ボス) が出るか、source_miss が止まるかを観測する。
- 再試行も cursor_stationary で閉じる場合はY画面側のカーソル読取の調査が必要 (follow-up)。
