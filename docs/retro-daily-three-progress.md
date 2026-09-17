# レトロ日次3試合: ローカル実装の途中経過

本番反映・自動プレイ有効化の完了記録ではない。2026-09-18、
`feature/retro-daily-three`、base `a25c201`。

## 実装した範囲

- `acc03de` の ninvaders 3試合制御を保持し、0点保存と保存失敗時の再開抑止を追加。
- tracked `games/cli-wrappers/nsnake_docich.sh` に既定3試合の終了画面待機を追加。
  `NSNAKE_BIN` / `NSNAKE_DRIVER_INTERVAL` でローカルfake gameを使って検証できる。
  終了判定とスコア抽出は同じcaptureを使い、先頭ゼロを正規化する。
- 両wrapperの試合数は正の整数のみ。試合終了・保存後に次回開始キーを抑止し、
  実行中試合を終了させるキーやkillは追加していない。
- `origin/codex/robots-game` (`63bd0ef`) の ninvaders/nsnake brain と
  対応テストを復元。標準ライブラリのみで動作する。
  `run/brain/<game>/weights.json` はtracked素材に存在せず、未作成なら
  各brain内の `DEFAULT_WEIGHTS` を使用する。

## 有効化前の必須残件

1. **試合境界契約:** ninvaders/nsnakeの現configは
   `lifecycle.require_round_boundary=false`。CLI adapterの境界検知は
   robotsの `Another game?` 系であり、wrapperが結果保存したことと次回開始を
   止めたことを示すruntime世代に紐づくackがまだない。
   `ends_at` 到達時の既存immediate-quiesceをそのまま新日次制御に使ってはならない。
   境界を確認できなければ待機/失敗にし、プレイ入力は継続する必要がある。
2. **スケジューラ:** `select_game` の既存決定性を維持したまま、各ゲーム・日付別の
   決定的ランダム時刻と実行済ledgerを追加する。日跨ぎ、再起動、program_slotの
   待機/直列化、3試合早期終了と時間切れ時の安全な境界待ちの回帰テストが必要。
3. **自動プレイ:** 復元brainは未接続。nsnake wrapperはmenu/retryだけで方向入力を
   行わない。ninvadersは現在のwrapper自走を維持する。
   nsnakeのconfigは依然 `/usr/local/bin/nsnake_docich` を参照し、未有効化。
   brainとwrapperの入力所有権を分け、tracked wrapperを参照してから有効化する。
4. **改善:** `corner_improve` は依然gnurobotsのみ。未マージ素材の `bot_eval.py` は
   wrapper自動再開との競合、turn capを完走と扱うこと、評価戦略ファイルの受渡しを
   解消してから採用する。現brainの `run/brain/.../weights.json` と昇格先の
   `<state_dir>/resolver/<game>_strategy.json` を統一する必要がある。
   日付だけで改善ゲームを再選択せず、終了したゲームを明示するargvと
   ゲーム別終了stateが必要（現状次コーナーで `retro_corner.json` が上書きされる）。
5. **設定:** 上記未接続のためlive games / improve_agentsは変更していない。
   pacman4console / moon-buggy / bastetも未対応。

## 検証と制約

fake tmux/fake gameで開始キー数、0点を含む保存数、保存失敗時の再開抑止を検証。
これは実ゲームのスコア抽出・盤面解析・無人完走の証明ではない。
ローカルにはtmuxがあるが `/usr/games/nsnake` はない。
VM・共通配信基盤・本番設定への操作は行っていない。
バナーはスクリプト不在で未実施。独立レビューは子エージェント深度制限で未実施。
root handoff.mdはこのworktreeに存在せず、作成・コミットしていない。
