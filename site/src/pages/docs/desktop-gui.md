---
layout: ../../layouts/Docs.astro
title: Desktop GUI
description: The Tauri + Svelte desktop profile manager.
---

A desktop app for managing profiles lives in the [`gui/`](https://github.com/andri-andreal/claude-code-profile-switcher/tree/main/gui)
directory, built with **Tauri v2 (Rust) + Svelte**. Profiles it creates are fully
interchangeable with the CLI.

## Prerequisites

- Rust + cargo, `tauri-cli`
- Node + npm
- Linux: `webkit2gtk-4.1`, `libsoup-3.0`, `gtk3`

## Develop

```bash
cd gui
npm install
npm run tauri dev
```

## Build

```bash
cd gui
npm run tauri build
```

## Notes

- Profiles are stored exactly like the CLI: `~/.config/ccx/profiles/<name>/profile.env`
  (mode 600). A profile made here works with `ccx <name>` and vice versa.
- **Launch** opens your terminal running `claude` with the profile's environment.
  **Copy** copies `ccx <name>` — the token never touches the clipboard.
- On Linux/Wayland, `npm run tauri` sets `WEBKIT_DISABLE_DMABUF_RENDERER=1`
  automatically to avoid a WebKitGTK rendering crash.
