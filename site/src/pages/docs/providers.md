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
included). It's a single binary `ccx` installs and owns — no third-party running
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

# Primary, then backup; retry each no more than twice:
ccx new --provider custom-oai --name resilient --model my-model \
  --upstream-url https://primary.example/v1 \
  --fallback-url https://backup.example/v1 --router-attempts 2 --yes
```

A router profile carries `CCX_ROUTER=builtin` and the upstream endpoint; the model
is passed straight through via the usual `ANTHROPIC_*` slots:

```bash
CCX_ROUTER=builtin
CCX_ISOLATE=true
CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1
CCX_ROUTER_FALLBACK_URLS=https://backup.example/v1
CCX_ROUTER_ATTEMPTS_PER_UPSTREAM=2
ANTHROPIC_MODEL=qwen2.5-coder:32b
```

`--model` fills every slot; `--opus` / `--sonnet` / `--haiku` override individual
ones. Set the endpoint/key with `--upstream-url` / `--upstream-key`.

### OpenRouter provider pinning

One OpenRouter model can be served by providers with different capabilities, and
an endpoint without tool support makes Claude Code unusable. Pinning is set per
upstream, because `provider` is an OpenRouter field that other backends in the
same chain must not receive.

```bash
ccx new --provider openrouter --name orr --model qwen/qwen3-coder \
  --provider-only groq,fireworks --require-parameters \
  --fallback-url https://openrouter.ai/api/v1 \
  --fallback-provider-only together --yes
```

| Flag | Body field | Effect |
| --- | --- | --- |
| `--provider-only` | `provider.only` | restricts routing to these provider slugs |
| `--provider-order` | `provider.order` | sets the preference order among them |
| `--require-parameters` | `provider.require_parameters` | only providers supporting the request's parameters, `tools` included |

The `--provider-*` flags pin the primary; each repeated `--fallback-provider-*`
flag pins the `--fallback-url` at the same position, like `--fallback-key`.
`--fallback-require-parameters` takes `1` or `0` so a position can be skipped.
Values are stored positionally, `;` between upstreams and `,` between slugs:

```bash
CCX_ROUTER_PROVIDER_ONLY=groq,fireworks;together
CCX_ROUTER_REQUIRE_PARAMETERS=1;;1
```

An empty position leaves that upstream's body untouched.

### Reliability and observability

Fallback only occurs for connection/timeouts and retryable HTTP responses. A
non-retryable client error is returned immediately, and a stream is never replayed
after it has started. Numeric or HTTP-date `Retry-After` is honored up to 30 seconds.

Advanced controls can be stored in `profile.env` or exported for one launch:

| Setting | Default | Meaning |
| --- | --- | --- |
| `CCX_ROUTER_ATTEMPTS_PER_UPSTREAM` | `1` | attempts per endpoint, 1–10 |
| `CCX_ROUTER_CONNECT_TIMEOUT_MS` | `5000` | TCP/TLS connection timeout |
| `CCX_ROUTER_RESPONSE_TIMEOUT_MS` | `30000` | headers/non-stream body timeout |
| `CCX_ROUTER_STREAM_IDLE_TIMEOUT_MS` | `120000` | maximum upstream stream silence |
| `CCX_ROUTER_STREAM_PING_INTERVAL_MS` | `15000` | downstream SSE keepalive interval |
| `CCX_ROUTER_STREAM_USAGE` | `auto` | `auto`, `on`, or `off` usage negotiation |
| `CCX_ROUTER_LOG` | `json` | secret-safe JSON events, or `off` |

Responses expose `x-request-id`, `x-ccx-upstream-index`, `x-ccx-attempt`, and the
upstream request ID when one is supplied. Read the same metadata with
`ccx logs <profile>`.

Fallback credentials are positional and explicit. A missing/empty fallback key
means no Authorization header; the primary credential is never copied to another
endpoint automatically.

### Installation and known gaps

- `./install.sh` first downloads a matching checksum-verified release binary and
  falls back to a local Rust build. Force a policy with
  `CCX_ROUTER_INSTALL=auto|download|build|skip`, or override the binary with
  `CCX_ROUTER_BIN`. Direct providers do not use the router.
- **The model must support tool use**; text-only models fail. Pick a tool-capable
  model (e.g. `qwen2.5-coder` for local).
- `cache_control` hints cannot guarantee prompt caching through generic Chat
  Completions. Extended thinking and `top_k` are rejected rather than silently lost.
- The local count-tokens endpoint is explicitly marked as estimated. Response
  usage is exact only when the upstream reports it.
- Retrying can duplicate billable work if the upstream processed a request but its
  response was lost. Keep the default of one attempt unless that tradeoff is acceptable.
