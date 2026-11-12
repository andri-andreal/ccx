<script>
  import { modalFocus } from "./modalFocus.js";
  import { teamRequest } from "./api.js";
  let { config = $bindable({}), profiles = [], project = "", info = null, platform = "", onprojectrefresh, onerror } = $props();
  let tab = $state("Roles");
  let catalogs = $state({});
  let api = $state({server:"",base_url:"http://127.0.0.1:8080",ready_path:"/",startup_timeout:30,checks:[]});
  let apiDirty = $state(false);
  let resource = $state(null);
  let resourceContent = $state("");
  let resourceLayer = $state("project");
  let resourceName = $state("");
  let resourceKind = $state("skills");
  let busy = $state(false);
  let notice = $state("");
  let extraKey = $state("");
  let confirmDelete = $state(false);
  const apiMode = $derived(!Object.hasOwn(config,"CCX_API_SERVER") ? "ask" : config.CCX_API_SERVER ? "command" : "running");
  const roles = [
    {id:"ORCHESTRATOR",name:"Orchestrator",icon:"◈",copy:"Understands the task, asks questions and picks a route."},
    {id:"PLANNER",name:"Planner",icon:"◇",copy:"Produces the implementation plan and acceptance criteria."},
    {id:"BUILDER",name:"Builder",icon:"⌘",copy:"Implements the approved work and addresses review findings."},
    {id:"REVIEWER",name:"Reviewer",icon:"◎",copy:"Independently checks the plan, code and verification evidence."},
  ];
  const eligible = $derived(profiles.filter(p => platform !== "win32" || !["kimi-cli","mcode"].includes(p.provider)));
  $effect(() => {
    for (const p of eligible) if (!catalogs[p.name]) {
      catalogs[p.name] = {models:[],efforts:["default","low","medium","high","xhigh","max"]};
      teamRequest("catalog", {name:p.name}).then(value => catalogs[p.name] = value).catch(e => onerror?.(String(e)));
    }
  });
  $effect(() => {
    if (info?.api) {
      const next = structuredClone($state.snapshot(info.api));
      next.base_url ||= "http://127.0.0.1:8080";
      next.checks = next.checks.map(check => ({...check,headers:(check.headers ?? []).map(h => Array.isArray(h) ? {name:h[0],value:h[1]} : h)}));
      api = next;
      apiDirty = false;
    }
  });
  function selectProfile(role) {
    config[`CCX_${role}_MODEL`] = "";
    config[`CCX_${role}_EFFORT`] = "default";
  }
  async function saveApi() {
    busy = true;
    try { await teamRequest("save_api", {project,api:$state.snapshot(api)}); apiDirty = false; notice="Project API checks saved."; await onprojectrefresh?.(); }
    catch(e) { onerror?.(String(e)); } finally { busy = false; }
  }
  function editResource(item) {
    resource=item; resourceContent=item.content; resourceLayer=item.layer === "builtin" ? "project" : item.layer;
    resourceName=item.name; resourceKind=item.kind;
  }
  async function saveResource() {
    busy = true;
    try {
      await teamRequest("save_resource",{project,layer:resourceLayer,kind:resourceKind,name:resourceName,content:resourceContent});
      notice=`${resourceKind === "agents" ? "Agent" : "Skill"} saved to ${resourceLayer} definitions.`;
      resource=null; await onprojectrefresh?.();
    } catch(e) { onerror?.(String(e)); } finally { busy=false; }
  }
  async function deleteResource() {
    busy=true;
    try {await teamRequest("delete_resource",{project,layer:resource.layer,kind:resource.kind,name:resource.name});resource=null;confirmDelete=false;notice="Override removed; the next available definition now applies.";await onprojectrefresh?.();}
    catch(e){onerror?.(String(e));}finally{busy=false;}
  }
  function addCheck() { api.checks.push({name:"Health check",method:"GET",path:"/",headers:[],body:"",expect_status:200,expect_contains:""}); apiDirty=true; }
</script>

<div class="subtabs" aria-label="Team configuration sections">
  {#each ["Roles","Limits & verification","API checks","Agents & skills","Advanced"] as item}
    <button class:active={tab === item} onclick={() => tab=item}>{item}</button>
  {/each}
</div>
<div class="config-body">
  {#if notice}<div class="notice" role="status"><span>{notice}</span><button class="ghost" onclick={() => notice=""}>Dismiss</button></div>{/if}
  {#if tab === "Roles"}
    <p class="section-copy">Assign a profile to each role. Models and effort inherit from the profile unless overridden. Each role can use the same profile.</p>
    {#if platform === "win32"}<p class="muted">On Windows, native Kimi/mcode cannot run as team roles. Use kimi/minimax provider profiles through Claude Code instead.</p>{/if}
    <div class="role-grid">
      {#each roles as role,i}
        <section class="panel role-editor">
          <div class="role-heading"><span class="role-icon">{role.icon}</span><div><small>0{i+1} / ROLE</small><h3>{role.name}</h3></div></div>
          <p class="section-copy">{role.copy}</p>
          <div class="form-grid">
            <label class="field full">CLI profile
              <select aria-label={`${role.name} CLI profile`} bind:value={config[`CCX_${role.id}`]} onchange={() => selectProfile(role.id)}>
                <option value="">{role.id === "ORCHESTRATOR" ? "No orchestrator · always plan" : "Choose a profile"}</option>
                {#each eligible as p}<option value={p.name}>{p.name} · {p.provider}</option>{/each}
              </select>
            </label>
            <label class="field full">Model override
              <input bind:value={config[`CCX_${role.id}_MODEL`]} list={`models-${role.id}`} placeholder={profiles.find(p => p.name === config[`CCX_${role.id}`])?.model || "Profile / CLI default"} disabled={!config[`CCX_${role.id}`]} />
              <datalist id={`models-${role.id}`}>{#each catalogs[config[`CCX_${role.id}`]]?.models ?? [] as model}<option value={model[0]}>{model[1]}</option>{/each}</datalist>
            </label>
            <label class="field">Reasoning effort
              <select bind:value={config[`CCX_${role.id}_EFFORT`]} disabled={!config[`CCX_${role.id}`]}>
                {#each catalogs[config[`CCX_${role.id}`]]?.efforts ?? ["default"] as level}<option value={level}>{level}</option>{/each}
              </select>
            </label>
            <label class="field">Fallback profile
              <select bind:value={config[`CCX_${role.id}_FALLBACK`]} disabled={!config[`CCX_${role.id}`]}>
                <option value="">None</option>
                {#each eligible.filter(p => p.name !== config[`CCX_${role.id}`]) as p}<option value={p.name}>{p.name}</option>{/each}
              </select>
            </label>
          </div>
          <p class="muted fallback-copy">Fallback handles overload, quota and context-limit failures.</p>
        </section>
      {/each}
    </div>
  {:else if tab === "Limits & verification"}
    <section class="panel">
      <h2>Loop limits</h2><p class="section-copy">Bound each task while keeping the plan, builder and reviewer agreement gates.</p>
      <div class="form-grid">
        {#each [["CCX_ROUNDS","Plan review rounds","Maximum planning and review attempts.","3"],["CCX_FIX_ROUNDS","Fix rounds","Additional revisions after the first build.","2"],["CCX_TIMEOUT","Timeout per agent (seconds)","Maximum time for each agent invocation.","900"],["CCX_KEEP_RUNS","Retained runs","0 keeps every run. Pruning happens when a session starts.","100"],["CCX_MAX_TOKENS","Token budget per task","Fresh input, output and cache writes; cache reads excluded. Checked between calls; 0 means unlimited.","0"],["CCX_MAX_COST_USD","Cost budget per task (USD)","Empty or 0 means unlimited. Applies where CLI cost data is available.","0"],["CCX_RETRIES","Transient failure retries","Retries per agent for temporary provider failures. 0 disables retries.","2"]] as field}
          <label class="field">{field[1]}<input inputmode="decimal" bind:value={config[field[0]]} placeholder={field[3]} /><small>{field[2]}</small></label>
        {/each}
      </div>
    </section>
    <section class="panel">
      <h2>Verification</h2><p class="section-copy">The host runs this command after each build. Acceptance requires a passing result on the reviewed code.</p>
      <label class="field">Verification command<input class="mono" bind:value={config.CCX_PROOF} list="proof-options" placeholder={info?.proof || "Auto-detect from the project"} /><small>Blank uses project detection; the session asks if it cannot find a command. Windows commands run through CMD.</small></label>
      <datalist id="proof-options">{#each info?.proofCandidates ?? [] as option}<option value={option[0]}>{option[1]}</option>{/each}</datalist>
      {#if info?.proof}<div class="detected"><span class="pill green">Detected</span><code>{info.proof}</code><button class="ghost" onclick={() => config.CCX_PROOF=info.proof}>Use command</button></div>{/if}
    </section>
  {:else if tab === "API checks"}
    <section class="panel">
      <h2>Team API defaults</h2><p class="section-copy">Saved with this team. Explicit team settings take precedence over project server and base URL settings. Leave them automatic to use the selected project's configuration.</p>
      <div class="form-grid">
        <label class="field">Server startup<select value={apiMode} onchange={event => {const mode=event.currentTarget.value;if(mode === "ask")delete config.CCX_API_SERVER;else config.CCX_API_SERVER=mode === "running" ? "" : "npm start";}}><option value="ask">Project settings / ask when needed</option><option value="running">Server is already running</option><option value="command">Start a server command</option></select></label>
        <label class="field">Local base URL override<input bind:value={config.CCX_API_BASE} placeholder="Project / http://127.0.0.1:8080" /></label>
        {#if apiMode === "command"}<label class="field full">Server command override<input class="mono" bind:value={config.CCX_API_SERVER} placeholder="npm start" /></label>{/if}
        <label class="field">API verification<select bind:value={config.CCX_API_SKIP}><option value="">Run standing and planner API checks</option><option value="0">Run checks (explicit)</option><option value="1">Skip API checks for this team</option></select><small>Skipping API checks still runs the verification command.</small></label>
      </div>
    </section>
    {#if !info}<div class="empty-state"><h2>Choose a project first</h2><p>API settings are stored in the project's .ccx directory.</p></div>
    {:else}
      <section class="panel">
        <div class="panel-title"><h2>Project API server</h2><span class="pill">.ccx/api.json</span></div>
        <p class="section-copy">Project defaults shared with the CLI. Requests stay on the local machine. Saving changes project files.</p>
        <div class="form-grid" oninput={() => apiDirty=true}>
          <label class="field full">Server command<input class="mono" bind:value={api.server} list="server-options" placeholder="Blank = server is already running" /><small>Use %PORT% in Windows commands. The host stops servers it starts after verification.</small></label>
          <datalist id="server-options">{#each info.serverCandidates as command}<option value={command}></option>{/each}</datalist>
          <label class="field">Local base URL<input bind:value={api.base_url} /></label>
          <label class="field">Ready path<input bind:value={api.ready_path} placeholder="/" /></label>
          <label class="field">Startup timeout (seconds)<input type="number" min="1" max="600" bind:value={api.startup_timeout} /></label>
        </div>
      </section>
      <section class="panel">
        <div class="panel-title"><h2>Standing API checks <span class="pill">{api.checks.length}</span></h2><button onclick={addCheck}>+ Add check</button></div>
        <p class="section-copy">Runs after verification, alongside any checks produced by the planner. Stored in .ccx/api-checks.json.</p>
        {#each api.checks as check,i}
          <div class="api-check" oninput={() => apiDirty=true}>
            <div class="panel-title"><h3>Check {i+1}</h3><button class="danger ghost" onclick={() => { api.checks.splice(i,1); apiDirty=true; }}>Remove</button></div>
            <div class="form-grid">
              <label class="field full">Name<input bind:value={check.name} /></label>
              <label class="field">Method<select bind:value={check.method} onchange={() => apiDirty=true}>{#each ["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"] as method}<option>{method}</option>{/each}</select></label>
              <label class="field">Path<input bind:value={check.path} placeholder="/health" /></label>
              <label class="field">Expected status<input type="number" min="0" max="599" bind:value={check.expect_status} /><small>0 accepts any status.</small></label>
              <label class="field">Response contains<input bind:value={check.expect_contains} placeholder="Optional text" /></label>
              <label class="field full">Request body<textarea rows="3" class="mono" bind:value={check.body} placeholder={'{"example":true}'}></textarea></label>
            </div>
            <div class="header-list"><h3>Request headers</h3>{#each check.headers as header,j}<div class="toolbar"><input aria-label="Header name" bind:value={header.name} placeholder="Content-Type" /><input aria-label="Header value" bind:value={header.value} placeholder="application/json" /><button aria-label="Remove header" onclick={() => {check.headers.splice(j,1);apiDirty=true;}}>×</button></div>{/each}<button class="ghost" onclick={() => {check.headers.push({name:"",value:""});apiDirty=true;}}>+ Header</button></div>
          </div>
        {/each}
        {#if !api.checks.length}<p class="muted">No standing checks. Add a request to verify your API every time.</p>{/if}
        <div class="toolbar"><button class="primary" disabled={busy || !apiDirty} onclick={saveApi}>{busy ? "Saving…" : "Save project API settings"}</button>{#if apiDirty}<span class="pill">Unsaved project changes</span>{/if}</div>
      </section>
    {/if}
  {:else if tab === "Agents & skills"}
    {#if !info}<div class="empty-state"><h2>Choose a project first</h2><p>Inspect the effective agents and edit their instructions here.</p></div>
    {:else}
      <section class="panel">
        <div class="panel-title"><h2>Agent definitions & skills</h2><button onclick={() => editResource({name:"",kind:"skills",layer:"project",content:"---\nname: my-skill\ndescription: Describe when this guidance applies\n---\n\nWrite your instructions here.\n"})}>+ New skill</button></div>
        <p class="section-copy">Project definitions override user definitions, which override built-ins. The session asks you to trust project instructions before using them. Built-ins can be copied to an editable layer.</p>
        {#each info.warnings ?? [] as warning}<p class="notice error">{warning}</p>{/each}
        <div class="effective-agents">{#each Object.entries(info.agents) as [role,agent]}<div><strong>{role}</strong><span class="pill">{agent.layer}</span><p class="muted">{agent.description || agent.name}</p><small>{agent.skills.map(s => s.name).join(" · ") || "No skills assigned"}</small></div>{/each}</div>
        <div class="resource-list">{#each info.resources as item}<button class="resource-row" onclick={() => editResource(item)}><span><strong>{item.name}</strong><small>{item.kind}/{item.name}.md</small></span><span class="pill">{item.layer}</span><span>{item.layer === "builtin" ? "View / copy →" : "Edit →"}</span></button>{/each}</div>
        <p class="muted">Assign skills in an agent's YAML header: <code>skills: [testing, security]</code>. Model and effort fields in definitions follow the same precedence as the CLI.</p>
      </section>
    {/if}
  {:else}
    <section class="panel"><h2>Workflow environment</h2><p class="section-copy">All saved keys, including future CLI settings, are preserved. Changes here are validated by the same workflow engine before saving.</p>
      <div class="advanced-grid">{#each Object.keys(config).sort() as key}<label class="field"><code>{key}</code><input class="mono" bind:value={config[key]} disabled={key === "CCX_TYPE"} /></label>{/each}</div>
      <div class="toolbar add-key"><input aria-label="Additional environment key" class="mono" bind:value={extraKey} placeholder="CCX_OPTION_NAME" /><button disabled={!/^[A-Z][A-Z0-9_]*$/.test(extraKey) || Object.hasOwn(config,extraKey)} onclick={() => {config[extraKey]="";extraKey="";}}>Add setting</button></div>
    </section>
  {/if}
</div>

{#if resource}
  <div class="modal-backdrop"><div use:modalFocus class="modal resource-modal" role="dialog" aria-modal="true" aria-label="Edit definition" tabindex="-1">
    <h2>{resource.layer === "builtin" ? "Copy built-in definition" : "Edit definition"}</h2>
    <div class="form-grid">
      <label class="field">Name<input bind:value={resourceName} disabled={resource.kind === "agents"} placeholder="my-skill" /></label>
      <label class="field">Save to<select bind:value={resourceLayer}><option value="project">Project · .ccx</option><option value="user">User · all projects</option></select></label>
      <label class="field full">Markdown instructions<textarea class="mono" rows="18" bind:value={resourceContent}></textarea></label>
    </div>
    {#if confirmDelete}<p class="notice error">Remove this {resource.layer} override? The next available definition will apply.<button class="danger" disabled={busy} onclick={deleteResource}>Remove override</button><button onclick={() => confirmDelete=false}>Keep</button></p>{/if}
    <div class="toolbar">{#if resource.layer !== "builtin" && resource.name}<button class="danger ghost" onclick={() => confirmDelete=true}>Delete override</button>{/if}<button onclick={() => {resource=null;confirmDelete=false;}}>Cancel</button><button class="primary" disabled={busy || !resourceName} onclick={saveResource}>Save definition</button></div>
  </div></div>
{/if}

<style>
  .config-body{padding:22px 0;display:grid;gap:20px}.role-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.role-heading{display:flex;align-items:center;gap:13px;margin-bottom:15px}.role-icon{width:39px;height:39px;border:1px solid #435237;background:#25301f;color:var(--accent);border-radius:9px;display:grid;place-items:center;font-size:24px}.role-heading small{color:var(--muted);font-size:9px;letter-spacing:1.2px}.role-heading h3{margin:4px 0 0;font-size:16px}.fallback-copy{margin:13px 0 0;font-size:10px}.role-editor .section-copy{min-height:38px}.detected{display:flex;gap:10px;align-items:center;margin-top:15px;font-size:12px;flex-wrap:wrap}.detected button{margin-left:auto}.api-check{border:1px solid var(--line);padding:18px;border-radius:8px;margin-bottom:18px;background:#13161d}.header-list{margin-top:16px;display:grid;gap:8px}.header-list h3{margin-bottom:0}.header-list input{flex:1;min-width:100px}.effective-agents{display:grid;grid-template-columns:repeat(2,1fr);gap:15px;margin-bottom:25px}.effective-agents>div{border:1px solid var(--line);padding:15px;border-radius:7px}.effective-agents strong{text-transform:capitalize;margin-right:12px}.effective-agents p{margin:10px 0 5px}.effective-agents small{color:var(--accent)}.resource-list{display:grid;gap:7px;margin-bottom:20px}.resource-row{display:flex;gap:14px;align-items:center;text-align:left}.resource-row>span:first-child{flex:1}.resource-row small{display:block;color:var(--muted);margin-top:4px}.resource-row>span:last-child{font-size:11px;color:var(--muted)}.advanced-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.advanced-grid code{font-size:10px}.resource-modal{width:850px}@media(max-width:1000px){.role-grid{gap:12px}.role-editor .form-grid{grid-template-columns:1fr}}@media(max-width:750px){.role-grid,.advanced-grid,.effective-agents{grid-template-columns:1fr}}
</style>
