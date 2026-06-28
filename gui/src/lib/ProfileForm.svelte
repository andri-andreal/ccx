<script>
  import { onMount } from "svelte";
  import { listProviders, getProfile, revealToken, revealUpstreamKey, createProfile, updateProfile } from "./api.js";

  let { editName = null, oncancel, onsaved } = $props();

  let providers = $state([]);
  let isEdit = $state(!!editName);
  let error = $state("");
  let revealed = $state(false);

  let form = $state({
    name: "", provider: "claude", isolate: false,
    baseUrl: "", token: "", model: "", opus: "", sonnet: "", haiku: "",
    // OpenAI-compatible (router) fields
    router: "", upstreamUrl: "", upstreamKey: "",
  });

  let isRouter = $derived(!!form.router);
  let anthropicProviders = $derived(providers.filter((p) => !p.router));
  let routerProviders = $derived(providers.filter((p) => p.router));

  onMount(async () => {
    providers = await listProviders();
    if (editName) {
      const p = await getProfile(editName);
      form = {
        name: p.name, provider: p.provider, isolate: p.isolate,
        baseUrl: p.baseUrl ?? "", token: "", model: p.model ?? "",
        opus: p.opus ?? "", sonnet: p.sonnet ?? "", haiku: p.haiku ?? "",
        router: p.router ?? "", upstreamUrl: p.upstreamUrl ?? "", upstreamKey: "",
      };
    } else {
      applyProvider("claude");
    }
  });

  function applyProvider(name) {
    const t = providers.find((x) => x.name === name);
    if (!t) return;
    form.provider = name;
    form.isolate = t.isolate;
    form.model = t.model ?? "";
    form.opus = t.opus ?? "";
    form.sonnet = t.sonnet ?? "";
    form.haiku = t.haiku ?? "";
    form.router = t.router ?? "";
    if (t.router) {
      form.upstreamUrl = t.upstreamUrl ?? "";
      form.upstreamKey = "";
    } else {
      form.baseUrl = t.baseUrl ?? "";
    }
  }

  async function reveal() {
    const t = isRouter ? await revealUpstreamKey(form.name) : await revealToken(form.name);
    if (isRouter) form.upstreamKey = t ?? "";
    else form.token = t ?? "";
    revealed = true;
  }

  function buildProfile() {
    const opt = (v) => (v && v.trim() !== "" ? v.trim() : null);
    const shared = {
      name: form.name.trim(),
      provider: form.provider,
      isolate: form.isolate,
      model: opt(form.model),
      opus: opt(form.opus),
      sonnet: opt(form.sonnet),
      haiku: opt(form.haiku),
    };
    if (isRouter) {
      return {
        ...shared,
        router: form.router,
        upstreamUrl: opt(form.upstreamUrl),
        upstreamKey: opt(form.upstreamKey),
      };
    }
    return { ...shared, baseUrl: opt(form.baseUrl), token: opt(form.token) };
  }

  async function save() {
    error = "";
    if (!/^[A-Za-z0-9_-]+$/.test(form.name)) {
      error = "Name must be letters, digits, _ or - only.";
      return;
    }
    try {
      const profile = buildProfile();
      if (isEdit) await updateProfile(profile);
      else await createProfile(profile);
      onsaved?.();
    } catch (e) {
      error = String(e);
    }
  }
</script>

<h2>{isEdit ? `Edit ${form.name}` : "New profile"}</h2>
{#if error}<p class="err">{error}</p>{/if}

<label>Name
  <input bind:value={form.name} disabled={isEdit} placeholder="work" />
</label>

<label>Provider
  <select value={form.provider} onchange={(e) => applyProvider(e.target.value)} disabled={isEdit}>
    <optgroup label="Anthropic-compatible">
      {#each anthropicProviders as p (p.name)}<option value={p.name}>{p.name}</option>{/each}
    </optgroup>
    <optgroup label="OpenAI-compatible (via ccx-router)">
      {#each routerProviders as p (p.name)}<option value={p.name}>{p.name}</option>{/each}
    </optgroup>
  </select>
</label>

{#if isRouter}
  <p class="hint">Routed through <code>ccx-router</code>, a translator <code>ccx</code> builds and
    starts on launch. Requires Rust to build (<code>./install.sh</code>).</p>

  <label>Upstream URL
    <input bind:value={form.upstreamUrl} placeholder="http://localhost:11434/v1" />
  </label>

  <label>Upstream API key
    {#if isEdit && !revealed}
      <div class="reveal"><input value="••••••••" disabled /><button type="button" onclick={reveal}>Reveal</button></div>
    {:else}
      <input type="password" bind:value={form.upstreamKey} placeholder="(blank for local servers)" />
    {/if}
  </label>
{:else}
  <label>Base URL
    <input bind:value={form.baseUrl} placeholder="(blank = Anthropic login)" />
  </label>

  <label>Token
    {#if isEdit && !revealed}
      <div class="reveal"><input value="••••••••" disabled /><button type="button" onclick={reveal}>Reveal</button></div>
    {:else}
      <input type="password" bind:value={form.token} placeholder="API token" />
    {/if}
  </label>
{/if}

<label class="check"><input type="checkbox" bind:checked={form.isolate} /> Isolate config (own CLAUDE_CONFIG_DIR)</label>

<label>Model
  <input bind:value={form.model} placeholder={isRouter ? "qwen2.5-coder:32b" : "model id"} />
</label>

<details>
  <summary>Advanced — model slots (opus / sonnet / haiku)</summary>
  <p class="hint">Each maps an alias to a concrete model id{isRouter ? " (passed through to the upstream)" : ""}.</p>
  <label>opus → <input bind:value={form.opus} /></label>
  <label>sonnet → <input bind:value={form.sonnet} /></label>
  <label>haiku → <input bind:value={form.haiku} /></label>
</details>

<div class="actions">
  <button onclick={save}>Save</button>
  <button onclick={() => oncancel?.()}>Cancel</button>
</div>

<style>
  label { display: block; margin: 10px 0; }
  input, select { width: 100%; padding: 6px; background: #1e1e1e; color: #eee; border: 1px solid #3a3a3a; border-radius: 4px; }
  label.check { display: flex; gap: 8px; align-items: center; }
  label.check input { width: auto; }
  .reveal { display: flex; gap: 8px; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .err { color: #f66; }
  .hint { color: #999; font-size: 0.85em; margin: 6px 0; }
  details { margin: 10px 0; }
</style>
