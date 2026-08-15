#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ffmpeg_bin="${DOCICH_CC_FFMPEG_BIN:-/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg}"
output="${1:-/tmp/docich-cc-poc.ts}"

if [[ ! -x "$ffmpeg_bin" ]]; then
  echo "caption-enabled FFmpeg not found: $ffmpeg_bin" >&2
  exit 2
fi
ffmpeg_prefix="$(cd "$(dirname "$ffmpeg_bin")/.." && pwd)"
caption_decoder="${DOCICH_CC_TS2SRT_BIN:-$(dirname "$ffmpeg_prefix")/libcaption-install/bin/ts2srt}"
if [[ ! -x "$caption_decoder" ]]; then
  echo "caption decoder not found: $caption_decoder" >&2
  exit 2
fi

runtime_dir="$(mktemp -d /tmp/docich-cc-poc.XXXXXX)"
socket_path="$runtime_dir/cc.sock"
chunks_file="$runtime_dir/chunks.txt"
translations_file="$runtime_dir/translations.json"
plan_file="$runtime_dir/plan.json"
log_file="$runtime_dir/ffmpeg.log"
collision_log="$runtime_dir/collision.log"
decoded_file="$runtime_dir/captions.srt"
ffmpeg_pid=""

cleanup() {
  if [[ -n "$ffmpeg_pid" ]] && kill -0 "$ffmpeg_pid" 2>/dev/null; then
    kill -TERM "$ffmpeg_pid" 2>/dev/null || true
    wait "$ffmpeg_pid" 2>/dev/null || true
  fi
  rm -rf "$runtime_dir"
}
trap cleanup EXIT INT TERM

# Leave behind an owned but unbound socket and prove the filter replaces only
# this stale case.  The active-socket case is checked after FFmpeg starts.
python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.close()' "$socket_path"
[[ -S "$socket_path" ]]

printf '%s\n' '固定字幕の動作確認です。' >"$chunks_file"
printf '%s\n' '["Fixed caption proof of concept."]' >"$translations_file"
"$repo_root/bin/docich" caption plan \
  --chunks-file "$chunks_file" \
  --translations-file "$translations_file" \
  --execution-id poc-fixed-english \
  --output "$plan_file"

"$ffmpeg_bin" -hide_banner -loglevel info -re \
  -f lavfi -i "testsrc2=size=640x360:rate=30" \
  -t 5 \
  -vf "docichcc=socket=$socket_path" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -a53cc 1 \
  -an -f mpegts -y "$output" >"$log_file" 2>&1 &
ffmpeg_pid="$!"

for _attempt in $(seq 1 100); do
  if [[ -S "$socket_path" ]] && grep -q 'docichcc listening on' "$log_file"; then
    break
  fi
  kill -0 "$ffmpeg_pid" 2>/dev/null || {
    sed -n '1,240p' "$log_file" >&2
    exit 1
  }
  sleep 0.05
done
[[ -S "$socket_path" ]] || {
  sed -n '1,240p' "$log_file" >&2
  echo "caption socket did not become ready" >&2
	exit 1
}

# A concurrent/replacement FFmpeg must not unlink the live owner's socket.
"$ffmpeg_bin" -hide_banner -loglevel info \
  -f lavfi -i "testsrc2=size=64x64:rate=30" -frames:v 2 \
  -vf "docichcc=socket=$socket_path" -f null - \
  >"$collision_log" 2>&1
grep -q 'refusing to replace active socket path' "$collision_log"
[[ -S "$socket_path" ]] || {
  sed -n '1,160p' "$collision_log" >&2
  echo "live caption socket was removed by a second FFmpeg" >&2
  exit 1
}

"$repo_root/bin/docich" caption send prepare \
  --socket "$socket_path" --plan "$plan_file"
"$repo_root/bin/docich" caption send commit \
  --socket "$socket_path" --plan "$plan_file"
# A late completion from an older speech must not erase the active caption.
if "$repo_root/bin/docich" caption send clear \
  --socket "$socket_path" --execution-id poc-stale --timeout 1 \
  >>"$log_file" 2>&1; then
	echo "stale execution clear was unexpectedly accepted" >&2
	exit 1
fi
sleep 1
"$repo_root/bin/docich" caption send clear \
  --socket "$socket_path" --plan "$plan_file"

wait "$ffmpeg_pid"
ffmpeg_pid=""
python3 "$repo_root/native/ffmpeg/verify_a53_sei.py" "$output"
"$caption_decoder" "$output" >"$decoded_file"
grep -Fq 'Fixed caption proof of concept.' "$decoded_file"
echo '{"decodedCaption":"Fixed caption proof of concept.","verified":true}'
