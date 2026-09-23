---
layout: ../../layouts/Docs.astro
title: Roadmap
description: OpenAI-compatible providers via the built-in ccx-router — shipped and what's next.
---

## OpenAI-compatible providers via a built-in translator

Claude Code speaks the Anthropic Messages API (`/v1/messages`), so providers that
**only** offer the OpenAI Chat Completions format (OpenAI, OpenRouter, local Ollama /
vLLM / LM Studio, …) can't be used directly. `ccx` now ships its own translator,
**`ccx-router`** (a single Rust binary), and runs one per launch to bridge them — see
[Providers](/docs/providers/#openai-compatible-providers-via-a-local-router).

### Shipped

- [x] Self-built translator `ccx-router` (Rust, single binary) — no third-party
      running service. Translates Anthropic ⇄ OpenAI: requests, non-streaming and
      streaming responses, and tool-call round-trips (with correct interleaved
      text/tool_use block handling).
- [x] Provider kind `openai-compatible` (templates: `openai`, `openrouter`,
      `ollama`, `vllm`, `lmstudio`, `custom-oai`).
- [x] Profile schema: `CCX_ROUTER=builtin` + `CCX_UPSTREAM_BASE_URL` /
      `CCX_UPSTREAM_API_KEY`; models pass through via the `ANTHROPIC_DEFAULT_*`
      slots. Secrets stay in `profile.env` (`chmod 600`); the local hop is secured by
      a random per-launch token bound to `127.0.0.1`.
- [x] Launch lifecycle: pick a free port, start `ccx-router`, health-check
      (port-readiness), set `ANTHROPIC_BASE_URL`, run `claude`, and tear the router
      down on exit / Ctrl-C via `trap`.
- [x] `CCX_DRY_RUN` prints the `ccx-router` command + port + env wiring without starting it.
- [x] Tests: Rust fixture tests for every translation path (incl. streaming
      interleave) + an integration test against a mock upstream; bash dry-run and
      live-lifecycle tests.

- [x] Byte-safe incremental SSE parser (split UTF-8, CRLF, multiline events),
      parallel tool calls, canonical errors, bounded bodies, and stream keepalives.
- [x] Ordered fallback, bounded retry/timeouts, `Retry-After`, request metadata,
      private structured logs, and `ccx logs`.
- [x] `ccx doctor` plus consent-gated basic/streaming/tool certification with a
      versioned JSON report.

- [x] GUI: provider picker, router/upstream/fallback settings, local health badges,
      detailed diagnostics, and capability badges.
- [x] Wizard fetches each provider's live model list (`/v1/models`) with a
      type-to-filter picker; falls back to the template default / manual entry.
- [x] Checksum-verified prebuilt router release matrix for Linux x86_64/aarch64,
      macOS Intel/ARM64, and Windows x86_64, with provenance and optional Cosign.
- [x] Native desktop release matrix for Linux, macOS, and Windows, with optional
      signing/notarization credentials.
- [x] OpenRouter provider pinning (`only`, `order`, `require_parameters`),
      configured per upstream so a pinned OpenRouter endpoint can share a
      fallback chain with backends that do not know the field.
