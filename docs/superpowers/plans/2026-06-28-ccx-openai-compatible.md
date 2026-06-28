# `ccx` — OpenAI-Compatible Providers via Local Router (`ccr`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Design spec:** [`../specs/2026-06-28-ccx-openai-compatible-design.md`](../specs/2026-06-28-ccx-openai-compatible-design.md) — read it first.

**Goal:** Add a new provider kind `openai-compatible` to `ccx`. For these profiles, `ccx` writes a per-profile `claude-code-router` (`ccr`) config, starts an isolated `ccr` instance on a free port, health-checks it, points `ANTHROPIC_BASE_URL` at it, runs `claude` as a child, and tears the router down on exit. Existing Anthropic-compatible profiles (`claude/minimax/glm/deepseek/kimi`) keep the untouched `exec claude` path.

**Architecture:** A `CCX_ROUTER=ccr` marker in `profile.env` switches `cmd_run` from the `exec claude` path to a new router-lifecycle path. Router-specific config is stored under `CCX_*` keys (already dropped from the env exported to `claude`). At launch, `ccx` generates `<profile>/router/.claude-code-router/config.json`, spawns `ccr` with `HOME=<profile>/router` (config + PID isolation) on a `ccx`-chosen free port, polls `/health`, then runs `claude`. A `trap` guarantees teardown.

**Tech stack:** Bash (dependency-free; free-port probe via `/dev/tcp`, health-check via `curl` with `/dev/tcp` fallback, random APIKEY via `/dev/urandom`). `ccr` is an external, version-pinned dependency (`CCX_CCR_BIN`, default `ccr`).

---

## Testability contract (new env overrides honored by `bin/ccx`)

- `CCX_CCR_BIN` — name/path of the `ccr` binary (default `ccr`). Tests set it to `true` or a fake script.
- `CCX_DRY_RUN=1` — for router profiles, print chosen port + `ccr` command + generated `config.json` (secrets masked) + final `claude` command, and exit `0` WITHOUT starting `ccr`.
- `CCX_ROUTER_TIMEOUT` — health-check timeout seconds (default 15). Tests set it low.
- Existing `CCX_HOME` / `CCX_CLAUDE_BIN` / `CCX_YES` continue to apply.

**Guardrails baked into the plan (from spec §2.1):** pin `ccr` v1.x in docs; `ccx` writes config itself; `ccx` manages the process (PID + trap), not `--daemon`; default transformer `enhancetool`.

---

## Task 1: OpenAI-compatible provider templates

**Files:**
- Create: `providers/openai.tmpl`, `providers/openrouter.tmpl`, `providers/ollama.tmpl`, `providers/vllm.tmpl`, `providers/lmstudio.tmpl`, `providers/custom-oai.tmpl`
- Modify: `tests/test_ccx.sh` (assert templates discoverable + carry `CCX_ROUTER`)

- [ ] **Step 1: Create the six templates** (values per spec §4.3). Each carries `CCX_ROUTER=ccr`, `CCX_ISOLATE=true`, `CCX_UPSTREAM_BASE_URL`, `CCX_TRANSFORMER`, and a `CCX_PROVIDER` hint. Example `providers/ollama.tmpl`:

```sh
CCX_ROUTER=ccr
CCX_ISOLATE=true
CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1/chat/completions
CCX_TRANSFORMER=enhancetool
ANTHROPIC_MODEL=qwen2.5-coder:32b
```

> Reuse the existing `ANTHROPIC_MODEL` key as the wizard's single-model seed (so `cmd_new`'s existing model logic applies); the launch path maps it into `CCX_MODEL_*`. `openrouter.tmpl` uses `CCX_TRANSFORMER=openrouter` and a `https://openrouter.ai/...` URL; `custom-oai.tmpl` leaves the URL blank.

- [ ] **Step 2: Failing test** — append to `tests/test_ccx.sh`:

```bash
# --- Task 1: openai-compatible templates ---
for p in openai openrouter ollama vllm lmstudio custom-oai; do
  tf="$REPO/providers/$p.tmpl"
  assert_eq "$([ -f "$tf" ] && echo y)" "y" "$p template exists"
done
assert_contains "$(cat "$REPO/providers/ollama.tmpl")" "CCX_ROUTER=ccr" "ollama is a router provider"
assert_contains "$(cat "$REPO/providers/openrouter.tmpl")" "CCX_TRANSFORMER=openrouter" "openrouter transformer set"
```

- [ ] **Step 3: Run** `bash tests/test_ccx.sh` → expect FAIL (templates absent).
- [ ] **Step 4:** Create the templates → run → PASS.
- [ ] **Step 5: Commit** `feat(ccx): openai-compatible provider templates (ccr router)`

---

## Task 2: `ccx new` writes router profiles

**Files:**
- Modify: `bin/ccx` (`cmd_new`: carry `CCX_ROUTER`, `CCX_UPSTREAM_*`, `CCX_TRANSFORMER`, `CCX_MODEL_*`; create `router/`)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Failing tests** — non-interactive create of an ollama profile:

```bash
# --- Task 2: new router profile ---
"$CCX" new --name lq --provider ollama --model qwen2.5-coder:7b --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new ollama exits 0"
penv="$CCX_HOME/profiles/lq/profile.env"
assert_mode "$penv" 600 "router profile.env is 600"
assert_contains "$(cat "$penv")" "CCX_ROUTER=ccr" "router marker written"
assert_contains "$(cat "$penv")" "CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1/chat/completions" "upstream url written"
assert_contains "$(cat "$penv")" "CCX_TRANSFORMER=enhancetool" "default transformer written"
assert_contains "$(cat "$penv")" "CCX_MODEL_DEFAULT=qwen2.5-coder:7b" "model mapped to default slot"
assert_eq "$([ -d "$CCX_HOME/profiles/lq/router" ] && echo y)" "y" "router home created"
assert_mode "$CCX_HOME/profiles/lq/router" 700 "router home is 700"
# openrouter requires a key
"$CCX" new --name orr --provider openrouter --model qwen/qwen3-coder --upstream-key sk-or-abc123456789 --yes >/dev/null 2>&1
assert_contains "$(cat "$CCX_HOME/profiles/orr/profile.env")" "CCX_UPSTREAM_API_KEY=sk-or-abc123456789" "openrouter key written"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement in `cmd_new`:**
  - Add flags: `--upstream-url`, `--upstream-key`, `--transformer`, `--think`, `--background`, `--longcontext`.
  - When the template (or `--provider`) carries `CCX_ROUTER`, read `CCX_UPSTREAM_BASE_URL`/`CCX_TRANSFORMER` from the template; prompt for upstream key via hidden input unless the template marks it blank/optional (ollama/vllm/lmstudio) or `--upstream-key` given.
  - Map the chosen single model into `CCX_MODEL_DEFAULT/THINK/BACKGROUND/LONGCONTEXT` (each defaults to the single model unless its flag overrides).
  - Write router keys into `profile.env`; create `router/` `chmod 700` (in addition to `home/`).
  - Keep the existing Anthropic-profile branch unchanged.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ccx): create openai-compatible (router) profiles`

---

## Task 3: Pure config generator (`kv → config.json`)

**Files:**
- Modify: `bin/ccx` (add `router_config_json` helper that prints the JSON to stdout)
- Modify: `tests/test_ccx.sh`

> Isolating generation as a pure, side-effect-free function makes it unit-testable without starting `ccr`.

- [ ] **Step 1: Failing test** — call the generator via a dry-run hook or a tiny internal subcommand `ccx __genconfig <profile> <port> <apikey>` (internal, undocumented, test-only):

```bash
# --- Task 3: config generator ---
cfg="$(CCX_CCR_BIN=true "$CCX" __genconfig lq 39000 testkey 2>&1)"
assert_contains "$cfg" "\"PORT\": 39000" "port injected"
assert_contains "$cfg" "\"HOST\": \"127.0.0.1\"" "host bound to loopback"
assert_contains "$cfg" "\"api_base_url\": \"http://localhost:11434/v1/chat/completions\"" "upstream url in provider"
assert_contains "$cfg" "\"use\": [\"enhancetool\"]" "transformer applied"
assert_contains "$cfg" "\"default\": \"upstream,qwen2.5-coder:7b\"" "router default mapped"
assert_contains "$cfg" "\"longContextThreshold\": 60000" "long-context threshold present"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `router_config_json` (reads the profile's `CCX_UPSTREAM_*`/`CCX_MODEL_*`/`CCX_TRANSFORMER`, takes port + apikey as args, prints JSON per spec §4.4; dedup `models[]`). Wire the `__genconfig` internal subcommand in `main()`. Emit JSON with `printf` (no `jq` dependency).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ccx): generate ccr config.json from router profile`

---

## Task 4: Free-port picker + helpers

**Files:**
- Modify: `bin/ccx` (`pick_free_port`, `gen_apikey`)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Failing test:**

```bash
# --- Task 4: free port ---
p="$("$CCX" __freeport 2>&1)"; rc=$?
assert_exit "$rc" 0 "freeport exits 0"
case "$p" in ''|*[!0-9]*) _fail "freeport numeric: got [$p]" ;; *) _pass "freeport numeric" ;; esac
TESTS_RUN=$((TESTS_RUN+1))
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:**
  - `pick_free_port`: iterate a range (e.g. 39000–39999); a port is free if `(exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null` FAILS (nothing listening). Print first free port; error if none.
  - `gen_apikey`: `head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-24` (fallback to `$RANDOM$RANDOM` if `/dev/urandom` unavailable).
  - Wire `__freeport` internal subcommand.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ccx): free-port picker and apikey generator`

---

## Task 5: Router dry-run path in `cmd_run`

**Files:**
- Modify: `bin/ccx` (`cmd_run`: detect `CCX_ROUTER`, branch; implement dry-run fully)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Failing test** — dry-run must show wiring and NOT start `ccr`:

```bash
# --- Task 5: router dry-run ---
out="$(CCX_DRY_RUN=1 CCX_CCR_BIN=true "$CCX" lq 2>&1)"; rc=$?
assert_exit "$rc" 0 "router dry-run exits 0"
assert_contains "$out" "CCX_DRY_RUN profile=lq provider=ollama router=ccr" "dry-run header shows router"
assert_contains "$out" "ANTHROPIC_BASE_URL=http://127.0.0.1:" "points claude at local router"
assert_contains "$out" "\"default\": \"upstream,qwen2.5-coder:7b\"" "dry-run prints generated config"
assert_not_contains "$out" "env: CCX_UPSTREAM_BASE_URL" "CCX_* not exported to claude"
# openrouter key must be masked
out2="$(CCX_DRY_RUN=1 CCX_CCR_BIN=true "$CCX" orr 2>&1)"
assert_not_contains "$out2" "sk-or-abc123456789" "upstream key masked in dry-run"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement the router branch in `cmd_run`:**
  - After parsing, if `CCX_ROUTER` is non-empty → router branch (else existing path untouched).
  - Compute `port="$(pick_free_port)"`, `apikey="$(gen_apikey)"`, `cfg="$(router_config_json <profile> "$port" "$apikey")"`.
  - If `CCX_DRY_RUN=1`: print header (`profile/provider/router`), the `ccr` command + `HOME`, the generated config with `api_key`/`APIKEY` masked, the `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` (masked) lines, the final `claude` command; exit `0`. Do NOT write files or start anything.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ccx): router-profile dry-run shows config + wiring without starting ccr`

---

## Task 6: Live router lifecycle (start, health-check, run, teardown)

**Files:**
- Modify: `bin/ccx` (`cmd_run` router branch: real launch)
- Create: `tests/fake_ccr.sh` (a stub that emulates `ccr start` + `/health`)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Build a fake `ccr`** (`tests/fake_ccr.sh`) that, on `start`, reads `HOME/.claude-code-router/config.json` for `PORT`, binds a trivial HTTP responder returning `200` on `/health` (e.g. via a small `nc`/bash loop), writes a PID file, and on `stop` kills it. Keep it dependency-light; if `nc` is unavailable, document the test as integration-tier and gate it.

- [ ] **Step 2: Failing test** — live start→health→teardown with a fake claude that exits 0:

```bash
# --- Task 6: router live lifecycle (gated on nc availability) ---
if command -v nc >/dev/null 2>&1; then
  export CCX_CCR_BIN="$HERE/fake_ccr.sh"
  export CCX_ROUTER_TIMEOUT=5
  out="$("$CCX" lq -p hi 2>&1)"; rc=$?
  assert_exit "$rc" 0 "router live run exits 0"
  assert_contains "$out" "exec: true" "claude launched"   # CCX_CLAUDE_BIN=true
  # no leftover ccr process / pid file
  assert_eq "$([ -f "$CCX_HOME/profiles/lq/router/.claude-code-router/.pid" ] && echo leftover || echo clean)" "clean" "router torn down"
  unset CCX_CCR_BIN CCX_ROUTER_TIMEOUT
fi
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement the live branch:**
  - Preflight: `command -v "$CCX_CCR_BIN"` (error + install hint if missing). Warn if `ccr version` looks like v2.x.
  - `mkdir -p "$pdir/router/.claude-code-router"`; write `config.json` (`chmod 600`).
  - Start: `HOME="$pdir/router" "$CCX_CCR_BIN" start &` ; `router_pid=$!` (see spec §5 note — if `ccr` self-detaches, also resolve PID file).
  - `trap 'router_teardown' EXIT INT TERM` where `router_teardown` kills `router_pid` and runs `HOME="$pdir/router" "$CCX_CCR_BIN" stop` (ignore errors), idempotent.
  - Health-check loop: up to `CCX_ROUTER_TIMEOUT` seconds, `curl -fsS http://127.0.0.1:$port/health` (fallback: `/dev/tcp` port-open probe). On timeout → teardown + `die`.
  - Export `ANTHROPIC_BASE_URL=http://127.0.0.1:$port`, `ANTHROPIC_AUTH_TOKEN=$apikey`, `CLAUDE_CONFIG_DIR=$pdir/home`.
  - Run `"$CCX_CLAUDE_BIN" "$@"` (NOT exec); capture exit code; teardown runs via trap; exit with claude's code.
- [ ] **Step 5: Run** → PASS (or SKIP cleanly without `nc`).
- [ ] **Step 6: Commit** `feat(ccx): manage ccr lifecycle (start, health-check, teardown) for router profiles`

---

## Task 7: `list` / `show` awareness + error handling

**Files:**
- Modify: `bin/ccx` (`cmd_list` shows router profiles sensibly; `cmd_show` masks `CCX_UPSTREAM_API_KEY`; preflight errors)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Failing tests:**

```bash
# --- Task 7: list/show/errors ---
assert_contains "$("$CCX" list 2>&1)" "lq" "router profile listed"
assert_contains "$("$CCX" show orr 2>&1)" "CCX_UPSTREAM_API_KEY=sk-or…6789" "upstream key masked in show"
assert_not_contains "$("$CCX" show orr 2>&1)" "sk-or-abc123456789" "raw upstream key hidden"
# missing ccr binary
out="$(CCX_CCR_BIN=definitely-not-a-binary "$CCX" lq -p hi 2>&1)"; rc=$?
assert_exit "$rc" 1 "missing ccr exits 1"
assert_contains "$out" "claude-code-router" "missing ccr gives install hint"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** extend `cmd_show` masking to `CCX_UPSTREAM_API_KEY` (reuse `mask_token`); make `cmd_list` print `router(<provider>)` or the upstream URL for router profiles; add the missing-`ccr` preflight error with an install/pin hint.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ccx): list/show support and preflight errors for router profiles`

---

## Task 8: GUI — provider picker + router fields

**Files:**
- Modify: `gui/` create/edit form (provider list + upstream URL/key, transformer, model)

> Scope to the Tauri/Svelte create-edit form so router profiles are creatable from the GUI and remain interchangeable with the CLI. Mirror the CLI field set (`CCX_UPSTREAM_*`, `CCX_TRANSFORMER`, `CCX_MODEL_*`). Follow the GUI's existing design spec ([`2026-06-26-ccx-gui-design.md`](../specs/2026-06-26-ccx-gui-design.md)).

- [ ] **Step 1:** Add the six openai-compatible providers to the picker; when a router provider is selected, reveal upstream URL, upstream key (masked), transformer (default from template), and model fields.
- [ ] **Step 2:** Persist via the same `profile.env` writer so CLI/GUI parity holds; verify `ccx show` reads a GUI-created router profile.
- [ ] **Step 3:** Manual check in `npm run tauri dev`.
- [ ] **Step 4: Commit** `feat(gui): create/edit openai-compatible router profiles`

---

## Task 9: Docs — README setup guide + Known gaps; flip roadmap checkboxes

**Files:**
- Modify: `README.md` (Supported providers; new "OpenAI-compatible via local router" section; Known gaps; check roadmap items)
- Modify: `site/` docs if they mirror the README

- [ ] **Step 1:** Document install of a pinned `ccr` (`npm i -g @musistudio/claude-code-router@<pinned-v1.x>`), the new providers, an Ollama and an OpenRouter walkthrough, and `CCX_DRY_RUN` inspection.
- [ ] **Step 2:** Add the **Known gaps** section verbatim from spec §12 (streaming tool-call caveat, tool-capable requirement, no prompt caching, v1.x pin).
- [ ] **Step 3:** Tick the now-done roadmap checkboxes in `README.md`.
- [ ] **Step 4: Commit** `docs(ccx): openai-compatible setup guide and known gaps`

---

## Task 10: Full-suite green run + manual smoke

- [ ] **Step 1:** `bash tests/test_ccx.sh` → every assertion ok, `0 failed` (router live test SKIPs cleanly if `nc` absent).
- [ ] **Step 2: Manual smoke (requires Ollama + a tool-capable model):**
  ```bash
  ./install.sh
  ccx new --provider ollama          # pick e.g. qwen2.5-coder:32b
  CCX_DRY_RUN=1 ccx <name>           # inspect generated config + wiring
  ccx <name> -p "create a file hello.txt with 'hi'"   # exercises tool-call round-trip
  ```
  Expected: `ccx` starts `ccr`, Claude Code performs the edit via the router, router is torn down on exit.
- [ ] **Step 3: Commit** any final fixes.

---

## Notes for the implementer

- **Don't touch the `exec claude` path.** The router branch is strictly additive, gated on `CCX_ROUTER`. Anthropic-compatible profiles must behave bit-for-bit as before.
- **`ccr` is external + version-sensitive.** Pin v1.x; treat `ccr start` foreground-vs-detached as something to verify against the pinned version (spec §5 note). Belt-and-suspenders teardown (kill PID + `ccr stop`).
- **No new hard dependencies in `ccx`.** Port probe via `/dev/tcp`, apikey via `/dev/urandom`, JSON via `printf`. `curl` is used if present with a `/dev/tcp` fallback for health-check.
- **Secrets:** `config.json` `chmod 600`, `router/` `chmod 700`, bind `127.0.0.1`, random per-launch `APIKEY` mirrored into `ANTHROPIC_AUTH_TOKEN`; mask everywhere (`list`/`show`/dry-run).
- **Tests stay offline:** dry-run + fake `ccr` + `CCX_CLAUDE_BIN=true`; the only live-process test is gated on `nc` and is integration-tier.
- **Default `enhancetool`** is a deliberate stability choice (disables tool-call streaming) — keep it the default; advanced users can override via `--transformer`/`ccx edit`.
