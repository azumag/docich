# Oracle Cloud ARM (Ampere A1) セットアップガイド

Soren Game AI の 24 時間ヘッドレス配信環境を Oracle Cloud Always Free (Ampere A1, 2 OCPU / 12GB RAM, Ubuntu 24.04 LTS ARM64) に構築するためのハンズオン手順書。

> Oracle の無料枠を 2026-08-12 に公式文書で再確認。free-only テナンシの A1 上限は合計 2 OCPU / 12 GB。4 OCPU / 24 GB は無料トライアルまたは PAYG 前提であり、常時稼働の全量が無料になる構成ではない。

## 表記ルール

- 【確認済】: 本ガイド作成時に Oracle 公式ドキュメント・公式リリースページ・実リポジトリを直接確認した事実
- 【未確認】: 推測または要実機検証の事項

---

## 1. Oracle Cloud 無料アカウント作成の要点

1. **申し込み**: https://signup.cloud.oracle.com から。メールアドレス・国・氏名・住所・クレカを入力。
2. **クレカは認証目的のみ**: 本人確認のため $1 前後の一時保留が発生しますが、無料枠の利用だけであれば課金は発生しません（保留は数日で解除されます）。**【未確認】** 一時保留額・解除日数は国・カード発行元で異なります。
3. **審査注意**:
   - 住所とクレカの請求先住所が一致しないと却下されることがあります。
   - VPN・プロキシ経由での申し込みは審査落ちの原因になります。
   - 却下された場合は、Oracle のサポートチケットから復活を依頼するか、別カードで再試行します。
4. **1 人 1 アカウント**: 無料アカウントは 1 顧客につき 1 つとされており、複数作成は利用規約違反で停止され得ます。**【未確認】** 公式の明文化場所の細部は要確認。
5. **Home Region がすべての起点**: 無料枠のリソース（コンピュート・ブロックボリューム）は home region でのみ作成可能で、home region は後から変更できません。配信先サーバーとして使うリージョンを最初に決めます（東京・大阪は人気のため容量不足になりがち。その分リージョン選択が勝負）。
6. MFA: セキュリティ向上のためコンソールに追加認証を設定することを推奨。

### Free Trial・Always Free・PAYG の違い

| アカウント状態 | A1 の扱い |
|---|---|
| Free Trial | $300 credit の有効期間中は 2/12 を超える構成も credit で実行できる |
| free-only | A1 合計 2 OCPU / 12 GB まで |
| PAYG | Always Free 分は残るが、月間無料量を超えた利用は課金対象 |

【確認済】Oracle は A1 の月間無料量を 1,500 OCPU-hours / 9,000 GB-hours としている。4 OCPU / 24 GB を 24 時間連続で動かすと、この両方を超えるため「PAYG にすれば 4/24 も無料」ではない。

【確認済】トライアル終了時に A1 合計が 2 OCPU / 12 GB を超えている free-only テナンシでは、既存 A1 が無効化され、PAYG へ移行しなければ 30 日後に削除される。終了前に PAYG へ移行するか、合計 2/12 以下へ縮退する。

- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

---

## 2. A1 Flex インスタンス作成（2 OCPU / 12GB）

### コンソール手順

1. メニュー → Compute → Instances → **Create instance**
2. Name: `soren-prod` など
3. Image: **Ubuntu 24.04** を選択（【確認済】公式ドキュメントで A1 形状の対応イメージに Ubuntu が明記されている。Oracle Linux も可）
4. Shape: **VM.Standard.A1.Flex**（Always Free eligible）を選択し、OCPU 数 **2**、メモリ **12 GB** に設定
   - 【確認済】free-only テナンシでは A1 全体で **2 OCPU + 12 GB** が上限（1,500 OCPU-hours / 9,000 GB-hours per month）
   - 2 台に分ける場合: 1 OCPU / 6GB × 2 台も可能
5. Networking: 新規 VCN とパブリックサブネットを作成（後述のセキュリティ設定）
6. Boot volume: 既定 **50 GB**（【確認済】無料枠のブロックボリュームは合計 **200 GB** で boot と追加ボリュームを合わせてカウント。boot は 50 GB 既定、最小 47 GB。必要なら 200 GB まで拡張可だが無料枠を使い切る）
7. SSH キー: コンソールで **Generate a key pair**（秘密鍵を安全に保存）または自分の公開鍵を貼り付け。**パスワード認証は使えない**ため鍵は必須
8. Create → 起動後に表示されるパブリック IP を控える

### Soren の性能比較用 4 OCPU / 24 GB

無料トライアル中または PAYG では 4/24 を選べる。Soren の短時間ベンチマークでは 4/24 が 720p30 を満たし、2/12 はゲーム描画と drop/duplicate 条件を満たさなかった。ただし 4/24 の継続は課金または trial credit 消費を伴う。トライアル終了前に必ず構成方針を決める。

### "Out of capacity" エラー時の対処

【確認済】公式ドキュメントに明記された対処:

- **別の Availability Domain (AD) で再作成**を試す
- 数時間〜1 日待って再試行（容量は日によって変動）
- OCPU 数を減らす（2 OCPU 1 台 → 1 OCPU 2 台 など）
- 最終手段として **Pay As You Go (PAYG) へのアップグレード**（無料枠は無料のまま、有料リソースの利用時のみ課金。容量確保が格段に楽になる）

補足: 【確認済】韓国北（春川）リージョンでは A1 インスタンスを作成できません。

### 初回ログインと基本設定

```bash
ssh -i ~/.ssh/soren_id_rsa ubuntu@<パブリックIP>
sudo apt update && sudo apt upgrade -y
sudo timedatectl set-timezone Asia/Tokyo
```

---

## 3. セキュリティ（セキュリティリスト）

1. VCN → **Security Lists** → 既定のセキュリティリストを編集
2. イングレスルールは **TCP 22 (SSH) のみ** を許可（ソース `0.0.0.0/0`。特定 IP や Tailscale 利用時は `100.64.0.0/10` に絞るのも可）
3. その他のポート（RDP 3389、OBS WebSocket 4455 など）は**開けない**。SSH トンネル経由でアクセスする（§4）
4. アウトバウンドは既定のまま全許可（RTMP 配信は外向き通信のため）
5. SSH は鍵認証のみ（Oracle イメージ既定でパスワード認証は無効）
6. キーペアを追加する場合は、コンソールで**新鍵を追加してから**古い鍵を削除する（ロックアウト防止）
7. コンソールに MFA を設定することを推奨

---

## 4. GUI 環境（Xvfb + XFCE + xrdp）

配信実行時は**仮想ディスプレイ Xvfb** を使い、メンテナンス時にのみ **xrdp** で GUI を操作できるようにします。

### Xvfb（配信用ディスプレイ）

```bash
sudo apt install -y xvfb x11-utils xdotool wmctrl
Xvfb :99 -screen 0 1280x720x24   # 720p 配信用の動作確認
```

systemd サービス化（/etc/systemd/system/xvfb.service）:

```ini
[Unit]
Description=Xvfb virtual display :99
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x720x24 -nolisten tcp
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xvfb
```

以後、配信プロセスには `DISPLAY=:99` を付けて起動します。

### XFCE + xrdp（メンテナンス用）

```bash
sudo apt install -y xfce4 xrdp
sudo systemctl enable --now xrdp
```

**xrdp (3389) は OCI 側で開放しない。** SSH トンネル経由で接続:

```bash
# ローカル (macOS 側) で:
ssh -L 13389:127.0.0.1:3389 ubuntu@<パブリックIP>
# → RDP クライアントで localhost:13389 に接続
```

**【未確認】** xrdp のデフォルトセッション (Xorg) と Xvfb :99 は別ディスプレイになります。配信は常に Xvfb :99 で統一し、xrdp は設定・デバッグ専用と割り切るのが安全です。

### Tailscale（推奨）

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

- Tailscale 導入後は SSH を Tailscale 限定（イングレス 22 のソースを `100.64.0.0/10` に変更）にできます。
- マシン間のファイル転送（`tailscale file cp` 等）も使えて便利です。

---

## 5. OBS Studio

### 導入方法

**arm64 環境（本ガイドの構成）では Ubuntu 公式リポジトリが本命です。**

- 【確認済】Ubuntu 24.04 (noble) の universe に **arm64 版 `obs-studio` が存在**します:
  - noble: `obs-studio_30.0.2+dfsg-3build1_arm64.deb`（ダウンロードページで実在を確認）
  - noble-backports: `obs-studio_30.2.3+dfsg-3~bpo24.04.1_arm64.deb`
  - どちらも OBS 28+ 相当なので obs-websocket 同梱（§「obs-websocket の有効化」参照）
- 【確認済】他系統は arm64 非対応:
  - GitHub Releases（OBS 32.2.1 時点）: Linux は Ubuntu 24.04/26.04 の **x86_64 .deb のみ**、arm64 なし
  - Flathub (`com.obsproject.Studio`): 配布アーキテクチャは **`["x86_64"]` のみ**（aarch64 配布なし）
  - PPA (obsproject/obs-studio): arm64 には対応していません
- 【確認済】OBS 30.0.2 の XComposite source id は `xcomposite_input`、設定キーは `capture_window`。item value は XID・title・WM_CLASS を改行で連結した形式。
- 【確認済】Oracle A1 実機では XSHM 全画面キャプチャを 1280x720 で動作確認済み。OBS UI やブラウザのアドレスバーを前面に出すと写り込むため、ゲーム配置の固定または FFmpeg direct backend を使う。

```bash
# arm64 (Oracle A1, Ubuntu 24.04) — 本命
sudo apt update && sudo apt install -y obs-studio

# より新しい 30.2.x (noble-backports) を選ぶ場合:
#   /etc/apt/sources.list.d/ubuntu.sources の Suites 行に noble-backports が
#   あればそのまま -t noble-backports で。無ければ:
#   echo "deb http://archive.ubuntu.com/ubuntu noble-backports main universe" \
#     | sudo tee /etc/apt/sources.list.d/noble-backports.list
sudo apt update && sudo apt install -y -t noble-backports obs-studio

# 参考: x86_64 環境で最新版 (32.x) が欲しい場合
sudo add-apt-repository ppa:obsproject/obs-studio
sudo apt update && sudo apt install -y obs-studio
```

### obs-websocket の有効化

- 【確認済】OBS 28+ では obs-websocket が本体に同梱されています。
- 有効化: OBS 起動 → メニュー ツール → **WebSocket サーバー設定** → 有効化、ポート **4455**、パスワード設定（既存 `.env` の `OBS_WEBSOCKET_PORT=4455` / `OBS_WEBSOCKET_PASSWORD` と揃える）。
- 設定は `~/.config/obs-studio/global.ini` に保存されます（ヘッドレス運用での変更手段。**【未確認】** ファイル直接編集で反映されるかは要確認）。

### obs-headless について

- 【未確認】OBS 公式の Linux ヘッドレスビルドは配布されていません。コミュニティの obs-headless プロジェクトは存在しますが、arm64 対応・WebSocket 同梱は要調査。
- **現実的な方針**: Xvfb (`DISPLAY=:99`) 上で通常の OBS を起動する方式で代替します（§4 の Xvfb がそのため）。ヘッドレスでもメニューなしで 24h 動作します。

---

## 6. VOICEVOX（音声合成エンジン）

### 公式 Linux CPU / arm64 ビルド（推奨）

- 【確認済】GitHub Releases（VOICEVOX/voicevox_engine、最新 0.25.2 時点）に **arm64 用 Linux CPU アセットが存在**:
  - `voicevox_engine-linux-cpu-arm64-0.25.2.7z.001`
  - `voicevox_engine-linux-cpu-arm64-0.25.2.vvpp`
- ダウンロード・展開・起動:

```bash
sudo apt install -y p7zip-full curl
cd /opt && sudo mkdir -p voicevox && cd voicevox
curl -L -o voicevox.7z.001 \
  "https://github.com/VOICEVOX/voicevox_engine/releases/download/0.25.2/voicevox_engine-linux-cpu-arm64-0.25.2.7z.001"
sudo 7z x voicevox.7z.001
# → voicevox_engine-linux-cpu-arm64-0.25.2/ が展開される

cd voicevox_engine-linux-cpu-arm64-0.25.2
./run      # 起動。http://127.0.0.1:50021 で待ち受け
```

- 動作確認: `curl -s http://127.0.0.1:50021/speakers | head`
- systemd サービス化（WorkingDirectory を展開先に、`Restart=always`）。
- 補足: マルチパート (`.002`) がある場合の分割ダウンロードは不要か、リリースページで `voicevox_engine-linux-cpu-arm64-0.25.2.7z.001` のみ配布されていることを確認してください（0.25.2 は .001 単体でした）。

### Docker 版との比較

| 項目 | 公式 CPU アセット (arm64) | Docker (voicevox/voicevox_engine:cpu-latest) |
|---|---|---|
| arm64 対応 | 【確認済】あり | 【未確認】イメージの arm64 タグ有無は要確認 |
| 依存 | 自己完結（Python 一式同梱） | Docker 環境が必要 |
| 起動 | `./run` ですぐ | `docker run` が必要 |
| リソース | ~1–1.5GB（目安） | 同程度（目安） |

**推奨: 公式 CPU arm64 アセットの直接実行**（Docker 不要・ARM 対応が確実）。CPU 合成のため 2 OCPU では合成に数秒かかることがあります（【未確認】実測で調整）。

---

## 7. Google TTS（gcloud）認証セットアップ

- 【確認済】soren の `google_tts.sh` は `gcloud auth print-access-token` → Text-to-Speech REST API の構成。gcloud CLI は **Linux arm64 の .deb** が公式配布されています。

```bash
sudo apt install -y apt-transport-https ca-certificates gnupg curl
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt update && sudo apt install -y google-cloud-cli
```

- 認証（ヘッドレス環境）:
  - **推奨: サービスアカウント方式** — GCP コンソールで SA を作成し JSON キーを取得:
    ```bash
    gcloud auth activate-service-account <sa>@<project>.iam.gserviceaccount.com --key-file=~/gcp-tts-key.json
    ```
  - またはブラウザ認証: `gcloud auth login --no-launch-browser` で表示される URL を手元のブラウザで開いてコードを入力。
- **Text-to-Speech API の有効化**が必要: `gcloud services enable texttospeech.googleapis.com`
- 【確認済】`google_tts.sh` は `PROJECT="gen-lang-client-0367522921"` がハードコードされています。VPS では自分のプロジェクト ID に合わせるか環境変数化してください（移植計画 §2.7）。

---

## 8. Node.js + Playwright（chrome for testing）

```bash
# Node.js 22 LTS (arm64)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Playwright が必要とするシステム依存
sudo apt install -y build-essential libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2 libpango-1.0-0 libcairo2 fonts-liberation fonts-noto-cjk
```

```bash
cd <soren リポジトリ>
npm ci                      # playwright ^1.58.2, dotenv, sharp が入る
npx playwright install --with-deps chromium
```

- 【確認済】Linux ではブラウザバイナリが **`~/.cache/ms-playwright/`** にインストールされます（soren の `soviet_local.mjs` は `chromium.executablePath()` で解決するため特別な設定不要。`PLAYWRIGHT_BROWSERS_PATH` で変更可）。
- chrome-headless-shell（ヘッドレス専用ビルド）が必要なら: `npx playwright install chromium-headless-shell`
- 動作確認:
  ```bash
  # headless
  SOREN_CHROME_HEADLESS=1 node -e "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch({headless:true});console.log('ok');await b.close()})()"
  # headed (Xvfb 上)
  DISPLAY=:99 node -e "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch({headless:false});console.log('ok');await b.close()})()"
  ```

---

## 9. メモリ・ディスクの留意点（12GB RAM）

### メモリ配分目安（【未確認】実測で調整）

| プロセス | 目安 |
|---|---|
| Chrome (Unity WebGL タブ) | 2–3 GB |
| OBS | 1–1.5 GB |
| VOICEVOX | 1–1.5 GB |
| soren ワーカー群 (python/node) | 1–2 GB |
| wildcard 並列候補 1 ジョブ | 0.5–1 GB |

→ **wildcard 並列ジョブは 1–2 に絞る**（現行 macOS の `.env` は `WILDCARD_PARALLEL_JOBS=2`）。

### スワップ（推奨: 8GB）

```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
# /etc/fstab に: /swapfile none swap sw 0 0
# /etc/sysctl.conf: vm.swappiness=10
```

### ディスク

- 無料枠は boot + block 合計 **200 GB**【確認済】。boot 既定 50 GB。
- OBS は録画無効（配信のみ）にしてログのみ保存。`logs/` は定期ローテーションを監視。
- 追加ボリュームは **home region でのみ無料**【確認済】。
- アウトバウンド **10 TB/月無料**【確認済】。24h 配信 2–6 Mbps なら約 0.8–2 TB/月に収まる計算（【未確認】実測値は本番で確認）。

---

## 10. 無料枠の回収ポリシーへの対処

### アイドル判定条件

【確認済】Oracle は、7 日間の CPU、network、memory（A1 のみ）の各利用率がすべて 20% 未満の場合、Always Free compute を idle と判定して回収することがある。

RTMP を送っているだけで network 利用率が 20% を超えるとは限らない。24 時間配信中でも回収されないと断定せず、OCI Monitoring で 3 指標を確認する。

### 対処方針

1. 配信と runtime の死活を監視し、停止時は systemd の restart policy で復旧する。
2. 回収回避だけを目的に人工的な負荷を生成せず、Monitoring とバックアップで備える。
3. 回収の通知・復旧プロセスは【未確認】（通知を試みた後、インスタンス終了 → 手動復旧となる運用と理解。自動再起動はありません）。**ブロックボリュームのスナップショットは 5 つまで無料**なので、定期的なスナップショット＋リポジトリの rsync バックアップを推奨します。
4. 運用の冗長化がしたい場合は 1 OCPU × 2 台構成も選択肢です（片方が回収されても片方が生き残る）。

---

## 付録: セットアップ完了チェックリスト

```bash
# 1. インスタンス
ssh ubuntu@<IP>            # 鍵認証でログインできる
free -h                     # ~12GB
df -h                       # boot 50GB

# 2. セキュリティ
#    セキュリティリストに TCP 22 のみ

# 3. GUI
DISPLAY=:99 xdpyinfo | head # Xvfb :99 が生きている
#    xrdp: ローカルから SSH トンネル経由で RDP 接続できる

# 4. OBS
DISPLAY=:99 obs --version  # 起動する (Ubuntu リポジトリの arm64 版 30.x)
#    WebSocket サーバー有効化 → 4455 で obs-websocket が応答

# 5. VOICEVOX
curl -s http://127.0.0.1:50021/speakers | head

# 6. Google TTS
gcloud auth print-access-token  # トークンが取れる

# 7. Playwright
ls ~/.cache/ms-playwright/      # chromium-*/ が存在

# 8. スワップ
swapon --show                   # /swapfile 8G
```
