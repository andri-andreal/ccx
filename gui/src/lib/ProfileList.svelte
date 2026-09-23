<script>
  import { launchProfile, copyCommand, deleteProfile, doctorProfile, certifyProfile } from "./api.js";

  let { profiles = [], onedit, onnew, onrefresh, onsettings } = $props();
  let message = $state("");
  let reports = $state({});
  let checking = $state({});
  let expanded = $state(null);

  $effect(() => {
    const names = profiles.map((p) => p.name);
    for (const name of names) {
      if (!reports[name] && !checking[name]) void check(name, false, false);
    }
  });

  async function check(name, announce = true, network = true) {
    checking = { ...checking, [name]: true };
    try {
      const report = await doctorProfile(name, network);
      reports = { ...reports, [name]: report };
      if (announce) {
        message = report.status === "pass"
          ? `${name} is ready`
          : `${name}: ${report.summary.failed} failed, ${report.summary.warnings} warnings`;
      }
    } catch (e) {
      reports = { ...reports, [name]: { status: "unknown", error: String(e), checks: [], capabilities: {} } };
      if (announce) message = `Could not check ${name}: ${e}`;
    } finally {
      checking = { ...checking, [name]: false };
    }
  }

  async function certify(name) {
    checking = { ...checking, [name]: true };
    try {
      const report = await certifyProfile(name);
      if (!report) {
        message = `Certification cancelled for ${name}`;
        return;
      }
      reports = { ...reports, [name]: report };
      message = report.status === "pass"
        ? `${name} passed compatibility certification`
        : `${name} certification: ${report.summary.failed} failed, ${report.summary.warnings} warnings`;
    } catch (e) {
      message = `Could not certify ${name}: ${e}`;
    } finally {
      checking = { ...checking, [name]: false };
    }
  }

  const reportStatus = (name) => checking[name] ? "running" : (reports[name]?.status ?? "unknown");
  const reportLabel = (name) => ({
    pass: "✓ ready",
    warn: "⚠ warning",
    fail: "✕ blocked",
    running: "… checking",
    unknown: "? unchecked",
  })[reportStatus(name)];

  function toggleReport(name) {
    expanded = expanded === name ? null : name;
  }

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

  // A router profile whose upstream is loopback is a local server (Ollama/vLLM/LM Studio).
  const isLocal = (p) =>
    !!p.router && /(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])/.test(p.upstreamUrl ?? "");
  const kindLabel = (p) => (!p.router ? "Direct" : isLocal(p) ? "Local" : "Via ccx-router");
  const kindClass = (p) => (!p.router ? "direct" : isLocal(p) ? "local" : "router");
  const fallbackCount = (p) => (p.fallbackUrls ?? "").split(",").filter((value) => value.trim()).length;
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
          <span class="kind {kindClass(p)}">{kindLabel(p)}</span>
          <button
            class="health {reportStatus(p.name)}"
            title="Show compatibility diagnostics"
            onclick={() => toggleReport(p.name)}
          >{reportLabel(p.name)}</button>
          {#if p.isolate}<span class="iso">⊘ isolated</span>{:else}<span class="iso shared">shared</span>{/if}
          <span class="model">{p.model ?? ""}</span>
        </div>
        <div class="sub">
          {#if p.router}
            ↳ {p.upstreamUrl ?? "(no upstream URL)"}{#if p.hasUpstreamKey} · key configured{/if}{#if fallbackCount(p)} · {fallbackCount(p)} fallback{fallbackCount(p) === 1 ? "" : "s"} · {p.attemptsPerUpstream ?? 1} attempt(s)/upstream{/if}
          {:else}
            {p.baseUrl ?? "(anthropic login)"}{#if p.hasToken} · token configured{/if}
          {/if}
        </div>
        {#if expanded === p.name}
          <div class="diagnostics">
            <div class="diaghead">
              <strong>Compatibility diagnostics</strong>
              <div class="diagactions">
                <button onclick={() => check(p.name)}>↻ Doctor</button>
                <button onclick={() => certify(p.name)}>◆ Certify</button>
              </div>
            </div>
            {#if reports[p.name]?.error}
              <p class="diagerror">{reports[p.name].error}</p>
            {:else if reports[p.name]}
              <div class="capabilities">
                {#each Object.entries(reports[p.name].capabilities ?? {}) as [capability, status]}
                  <span class="cap {status}">{capability}: {status}</span>
                {/each}
              </div>
              <ul class="checks">
                {#each reports[p.name].checks ?? [] as item (item.id)}
                  <li class={item.status}>
                    <span>{item.status === "pass" ? "✓" : item.status === "fail" ? "✕" : item.status === "warn" ? "⚠" : "–"}</span>
                    <div><strong>{item.label}</strong><small>{item.message}</small></div>
                  </li>
                {/each}
              </ul>
            {:else}
              <p>Checking…</p>
            {/if}
          </div>
        {/if}
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
  .kind { border-radius: 4px; padding: 0 6px; font-size: 11px; }
  .kind.direct { background: #2b3a55; color: #9cf; }
  .kind.router { background: #3a2b55; color: #c9f; }
  .kind.local  { background: #2b553a; color: #9fc; }
  .health { border: 1px solid transparent; border-radius: 999px; padding: 2px 7px; font-size: 11px; cursor: pointer; }
  .health.pass { background: #183f29; color: #8ee0a8; border-color: #286a40; }
  .health.warn { background: #493a13; color: #f2cf68; border-color: #755d1d; }
  .health.fail { background: #4a2020; color: #ff9c9c; border-color: #7b3333; }
  .health.running, .health.unknown { background: #303030; color: #aaa; border-color: #484848; }
  .iso { font-size: 12px; color: #e90; }
  .iso.shared { color: #888; }
  .model { margin-left: auto; color: #9cf; }
  .sub { color: #aaa; font-size: 13px; margin: 6px 0; }
  .diagnostics { margin: 10px 0; padding: 10px; border: 1px solid #333; border-radius: 6px; background: #181818; }
  .diaghead { display: flex; justify-content: space-between; align-items: center; }
  .diagactions { display: flex; gap: 6px; }
  .diaghead button { font-size: 12px; }
  .diagerror { color: #f99; font-size: 13px; }
  .capabilities { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0; }
  .cap { padding: 1px 6px; border-radius: 999px; background: #303030; color: #aaa; font-size: 11px; }
  .cap.pass { color: #8ee0a8; background: #183f29; }
  .cap.warn { color: #f2cf68; background: #493a13; }
  .cap.fail { color: #ff9c9c; background: #4a2020; }
  .checks { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 6px; }
  .checks li { display: grid; grid-template-columns: 18px 1fr; gap: 4px; color: #aaa; }
  .checks li.pass { color: #8ee0a8; }
  .checks li.warn { color: #f2cf68; }
  .checks li.fail { color: #ff9c9c; }
  .checks small { display: block; color: #999; overflow-wrap: anywhere; }
  .actions { display: flex; gap: 8px; }
  .danger { color: #f66; }
  .msg { color: #6c9; }
  .empty { color: #888; }
</style>
