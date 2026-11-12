<script>
  import { modalFocus } from "./modalFocus.js";
  import Markdown from "./Markdown.svelte";
  import { tick } from "svelte";
  let { runtime = {}, connected = false, ready = false, logs = [], onsend, onstart, onclose, oncancel, config = {}, project = "", disabled = false } = $props();
  let draft = $state("");
  let pane = $state("Conversation");
  let selectedRequest = $state(null);
  let feed = $state(null);
  let autoScroll = $state(true);
  let mode = $state("auto");
  let agentLog = $state(null);
  const effective = $derived(runtime.config || config);
  const roles = [{key:"orchestrate",name:"Orchestrator",id:"ORCHESTRATOR",icon:"◈"},{key:"plan",name:"Planner",id:"PLANNER",icon:"◇"},{key:"build",name:"Builder",id:"BUILDER",icon:"⌘"},{key:"review",name:"Reviewer",id:"REVIEWER",icon:"◎"}];
  const commands = [["/build","Build approved plan"],["/resume","Resume failed task"],["/undo","Undo last task"],["/redo","Redo task"],["/diff","View changes"],["/files","Project files"],["/history","Restore session"],["/queue","Queued tasks"],["/result","Last result"],["/roles","Agent definitions"],["/config","Live session configuration"],["/help","All commands"]];
  $effect(() => { if (runtime.mode) mode=runtime.mode; });
  $effect(() => { runtime.timeline; if (autoScroll) tick().then(() => { if(feed) feed.scrollTop=feed.scrollHeight; }); });
  async function send() {
    if (!draft.trim() || !connected) return;
    const text=draft; draft="";
    try { await onsend({type:"command",text}); } catch { draft=text; }
  }
  async function setMode() { if(connected) await onsend({type:"command",text:`/mode ${mode}`}); }
  function keydown(event) { if(event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(); } }
</script>

<div class="session-layout">
<div class="role-rail">
  {#each roles as role,i}
    {@const panel=runtime.panels?.[role.key]}
    <div class="role-card" class:running={panel?.status === "running"} class:done={panel?.status === "done"}>
      <div class="role-top"><span>{role.icon}</span><strong>{role.name}</strong><span class="role-status">{panel?.status || (effective[`CCX_${role.id}`] ? "ready" : "optional")}</span></div>
      <p>{panel?.profile || effective[`CCX_${role.id}`] || "Not assigned"}</p>
      <small class="model-name" title={runtime.agents?.[role.key]?.skills?.join(" · ") || ""}>{panel?.model || effective[`CCX_${role.id}_MODEL`] || "Profile default"} · {panel?.effort || effective[`CCX_${role.id}_EFFORT`] || "default"} effort</small>
      <div class="role-meta"><small>{panel?.totalUsage ? `${((panel.totalUsage.input || 0)+(panel.totalUsage.output || 0)+(panel.totalUsage.cache_read || 0)+(panel.totalUsage.cache_write || 0)).toLocaleString()} tokens` : "Ready when you are"}</small>{#if panel?.lines?.length}<button class="agent-log-button" onclick={() => agentLog=role.key}>Activity →</button>{/if}</div>
    </div>
  {/each}
</div>
{#if runtime.flow}<div class="flow-strip"><span class="status-dot"></span>Latest hand-off: {roles.find(r => r.key === runtime.flow[0])?.name} → {roles.find(r => r.key === runtime.flow[1])?.name}</div>{/if}
<div class="session-grid">
  <section class="conversation-panel">
    <div class="conversation-tabs"><div class="subtabs">{#each ["Conversation","Requests","Result"] as item}<button class:active={pane === item} onclick={() => pane=item}>{item}{#if item === "Requests"}<span class="pill">{runtime.requests?.length || 0}</span>{/if}</button>{/each}</div><span class="connection"><span class="status-dot" class:offline={!connected}></span>{connected ? ready ? "Ready" : "Working" : "Offline"}</span></div>
    {#if pane === "Conversation"}
      <div class="feed" bind:this={feed} onscroll={() => {autoScroll=feed.scrollHeight-feed.scrollTop-feed.clientHeight < 80;}} aria-live="polite" aria-label="Team conversation">
        {#if !runtime.timeline?.length}
          <div class="welcome"><div class="welcome-symbol">◈</div><h2>Your team, one conversation.</h2><p>Describe a task. Your agents will plan, build, verify and review it together.</p><div class="workflow-line"><span>Route</span> → <span>Plan</span> → <span>Build</span> → <span>Verify & review</span></div><small>{connected ? "Choose a mode and send your first task below." : "Open a session to start working in the selected project."}</small></div>
        {/if}
        {#each runtime.timeline ?? [] as entry,i}
          <article class="message" class:user={entry[0] === "YOU"} class:failure={entry[2] === "error"}>
            <div class="message-label"><span class="avatar">{entry[0] === "YOU" ? "Y" : entry[0].slice(0,1)}</span><strong>{entry[0] === "YOU" ? "You" : entry[0]}</strong>{#if entry[2]}<small>{entry[2]}</small>{/if}<span class="message-number">{String(i+1).padStart(2,"0")}</span></div>
            {#if entry[2] === "http"}<details><summary>HTTP request</summary><pre>{entry[1]}</pre></details>{:else if entry[0] !== "YOU" && (!entry[2] || entry[2] === "text")}<div class="text-output"><Markdown text={entry[1]} /></div>{:else}<div class="text-output">{entry[1]}</div>{/if}
          </article>
        {/each}
      </div>
      {#if !autoScroll}<button class="jump" onclick={() => {autoScroll=true;if(feed)feed.scrollTop=feed.scrollHeight;}}>Jump to latest ↓</button>{/if}
    {:else if pane === "Requests"}
      <div class="request-feed">{#if !runtime.requests?.length}<div class="empty-state"><h3>No requests yet</h3><p>HTTP exchanges made by agents appear here with secrets masked.</p></div>{/if}
        {#each runtime.requests ?? [] as req,i}<button class="request-row" onclick={() => selectedRequest=i}><span class="method">{req.method}</span><span>{req.url}</span><span class="pill">{req.status || "—"}</span><small>{req.phase}</small></button>{/each}
      </div>
    {:else}
      <div class="result-feed"><span class="pill" class:green={runtime.resultStatus === "completed"}>{runtime.resultStatus || "No result yet"}</span><div class="text-output"><Markdown text={runtime.result || "The final summary and verification outcome appear here when a task finishes."} /></div>{#if runtime.run}<p class="muted mono">Artifacts: {runtime.run}</p>{/if}</div>
    {/if}
    <div class="composer">
      <div class="composer-top"><label>Mode <select aria-label="Session mode" bind:value={mode} onchange={setMode} disabled={!connected || !ready}><option value="auto">Auto · route the task</option><option value="plan">Plan · review before building</option><option value="build">Build · direct implementation</option><option value="inspect">Inspect · read only</option></select></label>{#if runtime.approved}<button class="primary" onclick={() => onsend({type:"command",text:"/build"})}>Build approved plan</button>{/if}</div>
      <label class="sr-only" for="team-task">Task for your team</label><textarea id="team-task" bind:value={draft} rows="2" onkeydown={keydown} disabled={!connected} placeholder={connected ? "What should your team work on? Reference files with @path…" : "Open a session to send tasks…"}></textarea>
      <div class="composer-bottom"><small>Enter to send · Shift+Enter for a new line{#if connected && !ready} · Tasks are queued{/if}</small>{#if connected}<button class="primary" disabled={!draft.trim()} onclick={send}>Send task ↑</button>{:else}<button class="primary" disabled={disabled || !project} onclick={onstart}>Open session →</button>{/if}</div>
    </div>
  </section>
  <aside class="session-sidebar">
    <section><div class="sidebar-heading"><h3>Session overview</h3><span class="pill">{runtime.route || mode}</span></div><dl><dt>Verification</dt><dd class="mono">{effective.CCX_PROOF || "Auto-detect"}</dd><dt>Plan / fix rounds</dt><dd>{effective.CCX_ROUNDS} / {effective.CCX_FIX_ROUNDS}</dd><dt>Agent timeout</dt><dd>{effective.CCX_TIMEOUT}s</dd></dl></section>
    <section><h3>Usage this task</h3><div class="usage-total">{((runtime.usage?.input || 0)+(runtime.usage?.cache_read || 0)+(runtime.usage?.cache_write || 0)+(runtime.usage?.output || 0)).toLocaleString()}<small>tokens</small></div><dl><dt>Fresh input</dt><dd>{(runtime.usage?.input || 0).toLocaleString()}</dd><dt>Output</dt><dd>{(runtime.usage?.output || 0).toLocaleString()}</dd><dt>Cache read</dt><dd>{(runtime.usage?.cache_read || 0).toLocaleString()}</dd><dt>Cache write</dt><dd>{(runtime.usage?.cache_write || 0).toLocaleString()}</dd><dt>Cost reported</dt><dd>{runtime.usage?.cost_usd === undefined ? "Not reported" : `$${Number(runtime.usage.cost_usd).toFixed(4)}`}</dd><dt>Token limit</dt><dd>{!Number(effective.CCX_MAX_TOKENS) ? "Unlimited" : effective.CCX_MAX_TOKENS}</dd><dt>USD limit</dt><dd>{!Number(effective.CCX_MAX_COST_USD) ? "Unlimited" : effective.CCX_MAX_COST_USD}</dd></dl></section>
    <section><h3>Session actions</h3><div class="quick-actions">{#each commands as command}<button disabled={!connected || !ready} title={command[1]} onclick={() => onsend({type:"command",text:command[0]})}>{command[1]}</button>{/each}</div>{#if connected}<div class="session-controls"><button class="danger" onclick={oncancel} disabled={ready}>Stop current task</button><button class="ghost" onclick={onclose}>Close session</button></div>{/if}</section>
    {#if runtime.queued?.length}<section><h3>Queued tasks</h3>{#each runtime.queued as task}<p class="muted">{task}</p>{/each}</section>{/if}
    {#if logs.length}<details class="runtime-logs"><summary>Runtime messages ({logs.length})</summary><pre>{logs.slice(-50).join("\n")}</pre></details>{/if}
  </aside>
</div>
<div class="session-footer"><span class="status-dot" class:offline={!connected}></span><span>{runtime.footer || "Select a project and configure your team to get started."}</span></div>
</div>
{#if selectedRequest !== null && runtime.requests?.[selectedRequest]}
  <div class="modal-backdrop"><div use:modalFocus class="modal" role="dialog" aria-modal="true" aria-label="HTTP request" tabindex="-1"><h2>HTTP exchange</h2><pre>{JSON.stringify(runtime.requests[selectedRequest],null,2)}</pre><div class="toolbar"><button onclick={() => selectedRequest=null}>Close</button></div></div></div>
{/if}
{#if agentLog}
  <div class="modal-backdrop"><div use:modalFocus={() => agentLog=null} class="modal" role="dialog" aria-modal="true" aria-label="Agent activity" tabindex="-1"><h2>{roles.find(r => r.key === agentLog)?.name} activity</h2><p class="muted">{runtime.panels?.[agentLog]?.profile} · {runtime.panels?.[agentLog]?.model}</p><pre>{(runtime.panels?.[agentLog]?.lines || []).join("\n\n")}</pre><div class="toolbar"><button onclick={() => agentLog=null}>Close</button></div></div></div>
{/if}

<style>
  .role-meta{display:flex;align-items:center;justify-content:space-between;margin-top:8px;min-height:14px}.role-meta>small{color:var(--muted);font-size:9px}.role-meta .agent-log-button{margin-top:0}.role-top>strong{font-size:12px}.session-layout .composer textarea{min-height:40px}.session-layout .composer{padding:11px 17px}.session-layout .composer-top{margin-bottom:8px}.session-layout .model-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .session-layout{display:flex;flex-direction:column;min-height:0;height:100%}.session-layout .role-rail{flex-shrink:0;margin:14px 0}.session-layout .role-card{padding:12px 14px}.session-layout .role-top{margin-bottom:8px}.session-layout .session-grid{flex:1;min-height:0}.session-layout .conversation-panel{height:100%}.session-layout .feed,.session-layout .request-feed,.session-layout .result-feed{height:auto;min-height:100px;flex:1}.session-layout .composer{flex-shrink:0}.session-layout .session-sidebar{overflow:auto;scrollbar-width:thin}.session-layout .session-footer{flex-shrink:0;padding:10px 0}.session-layout .flow-strip{margin:0 0 10px;flex-shrink:0}@media(max-width:900px){.session-layout{height:auto}.session-layout .conversation-panel{min-height:650px}.session-layout .session-sidebar{overflow:visible}.session-layout .feed,.session-layout .request-feed,.session-layout .result-feed{height:400px;flex:auto}}
  .agent-log-button{padding:3px 0;font-size:9px;color:var(--accent);background:transparent;border:0;margin-top:7px}.flow-strip{font-size:10px;color:var(--muted);margin:-9px 0 16px}.running .role-top>span:first-child{animation:pulse 1.5s ease-in-out infinite}@keyframes pulse{50%{opacity:.35}}
  .role-rail{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}.role-card{padding:16px;background:var(--panel);border:1px solid var(--line);border-radius:9px;position:relative}.role-card:not(:last-child):after{content:"→";position:absolute;right:-11px;top:50%;z-index:1;color:#77836a;background:#101217}.role-top{display:flex;align-items:center;gap:9px;margin-bottom:14px}.role-top>span:first-child{color:var(--accent);font-size:22px}.role-status{margin-left:auto;font-size:9px;text-transform:uppercase;letter-spacing:.7px;color:var(--muted)}.role-card strong{font-size:13px}.role-card p{font-size:12px;margin:6px 0;color:#b3bac8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.model-name{display:block;color:#7f8b9e;font-size:10px;overflow-wrap:anywhere}.running{border-color:#9acb6d;box-shadow:inset 0 2px #b3f17c}.running .role-status,.done .role-status{color:var(--accent)}.session-grid{display:grid;grid-template-columns:minmax(0,1fr) 235px;gap:18px}.conversation-panel{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;display:flex;flex-direction:column;position:relative;overflow:hidden}.conversation-tabs{display:flex;align-items:center;padding:0 20px;border-bottom:1px solid var(--line);justify-content:space-between;gap:12px}.conversation-tabs .subtabs{border:0}.conversation-tabs .subtabs button{display:flex;align-items:center;gap:7px;font-size:12px}.connection{font-size:10px;white-space:nowrap;color:var(--muted)}.offline{background:#626b7c}.feed,.request-feed,.result-feed{height:470px;overflow:auto;padding:22px;scrollbar-width:thin;scrollbar-color:#3b4351 transparent}.welcome{height:100%;min-height:320px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;max-width:440px;margin:auto}.welcome-symbol{font-size:42px;color:var(--accent);margin-bottom:20px}.welcome h2{font-size:21px;letter-spacing:-.5px;margin-bottom:12px}.welcome p{color:var(--muted);font-size:13px}.welcome small{color:#7c8699;margin-top:22px}.workflow-line{font-size:10px;color:#697389;display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:center}.workflow-line span{padding:6px 8px;border:1px solid var(--line);border-radius:5px;color:#b8c4ab}.message{padding:0 0 22px;margin-bottom:22px;border-bottom:1px solid #252b36}.message:last-child{border:0}.message-label{display:flex;align-items:center;gap:9px;font-size:11px;margin-bottom:11px}.message-label small{color:#8792a6;font-size:9px}.avatar{width:24px;height:24px;display:grid;place-items:center;background:#2a3224;color:var(--accent);border-radius:6px;font-size:10px}.user .avatar{background:#283247;color:#b4caff}.failure{color:#ffc1c9}.message-number{margin-left:auto;font-size:9px;color:#596276}.message>.text-output{padding-left:33px}.jump{position:absolute;bottom:200px;left:35%;font-size:11px;box-shadow:0 3px 15px #0008}.composer{border-top:1px solid var(--line);padding:14px 18px;background:#191e27;margin-top:auto}.composer-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:10px}.composer-top label{font-size:10px;color:var(--muted)}.composer-top select{font-size:11px;padding:5px 6px;margin-left:7px;border-color:transparent;background:#252c37}.composer textarea{width:100%;border:0;background:transparent;padding:4px 0;font-size:13px;resize:vertical;min-height:66px}.composer textarea:focus{outline:none}.composer-bottom{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:10px}.composer-bottom small{font-size:9px;color:#8993a5}.composer-bottom button{font-size:12px}.session-sidebar{min-width:0}.session-sidebar section{border-bottom:1px solid var(--line);padding:10px 0 18px;margin-bottom:13px}.sidebar-heading{display:flex;align-items:center;justify-content:space-between}.sidebar-heading h3{margin:0}.session-sidebar h3{font-size:11px}.session-sidebar dl{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0 0;font-size:10px}.session-sidebar dt{color:#939eb1}.session-sidebar dd{margin:0;text-align:right;overflow-wrap:anywhere;color:#d3dac5}.usage-total{font-size:26px;font-weight:600;letter-spacing:-.6px}.usage-total small{font-size:10px;font-weight:400;color:var(--muted);margin-left:8px}.quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.quick-actions button{font-size:9px;text-align:left;padding:7px 8px;background:transparent}.session-controls{display:grid;gap:8px;margin-top:14px}.session-controls button{font-size:11px}.runtime-logs{font-size:10px;color:var(--muted)}.runtime-logs pre{font-size:9px;max-height:180px;overflow:auto}.session-footer{display:flex;align-items:center;font-size:10px;color:#8c97aa;padding:15px 0}.session-footer>span:last-child{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.request-row{display:flex;gap:10px;align-items:center;width:100%;margin-bottom:8px;font-size:11px;text-align:left}.request-row>span:nth-child(2){flex:1;overflow-wrap:anywhere}.method{color:var(--accent);font-size:10px}.result-feed .text-output{margin:20px 0}.result-feed p{font-size:10px}.result-feed .pill{display:inline-block}
  @media(min-height:950px){.feed,.request-feed,.result-feed{height:calc(100vh - 510px)}}@media(max-width:1150px){.session-grid{grid-template-columns:minmax(0,1fr) 190px}.role-card{padding:12px}.role-top{gap:5px}.role-status{font-size:8px}.conversation-tabs{padding:0 12px}.connection{display:none}}@media(max-width:900px){.session-grid{grid-template-columns:1fr}.session-sidebar{display:grid;grid-template-columns:1fr 1fr;gap:20px}.role-rail{grid-template-columns:repeat(2,minmax(0,1fr))}.role-card:after{display:none}.feed,.request-feed,.result-feed{height:400px}}@media(max-width:750px){.session-sidebar{grid-template-columns:1fr}.composer-bottom small{max-width:170px}.conversation-tabs .subtabs{gap:14px}}
</style>
