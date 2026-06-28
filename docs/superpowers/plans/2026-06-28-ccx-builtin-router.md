# `ccx` — Built-in Anthropic→OpenAI Translator (`ccx-router`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development. Steps use checkbox (`- [ ]`).

**Design spec:** [`../specs/2026-06-28-ccx-builtin-router-design.md`](../specs/2026-06-28-ccx-builtin-router-design.md) — read first.

**Goal:** Replace the third-party `ccr` dependency with a self-built Rust binary `ccx-router` that translates the Anthropic Messages API ⇄ OpenAI Chat Completions (streaming + tool calls), so `ccx` drives OpenAI-compatible backends with no external running service.

**Branch:** continue on `feat/openai-compatible-router` (pivot). Reuse its bash lifecycle skeleton + GUI form; rework the ccr-specific config generation, templates, and schema.

**Build/test:** `cargo` (network available; tokio/hyper/reqwest/rustls/serde_json cached, axum fetched). Translation logic is pure and fixture-tested. Bash via existing `tests/test_ccx.sh`. GUI via `cargo test gui/core`.

---

## Task R1: `router/` crate skeleton + types

- [ ] Create `router/Cargo.toml` (bin `ccx-router`) with deps: `tokio` (rt-multi-thread, macros), `axum`, `reqwest` (json, stream, rustls-tls), `serde`, `serde_json`, `futures`, `bytes`. Pin via committed `Cargo.lock`.
- [ ] `router/src/types.rs`: serde structs for the subset we translate — Anthropic (`MessagesRequest`, content blocks: text/tool_use/tool_result/image, `Tool`, `tool_choice`) and OpenAI (`ChatRequest`, `ChatMessage`, `ToolCall`, `ChatResponse`, streaming `ChatChunk`). Use `#[serde(rename_all)]` / `tag` enums for content blocks.
- [ ] `router/src/main.rs`: stub that parses `--port`/`--upstream-url` + env, prints, exits 0 (server wired in R5).
- [ ] **Test:** `cargo test --manifest-path router/Cargo.toml` compiles; a serde round-trip unit test for one Anthropic request fixture and one OpenAI response fixture (de/serialize).
- [ ] **Commit** `feat(router): ccx-router crate skeleton + Anthropic/OpenAI types`

## Task R2: Request translation (Anthropic → OpenAI), pure + fixture-tested

- [ ] `router/src/translate/request.rs`: `fn to_openai(req: &MessagesRequest) -> ChatRequest`.
  - `model` pass-through; `system` → leading `system` message; `max_tokens`, `temperature`, `stop_sequences`→`stop`, `stream`.
  - messages: text→content; assistant `tool_use`→`tool_calls` (arguments = JSON string of `input`); user `tool_result`→`role:"tool"` message (`tool_call_id`, content); `image` (base64)→`image_url` data URL.
  - `tools[]` → OpenAI `tools` (function/parameters from `input_schema`); `tool_choice` auto/any/tool → auto/required/named.
- [ ] **Tests (fixtures):** plain text turn; multi-turn with tool_use + tool_result; tools array; tool_choice variants; image block. Assert resulting `ChatRequest` JSON.
- [ ] **Commit** `feat(router): translate Anthropic requests to OpenAI`

## Task R3: Non-streaming response translation (OpenAI → Anthropic)

- [ ] `router/src/translate/response.rs`: `fn to_anthropic(resp: &ChatResponse, model: &str) -> MessagesResponse`.
  - `choices[0].message.content`→`text` block; `tool_calls`→`tool_use` blocks (`input` = parsed `arguments`).
  - `finish_reason`→`stop_reason` (stop→end_turn, length→max_tokens, tool_calls→tool_use).
  - `usage`→`input_tokens`/`output_tokens`; synthesize `id`, `role:"assistant"`, `type:"message"`.
- [ ] **Tests:** text-only; tool_calls; mixed; each finish_reason. Assert Anthropic JSON.
- [ ] **Commit** `feat(router): translate non-streaming OpenAI responses to Anthropic`

## Task R4: Streaming translation (OpenAI SSE → Anthropic SSE) — the hard part

- [ ] `router/src/translate/stream.rs`: a stateful translator that consumes OpenAI chunk JSONs and emits ordered Anthropic SSE events. **Track the currently-open content block + its type;** close it before opening another (correctly handles interleaved text↔tool_use — the PR #1356 failure mode).
  - emit `message_start` → per block `content_block_start`/`content_block_delta`(`text_delta` or `input_json_delta`)/`content_block_stop` → `message_delta`(stop_reason+usage) → `message_stop`.
  - accumulate tool_call `arguments` deltas as `input_json_delta`.
- [ ] **Tests (fixtures):** sequence of OpenAI chunks → assert exact ordered Anthropic event list, for: text-only stream; tool-call stream; **interleaved text→tool→text**; finish with tool_calls. This is the regression guard.
- [ ] **Commit** `feat(router): translate streaming OpenAI SSE to Anthropic events`

## Task R5: HTTP server + upstream forwarding

- [ ] `router/src/server.rs` (axum): 
  - middleware: require `Authorization: Bearer $CCX_ROUTER_API_KEY`; 401 otherwise.
  - `GET /health` → 200.
  - `POST /v1/messages`: translate→`reqwest` POST `${upstream}/chat/completions` with `Authorization: Bearer $CCX_ROUTER_UPSTREAM_KEY`. If `stream`, pipe upstream SSE through `stream.rs` as an axum SSE response; else translate JSON via `response.rs`.
  - `POST /v1/messages/count_tokens` → heuristic `{ "input_tokens": <chars/4> }`.
- [ ] `main.rs`: bind `127.0.0.1:<port>`, run.
- [ ] **Test:** integration test with a mock upstream (a tiny axum server in the test) — non-streaming round-trip + a streaming round-trip + 401 without auth.
- [ ] **Commit** `feat(router): http server forwarding to upstream (streaming + auth)`

## Task R6: Bash rework — drive `ccx-router` instead of `ccr`

- [ ] Templates: drop `CCX_TRANSFORMER`; `CCX_UPSTREAM_BASE_URL` becomes the `…/v1` base; set `ANTHROPIC_MODEL` seed. Keep `CCX_ROUTER` but value `builtin`.
- [ ] `cmd_new`: write `ANTHROPIC_DEFAULT_*` model slots (pass-through) instead of `CCX_MODEL_*`; keep `CCX_UPSTREAM_*`; drop transformer.
- [ ] `run_router`: find `ccx-router` (`CCX_ROUTER_BIN`, default PATH then `<repo>/router/target/release/ccx-router`); start `CCX_ROUTER_UPSTREAM_KEY=… CCX_ROUTER_API_KEY=… ccx-router --port <p> --upstream-url <url> &`; trap `kill`; health-check; export `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`. Remove `router_config_json` / ccr config writing.
- [ ] Update dry-run output (no JSON config; show ccx-router cmd + masked env).
- [ ] **Tests:** rewrite the OAI bash tests — router profile.env has `ANTHROPIC_DEFAULT_*` + `CCX_UPSTREAM_*`, no `CCX_TRANSFORMER`/`CCX_MODEL_*`; dry-run shows `ccx-router` wiring; live lifecycle uses a fake `ccx-router` (reuse python fake, rename) or the real binary. Keep CLI suite green.
- [ ] **Commit** `feat(ccx): drive built-in ccx-router; drop ccr config/transformer`

## Task R7: install.sh + docs

- [ ] `install.sh`: `cargo build --release --manifest-path router/Cargo.toml`; symlink/copy `ccx-router` to `~/.local/bin` (or let ccx find `router/target/release`). Guard when cargo absent (warn, skip — Anthropic profiles still work).
- [ ] README + site + gui docs: replace ccr prose with the built-in translator; prerequisite becomes Rust toolchain (build) instead of npm `ccr`; update known gaps (we own correctness now).
- [ ] **Commit** `docs(ccx): document built-in ccx-router; drop ccr references`

## Task R8: GUI rework

- [ ] core `provider.rs`/`profile.rs`: router templates carry `CCX_ROUTER=builtin` + `CCX_UPSTREAM_*`; profile uses `ANTHROPIC_DEFAULT_*` model slots (reuse existing opus/sonnet/haiku) + `CCX_UPSTREAM_*`; drop `transformer`/`model_*` fields. Update tests.
- [ ] `ProfileForm.svelte`: drop transformer field; upstream URL = `…/v1`; model fields reuse the Anthropic slots. Launch already delegates to `ccx <name>`.
- [ ] **Test:** `cargo test gui/core` green.
- [ ] **Commit** `feat(gui): built-in router profiles (drop transformer/model slots)`

## Task R9: Full green + manual smoke

- [ ] `cargo test router/`, `cargo test gui/core`, `bash tests/test_ccx.sh` all green.
- [ ] Manual smoke: build, `ccx new --provider ollama`, `ccx <name> -p "edit a file"` against real Ollama → tool-call round-trip works (streaming).
- [ ] **Commit** any final fixes.

---

## Notes
- **Don't touch the Anthropic `exec claude` path.** Router branch stays additive, gated on `CCX_ROUTER`.
- **Streaming correctness is the whole game** — invest in `stream.rs` fixtures, especially interleaved text/tool_use.
- Secrets (upstream key, local apikey) via **env**, never argv. Bind `127.0.0.1` only.
- Keep `ccx-router` dependency-light within reason; no telemetry, no outbound calls except the configured upstream.
