#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ffmpeg_bin="${DOCICH_CC_FFMPEG_BIN:-/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg}"
output="${1:-/tmp/docich-cc-stress.ts}"

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

runtime_dir="$(mktemp -d /tmp/docich-cc-stress.XXXXXX)"
socket_path="$runtime_dir/cc.sock"
log_file="$runtime_dir/ffmpeg.log"
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

"$ffmpeg_bin" -hide_banner -loglevel warning -re \
	-f lavfi -i "testsrc2=size=640x360:rate=30" \
	-t 15 -vf "docichcc=socket=$socket_path" \
	-c:v libx264 -preset ultrafast -pix_fmt yuv420p -a53cc 1 \
	-an -f mpegts -y "$output" >"$log_file" 2>&1 &
ffmpeg_pid="$!"

for _attempt in $(seq 1 100); do
	[[ -S "$socket_path" ]] && break
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

python3 "$repo_root/native/ffmpeg/stress_client.py" \
	--socket "$socket_path" --count 20 --max-p95-ms 500

wait "$ffmpeg_pid"
ffmpeg_pid=""
python3 "$repo_root/native/ffmpeg/verify_a53_sei.py" "$output" --minimum 20
"$caption_decoder" "$output" >"$decoded_file"
grep -Fq 'Caption cycle 01 synchronized.' "$decoded_file"
grep -Fq 'Caption cycle 20 synchronized.' "$decoded_file"
echo '{"decodedCycles":{"first":true,"last":true},"verified":true}'
