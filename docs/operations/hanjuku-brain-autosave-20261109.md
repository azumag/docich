# 半熟英雄: 脳から要求し、アダプタが実行する15分セーブ（v638、2026-11-10）

## 背景（重大）

v623 で `[lifecycle] periodic_savestate_s = 900` を入れてエージェントループにフックを
置きましたが、**実機では発動しませんでした**（`states/hanjuku-hero.state` の mtime が
10:58 のまま9時間）。その結果、19:3x の戦闘ハングで**復帰点がなく、第8話・8城の進行を失う**
ことになりました。

## 原因の切り分け

- フック関数は単体では正しく `SAVE_STATE` を送ります（ポート55518）。
- エージェントのループからは呼ばれていない、または設定値が届いていません。

## 修正（v638 `hanjuku-chart-v638-brain-autosave`）

**脳（bot.py）が要求し、アダプタが実行する**方式に変えました。

- `hanjuku_run.request_autosave(runtime, interval=900)`：脳が観測のたびに呼び、
  15分に1回だけ `hanjuku_autosave.json` を書きます。
- `RetroArchAdapter._honor_autosave(runtime_dir)`：入力ゲートの中で要求ファイルを見つけたら
  `SAVE_STATE` を送り、要求を消します（`act()` の先頭で実行）。

これにより、**エージェントのループ実装に依存せず**、実測可能な形で保存されます。

## 確認

- `tests/test_hanjuku_autosave_request.py`（2件: 間隔・無効時）。
- 脳・スクリプト・アダプタ・診断 140件 passed。
- 本番反映後: `states/hanjuku-hero.state` の mtime が15分ごとに更新されることを実測する。

## 追加（v639）: 観測時にも要求を実行する

`act()` だけで要求を処理していたため、保留（0アクション）が続くと保存されませんでした
（実測 20:38: 要求ファイルが数分未処理）。`observe()` の冒頭でも要求を処理するようにしました。

## 追加（v640）: ポート取得の修正

実機のアダプタは旧 `RetroArchAdapter` で `_network_port` を持たず、自動セーブが
`'RetroArchAdapter' object has no attribute '_network_port'` で失敗していました（実測 20:54）。
`retroarch_network_port(self.ctx.fence.generation)` から求めるよう修正しました。
