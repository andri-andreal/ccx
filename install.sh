#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
BINDIR="$HOME/.local/bin"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/ccx"

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

# Build the OpenAI-compatible translator (ccx-router). Only needed for the
# openai/openrouter/ollama/vllm/lmstudio/custom-oai providers; Anthropic
# providers work without it. Skip gracefully if cargo is missing.
if command -v cargo >/dev/null 2>&1; then
  printf '\nBuilding ccx-router (OpenAI-compatible translator)...\n'
  if cargo build --release --quiet --manifest-path "$REPO/router/Cargo.toml"; then
    ln -sf "$REPO/router/target/release/ccx-router" "$BINDIR/ccx-router"
    printf 'Installed ccx-router -> %s\n' "$BINDIR/ccx-router"
  else
    printf 'warning: ccx-router build failed; OpenAI-compatible providers will be unavailable.\n' >&2
  fi
else
  printf '\nNote: cargo not found — skipping ccx-router build. Anthropic providers work as-is;\n'
  printf '      install Rust and re-run to enable OpenAI-compatible providers.\n'
fi

case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *)
    printf '\n%s is not on your PATH. Add it:\n' "$BINDIR"
    printf '  fish: fish_add_path %s\n' "$BINDIR"
    printf '  zsh : echo '\''export PATH="%s:$PATH"'\'' >> ~/.zshrc && exec zsh\n' "$BINDIR" ;;
esac
printf '\nNext: ccx new   (create your first profile)\n'
