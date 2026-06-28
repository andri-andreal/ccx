<script>
  import { onMount } from "svelte";
  import { getSettings, setSettings, detectTerminal } from "./api.js";

  let { onclose } = $props();
  let detected = $state("");
  let overrideStr = $state("");
  let saved = $state(false);

  onMount(async () => {
    detected = (await detectTerminal()) ?? "(none found)";
    const s = await getSettings();
    overrideStr = (s.terminalOverride ?? []).join(" ");
  });

  async function save() {
    const parts = overrideStr.trim().split(/\s+/).filter(Boolean);
    await setSettings({ terminalOverride: parts.length ? parts : null });
    saved = true;
  }
</script>

<h2>Settings</h2>
<p>Detected terminal: <strong>{detected}</strong></p>
<label>Override terminal command (e.g. <code>alacritty -e</code>); blank = auto-detect
  <input bind:value={overrideStr} placeholder="auto-detect" />
</label>
{#if saved}<p class="ok">Saved.</p>{/if}
<div class="actions">
  <button onclick={save}>Save</button>
  <button onclick={() => onclose?.()}>Back</button>
</div>

<style>
  label { display: block; margin: 12px 0; }
  input { width: 100%; padding: 6px; background: #1e1e1e; color: #eee; border: 1px solid #3a3a3a; border-radius: 4px; }
  .actions { display: flex; gap: 8px; margin-top: 16px; }
  .ok { color: #6c9; }
</style>
