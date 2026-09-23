# ccx-gui

Desktop profile manager for [ccx](../README.md), built with Tauri v2 (Rust) + Svelte.

It supports direct and routed profiles, ordered router fallbacks, automatic offline
health badges, non-prompt Doctor checks, and consent-gated capability certification.

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
- The current desktop bundle calls `ccx` from `PATH`; install the CLI/router before
  launching a profile. Release bundles do not embed a second copy of the CLI.
- Doctor checks can contact the configured model-list endpoint but never send a
  prompt. Certification sends up to three minimal requests only after confirmation
  and may incur provider charges.
- Router fallback keys are stored in the same private `profile.env`. Existing
  credentials never enter the renderer: edits preserve them by default, while
  Replace/Remove are write-only operations.
- Certification consent is enforced by an OS-native dialog in Rust, even for a
  direct IPC invocation. Production CSP permits only packaged assets and Tauri IPC.
- Provider endpoints/model IDs are editable defaults; change them per profile or in
  `~/.config/ccx/providers/*.tmpl`.
- A standalone `cargo build` needs the frontend built first (`npm run build`); `npm run tauri dev/build` does this automatically.
- On Linux/Wayland, WebKitGTK's DMABUF renderer can fail with
  `Gdk Error 71 (Protocol error) dispatching to Wayland display`, preventing the
  window from opening. `npm run tauri` goes through `scripts/run-tauri.mjs`, which
  sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` on Linux automatically. Export your own
  value to override it.
