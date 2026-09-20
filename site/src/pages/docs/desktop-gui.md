---
layout: ../../layouts/Docs.astro
title: Desktop GUI
description: The Tauri + Svelte desktop profile manager.
---

A desktop app for managing profiles lives in the [`gui/`](https://github.com/andri-andreal/ccx/tree/main/gui)
directory, built with **Tauri v2 (Rust) + Svelte**. Profiles it creates are fully
interchangeable with the CLI.

## Build prerequisites

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
- The profile list runs local-only diagnostics automatically and shows
  ready/warning/blocked badges. Expand a profile for individual checks and
  basic/streaming/tool capability badges.
- **Doctor** performs an authenticated model-list reachability check without an
  inference prompt. **Certify**
  stays behind an OS-native confirmation dialog enforced by the Rust backend because
  its three minimal probes may incur a provider charge.
- Router profiles expose ordered fallback URLs, positional fallback credentials,
  and attempts per upstream. Stored credentials never enter the webview: they are
  preserved by default and can only be replaced or removed through write-only fields.
- A restrictive production Content Security Policy allows packaged assets and Tauri
  IPC, but no remote scripts or connections from the renderer.
- Release automation builds deb/AppImage, macOS app/DMG, and Windows NSIS/MSI
  bundles. The current desktop runtime launches the existing `ccx` command, so the
  CLI must still be on `PATH`.
- On Linux/Wayland, `npm run tauri` sets `WEBKIT_DISABLE_DMABUF_RENDERER=1`
  automatically to avoid a WebKitGTK rendering crash.
