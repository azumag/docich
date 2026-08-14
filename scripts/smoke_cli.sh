#!/usr/bin/env bash
# docich E2E スモークテスト。
#
# 【警告】実行中の docich セッション (tmux session 'docich') を落とすので、
# 本番稼働中 (実配信中) の環境ではこのスクリプトを実行しないこと。
#
# コンテナ/実機のどちらでも、依存コマンドさえ揃っていれば動く。Xvfb + nethack
# (cli アダプタ) を使い、起動・観測・入力注入・スクリーンショット・配信 (ファイル
# 出力)・ゲーム切替までの一連の流れを検証する (docs/architecture.md SS10 完了条件 b)。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
DOCICH="${REPO_ROOT}/bin/docich"

log() { echo "smoke_cli: $*"; }
fail() { echo "smoke_cli: NG - $*" >&2; exit 1; }

# --- 依存チェック (procs.py と同じく /usr/games もフォールバックで見る) -------
find_bin() {
    local name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        return 0
    fi
    for dir in /usr/games /usr/local/games; do
        if [ -x "${dir}/${name}" ]; then
            return 0
        fi
    done
    return 1
}

missing=()
for b in Xvfb tmux ffmpeg xdotool xterm nethack; do
    if ! find_bin "$b"; then
        missing+=("$b")
    fi
done

if [ "${#missing[@]}" -ne 0 ]; then
    echo "smoke_cli: NG - 以下の依存コマンドが見つかりません:" >&2
    for m in "${missing[@]}"; do
        echo "  - $m" >&2
    done
    exit 1
fi
log "依存コマンドの確認 OK (Xvfb tmux ffmpeg xdotool xterm nethack)"

# --- 一時 config (soren :99 とも本番 docich :98 とも衝突しない :96 を使う) -----
TMP_DIR="$(mktemp -d)"
STREAM_FILE="${TMP_DIR}/out.flv"
CONFIG_PATH="${TMP_DIR}/docich.toml"
STATE_DIR="${TMP_DIR}/run"

cat > "${CONFIG_PATH}" <<EOF
[display]
number = 96
width = 1280
height = 720

[audio]
enabled = false

[stream]
mode = "file"
file_path = "${STREAM_FILE}"
preset = "ultrafast"
video_bitrate = "800k"
maxrate = "1000k"
bufsize = "1600k"

[paths]
state_dir = "${STATE_DIR}"
games_dir = "${REPO_ROOT}/config/games"
roms_dir = "${REPO_ROOT}/games/roms"
EOF

export DOCICH_CONFIG="${CONFIG_PATH}"

cleanup() {
    "${DOCICH}" down >/dev/null 2>&1 || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

log "一時 config = ${CONFIG_PATH} (display :96, audio 無効, stream=file)"

# --- 実行 -------------------------------------------------------------------
"${DOCICH}" up
"${DOCICH}" status

"${DOCICH}" start nethack
sleep 4

OBS_JSON="$("${DOCICH}" obs nethack)"
echo "${OBS_JSON}" | python3 -c '
import json
import sys

obs = json.load(sys.stdin)
kind = obs.get("kind")
text = obs.get("text")
if kind != "text":
    sys.exit(f"kind が text ではありません: {kind!r}")
if not text:
    sys.exit("text が空です")
print(f"smoke_cli: obs OK (kind=text, text 長={len(text)})")
' || fail "obs nethack の検証に失敗しました"

"${DOCICH}" send nethack '{"type":"text","text":"j"}'
log "send OK"

SNAP_PATH="${TMP_DIR}/snap.png"
"${DOCICH}" snap -o "${SNAP_PATH}"
[ -f "${SNAP_PATH}" ] || fail "スクリーンショットが作成されていません (${SNAP_PATH})"
SNAP_SIZE="$(stat -c '%s' "${SNAP_PATH}" 2>/dev/null || stat -f '%z' "${SNAP_PATH}")"
[ "${SNAP_SIZE}" -ge 5120 ] || fail "スクリーンショットが小さすぎます (${SNAP_SIZE} bytes < 5120)"
log "snap OK (${SNAP_SIZE} bytes)"

# ffmpeg (file stream) が書き出しを始めるまでの猶予
sleep 2
[ -f "${STREAM_FILE}" ] || fail "stream ファイルが作成されていません (${STREAM_FILE})"
SIZE_1="$(stat -c '%s' "${STREAM_FILE}" 2>/dev/null || stat -f '%z' "${STREAM_FILE}")"
sleep 3
SIZE_2="$(stat -c '%s' "${STREAM_FILE}" 2>/dev/null || stat -f '%z' "${STREAM_FILE}")"
[ "${SIZE_2}" -gt "${SIZE_1}" ] || fail "stream ファイルのサイズが増加していません (${SIZE_1} -> ${SIZE_2})"
log "stream OK (${SIZE_1} -> ${SIZE_2} bytes)"

"${DOCICH}" switch nethack
log "switch OK (stop + start 経路)"

"${DOCICH}" down
echo "SMOKE PASS"
