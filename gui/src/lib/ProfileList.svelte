<script>
  import { launchProfile, copyCommand, deleteProfile } from "./api.js";

  let { profiles = [], onedit, onnew, onrefresh, onsettings } = $props();
  let message = $state("");

  async function launch(name) {
    try {
      await launchProfile(name);
      message = `Launched ${name}`;
    } catch (e) {
      message = `Launch failed: ${e}. Try Copy instead.`;
    }
  }

  async function copy(name) {
    const cmd = await copyCommand(name);
    await navigator.clipboard.writeText(cmd);
    message = `Copied: ${cmd}`;
  }

  async function remove(name) {
    if (!confirm(`Remove profile "${name}"?`)) return;
    await deleteProfile(name);
    message = `Removed ${name}`;
    onrefresh?.();
  }
</script>

<header>
  <h1>ccx</h1>
  <div class="hbtns">
    <button onclick={() => onnew?.()}>+ New profile</button>
    <button onclick={() => onsettings?.()}>⚙ Settings</button>
  </div>
</header>

{#if message}<p class="msg">{message}</p>{/if}

{#if profiles.length === 0}
  <p class="empty">No profiles yet. Create one.</p>
{:else}
  <ul class="cards">
    {#each profiles as p (p.name)}
      <li class="card">
        <div class="row">
          <strong>{p.name}</strong>
          <span class="badge">{p.provider}</span>
          {#if p.isolate}<span class="iso">⊘ isolated</span>{:else}<span class="iso shared">shared</span>{/if}
          <span class="model">{p.model ?? ""}</span>
        </div>
        <div class="sub">
          {p.baseUrl ?? "(anthropic login)"}{#if p.hasToken} · token {p.tokenMasked}{/if}
        </div>
        <div class="actions">
          <button onclick={() => launch(p.name)}>▶ Launch</button>
          <button onclick={() => copy(p.name)}>⧉ Copy</button>
          <button onclick={() => onedit?.(p.name)}>✎ Edit</button>
          <button class="danger" onclick={() => remove(p.name)}>🗑 Delete</button>
        </div>
      </li>
    {/each}
  </ul>
{/if}

<style>
  header { display: flex; justify-content: space-between; align-items: center; }
  .hbtns { display: flex; gap: 8px; }
  .cards { list-style: none; padding: 0; display: grid; gap: 12px; }
  .card { border: 1px solid #3a3a3a; border-radius: 8px; padding: 12px; background: #1e1e1e; }
  .row { display: flex; gap: 10px; align-items: center; }
  .badge { background: #2d4; color: #062; border-radius: 4px; padding: 0 6px; font-size: 12px; }
  .iso { font-size: 12px; color: #e90; }
  .iso.shared { color: #888; }
  .model { margin-left: auto; color: #9cf; }
  .sub { color: #aaa; font-size: 13px; margin: 6px 0; }
  .actions { display: flex; gap: 8px; }
  .danger { color: #f66; }
  .msg { color: #6c9; }
  .empty { color: #888; }
</style>
