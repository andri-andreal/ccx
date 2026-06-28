<script>
  import { onMount } from "svelte";
  import { listProfiles } from "./lib/api.js";
  import ProfileList from "./lib/ProfileList.svelte";
  import ProfileForm from "./lib/ProfileForm.svelte";
  import SettingsPanel from "./lib/SettingsPanel.svelte";

  let profiles = $state([]);
  let view = $state("list"); // "list" | "new" | "edit"
  let editName = $state(null);

  async function refresh() {
    profiles = await listProfiles();
    view = "list";
    editName = null;
  }

  onMount(refresh);
</script>

<main>
  {#if view === "list"}
    <ProfileList
      {profiles}
      onrefresh={refresh}
      onnew={() => (view = "new")}
      onedit={(name) => { editName = name; view = "edit"; }}
      onsettings={() => (view = "settings")}
    />
  {:else if view === "settings"}
    <SettingsPanel onclose={refresh} />
  {:else}
    <ProfileForm editName={view === "edit" ? editName : null} oncancel={refresh} onsaved={refresh} />
  {/if}
</main>

<style>
  :global(body) { font-family: system-ui, sans-serif; background: #161616; color: #eee; margin: 0; }
  main { max-width: 820px; margin: 0 auto; padding: 20px; }
</style>
