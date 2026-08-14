# クイックスタート

対象: Ubuntu 24.04 (arm64/amd64)。**既に soren (sorengame) が本番稼働中の VM でも安全**に
導入できる設計になっている (ディスプレイ番号・PulseAudio sink・tmux セッション名・配信の既定を
soren と分離している。詳細: [[アーキテクチャ|Architecture]] の soren 共存原則)。

## 1. 導入

```bash
# 1. リポジトリを clone する (非公開リポジトリ。GitHub アクセス権が必要。
#    ゲーム実装はサブモジュール (games/soviet_now, games/hanjuku-sfc-speedrun) なので
#    --recurse-submodules を付ける)
git clone --recurse-submodules git@github.com:azumag/docich.git
cd docich
# 既に clone 済みでサブモジュールを未取得の場合は代わりに:
#   git submodule update --init

# 2. VM の依存パッケージを導入する
#    (xvfb/tmux/ffmpeg/retroarch/nethack-console 等の apt install --no-upgrade と mkdir のみ。
#     既存サービス・設定 (soren 関連含む) には一切触れない)
scripts/setup_ubuntu_arm.sh

# 3. 依存コマンド・環境を点検する (アダプタ別に OK/NG 表示)
bin/docich doctor
```

## 2. 起動してみる (NetHack)

```bash
bin/docich up               # display(:98)/audio/stream の基盤を起動
bin/docich start nethack    # ゲームを起動
bin/docich status           # 各コンポーネントの生死・現在のゲームを確認
bin/docich snap             # スクリーンショットを撮ってみる (run/screenshots/manual.png)
bin/docich down              # 全停止
```

より詳しい日常操作 (ゲーム切替・観測・ログ調査・停止再起動の違いなど) は
[[日常運用|Operations]] を参照。

## 3. 配信を有効にする

既定 (`config/docich.toml` の `[stream] mode = "null"`) では**配信しない**。soren が既に
本番配信中の VM であることを踏まえた事故防止の既定値であり、明示的に設定を変えない限り
配信ストリーム・キーが soren 側と混同されることはない。

実配信する場合:

1. `config/docich.toml` の `[stream] mode` を `"rtmp"` に変更する。
2. ストリームキーは **環境変数のみ** で渡す (リポジトリ・設定ファイルには書かない):
   ```bash
   export DOCICH_STREAM_KEY="<配信キー>"
   ```
3. `bin/docich up` (既に起動済みなら `down` → `up`) で反映する。

`mode = "file"` にすると `run/out.flv` へローカル保存でき、実配信前の動作確認に使える。

## 4. 半熟英雄 (SFC) を動かす最短手順

1. 自己吸い出しした ROM を `games/roms/hanjuku-hero.sfc` に置く (docich は ROM の入手方法には
   一切関与しない。ポリシー: `games/roms/README.md`)。
2. `bin/docich up` 済みであれば:
   ```bash
   bin/docich start hanjuku-hero
   ```

コアの自動探索・操作確認・代表的なトラブルは [[半熟英雄 (SFC)|Game-Hanjuku-Hero]] を参照。
