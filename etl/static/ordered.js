"use strict";
let orderedDraft=null, orderedIndex=0, orderedConnectionsLoaded=false;
const orderedStep=number=>({id:`step-${number}`,name:`Step ${number}`,processing_version:2,query:{format_version:1,dialect:"tsql",sql:"",parameters:[],timeout_seconds:60},columns:[],destination:{kind:"csv",delimiter:";"},execution_target:"server"});
function clearOrdered(){orderedDraft=null;$("ordered-editor").hidden=true;}
function renderOrderedMode(){
 const active=!!orderedDraft;
 $("ordered-editor").hidden=!active;
 $("source-kind").disabled=active;$("query-connection").disabled=active;
 $("query-connections").hidden=active;
 $("name").parentElement.firstChild.textContent=active?"Step name":"Pipeline name";
}
function openOrdered(spec,id=null){
 clearTemplateDraft();orderedDraft=structuredClone(spec);orderedIndex=0;pipelineId=id;dirty=false;
 $("ordered-name").value=spec.name;$("ordered-policy").value=spec.failure_policy||"stop";$("ordered-parallel").value=spec.max_parallel_steps||1;
 if(![...$("ordered-connection").options].some(o=>o.value===spec.connection_env))$("ordered-connection").add(new Option(spec.connection_env||"Select an approved connection",spec.connection_env));
 $("ordered-connection").value=spec.connection_env;
 showOrderedStep();renderSaved();
 // Opening this editor means the shared connection list is needed right
 // away; loading it then, once, saves the separate click every time. The
 // button stays for a manual re-check after a restart.
 if(!orderedConnectionsLoaded) action(loadOrderedConnections);
}
function showOrderedStep(){
 const step=orderedDraft.steps[orderedIndex];
 definition={version:step.processing_version,name:step.name,source:{kind:"sqlserver_query",connection_env:orderedDraft.connection_env,query:structuredClone(step.query)},columns:structuredClone(step.columns),destination:structuredClone(step.destination)};
 $("ordered-step-id").value=step.id;$("ordered-version").value=String(step.processing_version);
 const target=step.execution_target||"server";
 if(![...$("ordered-step-target").options].some(o=>o.value===target))$("ordered-step-target").add(new Option(target+" (not currently configured)",target));
 $("ordered-step-target").value=target;
 render();renderOrderedSteps();
}
function captureOrderedStep(){
 if(!orderedDraft)return;
 const single=read();
 if(single.source.kind!=="sqlserver_query"||single.source.connection_env!==orderedDraft.connection_env)throw new Error("Every step must use the shared query connection.");
 orderedDraft.steps[orderedIndex]={id:$("ordered-step-id").value,name:single.name,processing_version:single.version,query:structuredClone(single.source.query),columns:single.columns,destination:single.destination,execution_target:$("ordered-step-target").value};
 orderedDraft.name=$("ordered-name").value;orderedDraft.failure_policy=$("ordered-policy").value;orderedDraft.max_parallel_steps=Number($("ordered-parallel").value)||1;
 renderOrderedSteps();
}
function workingDefinition(){if(!orderedDraft)return read();captureOrderedStep();return structuredClone(orderedDraft);}
function renderOrderedSteps(){
 $("ordered-steps").innerHTML=orderedDraft.steps.map((s,i)=>`<button data-ordered-index="${i}" ${i===orderedIndex?'aria-current="step"':""}>${i+1}. ${esc(s.name)} (${s.columns.length} mappings) → ${esc(s.id)}.${esc(s.destination.kind)}</button>`).join("");
 const s=orderedDraft.steps[orderedIndex];$("ordered-output").textContent=`Selected step ${orderedIndex+1}. Output: ${String(orderedIndex+1).padStart(3,"0")}-${s.id}/${s.id}.${s.destination.kind}. Rejected rows do not fail the step.`;
}
$("new-ordered").onclick=()=>{if(!dirty||confirm("Discard unsaved changes?"))openOrdered({kind:"ordered_query_export",format_version:1,name:"Ordered query exports",connection_env:"",failure_policy:"stop",max_parallel_steps:1,steps:[orderedStep(1)]});};
$("ordered-steps").onclick=event=>{const button=event.target.closest("[data-ordered-index]");if(button)action(async()=>{captureOrderedStep();orderedIndex=Number(button.dataset.orderedIndex);showOrderedStep();});};
$("ordered-add").onclick=()=>action(async()=>{captureOrderedStep();if(orderedDraft.steps.length>=20)throw new Error("Maximum 20 steps");let n=orderedDraft.steps.length+1;while(orderedDraft.steps.some(s=>s.id===`step-${n}`))n++;orderedDraft.steps.push(orderedStep(n));orderedIndex=orderedDraft.steps.length-1;showOrderedStep();dirty=true;});
function moveOrdered(delta){return action(async()=>{captureOrderedStep();const next=orderedIndex+delta;if(next<0||next>=orderedDraft.steps.length)return;[orderedDraft.steps[orderedIndex],orderedDraft.steps[next]]=[orderedDraft.steps[next],orderedDraft.steps[orderedIndex]];orderedIndex=next;showOrderedStep();dirty=true;});}
$("ordered-up").onclick=()=>moveOrdered(-1);$("ordered-down").onclick=()=>moveOrdered(1);
$("ordered-remove").onclick=()=>action(async()=>{captureOrderedStep();if(orderedDraft.steps.length===1)throw new Error("Keep at least one step");orderedDraft.steps.splice(orderedIndex,1);orderedIndex=Math.min(orderedIndex,orderedDraft.steps.length-1);showOrderedStep();dirty=true;});
async function loadOrderedConnections(){
 const data=await api("/api/query/connections",{});
 orderedConnectionsLoaded=true;
 const selected=orderedDraft.connection_env;
 $("ordered-connection").replaceChildren(new Option("Select an approved connection",""));
 data.connections.forEach(name=>$("ordered-connection").add(new Option(name,name)));
 if(selected && !data.connections.includes(selected))$("ordered-connection").add(new Option(selected+" (not currently approved)",selected));
 $("ordered-connection").value=selected;
 return data;
}
$("ordered-connections").onclick=()=>action(async()=>{
 const data=await loadOrderedConnections();
 notify(data.connections.length?"Shared connection references loaded; select explicitly.":"No read-only query connections are approved.");
});
// Native listeners respond to user selections, not jQuery's change.select2
// display refresh (which also invokes an onchange property handler).
$("ordered-connection").addEventListener("change",()=>{
 if(!orderedDraft)return;
 return action(async()=>{captureOrderedStep();orderedDraft.connection_env=$("ordered-connection").value;showOrderedStep();dirty=true;});
});
$("ordered-version").onchange=()=>action(async()=>{captureOrderedStep();orderedDraft.steps[orderedIndex].processing_version=Number($("ordered-version").value);showOrderedStep();dirty=true;});
function orderedRunCard(run){
 const entries=run.report.steps||run.spec.steps.map((s,i)=>({...s,position:i+1,status:"pending"}));
 return `<div class="ordered-run"><h3>${esc(run.name)} <span class="run-status">${esc(run.status)}</span></h3><p>${esc(run.started)} — ${esc(run.finished||"In progress")}</p>${run.error?`<p class="reason">${esc(run.error)}</p>`:""}<ol>${entries.map(step=>{
  const ready=step.status==="completed",partial=["failed","interrupted"].includes(step.status),prefix=`/download/${encodeURIComponent(run.id)}/${encodeURIComponent(step.id)}/`;
  const target=step.resolved_target||step.execution_target||"server",
        targetLabel=step.resolved_target&&step.execution_target==="auto"?`${esc(target)} (auto)`:esc(target);
  return `<li><strong>${esc(step.name)}</strong> — ${esc(step.status)} <span class="chip">${targetLabel}</span><p>${step.processed??0} processed / ${step.valid??0} valid / ${step.invalid??0} rejected${partial?" (incomplete counts)":""}</p><p>${esc(step.started||"Not started")}${step.finished?" / "+esc(step.finished):""}</p>${step.error?`<p class="reason">${esc(step.error.message)}</p>`:""}${ready?`<a href="${prefix}${encodeURIComponent(step.output)}">Download ${esc(step.output)}</a> <a href="${prefix}rejected.csv">Rejected rows</a> <a href="${prefix}rejected.xlsx">Rejected rows (Excel)</a> <a href="${prefix}report.json">Step snapshot/report</a>`:""}${ready||partial?` <button data-diagnostics="${esc(run.id)}" data-step="${esc(step.id)}">${partial?"Partial diagnostics":"Rejection diagnostics"}</button>`:""}</li>`;
 }).join("")}</ol></div>`;
}
