#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-}"

if [[ ! "$TAG" =~ ^v([0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?)$ ]]; then
  printf 'release tag must look like v1.2.3 (got %s)\n' "${TAG:-<empty>}" >&2
  exit 2
fi

expected="${BASH_REMATCH[1]}"
cli_version="$(sed -n 's/^CCX_VERSION="\([^"]*\)".*/\1/p' "$ROOT/bin/ccx" | head -n 1)"
router_version="$(awk '
  /^\[package\]$/ { package = 1; next }
  /^\[/ { package = 0 }
  package && /^version[[:space:]]*=/ {
    value = $0
    sub(/^[^=]*=[[:space:]]*"/, "", value)
    sub(/".*/, "", value)
    print value
    exit
  }
' "$ROOT/router/Cargo.toml")"
gui_version="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$ROOT/gui/src-tauri/tauri.conf.json" | head -n 1)"

failed=0
for component in cli router gui; do
  case "$component" in
    cli) actual="$cli_version" ;;
    router) actual="$router_version" ;;
    gui) actual="$gui_version" ;;
  esac
  if [ "$actual" != "$expected" ]; then
    printf '%s version is %s, expected %s for tag %s\n' "$component" "${actual:-<missing>}" "$expected" "$TAG" >&2
    failed=1
  fi
done

[ "$failed" -eq 0 ] || exit 1
printf 'release versions match %s\n' "$TAG"
