# Time Commando: 非本番プロファイル準備

関連: [Issue #17](https://github.com/azumag/docich/issues/17)

既存のRetroArch adapterとDOSBox Pureを使う設定例。
この変更でゲームを自動登録・起動せず、インストールやデータコピーも行わない。
ゲーム本体、CDイメージ、manual、音声、保存データをGit・CI artifact・releaseへ含めない。

## 設定例と適用範囲

`config/examples/time-commando/time-commando.toml` は通常の
`config/games/*.toml` の外に置く。既存のゲーム一覧・rotation・本番設定は変えない。

- `adapter = "retroarch"` は既存schemaを利用する。
- `core` はDOSBox Pureの絶対パスを明示する。既存の `core = "auto"` はSNES coreの探索用。
- `rom` はリポジトリ外のユーザー所有データを指す絶対パスのプレースホルダー。
- `agent.enabled = false`、`retro_corner.enabled = false`、`unattended = false` を維持する。
- 初回は `audio_enabled = false`。音声を試す段階で専用sinkへ接続し、既定sinkを変更しない。
- `lifecycle.require_round_boundary = true` は既存の保存済み・一時停止中の明示確認契約を保持する。
  Time Commandoの終了検知、save-state復元、無人切替を検証済みとは扱わない。

通常のloaderはファイル名と `[game].name` の一致を要求する。
後の非本番試験で `games_dir` をこの例のディレクトリへ向ける場合も、
ファイル名は `time-commando.toml` を保つ。
プレースホルダーを含む設定のままではcontent/core存在確認に失敗する。

## 付属設定から確認したDOS構成

付属Windows launcherはDOSBOXディレクトリを作業位置にしてTC.confを読む。
そのautoexecは親ディレクトリをC:、GAME.DATをD:にマウントし、C:\TIMECO.EXEを実行する。
Windows版DOSBox.exeやlauncherをLinux/Pure上で実行する必要はない。

GAME.DATはCD本体ではなく、GAME.GOGを参照するCUE形式の記述。
MODE1/2352のデータ1トラックと音声2トラックを持つ。
今回の設定調査資料はCD本体を省いている。これは起動失敗や未所持の証拠ではない。
実データの所在・完全性は、後に対象のユーザー所有配置を確認する段階で調べる。

Pure向けの私的contentを準備する段階では、次を確認する。

1. CUEと参照先GAME.GOGの相対位置・大文字小文字を保つ。
   `.cue` はPureが明示的に対応する拡張子。GAME.DATのままの自動認識は未検証。
2. ZIP/DOSZを選ぶ場合、現在のPureはZIPルートをC:にする。
   `TIMCO/` 以下に配置する構成なら、起動対象は `C:\TIMCO\TIMECO.EXE`。
   この変更はcontentの作成・変換を実行しない。
3. 起動対象をTIMECO.EXEに限定する。Windows launcher、SETSOUNDやSETLANGを
   無条件に選ばない。必要なDOS runtime・driver・resourceの配置を維持する。
4. TC.confのホスト相対mountをそのまま実行せず、Pureのcontentマウントと
   D:のCD認識を確認する。confを自動読込みさせる方法もcore設定依存。
5. 元ゲーム設定と既存保存を保持し、変更・保存は専用の私的試験領域へ分離する。
   PureはZIP/DOSZの書込みを別save ZIPへ保存するが、直接ファイル起動は同じ扱いではない。

観測された設定はSVGA S3、メモリ30MB、CPU auto/max、ゲーム解像度640×480。
SB16は220h/IRQ5/DMA1/HDMA5、44100Hz。General MIDI設定は330h。
これらは元構成の記録であり、Pureや実機での動作保証ではない。
30MB設定の扱い、CPU=maxの負荷、CD音声・効果音・必要ならMIDIを個別に検証する。

## 手動入力案

READMEのデフォルトキーは次のとおり。付属TIMECO.DEFには独自の数値入力設定があり、
現在の割当がデフォルトと同じかは未確認。元設定を上書きせず、試験領域で確認する。

- 移動: `Up` / `Down` / `Left` / `Right`
- 攻撃・防御: `Control_L` と方向キーの同時押し
- 回避・ジャンプ: `Alt_L` と方向キーの同時押し
- アクション・探索: `space`
- 武器: `1`〜`6`、前後選択は `w` / `x`
- メニュー: `Escape`、選択: `Return` / `space`、一時停止: `p`

既存adapterの `key` actionは複数キーを同時に渡せる。
例: `{"type":"key","keys":["Control_L","Up"],"hold_ms":100}`。
まずRetroArchのGame Focusを有効にし、ホットキーやRetroPadへの二重入力を抑止する。
専用Gamepad Mapperを使う方法もあるが、SNES用の既定pad mapを
そのままTime Commandoの操作割当とは扱わない。
Game Focus/core optionsを本profileから自動設定する機構はこの変更に含めない。

## 後の非本番検証手順

実起動する段階で、対象環境・ユーザー所有配置の利用範囲を確認してから実施する。
この文書や静的テストの成功は、データコピー・実起動・本番追加を承認した扱いにはしない。

1. 本番と共有しないstate directory、空いているdisplay、専用保存先を選ぶ。
   streamは `null`、AI・watchdog・全コーナーを無効にした私的global設定を使う。
   `[display]` の `viewport_width` と `viewport_height` は両方とも正にする
   （例: `640` と `480`）。viewportの位置と全体寸法も整合させ、
   private contained presentationを使う。通常globalの既定値 `0` のままでは
   `require_round_boundary = true` のRetroArch preflightに拒否される。
   このround-boundary設定を維持し、試験もcoordinator経由の
   `docich start` / `switch` / `stop` を使う。legacyの `docich run game` や
   設定無効化でpreflight・保存境界を迂回しない。
   本番global設定や稼働中displayを流用しない。
2. content/core、D:のCD、起動対象TIMECO.EXEを確認する。
   タイトル表示だけでCD内の全データや音声が正常とは判定しない。
3. 新規ゲームで640×480の四辺、移動、Ctrl/Alt同時押し、Space、武器選択を確認する。
   キー解除後に操作が残らないこと、Game Focusで二重入力しないことも確認する。
4. 専用sinkを用意した場合だけ試験設定で音声を有効化し、効果音とCD音声を区別して確認する。
5. 私的保存先でゲーム内保存・再読込みとPure save-state復元を別々に検証する。
   DOS後期タイトルのsave-stateは公式資料でも個別検証が必要。
6. 既存のround-boundary契約に従い、保存完了・一時停止・明示確認を経て停止/再起動を試す。
   復元不能や境界不明なら未完了として停止判断を保留し、強制終了を成功扱いにしない。
7. CPU/メモリ、画像欠け、入力解除、音声、保存、通常終了、再起動を記録する。
   将来のコーナー試験では終了後の画面復帰と共有配信PID維持も別途確認する。

## 権利と未確認事項

付属COPYINGはDOSBoxのGPLであり、ゲームデータの再配布許諾にはならない。
manualはゲームとmanualの著作権留保を記載する。
この記載だけで本人の正規所有データの私的起動に追加許可が必須とも判断しない。
配信・動画投稿・AI操作・別環境での利用条件は対象版の条件に照らして別途確認する。

静的検証で確認できるのはschema適合、無効化gate、既存adapterの設定生成まで。
実ゲーム互換性、実データの所在、音声、性能、save-state、終了検知は未確認。

## ゲームを起動しない静的テスト

`python3 -m unittest discover -s tests -p 'test_time_commando_profile.py'`
で設定例・通常一覧への非登録・placeholderの存在確認失敗・無効化gateを検証する。
既存の `test_config.py`、`test_retroarch_adapter.py`、
`test_retroarch_readiness_contract.py` も同じ形式で個別に実行できる。
これらの成功と実ゲームの動作確認は区別する。

根拠: [DOSBox Pure公式Libretro資料](https://docs.libretro.com/library/dosbox_pure/)
（content、conf、CD、Game Focus、save-state）。
