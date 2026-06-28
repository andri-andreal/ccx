---
layout: ../../layouts/Docs.astro
title: Providers
description: Supported providers and how templates work.
---

## Anthropic-compatible providers (direct)

| Profile | Provider | Notes |
| --- | --- | --- |
| `ccx claude` | Anthropic | your normal login, `opusplan` |
| `ccx minimax` | MiniMax | MiniMax-M3 / M2 |
| `ccx glm` | GLM (Z.ai) | Anthropic-compatible endpoint |
| `ccx deepseek` | DeepSeek | Anthropic-compatible endpoint |
| `ccx kimi` | Kimi (Moonshot) | Anthropic-compatible endpoint |
| `ccx custom` | Custom | any Anthropic-compatible endpoint |

Every provider above exposes an **Anthropic-compatible** endpoint (the same
`/v1/messages` API Claude Code speaks), so `ccx` just points `ANTHROPIC_BASE_URL`
at it — no translation needed.

## Provider templates

Defaults live in `~/.config/ccx/providers/*.tmpl`. For example, MiniMax:

```bash
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
ANTHROPIC_MODEL=MiniMax-M3
ANTHROPIC_DEFAULT_OPUS_MODEL=MiniMax-M3
ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M3
ANTHROPIC_DEFAULT_HAIKU_MODEL=MiniMax-M3
```

Endpoints and model IDs change over time — edit the template (affects new profiles)
or a single `profile.env` (affects that profile only).

## Choosing a model in `/model`

Because a provider usually serves one model, each Claude Code slot (Opus / Sonnet /
Haiku) maps to it. To expose two models in one profile's `/model` menu, point the
slots at different model IDs, or just type `/model <id>` directly.

## OpenAI-compatible providers (via a local router)

Providers that only speak the OpenAI Chat Completions format — OpenAI, OpenRouter,
and local servers like **Ollama / vLLM / LM Studio** — can't talk to Claude Code
directly. For these, `ccx` runs **`ccx-router`**, a small translator bundled in the
repo (`router/`) that converts Anthropic ⇄ OpenAI (streaming and tool calls
included). It's a single Rust binary `ccx` builds and owns — no third-party running
service. On launch `ccx` picks a free port, starts `ccx-router` against your
upstream, points `ANTHROPIC_BASE_URL` at it, and kills it on exit.

| Profile | Provider | Notes |
| --- | --- | --- |
| `ccx openai` | OpenAI | via ccx-router |
| `ccx openrouter` | OpenRouter | via ccx-router |
| `ccx ollama` | Ollama | local models, no API key |
| `ccx vllm` | vLLM | local server |
| `ccx lmstudio` | LM Studio | local server |
| `ccx custom-oai` | Custom (OpenAI) | any OpenAI-compatible endpoint |

```bash
# Local model with Ollama (no key needed):
ccx new --provider ollama --name local --model qwen2.5-coder:32b
ccx local -p "refactor this function"

# See the ccx-router command + wiring without starting it:
CCX_DRY_RUN=1 ccx local
```

A router profile carries `CCX_ROUTER=builtin` and the upstream endpoint; the model
is passed straight through via the usual `ANTHROPIC_*` slots:

```bash
CCX_ROUTER=builtin
CCX_ISOLATE=true
CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1
ANTHROPIC_MODEL=qwen2.5-coder:32b
```

`--model` fills every slot; `--opus` / `--sonnet` / `--haiku` override individual
ones. Set the endpoint/key with `--upstream-url` / `--upstream-key`.

### Prerequisite and known gaps

- Building `ccx-router` needs **Rust + cargo** (`./install.sh` builds it, or
  `cargo build --release --manifest-path router/Cargo.toml`). Override the binary
  path with `CCX_ROUTER_BIN`. Anthropic providers need none of this.
- **The model must support tool use**; text-only models fail. Pick a tool-capable
  model (e.g. `qwen2.5-coder` for local).
- **No prompt caching**, and token usage is an estimate for streaming requests.
