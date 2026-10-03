# Fly Me To The Home!（Steam）— 事前調査と Windows ランナー

Steam の「Fly Me To The Home!」を AI が Windows 上でプレイするための調査結果と、
`src/docich/flyhome/`（Windows ランナー）の設計・使い方・引き継ぎ手順をまとめる。

- 対象: **Windows ローカル**（docich 本体の Linux/X11 配信基盤とは独立）。配信への組み込みは後続。
- 状態（2026-10-03）: 画面認識・制御・物理推定・入力記録/再生まで実装済み。
  オフラインテストは通過。**実機（Windows + Steam）では未実行**なので、下の「引き継ぎ」の順で確認する。

## 1. ゲームの調査結果

| 項目 | 内容 | 根拠 |
|---|---|---|
| タイトル | Fly Me To The Home!（開発・販売: tdhr、金沢） | Steam ストア |
| Steam App ID | 本編 **2076670** / 体験版 **2919910**（全 50 レベル中 12 レベル） | Steam appdetails API |
| 発売 | 2024-07-31。対応 OS は Windows / macOS / Linux（必要スペック欄は "unknown"） | 同上 |
| 価格 | 通常 $5.99（2026-10 時点はセールで $2.99。本編購入でサウンドトラックが付く） | ストアページ |
| ジャンル | 2 ボタンの「ジェットブーツ帰宅アクション」。一画面完結の 50 レベル、メダル（コイン）でスキン解放、レベルごとのタイムアタック、Steam ランキング・実績 | ストア説明 |
| 前作 | スマホ版「MR.JET」（同作者）。左右タップ → 左右ジェット | tdhr.jp |
| コントローラ | フルコントローラ対応（キーボードでも操作可） | appdetails |
| 配信・収益化 | **作者への連絡・許可は不要**。動画タイトルにゲーム名、概要欄にストア URL を入れてほしいとの依頼あり | ストア説明（日本語版） |
| エンジン | 公表なし（作者は Godot / Unity を使用）。実装は特定エンジンに依存させていない | tdhr.jp |
| 物理の注意 | 2024-08 に「LEVEL 50 の難易度が環境によって変わる不具合」を修正 → 以前はフレームレート依存だった可能性。**開ループ再生の決定性は要確認** | Steam ニュース |

### 操作（ゲーム内の案内表示から確認済み）

| キー | 動作 |
|---|---|
| ← / → | 左 / 右のジェットブーツ ON（押している間噴射すると推定。右下の矢印 HUD が押下中オレンジに光る） |
| Enter | 死亡後: リトライ。クリア後: 次の画面へ（マップへ戻る系のアイコン） |
| Esc | マップへ戻る |
| Space | クリア画面で何らかの操作（棒グラフのアイコン。ランキング表示と推測、要確認） |
| ↑ / ↓ | メニューの項目移動（BGM 設定画面の案内より） |

### 画面の実測（ストアのスクリーンショット 7 枚を解析）

- **内部解像度は 320×180**（16:9）。スクショは 1728×1080 の中央 1728×972 に 5.4 倍で描かれ、上下が黒帯。
- プレイヤー: 8×14 px のオレンジ (255,127,0)、白い顔、灰色のブーツ。回転しても橙画素数は約 92 px で一定。
  噴射中は足元に赤 (251,0,2)・黄 (249,255,0) の炎。
- 家（ゴール）: 屋根が暗赤 (128,0,0)、壁は紺、上に白文字 "HOME"。
- 障害物: 棘ブロック = 16×16 タイル、赤 (217,0,0) の縁 + 黒い中身。触れると爆発（死亡）。
- 地形: 草 (32,129,0) / 土 (128,64,0)。**地形に触れて死ぬかは未確認**（ランナーは安全側で避ける）。
- コイン: 白縁付きのオレンジ（回転アニメあり）。
- HUD: 左上 "LEVEL n"、右上タイマー "00:00.000"（開始時 0、最初の入力で動き出すと推定）、
  右下に ← → の押下表示。死亡/クリア時は左上に木の看板（Enter/Esc/Space の案内）が出る。
- 背景はレベルで変わる（昼の青空、夕焼け、夜の暗色など）。夜は地形も暗くなるため色は比率で判定する。

## 2. 実装（`src/docich/flyhome/`）

docich 本体と同じく **Python 3.11+ 標準ライブラリのみ**（Pillow / numpy 不要）。

```
取得 (GDI StretchBlt で 320x180 へ直接縮小)
  → vision   : 色ラベル → プレイヤー位置・向き・炎 / 家 / 棘 / コイン / 地形 / 矢印 HUD
  → tracker  : 速度・角速度、死亡 (消失) / 帰宅 (家の近くで消失) の判定、試行番号
  → planner  : 4px セルの A*（地形・棘・画面端を膨張して回避、家の箱をゴール）
  → control  : 目標速度 → 必要推力 → 体の傾き → 片足で回転 / 両足で推進 / 何もしない
  → win32    : SendInput（スキャンコード）で ←/→ を押し離し
```

| モジュール | 役割 |
|---|---|
| `win32.py` | ウィンドウ探索（タイトル部分一致 / exe 名）、DPI 対応、GDI キャプチャ（`screen` / `printwindow`）、SendInput / PostMessage、F12 停止 |
| `steam.py` | レジストリ → Steam ルート → `libraryfolders.vdf` → `appmanifest_*.acf` で導入確認、`steam://rungameid/<id>` で起動 |
| `vision.py` | 上記の実測色でラベル付け。色→ラベルはキャッシュするので 1 フレーム約 15ms |
| `tracker.py` | 位置の差分から速度（指数平滑）。3 フレーム見失ったら結末を確定 |
| `planner.py` | A*・見通しによる折れ点化・lookahead |
| `control.py` | `Physics`（重力・推力・片足推力比・回転速度/角加速度・回転の向き・抵抗）と `JetController` |
| `calibrate.py` | 入力一定区間の二次当てはめで `Physics` を推定 |
| `sim.py` | 仮説物理 + ゲームと同じ配色の描画。Linux CI で認識〜制御を一気通貫に検証 |
| `timeline.py` / `runner.py` | 入力タイムライン、play / probe / record / replay / watch のループと JSONL ログ |
| `cli.py` | `python -m docich.flyhome ...` |

設定は `config/windows/fly-me-to-the-home.toml`（未知のキーはエラー）。ログ・スクショ・推定物理は
`run/flyhome/`（gitignore 済み）に出る。

### 物理モデル（仮説）と制御

- 両足 ON で頭の方向へ推進、片足 ON で回転（＋弱い推進）、OFF で重力落下、と仮定している。
- **どちらの足でどちらに回るか、回転が「速度一定」か「加速」か、推力・重力の値は未計測**。
  `probe` で台本パルスを打って `calibrate` が推定し、`run/flyhome/physics.json` に保存する。
- 制御器は速度上限付き（既定 70 px/s）。タイムより確実な帰宅を優先している。
  シミュレータでは回転の向き・回転方式・片足推力 0・抵抗 0・推力弱めの各仮説で、
  壁越え + 棘回避のコースを帰宅できることをテストしている（`tests/test_flyhome.py`）。

## 3. 引き継ぎ: Windows ローカルでの確認手順

前提: Windows 10/11、Python 3.11 以上（`py -3.11` 等）、Steam にログイン済み。
まだ購入していなければ **体験版（`--demo`、12 レベル）で全手順を確認できる**。

```bat
cd docich
git fetch origin claude/zealous-planck-9ktan2
git checkout claude/zealous-planck-9ktan2
set PYTHONPATH=src
py -m docich.flyhome doctor
```

1. **導入と起動**: `doctor` の `full` / `demo` が `installed: true` か確認。無ければ
   `py -m docich.flyhome --demo launch --install`（Steam のインストール画面が開く）。
   起動は `py -m docich.flyhome launch`（体験版は `--demo launch`）。
2. **ウィンドウ設定**: ゲームの表示設定で **ウィンドウモード（16:9、例 1280×720 以上）** にする。
   `py -m docich.flyhome windows fly` でタイトル・exe を確認し、既定の "Fly Me To The Home" に
   一致しなければ TOML の `window_titles` / `exe_names` を直す。スキンは **既定（オレンジ）** にする。
3. **画面取得と認識**: レベル 1 を開始した状態で `shot` → `run/flyhome/shots/shot.*.png` を目視。
   - `shot.annot.png`: 緑枠 = プレイヤー（緑線 = 頭の向き）、水色 = 家、桃色 = 棘、黄 = コイン。
   - 画面が真っ黒なら `capture_mode = "printwindow"` を試す。
   - 認識がずれたら `shot.labels.png`（色ラベル）を見て `vision.classify_rgb` のしきい値か HUD 矩形を調整。
   - 入力なしで連続確認: `watch --seconds 15`（自分で操作しながら座標・角度・HUD が追従するか）。
4. **入力経路**: レベル内で `keytest`。右下の矢印 HUD の点灯が送ったキーと一致すれば `"ok": true`。
   一致しない場合: ゲームが管理者権限なら同じ権限で実行、`input_mode = "postmessage"` を試す、
   それでも駄目なら物理キーボード以外の入力を拒否している可能性（仮想パッド ViGEm 等が次の候補）。
5. **物理の推定**: 開けたレベル（1 など）を開始した状態で `probe --save`。死んでもよい。
   出力の `report`（各区間の加速度・角速度）と `run/flyhome/sessions/*-probe/snaps/` を見て妥当性を判断。
   数回繰り返し、`fit <frames.jsonl>... --save` で複数回分をまとめて推定し直せる。
   - `torque_sign`: 左キーで時計回り（頭が右へ倒れる）なら +1。
   - `spin_mode`: 片足を押している間の回転速度が一定なら `rate`、だんだん速くなるなら `accel`
     （自動判定は粗いので、`report` の角速度と目視で確認し、必要なら JSON を手で直す）。
6. **自動プレイ**: レベル開始状態で `play --attempts 10`。死亡 → 0.8 秒待って Enter → 再挑戦、帰宅で停止。
   `run/flyhome/sessions/*-play/` に `frames.jsonl`・`attempts.jsonl`・注釈スクショ・帰宅時のタイムラインが残る。
   **F12 で即停止**。ゲームが前面でない間はキーを送らない（他アプリへの誤入力防止）。
7. **記録と再生**（制御が難しいレベル向け）: `record` を実行して人間がプレイ → 帰宅/死亡ごとに
   `timeline-*.json` が保存される。`replay <timeline.json>` で再生し、複数回同じ結果になるかで
   物理の決定性を確かめる（決定的なら、確実にクリアできる配信用の手段になる）。

### 未確認事項（実機で最初に潰す）

- [ ] ウィンドウタイトル・exe 名、ウィンドウモードの有無と可能な解像度
- [ ] GDI キャプチャで画面が取れるか（取れなければ printwindow / Windows.Graphics.Capture）
- [ ] SendInput が届くか（`keytest`）
- [ ] ← が左足・→ が右足か、押している間だけ噴射か（トグルではないか）
- [ ] 地形（草・土）に触れたら死ぬか、着地できるか。画面外へ出たら死ぬか
- [ ] タイマーは最初の入力で動き出すか / 死亡後の Enter で即リトライか / クリア後の Enter・Space の遷移
- [ ] 物理の決定性（同じタイムラインの再生で毎回同じ結果か）とフレームレート依存
- [ ] 動く障害物などスクショに無い要素（レベル後半）
- [ ] マップ画面でのレベル選択操作（現状は人がレベルを開始してからランナーを動かす）

### 次の実装候補

1. 実機の色・HUD 位置の補正と、`physics.json` に合わせた制御ゲインの調整（`control.Gains`）。
2. レベル番号の識別: `level_sig`（左上の文字の白画素ハッシュ）をレベルごとに記録して対応表を作る。
3. マップ画面の操作と、クリア後に次のレベルへ進む自動化。
4. 改善ループ: 失敗試行の `best_dist`（家への最接近距離）を評価値に、タイムラインの部分変異で探索。
5. 配信: Windows 側で OBS 等から配信するか、docich（Linux）のオーバーレイと組み合わせるかを決める。

## 4. オフラインでの確認（Linux / CI）

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_flyhome.py
PYTHONPATH=src python3 -m docich.flyhome play --sim        # シミュレータで帰宅まで
PYTHONPATH=src python3 -m docich.flyhome probe --sim       # 物理推定の流れ
PYTHONPATH=src python3 -m docich.flyhome analyze shot.png  # 任意のスクショ(PNG)を解析
```

## 出典

- [Fly Me To The Home! — Steam](https://store.steampowered.com/app/2076670/Fly_Me_To_The_Home/)
- Steam appdetails API（`store.steampowered.com/api/appdetails?appids=2076670`）と ISteamNews（更新履歴）
- [tdhr.jp](https://tdhr.jp/) / [Fly Me To The Home! 公式ページ](https://tdhr.jp/fmtth/) / [MR.JET](https://tdhr.jp/mrjet/)
