#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_root="${1:-${DOCICH_CC_BUILD_ROOT:-/tmp/docich-cc-build}}"
ffmpeg_source="$build_root/ffmpeg-src"
caption_source="$build_root/libcaption-src"
caption_prefix="$build_root/libcaption-install"
ffmpeg_prefix="$build_root/ffmpeg-install"

ffmpeg_commit="e38092ef9395d7049f871ef4d5411eb410e283e0"
caption_commit="e8b6261090eb3f2012427cc6b151c923f82453db"

build_arch="$(uname -m)"
if [[ -n "${DOCICH_FFMPEG_EXPECT_ARCH:-}" &&
      "$build_arch" != "$DOCICH_FFMPEG_EXPECT_ARCH" ]]; then
  echo "build architecture is $build_arch; expected $DOCICH_FFMPEG_EXPECT_ARCH" >&2
  exit 2
fi

if [[ "$build_root" != /* || "$build_root" == "/" ]]; then
  echo "build root must be an absolute, non-root path" >&2
  exit 2
fi

mkdir -p "$build_root"

clone_and_pin() {
  local url="$1"
  local tag="$2"
  local commit="$3"
  local destination="$4"
  if [[ ! -d "$destination/.git" ]]; then
    git clone --depth 1 --branch "$tag" "$url" "$destination"
  fi
  local actual
  actual="$(git -C "$destination" rev-parse HEAD)"
  if [[ "$actual" != "$commit" ]]; then
    echo "$destination is at $actual; expected pinned commit $commit" >&2
    exit 2
  fi
}

clone_and_pin "https://github.com/FFmpeg/FFmpeg.git" "n6.1.1" "$ffmpeg_commit" "$ffmpeg_source"
clone_and_pin "https://github.com/szatmary/libcaption.git" "v0.8" "$caption_commit" "$caption_source"

cmake -S "$caption_source" -B "$build_root/libcaption-build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DBUILD_EXAMPLES=OFF \
  -DCMAKE_INSTALL_PREFIX="$caption_prefix"
cmake --build "$build_root/libcaption-build" --target install --parallel

# Build only the pinned transport-stream decoder needed by the local proof.
# It is verification tooling; the streaming runtime never invokes it.
install -d "$caption_prefix/bin"
caption_cc="${CC:-cc}"
"$caption_cc" -O2 \
  -I"$caption_source/caption" -I"$caption_source/examples" \
  "$caption_source/examples/ts2srt.c" "$caption_source/examples/ts.c" \
  "$caption_prefix/lib/libcaption.a" -lm \
  -o "$caption_prefix/bin/ts2srt"

cp "$repo_root/native/ffmpeg/vf_docichcc.c" "$ffmpeg_source/libavfilter/vf_docichcc.c"
if ! grep -q 'ff_vf_docichcc' "$ffmpeg_source/libavfilter/allfilters.c"; then
  git -C "$ffmpeg_source" apply "$repo_root/native/ffmpeg/patches/0001-add-docichcc-filter.patch"
fi

if command -v nproc >/dev/null 2>&1; then
  build_jobs="$(nproc)"
else
  build_jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 2)"
fi

extra_configure=()
if [[ -n "${DOCICH_FFMPEG_CONFIGURE_ARGS:-}" ]]; then
  # This variable is operator-owned build configuration, not user/model text.
  read -r -a extra_configure <<<"$DOCICH_FFMPEG_CONFIGURE_ARGS"
fi

(
  cd "$ffmpeg_source"
  PKG_CONFIG_PATH="$caption_prefix/lib/pkgconfig:${PKG_CONFIG_PATH:-}" \
    ./configure \
      --prefix="$ffmpeg_prefix" \
      --disable-debug \
      --disable-doc \
      --enable-gpl \
      --enable-libx264 \
      --extra-cflags="-I$caption_prefix/include" \
      --extra-ldflags="-L$caption_prefix/lib" \
      --extra-libs=-lcaption \
      "${extra_configure[@]}"
  make -j"$build_jobs"
  make install
)

install -d "$ffmpeg_prefix/share/doc/docich-ffmpeg"
install -m 0644 \
  "$repo_root/native/ffmpeg/THIRD_PARTY_NOTICES.md" \
  "$ffmpeg_prefix/share/doc/docich-ffmpeg/THIRD_PARTY_NOTICES.md"

"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -filters | grep ' docichcc '
"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -devices | grep -E '^ D.* x11grab[[:space:]]'
"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -devices | grep -E '^ D.* pulse[[:space:]]'
"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -encoders | grep -E '^ V.* libx264[[:space:]]'
"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -h encoder=libx264 | grep -E '(^|[[:space:]])-?a53cc([[:space:]]|$)'
"$ffmpeg_prefix/bin/ffmpeg" -hide_banner -protocols | grep -x '  rtmp'
test -x "$caption_prefix/bin/ts2srt"
echo "architecture=$build_arch" >&2
echo "$ffmpeg_prefix/bin/ffmpeg"
