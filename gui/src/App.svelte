<script>
  import { onMount } from "svelte";
  import { listProfiles } from "./lib/api.js";
  import ProfileList from "./lib/ProfileList.svelte";
  import ProfileForm from "./lib/ProfileForm.svelte";
  import SettingsPanel from "./lib/SettingsPanel.svelte";
  import TeamWorkspace from "./lib/TeamWorkspace.svelte";
  import "./style.css";

  let profiles = $state([]);
  let section = $state("teams");
  let view = $state("list");
  let editName = $state(null);
  let error = $state("");

  async function refresh() {
    try { profiles = await listProfiles(); error = ""; }
    catch (e) { error = String(e); }
    view = "list";
    editName = null;
  }

  onMount(refresh);
</script>

<div class="app-shell">
  <aside class="app-nav" aria-label="Main navigation">
    <div class="brand"><span class="brand-mark">c<span>c</span>x</span><small>YOUR CODING WORKSPACE</small></div>
    <nav>
      <button class:chosen={section === "teams"} onclick={() => section = "teams"}><span>◈</span> Teams</button>
      <button class:chosen={section === "profiles"} onclick={() => { section = "profiles"; view = "list"; }}><span>◫</span> Profiles <small>{profiles.length}</small></button>
      <button class:chosen={section === "settings"} onclick={() => section = "settings"}><span>⚙</span> Settings</button>
    </nav>
    <div class="nav-footer"><span class="status-dot"></span> Local workspace<small>Claude · Codex · Your models</small></div>
  </aside>
  <main>
    {#if error}<div class="notice error" role="alert">{error}<button onclick={refresh}>Retry</button></div>{/if}
    <div hidden={section !== "teams"} class="team-container"><TeamWorkspace {profiles} onprofiles={() => { section = "profiles"; view = "new"; }} /></div>
    {#if section === "settings"}
      <div class="legacy-content"><SettingsPanel onclose={() => section = "teams"} /></div>
    {:else if section === "profiles"}
      <div class="legacy-content">
        {#if view === "list"}
          <ProfileList {profiles} onrefresh={refresh} onnew={() => view = "new"} onedit={(name) => { editName = name; view = "edit"; }} onsettings={() => section = "settings"} />
        {:else}
          <ProfileForm editName={view === "edit" ? editName : null} oncancel={refresh} onsaved={refresh} />
        {/if}
      </div>
    {/if}
  </main>
</div>
