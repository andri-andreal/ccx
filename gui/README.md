# ccx-gui

Desktop profile manager for [ccx](../README.md), built with Tauri v2 (Rust) + Svelte.

## Prerequisites
- Rust + cargo, `tauri-cli` (`cargo install tauri-cli` or via `npm`)
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

## Test the Rust core
```bash
cargo test --manifest-path gui/Cargo.toml
```

## Notes
- Profiles are stored exactly like the CLI: `~/.config/ccx/profiles/<name>/profile.env`
  (mode 600). A profile made here works with `ccx <name>` and vice versa.
- "Launch" opens your terminal running `claude` with the profile's environment.
  "Copy" copies `ccx <name>` (requires the CLI installed) — the token never touches the clipboard.
- Provider endpoints/model IDs are editable defaults; change them per profile or in
  `~/.config/ccx/providers/*.tmpl`.
- A standalone `cargo build` needs the frontend built first (`npm run build`); `npm run tauri dev/build` does this automatically.
- On Linux/Wayland, WebKitGTK's DMABUF renderer can fail with
  `Gdk Error 71 (Protocol error) dispatching to Wayland display`, preventing the
  window from opening. `npm run tauri` goes through `scripts/run-tauri.mjs`, which
  sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` on Linux automatically. Export your own
  value to override it.
