# CI 修正記録 — docich#5584 / PR #5657

対象: PR #5657 `feat(corner): new_corner_runtime 向け共通 production adapter`
head: 13c97fc45、ブランチ impl/5584-production-adapter、worktree /home/hatch/work/docich-5584

## 1. `assert len(catalog) == 18` の更新（実施済み）

geography-world の catalog 行追加（`config/docich.soren-live.toml` 139行目、
`adapter = "newcorner"`、DOCICH_NEW_CORNER_ENABLED ゲート）により production
catalog が 18 → 19 件になったため、以下を更新した。

- `tests/test_yoso_corner.py:146` — `assert len(catalog) == 19` へ更新。
  `test_corner_rotation.py` と同じ履歴コメント形式（chart-659 追加で18本、
  #5584 geography-world で19本）を追記。
- `tests/test_doushi_corner.py:147` — 同上。

どちらのテストも catalog の id 集合を厳密列挙しておらず（`next(c for c in
catalog if c.id == ...)` で個別行のみ検証）、assertion の弱体化はしていない。

## 2. 他テストの件数ハードコード棚卸し（差異なし）

`tests/` 配下で production catalog の件数・id 集合を検証する箇所を全件確認。

- `tests/test_corner_rotation.py:73-77` — `{c.id for c in catalog} == {...}` の
  厳密集合は既に `geography-world` を含む 19 id で更新済み（PR 本体で対応済み）。
- `tests/test_corner_rotation.py:100` — `assert len(catalog) == 19` 済み。
- `tests/test_corner_rotation.py:292,305` — `len(eligible) == 15 / 14`。
  geography-world は flag OFF で `excluded["geography-world"] ==
  "adapter-unavailable"` 扱いになるよう既に更新済み。変更不要。
- `tests/test_weather_corner.py:297` — `== 1` は tmp config 由来で production
  catalog 非依存。対象外。
- その他 `len(catalog)` を持つテストなし。`soren-live.toml` を読む残りの
  テスト（mystery/lawsim/chart/soren91/retro 等）は個別行の `next(...)`
  参照のみで件数に依存しない。

## 3. `nethack-tiles-gui-smoke` DeadlineExceededError の調査

### 失敗の意味

`tests/test_nethack_tiles_gui_smoke.py:238` が `adapter.readiness(deadline =
monotonic()+90, cancel=None)` を呼ぶ。`DeadlineExceededError` は
`src/docich/adapters/cli_game.py:349-350` の `_check_active` が
`time.monotonic() >= deadline` で送出するもので、`cancel=None` のため cancel
由来ではない。つまり 90 秒の readiness 予算内に tiles supervisor（frame
service → Chromium 起動 → window 検出、失敗時は TTY fallback → presentation
window 検出）が `tiles_active` / `fallback_tty` + 可視 presenter window に
到達しなかった、という意味。同テスト 233-237 行のコメントにも「loaded CI
runner では fallback が観測可能になる直前で期限切れになり得る」として
30s→90s へ引き上げた経緯（docich#3240）が記録されており、負荷感受性は
既知。

### PR #5657 の変更が smoke 起動経路に影響するかの確認

smoke テストの依存経路（`tests/test_nethack_tiles_gui_smoke.py` →
`docich.adapters.nethack` / `config` / `game_switch` / `naming` /
`nethack_tiles_supervisor` / `tmux` / `xkit` / `nethack_spectator_live` /
`nethack_spectator_server` / `broadcast_view` / `adapters/base` /
`adapters/cli_game`）の全ファイルに `new_corner` / `newcorner` / `5584` /
`geography` / `CORNER_SPECS` の参照がないことを grep で確認。PR が触れた
可能性のある共有ファイルは `src/docich/adapters/__init__.py` のみで、そこ
の追加分岐は `spec.game.endswith("-view")` ガード付きかつ関数内 lazy
import のため、`spec.game == "nethack"` の経路では評価・import ともに
実行されない。

production catalog の TOML 行も smoke では読まれない（`SimpleNamespace` +
`GameConfig` fixture を直接構築し `load_global`/`load_catalog` を通らない）。
Chromium 起動引数の共通経路 `broadcast_browser_command`
（`adapters/program.py`）および `broadcast_view.py`（
`BROADCAST_TRANSLATE_ARGS`）にも PR 由来の変更なし。

### 結論

PR #5657 の変更が nethack-tiles-gui-smoke の起動経路へ影響するコード経路は
見つからなかった。workflow の path filter に `src/docich/adapters/__init__.py`
が含まれるため本 PR で check が走ったが、実行内容は従来どおりで、失敗は
CI ランナー負荷下のタイミング超過（既知のフレーク類型、docich#3240 と同族）
と判断する。コード修正は行わず、rerun 結果はゲート側で確認する。

## 変更ファイル一覧（本修正分のみ）

- `tests/test_yoso_corner.py` — catalog 件数 18→19 + 履歴コメント追記
- `tests/test_doushi_corner.py` — 同上
- `CI-FIX-5584.md` — 本記録（新規）

検証未実施: このレーンでは pytest/python3/gh の実行が sandbox で拒否される
ため、テスト実行は行っていない。変更は assertion の数値とコメントのみで、
実装コードへの変更はない。
