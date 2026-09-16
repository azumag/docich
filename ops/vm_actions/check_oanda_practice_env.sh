#!/usr/bin/env bash
set -euo pipefail

# Fail closed before the OANDA-practice read-only collector starts. Never print
# credential values or file contents. The file is deliberately operator-managed
# outside the repository/GitHub control plane.
file="${HOME}/.config/docich/oanda-practice.env"

fail() {
  echo "OANDA practice credential file failed safety checks" >&2
  exit 30
}

[[ -f "$file" && ! -L "$file" ]] || fail
[[ "$(stat -c '%u' "$file" 2>/dev/null || true)" == "$(id -u)" ]] || fail
[[ "$(stat -c '%a' "$file" 2>/dev/null || true)" == "600" ]] || fail

account_count=0
token_count=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || fail
  key="${line%%=*}"
  value="${line#*=}"
  case "$key" in
    DOCICH_OANDA_ACCOUNT_ID) ((account_count += 1)) ;;
    DOCICH_OANDA_TOKEN) ((token_count += 1)) ;;
    *) fail ;;
  esac
  [[ -n "$value" ]] || fail
done < "$file"

[[ "$account_count" -eq 1 && "$token_count" -eq 1 ]] || fail
