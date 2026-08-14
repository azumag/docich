#!/usr/bin/env bash
#
# publish_wiki.sh — docich wiki を GitHub wiki (docich.wiki.git) へミラー発行する。
#
# wiki ページの原稿は本リポジトリの wiki/ ディレクトリで管理する (バージョン管理・
# レビュー可能・陳腐化防止のため)。本スクリプトは wiki/*.md を GitHub wiki リポジトリへ
# 一方向にミラーする発行コマンドである (repo -> GitHub wiki の片道のみ。逆方向の同期はしない)。
#
# 【重要】完全ミラーである。GitHub Wiki 側 (Web UI 等) を直接編集していても、
# 次回このスクリプトを実行した時点でその内容は削除・上書きされる。
# wiki の編集は必ずこのリポジトリの wiki/*.md に対して行うこと。
#
# 実行環境: GitHub への認証 (SSH 鍵 or HTTPS 資格情報) があるマシン (手元 PC や VM) で
# 実行する想定。このリポジトリ (docich) は非公開のため、docich.wiki.git への push には
# 対応する権限が必要。ネットワークアクセスの無い環境では実行できない。
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
WIKI_SRC_DIR="${REPO_ROOT}/wiki"

# env WIKI_URL で差し替え可 (例: SSH 鍵が無いマシンでは https://github.com/azumag/docich.wiki.git)。
WIKI_URL="${WIKI_URL:-git@github.com:azumag/docich.wiki.git}"

usage() {
  cat <<'EOF'
使い方:
  scripts/publish_wiki.sh              wiki/ の内容を GitHub wiki へ発行する (clone -> ミラー -> commit -> push)
  scripts/publish_wiki.sh --dry-run    push せず、発行される差分 (git diff) を表示するだけ
  scripts/publish_wiki.sh -h|--help    このヘルプを表示する

環境変数:
  WIKI_URL   発行先の wiki リポジトリ URL
             既定: git@github.com:azumag/docich.wiki.git
             (SSH 認証が無い場合は https://github.com/azumag/docich.wiki.git を指定する)

注意:
  完全ミラー発行である。GitHub Wiki 側を直接編集していても、次回実行時に
  上書き・削除される。編集は必ずこのリポジトリの wiki/*.md に対して行うこと。
EOF
}

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "publish_wiki: 未知のオプションです: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

log() { echo "publish_wiki: $*"; }
fail() { echo "publish_wiki: エラー: $*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git が見つかりません"

if [ ! -d "${WIKI_SRC_DIR}" ]; then
  fail "wiki/ ディレクトリが見つかりません (${WIKI_SRC_DIR})"
fi
if ! compgen -G "${WIKI_SRC_DIR}/*.md" >/dev/null; then
  fail "wiki/ に *.md がありません (${WIKI_SRC_DIR})"
fi

# --- 1. 一時ディレクトリへ clone ------------------------------------------------
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

log "clone 元: ${WIKI_URL}"
log "clone 先 (一時): ${TMP_DIR}"
if ! clone_output="$(git clone --quiet "${WIKI_URL}" "${TMP_DIR}" 2>&1)"; then
  echo "${clone_output}" >&2
  echo "" >&2
  echo "publish_wiki: エラー: wiki の clone に失敗しました (${WIKI_URL})。" >&2
  echo "  wiki が未初期化の可能性があります。" >&2
  echo "  GitHub の Wiki タブで最初のページを一度作成してから再実行してください" >&2
  echo "  (ページが 1 つも無い wiki リポジトリは git clone できません)。" >&2
  exit 1
fi

# --- 2. 完全ミラー: clone 内の *.md を全削除してから repo の wiki/*.md をコピー ---
# (GitHub Wiki 側を直接編集していた場合、その内容はここで失われる。上部の注意書き参照)
find "${TMP_DIR}" -maxdepth 1 -type f -name '*.md' -delete
cp "${WIKI_SRC_DIR}"/*.md "${TMP_DIR}/"

# --- 3. 差分確認 -> commit -> push ----------------------------------------------
cd "${TMP_DIR}"
git add -A

if git diff --cached --quiet; then
  log "変更なし。発行をスキップします。"
  exit 0
fi

if [ "${DRY_RUN}" -eq 1 ]; then
  log "--dry-run: push は行いません。発行される差分:"
  git diff --cached
  exit 0
fi

HEAD_SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
git commit --quiet -m "Publish wiki from repo wiki/ (${HEAD_SHA})"
git push --quiet origin HEAD

log "発行しました (repo HEAD: ${HEAD_SHA})。"

# --- 4. 後始末 (trap で一時ディレクトリを削除) -----------------------------------
# cleanup() は trap 経由でスクリプト終了時に必ず実行される (上記参照)。
