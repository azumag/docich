#!/usr/bin/env bash
# CI-only installer. Never use it on the production VM or a developer daemon.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_ENVIRONMENT:-}" != "github-hosted" || "${RUNNER_OS:-}" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  printf '%s\n' 'This installer is restricted to an ephemeral GitHub-hosted Linux x86_64 runner.' >&2
  exit 1
fi

# https://github.com/google/gvisor/releases/tag/release-20260907.0
# Official SHA256SUMS verified on 2026-09-12. Keep the complete gvisor-bin/
# distribution alongside runsc; standalone runsc installations are obsolete.
release='release-20260907.0'
archive='gvisor-x86_64.tar.bz2'
expected='81416511897ab8abd4e723d66823c5b0461a2ee3311cfa70d152404ef9b860cf'
base="https://github.com/google/gvisor/releases/download/${release}"
gvisor_ci_tmp="$(mktemp -d "${RUNNER_TEMP:?}/docich-gvisor.XXXXXX")"
trap 'rm -rf "$gvisor_ci_tmp"' EXIT

curl --fail --silent --show-error --location --retry 3 --max-time 180 \
  "${base}/${archive}" -o "${gvisor_ci_tmp}/${archive}"
curl --fail --silent --show-error --location --retry 3 --max-time 30 \
  "${base}/SHA256SUMS" -o "${gvisor_ci_tmp}/SHA256SUMS"

python3 - "$gvisor_ci_tmp" "$archive" "$expected" <<'PY'
import hashlib
from pathlib import Path
import sys

directory, archive, expected = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
matches = [line.split()[0] for line in (directory / "SHA256SUMS").read_text().splitlines()
           if len(line.split()) == 2 and line.split()[1] == archive]
if matches != [expected]:
    raise SystemExit("Official release checksum differs from the reviewed pin")
with (directory / archive).open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != expected:
    raise SystemExit("Downloaded gVisor archive failed SHA-256 verification")
PY

mkdir "${gvisor_ci_tmp}/distribution"
tar -xjf "${gvisor_ci_tmp}/${archive}" -C "${gvisor_ci_tmp}/distribution"
test -x "${gvisor_ci_tmp}/distribution/runsc"
test -d "${gvisor_ci_tmp}/distribution/gvisor-bin"
sudo cp -a "${gvisor_ci_tmp}/distribution/." /usr/local/bin/
sudo /usr/local/bin/runsc install
sudo systemctl restart docker
/usr/local/bin/runsc --version
docker info --format '{{json .Runtimes}}' | python3 -c \
  'import json,sys; assert "runsc" in json.load(sys.stdin), "gVisor runtime was not registered"'
