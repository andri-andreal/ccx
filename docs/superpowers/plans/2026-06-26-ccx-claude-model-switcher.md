# `ccx` Claude Code Profile Switcher — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ccx`, a single dependency-free Bash script that creates and launches named Claude Code profiles, each pointing `claude` at a different provider/model setup via environment variables.

**Architecture:** One executable `bin/ccx` dispatches subcommands (`new`/`list`/`show`/`edit`/`rm`/`help`) or, given a profile name, loads that profile's `profile.env`, exports its env vars (plus an isolated `CLAUDE_CONFIG_DIR` when `CCX_ISOLATE=true`), and `exec`s `claude` with all remaining args passed through. Provider defaults live in editable `providers/*.tmpl` files. Config lives under `${XDG_CONFIG_HOME:-~/.config}/ccx` (overridable via `CCX_HOME` for testing).

**Tech Stack:** Bash (POSIX-leaning, `bash` shebang), dependency-free shell test harness, `install.sh` symlinking into `~/.local/bin`.

---

## File Structure

```
ClaudeModelSwitcher/
  bin/ccx                       # the entire tool (one Bash script)
  providers/
    claude.tmpl                 # default KEY=VALUE per provider (no token)
    minimax.tmpl
    glm.tmpl
    deepseek.tmpl
    kimi.tmpl
  install.sh                    # symlink bin/ccx -> ~/.local/bin; copy templates -> ~/.config/ccx
  tests/
    helpers.sh                  # assertion helpers (no external deps)
    test_ccx.sh                 # the test suite (run: bash tests/test_ccx.sh)
  README.md
```

**Testability contract (env overrides honored by `bin/ccx`):**
- `CCX_HOME` — config root (default `${XDG_CONFIG_HOME:-$HOME/.config}/ccx`). Tests point this at a temp dir.
- `CCX_CLAUDE_BIN` — name/path of the claude binary (default `claude`). Tests set it to `true`.
- `CCX_DRY_RUN=1` — print the resolved env + final command and exit 0 instead of `exec`ing.
- `CCX_YES=1` — auto-confirm destructive prompts (used by `rm` tests).
- Provider templates are searched at `$CCX_HOME/providers/<p>.tmpl`, then `<script_dir>/../providers/<p>.tmpl` (repo fallback, used by tests pre-install).

---

## Task 1: Bootstrap — repo skeleton, test harness, `ccx help` + dispatch

**Files:**
- Create: `tests/helpers.sh`
- Create: `tests/test_ccx.sh`
- Create: `bin/ccx`

- [ ] **Step 1: Write the test harness helpers**

Create `tests/helpers.sh`:

```bash
# tests/helpers.sh — dependency-free assertions
TESTS_RUN=0
TESTS_FAIL=0

_pass() { printf 'ok   %s\n' "$1"; }
_fail() { TESTS_FAIL=$((TESTS_FAIL + 1)); printf 'FAIL %s\n' "$1" >&2; }

assert_eq() {
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" = "$2" ]; then _pass "${3:-eq}"; else _fail "${3:-eq}: expected [$2] got [$1]"; fi
}
assert_contains() {
  TESTS_RUN=$((TESTS_RUN + 1))
  case "$1" in *"$2"*) _pass "${3:-contains}" ;; *) _fail "${3:-contains}: [$1] lacks [$2]" ;; esac
}
assert_not_contains() {
  TESTS_RUN=$((TESTS_RUN + 1))
  case "$1" in *"$2"*) _fail "${3:-not_contains}: [$1] still has [$2]" ;; *) _pass "${3:-not_contains}" ;; esac
}
assert_exit() {
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" -eq "$2" ]; then _pass "${3:-exit}"; else _fail "${3:-exit}: expected $2 got $1"; fi
}
assert_mode() {
  TESTS_RUN=$((TESTS_RUN + 1))
  m="$(stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1")"
  if [ "$m" = "$2" ]; then _pass "${3:-mode}"; else _fail "${3:-mode}: $1 is $m not $2"; fi
}
finish() {
  printf '\n%d assertions, %d failed\n' "$TESTS_RUN" "$TESTS_FAIL"
  [ "$TESTS_FAIL" -eq 0 ]
}
```

- [ ] **Step 2: Write the failing test for `ccx help`**

Create `tests/test_ccx.sh`:

```bash
#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
CCX="$REPO/bin/ccx"
. "$HERE/helpers.sh"

CCX_HOME="$(mktemp -d)"
export CCX_HOME
export CCX_CLAUDE_BIN=true
trap 'rm -rf "$CCX_HOME"' EXIT

# --- Task 1: help + dispatch ---
out="$("$CCX" help 2>&1)"; rc=$?
assert_exit "$rc" 0 "help exits 0"
assert_contains "$out" "Usage: ccx" "help shows usage"
assert_contains "$out" "new" "help lists new"

out="$("$CCX" 2>&1)"; rc=$?
assert_exit "$rc" 0 "no-arg shows help"
assert_contains "$out" "Usage: ccx" "no-arg shows usage"

finish
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `bin/ccx` does not exist yet (`No such file or directory`), assertions fail.

- [ ] **Step 4: Write minimal `bin/ccx`**

Create `bin/ccx`:

```bash
#!/usr/bin/env bash
set -euo pipefail

CCX_HOME="${CCX_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/ccx}"
CCX_CLAUDE_BIN="${CCX_CLAUDE_BIN:-claude}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { printf 'ccx: %s\n' "$1" >&2; exit "${2:-1}"; }

cmd_help() {
  cat <<'EOF'
Usage: ccx <profile> [claude args...]   launch claude with a profile
       ccx new                          create a profile (wizard / flags)
       ccx list                         list profiles
       ccx show <profile>               show a profile (token masked)
       ccx edit <profile>               edit a profile in $EDITOR
       ccx rm <profile>                 remove a profile
       ccx help                         this help
EOF
}

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    help | -h | --help) cmd_help ;;
    *) die "not implemented yet: $cmd" ;;
  esac
}

main "$@"
```

- [ ] **Step 5: Make it executable and run the test to verify it passes**

Run: `chmod +x bin/ccx && bash tests/test_ccx.sh`
Expected: PASS — all Task 1 assertions ok, `0 failed`.

- [ ] **Step 6: Commit**

```bash
git add bin/ccx tests/helpers.sh tests/test_ccx.sh
git commit -m "feat(ccx): bootstrap script, help, and shell test harness"
```

---

## Task 2: Provider templates + config paths + `ccx list` (empty)

**Files:**
- Create: `providers/claude.tmpl`, `providers/minimax.tmpl`, `providers/glm.tmpl`, `providers/deepseek.tmpl`, `providers/kimi.tmpl`
- Modify: `bin/ccx` (add path helpers + `cmd_list` + dispatch)
- Modify: `tests/test_ccx.sh` (add list-empty test)

- [ ] **Step 1: Create the five provider templates**

`providers/claude.tmpl`:
```sh
CCX_ISOLATE=false
ANTHROPIC_MODEL=opusplan
```

`providers/minimax.tmpl`:
```sh
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
ANTHROPIC_MODEL=MiniMax-M2
ANTHROPIC_DEFAULT_OPUS_MODEL=MiniMax-M2
ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M2
ANTHROPIC_DEFAULT_HAIKU_MODEL=MiniMax-M2
```

`providers/glm.tmpl`:
```sh
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic
ANTHROPIC_MODEL=glm-4.6
ANTHROPIC_DEFAULT_OPUS_MODEL=glm-4.6
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.6
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.5-air
```

`providers/deepseek.tmpl`:
```sh
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-chat
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-reasoner
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-chat
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-chat
```

`providers/kimi.tmpl`:
```sh
CCX_ISOLATE=true
ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic
ANTHROPIC_MODEL=kimi-k2-0905-preview
ANTHROPIC_DEFAULT_OPUS_MODEL=kimi-k2-0905-preview
ANTHROPIC_DEFAULT_SONNET_MODEL=kimi-k2-0905-preview
ANTHROPIC_DEFAULT_HAIKU_MODEL=kimi-k2-turbo-preview
```

- [ ] **Step 2: Add the failing test for `ccx list` (empty)**

Append to `tests/test_ccx.sh` before `finish`:

```bash
# --- Task 2: list empty + template lookup ---
out="$("$CCX" list 2>&1)"; rc=$?
assert_exit "$rc" 0 "list empty exits 0"
assert_contains "$out" "No profiles yet" "list shows empty hint"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `ccx list` currently hits `not implemented yet: list`.

- [ ] **Step 4: Add path helpers, template lookup, and `cmd_list` to `bin/ccx`**

In `bin/ccx`, add these helpers after `die()`:

```bash
profiles_dir() { printf '%s/profiles' "$CCX_HOME"; }
profile_dir()  { printf '%s/profiles/%s' "$CCX_HOME" "$1"; }
profile_env()  { printf '%s/profiles/%s/profile.env' "$CCX_HOME" "$1"; }

template_file() { # echo template path for a provider, or nothing
  if [ -f "$CCX_HOME/providers/$1.tmpl" ]; then
    printf '%s/providers/%s.tmpl' "$CCX_HOME" "$1"
  elif [ -f "$SCRIPT_DIR/../providers/$1.tmpl" ]; then
    printf '%s/../providers/%s.tmpl' "$SCRIPT_DIR" "$1"
  fi
}

kv_get() { # file key -> value (first match)
  awk -F= -v k="$2" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$1"
}
```

Add `cmd_list`:

```bash
cmd_list() {
  local pd; pd="$(profiles_dir)"
  local found=0 d name penv prov url model
  if [ -d "$pd" ]; then
    for d in "$pd"/*/; do
      [ -d "$d" ] || continue
      penv="$d/profile.env"; [ -f "$penv" ] || continue
      found=1
      name="$(basename "$d")"
      prov="$(kv_get "$penv" CCX_PROVIDER)"
      url="$(kv_get "$penv" ANTHROPIC_BASE_URL)"
      model="$(kv_get "$penv" ANTHROPIC_MODEL)"
      printf '%-16s %-10s %-34s %s\n' "$name" "${prov:-?}" "${url:-(anthropic login)}" "${model:-}"
    done
  fi
  [ "$found" -eq 1 ] || printf 'No profiles yet. Create one: ccx new\n'
}
```

Update `main()`'s case to route `list`:

```bash
    list | ls) cmd_list ;;
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — Task 1 + Task 2 assertions ok, `0 failed`.

- [ ] **Step 6: Commit**

```bash
git add bin/ccx providers tests/test_ccx.sh
git commit -m "feat(ccx): provider templates, path helpers, and list command"
```

---

## Task 3: `ccx new` — create a profile from flags (writes profile.env, chmod 600)

**Files:**
- Modify: `bin/ccx` (add `cmd_new` + dispatch)
- Modify: `tests/test_ccx.sh` (add new-profile tests)

- [ ] **Step 1: Add failing tests for `ccx new` (claude + glm)**

Append to `tests/test_ccx.sh` before `finish`:

```bash
# --- Task 3: new ---
"$CCX" new --name myclaude --provider claude --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new claude exits 0"
penv="$CCX_HOME/profiles/myclaude/profile.env"
assert_eq "$([ -f "$penv" ] && echo y)" "y" "claude profile.env created"
assert_mode "$penv" 600 "claude profile.env is 600"
assert_contains "$(cat "$penv")" "CCX_PROVIDER=claude" "claude provider recorded"
assert_contains "$(cat "$penv")" "ANTHROPIC_MODEL=opusplan" "claude default model opusplan"
assert_contains "$(cat "$penv")" "CCX_ISOLATE=false" "claude not isolated"

"$CCX" new --name myglm --provider glm --token sk-test-123456789 --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new glm exits 0"
gpenv="$CCX_HOME/profiles/myglm/profile.env"
assert_mode "$gpenv" 600 "glm profile.env is 600"
assert_contains "$(cat "$gpenv")" "ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" "glm base url from template"
assert_contains "$(cat "$gpenv")" "ANTHROPIC_AUTH_TOKEN=sk-test-123456789" "glm token written"
assert_contains "$(cat "$gpenv")" "CCX_ISOLATE=true" "glm isolated by default"
assert_mode "$CCX_HOME/profiles/myglm/home" 700 "glm home dir is 700"

# duplicate name rejected
"$CCX" new --name myglm --provider glm --token x --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 1 "duplicate name rejected"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `ccx new` hits `not implemented yet: new`.

- [ ] **Step 3: Add `cmd_new` to `bin/ccx`**

```bash
cmd_new() {
  local name="" provider="" base_url="" token="" model="" \
        m_opus="" m_sonnet="" m_haiku="" isolate="" yes=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --provider) provider="$2"; shift 2 ;;
      --base-url) base_url="$2"; shift 2 ;;
      --token) token="$2"; shift 2 ;;
      --model) model="$2"; shift 2 ;;
      --opus) m_opus="$2"; shift 2 ;;
      --sonnet) m_sonnet="$2"; shift 2 ;;
      --haiku) m_haiku="$2"; shift 2 ;;
      --isolate) isolate=true; shift ;;
      --no-isolate) isolate=false; shift ;;
      --yes | -y) yes=1; shift ;;
      *) die "new: unknown option: $1" ;;
    esac
  done

  [ -n "$name" ] || { printf 'Profile name: '; read -r name; }
  printf '%s' "$name" | grep -qE '^[A-Za-z0-9_-]+$' || die "invalid name: $name"
  [ -e "$(profile_dir "$name")" ] && die "profile already exists: $name"
  [ -n "$provider" ] || { printf 'Provider [claude/minimax/glm/deepseek/kimi/custom]: '; read -r provider; }

  local tf; tf="$(template_file "$provider")"
  if [ -n "$tf" ]; then
    [ -n "$base_url" ] || base_url="$(kv_get "$tf" ANTHROPIC_BASE_URL)"
    [ -n "$model" ]    || model="$(kv_get "$tf" ANTHROPIC_MODEL)"
    [ -n "$m_opus" ]   || m_opus="$(kv_get "$tf" ANTHROPIC_DEFAULT_OPUS_MODEL)"
    [ -n "$m_sonnet" ] || m_sonnet="$(kv_get "$tf" ANTHROPIC_DEFAULT_SONNET_MODEL)"
    [ -n "$m_haiku" ]  || m_haiku="$(kv_get "$tf" ANTHROPIC_DEFAULT_HAIKU_MODEL)"
    [ -n "$isolate" ]  || isolate="$(kv_get "$tf" CCX_ISOLATE)"
  fi
  [ -n "$isolate" ] || isolate=true

  if [ "$provider" != "claude" ] && [ -z "$token" ]; then
    printf 'API token (input hidden): '; read -rs token; printf '\n'
  fi

  local pdir penv
  pdir="$(profile_dir "$name")"
  mkdir -p "$pdir"
  penv="$pdir/profile.env"
  {
    printf '# ccx profile: %s\n' "$name"
    printf 'CCX_PROVIDER=%s\n' "$provider"
    printf 'CCX_ISOLATE=%s\n' "$isolate"
    [ -n "$base_url" ] && printf 'ANTHROPIC_BASE_URL=%s\n' "$base_url"
    [ -n "$token" ]    && printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$token"
    [ -n "$model" ]    && printf 'ANTHROPIC_MODEL=%s\n' "$model"
    [ -n "$m_opus" ]   && printf 'ANTHROPIC_DEFAULT_OPUS_MODEL=%s\n' "$m_opus"
    [ -n "$m_sonnet" ] && printf 'ANTHROPIC_DEFAULT_SONNET_MODEL=%s\n' "$m_sonnet"
    [ -n "$m_haiku" ]  && printf 'ANTHROPIC_DEFAULT_HAIKU_MODEL=%s\n' "$m_haiku"
  } > "$penv"
  chmod 600 "$penv"
  if [ "$isolate" = "true" ]; then mkdir -p "$pdir/home"; chmod 700 "$pdir/home"; fi

  printf 'Created profile "%s". Run it: ccx %s\n' "$name" "$name"
}
```

Add dispatch in `main()`:

```bash
    new) shift; cmd_new "$@" ;;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add bin/ccx tests/test_ccx.sh
git commit -m "feat(ccx): new command creates profiles from templates and flags"
```

---

## Task 4: `ccx <profile>` — load env and dry-run (claude, no isolation)

**Files:**
- Modify: `bin/ccx` (add `cmd_run` + default dispatch)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Add a failing dry-run test for the claude profile**

Append before `finish` (assumes `myclaude` from Task 3 exists):

```bash
# --- Task 4: run (dry-run), no isolation ---
out="$(CCX_DRY_RUN=1 "$CCX" myclaude 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run claude exits 0"
assert_contains "$out" "CCX_DRY_RUN profile=myclaude" "dry-run header shows profile"
assert_contains "$out" "env: ANTHROPIC_MODEL=opusplan" "exports opusplan"
assert_not_contains "$out" "CLAUDE_CONFIG_DIR" "claude profile not isolated"
assert_not_contains "$out" "env: CCX_PROVIDER" "CCX_* not exported to claude"
assert_contains "$out" "exec: true" "exec uses CCX_CLAUDE_BIN"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — default case still `die`s with `not implemented yet: myclaude`.

- [ ] **Step 3: Add `cmd_run` to `bin/ccx` and route unknown commands to it**

```bash
cmd_run() {
  local name="${1:-}"
  [ -n "$name" ] || { cmd_help; exit 0; }
  shift || true
  local penv; penv="$(profile_env "$name")"
  if [ ! -f "$penv" ]; then
    printf 'ccx: unknown profile: %s\n\nAvailable profiles:\n' "$name" >&2
    cmd_list >&2
    exit 1
  fi

  local isolate=false provider="" key val
  local -a exports=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in '' | '#'*) continue ;; esac
    key="${line%%=*}"; val="${line#*=}"
    case "$key" in
      CCX_ISOLATE) isolate="$val" ;;
      CCX_PROVIDER) provider="$val" ;;
      CCX_*) : ;;
      *) export "$key=$val"; exports+=("$key=$val") ;;
    esac
  done < "$penv"

  if [ "$isolate" = "true" ]; then
    local home; home="$(profile_dir "$name")/home"
    mkdir -p "$home"; chmod 700 "$home"
    export CLAUDE_CONFIG_DIR="$home"
    exports+=("CLAUDE_CONFIG_DIR=$home")
  fi

  if [ "${CCX_DRY_RUN:-}" = "1" ]; then
    printf 'CCX_DRY_RUN profile=%s provider=%s\n' "$name" "$provider"
    local e k
    for e in "${exports[@]}"; do
      k="${e%%=*}"
      case "$k" in
        *TOKEN* | *API_KEY*) printf 'env: %s=%s\n' "$k" "$(mask_token "${e#*=}")" ;;
        *) printf 'env: %s\n' "$e" ;;
      esac
    done
    printf 'exec: %s %s\n' "$CCX_CLAUDE_BIN" "$*"
    exit 0
  fi

  command -v "$CCX_CLAUDE_BIN" >/dev/null 2>&1 \
    || die "claude not found (looked for '$CCX_CLAUDE_BIN'). Install Claude Code or set CCX_CLAUDE_BIN."
  exec "$CCX_CLAUDE_BIN" "$@"
}
```

Add the `mask_token` helper (used by dry-run and later by `show`) near the other helpers:

```bash
mask_token() {
  local t="$1"
  if [ "${#t}" -le 8 ]; then printf '****'; else printf '%s…%s' "${t:0:3}" "${t: -4}"; fi
}
```

Change `main()`'s default case from the `die` line to:

```bash
    *) cmd_run "$@" ;;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add bin/ccx tests/test_ccx.sh
git commit -m "feat(ccx): run profiles with env loading and dry-run output"
```

---

## Task 5: Isolation — `CLAUDE_CONFIG_DIR` set for `CCX_ISOLATE=true`

**Files:**
- Modify: `tests/test_ccx.sh` (isolation assertions; logic already added in Task 4)

> Task 4 already implemented the isolation branch. This task adds the explicit test proving it, and verifies token masking in dry-run output.

- [ ] **Step 1: Add failing/confirming tests for the glm profile**

Append before `finish` (uses `myglm` from Task 3):

```bash
# --- Task 5: isolation + token masking ---
out="$(CCX_DRY_RUN=1 "$CCX" myglm 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run glm exits 0"
assert_contains "$out" "env: CLAUDE_CONFIG_DIR=$CCX_HOME/profiles/myglm/home" "glm isolated config dir"
assert_contains "$out" "env: ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" "glm base url exported"
assert_contains "$out" "ANTHROPIC_AUTH_TOKEN=sk-…6789" "token masked in dry-run"
assert_not_contains "$out" "sk-test-123456789" "raw token not printed"
assert_eq "$([ -d "$CCX_HOME/profiles/myglm/home" ] && echo y)" "y" "home dir exists"
```

- [ ] **Step 2: Run the test**

Run: `bash tests/test_ccx.sh`
Expected: PASS immediately (logic exists from Task 4). If `token masked` fails, confirm `mask_token` produces `sk-…6789` for `sk-test-123456789` (first 3 = `sk-`, last 4 = `6789`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_ccx.sh
git commit -m "test(ccx): verify profile isolation and token masking"
```

---

## Task 6: Argument pass-through to `claude`

**Files:**
- Modify: `tests/test_ccx.sh`

> `cmd_run` already does `exec "$CCX_CLAUDE_BIN" "$@"` and prints `exec: ... $*` in dry-run. This task locks the behavior with a test.

- [ ] **Step 1: Add a failing/confirming pass-through test**

Append before `finish`:

```bash
# --- Task 6: arg pass-through ---
out="$(CCX_DRY_RUN=1 "$CCX" myclaude --model haiku -p "hi there" 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run with args exits 0"
assert_contains "$out" "exec: true --model haiku -p hi there" "args forwarded verbatim"
```

- [ ] **Step 2: Run the test**

Run: `bash tests/test_ccx.sh`
Expected: PASS (behavior already present).

- [ ] **Step 3: Commit**

```bash
git add tests/test_ccx.sh
git commit -m "test(ccx): verify claude argument pass-through"
```

---

## Task 7: `ccx show` with token masking

**Files:**
- Modify: `bin/ccx` (add `cmd_show` + dispatch)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Add failing tests for `ccx show`**

Append before `finish`:

```bash
# --- Task 7: show ---
out="$("$CCX" show myglm 2>&1)"; rc=$?
assert_exit "$rc" 0 "show exits 0"
assert_contains "$out" "ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" "show prints base url"
assert_contains "$out" "ANTHROPIC_AUTH_TOKEN=sk-…6789" "show masks token"
assert_not_contains "$out" "sk-test-123456789" "show hides raw token"

out="$("$CCX" show nope 2>&1)"; rc=$?
assert_exit "$rc" 1 "show unknown exits 1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `show` routes to `cmd_run` (unknown profile `show`) until dispatch is added.

- [ ] **Step 3: Add `cmd_show` and dispatch**

```bash
cmd_show() {
  local name="${1:-}"; [ -n "$name" ] || die "show: profile name required"
  local penv; penv="$(profile_env "$name")"
  [ -f "$penv" ] || die "unknown profile: $name"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ANTHROPIC_AUTH_TOKEN=* | ANTHROPIC_API_KEY=*)
        printf '%s=%s\n' "${line%%=*}" "$(mask_token "${line#*=}")" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < "$penv"
}
```

Add dispatch in `main()`:

```bash
    show) shift; cmd_show "$@" ;;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add bin/ccx tests/test_ccx.sh
git commit -m "feat(ccx): show command with masked token output"
```

---

## Task 8: `ccx edit` and `ccx rm`

**Files:**
- Modify: `bin/ccx` (add `cmd_edit`, `cmd_rm` + dispatch)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Add failing tests for `rm` (and edit re-chmod)**

Append before `finish`:

```bash
# --- Task 8: edit + rm ---
# edit: use a fake editor that appends a line, then verify perms preserved
export EDITOR="$HERE/fake_editor.sh"
cat > "$HERE/fake_editor.sh" <<'EOS'
#!/usr/bin/env bash
printf '\n# edited\n' >> "$1"
EOS
chmod +x "$HERE/fake_editor.sh"
"$CCX" edit myclaude >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "edit exits 0"
assert_contains "$(cat "$CCX_HOME/profiles/myclaude/profile.env")" "# edited" "edit applied"
assert_mode "$CCX_HOME/profiles/myclaude/profile.env" 600 "edit keeps 600"
rm -f "$HERE/fake_editor.sh"; unset EDITOR

# rm with CCX_YES
CCX_YES=1 "$CCX" rm myglm >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "rm exits 0"
assert_eq "$([ -d "$CCX_HOME/profiles/myglm" ] && echo y || echo n)" "n" "profile dir removed"

# rm unknown
CCX_YES=1 "$CCX" rm ghost >/dev/null 2>&1; rc=$?
assert_exit "$rc" 1 "rm unknown exits 1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `edit`/`rm` route to `cmd_run` (unknown profile) until dispatched.

- [ ] **Step 3: Add `cmd_edit`, `cmd_rm`, and dispatch**

```bash
cmd_edit() {
  local name="${1:-}"; [ -n "$name" ] || die "edit: profile name required"
  local penv; penv="$(profile_env "$name")"
  [ -f "$penv" ] || die "unknown profile: $name"
  "${EDITOR:-vi}" "$penv"
  chmod 600 "$penv"
}

cmd_rm() {
  local name="${1:-}"; [ -n "$name" ] || die "rm: profile name required"
  local pdir; pdir="$(profile_dir "$name")"
  [ -d "$pdir" ] || die "unknown profile: $name"
  if [ "${CCX_YES:-}" != "1" ]; then
    printf 'Remove profile "%s" and its isolated config? [y/N] ' "$name"
    local ans; read -r ans
    case "$ans" in y | Y) ;; *) printf 'Aborted.\n'; return 0 ;; esac
  fi
  rm -rf "$pdir"
  printf 'Removed profile "%s".\n' "$name"
}
```

Add dispatch in `main()`:

```bash
    edit) shift; cmd_edit "$@" ;;
    rm | remove) shift; cmd_rm "$@" ;;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add bin/ccx tests/test_ccx.sh
git commit -m "feat(ccx): edit and rm commands"
```

---

## Task 9: Error handling — unknown profile listing, loose-permission warning

**Files:**
- Modify: `bin/ccx` (permission warning in `cmd_run`)
- Modify: `tests/test_ccx.sh`

- [ ] **Step 1: Add failing tests for unknown-profile output and perms warning**

Append before `finish`:

```bash
# --- Task 9: errors ---
out="$("$CCX" doesnotexist 2>&1)"; rc=$?
assert_exit "$rc" 1 "unknown profile exits 1"
assert_contains "$out" "unknown profile: doesnotexist" "unknown profile message"
assert_contains "$out" "Available profiles" "unknown profile lists available"

# loose permission warning
"$CCX" new --name loose --provider glm --token sk-loose-000011112222 --yes >/dev/null 2>&1
chmod 644 "$CCX_HOME/profiles/loose/profile.env"
out="$(CCX_DRY_RUN=1 "$CCX" loose 2>&1)"; rc=$?
assert_exit "$rc" 0 "loose profile still runs"
assert_contains "$out" "warning" "warns on loose permissions"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — no permission warning emitted yet (unknown-profile listing already passes from Task 4).

- [ ] **Step 3: Add the permission warning to `cmd_run`**

In `cmd_run`, immediately after the `if [ ! -f "$penv" ]; then ... fi` block, insert:

```bash
  local mode; mode="$(stat -c '%a' "$penv" 2>/dev/null || stat -f '%Lp' "$penv")"
  if [ "$mode" != "600" ]; then
    printf 'ccx: warning: %s has mode %s (expected 600); run: chmod 600 %s\n' "$penv" "$mode" "$penv" >&2
  fi
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add bin/ccx tests/test_ccx.sh
git commit -m "feat(ccx): warn on loose profile permissions; harden errors"
```

---

## Task 10: `install.sh` + PATH guidance

**Files:**
- Create: `install.sh`
- Modify: `tests/test_ccx.sh` (install into a temp HOME, verify symlink + templates)

- [ ] **Step 1: Add a failing test for `install.sh`**

Append before `finish`:

```bash
# --- Task 10: install ---
FAKE_HOME="$(mktemp -d)"
out="$(HOME="$FAKE_HOME" XDG_CONFIG_HOME="$FAKE_HOME/.config" bash "$REPO/install.sh" 2>&1)"; rc=$?
assert_exit "$rc" 0 "install exits 0"
assert_eq "$([ -L "$FAKE_HOME/.local/bin/ccx" ] && echo y || echo n)" "y" "ccx symlinked into ~/.local/bin"
assert_eq "$([ -f "$FAKE_HOME/.config/ccx/providers/glm.tmpl" ] && echo y || echo n)" "y" "templates copied"
rm -rf "$FAKE_HOME"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash tests/test_ccx.sh`
Expected: FAIL — `install.sh` does not exist.

- [ ] **Step 3: Create `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
BINDIR="$HOME/.local/bin"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/ccx"

mkdir -p "$BINDIR" "$CFG/providers" "$CFG/profiles"
ln -sf "$REPO/bin/ccx" "$BINDIR/ccx"
chmod +x "$REPO/bin/ccx"
for t in "$REPO"/providers/*.tmpl; do
  [ -e "$t" ] || continue
  cp -n "$t" "$CFG/providers/" 2>/dev/null || true
done
chmod 700 "$CFG" "$CFG/profiles"

printf 'Installed ccx -> %s\n' "$BINDIR/ccx"
case ":$PATH:" in
  *":$BINDIR:"*) ;;
  *)
    printf '\n%s is not on your PATH. Add it:\n' "$BINDIR"
    printf '  fish: fish_add_path %s\n' "$BINDIR"
    printf '  zsh : echo '\''export PATH="%s:$PATH"'\'' >> ~/.zshrc && exec zsh\n' "$BINDIR" ;;
esac
printf '\nNext: ccx new   (create your first profile)\n'
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash tests/test_ccx.sh`
Expected: PASS — `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_ccx.sh
git commit -m "feat(ccx): install script with symlink, template copy, and PATH guidance"
```

---

## Task 11: README + final full-suite green run

**Files:**
- Create: `README.md`
- Modify: `tests/test_ccx.sh` (no new logic; just final run)

- [ ] **Step 1: Write `README.md`**

```markdown
# ccx — Claude Code profile/provider switcher

`ccx` launches the `claude` CLI under named profiles. Each profile points Claude Code
at a provider + model setup via environment variables — no changes to Claude Code itself.

## Install
```bash
./install.sh
# add ~/.local/bin to PATH if prompted
```

## Use
```bash
ccx new                      # create a profile (claude / minimax / glm / deepseek / kimi / custom)
ccx list                     # list profiles
ccx claude                   # Anthropic login; Opus plans, Sonnet executes (opusplan)
ccx claude --model haiku     # extra args are passed straight to claude
ccx glm -p "hello"           # run an isolated third-party profile
ccx show <name>              # inspect a profile (token masked)
ccx edit <name>              # edit in $EDITOR
ccx rm <name>                # delete a profile
```

## How it works
- Profiles live in `~/.config/ccx/profiles/<name>/profile.env` (`chmod 600`).
- Third-party profiles are isolated: each gets its own `CLAUDE_CONFIG_DIR`
  (`~/.config/ccx/profiles/<name>/home`), so credentials and history never mix.
- The `claude` profile uses your normal Anthropic login and `~/.claude` (plugins/skills intact).
- Provider defaults are in `~/.config/ccx/providers/*.tmpl` — edit them to update
  endpoints or model IDs (these can change over time).

## Test
```bash
bash tests/test_ccx.sh
```
```

- [ ] **Step 2: Run the full suite to confirm everything is green**

Run: `bash tests/test_ccx.sh`
Expected: PASS — every assertion ok, final line `N assertions, 0 failed`.

- [ ] **Step 3: Manual smoke test (optional, requires real credentials)**

Run:
```bash
./install.sh
ccx claude -p "say hi in 3 words"          # uses your Anthropic login
ccx new --provider glm                       # paste a real GLM token when prompted
ccx glm -p "say hi in 3 words"               # exercises a third-party profile
```
Expected: each command starts Claude Code against the right provider.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(ccx): add README"
```

---

## Notes for the implementer

- **`set -euo pipefail`**: `kv_get`/`stat` calls that may legitimately return empty/non-zero are wrapped or used in assignments where that's safe. If a step trips `-e` unexpectedly, prefer `x="$(cmd || true)"` over removing `-e`.
- **`stat` portability**: tests and `ccx` use `stat -c` (GNU/Linux) with a `stat -f` (BSD/macOS) fallback. The target machine is Linux (CachyOS), so `stat -c` is the primary path.
- **No network in tests**: all tests use `CCX_DRY_RUN=1` and `CCX_CLAUDE_BIN=true`; nothing calls a real API.
- **Idempotent provider copy**: `install.sh` uses `cp -n` so re-running never clobbers user-edited templates.
- **Provider defaults can drift**: endpoint URLs and model IDs in `providers/*.tmpl` are best-effort as of 2026-06-26; `ccx new` always lets the user confirm/override, and templates are editable post-install.
