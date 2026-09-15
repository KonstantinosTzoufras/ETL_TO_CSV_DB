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
  const sql=$("source-kind").value==="sqlserver", query=$("source-kind").value==="sqlserver_query";
  document.querySelectorAll(".csv").forEach(el=>el.hidden=sql||query);
  $("query-editor").hidden=!query; $("discover-browse").hidden=query;
  document.querySelectorAll(".sql").forEach(el=>el.hidden=!sql);
  $("source-note").textContent=query?"Query execution requires an administrator-approved read-only connection.":sql?"Use a read-only SQL Server connection configured on this computer. See README for setup.":"Use a CSV inside the project folder. The demo is ready to preview.";
}
function source() {
  if($("source-kind").value==="sqlserver_query")return querySource();
  return $("source-kind").value==="csv" ? {kind:"csv",path:$("source-path").value,delimiter:$("source-delimiter").value,encoding:$("encoding").value} : {kind:"sqlserver",connection_env:$("connection").value,schema:$("schema").value,table:$("table").value};
}
function read(options={}) {
  if(typeof templateIsPending==="function" && templateIsPending() && !options.allowTemplateDraft) throw new Error("Resolve template bindings and generate the pipeline first.");
  const columns=[...$("columns").children].map((tr,i)=>{
    const field=key=>tr.querySelector(`[data-field="${key}"]`);
    const column={...definition.columns[i],name:field("name").value,type:field("type").value,required:field("required").checked,transforms:field("transforms").value.split(",").map(s=>s.trim()).filter(Boolean)};
    delete column.source; delete column.literal; delete column.max_length;
    const mode=field("mode").value, value=field("value").value;
    // Keep typed literals from imported definitions until the user changes them.
    const old=definition.columns[i];
    column[mode]=mode==="literal" && "literal" in old && (String(old.literal??"")===value || !field("value").dataset.edited) ? old.literal : value;
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
  resetDiscovery();
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
  if(typeof renderQuery==="function")renderQuery(s);
  sourceFields(); renderColumns(); $("json").value=JSON.stringify(definition,null,2);
  if(typeof renderOrderedMode==="function")renderOrderedMode();
  $("results").hidden=true; $("source-columns").replaceChildren(); $("available-columns").replaceChildren();
}
function openDefinition(spec,id=null) {
  if(spec.kind==="ordered_query_export"){openOrdered(spec,id);return;}
  if(typeof clearOrdered==="function")clearOrdered();
  if(typeof clearTemplateDraft==="function")clearTemplateDraft();
  definition=structuredClone(spec); pipelineId=id; dirty=false; render(); renderSaved();
}
function renderSaved() {
  $("pipelines").innerHTML=saved.length?saved.map(p=>`<button class="pipeline ${p.id===pipelineId?"active":""}" data-pipeline="${esc(p.id)}">${esc(p.name)}<small>${esc(p.spec.kind==="ordered_query_export"?"ORDERED QUERIES":p.spec.source.kind.toUpperCase())} → ${esc(p.spec.kind==="ordered_query_export"?`${p.spec.steps.length} FILES`:p.spec.destination.kind.toUpperCase())}</small></button>`).join(""):'<p class="muted">Save your first pipeline to reuse it here.</p>';
}
async function refreshSaved() { saved=await api("/api/pipelines");renderSaved(); }
function preview(report) {
  $("results").hidden=false;
  $("counts").innerHTML=`<div class="count"><b>${report.processed}</b>Sampled records</div><div class="count good"><b>${report.valid}</b>Valid</div><div class="count bad"><b>${report.invalid}</b>Rejected</div>`;
  if(report.diagnostics) {
    $("preview-table").innerHTML='<p class="hint">Processing diagnostics from this preview. Expand a record and field to inspect stored stage values. Later edits do not update these results.</p>'+Diagnostics.rows(report.diagnostics);
    $("results").scrollIntoView({behavior:"smooth",block:"start"});
    return;
  }
  const names=definition.columns.map(c=>c.name);
  $("preview-table").innerHTML=`<table><thead><tr><th>STATUS</th>${names.map(n=>`<th>${esc(n)}</th>`).join("")}<th>VALIDATION</th></tr></thead><tbody>${report.sample.map(row=>`<tr><td class="${Object.keys(row.errors).length?"invalid":"valid"}">${Object.keys(row.errors).length?"Rejected":"Valid"}</td>${names.map(n=>`<td>${esc(displayValue((row.values || row.converted_values)[n]))}</td>`).join("")}<td class="reason">${Object.entries(row.errors).map(([k,v])=>`${esc(k)}: ${esc(v.map(error=>typeof error === "string" ? error : error.message).join(", "))}`).join("<br>")}</td></tr>`).join("")}</tbody></table>`;
  $("results").scrollIntoView({behavior:"smooth",block:"start"});
}
async function refreshRuns() {
  const runs=await api("/api/runs");
  $("runs").innerHTML=runs.length?runs.map(run=>run.spec.kind==="ordered_query_export"?orderedRunCard(run):`<div class="run"><div><strong>${esc(run.name)}</strong><p>${esc(new Date(run.started).toLocaleString())} · ${run.report.processed??0} processed · ${run.report.valid??0} valid · ${run.report.invalid??0} rejected</p>${run.error?`<div class="reason">${esc(run.error)}</div>`:""}</div><div><span class="run-status ${esc(run.status)}">${esc(run.status)}</span>${run.status==="completed"?`<br><a href="/download/${run.id}/valid.${run.spec.destination.kind}">Download ${run.spec.destination.kind.toUpperCase()}</a><a href="/download/${run.id}/rejected.csv">Rejected rows</a><button data-diagnostics="${esc(run.id)}">View rejection diagnostics</button>`:""}</div></div>`).join(""):'<p class="muted">No runs yet. Preview your pipeline, then run an export.</p>';
  clearTimeout(timer);
  if(runs.some(r=>["queued","running"].includes(r.status))) timer=setTimeout(()=>refreshRuns().catch(e=>notify(e.message,true)),1500);
}
document.addEventListener("input",event=>{if(event.target.closest("main") && event.target.id!=="json") dirty=true;if(event.target.closest("#columns") && event.target.dataset.field==="value")event.target.dataset.edited="true";});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
$("source-kind").onchange=()=>{sourceFields();dirty=true;};
$("new").onclick=()=>{if(!dirty||confirm("Discard unsaved changes and create a new pipeline?"))openDefinition(blank());};
$("pipelines").onclick=event=>{const button=event.target.closest("[data-pipeline]");if(button&&(!dirty||confirm("Discard unsaved changes?")))openDefinition(saved.find(p=>p.id===button.dataset.pipeline).spec,button.dataset.pipeline);};
$("columns").onclick=event=>{const button=event.target.closest("[data-remove]");if(button){read();definition.columns.splice(Number(button.dataset.remove),1);renderColumns();dirty=true;}};
$("add").onclick=()=>action(async()=>{read();definition.columns.push({name:"",source:"",type:"string"});renderColumns();dirty=true;});
$("inspect").onclick=()=>action(async()=>{
  if(typeof templateIsPending==="function" && templateIsPending())throw new Error("Use Read binding columns in the template binding panel; mappings stay explicit.");
  const result=await api("/api/columns",{source:source()});
  $("available-columns").innerHTML=result.columns.map(c=>`<option value="${esc(c)}">`).join("");
  $("source-columns").innerHTML=result.columns.map(c=>`<span class="chip">${esc(c)}</span>`).join("");
  read();if(!definition.columns.length && definition.source.kind!=="sqlserver_query"){definition.columns=result.columns.map(name=>({name,source:name,type:"string"}));renderColumns();dirty=true;}
  notify(`${result.columns.length} source columns available. Existing mappings have been preserved.`);
});
$("save").onclick=()=>action(async()=>{const result=await api("/api/pipelines",{id:pipelineId,spec:workingDefinition()});pipelineId=result.id;dirty=false;await refreshSaved();notify("Pipeline saved. You can load it from the sidebar.");});
$("preview").onclick=()=>action(async()=>{const report=typeof orderedDraft!=="undefined" && orderedDraft?await api("/api/ordered/preview",{spec:workingDefinition(),step_id:orderedDraft.steps[orderedIndex].id,diagnostics:true}):await api("/api/preview",{spec:read(),diagnostics:true});preview(report);notify("Preview complete. No export files were created.");});
$("run").onclick=()=>action(async()=>{await api("/api/runs",{spec:workingDefinition()});notify("Export started. Follow its progress in Run history.");await refreshRuns();});
$("refresh").onclick=()=>action(refreshRuns);
$("advanced").ontoggle=()=>{if($("advanced").open){try{$("json").value=JSON.stringify(workingDefinition(),null,2);}catch(error){$("advanced").open=false;notify(error.message,true);}}};
$("json").oninput=()=>{dirty=true;};
$("apply").onclick=()=>action(async()=>{read();const next=JSON.parse($("json").value);await api("/api/validate",{spec:next});openDefinition(next,pipelineId);dirty=true;notify("Definition applied. Save the pipeline to keep these changes.");});
(async()=>{try{const bootstrap=await api("/api/bootstrap");token=bootstrap.token;openDefinition(bootstrap.example||blank());await refreshSaved();await refreshRuns();}catch(error){notify(error.message,true);}})();

function displayValue(value) { return value && typeof value === "object" && "$type" in value ? value.value : value; }

let diagnosticRun=null, diagnosticStep=null, diagnosticCursor=null, diagnosticGeneration=0;
async function loadRejections(runId, cursor=null, stepId=null) {
  const generation=++diagnosticGeneration;
  const page=await api("/api/diagnostics/rejections",{run_id:runId,step_id:stepId,cursor,limit:20});
  if(generation!==diagnosticGeneration)return;
  diagnosticRun=runId; diagnosticStep=stepId; diagnosticCursor=page.next_cursor;
  $("run-diagnostics-name").textContent=`${page.name} · Run ${page.run_id} · v${page.version}`;
  $("run-diagnostics-rows").innerHTML=(page.partial?'<p class="hint">Partial diagnostics from an incomplete step. Counts and records may be incomplete.</p>':"")+Diagnostics.rows(page.rows);
  $("run-diagnostics-next").hidden=!diagnosticCursor;
  $("run-diagnostics").hidden=false;
  $("run-diagnostics").scrollIntoView({behavior:"smooth",block:"start"});
}
$("runs").addEventListener("click",event=>{
  const button=event.target.closest("[data-diagnostics]");
  if(button)action(()=>loadRejections(button.dataset.diagnostics,null,button.dataset.step||null));
});
$("run-diagnostics-next").onclick=()=>action(()=>loadRejections(diagnosticRun,diagnosticCursor,diagnosticStep));
$("run-diagnostics-close").onclick=()=>{diagnosticGeneration++;$("run-diagnostics").hidden=true;};

// Discovery edits only the source definition. Processing remains in /api/preview.
let discoveryGeneration=0, discoveryNamespace=[], discoveryFolders=[], discoveryDatasets=[], discoverySource=null;
let foldersCursor=null, datasetsCursor=null;
function resetDiscovery() {
  discoveryGeneration++;
  discoverySource=null;
  for(const id of ["discovery-browser","discovery-selection","discovery-sample"]) $(id).hidden=true;
}
const discoveryInputs=new Set(["source-kind","source-path","source-delimiter","encoding","connection","schema","table","query-connection","query-sql","query-parameters","query-timeout"]);
document.addEventListener("input",event=>{if(discoveryInputs.has(event.target.id))resetDiscovery();});
document.addEventListener("change",event=>{if(discoveryInputs.has(event.target.id))resetDiscovery();});
function discoveryContext() { return {connector:$("source-kind").value,connection_env:$("source-kind").value==="sqlserver_query"?$("query-connection").value:$("connection").value}; }
async function discoveryRequest(operation, values={}) { return api(`/api/discovery/${operation}`,{...discoveryContext(),...values}); }
async function browseDiscovery(namespace=[], more=null) {
  const generation=++discoveryGeneration;
  discoverySource=null; $("discovery-selection").hidden=true; $("discovery-sample").hidden=true;
  const capabilities=await discoveryRequest("capabilities");
  if(generation!==discoveryGeneration)return;
  const sql=$("source-kind").value==="sqlserver";
  const folders=(!more||more==="folders") && capabilities.operations.includes("list_namespaces") && (!sql||!namespace.length)
    ? await discoveryRequest("namespaces",{namespace,cursor:more?foldersCursor:null}) : null;
  if(generation!==discoveryGeneration)return;
  const datasets=(!more||more==="datasets") && capabilities.operations.includes("list_datasets") && (!sql||namespace.length)
    ? await discoveryRequest("datasets",{namespace,cursor:more?datasetsCursor:null}) : null;
  if(generation!==discoveryGeneration)return;
  discoveryNamespace=namespace;
  if(!more||folders){discoveryFolders=folders?.items||[];foldersCursor=folders?.next_cursor||null;}
  if(!more||datasets){discoveryDatasets=datasets?.items||[];datasetsCursor=datasets?.next_cursor||null;}
  $("discovery-browser").hidden=false;
  $("discovery-location").textContent=namespace.join(" / ")||(sql?"Configured database schemas":"Workspace folders and CSV files");
  $("discover-up").hidden=!namespace.length;
  $("discover-folders-next").hidden=!foldersCursor; $("discover-datasets-next").hidden=!datasetsCursor;
  $("discovery-folders").innerHTML=discoveryFolders.map((n,i)=>`<button data-folder="${i}">${esc(n[n.length-1])} /</button>`).join("");
  $("discovery-datasets").innerHTML=discoveryDatasets.map((d,i)=>`<button data-dataset="${i}">${esc(d.name)} (${esc(d.kind)})</button>`).join("")||'<p class="hint">No datasets on this page.</p>';
}
async function selectDiscovery(locator) {
  const generation=++discoveryGeneration;
  discoverySource=null; $("discovery-selection").hidden=true; $("discovery-sample").hidden=true;
  const dataset=await discoveryRequest("resolve",{locator});
  if(generation!==discoveryGeneration)return;
  const options=dataset.connector==="csv"?{delimiter:$("source-delimiter").value,encoding:$("encoding").value}:{};
  const configured=await discoveryRequest("configure",{dataset_key:dataset.key,options});
  if(generation!==discoveryGeneration)return;
  const columns=await discoveryRequest("columns",{source:configured});
  if(generation!==discoveryGeneration)return;
  discoverySource=configured;
  $("discovery-name").textContent=[...dataset.namespace,dataset.name].join(" / ");
  const known=value=>value===null?"Unknown":String(value);
  $("discovery-metadata").innerHTML=`<table><thead><tr><th>Position</th><th>Name</th><th>Read type</th><th>Declared type</th><th>Nullable</th><th>Precision</th><th>Scale</th><th>Max bytes (−1 = max)</th></tr></thead><tbody>${columns.map((m,i)=>`<tr>${[i+1,m.column.name,m.column.native_type,m.declared_type,m.column.nullable,m.column.precision,m.column.scale,m.max_length_bytes].map(v=>`<td>${esc(known(v))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  $("discovery-selection").hidden=false;
  notify("Dataset inspected. Use it explicitly when ready; mappings are unchanged.");
}
function sampleValue(value) {
  if(value===null)return '<em>NULL</em>';
  if(value==="")return '<em>Empty string (length 0)</em>';
  const tag=value && typeof value==="object" && "$type" in value ? `${esc(value.$type)}: ` : "";
  return `${tag}<pre>${esc(displayValue(value))}</pre>`;
}
$("discover-browse").onclick=()=>action(()=>browseDiscovery());
$("discover-up").onclick=()=>action(()=>browseDiscovery(discoveryNamespace.slice(0,-1)));
$("discover-folders-next").onclick=()=>action(()=>browseDiscovery(discoveryNamespace,"folders"));
$("discover-datasets-next").onclick=()=>action(()=>browseDiscovery(discoveryNamespace,"datasets"));
$("discovery-folders").onclick=event=>{const button=event.target.closest("[data-folder]");if(button)action(()=>browseDiscovery(discoveryFolders[Number(button.dataset.folder)]));};
$("discovery-datasets").onclick=event=>{const button=event.target.closest("[data-dataset]");if(button)action(()=>selectDiscovery(discoveryDatasets[Number(button.dataset.dataset)].key));};
$("discover-path").onclick=()=>action(()=>selectDiscovery($("source-path").value));
$("discover-sample").onclick=()=>action(async()=>{
  if(!discoverySource)return;
  const generation=discoveryGeneration;
  $("discovery-sample").hidden=true;
  const sample=await discoveryRequest("sample",{source:discoverySource,limit:Number($("discovery-limit").value)});
  if(generation!==discoveryGeneration)return;
  $("discovery-sample-status").textContent=`${sample.rows.length} records read. ${sample.stop_reason==="row_limit"?"Input limit reached; more records may or may not exist.":"End of source reached."}`;
  $("discovery-sample-table").innerHTML=`<table><thead><tr><th>Source position</th>${sample.columns.map(c=>`<th>${esc(c.name)}</th>`).join("")}</tr></thead><tbody>${sample.rows.map(r=>`<tr><td>${r.number}${r.line_start===null?"":` (lines ${r.line_start}–${r.line_end})`}</td>${sample.columns.map(c=>`<td>${sampleValue(r.values[c.name])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  $("discovery-sample").hidden=false;
});
$("discover-use").onclick=()=>action(async()=>{
  if(!discoverySource)return;
  const selected=structuredClone(discoverySource);
  read({allowTemplateDraft:true}); definition.source=selected; render(); dirty=true;
  notify("Source selected. Existing mappings are unchanged; add or review mappings before Processed Preview.");
});
