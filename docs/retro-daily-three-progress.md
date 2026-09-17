# レトロ日次3試合: ローカル実装の途中経過

**2026-09-18更新: A/B/Cの中核実装は完了 (feature/retro-daily-three)。**
本番反映・VM実測は未実施。base `a25c201`。

## 実装済み (検証済み)

- **A スケジューラ** (`5f5263f`, `05b1b67`):
  `RetroCornerConfig` に `daily_each_game` / `randomize_start` /
  `start_window_minutes` / `target_matches` を追加。既定は現行挙動のまま
  (既存テスト62件は無変更で通る)。
  開始時刻は `scheduled_start()` が `random.Random(f"{date}|{game}")` で
  `start_hour:00` から窓内オフセット秒を決定的に導出し、再起動・再tickで不変。
  23:59を超える窓は当日中にクランプ。`_begin_locked` は due チェックと
  実行済み試行 (state `daily_attempts`) を持ち、1日1ゲーム×各ゲームで回す。
  3試合検知は scorelog (`<state_dir>/scores/<game>.jsonl`) のコーナー開始
  以降の件数で行い、検知できたら `_finish_locked` で早期終了。
  検知できなければ従来どおり `ends_at` で終了 (時間上限は必ず保持)。
  プレイ入力は止めない。legacy 1ゲームモードは単一sleepの元挙動を維持。
  soren91/nethack corner サブクラスは `getattr` フォールバックで後方互換。
  program boundary 経路 (`require_program_boundary=true`) + daily_each_game の
  同日複数ゲーム実行をテストで検証 (`TestDailyEachGameWithProgramBoundary`)。
  `improve-once` に `--game` を追加し、spawn argv が終了ゲームを明示
  (`select_game(日付)` の誤選択を解消)。
- **B 改善dispatch** (`c0e58f3`): `run_corner_improve` のハードコードを
  `BOT_GAMES = ("nsnake", "ninvaders")` レジストリに置換。
  `origin/codex/robots-game` の `bot_eval.py` (tmux headless evaluator) を復元し、
  生バイナリ (wrapperでない) + bot_eval 自前の start/retry キーで評価。
  `maxed` (turn cap到達) 試合は完走扱いにせず昇格 gate を fail closed。
  候補重みは一時ディレクトリの weights.json を `DOCICH_BRAIN_WEIGHTS` env で
  brain に渡す (グローバル共有ファイルは書き換えない、並行安全)。
  真偽値重み (nsnake `tail_passable`) は LLM 提案キーから除外。
  `DOCICH_ALLOW_REAL_AI=1` ガードは維持。gnurobots 経路は不変。
  昇格先は既存 `strategy_path(g.state_dir, game)`。
- **C 設定と自動プレイ** (`9b036d9`): live `[retro_corner]` を
  `games = ["ninvaders", "nsnake"]` + `daily_each_game = true` +
  `randomize_start = true` + `target_matches = 3` + 非空 `improve_agents`
  (両ゲームが bot_eval 対応のため)。`config/games/nsnake.toml` は tracked
  wrapper 参照へ変更し、`[agent] enabled=true brain="command"
  command=["python3", "brains/nsnake/brain.py"]` (hanjuku-hero と同一スキーマ)。
  wrapper は menu/retry/score 記録/3試合上限のみ担当。ninvaders は wrapper
  自走 (`self_play=true`) を維持。pacman4console / moon-buggy / bastet は
  tracked wrapper と動作 brain が無いため games に追加しない。
- 旧実装 (`8c200b2`): wrapper 0点保存/保存失敗時再開抑止/上限検証。

## 検証

```
tests/test_retro_daily_schedule.py tests/test_retro_corner.py
tests/test_retro_corner_manual.py tests/test_corner_boundary.py
tests/test_corner_improve.py tests/test_corner_improve_dispatch.py
tests/test_nsnake_agent_config.py tests/test_ninvaders_wrapper.py
tests/test_retro_wrapper_limits.py tests/test_ninvaders_brain.py
tests/test_nsnake_brain.py tests/test_soren91_corner.py
tests/test_nethack_corner.py
→ 173 passed, 45 subtests passed
```

base `a25c201` のクリーン worktree で全テストを走らせ、既存失敗 10件
(test_tts 2 / test_chat 2 / test_overlay 1 / test_overlay_interop 4 /
test_hanjuku_brain 1) は環境起因と確定 (今回の退行ではない)。

## 未検証

- 実ゲーム (`/usr/games/ninvaders`, `/usr/games/nsnake`) はローカルに無く、
  headless 評価の実走・無人完走は未実測。
- `run/brain/<game>/weights.json` 未作成 (brain内蔵既定値で動作)。
  改善昇格後の live brain 反映は follow-up (昇格先 `<state_dir>/resolver/`
  と brain 参照先 `run/brain/` の統合が必要)。
- VM実測・本番反映・program boundary 実運用との干渉は未実施。
  本番適用は docich 正規フロー (branch→PR→CI→protected main→VM gateway)。
- ローカルには tmux あり。バナー未実施 (スクリプト不在)。音声不要。
