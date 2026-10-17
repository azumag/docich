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
# xdpyinfo is not called by this script directly: `docich up` waits for the
# display through XKit.wait_display() -> xdpyinfo. Check it here so a missing
# binary fails as a dependency error instead of a traceback mid-run.
for b in Xvfb tmux ffmpeg xdotool xdpyinfo xterm nethack; do
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
log "依存コマンドの確認 OK (Xvfb tmux ffmpeg xdotool xdpyinfo xterm nethack)"

# --- 一時 config (soren :99 とも本番 docich :98 とも衝突しない :96 を使う) -----
TMP_DIR="$(mktemp -d)"
STREAM_FILE="${TMP_DIR}/out.flv"
CONFIG_PATH="${TMP_DIR}/docich.toml"
STATE_DIR="${TMP_DIR}/run"

# config/games/nethack.toml は tiles presentation を既定にしている。tiles は
# display viewport と Chromium のビューアを必要とし、隔離検証は
# nethack-tiles-gui-smoke.yml が担当する。このスクリプトが確かめるのは cli
# アダプタの tty 経路 (obs が text を返し、キー注入と pane 変化が見える経路)
# なので、game config の私用コピーで当該1行だけ tty に戻して実行する。
GAMES_DIR="${TMP_DIR}/games"
cp -R "${REPO_ROOT}/config/games" "${GAMES_DIR}"
python3 - "${GAMES_DIR}/nethack.toml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.is_file():
    section = False
    patched = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped == "[nethack.presentation]"
        patched.append('mode = "tty"' if section and stripped.startswith("mode") else line)
    path.write_text("\n".join(patched) + "\n", encoding="utf-8")
PY

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
games_dir = "${GAMES_DIR}"
roms_dir = "${REPO_ROOT}/games/roms"
EOF

export DOCICH_CONFIG="${CONFIG_PATH}"

cleanup() {
    "${DOCICH}" down >/dev/null 2>&1 || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

log "一時 config = ${CONFIG_PATH} (display :96, audio 無効, stream=file, nethack=tty)"

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
# 静止画面では x264 の映像出力がほぼ無くなり、muxer のバッファが flush される
# までファイルサイズが動かないことがある (隔離 CI の実測で 256KiB 単位の段階
# 増加を確認)。固定3秒だと「書き出しが続いている」ことの確認が flake になる
# ため、期限付きで増加を待つ。音声 (anullsrc) があるので必ず増加する。
SIZE_2="${SIZE_1}"
deadline=$(( $(date +%s) + 30 ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    sleep 2
    SIZE_2="$(stat -c '%s' "${STREAM_FILE}" 2>/dev/null || stat -f '%z' "${STREAM_FILE}")"
    if [ "${SIZE_2}" -gt "${SIZE_1}" ]; then
        break
    fi
done
if [ "${SIZE_2}" -le "${SIZE_1}" ]; then
    # 隔離環境では tmux の中身を見られるのが唯一の手掛かりなので、
    # 失敗時は stream window の末尾を残す (原因究明を別ターンに先送りしない)。
    log "stream が増えていないため診断を出力します (tmux windows / stream pane 末尾):"
    tmux list-windows -t docich 2>&1 || true
    tmux capture-pane -p -t docich:stream 2>&1 | tail -25 || true
    fail "stream ファイルのサイズが増加していません (${SIZE_1} -> ${SIZE_2}, 30秒待機後)"
fi
log "stream OK (${SIZE_1} -> ${SIZE_2} bytes)"

"${DOCICH}" switch nethack
log "switch OK (stop + start 経路)"

"${DOCICH}" down
echo "SMOKE PASS"
