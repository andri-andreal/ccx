---
layout: ../../layouts/Docs.astro
title: Usage
description: Create, launch, and manage ccx profiles.
---

## Commands

```bash
ccx new                      # create a profile (wizard or flags)
ccx list                     # list profiles
ccx doctor work              # validate configuration and reachability
ccx doctor work --no-network # local-only checks
ccx certify work             # opt-in compatibility probes
ccx claude                   # Anthropic login; Opus plans, Sonnet executes (opusplan)
ccx claude --model haiku     # extra args are passed straight to claude
ccx glm -p "hello"           # run an isolated third-party profile
ccx logs local               # read the last 100 router events
ccx show <name>              # inspect a profile (token masked)
ccx edit <name>              # edit in $EDITOR
ccx rm <name>                # delete a profile
```

## Creating a profile

`ccx new` is an interactive wizard: pick a provider from an arrow-key list, then a
model. Where the provider exposes one, the wizard **fetches the live model list**
from its API (`/v1/models`) and gives you a type-to-filter picker; otherwise it
offers the template default or lets you type a model. It then asks for whatever the
provider needs — an API token (direct providers), or an upstream URL + optional key
(OpenAI-compatible providers).

The **model** you pick fills every model slot (Opus / Sonnet / Haiku) so each profile
is cleanly single-model. Skip the wizard entirely with flags:

```bash
ccx new --name m2 --provider minimax --model MiniMax-M2 --token <key> --yes
```

Router profiles can add ordered fallback endpoints and a bounded retry budget:

```bash
ccx new --name resilient --provider custom-oai --model my-model \
  --upstream-url https://primary.example/v1 --upstream-key "$PRIMARY_KEY" \
  --fallback-url https://backup.example/v1 --fallback-key "$BACKUP_KEY" \
  --router-attempts 2 --yes
```

Each `--fallback-key` is positional. A missing or empty entry sends no
Authorization header; primary credentials are never forwarded implicitly to a
fallback origin. The default is one attempt per upstream.

For automation, keep secrets out of argv and shell history:

```bash
printf '%s\n' "$API_KEY" | \
  ccx new --name m2 --provider minimax --model MiniMax-M3 --token-stdin --yes
```

Router equivalents are `--upstream-key-stdin` and repeatable
`--fallback-key-stdin`. Interactive prompts are hidden as well.

## Provider types

Every profile launches with the same `ccx <name>` command, but there are three paths:

- **Direct (Anthropic-compatible)** — `claude`, `minimax`, `glm`, `deepseek`, `kimi`,
  or a custom Anthropic endpoint. Claude Code talks to the provider directly via
  `ANTHROPIC_BASE_URL` + token.
- **OpenAI-compatible (via ccx-router)** — `openai`, `openrouter`, `sakana`,
  `custom-oai`. On launch, ccx starts the bundled `ccx-router` translator to bridge
  the Anthropic ⇄ OpenAI formats. See [Providers](/docs/providers/).
- **Local (via ccx-router)** — `ollama`, `vllm`, `lmstudio`. Same router path, but the
  upstream is a local server and no API key is needed — start the server first.

## How it works

- Profiles are environment variables wrapped around the `claude` binary — Claude Code
  itself is never modified.
- Third-party profiles are **isolated**: each gets its own `CLAUDE_CONFIG_DIR`, so
  credentials and history never mix with your Anthropic account.
- The `claude` profile uses your normal Anthropic login and `~/.claude` (plugins and
  skills intact) and defaults to `ANTHROPIC_MODEL=opusplan`.
- Router profiles bind their translator to `127.0.0.1`, protect the local hop with
  a fresh bearer token, and tear it down when Claude exits.

## Check before launch

`ccx doctor` performs setup and reachability checks without an inference prompt.
`ccx certify` is separate and consent-gated because it sends minimal provider requests. See
[Diagnostics](/docs/diagnostics/) for JSON automation and exact probe behavior.

## Router events

`ccx logs <profile> [--follow] [--lines N]` reads private JSON events from the
profile's `router.log`. Events include request ID, model, upstream index, attempt,
status, latency, and reported token counts; request bodies, URLs, and credentials
are not logged. Set `CCX_ROUTER_LOG=off` to disable them.

## Tokens stay safe

`profile.env` and `router.log` are written `chmod 600`. `ccx show`, dry-run output,
diagnostics, and the GUI mask credentials; they are never copied to the clipboard.
