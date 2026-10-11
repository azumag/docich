# Fly Me To The Home! BOT 保存版（2026-10-11）

Steam製品版 app 2076670、実機 Windows 10 / Python 3.13。停止時のBOTを指定ブランチ `claude/zealous-planck-9ktan2` に保存。自動プレイは一時停止中。保存・復元だけではゲームを起動せず、キー入力も送らない。

## 保存内容

- 実装：`src/docich/flyhome/`。画像認識、追跡、A*経路、MPC、入力遅延と角速度推定、Windows入力・撮影、進捗、実況、入力記録・再生。
- 操作スクリプト：`scripts/flyhome/`。連続実行、監視、専用コントローラ生成、撮影、保存版の復元。
- 実効設定：`config/windows/fly-me-to-the-home.toml`。960×540の窓を320×180で観測、PrintWindow撮影とPostMessage入力。
- 物理設定：`config/windows/flyhome-saved/physics.json`。重力86、推力211、片足比0.89、角加速度5.29、角減衰0.17、抵抗0.55。
- Git管理外だった9個の専用コントローラ：同ディレクトリの `controller_profiles/level{35,36,37,38,39,41,44,46,49}.py`。campaignは対応する面だけに適用する。過去コミット由来の挙動も含むため、現在のmpc.pyだけでは再現できない。
- 実マップ数字テンプレート：`map-digits.json`。
- テストと実画像の回帰fixture：`tests/test_flyhome*.py` と `tests/fixtures/*flyhome*`。
- 証拠：`evidence/` の実機PNG109枚。複数期間の画像を含むため、ファイル名だけで面番号を断定しない。
- 結果：`results/` に4期間の結果ログと231セッションの試行記録。端末固有の絶対パスは相対化した。
- 未採用差分：`unconfirmed-experiments/`。未検証のパッチで、実行時に適用しない。
- `manifest.json`：保存内容のSHA-256。BOTソース・設定・回帰テストも照合する。テキストはLFに正規化してハッシュ化し、Windows/Linux間の改行差を許容する。

APIキー、音声トークン、OBS送信キー、VM接続情報、環境変数、製品ファイル、内部運用正本 `handoff.md` は含めない。全フレームの生ログ約1.4GBは元端末の `run/flyhome/sessions/` に残した。リポジトリにはBOTの実行・復元に必要な内容と、試行・クリアの証拠を保存する。

## クリア状況

15〜49面の順番通りの再プレイは各面の実機クリア画面を個別確認済み。1〜14面は以前のプレイ記録・画像を参照。

50面はユーザーからクリア報告あり。保存時はゲームのウィンドウが存在せず、新しいクリア画面を撮れなかった。最後のBOTログは50面の失敗試行までで、50面のクリア記録を後から作っていない。全50面のマップ✓/BEST TIMEの最終監査も未実施。`status.json` に確認範囲を記録。

全メダル取得は未着手。50面確認後に1面から取り組む依頼は残っているが、再開指示までは停止する。

## 別のWindowsで復元

製品版をSteamからインストールし、このブランチをcheckoutする。リポジトリのルートで実行する。

```powershell
git config core.longpaths true
py -m pip install -r requirements-test.txt
py scripts/flyhome/restore_saved_bot.py --check
py scripts/flyhome/restore_saved_bot.py
```

復元先は `run/flyhome/`。異なる既存設定があればコピー前に中止する。古い結果ログは自動復元しない。

再開の指示がある時だけ、ゲーム内で開始面を選び、次を実行する。以下は15面から50面まで順番に再プレイする例で、メダル収集専用戦略ではない。

```powershell
$env:FLYHOME_LEVEL_ORDER='sequential'
$env:FLYHOME_CAMPAIGN_LOG='run/replay-new-results.txt'
$env:FLYHOME_STATE_FILE='run/game-state.txt'
bash scripts/flyhome/supervise.sh 15 36 900
```

監視スクリプトは「既にそのレベル内にいる」設定で動く。開始面を選んでから起動する。進捗は各ステップで上書き。実況は `FLYHOME_AUDIO_URL` を安全な環境から設定した時だけ既存キューへ送る。URLや認証情報は保存版に固定しない。

## 実機で得た要点

- 地面では回転しない。片足でも推力が大きく、キーを離しても回転が残る。入力遅延と角速度の予測が必要。
- HOME文字との重なりでプレイヤーの形が崩れる。過去の姿勢・家の位置で追跡し、帰宅中に見失ったら待つ。家の前で満ちる白い面はクリアの証明ではない。
- クリアは上下端の濃い橙の像の列などで確認。マップでは自動で次の面が選ばれるとは限らず、順番モードで番号を確認して1面ずつ進める。
- 狭い通路では余白を段階的に縮めて探索し、経路を維持する。HUDに隠れる暗赤の小棘も認識する。
- 50面は縦スクロールで画面yが約80に固定される。画面速度は世界内の速度と一致しない。HOME上矢印は途中目標であり、実際の家と区別する。
- 50面の最初の警告列x=72/152/232を避け、最初からx=134付近へ寄せる。次の警告を2秒保持し、純赤のDANGER文字を棘から分離する。最高到達はクリアの代わりにしない。
- 50面右上の高度表示はKM単位。以前の調査メモのMとの読み違いを訂正。単位から物理換算率は断定していない。

## 検証

運動シミュレータを使用せず、実観測の回帰と単体テストを実行する。既存の運動シミュレータテストは明示的なopt-inがない限りCIでも実行しない。PowerShellからはテストファイルを明示して渡す（ワイルドカード展開に依存しない）。

```powershell
$env:PYTHONPATH='src'
$cases = @(Get-ChildItem tests/test_flyhome*.py | ForEach-Object { $_.FullName })
py -m pytest -q --noconftest @cases -k 'not TestControllerInSim and not TestRunnerWithSim'
```

保存版の実行結果は `verification.json` を参照。実機再プレイ、全50面最終監査、独立レビュー、CI、本番配備とは区別する。本番VM・OBS・配信設定を変更せず、VM作業バナーも操作していない。
