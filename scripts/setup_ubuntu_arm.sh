#!/usr/bin/env bash
#
# docich: Ubuntu 24.04 (arm64/amd64) 向け VM 初期セットアップスクリプト
#
# このスクリプトが行うのは「apt install (未導入パッケージの追加のみ)」と
# 「作業ディレクトリの mkdir」だけである。apt upgrade / dist-upgrade は実行せず、
# systemd ユニット・pulseaudio の設定ファイル・その他 soren 関連のサービスや
# 設定には一切触れない。
#
# 【重要】この VM では soren (sorengame) が既に本番稼働中である想定。
# 本スクリプトは soren の稼働に影響を与えないことを目的として、上記の範囲に
# 限定している (詳細: docs/architecture.md §0 共存原則)。
# apt install には --no-upgrade を付け、pulseaudio など soren が既に使用中の
# パッケージが誤って上書きアップグレードされないようにしている
# (ただし新規パッケージの依存解決で無関係な共有ライブラリが更新される可能性は
# apt の一般的な挙動として残る。完全な隔離が必要な場合はコンテナ等を検討すること)。
#
# 対象: Oracle Cloud Ampere A1 (arm64) を主対象に、amd64 でも同じパッケージ構成で
# 動作確認できるようにしている。root 直接実行 (コンテナ・一部 VM イメージの初回など) と
# sudo 経由のどちらでも動く。
#
# 使い方:
#   sudo scripts/setup_ubuntu_arm.sh     # 一般ユーザーから
#   scripts/setup_ubuntu_arm.sh          # root から直接
#
# 詳細: docs/architecture.md §0, §1, §9 / docs/oracle_arm_setup_guide.md

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# --- root / sudo 両対応 -----------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# --- リポジトリルート (このスクリプトの1つ上) --------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- OS 確認 (警告のみ。他ディストリ・他バージョンでも止めない) -----------
OS_PRETTY_NAME="unknown"
OS_VERSION_ID=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_PRETTY_NAME="${PRETTY_NAME:-unknown}"
  OS_VERSION_ID="${VERSION_ID:-}"
fi

echo "==> docich セットアップ: $(uname -m) / ${OS_PRETTY_NAME}"
if [ "${OS_VERSION_ID}" != "24.04" ]; then
  echo "警告: Ubuntu 24.04 以外の環境です。パッケージ構成は 24.04 でのみ確認済みです。" >&2
fi

# --- apt パッケージ ------------------------------------------------------
# 【確認済】(docs/architecture.md 付録 ADR, §9): retroarch 1.18.0 / libretro-snes9x 1.61 は
# Ubuntu 24.04 (noble) universe に arm64 版が存在する (amd64 は言うまでもなく提供されている)。
APT_PACKAGES=(
  xvfb               # 仮想ディスプレイ (docich の既定は :98。:99 は soren が使用中)
  x11-utils          # xdpyinfo 等の診断ツール
  xdotool            # X11 入力注入 (XTEST) / ウィンドウ操作
  wmctrl             # ウィンドウ一覧・操作 (診断用)
  tmux               # 常駐プロセスの window 管理 (docich / docich-game セッション)
  ffmpeg             # 配信 (x11grab + pulse → RTMP/ファイル)
  pulseaudio         # 音声。既にインストール・稼働中なら --no-upgrade により変更しない
  pulseaudio-utils   # pactl 等
  xterm              # cli アダプタの映像化 (tmux attach を表示)
  fonts-dejavu-core  # xterm 等の基本フォント
  retroarch          # SFC 等のエミュレータフロントエンド
  libretro-snes9x    # SFC コア (半熟英雄用)
  libretro-core-info # RetroArch のコア情報 (自動探索に必要)
  dbus               # dbus-run-session (retroarch 起動ラップに必須。architecture.md §9-1b)
  nethack-console    # CLI ゲームの例 (NetHack)
)

echo "==> apt-get update (パッケージ索引の更新のみ。インストール済みパッケージは変更しない)"
${SUDO} apt-get update

echo "==> apt-get install --no-upgrade: ${APT_PACKAGES[*]}"
${SUDO} apt-get install -y --no-upgrade "${APT_PACKAGES[@]}"

# --- chromium (browser アダプタ, soren game 用) --------------------------
# 既定ではインストールしない (このスクリプトは実行しない)。理由: soren game を
# docich 単体の browser アダプタで動かすか、soren 本体の Playwright 経由 chromium
# (soren_linux_migration_plan.md §2.4) で動かすかは運用側の選択に委ねるため。
# 必要になったら以下のいずれかを手動で実行すること。
#
#   選択肢 1: Ubuntu の chromium は snap 配布 (deb 版は廃止済み)
#     sudo snap install chromium
#
#   選択肢 2: soren 側システムと連携する場合、soren リポジトリの
#     `npx playwright install --with-deps chromium` で導入される
#     ~/.cache/ms-playwright/ の chromium を docich の [browser] binary に
#     指定して共用する (docs/games/sorengame.md 参照)。

# --- ディレクトリ ---------------------------------------------------------
echo "==> ディレクトリ作成: games/roms/, run/"
mkdir -p "${REPO_ROOT}/games/roms"
mkdir -p "${REPO_ROOT}/run"

echo "=================================================================="
echo " セットアップ完了 (apt install --no-upgrade + mkdir のみ実行。"
echo " 既存サービス・設定 (soren 関連含む) は変更していません)。"
echo " 次のステップ: bin/docich doctor"
echo "=================================================================="
