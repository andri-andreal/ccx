# ccx - Claude Code and Codex profile/provider switcher

`ccx` launches Claude Code or Codex CLI through named profiles. Use your existing
CLI login, select another provider/model, or save a workflow that plans, builds,
tests and checks changes until the builder and checker agree.

Claude Code profiles support Anthropic-compatible providers and OpenAI-compatible
backends through the bundled Rust translator, `ccx-router`. Native Codex profiles
run Codex CLI directly with its own authentication and configuration. Third-party
Claude profiles are isolated by default; native Claude and Codex profiles reuse
their existing login unless you request isolation. Neither CLI is patched.

[Quick start](#quick-start) · [OAuth login](#login-and-default-cli-settings) ·
[Workflows](#create-a-workflow-once-launch-by-name) · [CLI options](#cli-reference) ·
[CachyOS / Arch](#cachyos--arch-linux) · [Desktop GUI](gui/README.md)

![ccx running Claude Code under four profiles at once, each answering "which model are you using?" with a different model](images/ccx.png)

*One question, four providers: `ccx claude` (Opus 4.8), `ccx openrouter` (Qwen3-Coder),
`ccx fugu` (Sakana Fugu), and `ccx glm` (GLM-5.2), the same Claude Code, a different model
behind each.*

## Features

- **Named profiles** - one command per provider/model combo: `ccx glm`, `ccx claude`, …
- **Isolated credentials** - third-party profiles each get their own `CLAUDE_CONFIG_DIR`,
  so logins and history never bleed into your Anthropic account.
- **No patching** - Claude Code runs unmodified; everything is driven by env vars.
- **Editable provider defaults** - endpoints and model IDs live in simple template files.
- **Live model discovery** - the `ccx new` wizard fetches each provider's model list
  from its API (`/v1/models`) and offers a type-to-filter picker; falls back to typing.
- **CLI + desktop GUI** - manage profiles from the terminal or a [Tauri](https://tauri.app) app.
- **Native Codex profiles** - use Codex CLI with its own ChatGPT login and optional isolated home.
- **Saved workflows** - configure planner, builder, reviewer and verification once in `ccx new`, then launch by name.
- **Builder/checker consensus** - exchange fixes and evidence, rerun verification,
  and require both `READY` and `APPROVED` on the same code before completion.
- **Bounded retries and records** - configure revision budgets/timeouts, preserve
  failure diagnostics, and bind approvals to plan and code fingerprints.

## Supported providers

**Anthropic-compatible** (direct, no router): `claude` (Anthropic) · `minimax` · `glm` · `deepseek` · `kimi` · `custom`

**OpenAI-compatible** (via `ccx-router`, a small translator `ccx` builds and runs
for you): `openai` · `openrouter` · `ollama` · `vllm` · `lmstudio` · `sakana` · `custom-oai`.
See [OpenAI-compatible providers](#openai-compatible-providers-via-a-local-router).

**Native CLI:** `codex` uses Codex CLI with ChatGPT OAuth or its configured login.

| Profile provider/type | Executable | Authentication/configuration |
| --- | --- | --- |
| `claude` | `claude` | Existing Claude Code login; `opusplan` is the bundled model default |
| `minimax`, `glm`, `deepseek`, `kimi`, `custom` | `claude` | Provider endpoint/key; isolated Claude home by default |
| `openai`, `openrouter`, `ollama`, `vllm`, `lmstudio`, `sakana`, `custom-oai` | `claude` + `ccx-router` | Upstream API key or local server; isolated Claude home by default |
| `codex` | `codex` | Existing Codex login/configuration, or a separate home with `--isolate` |
| Loop workflow | CLI belonging to each selected profile | Each role uses that profile's configuration |

Names are chosen by you: `ccx work` can launch Codex if `work` was created with
`--provider codex`. The provider/type determines the executable. In particular,
the `openai` provider runs Claude Code through the router, while `codex` runs
Codex CLI directly.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and on your `PATH` for Claude/provider profiles.
  `ccx` runs the `claude` binary (override with `CCX_CLAUDE_BIN`).
- Codex CLI on your `PATH` for native Codex profiles (override with `CCX_CODEX_BIN`).
- Python 3.10+ for saved workflows; both CLIs are needed when the selected roles use both engines.
- Bash (the `ccx` CLI is a Bash script).
- Linux or macOS for the Bash CLI and Unix workflow locking. The build/install
  instructions below target Linux; native Windows CLI installation is not supplied.
- For **OpenAI-compatible** providers only: Rust + cargo, to build the bundled
  `ccx-router` translator (`./install.sh` builds it; or
  `cargo build --release --manifest-path router/Cargo.toml`). Override the binary
  path with `CCX_ROUTER_BIN` if needed. Anthropic providers need none of this.
- For the desktop GUI: Rust + cargo and Node + npm (see [`gui/README.md`](gui/README.md)).

### Installing Rust

If you don't have Rust installed, use [rustup](https://rustup.rs) (the official installer):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Follow the on-screen prompts (the defaults are fine), then reload your shell:

```bash
source "$HOME/.cargo/env"
```

Verify the installation:

```bash
rustc --version
cargo --version
```

> **Note:** Rust is only needed if you want to use OpenAI-compatible providers
> (which require building `ccx-router`) or the desktop GUI. If you only use
> Anthropic-compatible providers (`claude`, `minimax`, `glm`, `deepseek`, `kimi`),
> Rust is not required.

## Install

```bash
./install.sh
# add ~/.local/bin to PATH if prompted
```

Run this from your checkout. The installer links `~/.local/bin/ccx` to this
repository, copies the bundled provider templates, and builds/links `ccx-router`
when Cargo is available. Keep the checkout at this location. Existing profile
files are retained; reinstalling refreshes bundled template files, including any
edits you made to those templates. Custom-named templates are retained.

The installer installs the CLI/router. Build and install the GUI separately using
the [desktop instructions](gui/README.md#build-and-install-on-linux).

### CachyOS / Arch Linux

For a source build of the CLI, router and GUI, install the build tools and Linux
libraries if they are missing:

```bash
sudo pacman -Syu
sudo pacman -S --needed base-devel rustup nodejs npm python pkgconf \
  webkit2gtk-4.1 gtk3 libsoup3 openssl
# Only if rustup has no toolchain configured:
rustup default stable
```

If you already have a working Rust/Cargo toolchain, keep it rather than adding a
second Rust installation. Claude Code and Codex CLI are installed and
authenticated separately. Check the required engines with `claude --version`
and `codex --version`.

```bash
./install.sh
cd gui
npm ci
npm run tauri build -- --no-bundle
```

The binary is `gui/target/release/ccx-gui` relative to the repository root.
See [GUI installation](gui/README.md#build-and-install-on-linux) for the user-local
launcher and desktop-menu entry. More Linux package details are in the
[Tauri prerequisites](https://v2.tauri.app/start/prerequisites/#linux).

### Quick start

Run `ccx new` in a terminal. Use **↑/↓ and Enter** to choose **Single model** or
**Plan/build/review loop**; press **q** to cancel. Single-model creation then opens
the provider picker. Claude/provider profiles offer model discovery when supported;
Codex profiles ask for an optional model ID and whether to sign in.

Or create native profiles with flags:

```bash
ccx new --name claude --provider claude --yes
ccx new --name codex --provider codex --yes
ccx claude
ccx codex
```

Skip creation for names you already have. These native profiles reuse the login
of their respective CLI by default. Follow the login section if you need to sign in.

## Use

```bash
ccx new                      # create a profile (interactive wizard: provider picker, live model list, upstream URL/key on OpenAI-compatible providers)
ccx --version                # print ccx, ccx-router, Claude Code and Codex versions
ccx list                     # list profiles  (alias: ls)
ccx claude                   # Anthropic login; Opus plans, Sonnet executes (opusplan)
ccx claude --model haiku     # extra args are passed straight to claude
ccx ollama -p "hello"        # run an OpenAI-compatible profile through ccx-router
ccx codex exec "Explain this repository" # native Codex non-interactive execution
ccx show work                # inspect a profile named work (token masked)
ccx edit work                # edit work in $EDITOR
ccx rm unused-profile        # remove a profile (aliases: remove, delete)
```

## How it works

- Profiles live in `~/.config/ccx/profiles/<name>/profile.env` (`chmod 600`).
- Third-party profiles are isolated: each gets its own `CLAUDE_CONFIG_DIR`
  (`~/.config/ccx/profiles/<name>/home`), so credentials and history never mix.
- The `claude` profile uses your normal Anthropic login and `~/.claude`
  (plugins/skills intact) and defaults to `ANTHROPIC_MODEL=opusplan`.
- Provider defaults are in `~/.config/ccx/providers/*.tmpl`. Edit them to update
  endpoints or model IDs (these can change over time).

## Login and default CLI settings

CCX delegates login and credential refresh to the official CLIs. Existing login,
settings and history are reused when the profile has isolation disabled. Profile
model/endpoint overrides apply to the invocation; CCX does not rewrite the global
CLI configuration.

| Engine | Sign in | Check login | Sign out |
| --- | --- | --- | --- |
| Claude Code | `ccx claude auth login` | `ccx claude auth status` | `ccx claude auth logout` |
| Codex CLI | `ccx codex login` | `ccx codex login status` | `ccx codex logout` |

The examples assume profiles named `claude` and `codex`. Native Claude uses its
configured authentication; run `auth login` and select the account/login method
appropriate for your subscription. See [Claude Code authentication](https://code.claude.com/docs/en/authentication).

The bundled Claude profile sets `ANTHROPIC_MODEL=opusplan`; it therefore reuses
your login/settings while overriding the model. Use `ccx claude --model <model>`
or edit the profile to change that default. Codex uses its configured model when
no `--model` was saved in the profile.

To isolate another account, create a profile with `--isolate` and authenticate
through that profile. Claude uses a profile-specific `CLAUDE_CONFIG_DIR`; Codex
uses a profile-specific `CODEX_HOME`. Existing externally configured home
variables are respected for nonisolated profiles. `ccx new --login` and
`--device-auth` are Codex-only options; Claude authentication is invoked after
profile creation through `ccx <profile> auth login`.

### Codex with ChatGPT login

Choose `codex` in `ccx new`, or create a profile and sign in in one command:

```bash
ccx new --name codex --provider codex --login --yes
ccx codex
ccx codex exec "Explain this repository"
ccx codex login status
```

For a remote/headless machine, use `--device-auth` instead of `--login`.
You complete authentication in your browser; Codex CLI handles OAuth, credential
storage and token refresh. CCX does not copy or parse OAuth tokens. See the
[official authentication documentation](https://learn.chatgpt.com/docs/auth).

Native profiles reuse your existing Codex configuration and login by default.
Add `--isolate` when creating one to use a separate `CODEX_HOME` at
`~/.config/ccx/profiles/<name>/home`; sign in separately for that home. Use
`ccx <name> login`, `login --device-auth`, `login status`, or `logout` as needed.
An existing API-key login remains an API-key login until you explicitly switch
it using Codex's login flow. CCX does not change your global authentication.

`--model` on creation saves an optional default, while invocation arguments can
override it. An empty model uses Codex's own configuration. The existing `openai`
provider continues to run Claude Code through the API router; `codex` runs the
native CLI directly. Native profiles are available in the desktop GUI as well.

## Create a workflow once, launch by name

Run `ccx new` in a terminal and choose **Plan/build/review loop** with **↑/↓ and Enter**.
Use the same arrow-key menus to select existing profiles for planner, builder and
checker (reviewer). Each menu shows the provider and model; the builder menu
starts with the planner profile. All roles may use the same profile/model;
each turn uses a separate CLI session. The wizard completes each role in order:
**profile → model → effort**, then moves to the next role. All three menus use
**↑/↓ and Enter**. **default** keeps the profile/CLI configuration.
Native Claude profiles offer Sonnet, Opus and Haiku aliases. Native Codex profiles
read available model IDs and their effort levels from that profile's local
`models_cache.json` (isolated home or `CODEX_HOME`/`~/.codex`). CCX does not
contact the service or read credentials to populate these menus. If the cache
is absent or a model is missing, use **Custom model ID**. Model access still
depends on the account and CLI. Overrides apply only to the selected workflow
role and leave the original profile unchanged.
Completed menus disappear, leaving a single-line summary of each choice.
Press **q** to cancel. Verification is detected from the target project when
you run the workflow. Create model profiles first if none are available.
You can also save the same configuration using flags:

```bash
# First create the model profiles, if you do not already have them.
ccx new --name claude --provider claude --yes
ccx new --name codex --provider codex --login --yes

ccx new --name team --type loop \
  --planner claude --builder claude --reviewer codex \
  --planner-effort high --builder-effort medium --reviewer-effort high --yes

# Run from a clean Git checkout of the project you want to change.
ccx team "Add CSV export with acceptance tests"
ccx team --review-only "Plan CSV export"
# Per-run revision-budget override:
ccx team --fix-rounds 10 "Add CSV export with acceptance tests"
# Optional verification override for this run:
ccx team --proof 'npm run lint && npm test' "Add CSV export"

# Claude Sonnet plans, Codex builds, Claude Haiku checks, using existing logins:
ccx new --name mixed --type loop \
  --planner claude --planner-model sonnet \
  --builder codex --builder-model gpt-6.1-sol \
  --reviewer claude --reviewer-model haiku --yes
```

Swap the roles with `--planner codex --builder codex --reviewer claude` to have
Codex implement and Claude check. Any existing tool-capable model profile can
serve a role. The reviewer is also the final code checker.

Calling `ccx team` without a task asks for one in a terminal. Workflow presets
are configured in the CLI; `ccx list`, `show`, `edit` and `rm` also support them.
They reference existing profiles and contain no credentials. The builder defaults
to the planner when omitted. The reviewer can use the same profile/model.

The loop creates a plan, reviews it, revises within the round budget, builds only
an approved plan, executes your saved verification command locally with Bash, and
inspects the final changes in a fresh reviewer session. Builder and checker
exchange structured feedback: each finding gets a fix, a dispute with evidence,
or an explicit deferral. The checker evaluates those responses against the code.
The loop finishes only when the builder reports `READY`, the checker reports
`APPROVED`, and verification passes for the inspected changes. Failed verification
returns its output to the builder for another attempt; checker approval alone
cannot finish a run when the builder still reports work remaining.

Verification is optional when saving a preset. At runtime CCX detects root-level
Node scripts (`test`, then `check`, then `build`, using the configured package
manager or lockfile), Cargo (`cargo test`), Go (`go test ./...`), Makefile
targets (`test`, `check`, `build`), pytest configuration, or unittest tests.
The default npm placeholder test is ignored. A saved `--proof` takes precedence
over detection; a runtime `--proof` overrides both. If detection finds nothing,
CCX asks for a command in a terminal, or reports how to supply `--proof` before
calling any models. Review-only runs can proceed without verification.
Commands run with your normal local permissions at the target Git root.
Approval binds the plan hash and inspection binds the change snapshot, including
staged, untracked and deleted files. Concurrent workflows in the same checkout
are rejected. Malformed model output, blocked builders/reviews and
exhausted budgets stop the run with a nonzero exit. Changes are preserved for you
to inspect; CCX does not commit, publish, reset or stash them.

`--rounds` (default 3), `--fix-rounds` (default 2, after initial inspection), and
`--timeout` (default 600 seconds per process) can be saved during creation.
Increase the saved revision budget or override it for a single run, for example
`ccx team --fix-rounds 10 "Add CSV export"`. The budget counts revisions after
the first build; unresolved disagreement or failed verification at the limit
produces a nonzero exit, never an automatic approval. `consensus-rounds.json`
preserves the dialogue and proof results; `consensus.json` records both parties'
agreement and the plan/code fingerprints only on success.

Progress is shown automatically: round numbers, profile/model/effort, public
Claude tool activity or Codex command/file events, elapsed time every 10 seconds,
review/builder summaries and findings, and verification stdout/stderr. Long
messages are shortened on screen; full raw CLI streams remain in the artifacts.
Claude uses `--output-format stream-json --verbose`; private reasoning blocks
are not rendered. Reviews accept `high`, `medium`, `low`, and informational
`info` findings. Unresolved high/medium findings prevent approval; duplicate IDs
and unsupported severity values report the specific offending finding.

Plans, per-turn results, diagnostics, proof output and final status are kept under
`$CCX_HOME/runs/<unique-run>/`, outside the target checkout. These artifacts may
contain private code. Plan review and inspection are model judgments; passing
tests and approval do not establish exhaustive correctness.

Workflow calls run without permission bypass flags. Claude planning/review only
exposes reading tools with customizations disabled; Claude builders expose read
and edit tools. Codex uses read-only/workspace-write shell sandboxes. The workflow
queries effective Codex MCP servers and disables them for its calls; Claude uses
an empty strict MCP configuration. Unsupported CLI flags, authentication failures
or blocked operations are reported rather than retried with weaker permissions.
No live model calls are made by the automated integration tests.

## Desktop GUI

A Tauri + Svelte desktop app for managing profiles is in [`gui/`](gui/README.md).
Profiles it creates are fully interchangeable with the CLI.

The GUI can create/edit/delete model profiles, launch a profile in your terminal,
copy its `ccx <name>` command, reveal a stored provider key, and configure the
terminal launcher. Native Codex profiles have their own provider choice and model
field. Loop presets are currently created, listed and run through the CLI.

```bash
cd gui
npm ci
npm run tauri dev
```

For a release binary, launcher and application-menu entry, follow
[Build and install on Linux](gui/README.md#build-and-install-on-linux).

## CLI reference

Profile names allow letters, digits, `_` and `-`. Use a unique name and avoid the
built-in command names (`new`, `list`, `show`, `edit`, `rm`, `help`, `version`).

| Command | Behavior |
| --- | --- |
| `ccx new` | Interactive creation; ↑/↓ selects Single or Loop |
| `ccx <model-profile> [args...]` | Forward arguments to that profile's CLI |
| `ccx <workflow> "task"` | Run plan review, implementation, proof and consensus |
| `ccx <workflow> --review-only "task"` | Create/review a plan without building |
| `ccx <workflow> --fix-rounds N "task"` | Override the revision budget for one run |
| `ccx list` / `ccx ls` | List model profiles and saved workflows |
| `ccx show <name>` | Show profile/preset settings; supported token fields are masked |
| `ccx edit <name>` | Edit settings with `$EDITOR` (default `vi`) |
| `ccx rm <name>` | Confirm and remove profile/preset, including its isolated home |
| `ccx remove <name>` / `ccx delete <name>` | Aliases for `rm` |
| `ccx --version` / `ccx version` / `ccx -V` | Print CCX/router and detected CLI versions |
| `ccx help` / `ccx --help` | Print command help |

### Creation options

Use these with `ccx new`. Workflow roles reference model profiles that already
exist. Provider/model/authentication options belong to single-model profiles;
planner/builder/reviewer/proof options belong to loop profiles.

| Option | Purpose/default |
| --- | --- |
| `--name NAME` | Profile/preset name; prompted when omitted |
| `--type single` / `--type loop` | Profile kind; interactive menu when no provider/type is supplied in a terminal |
| `--provider NAME` | Provider from the picker, or a custom template/name |
| `--model ID` | Model default; Codex may leave it empty to use its CLI configuration |
| `--opus ID`, `--sonnet ID`, `--haiku ID` | Claude model-slot overrides |
| `--base-url URL`, `--token TOKEN` | Anthropic-compatible endpoint and credential |
| `--upstream-url URL`, `--upstream-key KEY` | OpenAI-compatible router endpoint and credential |
| `--isolate` / `--no-isolate` | Override whether the profile has its own CLI home |
| `--login` | Start Codex's browser login after creating a Codex profile |
| `--device-auth` | Start Codex device-code login; implies `--login` |
| `--planner NAME` | Loop planner profile; required or prompted |
| `--builder NAME` | Loop builder profile; defaults to planner |
| `--reviewer NAME` | Independent plan reviewer and code checker; required or prompted |
| `--planner-model ID`, `--builder-model ID`, `--reviewer-model ID` | Per-role model override; omitted values follow the selected profile/CLI |
| `--planner-effort LEVEL`, `--builder-effort LEVEL`, `--reviewer-effort LEVEL` | Per-role effort; arrow-key menus in the wizard, otherwise `default` |
| `--proof COMMAND` | Optional saved verification command; otherwise detect from the target project |
| `--rounds N` | Maximum plan-review rounds; default `3` |
| `--fix-rounds N` | Maximum revisions after the first build; default `2` |
| `--timeout SECONDS` | Timeout per model/proof process; default `600` |
| `--yes` / `-y` | Skip optional wizard prompts; provide required values explicitly |

Effort levels include `default`, `low`, `medium`, `high`, `xhigh`, and `max`,
plus `minimal`/`ultra` for Codex models that support them. The Codex menu uses
the cached model's supported levels when available; otherwise it offers the
conservative `minimal` through `xhigh` range. Availability also depends on
the selected model and installed CLI. Default passes no effort override.
The round limits and timeout are positive integers. Workflow invocation accepts
`--review-only`, `--proof` and `--fix-rounds`; other settings are saved at creation or changed
with `ccx edit <workflow>`. Omitting the task prompts for it in a terminal; a
noninteractive invocation requires the task argument.

### CLI runtime environment

| Variable | Purpose/default |
| --- | --- |
| `CCX_HOME` | Configuration root; otherwise `$XDG_CONFIG_HOME/ccx` or `~/.config/ccx` |
| `CCX_CLAUDE_BIN` | Claude Code executable; default `claude` from `PATH` |
| `CCX_CODEX_BIN` | Codex executable; default `codex` from `PATH` |
| `CCX_ROUTER_BIN` | Router executable; default `ccx-router`, with a checkout release-binary fallback |
| `CCX_ROUTER_TIMEOUT` | Router startup wait in seconds; default `15` |
| `CCX_DRY_RUN=1` | Print launch wiring or workflow configuration without starting model calls |
| `CCX_YES=1` | Skip the CLI profile-removal confirmation |
| `EDITOR` | Editor for `ccx edit`; default `vi` |

For example, `CCX_DRY_RUN=1 ccx team` shows the saved roles/proof command;
`CCX_DRY_RUN=1 ccx codex` shows the selected executable/home. Dry runs do not log
in or launch models, though profile home directories may still be created.
Claude arguments such as `--model` and Codex arguments such as `exec`, `resume`
or `login` are passed to the corresponding CLI, not translated between engines.

### Files and logs

Paths are relative to the resolved configuration root:

| Path | Contents |
| --- | --- |
| `profiles/<name>/profile.env` | Model profile, mode `600`; includes provider credentials when applicable |
| `profiles/<name>/home/` | Isolated Claude or Codex home, mode `700` |
| `profiles/<name>/workflow.env` | Loop role references, proof command and limits, mode `600` |
| `providers/*.tmpl` | Provider defaults; model-discovery templates can also define `CCX_MODELS_URL` and `CCX_MODELS_AUTH` |
| `gui.json` | GUI terminal preferences |
| `runs/<unique-run>/PLAN.md` | Reviewed plan |
| `runs/<unique-run>/<turn>-<phase>/` | Prompt, raw stdout/stderr and parsed result for each model call |
| `runs/<unique-run>/proof-*.json` and `proof-*.stdout.txt` / `proof-*.stderr.txt` | Verification command, exit status and output |
| `runs/<unique-run>/consensus-rounds.json` | Builder responses, checker findings and proof history |
| `runs/<unique-run>/consensus.json` | Successful agreement bound to the plan and code snapshot |
| `runs/<unique-run>/result.json` | Final completion/failure status and summary |

CCX prints the run directory when a workflow starts. Inspect `result.json` first
on failure, then the indicated turn diagnostics or proof output. Failed calls are
not resumed automatically; inspect the preserved changes before starting another
run from a clean checkout. Removing a preset does not remove its historical run
directories or the model profiles it references.

## Test

```bash
bash tests/test_ccx.sh
python3 -B -m unittest discover -s tests -p 'test_native_workflow.py' -v
cargo test --manifest-path router/Cargo.toml
cargo test --manifest-path gui/Cargo.toml
cd gui
npm ci
npm run build
```

The Python integration tests use temporary profiles/Git repositories and fake
CLI processes, including real terminal input for the wizard. They cover both
Claude/Codex directions, proof repair, disputed findings, agreement requirements,
round limits, snapshot invalidation and timeout handling. They do not consume
model quota or establish live OAuth/model compatibility.

## OpenAI-compatible providers (via a local router)

Claude Code speaks the Anthropic Messages API (`/v1/messages`). Providers that
**only** offer the OpenAI Chat Completions format (OpenAI, OpenRouter, and local
servers like Ollama / vLLM / LM Studio) can't be used directly, so for these
`ccx` runs **`ccx-router`**, a small translator bundled in this repo
([`router/`](router)), that converts Anthropic ⇄ OpenAI (streaming and tool
calls included). It's a single Rust binary `ccx` builds and owns, no external
service. On launch `ccx` picks a free port, starts `ccx-router` against your
upstream, points Claude Code at it, and kills it on exit.

```bash
# Interactive: `ccx new` walks you through it (prompts for the model, upstream
# URL, and API key. Templates pre-fill sensible defaults. Or use flags:

# Local model with Ollama (no API key needed):
ccx new --provider ollama --name local --model qwen2.5-coder:32b
ccx local -p "refactor this function"

# OpenRouter / Sakana Fugu (prompts for the upstream key, stored chmod 600):
ccx new --provider openrouter --name orr --model qwen/qwen3-coder
ccx new --provider sakana --name fugu --upstream-key "$SAKANA_KEY"   # api.sakana.ai/v1, model fugu
ccx fugu

# Inspect what would happen without starting the router:
CCX_DRY_RUN=1 ccx local      # prints the ccx-router command, port, and env wiring

# Any other OpenAI-compatible endpoint: `custom-oai` + --upstream-url.
```

The model is passed straight through to the upstream, so `--model` sets it for
every slot and `--opus` / `--sonnet` / `--haiku` override individual slots (so
`/model opus|sonnet|haiku` keep working). Set the upstream endpoint/key during
profile creation with `--upstream-url` and `--upstream-key`, or use `ccx edit`.

### Known gaps

- **The model must support tool use.** Text-only models fail. Claude Code's
  edits, git, and bash all go through tool calls. Pick a tool-capable model
  (e.g. `qwen2.5-coder` for local).
- **No prompt caching**, and usage reporting is best-effort (token counts are an
  estimate for streaming requests).
- **Building requires Rust.** `./install.sh` builds `ccx-router` if `cargo` is
  present; otherwise OpenAI-compatible providers are unavailable until you build
  it. Anthropic providers are unaffected.

## Troubleshooting

| Symptom | Check/action |
| --- | --- |
| `claude not found` / `codex not found` | Install the selected CLI, check `PATH`, or set the corresponding `CCX_*_BIN` |
| Login missing in an isolated profile | Log in through that profile; its home has separate credentials |
| Codex browser login cannot finish remotely | Use `ccx <profile> login --device-auth` and complete the device flow |
| Router unavailable | Rebuild with `cargo build --release --manifest-path router/Cargo.toml`; check `CCX_ROUTER_BIN` |
| Model picker cannot fetch models | Enter the provider-supported model ID manually; inspect endpoint/auth settings |
| Workflow requires a clean checkout | Commit/stash your existing work yourself, or run in a clean Git worktree |
| Verification keeps failing | Read `proof-*.stderr.txt`/stdout; correct the proof command or implementation |
| Builder/checker have not agreed | Inspect `consensus-rounds.json`; increase `--fix-rounds` if further revisions are appropriate |
| Unsupported CLI flag / invalid structured output | Inspect turn stderr; check CLI version compatibility without weakening permissions |
| GUI cannot launch a terminal | Set the terminal override in GUI Settings, or use Copy command |
| GUI fails to open on Wayland | Use the installed launcher or set `WEBKIT_DISABLE_DMABUF_RENDERER=1` as described in the [GUI README](gui/README.md) |

## Roadmap

### OpenAI-compatible providers via a local translator

Shipped. See [OpenAI-compatible providers](#openai-compatible-providers-via-a-local-router):

- [x] Self-built translator `ccx-router` (Rust, single binary), no third-party
      running service. Translates Anthropic ⇄ OpenAI: requests, non-streaming and
      streaming responses, and tool-call round-trips (with correct interleaved
      text/tool_use block handling).
- [x] Provider kind `openai-compatible` (templates: `openai`, `openrouter`,
      `ollama`, `vllm`, `lmstudio`, `custom-oai`).
- [x] Profile schema: `CCX_ROUTER=builtin` + `CCX_UPSTREAM_BASE_URL` /
      `CCX_UPSTREAM_API_KEY`; models pass through via the `ANTHROPIC_DEFAULT_*`
      slots. Secrets stay in `profile.env` (`chmod 600`); the local hop is secured
      by a random per-launch token bound to `127.0.0.1`.
- [x] Launch lifecycle: pick a free port, start `ccx-router`, health-check
      (port-readiness), set `ANTHROPIC_BASE_URL`, run `claude`, and tear the
      router down on exit/Ctrl-C via `trap`.
- [x] `CCX_DRY_RUN` prints the `ccx-router` command + port + env wiring without starting it.
- [x] Tests: Rust fixture tests for every translation path (incl. streaming
      interleave) + an integration test against a mock upstream; bash dry-run and
      live-lifecycle tests.

Also shipped since:

- [x] GUI: provider picker + router/upstream fields in the create/edit form, with a
      per-profile Direct / Via ccx-router / Local badge and the upstream URL shown.
- [x] Wizard fetches each provider's live model list (`/v1/models`) with a
      type-to-filter picker; falls back to the template default / manual entry.
- [x] `ccx --version` (ccx + ccx-router + detected Claude Code/Codex).
- [x] Native Codex profiles, existing-login reuse, optional isolated home, browser/device OAuth entry points.
- [x] ↑/↓ Single/Loop selection in `ccx new`.
- [x] Saved workflows with independent plan review, proof repair, builder/checker feedback and consensus records.

Remaining:

- [ ] Prebuilt `ccx-router` binaries so Rust isn't required to install.
- [ ] OpenRouter provider pinning (`provider.only/order`) to avoid non-tool endpoints.
- [ ] Workflow preset creation, listing and execution in the desktop GUI.

## License

[MIT](LICENSE) © andreal
