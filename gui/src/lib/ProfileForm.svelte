<script>
  import { onMount } from "svelte";
  import { listProviders, getProfile, createProfile, updateProfile } from "./api.js";

  let { editName = null, oncancel, onsaved } = $props();

  let providers = $state([]);
  let isEdit = $derived(!!editName);
  let error = $state("");
  // Existing credentials never enter the renderer. "unchanged" preserves the
  // backend value; "replace" accepts a write-only replacement (blank clears);
  // and "remove" explicitly schedules deletion on Save.
  let primarySecretMode = $state("replace");
  let fallbackSecretMode = $state("replace");
  let hasStoredPrimary = $state(false);
  let hasStoredFallbackKeys = $state(false);

  let form = $state({
    name: "", provider: "claude", isolate: false,
    baseUrl: "", token: "", model: "", opus: "", sonnet: "", haiku: "",
    // OpenAI-compatible (router) fields
    router: "", upstreamUrl: "", upstreamKey: "",
    fallbackUrls: "", fallbackKeys: "", attemptsPerUpstream: 1,
    // Positional across the upstream chain: `;` separates upstreams (position 0
    // is the primary), `,` separates provider slugs within one upstream.
    providerOnly: "", providerOrder: "", requireParameters: "",
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
        fallbackUrls: p.fallbackUrls ?? "", fallbackKeys: "",
        attemptsPerUpstream: p.attemptsPerUpstream ?? 1,
        providerOnly: p.providerOnly ?? "", providerOrder: p.providerOrder ?? "",
        requireParameters: p.requireParameters ?? "",
      };
      hasStoredPrimary = p.router ? p.hasUpstreamKey : p.hasToken;
      hasStoredFallbackKeys = p.hasFallbackKeys;
      primarySecretMode = hasStoredPrimary ? "unchanged" : "replace";
      fallbackSecretMode = hasStoredFallbackKeys ? "unchanged" : "replace";
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
      form.fallbackUrls = "";
      form.fallbackKeys = "";
      form.attemptsPerUpstream = 1;
      form.providerOnly = "";
      form.providerOrder = "";
      form.requireParameters = "";
    } else {
      form.baseUrl = t.baseUrl ?? "";
    }
  }

  function replacePrimarySecret() {
    if (isRouter) form.upstreamKey = "";
    else form.token = "";
    primarySecretMode = "replace";
  }

  function removePrimarySecret() {
    if (isRouter) form.upstreamKey = "";
    else form.token = "";
    primarySecretMode = "remove";
  }

  function keepPrimarySecret() {
    if (isRouter) form.upstreamKey = "";
    else form.token = "";
    primarySecretMode = "unchanged";
  }

  function replaceFallbackSecrets() {
    form.fallbackKeys = "";
    fallbackSecretMode = "replace";
  }

  function removeFallbackSecrets() {
    form.fallbackKeys = "";
    fallbackSecretMode = "remove";
  }

  function keepFallbackSecrets() {
    form.fallbackKeys = "";
    fallbackSecretMode = "unchanged";
  }

  function buildProfile() {
    const opt = (v) => (v && v.trim() !== "" ? v.trim() : null);
    const csv = (v) => opt(v.split(/[\n,]/).map((part) => part.trim()).filter(Boolean).join(","));
    const secretCsv = (v) => opt(v.split(",").map((part) => part.trim()).join(","));
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
        fallbackUrls: csv(form.fallbackUrls),
        fallbackKeys: secretCsv(form.fallbackKeys),
        attemptsPerUpstream: Number(form.attemptsPerUpstream),
        providerOnly: opt(form.providerOnly),
        providerOrder: opt(form.providerOrder),
        requireParameters: opt(form.requireParameters),
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
    if (isRouter && (!Number.isInteger(Number(form.attemptsPerUpstream)) || Number(form.attemptsPerUpstream) < 1 || Number(form.attemptsPerUpstream) > 10)) {
      error = "Attempts per upstream must be between 1 and 10.";
      return;
    }
    if (isRouter) {
      const upstreamCount = 1 + form.fallbackUrls.split(/[\n,]/).map((part) => part.trim()).filter(Boolean).length;
      for (const [label, value] of [["Only", form.providerOnly], ["Order", form.providerOrder]]) {
        if (!value) continue;
        const positions = value.split(";");
        if (positions.length > upstreamCount) {
          error = `Provider ${label.toLowerCase()} lists ${positions.length} upstream positions but only ${upstreamCount} upstream(s) are configured.`;
          return;
        }
        const bad = positions.flatMap((position) => position.split(",")).map((slug) => slug.trim())
          .filter(Boolean).find((slug) => !/^[A-Za-z0-9._-]+$/.test(slug));
        if (bad) {
          error = `Invalid provider slug: ${bad}.`;
          return;
        }
      }
      if (form.requireParameters) {
        const positions = form.requireParameters.split(";");
        if (positions.length > upstreamCount) {
          error = `Require parameters lists ${positions.length} upstream positions but only ${upstreamCount} upstream(s) are configured.`;
          return;
        }
        if (positions.some((position) => !["", "1", "true"].includes(position.trim()))) {
          error = "Require parameters entries must be 1, true, or empty.";
          return;
        }
      }
      const fallbackUrls = form.fallbackUrls.split(/[\n,]/).map((part) => part.trim()).filter(Boolean);
      for (const value of fallbackUrls) {
        try {
          const parsed = new URL(value);
          const loopback = ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);
          if (!["http:", "https:"].includes(parsed.protocol) || (parsed.protocol === "http:" && !loopback)) throw new Error();
        } catch {
          error = `Invalid fallback URL: ${value}. Remote endpoints must use HTTPS.`;
          return;
        }
      }
    }
    try {
      const profile = buildProfile();
      // Only the explicit change modes let an empty field clear a credential.
      // In the default state the backend retains the value that never entered
      // this webview.
      if (isEdit) {
        await updateProfile(
          profile,
          primarySecretMode === "unchanged",
          fallbackSecretMode === "unchanged",
        );
      }
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
  <p class="hint">Routed through <code>ccx-router</code>, a local translator <code>ccx</code>
    installs and starts on launch. The installer uses a verified prebuilt when available.</p>

  <label>Upstream URL
    <input bind:value={form.upstreamUrl} placeholder="http://localhost:11434/v1" />
  </label>

  <label>Upstream API key
    {#if isEdit && hasStoredPrimary && primarySecretMode === "unchanged"}
      <div class="secret-state">
        <span>Configured (value hidden)</span>
        <button type="button" onclick={replacePrimarySecret}>Replace</button>
        <button class="danger" type="button" onclick={removePrimarySecret}>Remove</button>
      </div>
    {:else if isEdit && hasStoredPrimary && primarySecretMode === "remove"}
      <div class="secret-state pending">
        <span>Will be removed when you save.</span>
        <button type="button" onclick={keepPrimarySecret}>Undo</button>
      </div>
    {:else}
      <input type="password" bind:value={form.upstreamKey} placeholder="(blank for local servers)" />
      {#if isEdit && hasStoredPrimary}
        <p class="hint">Write-only replacement. Leave blank to remove the current key.</p>
        <button type="button" onclick={keepPrimarySecret}>Keep current key</button>
      {/if}
    {/if}
  </label>

  <details>
    <summary>Reliability — ordered fallback and retries</summary>
    <p class="hint">One fallback URL per line. The router tries them in order only for retryable failures. Empty fallback key slots send no Authorization header.</p>
    <label>Fallback URLs
      <textarea bind:value={form.fallbackUrls} rows="3" placeholder="https://backup.example/v1"></textarea>
    </label>
    <label>Fallback API keys (comma-separated)
      {#if isEdit && hasStoredFallbackKeys && fallbackSecretMode === "unchanged"}
        <div class="secret-state">
          <span>Configured (values hidden)</span>
          <button type="button" onclick={replaceFallbackSecrets}>Replace</button>
          <button class="danger" type="button" onclick={removeFallbackSecrets}>Remove</button>
        </div>
      {:else if isEdit && hasStoredFallbackKeys && fallbackSecretMode === "remove"}
        <div class="secret-state pending">
          <span>Will be removed when you save.</span>
          <button type="button" onclick={keepFallbackSecrets}>Undo</button>
        </div>
      {:else}
        <input type="password" bind:value={form.fallbackKeys} placeholder="blank = no Authorization" />
        {#if isEdit && hasStoredFallbackKeys}
          <p class="hint">Write-only replacement. Leave blank to remove all fallback keys.</p>
          <button type="button" onclick={keepFallbackSecrets}>Keep current keys</button>
        {/if}
      {/if}
    </label>
    <label>Attempts per upstream
      <input type="number" min="1" max="10" step="1" bind:value={form.attemptsPerUpstream} />
    </label>

    <p class="hint">
      OpenRouter provider pinning. One position per upstream, separated by
      <code>;</code> (the first is the primary); provider slugs within a
      position are separated by <code>,</code>. Leave a position empty to send
      nothing to that upstream.
    </p>

    <label>Provider only
      <input bind:value={form.providerOnly} placeholder="groq,fireworks;together" />
    </label>

    <label>Provider order
      <input bind:value={form.providerOrder} placeholder="groq,fireworks" />
    </label>

    <label>Require parameters
      <input bind:value={form.requireParameters} placeholder="1;;1" />
    </label>
  </details>
{:else}
  <label>Base URL
    <input bind:value={form.baseUrl} placeholder="(blank = Anthropic login)" />
  </label>

  <label>Token
    {#if isEdit && hasStoredPrimary && primarySecretMode === "unchanged"}
      <div class="secret-state">
        <span>Configured (value hidden)</span>
        <button type="button" onclick={replacePrimarySecret}>Replace</button>
        <button class="danger" type="button" onclick={removePrimarySecret}>Remove</button>
      </div>
    {:else if isEdit && hasStoredPrimary && primarySecretMode === "remove"}
      <div class="secret-state pending">
        <span>Will be removed when you save.</span>
        <button type="button" onclick={keepPrimarySecret}>Undo</button>
      </div>
    {:else}
      <input type="password" bind:value={form.token} placeholder="API token" />
      {#if isEdit && hasStoredPrimary}
        <p class="hint">Write-only replacement. Leave blank to remove the current token.</p>
        <button type="button" onclick={keepPrimarySecret}>Keep current token</button>
      {/if}
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
  input, select, textarea { width: 100%; padding: 6px; background: #1e1e1e; color: #eee; border: 1px solid #3a3a3a; border-radius: 4px; box-sizing: border-box; }
  label.check { display: flex; gap: 8px; align-items: center; }
  label.check input { width: auto; }
  .secret-state { display: flex; gap: 8px; align-items: center; }
  .secret-state span { flex: 1; color: #aaa; }
  .secret-state.pending span { color: #f0b45d; }
  .danger { color: #f66; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .err { color: #f66; }
  .hint { color: #999; font-size: 0.85em; margin: 6px 0; }
  details { margin: 10px 0; }
</style>
