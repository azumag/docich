#!/usr/bin/env bash
set -euo pipefail

# Fail-closed preflight for the operator-managed Moomoo OpenD runtime.
#
# This helper deliberately does not read or print the OpenD configuration.
# The binary, Appdata.dat and config live outside the repository and are
# provisioned by the operator.  The config may contain login/account material,
# so it must be owned by the service user and inaccessible to group/others.
#
# Usage:
#   check_moomoo_opend_runtime.sh <docich-root>

root="${1:-}"
[[ -n "$root" && -d "$root" ]] || exit 30

home="${HOME:-}"
[[ -n "$home" && -d "$home" ]] || exit 30

runtime_dir="$home/.local/share/docich/moomoo-opend"
binary="$runtime_dir/OpenD"
appdata="$runtime_dir/Appdata.dat"
config="$home/.config/docich/moomoo-opend/OpenD.xml"
python_bin="$root/.venv-trading/bin/python3"

[[ -f "$binary" && ! -L "$binary" && -x "$binary" ]] || exit 31
[[ -f "$appdata" && ! -L "$appdata" && -s "$appdata" ]] || exit 32
[[ -f "$config" && ! -L "$config" && -s "$config" ]] || exit 33
[[ "$(stat -c '%u' "$config")" == "$(id -u)" ]] || exit 34
case "$(stat -c '%a' "$config")" in
  400|600) ;;
  *) exit 35 ;;
esac

# The collector and the readiness handshake use the separately pinned quote SDK.
[[ -x "$python_bin" ]] || exit 36
"$python_bin" -c 'import moomoo' >/dev/null 2>&1 || exit 37

exit 0
