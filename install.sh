#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
BINDIR="${CCX_BIN_DIR:-$HOME/.local/bin}"
CFG="${CCX_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/ccx}"

# Release downloads are intentionally configurable so mirrors and offline tests
# do not need to impersonate github.com. CCX_RELEASE_BASE_URL must be the
# directory that contains version/tag directories.
CCX_RELEASE_REPOSITORY="${CCX_RELEASE_REPOSITORY:-andri-andreal/ccx}"
CCX_RELEASE_BASE_URL="${CCX_RELEASE_BASE_URL:-https://github.com/$CCX_RELEASE_REPOSITORY/releases/download}"
CCX_ROUTER_INSTALL="${CCX_ROUTER_INSTALL:-auto}"
CCX_VERSION="$(sed -n 's/^CCX_VERSION="\([^"]*\)".*/\1/p' "$REPO/bin/ccx" | head -n 1)"
CCX_VERSION="${CCX_VERSION:-0.1.0}"
CCX_RELEASE_VERSION="${CCX_RELEASE_VERSION:-v$CCX_VERSION}"
INSTALL_TMP=""

cleanup() {
  if [ -n "$INSTALL_TMP" ] && [ -d "$INSTALL_TMP" ]; then
    rm -rf -- "$INSTALL_TMP"
  fi
}
trap cleanup EXIT

warn() { printf 'warning: %s\n' "$*" >&2; }

download() { # url destination
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --retry 3 --output "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --tries=3 --output-document="$2" "$1"
  else
    warn "curl or wget is required to download a prebuilt ccx-router"
    return 1
  fi
}

sha256_file() { # file
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    warn "no SHA-256 tool found (need sha256sum, shasum, or openssl)"
    return 1
  fi
}

platform_asset() {
  local os arch ext=""
  case "$(uname -s 2>/dev/null || true)" in
    Linux) os="linux" ;;
    Darwin) os="macos" ;;
    MINGW*|MSYS*|CYGWIN*) os="windows"; ext=".exe" ;;
    *) return 1 ;;
  esac
  case "$(uname -m 2>/dev/null || true)" in
    x86_64|amd64|AMD64) arch="x86_64" ;;
    arm64|aarch64|ARM64) arch="aarch64" ;;
    *) return 1 ;;
  esac
  printf 'ccx-router-%s-%s%s' "$os" "$arch" "$ext"
}

install_binary() { # source destination
  if command -v install >/dev/null 2>&1; then
    install -m 0755 "$1" "$2"
  else
    cp "$1" "$2"
    chmod 0755 "$2"
  fi
}

download_router() {
  local asset release_url checksums expected actual destination installed_name="ccx-router"
  if ! asset="$(platform_asset)"; then
    warn "no prebuilt ccx-router is published for $(uname -s 2>/dev/null || printf unknown)/$(uname -m 2>/dev/null || printf unknown)"
    return 1
  fi

  if ! INSTALL_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ccx-install.XXXXXX")"; then
    warn "could not create a temporary download directory"
    return 1
  fi
  release_url="${CCX_RELEASE_BASE_URL%/}/$CCX_RELEASE_VERSION"
  checksums="$INSTALL_TMP/SHA256SUMS"
  destination="$INSTALL_TMP/$asset"

  printf '\nDownloading ccx-router %s (%s)...\n' "$CCX_RELEASE_VERSION" "$asset"
  if ! download "$release_url/SHA256SUMS" "$checksums"; then
    warn "could not download checksums from $release_url"
    return 1
  fi
  if ! download "$release_url/$asset" "$destination"; then
    warn "could not download $asset from $release_url"
    return 1
  fi

  expected="$(awk -v name="$asset" '$2 == name || $2 == "*" name { print $1; exit }' "$checksums")"
  if ! printf '%s' "$expected" | grep -Eq '^[0-9a-fA-F]{64}$'; then
    warn "SHA256SUMS does not contain a valid checksum for $asset"
    return 1
  fi
  if ! actual="$(sha256_file "$destination")"; then
    return 1
  fi
  if [ "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" != "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" ]; then
    warn "checksum mismatch for $asset; refusing to install it"
    return 1
  fi

  [[ "$asset" == *.exe ]] && installed_name="ccx-router.exe"
  if ! install_binary "$destination" "$BINDIR/$installed_name"; then
    warn "could not install ccx-router into $BINDIR"
    return 1
  fi
  printf 'Installed ccx-router -> %s\n' "$BINDIR/$installed_name"
}

build_router() {
  local binary="ccx-router"
  if ! command -v cargo >/dev/null 2>&1; then
    warn "cargo is not installed, so ccx-router cannot be built locally"
    return 1
  fi
  case "$(uname -s 2>/dev/null || true)" in MINGW*|MSYS*|CYGWIN*) binary="ccx-router.exe" ;; esac

  printf '\nBuilding ccx-router from source...\n'
  local build_dir="${CCX_ROUTER_BUILD_DIR:-$REPO/router/target}"
  if ! CARGO_TARGET_DIR="$build_dir" cargo build --locked --release --quiet --manifest-path "$REPO/router/Cargo.toml"; then
    warn "ccx-router build failed"
    return 1
  fi
  if ! install_binary "$build_dir/release/$binary" "$BINDIR/$binary"; then
    warn "could not install the locally built ccx-router into $BINDIR"
    return 1
  fi
  printf 'Installed ccx-router -> %s\n' "$BINDIR/$binary"
}

mkdir -p "$BINDIR" "$CFG/providers" "$CFG/profiles"
ln -sf "$REPO/bin/ccx" "$BINDIR/ccx"
chmod +x "$REPO/bin/ccx"
# Refresh the bundled provider templates so updates (e.g. the ccr -> ccx-router
# migration, new providers) propagate on re-install. Only the templates ccx
# ships are overwritten; any custom-named templates you added are left alone.
for t in "$REPO"/providers/*.tmpl; do
  [ -e "$t" ] || continue
  cp "$t" "$CFG/providers/" 2>/dev/null || true
done
chmod 700 "$CFG" "$CFG/profiles"

printf 'Installed ccx -> %s\n' "$BINDIR/ccx"

# auto: download the matching verified release and fall back to a local build.
# download/build: force one strategy and fail if it cannot complete.
# skip: install the direct Anthropic-compatible launcher only.
case "$CCX_ROUTER_INSTALL" in
  auto)
    if ! download_router; then
      warn "prebuilt router unavailable; falling back to a local Rust build"
      if ! build_router; then
        warn "ccx-router was not installed; OpenAI-compatible providers will be unavailable"
      fi
    fi
    ;;
  download) download_router ;;
  build) build_router ;;
  skip)
    printf '\nSkipping ccx-router installation (CCX_ROUTER_INSTALL=skip).\n'
    ;;
  *)
    printf 'error: CCX_ROUTER_INSTALL must be auto, download, build, or skip (got %s)\n' "$CCX_ROUTER_INSTALL" >&2
    exit 2
    ;;
esac

case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *)
    printf '\n%s is not on your PATH. Add it:\n' "$BINDIR"
    printf '  fish: fish_add_path %s\n' "$BINDIR"
    printf '  zsh : echo '\''export PATH="%s:$PATH"'\'' >> ~/.zshrc && exec zsh\n' "$BINDIR" ;;
esac
printf '\nNext: ccx new   (create your first profile)\n'
