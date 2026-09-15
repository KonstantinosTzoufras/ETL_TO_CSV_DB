"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token, definition, pipelineId = null, saved = [], dirty = false, busy = false, timer;
const blank = () => ({version:1,name:"Untitled pipeline",source:{kind:"csv",path:"",delimiter:";"},columns:[],destination:{kind:"csv",delimiter:";"}});
function notify(message, error = false) { $("notice").textContent=message; $("notice").className=error?"error":""; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-ETL-Token":token},body:JSON.stringify(body)});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
async function action(fn) {
  if(busy) return;
  busy=true;
  const buttons=[...document.querySelectorAll("button")];
  buttons.forEach(b=>b.disabled=true);
  try { await fn(); } catch(error) { notify(error.message,true); }
  finally { busy=false; buttons.forEach(b=>b.disabled=false); }
}
function sourceFields() {
  const sql=$("source-kind").value==="sqlserver";
  document.querySelectorAll(".csv").forEach(el=>el.hidden=sql);
  document.querySelectorAll(".sql").forEach(el=>el.hidden=!sql);
  $("source-note").textContent=sql?"Use a read-only SQL Server connection configured on this computer. See README for setup.":"Use a CSV inside the project folder. The demo is ready to preview.";
}
function source() {
  return $("source-kind").value==="csv" ? {kind:"csv",path:$("source-path").value,delimiter:$("source-delimiter").value,encoding:$("encoding").value} : {kind:"sqlserver",connection_env:$("connection").value,schema:$("schema").value,table:$("table").value};
}
function read() {
  const columns=[...$("columns").children].map((tr,i)=>{
    const field=key=>tr.querySelector(`[data-field="${key}"]`);
    const column={...definition.columns[i],name:field("name").value,type:field("type").value,required:field("required").checked,transforms:field("transforms").value.split(",").map(s=>s.trim()).filter(Boolean)};
    delete column.source; delete column.literal; delete column.max_length;
    const mode=field("mode").value, value=field("value").value;
    // Keep typed literals from imported definitions until the user changes them.
    const old=definition.columns[i];
    column[mode]=mode==="literal" && "literal" in old && String(old.literal??"")===value ? old.literal : value;
    if(field("max_length").value!=="") column.max_length=Number(field("max_length").value);
    return column;
  });
  definition={version:definition.version,name:$("name").value,source:source(),columns,destination:{...definition.destination,kind:$("format").value,delimiter:$("output-delimiter").value}};
  return structuredClone(definition);
}
function renderColumns() {
  $("columns").innerHTML=definition.columns.map((column,i)=>{
    const literal="literal" in column;
    const input=(key,value,extra="")=>`<input aria-label="Field ${i+1} ${key}" data-field="${key}" value="${esc(value)}" ${extra}>`;
    return `<tr><td>${input("name",column.name)}</td><td><select aria-label="Field ${i+1} value from" data-field="mode"><option value="source" ${literal?"":"selected"}>Column</option><option value="literal" ${literal?"selected":""}>Constant</option></select></td><td>${input("value",literal?column.literal:column.source,literal?"":"list=available-columns")}${column.lookup?'<span class="chip">Lookup enabled</span>':""}</td><td><select aria-label="Field ${i+1} type" data-field="type">${["string","int","decimal","float","bool","date","datetime"].map(t=>`<option ${(column.type||"string")===t?"selected":""}>${t}</option>`).join("")}</select></td><td>${input("transforms",(column.transforms||[]).join(", "))}</td><td><input aria-label="Field ${i+1} required" data-field="required" type="checkbox" ${column.required?"checked":""}></td><td>${input("max_length",column.max_length??"",'type="number" min="1" step="1"')}</td><td><button class="remove" data-remove="${i}" aria-label="Remove field ${i+1}">×</button></td></tr>`;
  }).join("");
}
function render() {
  function setDelimiter(id,value) {
    const select=$(id);
    if(![...select.options].some(o=>o.value===value)) select.add(new Option(value,value));
    select.value=value;
  }
  $("name").value=definition.name;
  const s=definition.source;
  $("source-kind").value=s.kind; $("source-path").value=s.path||""; setDelimiter("source-delimiter",s.delimiter||";");
  $("encoding").value=s.encoding||"utf-8-sig"; $("connection").value=s.connection_env||"ETL_SQL_MAIN"; $("schema").value=s.schema||"dbo"; $("table").value=s.table||"";
  $("format").value=definition.destination.kind; setDelimiter("output-delimiter",definition.destination.delimiter||";");
  sourceFields(); renderColumns(); $("json").value=JSON.stringify(definition,null,2);
  $("results").hidden=true; $("source-columns").replaceChildren(); $("available-columns").replaceChildren();
}
function openDefinition(spec,id=null) {
  definition=structuredClone(spec); pipelineId=id; dirty=false; render(); renderSaved();
}
function renderSaved() {
  $("pipelines").innerHTML=saved.length?saved.map(p=>`<button class="pipeline ${p.id===pipelineId?"active":""}" data-pipeline="${esc(p.id)}">${esc(p.name)}<small>${esc(p.spec.source.kind.toUpperCase())} → ${esc(p.spec.destination.kind.toUpperCase())}</small></button>`).join(""):'<p class="muted">Save your first pipeline to reuse it here.</p>';
}
async function refreshSaved() { saved=await api("/api/pipelines");renderSaved(); }
function preview(report) {
  $("results").hidden=false;
  $("counts").innerHTML=`<div class="count"><b>${report.processed}</b>Sampled records</div><div class="count good"><b>${report.valid}</b>Valid</div><div class="count bad"><b>${report.invalid}</b>Rejected</div>`;
  const names=definition.columns.map(c=>c.name);
  $("preview-table").innerHTML=`<table><thead><tr><th>STATUS</th>${names.map(n=>`<th>${esc(n)}</th>`).join("")}<th>VALIDATION</th></tr></thead><tbody>${report.sample.map(row=>`<tr><td class="${Object.keys(row.errors).length?"invalid":"valid"}">${Object.keys(row.errors).length?"Rejected":"Valid"}</td>${names.map(n=>`<td>${esc(displayValue((row.values || row.converted_values)[n]))}</td>`).join("")}<td class="reason">${Object.entries(row.errors).map(([k,v])=>`${esc(k)}: ${esc(v.map(error=>typeof error === "string" ? error : error.message).join(", "))}`).join("<br>")}</td></tr>`).join("")}</tbody></table>`;
  $("results").scrollIntoView({behavior:"smooth",block:"start"});
}
async function refreshRuns() {
  const runs=await api("/api/runs");
  $("runs").innerHTML=runs.length?runs.map(run=>`<div class="run"><div><strong>${esc(run.name)}</strong><p>${esc(new Date(run.started).toLocaleString())} · ${run.report.processed??0} processed · ${run.report.valid??0} valid · ${run.report.invalid??0} rejected</p>${run.error?`<div class="reason">${esc(run.error)}</div>`:""}</div><div><span class="run-status ${esc(run.status)}">${esc(run.status)}</span>${run.status==="completed"?`<br><a href="/download/${run.id}/valid.${run.spec.destination.kind}">Download ${run.spec.destination.kind.toUpperCase()}</a><a href="/download/${run.id}/rejected.csv">Rejected rows</a>`:""}</div></div>`).join(""):'<p class="muted">No runs yet. Preview your pipeline, then run an export.</p>';
  clearTimeout(timer);
  if(runs.some(r=>["queued","running"].includes(r.status))) timer=setTimeout(()=>refreshRuns().catch(e=>notify(e.message,true)),1500);
}
document.addEventListener("input",event=>{if(event.target.closest("main") && event.target.id!=="json") dirty=true;});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
$("source-kind").onchange=()=>{sourceFields();dirty=true;};
$("new").onclick=()=>{if(!dirty||confirm("Discard unsaved changes and create a new pipeline?"))openDefinition(blank());};
$("pipelines").onclick=event=>{const button=event.target.closest("[data-pipeline]");if(button&&(!dirty||confirm("Discard unsaved changes?")))openDefinition(saved.find(p=>p.id===button.dataset.pipeline).spec,button.dataset.pipeline);};
$("columns").onclick=event=>{const button=event.target.closest("[data-remove]");if(button){read();definition.columns.splice(Number(button.dataset.remove),1);renderColumns();dirty=true;}};
$("add").onclick=()=>{read();definition.columns.push({name:"",source:"",type:"string"});renderColumns();dirty=true;};
$("inspect").onclick=()=>action(async()=>{
  const result=await api("/api/columns",{source:source()});
  $("available-columns").innerHTML=result.columns.map(c=>`<option value="${esc(c)}">`).join("");
  $("source-columns").innerHTML=result.columns.map(c=>`<span class="chip">${esc(c)}</span>`).join("");
  read();if(!definition.columns.length){definition.columns=result.columns.map(name=>({name,source:name,type:"string"}));renderColumns();dirty=true;}
  notify(`${result.columns.length} source columns available. Existing mappings have been preserved.`);
});
$("save").onclick=()=>action(async()=>{const result=await api("/api/pipelines",{id:pipelineId,spec:read()});pipelineId=result.id;dirty=false;await refreshSaved();notify("Pipeline saved. You can load it from the sidebar.");});
$("preview").onclick=()=>action(async()=>{const report=await api("/api/preview",{spec:read()});preview(report);notify("Preview complete. No export files were created.");});
$("run").onclick=()=>action(async()=>{await api("/api/runs",{spec:read()});notify("Export started. Follow its progress in Run history.");await refreshRuns();});
$("refresh").onclick=()=>action(refreshRuns);
$("advanced").ontoggle=()=>{if($("advanced").open)$("json").value=JSON.stringify(read(),null,2);};
$("json").oninput=()=>{dirty=true;};
$("apply").onclick=()=>action(async()=>{const next=JSON.parse($("json").value);await api("/api/validate",{spec:next});definition=next;render();dirty=true;notify("Definition applied. Save the pipeline to keep these changes.");});
(async()=>{try{const bootstrap=await api("/api/bootstrap");token=bootstrap.token;openDefinition(bootstrap.example||blank());await refreshSaved();await refreshRuns();}catch(error){notify(error.message,true);}})();

function displayValue(value) { return value && typeof value === "object" && "$type" in value ? value.value : value; }
