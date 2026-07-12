# ccx — Claude Code profile/provider switcher

`ccx` launches the [`claude`](https://docs.anthropic.com/en/docs/claude-code) CLI under
named profiles. Each profile points Claude Code at a provider + model setup via
environment variables — no changes to Claude Code itself.

Switch between Anthropic-compatible providers (MiniMax, GLM, DeepSeek, Kimi, custom) and
OpenAI-compatible backends (OpenAI, OpenRouter, Ollama, vLLM, LM Studio, Sakana) — the
latter through a small Rust translator `ccx` builds for you — without ever mixing
credentials or history.

![ccx running Claude Code under four profiles at once, each answering "which model are you using?" with a different model](images/ccx.png)

*One question, four providers: `ccx claude` (Opus 4.8), `ccx openrouter` (Qwen3-Coder),
`ccx fugu` (Sakana Fugu), and `ccx glm` (GLM-5.2) — the same Claude Code, a different model
behind each.*

## Features

- **Named profiles** — one command per provider/model combo: `ccx glm`, `ccx claude`, …
- **Isolated credentials** — third-party profiles each get their own `CLAUDE_CONFIG_DIR`,
  so logins and history never bleed into your Anthropic account.
- **No patching** — Claude Code runs unmodified; everything is driven by env vars.
- **Editable provider defaults** — endpoints and model IDs live in simple template files.
- **Live model discovery** — the `ccx new` wizard fetches each provider's model list
  from its API (`/v1/models`) and offers a type-to-filter picker; falls back to typing.
- **CLI + desktop GUI** — manage profiles from the terminal or a [Tauri](https://tauri.app) app.

## Supported providers

**Anthropic-compatible** (direct, no router): `claude` (Anthropic) · `minimax` · `glm` · `deepseek` · `kimi` · `custom`

**OpenAI-compatible** (via `ccx-router`, a small translator `ccx` builds and runs
for you): `openai` · `openrouter` · `ollama` · `vllm` · `lmstudio` · `sakana` · `custom-oai`
— see [OpenAI-compatible providers](#openai-compatible-providers-via-a-local-router).

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and on your `PATH`
  — `ccx` runs the `claude` binary (override with `CCX_CLAUDE_BIN`).
- Bash (the `ccx` CLI is a Bash script).
- For **OpenAI-compatible** providers only: Rust + cargo, to build the bundled
  `ccx-router` translator (`./install.sh` builds it; or
  `cargo build --release --manifest-path router/Cargo.toml`). Override the binary
  path with `CCX_ROUTER_BIN` if needed. Anthropic providers need none of this.
- For the desktop GUI: Rust + cargo and Node + npm (see [`gui/README.md`](gui/README.md)).

## Install

```bash
./install.sh
# add ~/.local/bin to PATH if prompted
```

## Use

```bash
ccx new                      # create a profile (interactive wizard: provider picker, live model list, upstream URL/key on OpenAI-compatible providers)
ccx --version                # print ccx, ccx-router, and detected claude versions
ccx list                     # list profiles  (alias: ls)
ccx claude                   # Anthropic login; Opus plans, Sonnet executes (opusplan)
ccx claude --model haiku     # extra args are passed straight to claude
ccx ollama -p "hello"        # run an OpenAI-compatible profile through ccx-router
ccx show <name>              # inspect a profile (token masked)
ccx edit <name>              # edit in $EDITOR
ccx rm <name>                # remove a profile  (aliases: remove, delete)
```

## How it works

- Profiles live in `~/.config/ccx/profiles/<name>/profile.env` (`chmod 600`).
- Third-party profiles are isolated: each gets its own `CLAUDE_CONFIG_DIR`
  (`~/.config/ccx/profiles/<name>/home`), so credentials and history never mix.
- The `claude` profile uses your normal Anthropic login and `~/.claude`
  (plugins/skills intact) and defaults to `ANTHROPIC_MODEL=opusplan`.
- Provider defaults are in `~/.config/ccx/providers/*.tmpl` — edit them to update
  endpoints or model IDs (these can change over time).

## Desktop GUI

A Tauri + Svelte desktop app for managing profiles is in [`gui/`](gui/README.md).
Profiles it creates are fully interchangeable with the CLI.

```bash
cd gui
npm install
npm run tauri dev
```

## Test

```bash
bash tests/test_ccx.sh
```

## OpenAI-compatible providers (via a local router)

Claude Code speaks the Anthropic Messages API (`/v1/messages`). Providers that
**only** offer the OpenAI Chat Completions format (OpenAI, OpenRouter, and local
servers like Ollama / vLLM / LM Studio) can't be used directly — so for these
`ccx` runs **`ccx-router`**, a small translator bundled in this repo
([`router/`](router)), that converts Anthropic ⇄ OpenAI (streaming and tool
calls included). It's a single Rust binary `ccx` builds and owns — no external
service. On launch `ccx` picks a free port, starts `ccx-router` against your
upstream, points Claude Code at it, and kills it on exit.

```bash
# Interactive: `ccx new` walks you through it (prompts for the model, upstream
# URL, and API key — templates pre-fill sensible defaults). Or use flags:

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
`/model opus|sonnet|haiku` keep working). Override the upstream endpoint/key with
`--upstream-url` and `--upstream-key`.

### Known gaps

- **The model must support tool use.** Text-only models fail — Claude Code's
  edits, git, and bash all go through tool calls. Pick a tool-capable model
  (e.g. `qwen2.5-coder` for local).
- **No prompt caching**, and usage reporting is best-effort (token counts are an
  estimate for streaming requests).
- **Building requires Rust.** `./install.sh` builds `ccx-router` if `cargo` is
  present; otherwise OpenAI-compatible providers are unavailable until you build
  it. Anthropic providers are unaffected.

## Roadmap

### OpenAI-compatible providers via a local translator

Shipped — see [OpenAI-compatible providers](#openai-compatible-providers-via-a-local-router):

- [x] Self-built translator `ccx-router` (Rust, single binary) — no third-party
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
- [x] `ccx --version` (ccx + ccx-router + detected claude).

Remaining:

- [ ] Prebuilt `ccx-router` binaries so Rust isn't required to install.
- [ ] OpenRouter provider pinning (`provider.only/order`) to avoid non-tool endpoints.

## License

[MIT](LICENSE) © andreal
