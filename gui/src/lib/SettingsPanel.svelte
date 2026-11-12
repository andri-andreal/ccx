<script>
  import { onMount } from "svelte";
  import { getSettings, setSettings, detectTerminal, teamRequest } from "./api.js";

  let { onclose } = $props();
  let detected = $state("");
  let overrideStr = $state("");
  let saved = $state(false);
  let theme = $state("default");
  let error = $state("");
  let loading = $state(true);
  let saving = $state(false);

  onMount(async () => {
    try {
    detected = (await detectTerminal()) ?? "(none found)";
    const s = await getSettings();
    overrideStr = (s.terminalOverride ?? []).join(" ");
    const display=await teamRequest("display");theme=display.theme || "default";
    }catch(e){error=String(e);}finally{loading=false;}
  });

  async function save() {
    saving=true;
    const selectedTheme=theme;
    try {
    const parts = overrideStr.trim().split(/\s+/).filter(Boolean);
    await setSettings({ terminalOverride: parts.length ? parts : null });
    await teamRequest("save_display",{theme:selectedTheme});
    document.documentElement.dataset.theme=selectedTheme;
    saved = true;
    error="";
    }catch(e){error=String(e);}finally{saving=false;}
  }
</script>

<h2>Settings</h2>
{#if error}<p class="notice error" role="alert">{error}</p>{/if}
<label>Workspace theme
  <select bind:value={theme} disabled={loading || saving}><option value="default">Default · green</option><option value="ocean">Ocean · blue</option><option value="contrast">High contrast</option></select>
</label>
<p class="muted">Shared with Team's live theme settings. Terminal override applies to profile launches; Team sessions run inside the desktop.</p>
<p>Detected terminal: <strong>{detected}</strong></p>
<label>Override terminal command (e.g. <code>alacritty -e</code>); blank = auto-detect
  <input bind:value={overrideStr} placeholder="auto-detect" disabled={loading || saving} />
</label>
{#if saved}<p class="ok">Saved.</p>{/if}
<div class="actions">
  <button onclick={save} disabled={loading || saving}>{saving ? "Saving…" : "Save"}</button>
  <button onclick={() => onclose?.()}>Back</button>
</div>

<style>
  label { display: block; margin: 12px 0; }
  input { width: 100%; padding: 6px; background: #1e1e1e; color: #eee; border: 1px solid #3a3a3a; border-radius: 4px; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .ok { color: #6c9; }
</style>
