---
layout: ../../layouts/Docs.astro
title: Usage
description: Create, launch, and manage ccx profiles.
---

## Commands

```bash
ccx new                      # create a profile (wizard or flags)
ccx list                     # list profiles
ccx claude                   # Anthropic login; Opus plans, Sonnet executes (opusplan)
ccx claude --model haiku     # extra args are passed straight to claude
ccx glm -p "hello"           # run an isolated third-party profile
ccx show <name>              # inspect a profile (token masked)
ccx edit <name>              # edit in $EDITOR
ccx rm <name>                # delete a profile
```

## Creating a profile

`ccx new` walks you through name, provider, model, and token:

```text
Profile name: m2
Provider [claude/minimax/glm/deepseek/kimi/custom]: minimax
Model [MiniMax-M3]: MiniMax-M2
API token (input hidden): ****
```

The **model** you pick — interactively or with `--model` — fills every model slot
(Opus / Sonnet / Haiku) so each profile is cleanly single-model. Pass flags to skip
the wizard:

```bash
ccx new --name m2 --provider minimax --model MiniMax-M2 --token <key> --yes
```

## How it works

- Profiles are environment variables wrapped around the `claude` binary — Claude Code
  itself is never modified.
- Third-party profiles are **isolated**: each gets its own `CLAUDE_CONFIG_DIR`, so
  credentials and history never mix with your Anthropic account.
- The `claude` profile uses your normal Anthropic login and `~/.claude` (plugins and
  skills intact) and defaults to `ANTHROPIC_MODEL=opusplan`.

## Tokens stay safe

`profile.env` is written `chmod 600`. `ccx show` and the GUI mask the token; it is
never copied to the clipboard.
