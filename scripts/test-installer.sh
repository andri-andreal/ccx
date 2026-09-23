#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ccx-installer-test.XXXXXX")"
trap 'rm -rf -- "$TEST_TMP"' EXIT

case "$(uname -s)" in
  Linux) test_os="linux" ;;
  Darwin) test_os="macos" ;;
  *) printf 'installer test skipped on unsupported shell platform\n'; exit 0 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) test_arch="x86_64" ;;
  arm64|aarch64) test_arch="aarch64" ;;
  *) printf 'installer test skipped on unsupported architecture\n'; exit 0 ;;
esac

release_dir="$TEST_TMP/releases/v0.1.0"
asset="ccx-router-$test_os-$test_arch"
mkdir -p "$release_dir" "$TEST_TMP/home" "$TEST_TMP/config"
printf '#!/usr/bin/env sh\nprintf "fixture router\\n"\n' > "$release_dir/$asset"
chmod +x "$release_dir/$asset"

if command -v sha256sum >/dev/null 2>&1; then
  hash="$(sha256sum "$release_dir/$asset" | awk '{print $1}')"
else
  hash="$(shasum -a 256 "$release_dir/$asset" | awk '{print $1}')"
fi
printf '%s  %s\n' "$hash" "$asset" > "$release_dir/SHA256SUMS"

HOME="$TEST_TMP/home" \
XDG_CONFIG_HOME="$TEST_TMP/config" \
CCX_RELEASE_BASE_URL="file://$TEST_TMP/releases" \
CCX_RELEASE_VERSION="v0.1.0" \
CCX_ROUTER_INSTALL=download \
  bash "$ROOT/install.sh" >/dev/null

installed="$TEST_TMP/home/.local/bin/ccx-router"
[ -x "$installed" ] || { printf 'installer did not create %s\n' "$installed" >&2; exit 1; }
[ "$("$installed")" = "fixture router" ] || { printf 'installed router is not the verified fixture\n' >&2; exit 1; }
[ -L "$TEST_TMP/home/.local/bin/ccx" ] || { printf 'installer did not link the ccx CLI\n' >&2; exit 1; }
[ -f "$TEST_TMP/config/ccx/providers/openai.tmpl" ] || { printf 'provider templates were not installed\n' >&2; exit 1; }

# Auto mode must fall back to a local Cargo build when no release asset exists.
# A tiny fake Cargo keeps this test offline and proves the destination contract.
mkdir -p "$TEST_TMP/fake-bin" "$TEST_TMP/fallback-home" "$TEST_TMP/fallback-config"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'mkdir -p "$CARGO_TARGET_DIR/release"' \
  'printf '\''#!/usr/bin/env sh\nprintf "fallback router\\n"\n'\'' > "$CARGO_TARGET_DIR/release/ccx-router"' \
  'chmod +x "$CARGO_TARGET_DIR/release/ccx-router"' \
  > "$TEST_TMP/fake-bin/cargo"
chmod +x "$TEST_TMP/fake-bin/cargo"
HOME="$TEST_TMP/fallback-home" \
XDG_CONFIG_HOME="$TEST_TMP/fallback-config" \
PATH="$TEST_TMP/fake-bin:$PATH" \
CCX_RELEASE_BASE_URL="file://$TEST_TMP/missing-releases" \
CCX_RELEASE_VERSION="v0.1.0" \
CCX_ROUTER_BUILD_DIR="$TEST_TMP/fake-target" \
CCX_ROUTER_INSTALL=auto \
  bash "$ROOT/install.sh" >/dev/null 2>&1
[ "$("$TEST_TMP/fallback-home/.local/bin/ccx-router")" = "fallback router" ] || {
  printf 'installer did not fall back to a local router build\n' >&2
  exit 1
}

# A changed payload must never pass verification in forced-download mode.
printf 'tampered\n' >> "$release_dir/$asset"
if HOME="$TEST_TMP/home" \
  XDG_CONFIG_HOME="$TEST_TMP/config" \
  CCX_RELEASE_BASE_URL="file://$TEST_TMP/releases" \
  CCX_RELEASE_VERSION="v0.1.0" \
  CCX_ROUTER_INSTALL=download \
  bash "$ROOT/install.sh" >/dev/null 2>&1; then
  printf 'installer accepted an asset with a mismatched checksum\n' >&2
  exit 1
fi

printf 'installer download, checksum, and fallback tests passed\n'
