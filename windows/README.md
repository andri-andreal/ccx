# CCX on native Windows

This entry point runs from CMD, PowerShell or Windows Terminal without Bash,
Git Bash or WSL. The Linux/macOS CLI, workflow source, router source and GUI
source remain unchanged. Windows adaptations and build outputs live here.

## Start from CMD

Install Python 3.10 or newer and the AI CLIs you intend to use. From the
repository root you can immediately run:

```cmd
windows\ccx.cmd help
windows\ccx.cmd new --name codex-work --provider codex --yes
windows\ccx.cmd codex-work login
windows\ccx.cmd codex-work
```

To initialize configuration and add `ccx` to your user PATH, run from CMD:

```cmd
install-windows.cmd --add-to-path
```

This installer works even when PowerShell script execution is disabled. A
PowerShell entry point is also available if your execution policy permits it:

```powershell
.\install.ps1 -AddToPath
```

Open a new terminal afterwards. Keep this checkout at its current location;
the launcher uses the Python files and bundled definitions in the checkout.
The installer initializes the configuration and refreshes bundled provider
templates, preserving existing profiles and custom-named templates.

The installer does not install AI CLIs or access their login credentials.

## Profiles and workflows

```cmd
ccx new --name claude-work --provider claude --yes
ccx claude-work auth login
ccx new --name local --provider ollama --model qwen2.5-coder:32b --yes
ccx list
ccx show local
ccx default codex-work
ccx doctor local --json --no-network
```

Use `--token-stdin` or `--upstream-key-stdin` to keep API keys out of command
history. Interactive creation uses a masked key prompt. Profiles use the same
plain `profile.env` / `workflow.env` data format as the Unix implementation;
these files are parsed as data and never executed as shell code.

Windows configuration resolution matches the desktop GUI:
`CCX_HOME`, then `XDG_CONFIG_HOME\ccx`, then `%APPDATA%\ccx`, then
`%USERPROFILE%\.config\ccx`. New configuration directories have inheritance
disabled and grant full control to the current Windows account, SYSTEM and
Administrators. Linux configuration files are not migrated or modified.

Create a team with Claude Code and/or Codex profiles:

```cmd
ccx new --name team --type loop --orchestrator claude-work --planner codex-work --builder codex-work --reviewer claude-work --yes
ccx team
ccx team --route direct --proof "npm test" "Implement the requested change"
ccx team --review-only "Review the implementation plan"
```

The shared workflow engine retains routing, agent definitions, project trust,
review gates, revision budgets, HTTP request records, API checks and git
checkpoints. The Windows layer supplies console input, file locking, process
tree termination and UTF-8/LF artifact writing. Arrow keys, Unicode input and
the interactive picker are tested in a real Windows console. Mouse reporting
is disabled by default; use keyboard navigation. Proof and API-server commands
are interpreted by **CMD**, so use `%PORT%` instead of `$PORT` and Windows
commands rather than Unix shell syntax. Automatically detected Python commands
use `python` instead of `python3`.

Native Kimi Code and MiniMax Code profiles can launch their separately installed
CLIs (`--provider kimi-cli` and `--provider mcode`). Their **team roles remain
unavailable on Windows**, because the Unix implementation depends on Bubblewrap
to contain these agents. The Windows version refuses those roles rather than
running them without that boundary. To use Kimi/MiniMax models in a Windows
team, create `kimi`/`minimax` provider profiles, which run through Claude Code.

Executable overrides are `CCX_CLAUDE_BIN`, `CCX_CODEX_BIN`, `CCX_KIMI_BIN`,
`CCX_MCODE_BIN` and `CCX_ROUTER_BIN`. Values name a single executable path.
Standard npm-generated `.cmd` wrappers are resolved to their Node entry point,
preserving JSON and prompts without shell expansion. Custom `.bat`/`.cmd`
wrappers are rejected; supply an `.exe` or `.py` executable instead.

## Router

Direct Anthropic-compatible profiles and native CLI profiles do not need the
router. OpenAI-compatible profiles do. Build it with Rust/Cargo and the Visual
Studio C++ build tools installed:

```cmd
install-windows.cmd --build-router --add-to-path
```

Or build/test it separately:

```cmd
python windows\build_router.py --test
python windows\build_router.py --release
```

The build is staged in `windows\.build\router` using the shared source under
`router/` without source patches. It runs the same router unit and HTTP
conformance tests as the other platforms.

Alternatively put a trusted Windows `ccx-router.exe` in `windows\bin`, put it on
PATH, or set `CCX_ROUTER_BIN`. No binary is downloaded automatically.

Router processes start per launch and are stopped, including descendants, on
exit or interruption. API keys are passed through environment variables, never
through process arguments. `ccx logs NAME` reads the router's redacted log.

`ccx doctor NAME` can inspect the model-list endpoint without sending a prompt.
`ccx certify NAME --yes` sends up to three bounded capability probes and can
incur provider charges. Native CLI login sessions are not read for probes.

## Windows desktop GUI

With Node/npm, Rust/Cargo, Visual Studio C++ build tools and WebView2 available:

```cmd
python windows\build_gui.py --test-core
python windows\build_gui.py --no-bundle
```

The executable is `windows\.build\gui\target\release\ccx-gui.exe`.
Open **Teams** to configure all four roles, limits, verification, API checks
and agent/skill definitions, then choose a project and open an embedded session.
Team conversations, approvals, activity, request records, recovery and history
are available inside the desktop. See the [Team workspace guide](../gui/README.md#team-workspace).
Omit `--no-bundle` to build the Tauri installer packages. `--dev` starts the
development app. The CLI must already be on PATH for **Launch**, **Doctor** and
**Certify**.

The build stages the existing GUI under `windows\.build\gui`, replaces its
launcher with the Windows implementation, and connects diagnostics directly to
Python instead of trying to execute a batch file through `CreateProcess`.
`gui/` remains untouched. Use `cmd.exe /d /k` as a terminal override, or leave
the override empty for Windows Terminal/CMD detection.

## Verification

```cmd
python -X utf8 windows\build_router.py --test
python -X utf8 windows\build_router.py
python -X utf8 windows\test_windows.py -v
python -X utf8 windows\build_gui.py --test-core
python -X utf8 windows\build_gui.py --no-bundle
python -X utf8 windows\gui_smoke.py
```

Integration tests use disposable configuration directories, fake AI CLIs and
local HTTP fixtures. They do not call a paid model or modify your AI login.

Desktop Team verification:

```cmd
python -X utf8 tests\test_gui_team.py -v
cd gui
npm ci
npm run test:desktop
```

The desktop UI test drives the compiled Windows app through WebView2's local
debugging transport and stores screenshots in `gui/test-results`. It uses a
disposable configuration/project and fake model CLIs. Build the desktop first.
