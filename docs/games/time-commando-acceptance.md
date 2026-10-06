# Time Commando 非本番受入

関連: [Issue #17](https://github.com/azumag/docich/issues/17)、
[設定例](time-commando.md)。この手順の追加だけでは実ゲームを起動しない。
検査器は本番loader・起動preflight・rotationから呼ばれない独立ツールである。

## 起動しない構成検査

外部の私的ZIP/DOSZを指定して、次を実行できる。

```sh
PYTHONPATH=src python3 -m docich.time_commando_preflight /PRIVATE/content.dosz
python3 -m unittest discover -s tests -p 'test_time_commando_preflight.py'
```

検査器は展開・変換・コピー・ダウンロード・プロセス起動を行わない。
ZIPメタデータと64KiB以下のCUE記述だけを読む。TIMECO.EXEと付属runtime/resource、
CD参照の存在・大文字小文字、3トラック構成、INDEXまでの宣言サイズを確認する。
パス逸脱、重複名、case衝突、symlink、暗号化、過大な構成は拒否する。
対応CUE文法は引用符付きBINARY FILE、MODE1/2352とAUDIO TRACK、INDEX 00/01、REMのみ。
別版の文法を勝手に変換せず、unsupportedとして個別確認する。

成功値は `archive_structure=metadata_checks_passed` に限定する。
CD本体・EXEの完全性、正規所有、互換性や権利を証明せず、
`runtime_acceptance=not_run` と `rights_acceptance=not_assessed` を必ず返す。
内容のSHAやホスト絶対パスを結果へ含めない。実データをCI入力にしない。

設定調査用にCD本体を省いた資料ではCD参照が未解決になるのが期待結果である。
これは実機配置の欠落・故障を意味しない。資料の再提出も要求しない。
GAME.DATにCUE文法があってもPureでの自動認識を合格扱いせず、
将来の私的content準備で明示的な `.cue` を確認する。検査器自身は改名しない。

## 実受入の前提

実施者は先に対象環境、ユーザー所有配置、利用するcoreの版・由来、
コピーや変換が必要ならその対象・保存先、および今回の実起動範囲を確認する。
不明な実行ファイルの起動や本番操作へ、静的検査の成功を流用しない。

- 本番と別のstate/display/save領域を選び、共有tmux sessionとcommand portの衝突も確認する
- streamはnull。agent、watchdog、全コーナー、trading worker、通知・字幕は無効
- 正のviewport寸法とprivate contained presentationを使い、round-boundaryを維持する
- 元データ・既存保存は変更しない。CUEと参照画像の位置・caseを保ち、私的試験contentを外部に置く
- Windows launcherを起動せず、TIMECO.EXEだけを選ぶ。DOSBOX.BATやconfのautoexecは別途レビューする
- 最初は無音。音声試験時だけ専用sinkを使い、既定sinkや共有配信先へ接続しない
- core/CPU/video/memory設定を固定して記録する。既存adapterはsave/stateを世代別に置くため、世代をまたぐ復元方法は未受入

## 実測する順序と判定

各項目は証拠が揃った場合だけpassedとし、未実施はnot_run、失敗はfailed、
権限・構成待ちはblockedとする。下の項目が未完なら実ゲーム受入を完了にしない。
記録テンプレートは `config/examples/time-commando/acceptance.json` に置く。
実測時はリポジトリ外の私的コピーへ記録し、空欄やnot_runを推測で埋めない。
このJSONはloaderが読むゲーム設定でも自動実行指示でもない。

1. **開始とCD**: coordinator経由で起動し、対象content/core、C:の起動対象、D:のCD、
   新規ゲームの実画面を確認する。titleだけでは合格にしない。欧州版introに声がないのは公式仕様。
2. **映像と入力**: 640×480の四辺とHUD、方向、Ctrl/Alt同時押し、Space、武器選択を確認する。
   Game Focusを使い、ホットキー誤発火・二重入力・キー解除後の移動継続がないことを確かめる。
3. **音声**: 初回無音を確認した後、許可された専用sinkで効果音とCD音声を別々に聴く。
   MIDIが必要なら使用音源の利用条件も確認し、音がない原因をCDと混同しない。
4. **ゲーム内保存**: READMEが示す青チップのアップロード端末まで進み、保存後の画面を記録する。
   任意時点の保存メニューを仮定しない。Pureの保存ZIP書込みを確認し、freshな私的起動で
   Load Gameから同じplayer・difficulty・worldの保存を選び、到達地点を復元する。
5. **save-state**: 同じcore/CPU/video/memory設定で独立して保存・復元し、画面と操作継続を確認する。
   stateファイルの存在やSAVE_STATE応答だけでは合格にしない。ゲーム内保存とは別項目にする。
6. **停止境界**: 保存を終え、全キーを解除し、Pで一時停止した画面を確認する。
   READMEでは任意の次キーで再開するため、確認後はゲーム入力を送らない。
   保存済み・一時停止・明示確認の既存契約を通す。タイムアウトや不明境界を強制停止の成功にしない。
7. **通常終了と再起動**: ゲーム終了、Pureメニューへの戻り、RetroArchプロセス終了を区別する。
   coreのメニュー設定次第で終了後もプロセスが残るため、終了検知は実測する。
   coordinatorの終了と対象子プロセス解放、私的再起動で保存復元を確認する。
8. **性能**: 起動・通常移動・戦闘・CD音声・保存それぞれでCPU/RSS、フレーム更新、音切れを記録する。
   同じ条件の前後比較を使い、cycles=maxやtitleの低負荷だけで性能合格にしない。

保存ZIPには遅延書込みがある。固定2秒の待機を保存完了判定にせず、
必要な書込みの収束と再読込みを証拠にする。保存・終了判定が未成立なら、その状態を残して確認を求める。
将来の本番コーナー試験は別段階で、元画面復帰・共有配信PID維持・無人切替も受入対象にする。

## 利用と配信の境界

提供版の出自は、古いREADME・CUEやapp IDファイルだけで確定しない。
本人の正規所有を保持したうえで、今回使うデータの版・配布元を実配置と照合する。
Steam版を選ぶ場合、2026-10-06確認の[Steam規約4.C](https://store.steampowered.com/subscriber_agreement/#4)は、
Content and Servicesをscripts/bots等の非人間制御で操作することを広く制限する。
その版の自動プレイが許されるとは扱わない。旧CDや別配布版へSteam規約を自動適用しない。
別環境の私的利用・配信・収益化の条件も、
対象版の条件と権利者の方針を個別に確認する。DOSBoxのGPLはゲームデータの許諾ではない。
現時点でTime Commando固有の明示的な配信・AI操作許諾はこの受入に含めていない。
まず私的・手動・無配信の互換性確認と、自動コーナー導入判断を分ける。

## 根拠

- [Steam公式 Time Commando](https://store.steampowered.com/app/1758910/Time_Commando/)のDOSBox・欧州版の説明
- [DOSBox Pure公式Libretro資料](https://docs.libretro.com/library/dosbox_pure/)のcontent、保存、入力、終了設定
- [DOSBox Pure公式リポジトリ](https://github.com/schellingb/dosbox-pure)はCodeberg移転を案内している。将来取得する際は公式移転先・版・hashを確認し、旧リンクだけで安全性を判断しない
- 手元の対象版READMEの入力・保存・pause記載。README本文、manual、ゲームデータはこの文書へ転載しない
