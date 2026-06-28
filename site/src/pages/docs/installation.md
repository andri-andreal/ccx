---
layout: ../../layouts/Docs.astro
title: Installation
description: Install the ccx CLI and its prerequisites.
---

## Prerequisites

- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** installed and on
  your `PATH` — `ccx` runs the `claude` binary (override with `CCX_CLAUDE_BIN`).
- **Bash** — the `ccx` CLI is a Bash script.
- For the desktop GUI: **Rust + cargo** and **Node + npm** (see [Desktop GUI](/docs/desktop-gui/)).

## Install

```bash
./install.sh
# add ~/.local/bin to PATH if prompted
```

The installer symlinks `ccx` into `~/.local/bin` and copies the provider templates
into `~/.config/ccx/providers/`.

## Verify

```bash
ccx help
ccx list      # "No profiles yet" on a fresh install
```

## Where things live

- Profiles: `~/.config/ccx/profiles/<name>/profile.env` (`chmod 600`).
- Provider templates: `~/.config/ccx/providers/*.tmpl`.
- Isolated third-party homes: `~/.config/ccx/profiles/<name>/home`.
