<script>
  import { modalFocus } from "./modalFocus.js";
  import { onMount, onDestroy } from "svelte";
  import { teamRequest, pickTeamProject, startTeamSession, sendTeamInput, closeTeamSession } from "./api.js";
  import TeamConfig from "./TeamConfig.svelte";
  import TeamSession from "./TeamSession.svelte";
  let { profiles = [], onprofiles } = $props();
  let overview = $state(null);
  let selected = $state("");
  let name = $state("");
  let config = $state({});
  let savedConfig = $state("");
  let creating = $state(false);
  let tab = $state("Session");
  let project = $state(localStorage.getItem("ccx-team-project") || "");
  let info = $state(null);
  let loadedProject = $state("");
  let busy = $state(false);
  let loadingProject = $state(false);
  let error = $state("");
  let notice = $state("");
  let sessionId = $state(null);
  let runtime = $state({});
  let logs = $state([]);
  let ready = $state(false);
  let prompt = $state(null);
  let answer = $state("");
  let document = $state(null);
  let confirmAction = $state(null);
  let runs = $state([]);
  let historyBusy = $state(false);
  let artifact = $state(null);
  const dirty = $derived(JSON.stringify(config) !== savedConfig);
  const activeTeam = $derived(overview?.teams.find(t => t.name === selected));
  let projectGeneration = 0;
  let sessionGeneration = 0;
  let runtimeTheme = "";

  async function refresh(preferred = selected) {
    try {
      overview=await teamRequest("overview");
      if(!creating) {
        const team=overview.teams.find(t => t.name === preferred) || overview.teams[0];
        if(team) select(team.name,true);
        else { selected="";name="";config={};savedConfig="{}"; }
      }
    } catch(e) { error=String(e); }
  }
  function select(value, force=false) {
    if(sessionId) return;
    if(!force && dirty) { confirmAction={title:"Discard unsaved team changes?",body:"The saved team configuration stays as it was.",label:"Discard changes",action:() => select(value,true)}; return; }
    const team=overview?.teams.find(t => t.name === value);
    if(!team) return;
    selected=value;name=value;creating=false;
    config=structuredClone($state.snapshot(team.config || overview.defaults));
    savedConfig=JSON.stringify(config);
    error=team.error || "";notice="";runs=[];
  }
  function newTeam(copy=false, force=false) {
    if(sessionId) return;
    if(!force && dirty) {confirmAction={title:"Discard unsaved team changes?",body:"Create a new team using the saved defaults.",label:"Continue",action:() => newTeam(copy,true)};return;}
    const first=profiles.find(p => overview?.platform !== "win32" || !["kimi-cli","mcode"].includes(p.provider))?.name || "";
    const next=copy ? structuredClone($state.snapshot(config)) : {...overview.defaults};
    if(!copy) for(const role of ["ORCHESTRATOR","PLANNER","BUILDER","REVIEWER"]) next[`CCX_${role}`]=first;
    name=copy ? `${selected}-copy` : "";selected="";creating=true;config=next;savedConfig="";tab="Configuration";error="";notice="";
  }
  async function save() {
    busy=true;error="";
    try {
      const result=await teamRequest("save",{name,config:$state.snapshot(config),create:creating});
      creating=false;savedConfig=JSON.stringify(result.config);await refresh(result.name);notice="Team configuration saved. CLI and desktop share this preset.";
    } catch(e) {error=String(e);} finally {busy=false;}
  }
  async function remove() {
    try {await teamRequest("delete",{name:selected});await refresh("");notice="Team removed. Run history was preserved.";}
    catch(e) {error=String(e);}
  }
  async function setDefault() {
    try {await teamRequest("default",{name:selected});overview.defaultTeam=selected;notice=`${selected} is now the default for ccx.`;}catch(e){error=String(e);}
  }
  async function loadProject() {
    const generation=++projectGeneration;
    const requestedProject=project;
    info=null;loadedProject="";
    if(!project.trim()) return;
    loadingProject=true;
    try {
      const result=await teamRequest("project",{project:requestedProject});
      if(generation !== projectGeneration || project !== requestedProject) return;
      info=result;project=result.project;loadedProject=result.project;localStorage.setItem("ccx-team-project",result.project);
    }catch(e){if(generation === projectGeneration)error=String(e);}finally{if(generation === projectGeneration)loadingProject=false;}
  }
  async function chooseProject() {
    try {const value=await pickTeamProject();if(value){project=value;await loadProject();}}catch(e){error=String(e);}
  }
  function receive(event) {
    if(event.kind === "state") {runtime=event;if(event.theme && event.theme !== runtimeTheme){runtimeTheme=event.theme;globalThis.document.documentElement.dataset.theme=event.theme;}}
    else if(event.kind === "ready") {ready=true;void loadHistory();}
    else if(event.kind === "prompt") {prompt=event;answer="";ready=false;}
    else if(event.kind === "prompt_closed") {if(prompt?.id === event.id)prompt=null;}
    else if(event.kind === "document") document=event;
    else if(event.kind === "log") logs=[...logs.slice(-199),event.text];
    else if(event.kind === "event" && event.event.type === "task_started")ready=false;
    else if(event.kind === "requests") {runtime={...runtime,requests:event.requests};}
    else if(event.kind === "exited" || event.kind === "disconnected" || event.ok === false) {
      sessionId=null;ready=false;prompt=null;
      if(event.error)error=event.error;
      else if(event.kind === "disconnected")notice="Team session closed. Saved run artifacts remain available in History.";
    }
  }
  async function openSession() {
    if(dirty || creating){error="Save the team configuration before opening a session.";tab="Configuration";return;}
    if(!loadedProject || project !== loadedProject){await loadProject();if(!loadedProject)return;}
    busy=true;error="";notice="";runtime={};runtimeTheme="";logs=[];ready=false;
    const generation=++sessionGeneration;
    try {sessionId=await startTeamSession({name:selected,project:loadedProject},event => {if(generation === sessionGeneration)receive(event);});tab="Session";}
    catch(e){error=String(e);}finally{busy=false;}
  }
  async function send(message) {
    if(!sessionId)return;
    try {if(message.type === "command")ready=false;await sendTeamInput(sessionId,message);}
    catch(e){error=String(e);throw e;}
  }
  async function close() {
    if(!sessionId)return;
    ++sessionGeneration;
    try {await closeTeamSession(sessionId);sessionId=null;ready=false;prompt=null;await refresh(selected);}catch(e){error=String(e);}
  }
  async function respond(index=null, textOverride=null) {
    if(!prompt)return;
    try {await send({type:"answer",id:prompt.id,index,text:textOverride ?? answer});}catch{ /* error displayed above */ }
  }
  async function loadHistory() {
    if(!selected)return;
    historyBusy=true;
    try {runs=await teamRequest("history",{name:selected});}catch(e){error=String(e);}finally{historyBusy=false;}
  }
  async function viewArtifact(run,file) {
    try {const body=await teamRequest("artifact",{run,file});artifact={title:`${run} / ${file}`,body};}catch(e){error=String(e);}
  }
  onMount(async () => {await refresh();try{const display=await teamRequest("display");globalThis.document.documentElement.dataset.theme=display.theme || "default";}catch(e){error=String(e);}if(project)await loadProject();});
  onDestroy(() => {if(sessionId)void closeTeamSession(sessionId);});
</script>

<div class="workspace" class:session-view={tab === "Session" && !!selected}>
  <header class="workspace-header"><div><div class="eyebrow">WORKSPACE / TEAMS</div><h1>Build with your team<span class="title-dot">.</span></h1><p>Different minds. One verified result.</p></div><button class="primary" disabled={!overview || busy || !!sessionId} onclick={() => newTeam()}>+ New team</button></header>
  {#if error}<div class="notice error" role="alert">{error}<button class="ghost" onclick={() => error=""}>Dismiss</button></div>{/if}
  {#if notice}<div class="notice" role="status"><span>{notice}</span><button class="ghost" onclick={() => notice=""}>Dismiss</button></div>{/if}
  {#if !overview}
    <div class="empty-state"><h2>{error ? "Team workspace could not load" : "Loading your workspace…"}</h2>{#if error}<p>Python 3.10+ is required for the Team runtime.</p><button onclick={() => refresh()}>Retry</button>{/if}</div>
  {:else if !overview.teams.length && !creating}
    <div class="empty-state onboarding"><div class="empty-icon">◈</div><h2>A team for the way you build.</h2><p>Connect your profiles to four roles, configure how they work together, and follow every step from one workspace.</p><div class="onboarding-roles"><span>◈ Orchestrator</span><span>◇ Planner</span><span>⌘ Builder</span><span>◎ Reviewer</span></div><button class="primary" onclick={() => newTeam()}>Create your first team →</button>{#if !profiles.length}<p class="muted">Start by <button class="text-button" onclick={onprofiles}>creating a model profile</button> for Claude or Codex.</p>{/if}</div>
  {:else}
    <div class="team-toolbar">
      {#if creating}<label class="team-name">New team<input aria-label="Team name" bind:value={name} placeholder="e.g. product-team" /></label>
      {:else}<label class="team-name">TEAM<select value={selected} disabled={!!sessionId} onchange={event => select(event.currentTarget.value)}>{#each overview.teams as team}<option value={team.name}>{team.name}{overview.defaultTeam === team.name ? " · default" : ""}</option>{/each}</select></label>{/if}
      <div class="toolbar team-actions">{#if dirty}<span class="pill">Unsaved changes</span>{/if}{#if !creating}<button class="ghost" disabled={!!sessionId} onclick={() => newTeam(true)}>Duplicate</button><button class="ghost" disabled={!!sessionId || overview.defaultTeam === selected} onclick={setDefault}>Set default</button><button class="danger ghost" disabled={!!sessionId} onclick={() => confirmAction={title:`Delete team “${selected}”?`,body:"This removes the workflow preset. Your model profiles and run history are preserved.",label:"Delete team",action:remove}}>Delete</button>{/if}<button class="primary" disabled={busy || !!sessionId || !name || !dirty} onclick={save}>{busy ? "Working…" : "Save team"}</button></div>
    </div>
    <div class="project-bar"><span class="folder-icon">▱</span><label class="project-field"><span>PROJECT DIRECTORY</span><input aria-label="Project directory" bind:value={project} disabled={!!sessionId} onkeydown={event => {if(event.key === "Enter")void loadProject();}} placeholder="Choose the checkout your team will work in" /></label><button class="ghost" disabled={!!sessionId || loadingProject} onclick={chooseProject}>Browse…</button><button disabled={!!sessionId || loadingProject || !project} onclick={loadProject}>{loadingProject ? "Loading…" : "Load"}</button>{#if loadedProject === project && info}<span class="pill green">Loaded</span>{/if}</div>
    <div class="workspace-tabs"><div class="subtabs">{#each ["Session","Configuration","History"] as item}<button class:active={tab === item} onclick={() => {tab=item;if(item === "History")void loadHistory();}}>{item}{#if item === "Configuration" && dirty}<span class="unsaved-dot"></span>{/if}</button>{/each}</div><span class="workspace-meta">{sessionId ? "Session connected" : creating ? "New workflow" : "Saved workflow"}</span></div>
    <div hidden={tab !== "Session"} class="session-body">
      <TeamSession {runtime} connected={!!sessionId} {ready} {logs} {config} project={loadedProject} disabled={busy || dirty || creating} onsend={send} onstart={openSession} onclose={() => confirmAction={title:"Close this session?",body:"A running task will be stopped. Run artifacts are preserved for inspection and recovery.",label:"Close session",action:close}} oncancel={() => send({type:"cancel"})} />
    </div>
    <div hidden={tab !== "Configuration"}>
      {#if sessionId}<div class="notice">A session is open. Use “Live session configuration” in Session actions to adjust it, or close the session to edit the saved team.</div>{/if}
      {#if !profiles.length}<div class="notice">Create model profiles before assigning team roles.<button onclick={onprofiles}>Create profile</button></div>{/if}
      <fieldset class="config-fieldset" disabled={!!sessionId}><TeamConfig bind:config {profiles} project={loadedProject} {info} platform={overview.platform} onprojectrefresh={loadProject} onerror={value => error=value} /></fieldset>
    </div>
    {#if tab === "History"}
      <div class="history-header"><div><h2>Run history</h2><p class="muted">Saved tasks, verification evidence and artifacts for {selected}.</p></div><button onclick={loadHistory} disabled={historyBusy}>{historyBusy ? "Loading…" : "Refresh"}</button></div>
      {#if !runs.length}<div class="empty-state"><h2>No recorded tasks yet</h2><p>Run your first task from the Session tab.</p></div>{/if}
      <div class="history-list">{#each runs as run}<section class="panel"><div class="history-title"><span class="pill" class:green={run.result.status === "completed"}>{run.result.status || "In progress / interrupted"}</span><small>{run.started}</small></div><h3>{run.task}</h3><p class="muted mono">{run.repo}</p><div class="text-output">{run.result.summary || run.result.error || "No final result recorded."}</div><details><summary>Artifacts ({run.files.length})</summary><div class="artifact-buttons">{#each run.files.filter(file => !file.endsWith(".zip")) as file}<button onclick={() => viewArtifact(run.id,file)}>{file}</button>{/each}</div></details><p class="muted">Use “Restore session” in Session actions to continue this conversation in its original project.</p></section>{/each}</div>
    {/if}
  {/if}
</div>

{#if prompt}
  <div class="modal-backdrop"><div use:modalFocus class="modal" role="dialog" aria-modal="true" aria-label={prompt.title} tabindex="-1"><span class="pill green">TEAM NEEDS YOUR INPUT</span><h2 class="prompt-title">{prompt.title}</h2>{#if prompt.subtitle}<div class="text-output prompt-copy">{prompt.subtitle}</div>{/if}{#if prompt.input}<label class="field">Your response<textarea rows="5" bind:value={answer}></textarea></label><div class="toolbar"><button onclick={() => respond(null, "")}>Cancel</button><button class="primary" onclick={() => respond(null)}>Continue</button></div>{:else}<div class="option-list">{#each prompt.options ?? [] as option,i}<button onclick={() => respond(i)}>{option.label}{#if option.note}<small>{option.note}</small>{/if}</button>{/each}</div><div class="toolbar"><button class="ghost" onclick={() => respond()}>Cancel / back</button></div>{/if}</div></div>
{/if}
{#if document || artifact}
  {@const shown=artifact || document}
  <div class="modal-backdrop"><div use:modalFocus class="modal document-modal" role="dialog" aria-modal="true" aria-label={shown.title} tabindex="-1"><h2>{shown.title}</h2><pre>{shown.body}</pre>{#if shown.editable}<p class="muted">Edit this definition under Configuration → Agents & skills after closing the session.</p>{/if}<div class="toolbar"><button onclick={() => {document=null;artifact=null;}}>Close</button></div></div></div>
{/if}
{#if confirmAction}
  <div class="modal-backdrop"><div use:modalFocus class="modal" role="dialog" aria-modal="true" aria-label={confirmAction.title} tabindex="-1"><h2>{confirmAction.title}</h2><p class="muted">{confirmAction.body}</p><div class="toolbar"><button onclick={() => confirmAction=null}>Cancel</button><button class="primary" onclick={() => {const action=confirmAction.action;confirmAction=null;void action();}}>{confirmAction.label}</button></div></div></div>
{/if}

<style>
  .workspace.session-view{height:100vh;display:flex;flex-direction:column;overflow:hidden;padding-top:20px}.session-view .workspace-header{margin-bottom:16px;flex-shrink:0}.session-view .workspace-header p,.session-view .eyebrow{display:none}.session-view .workspace-header h1{font-size:22px;margin-bottom:0}.session-view .team-toolbar{margin-bottom:12px;flex-shrink:0}.session-view .project-bar{padding:9px 14px;margin-bottom:8px;flex-shrink:0}.session-view .workspace-tabs{flex-shrink:0}.session-body{flex:1;min-height:0}.session-view>.notice{flex-shrink:0}
  @media(max-width:900px){.workspace.session-view{height:auto;min-height:100vh;overflow:visible}.session-body{flex:auto}}
  .workspace{padding:30px 30px 0;max-width:1600px;margin:auto}.workspace-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:25px;gap:16px}.eyebrow{font-size:9px;letter-spacing:1.7px;color:#7e8a9e;margin-bottom:11px}.workspace-header h1{margin-bottom:7px}.title-dot{color:var(--accent)}.workspace-header p{color:var(--muted);font-size:12px;margin-bottom:0}.workspace>.notice{margin-bottom:15px}.team-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;flex-wrap:wrap}.team-name{display:flex;align-items:center;gap:13px;font-size:9px;letter-spacing:1px;color:var(--muted)}.team-name select,.team-name input{min-width:190px;font-size:13px;font-weight:600;letter-spacing:0}.team-actions button{font-size:11px;padding:8px 11px}.project-bar{display:flex;gap:12px;align-items:center;padding:13px 16px;background:#171b23;border:1px solid var(--line);border-radius:8px;margin-bottom:16px}.folder-icon{font-size:26px;color:#8c9c7e}.project-field{display:flex;flex-direction:column;gap:4px;flex:1;min-width:0}.project-field>span{font-size:8px;letter-spacing:1px;color:#7c879c}.project-field input{background:transparent;border:0;padding:3px 0;font-size:12px;font-family:Consolas,monospace;width:100%;color:#c6cedc}.project-field input:focus{outline:none}.project-bar button{font-size:11px;padding:7px 10px}.workspace-tabs{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);margin-bottom:2px}.workspace-tabs .subtabs{border:0;gap:25px}.workspace-meta{font-size:9px;color:#78859b}.unsaved-dot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--accent);margin-left:7px;vertical-align:middle}.config-fieldset{margin:0;padding:0;border:0;min-width:0}.history-header{display:flex;justify-content:space-between;align-items:center;margin:25px 0}.history-header h2{margin-bottom:6px}.history-header p{margin:0}.history-list{display:grid;gap:18px;padding-bottom:30px}.history-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.history-title small{color:var(--muted)}.history-list h3{line-height:1.6;font-size:15px;white-space:pre-wrap}.history-list details{margin:20px 0;font-size:12px}.history-list summary{cursor:pointer;color:var(--muted)}.artifact-buttons{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.artifact-buttons button{font-size:10px;padding:6px 8px}.history-list .mono{font-size:10px}.history-list .panel>p:last-child{margin-bottom:0;font-size:10px}.prompt-title{margin:18px 0 12px}.prompt-copy{color:#aeb8c9;margin-bottom:20px;max-height:40vh;overflow:auto;font-size:12px}.document-modal{width:950px}.document-modal h2{font-size:14px;overflow-wrap:anywhere}.onboarding{padding-top:80px}.onboarding-roles{display:flex;gap:9px;flex-wrap:wrap;justify-content:center;margin:25px 0}.onboarding-roles span{border:1px solid var(--line);border-radius:7px;padding:10px;font-size:11px;color:#becbb1}.onboarding>.primary{margin-bottom:15px}.text-button{border:0;background:transparent;color:var(--accent);padding:0;font-size:12px;text-decoration:underline}@media(max-width:1100px){.workspace{padding:25px 20px 0}.team-actions{gap:5px}.team-actions button{font-size:10px;padding:7px 8px}}@media(max-width:750px){.workspace{padding:22px 15px 0}.workspace-header h1{font-size:22px}.workspace-header>.primary{font-size:11px;white-space:nowrap}.project-bar{gap:8px;padding:10px}.project-bar>.pill{display:none}.workspace-meta{display:none}.team-actions{width:100%}.team-toolbar{gap:12px}.history-title{gap:10px}.history-title small{font-size:9px}}
</style>
