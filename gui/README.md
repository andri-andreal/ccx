# ccx-gui

Desktop profile manager for [ccx](../README.md), built with Tauri v2 (Rust) + Svelte.

## Features

- Create, edit and delete model profiles shared with the CLI.
- Configure direct Anthropic-compatible providers, OpenAI-compatible router
  providers, or native Codex CLI profiles.
- Launch the selected profile in a terminal or copy its `ccx <name>` command.
- See model, provider, isolation and connection-kind indicators.
- Keep provider keys masked until explicitly revealed in the editor.
- Select an optional terminal command in Settings.

Native Codex profiles use Codex CLI, including its existing login or isolated
home. Login is completed through `ccx <profile> login`; the editor displays the
command rather than handling OAuth itself. Claude login uses
`ccx <profile> auth login`. See [authentication](../README.md#login-and-default-cli-settings).

Loop presets are currently managed in the CLI. Run `ccx new`, choose
**Plan/build/review loop** with ↑/↓ and Enter, then use the same menus to choose
each role's profile, model and effort in that order. Roles can share
the same profile/model. Verification is detected from the project at runtime;
use `--proof` to supply a specific command.
See the [workflow guide](../README.md#create-a-workflow-once-launch-by-name).

## Prerequisites

- Rust and Cargo.
- Node.js and npm; the project installs Tauri CLI through `npm ci`.
- Linux libraries: WebKitGTK 4.1, GTK 3 and libsoup 3.
- `ccx` installed and available on `PATH` for router and native Codex launches.
- Claude Code and/or Codex CLI, according to the profiles you run.

On CachyOS/Arch, the build packages used by this project are:

```bash
sudo pacman -Syu
sudo pacman -S --needed base-devel rustup nodejs npm pkgconf \
  webkit2gtk-4.1 gtk3 libsoup3 openssl
# Only if rustup does not already have a toolchain:
rustup default stable
```

Keep your existing Rust toolchain if it already works. See
[Tauri's Linux prerequisites](https://v2.tauri.app/start/prerequisites/#linux)
for distribution-specific details.

## Develop

From the repository root:

```bash
./install.sh
cd gui
npm ci
npm run tauri dev
```

## Build and install on Linux

From the repository root, build the CLI/router and desktop release binary:

```bash
./install.sh
cd gui
npm ci
npm run tauri build -- --no-bundle
cd ..
```

The GUI binary is `gui/target/release/ccx-gui`. The `--no-bundle` build produces
the native executable without distribution packages. To use the configured
Tauri package targets instead, run `npm run tauri build` from `gui/`.

Install the binary and a launcher for your account, still from the repository root:

```bash
install -Dm755 gui/target/release/ccx-gui "$HOME/.local/lib/ccx/ccx-gui"
install -Dm644 gui/src-tauri/icons/icon.png "$HOME/.local/share/ccx/icon.png"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"

cat > "$HOME/.local/bin/ccx-gui" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export WEBKIT_DISABLE_DMABUF_RENDERER="${WEBKIT_DISABLE_DMABUF_RENDERER:-1}"
exec "$HOME/.local/lib/ccx/ccx-gui" "$@"
EOF
chmod 755 "$HOME/.local/bin/ccx-gui"

cat > "$HOME/.local/share/applications/ccx.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=CCX
Comment=Manage Claude Code and Codex profiles
Exec="$HOME/.local/bin/ccx-gui"
Icon=$HOME/.local/share/ccx/icon.png
Terminal=false
Categories=Development;
StartupNotify=true
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications"
fi
```

Launch **CCX** from the application menu or run `~/.local/bin/ccx-gui`.
Rebuild and repeat the binary-copy step after GUI changes. CLI installation is a
symlink to the checkout; this GUI installation copies a release binary.

## Profile launch and storage

Profiles live at `~/.config/ccx/profiles/<name>/profile.env` (mode `600`), with
the same `CCX_HOME` / `XDG_CONFIG_HOME` root selection as the CLI. A profile
created here can be run with `ccx <name>` and a CLI model profile is editable here.

**Launch** detects a terminal and starts:

| Profile | Command inside the terminal |
| --- | --- |
| Direct Claude/Anthropic-compatible | `claude` with the profile's environment |
| OpenAI-compatible router profile | `ccx <name>`, which manages the router lifecycle |
| Native Codex | `ccx <name>`, which applies the Codex model/home settings |

**Copy** copies only `ccx <name>`; provider credentials are not included.
**Delete** asks for confirmation and removes the profile and its isolated home.
Provider endpoint/model defaults come from `providers/*.tmpl` under the CCX root.
Changing a template does not change an existing profile.

Terminal discovery checks `$TERMINAL`, then supported terminals including Kitty,
Alacritty, Konsole, Foot, WezTerm, Ghostty, GNOME Terminal and xterm. Use Settings
to save an override such as `alacritty -e`; an empty override restores detection.
The preference is stored in `gui.json` under the CCX configuration root.

## Tests

From the repository root:

```bash
cargo test --manifest-path gui/Cargo.toml
cd gui
npm run build
```

The root [README](../README.md#test) also lists CLI, workflow and router tests.
A standalone `cargo build` needs the frontend built first; Tauri dev/build runs
the frontend command automatically.

## Wayland troubleshooting

On some Linux compositor/GPU combinations, WebKitGTK's DMABUF renderer can fail
with `Gdk Error 71 (Protocol error) dispatching to Wayland display`. Both the npm
Tauri wrapper and the installed launcher above default to
`WEBKIT_DISABLE_DMABUF_RENDERER=1`, while preserving an explicit value.

If you run the release binary directly, use:

```bash
WEBKIT_DISABLE_DMABUF_RENDERER=1 ./gui/target/release/ccx-gui
```
