#!/usr/bin/env bash
# 半熟英雄 brain の E2E スモークテスト (docs/hanjuku_brain.md §5.2)。
#
# 【警告】実行中の docich セッション (tmux session 'docich') を落とすので、
# 本番稼働中 (実配信中) の環境ではこのスクリプトを実行しないこと (smoke_cli.sh と同様)。
#
# 手順:
#   1. up (display :96 の起動確認)
#   2. obs hanjuku-hero (ROM 不要で観測 JSON が返ること)
#   3. fake brain (tests/fixtures/hanjuku_fake_brain.jsonl) に obs を流し、actions が
#      fixture と一致することを2回連続実行で確認する (fake_cursor が前進すること)
#   4. retroarch があれば ROM なしメニューを起動し、fake brain の出力を `send` で注入して
#      前後スクリーンショットの差分でキー入力の到達を確認する (無ければこの手順は SKIP)
#   5. down、Xvfb :96 が残存していないことを確認する
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
DOCICH="${REPO_ROOT}/bin/docich"
BRAIN_PY="${REPO_ROOT}/brains/hanjuku/brain.py"
FIXTURE="${REPO_ROOT}/tests/fixtures/hanjuku_fake_brain.jsonl"

log() { echo "smoke_brain: $*"; }
fail() { echo "smoke_brain: NG - $*" >&2; exit 1; }

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
for b in Xvfb tmux ffmpeg xdotool xdpyinfo; do
    if ! find_bin "$b"; then
        missing+=("$b")
    fi
done
if [ "${#missing[@]}" -ne 0 ]; then
    echo "smoke_brain: NG - 以下の依存コマンドが見つかりません:" >&2
    for m in "${missing[@]}"; do
        echo "  - $m" >&2
    done
    exit 1
fi
log "依存コマンドの確認 OK (Xvfb tmux ffmpeg xdotool xdpyinfo)"

[ -f "${BRAIN_PY}" ] || fail "brain.py が見つかりません: ${BRAIN_PY}"
[ -f "${FIXTURE}" ] || fail "fake fixture が見つかりません: ${FIXTURE}"

# --- 一時 config (soren :99 とも本番 docich :98 とも衝突しない :96 を使う) -----
TMP_DIR="$(mktemp -d)"
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
mode = "null"

[paths]
state_dir = "${STATE_DIR}"
games_dir = "${REPO_ROOT}/config/games"
roms_dir = "${REPO_ROOT}/games/roms"
EOF

log "一時 config = ${CONFIG_PATH} (display :96, audio 無効, stream=null)"

cleanup() {
    pkill -x retroarch >/dev/null 2>&1 || true
    "${DOCICH}" --config "${CONFIG_PATH}" down >/dev/null 2>&1 || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

# tmux session 'docich' は名前が固定 (docich.toml の設定に依らない) のため、共有環境で
# 他プロセスが残した停止しそこないの状態が残っていると `up` が「既に起動中」を誤検知しうる。
# `down` は何も無くても安全に呼べるので、開始前に一度掃除しておく (cleanup() と同じ操作)。
pkill -x retroarch >/dev/null 2>&1 || true
"${DOCICH}" --config "${CONFIG_PATH}" down >/dev/null 2>&1 || true

# brain の状態ディレクトリは cwd (repo root) の run/brain/hanjuku/ 固定で、
# docich.toml の [paths] には従わない (design doc §0)。既定 cursor の影響を
# 受けないよう、開始時にクリアしてよい (design doc の指示どおり)。
rm -rf "${REPO_ROOT}/run/brain/hanjuku"

# --- 1. up: display 起動確認 ------------------------------------------------
"${DOCICH}" --config "${CONFIG_PATH}" up
"${DOCICH}" --config "${CONFIG_PATH}" status
if ! DISPLAY=:96 xdpyinfo >/dev/null 2>&1; then
    fail "ディスプレイ :96 の起動を確認できませんでした"
fi
log "up OK (display :96 起動確認)"

# --- 2. obs hanjuku-hero (ROM 不要で成功すること) ----------------------------
OBS_JSON="${TMP_DIR}/obs.json"
"${DOCICH}" --config "${CONFIG_PATH}" obs hanjuku-hero > "${OBS_JSON}"
python3 -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    obs = json.load(fh)
kind = obs.get("kind")
screenshot = obs.get("screenshot")
if kind != "screenshot":
    sys.exit(f"kind が screenshot ではありません: {kind!r}")
if not screenshot:
    sys.exit("screenshot が空です")
print(f"smoke_brain: obs OK (kind=screenshot, screenshot={screenshot})")
' "${OBS_JSON}" || fail "obs hanjuku-hero の検証に失敗しました"

# --- 3. fake brain: 2回実行して fixture の1行目→2行目 (cursor 前進) を確認 -----
OUT1_JSON="${TMP_DIR}/out1.json"
OUT2_JSON="${TMP_DIR}/out2.json"

(
    cd "${REPO_ROOT}"
    DOCICH_BRAIN_LLM="fake:tests/fixtures/hanjuku_fake_brain.jsonl" \
        python3 brains/hanjuku/brain.py < "${OBS_JSON}" > "${OUT1_JSON}"
    DOCICH_BRAIN_LLM="fake:tests/fixtures/hanjuku_fake_brain.jsonl" \
        python3 brains/hanjuku/brain.py < "${OBS_JSON}" > "${OUT2_JSON}"
)

python3 -c '
import json
import sys

out1_path, out2_path, fixture_path = sys.argv[1:4]
with open(out1_path, encoding="utf-8") as fh:
    out1 = json.load(fh)
with open(out2_path, encoding="utf-8") as fh:
    out2 = json.load(fh)
with open(fixture_path, encoding="utf-8") as fh:
    fixture_lines = [json.loads(line) for line in fh if line.strip()]

expected1 = {"actions": fixture_lines[0]["actions"]}
expected2 = {"actions": fixture_lines[1]["actions"]}
if out1 != expected1:
    sys.exit(f"1回目の actions が fixture の1行目と一致しません: {out1} != {expected1}")
if out2 != expected2:
    sys.exit(f"2回目の actions が fixture の2行目と一致しません: {out2} != {expected2}")
print("smoke_brain: fake brain OK (1回目=down, 2回目=up。fake_cursor の前進を確認)")
' "${OUT1_JSON}" "${OUT2_JSON}" "${FIXTURE}" || fail "fake brain の出力検証に失敗しました"

# --- 4. retroarch があれば: 入力到達をスクリーンショット差分で確認 -------------
if find_bin retroarch && find_bin dbus-run-session; then
    RA_CFG="${TMP_DIR}/retroarch.cfg"
    cat > "${RA_CFG}" <<EOF
input_driver = "sdl2"
video_driver = "sdl2"
gamemode_enable = "false"
input_exit_emulator = "nul"
EOF

    DISPLAY=:96 dbus-run-session -- retroarch --config "${RA_CFG}" \
        >"${TMP_DIR}/retroarch.log" 2>&1 &
    RETROARCH_PID=$!
    log "retroarch を起動しました (pid=${RETROARCH_PID}, ROM なし=メニュー)"

    sleep 8

    WIN_ID="$(DISPLAY=:96 xdotool search --name RetroArch | head -n1 || true)"
    if [ -z "${WIN_ID}" ]; then
        fail "RetroArch ウィンドウが見つかりません (起動ログ: ${TMP_DIR}/retroarch.log)"
    fi
    log "RetroArch ウィンドウ確認 OK (id=${WIN_ID})"

    SNAP_A="${TMP_DIR}/snap_a.png"
    SNAP_B="${TMP_DIR}/snap_b.png"
    "${DOCICH}" --config "${CONFIG_PATH}" snap -o "${SNAP_A}"

    "${DOCICH}" --config "${CONFIG_PATH}" send hanjuku-hero "$(cat "${OUT1_JSON}")"
    sleep 1
    "${DOCICH}" --config "${CONFIG_PATH}" snap -o "${SNAP_B}"

    if cmp -s "${SNAP_A}" "${SNAP_B}"; then
        fail "スクリーンショットが変化していません (入力がRetroArchに届いていない可能性)"
    fi
    log "retroarch 入力到達 OK (送信前後のスクリーンショットが異なることを確認: メニューカーソル移動)"

    # 【重要】pkill -f は自分自身の shell コマンドラインにも一致して道連れで
    # 死ぬ既知の罠があるため、絶対に使わない。プロセス名の完全一致 (-x) のみを使う。
    pkill -x retroarch >/dev/null 2>&1 || true
    sleep 1
    log "retroarch 終了"
else
    log "retroarch (または dbus-run-session) が見つからないため手順4は SKIP します"
fi

# --- 5. down: Xvfb :96 が残存していないこと ----------------------------------
"${DOCICH}" --config "${CONFIG_PATH}" down
if pgrep -f "Xvfb :96" >/dev/null 2>&1; then
    fail "Xvfb :96 が残存しています"
fi
log "down OK (Xvfb :96 残存なし)"

echo "PASS"
