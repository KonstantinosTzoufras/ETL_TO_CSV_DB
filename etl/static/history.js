"use strict";
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token, timer;
function notify(message,error=false){
  // No dedicated notice bar on this page (yet) - errors still need to be
  // visible, so they replace the run list rather than vanishing silently.
  if(error)$("runs").innerHTML=`<p class="reason">${esc(message)}</p>`;
}
async function api(path,body){
  const response=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-ETL-Token":token},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||"Request failed");
  return result;
}
function downloadLinks(run){
  const kind=run.spec.destination.kind;
  const reportedFiles=run.report.files||["rejected.csv"];
  const rejectedLink=(reportedFiles.includes("rejected.csv")?`<a href="/download/${run.id}/rejected.csv">Rejected rows</a>`:"")
                     +(reportedFiles.includes("rejected.xlsx")?`<a href="/download/${run.id}/rejected.xlsx">Rejected rows (Excel)</a>`:"");
  if(kind==="sqlserver"){
    const table=run.report.table;
    return (table?`<span>Written to ${esc(table.schema)}.${esc(table.name)} (${table.rows.toLocaleString()} rows)</span>`
                 :`<span>Written to a database table.</span>`)+rejectedLink;
  }
  const files=run.report.files||[`valid.${kind}`,"rejected.csv"];
  const valid=files.filter(f=>f!=="rejected.csv" && f!=="rejected.xlsx");
  if(!valid.length)return rejectedLink;
  const anchor=f=>{
    const group=f.includes("/")?f.split("/")[0]:null;
    return `<a href="/download/${run.id}/${esc(f)}">${group?esc(group):`Download ${esc(kind.toUpperCase())}`}</a>`;
  };
  if(valid.length<=4)return valid.map(anchor).join("")+rejectedLink;
  const splitBy=run.spec.destination.split_by;
  return `<details class="split-downloads"><summary>${valid.length} files${splitBy?` · split by ${esc(splitBy)}`:""}</summary>${valid.map(anchor).join("")}</details>${rejectedLink}`;
}
function orderedRunCard(run){
  const entries=run.report.steps||run.spec.steps.map((s,i)=>({...s,position:i+1,status:"pending"}));
  return `<div class="ordered-run"><h3>${esc(run.name)} <span class="run-status">${esc(run.status)}</span></h3><p>${esc(run.started)} — ${esc(run.finished||"In progress")}</p>${run.error?`<p class="reason">${esc(run.error)}</p>`:""}<ol>${entries.map(step=>{
   const ready=step.status==="completed",partial=["failed","interrupted"].includes(step.status),prefix=`/download/${encodeURIComponent(run.id)}/${encodeURIComponent(step.id)}/`;
   const target=step.resolved_target||step.execution_target||"server",
         targetLabel=step.resolved_target&&step.execution_target==="auto"?`${esc(target)} (auto)`:esc(target);
   return `<li><strong>${esc(step.name)}</strong> — ${esc(step.status)} <span class="chip">${targetLabel}</span><p>${step.processed??0} processed / ${step.valid??0} valid / ${step.invalid??0} rejected${partial?" (incomplete counts)":""}</p><p>${esc(step.started||"Not started")}${step.finished?" / "+esc(step.finished):""}</p>${step.error?`<p class="reason">${esc(step.error.message)}</p>`:""}${ready?`<a href="${prefix}${encodeURIComponent(step.output)}">Download ${esc(step.output)}</a> <a href="${prefix}rejected.csv">Rejected rows</a> <a href="${prefix}rejected.xlsx">Rejected rows (Excel)</a> <a href="${prefix}report.json">Step snapshot/report</a>`:""}${ready||partial?` <button data-diagnostics="${esc(run.id)}" data-step="${esc(step.id)}">${partial?"Partial diagnostics":"Rejection diagnostics"}</button>`:""}</li>`;
  }).join("")}</ol></div>`;
}
function runCard(run){
  return run.spec.kind==="ordered_query_export"?orderedRunCard(run):`<div class="run"><div><strong>${esc(run.name)}</strong><p>${esc(new Date(run.started).toLocaleString())} · ${run.report.processed??0} processed · ${run.report.valid??0} valid · ${run.report.invalid??0} rejected</p>${run.error?`<div class="reason">${esc(run.error)}</div>`:""}</div><div><span class="run-status ${esc(run.status)}">${esc(run.status)}</span>${run.status==="completed"?`<br>${downloadLinks(run)}<button data-diagnostics="${esc(run.id)}">View rejection diagnostics</button>`:""}</div></div>`;
}
async function refreshRuns(){
  const runs=await api("/api/runs");
  const filter=$("status-filter").value;
  const shown=filter?runs.filter(r=>filter==="running"?["queued","running"].includes(r.status):r.status===filter):runs;
  $("runs").innerHTML=shown.length?shown.map(runCard).join(""):'<p class="muted">No runs match this filter.</p>';
  clearTimeout(timer);
  if(runs.some(r=>["queued","running"].includes(r.status)))timer=setTimeout(()=>refreshRuns().catch(e=>notify(e.message,true)),1500);
}
$("refresh").onclick=()=>refreshRuns().catch(e=>notify(e.message,true));
$("status-filter").onchange=()=>refreshRuns().catch(e=>notify(e.message,true));

let diagnosticRun=null,diagnosticStep=null,diagnosticCursor=null,diagnosticGeneration=0;
async function loadRejections(runId,cursor=null,stepId=null){
  const generation=++diagnosticGeneration;
  const page=await api("/api/diagnostics/rejections",{run_id:runId,step_id:stepId,cursor,limit:20});
  if(generation!==diagnosticGeneration)return;
  diagnosticRun=runId;diagnosticStep=stepId;diagnosticCursor=page.next_cursor;
  $("run-diagnostics-name").textContent=`${page.name} · Run ${page.run_id} · v${page.version}`;
  $("run-diagnostics-rows").innerHTML=(page.partial?'<p class="hint">Partial diagnostics from an incomplete step. Counts and records may be incomplete.</p>':"")+Diagnostics.rows(page.rows);
  $("run-diagnostics-next").hidden=!diagnosticCursor;
  $("run-diagnostics").hidden=false;
  $("run-diagnostics").scrollIntoView({behavior:"smooth",block:"start"});
}
$("runs").addEventListener("click",event=>{
  const button=event.target.closest("[data-diagnostics]");
  if(button)loadRejections(button.dataset.diagnostics,null,button.dataset.step||null).catch(e=>notify(e.message,true));
});
$("run-diagnostics-next").onclick=()=>loadRejections(diagnosticRun,diagnosticCursor,diagnosticStep).catch(e=>notify(e.message,true));
$("run-diagnostics-close").onclick=()=>{diagnosticGeneration++;$("run-diagnostics").hidden=true;};

(async()=>{
  try{
    const bootstrap=await api("/api/bootstrap");
    token=bootstrap.token;
    await refreshRuns();
  }catch(error){notify(error.message,true);}
})();
