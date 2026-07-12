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

# --- Task 2: list empty + template lookup ---
out="$("$CCX" list 2>&1)"; rc=$?
assert_exit "$rc" 0 "list empty exits 0"
assert_contains "$out" "No profiles yet" "list shows empty hint"

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

# --- Task 4: run (dry-run), no isolation ---
out="$(CCX_DRY_RUN=1 "$CCX" myclaude 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run claude exits 0"
assert_contains "$out" "CCX_DRY_RUN profile=myclaude" "dry-run header shows profile"
assert_contains "$out" "env: ANTHROPIC_MODEL=opusplan" "exports opusplan"
assert_not_contains "$out" "CLAUDE_CONFIG_DIR" "claude profile not isolated"
assert_not_contains "$out" "env: CCX_PROVIDER" "CCX_* not exported to claude"
assert_contains "$out" "exec: true" "exec uses CCX_CLAUDE_BIN"

# --- Task 5: isolation + token masking ---
out="$(CCX_DRY_RUN=1 "$CCX" myglm 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run glm exits 0"
assert_contains "$out" "env: CLAUDE_CONFIG_DIR=$CCX_HOME/profiles/myglm/home" "glm isolated config dir"
assert_contains "$out" "env: ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" "glm base url exported"
assert_contains "$out" "ANTHROPIC_AUTH_TOKEN=sk-…6789" "token masked in dry-run"
assert_not_contains "$out" "sk-test-123456789" "raw token not printed"
assert_eq "$([ -d "$CCX_HOME/profiles/myglm/home" ] && echo y)" "y" "home dir exists"

# --- Task 6: arg pass-through ---
out="$(CCX_DRY_RUN=1 "$CCX" myclaude --model haiku -p "hi there" 2>&1)"; rc=$?
assert_exit "$rc" 0 "dry-run with args exits 0"
assert_contains "$out" "exec: true --model haiku -p hi there" "args forwarded verbatim"

# --- Task 7: show ---
out="$("$CCX" show myglm 2>&1)"; rc=$?
assert_exit "$rc" 0 "show exits 0"
assert_contains "$out" "ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic" "show prints base url"
assert_contains "$out" "ANTHROPIC_AUTH_TOKEN=sk-…6789" "show masks token"
assert_not_contains "$out" "sk-test-123456789" "show hides raw token"

out="$("$CCX" show nope 2>&1)"; rc=$?
assert_exit "$rc" 1 "show unknown exits 1"

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

# `delete` is an alias for rm
"$CCX" new --name todel --provider claude --yes >/dev/null 2>&1
CCX_YES=1 "$CCX" delete todel >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "delete alias exits 0"
assert_eq "$([ -d "$CCX_HOME/profiles/todel" ] && echo y || echo n)" "n" "delete removed the profile"

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

# --- Task 10: install ---
FAKE_HOME="$(mktemp -d)"
out="$(HOME="$FAKE_HOME" XDG_CONFIG_HOME="$FAKE_HOME/.config" bash "$REPO/install.sh" 2>&1)"; rc=$?
assert_exit "$rc" 0 "install exits 0"
assert_eq "$([ -L "$FAKE_HOME/.local/bin/ccx" ] && echo y || echo n)" "y" "ccx symlinked into ~/.local/bin"
assert_eq "$([ -f "$FAKE_HOME/.config/ccx/providers/glm.tmpl" ] && echo y || echo n)" "y" "templates copied"
rm -rf "$FAKE_HOME"

# --- Task 11: model selection at creation (flag + interactive prompt) ---
# --model makes a clean single-model profile: every slot follows the chosen model
"$CCX" new --name mm2 --provider minimax --model MiniMax-M2 --token sk-mm-000011112222 --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new minimax --model exits 0"
mm2="$CCX_HOME/profiles/mm2/profile.env"
assert_contains "$(cat "$mm2")" "ANTHROPIC_MODEL=MiniMax-M2" "--model sets default model"
assert_contains "$(cat "$mm2")" "ANTHROPIC_DEFAULT_OPUS_MODEL=MiniMax-M2" "--model fills opus slot"
assert_contains "$(cat "$mm2")" "ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M2" "--model fills sonnet slot"
assert_contains "$(cat "$mm2")" "ANTHROPIC_DEFAULT_HAIKU_MODEL=MiniMax-M2" "--model fills haiku slot"

# interactive prompt offers the template default and accepts an override
out="$(printf 'MiniMax-M2\n' | "$CCX" new --name mm2i --provider minimax --token sk-mm-333344445555 2>&1)"; rc=$?
assert_exit "$rc" 0 "new minimax interactive exits 0"
assert_contains "$out" "Model [MiniMax-M3]" "prompts model with template default"
mm2i="$CCX_HOME/profiles/mm2i/profile.env"
assert_contains "$(cat "$mm2i")" "ANTHROPIC_MODEL=MiniMax-M2" "interactive override applied"
assert_contains "$(cat "$mm2i")" "ANTHROPIC_DEFAULT_OPUS_MODEL=MiniMax-M2" "interactive override fills slots"

# empty input keeps the template default for every slot
out="$(printf '\n' | "$CCX" new --name mm3i --provider minimax --token sk-mm-666677778888 2>&1)"; rc=$?
assert_exit "$rc" 0 "new minimax interactive default exits 0"
mm3i="$CCX_HOME/profiles/mm3i/profile.env"
assert_contains "$(cat "$mm3i")" "ANTHROPIC_MODEL=MiniMax-M3" "empty input keeps default model"
assert_contains "$(cat "$mm3i")" "ANTHROPIC_DEFAULT_SONNET_MODEL=MiniMax-M3" "empty input fills slots with default"

# --yes is non-interactive: no prompt, never blocks on stdin
out="$("$CCX" new --name mmy --provider minimax --token sk-mm-999900001111 --yes 2>&1 </dev/null)"; rc=$?
assert_exit "$rc" 0 "new minimax --yes exits 0"
assert_not_contains "$out" "Model [" "--yes skips the model prompt"

# claude provider: prompt keeps opusplan and writes no custom slots
out="$(printf '\n' | "$CCX" new --name cint --provider claude 2>&1)"; rc=$?
assert_exit "$rc" 0 "new claude interactive exits 0"
cint="$CCX_HOME/profiles/cint/profile.env"
assert_contains "$(cat "$cint")" "ANTHROPIC_MODEL=opusplan" "claude keeps opusplan"
assert_not_contains "$(cat "$cint")" "ANTHROPIC_DEFAULT_OPUS_MODEL" "claude writes no custom slot"

# ============================================================================
# OpenAI-compatible providers via the built-in translator (ccx-router)
# spec: docs/superpowers/specs/2026-06-28-ccx-builtin-router-design.md
# ============================================================================

# --- OAI Task 1: openai-compatible provider templates ---
for p in openai openrouter ollama vllm lmstudio custom-oai; do
  tf="$REPO/providers/$p.tmpl"
  assert_eq "$([ -f "$tf" ] && echo y)" "y" "$p template exists"
done
assert_contains "$(cat "$REPO/providers/ollama.tmpl")" "CCX_ROUTER=builtin" "ollama is a router provider"
assert_contains "$(cat "$REPO/providers/ollama.tmpl")" "CCX_ISOLATE=true" "ollama isolated by default"
assert_contains "$(cat "$REPO/providers/ollama.tmpl")" "CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1" "ollama upstream base url (/v1)"
assert_not_contains "$(cat "$REPO/providers/ollama.tmpl")" "CCX_TRANSFORMER" "no transformer in builtin templates"

# --- OAI Task 2: new router profile ---
"$CCX" new --name lq --provider ollama --model qwen2.5-coder:7b --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new ollama exits 0"
penv="$CCX_HOME/profiles/lq/profile.env"
assert_mode "$penv" 600 "router profile.env is 600"
assert_contains "$(cat "$penv")" "CCX_PROVIDER=ollama" "router provider recorded"
assert_contains "$(cat "$penv")" "CCX_ROUTER=builtin" "router marker written"
assert_contains "$(cat "$penv")" "CCX_ISOLATE=true" "router isolated by default"
assert_contains "$(cat "$penv")" "CCX_UPSTREAM_BASE_URL=http://localhost:11434/v1" "upstream base url written"
assert_contains "$(cat "$penv")" "ANTHROPIC_MODEL=qwen2.5-coder:7b" "model written (pass-through)"
assert_contains "$(cat "$penv")" "ANTHROPIC_DEFAULT_OPUS_MODEL=qwen2.5-coder:7b" "opus slot filled"
assert_contains "$(cat "$penv")" "ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen2.5-coder:7b" "haiku slot filled"
assert_not_contains "$(cat "$penv")" "CCX_TRANSFORMER" "no transformer key"
assert_not_contains "$(cat "$penv")" "CCX_MODEL_DEFAULT" "no ccr model-slot keys"
assert_not_contains "$(cat "$penv")" "ANTHROPIC_BASE_URL" "router profile has no anthropic base url (set at launch)"
assert_eq "$([ -d "$CCX_HOME/profiles/lq/home" ] && echo y)" "y" "claude config home created"
assert_eq "$([ -d "$CCX_HOME/profiles/lq/router" ] && echo leftover || echo none)" "none" "no router dir (builtin needs none)"

# openrouter: remote provider takes an upstream key
"$CCX" new --name orr --provider openrouter --model qwen/qwen3-coder --upstream-key sk-or-abc123456789 --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new openrouter exits 0"
orr="$CCX_HOME/profiles/orr/profile.env"
assert_contains "$(cat "$orr")" "CCX_UPSTREAM_BASE_URL=https://openrouter.ai/api/v1" "openrouter upstream base"
assert_contains "$(cat "$orr")" "CCX_UPSTREAM_API_KEY=sk-or-abc123456789" "openrouter key written"
assert_contains "$(cat "$orr")" "ANTHROPIC_MODEL=qwen/qwen3-coder" "openrouter model recorded"

# per-slot override via --opus keeps the rest on the single model
"$CCX" new --name lq2 --provider ollama --model base-model --opus big-model --yes >/dev/null 2>&1; rc=$?
assert_exit "$rc" 0 "new ollama with --opus exits 0"
lq2="$CCX_HOME/profiles/lq2/profile.env"
assert_contains "$(cat "$lq2")" "ANTHROPIC_MODEL=base-model" "default model is the single model"
assert_contains "$(cat "$lq2")" "ANTHROPIC_DEFAULT_OPUS_MODEL=big-model" "opus slot overridden"
assert_contains "$(cat "$lq2")" "ANTHROPIC_DEFAULT_SONNET_MODEL=base-model" "sonnet slot keeps single model"

# --- OAI Task 2b: interactive wizard (model + upstream URL prompts) ---
assert_eq "$([ -f "$REPO/providers/sakana.tmpl" ] && echo y)" "y" "sakana template exists"
# sakana: name+provider via flags. Prompt order is upstream URL, then key, then
# model — keep the URL default, type the key, keep the model default.
out="$(printf '\nmykey\n' | "$CCX" new --name sk --provider sakana 2>&1)"; rc=$?
assert_exit "$rc" 0 "interactive sakana exits 0"
assert_contains "$out" "Model [fugu]" "wizard offers model default"
assert_contains "$out" "Upstream base URL [https://api.sakana.ai/v1]" "wizard offers upstream default"
sk="$CCX_HOME/profiles/sk/profile.env"
assert_contains "$(cat "$sk")" "CCX_ROUTER=builtin" "sakana is a router profile"
assert_contains "$(cat "$sk")" "CCX_UPSTREAM_BASE_URL=https://api.sakana.ai/v1" "sakana upstream default kept"
assert_contains "$(cat "$sk")" "ANTHROPIC_MODEL=fugu" "sakana model default kept"
assert_contains "$(cat "$sk")" "CCX_UPSTREAM_API_KEY=mykey" "sakana key from prompt"
# custom-oai: no template defaults — type upstream URL, key, then model (in order).
out="$(printf 'https://api.example.com/v1\nk\ngpt-4o\n' | "$CCX" new --name c1 --provider custom-oai 2>&1)"; rc=$?
assert_exit "$rc" 0 "interactive custom-oai exits 0"
c1="$CCX_HOME/profiles/c1/profile.env"
assert_contains "$(cat "$c1")" "CCX_UPSTREAM_BASE_URL=https://api.example.com/v1" "typed upstream url"
assert_contains "$(cat "$c1")" "ANTHROPIC_MODEL=gpt-4o" "typed model"
assert_contains "$(cat "$c1")" "ANTHROPIC_DEFAULT_OPUS_MODEL=gpt-4o" "typed model fills slots"

# --- OAI Task 4: free-port picker + apikey generator ---
p="$("$CCX" __freeport 2>&1)"; rc=$?
assert_exit "$rc" 0 "freeport exits 0"
TESTS_RUN=$((TESTS_RUN + 1))
case "$p" in '' | *[!0-9]*) _fail "freeport numeric: got [$p]" ;; *) _pass "freeport numeric" ;; esac
TESTS_RUN=$((TESTS_RUN + 1))
if [ "${p:-0}" -ge 39000 ] && [ "${p:-0}" -le 39999 ]; then _pass "freeport in range"; else _fail "freeport in range: got [$p]"; fi
k="$("$CCX" __apikey 2>&1)"
TESTS_RUN=$((TESTS_RUN + 1))
if [ "${#k}" -ge 16 ]; then _pass "apikey is long enough"; else _fail "apikey too short: [$k]"; fi
assert_not_contains "$k" "/" "apikey has no slash"

# --- OAI Task 5: router dry-run (no ccx-router started) ---
out="$(CCX_DRY_RUN=1 "$CCX" lq 2>&1)"; rc=$?
assert_exit "$rc" 0 "router dry-run exits 0"
assert_contains "$out" "CCX_DRY_RUN profile=lq provider=ollama router=builtin" "dry-run header shows router"
assert_contains "$out" "ccx-router --port" "dry-run shows ccx-router command"
assert_contains "$out" "--upstream-url http://localhost:11434/v1" "dry-run shows upstream url"
assert_contains "$out" "ANTHROPIC_BASE_URL=http://127.0.0.1:" "points claude at local router"
assert_contains "$out" "env: CLAUDE_CONFIG_DIR=$CCX_HOME/profiles/lq/home" "router profile isolates claude config"
assert_not_contains "$out" "env: CCX_UPSTREAM_BASE_URL" "CCX_* not exported to claude"
assert_contains "$out" "exec: true" "exec uses CCX_CLAUDE_BIN"
# args pass through
out="$(CCX_DRY_RUN=1 "$CCX" lq -p "hello" 2>&1)"
assert_contains "$out" "exec: true -p hello" "router dry-run forwards claude args"
# openrouter upstream key masked
out2="$(CCX_DRY_RUN=1 "$CCX" orr 2>&1)"
assert_not_contains "$out2" "sk-or-abc123456789" "upstream key masked in dry-run"
assert_contains "$out2" "CCX_ROUTER_UPSTREAM_KEY=sk-…6789" "dry-run shows masked upstream key env"

# --- OAI Task 6: live router lifecycle (start, health-check, teardown) ---
if command -v python3 >/dev/null 2>&1; then
  chmod +x "$HERE/fake_ccx_router.sh"
  out="$(CCX_ROUTER_BIN="$HERE/fake_ccx_router.sh" CCX_ROUTER_TIMEOUT=8 "$CCX" lq -p hi 2>&1)"; rc=$?
  assert_exit "$rc" 0 "router live run exits 0"
  assert_contains "$out" "router ready on http://127.0.0.1:" "announces router readiness"
  # router torn down: the port it used is no longer accepting connections
  lport="$(printf '%s' "$out" | grep -o '127.0.0.1:[0-9]*' | head -1 | grep -o '[0-9]*$')"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ -n "$lport" ] && (exec 3<>"/dev/tcp/127.0.0.1/$lport") 2>/dev/null; then
    _fail "router torn down (port $lport still open)"
  else
    _pass "router torn down (port freed)"
  fi
else
  printf 'skip: python3 not available, router live lifecycle test skipped\n'
fi

# missing ccx-router binary -> clear error, exit 1 (no dry-run)
out="$(CCX_ROUTER_BIN=definitely-not-a-binary "$CCX" lq -p hi 2>&1)"; rc=$?
assert_exit "$rc" 1 "missing ccx-router exits 1"
assert_contains "$out" "ccx-router not found" "missing binary gives build hint"

# --- OAI Task 7: list/show awareness for router profiles ---
lst="$("$CCX" list 2>&1)"
assert_contains "$lst" "lq" "router profile listed"
assert_contains "$lst" "ollama" "router provider shown in list"
assert_contains "$lst" "localhost:11434" "router upstream shown in list (not 'anthropic login')"
out="$("$CCX" show orr 2>&1)"; rc=$?
assert_exit "$rc" 0 "show router exits 0"
assert_contains "$out" "CCX_UPSTREAM_BASE_URL=https://openrouter.ai" "show prints upstream url"
assert_contains "$out" "CCX_UPSTREAM_API_KEY=sk-…6789" "upstream key masked in show"
assert_not_contains "$out" "sk-or-abc123456789" "raw upstream key hidden"

# --- Safety: profile-name validation rejects traversal/odd names on every command ---
for bad in "../evil" "a/b" "." ".." "with space" "semi;colon"; do
  CCX_YES=1 "$CCX" rm "$bad" >/dev/null 2>&1; assert_exit "$?" 1 "rm rejects bad name: $bad"
  "$CCX" show "$bad" >/dev/null 2>&1;         assert_exit "$?" 1 "show rejects bad name: $bad"
  "$CCX" edit "$bad" >/dev/null 2>&1;         assert_exit "$?" 1 "edit rejects bad name: $bad"
  "$CCX" new --name "$bad" --provider claude -y >/dev/null 2>&1; assert_exit "$?" 1 "new rejects bad name: $bad"
done
out="$("$CCX" show "../../etc/passwd" 2>&1)"; assert_contains "$out" "invalid profile name" "show gives a clear error for a bad name"

# --- ccx --version ---
out="$("$CCX" --version 2>&1)"; rc=$?
assert_exit "$rc" 0 "--version exits 0"
assert_contains "$out" "ccx 0.1.0" "--version prints ccx version"
assert_contains "$out" "claude:" "--version reports claude detection"

finish
