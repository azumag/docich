"""Fly Me To The Home! (Steam app 2076670) を Windows 上でプレイするための部品群。

docich 本体 (Linux/X11 + tmux) とは独立した Windows 専用ランナーで、標準ライブラリのみで動く。

- ``win32``     : ウィンドウ探索・画面取得 (GDI)・キー入力 (SendInput)。Windows 専用。
- ``steam``     : Steam のインストール先・ライブラリ・ゲーム導入有無の検出と起動。
- ``image``     : RGB 画像と PNG 読み書き (zlib/struct のみ)。
- ``vision``    : 320x180 のネイティブ画面からプレイヤー/家/障害物/地形/HUD を抽出する。
- ``tracker``   : フレーム間の速度・角度推定と、死亡/帰宅(クリア)の判定。
- ``planner``   : 障害物マップ上の A* で家までの経路を作る。
- ``control``   : 左右ジェットの ON/OFF を決める制御器。
- ``sim``       : 制御器を検証するための簡易物理シミュレータ (仮説モデル)。
- ``calibrate`` : 実機のパルス入力ログから物理パラメータを推定する。
- ``timeline``  : 入力タイムラインの記録・再生形式。
- ``runner``    : 上記をつなぐ実行ループ (play / probe / record / replay)。
- ``cli``       : ``python -m docich.flyhome`` のエントリポイント。

調査結果と引き継ぎ手順は ``docs/games/fly-me-to-the-home.md`` を参照。
"""

APP_ID = 2076670
DEMO_APP_ID = 2919910
