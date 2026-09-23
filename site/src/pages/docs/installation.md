---
layout: ../../layouts/Docs.astro
title: Installation
description: Install the ccx CLI and its prerequisites.
---

## Prerequisites

- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** installed and on
  your `PATH` — `ccx` runs the `claude` binary (override with `CCX_CLAUDE_BIN`).
- **Bash** — the `ccx` CLI is a Bash script.
- **curl or wget** — downloads the release manifest/router. Network diagnostics
  and certification specifically require `curl`.
- **Python 3 or Node.js** — either one provides structured validation for active
  certification responses.
- Rust + cargo are only needed for a source-build fallback or development.
- Building the desktop GUI from source also needs Node + npm (see
  [Desktop GUI](/docs/desktop-gui/)).

## Install

```bash
./install.sh
# add ~/.local/bin to PATH if prompted
```

The installer symlinks `ccx` into `~/.local/bin`, copies provider templates into
`~/.config/ccx/providers/`, and tries to download the matching `ccx-router` release
for Linux x86_64/aarch64, macOS x86_64/arm64, or Windows x86_64. It verifies the
selected asset against `SHA256SUMS` before installation. When no matching release is
available, the default `auto` policy falls back to `cargo build --locked --release`.

Choose the policy explicitly when needed:

```bash
CCX_ROUTER_INSTALL=download ./install.sh # verified prebuilt or fail
CCX_ROUTER_INSTALL=build ./install.sh    # build locally with Rust
CCX_ROUTER_INSTALL=skip ./install.sh     # direct providers only
```

Mirrors and offline fixtures can set `CCX_RELEASE_BASE_URL` and
`CCX_RELEASE_VERSION`. `CCX_BIN_DIR`, `CCX_HOME`, and `CCX_ROUTER_BUILD_DIR`
control installation locations.

## Verify

```bash
ccx help
ccx --version
ccx list      # "No profiles yet" on a fresh install
ccx doctor    # verify Claude, config, and profiles
```

Release automation creates SHA-256 manifests and GitHub build-provenance
attestations for every router asset. A detached Cosign signature and public key are
also attached when the repository signing secret is configured.

## Where things live

- Profiles: `~/.config/ccx/profiles/<name>/profile.env` (`chmod 600`).
- Provider templates: `~/.config/ccx/providers/*.tmpl`.
- Isolated third-party homes: `~/.config/ccx/profiles/<name>/home`.
