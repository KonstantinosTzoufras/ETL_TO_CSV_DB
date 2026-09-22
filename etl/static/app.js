"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let maxOutputColumns=4096;
let token, definition, pipelineId = null, saved = [], dirty = false, busy = false, timer;
const blank = () => ({version:2,name:"Untitled pipeline",source:{kind:"csv",path:"",delimiter:";"},columns:[],destination:{kind:"csv",delimiter:";"}});
let noticeTimer;
// A banner outlives the situation it described, so it clears itself: failures
// stay longer than confirmations, and starting another action clears both.
function notify(message, error = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent=message; $("notice").className=error?"error":"";
  if(message) noticeTimer=setTimeout(()=>{$("notice").textContent="";$("notice").className="";}, error?15000:8000);
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-ETL-Token":token},body:JSON.stringify(body)});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
async function action(fn) {
  if(busy) return;
  busy=true;
  notify("");  // the previous outcome no longer describes what is happening
  const buttons=[...document.querySelectorAll("button")].map(button=>[button,button.disabled]);
  buttons.forEach(([button])=>button.disabled=true);
  try { await fn(); } catch(error) { notify(error.message,true); }
  finally { busy=false; buttons.forEach(([button,disabled])=>button.disabled=disabled); window.EditorControls?.refreshBulk(); window.TemplateState?.refresh(); }
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
  // A table has no delimiter/split concept, and split_by is refused outright
  // for it server-side - build it as its own small shape, not a CSV/XLSX one
  // with fields quietly left over from whatever format was picked before.
  const destination=$("format").value==="sqlserver"
    ?{kind:"sqlserver",connection_env:$("export-connection").value}
    :(()=>{const d={...definition.destination,kind:$("format").value,delimiter:$("output-delimiter").value};
            if($("split-by").value)d.split_by=$("split-by").value;else delete d.split_by;return d;})();
  definition={version:definition.version,name:$("name").value,source:source(),columns,destination};
  return structuredClone(definition);
}
function toggleExportFields(){
  const isDb=$("format").value==="sqlserver";
  document.querySelectorAll("#export-card .file-format-only").forEach(el=>el.hidden=isDb);
  $("export-connection-field").hidden=!isDb;
  $("export-connections").hidden=!isDb;
  $("export-table-hint").hidden=!isDb;
}
$("format").onchange=()=>{toggleExportFields();dirty=true;};
$("export-connections").onclick=()=>action(async()=>{
  const result=await api("/api/export/connections",{});
  const selected=$("export-connection").value;
  $("export-connection").innerHTML='<option value="">Select an approved connection</option>'+result.connections.map(name=>`<option>${esc(name)}</option>`).join("");
  if(selected)$("export-connection").value=selected;
  notify(result.connections.length?"Approved export connections loaded.":"No database connections are approved for export. Set ETL_EXPORT_CONNECTIONS and restart.");
});
function renderColumns() {
  window.EditorControls?.destroyMappings();
  closeBindingPicker();
  $("columns").innerHTML=definition.columns.map((column,i)=>{
    const literal="literal" in column;
    const input=(key,value,extra="")=>`<input aria-label="Field ${i+1} ${key}" data-field="${key}" value="${esc(value)}" ${extra}>`;
    return `<tr><td>${input("name",column.name)}</td><td><select aria-label="Field ${i+1} value from" data-field="mode"><option value="source" ${literal?"":"selected"}>Column</option><option value="literal" ${literal?"selected":""}>Constant</option></select></td><td>${input("value",literal?column.literal:column.source,literal?"":"list=available-columns")}<button type="button" data-choose-source="${i}" ${literal?"hidden":""}>Choose column</button>${column.lookup?'<span class="chip">Lookup enabled</span>':""}</td><td>${input("transforms",(column.transforms||[]).join(", "))}</td><td><input aria-label="Field ${i+1} required" data-field="required" type="checkbox" ${column.required?"checked":""}></td><td>${input("max_length",column.max_length??"",'type="number" min="1" step="1"')}</td><td><select aria-label="Field ${i+1} type" data-field="type">${["string","int","decimal","float","bool","date","datetime"].map(t=>`<option ${(column.type||"string")===t?"selected":""}>${t}</option>`).join("")}</select></td><td><button class="remove" data-remove="${i}" aria-label="Remove field ${i+1}">×</button></td></tr>`;
  }).join("");
  // An empty draft has no field pipeline to describe yet.
  $("stage-guide").hidden = !definition.columns.length;
  // Keep the current choice only if that field still exists; a renamed or
  // removed column falls back to "no split" rather than pointing at nothing.
  const currentSplit=$("split-by").value;
  $("split-by").innerHTML='<option value="">No split — one file</option>'+definition.columns.map(c=>`<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("");
  $("split-by").value=definition.columns.some(c=>c.name===currentSplit)?currentSplit:"";
  renderColumnPicker();
  window.EditorControls?.mappings();
}
function render() {
  resetDiscovery();
  clearColumnPicker();
  $("available-tables").replaceChildren();
  function setDelimiter(id,value) {
    const select=$(id);
    if(![...select.options].some(o=>o.value===value)) select.add(new Option(value,value));
    select.value=value;
  }
  $("name").value=definition.name;
  const s=definition.source;
  $("source-kind").value=s.kind; $("source-path").value=s.path||""; setDelimiter("source-delimiter",s.delimiter||";");
  if(s.encoding && ![...$("encoding").options].some(o=>o.value===s.encoding))$("encoding").add(new Option(s.encoding,s.encoding)); $("encoding").value=s.encoding||"utf-8-sig"; if(s.connection_env && ![...$("connection").options].some(o=>o.value===s.connection_env))$("connection").add(new Option(s.connection_env+" (saved reference; availability checked on use)",s.connection_env)); $("connection").value=s.connection_env||""; $("schema").value=s.schema||"dbo"; $("table").value=s.table||"";
  $("format").value=definition.destination.kind; setDelimiter("output-delimiter",definition.destination.delimiter||";");
  if(definition.destination.kind==="sqlserver"){
    const wanted=definition.destination.connection_env;
    if(wanted && ![...$("export-connection").options].some(o=>o.value===wanted))$("export-connection").add(new Option(wanted+" (not yet refreshed)",wanted));
    $("export-connection").value=wanted||"";
  }
  toggleExportFields();
  $("processing-note").textContent=definition.version===2?"Version 2: NULL and empty string are distinct. Transforms run in the order entered; empty_to_null is explicit.":"Version 1 (legacy): optional empty strings become NULL. Saved pipeline processing behavior is preserved.";
  if(typeof renderQuery==="function")renderQuery(s);
  sourceFields(); renderColumns(); $("split-by").value=definition.destination.split_by||""; window.EditorControls?.sync(); $("json").value=JSON.stringify(definition,null,2);
  if(typeof renderOrderedMode==="function")renderOrderedMode();
  $("results").hidden=true; $("source-columns").replaceChildren(); $("available-columns").replaceChildren();
}
function openDefinition(spec,id=null) {
  if(spec.kind==="ordered_query_export"){openOrdered(spec,id);return;}
  if(typeof clearOrdered==="function")clearOrdered();
  if(typeof clearTemplateDraft==="function")clearTemplateDraft();
  // A stamp is only valid for the save right after generating mappings from a
  // template; switching to an unrelated pipeline must not carry it along.
  if(typeof pipelineProvenance!=="undefined")pipelineProvenance=null;
  definition=structuredClone(spec); pipelineId=id; dirty=false; render(); renderSaved(); describeSave(); renderProvenance(id);
}
function renderSaved() {
  $("pipelines").innerHTML=saved.length?saved.map(p=>`<div class="saved-pipeline"><button class="pipeline ${p.id===pipelineId?"active":""}" data-pipeline="${esc(p.id)}">${esc(p.name)}<small>${esc(p.spec.kind==="ordered_query_export"?"ORDERED QUERIES":p.spec.source.kind.toUpperCase())} → ${esc(p.spec.kind==="ordered_query_export"?`${p.spec.steps.length} FILES`:p.spec.destination.kind.toUpperCase())} · ${esc(String(p.updated).slice(0,16).replace("T"," "))}</small></button><button class="delete-pipeline" data-delete-pipeline="${esc(p.id)}" aria-label="Delete pipeline ${esc(p.name)}">Delete</button></div>`).join(""):'<p class="muted">Save your first pipeline to reuse it here.</p>';
}
async function refreshSaved() { saved=await api("/api/pipelines");renderSaved();describeSave();renderProvenance(pipelineId); }
// Informational only: names how a saved pipeline was originally produced.
// Never used to resolve, change or re-generate anything - the pipeline is
// already a complete, independent definition regardless of what this says.
async function renderProvenance(id){
  const note=$("provenance-note");
  note.dataset.forId=id??"";
  const entry=saved.find(p=>p.id===id);
  const provenance=entry&&entry.provenance;
  if(!provenance){note.hidden=true;note.textContent="";return;}
  note.hidden=false;note.textContent="Checking how this pipeline was generated…";
  try{
    const template=await templateApi("read",{id:provenance.template_id,revision:provenance.template_revision});
    if(note.dataset.forId!==(id??""))return;  // the user moved on while this was in flight
    let text=`Generated from template "${template.name}", revision ${provenance.template_revision}`;
    if(provenance.binding_profile_id){
      try{
        const profile=await profileApi("read",{id:provenance.binding_profile_id});
        text+=`, using the saved binding "${profile.name}"`;
        if(profile.updated!==provenance.binding_profile_updated)text+=" (that binding has changed since)";
      }catch{
        text+=", using a saved binding that no longer exists";
      }
    }
    text+=". This pipeline is independent of both and is unaffected by later changes to either.";
    if(note.dataset.forId===(id??""))note.textContent=text;
  }catch{
    if(note.dataset.forId===(id??""))note.textContent="Generated from a template that could not be read (it may have been removed).";
  }
}
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
function downloadLinks(run){
  const kind=run.spec.destination.kind;
  const rejectedLink=(run.report.files||["rejected.csv"]).includes("rejected.csv")?`<a href="/download/${run.id}/rejected.csv">Rejected rows</a>`:"";
  if(kind==="sqlserver"){
    const table=run.report.table;
    // A run stored before the report carried this can no longer say where its
    // rows went; say so rather than guess.
    return (table?`<span>Written to ${esc(table.schema)}.${esc(table.name)} (${table.rows.toLocaleString()} rows)</span>`
                 :`<span>Written to a database table.</span>`)+rejectedLink;
  }
  // Runs saved before split export existed have no report.files; keep their
  // exact former behaviour rather than guessing at names that may not exist.
  const files=run.report.files||[`valid.${kind}`,"rejected.csv"];
  const valid=files.filter(f=>f!=="rejected.csv");
  if(!valid.length)return rejectedLink;  // every row was rejected; no group was ever created
  const anchor=f=>{
    const group=f.includes("/")?f.split("/")[0]:null;
    return `<a href="/download/${run.id}/${esc(f)}">${group?esc(group):`Download ${esc(kind.toUpperCase())}`}</a>`;
  };
  if(valid.length<=4)return valid.map(anchor).join("")+rejectedLink;  // a few groups read fine as plain links
  const splitBy=run.spec.destination.split_by;
  return `<details class="split-downloads"><summary>${valid.length} files${splitBy?` · split by ${esc(splitBy)}`:""}</summary>${valid.map(anchor).join("")}</details>${rejectedLink}`;
}
async function refreshRuns() {
  const runs=await api("/api/runs");
  $("runs").innerHTML=runs.length?runs.map(run=>run.spec.kind==="ordered_query_export"?orderedRunCard(run):`<div class="run"><div><strong>${esc(run.name)}</strong><p>${esc(new Date(run.started).toLocaleString())} · ${run.report.processed??0} processed · ${run.report.valid??0} valid · ${run.report.invalid??0} rejected</p>${run.error?`<div class="reason">${esc(run.error)}</div>`:""}</div><div><span class="run-status ${esc(run.status)}">${esc(run.status)}</span>${run.status==="completed"?`<br>${downloadLinks(run)}<button data-diagnostics="${esc(run.id)}">View rejection diagnostics</button>`:""}</div></div>`).join(""):'<p class="muted">No runs yet. Preview your pipeline, then run an export.</p>';
  clearTimeout(timer);
  if(runs.some(r=>["queued","running"].includes(r.status))) timer=setTimeout(()=>refreshRuns().catch(e=>notify(e.message,true)),1500);
}
document.addEventListener("input",event=>{if(event.target.closest("main") && event.target.id!=="json" && !event.target.closest('#bulk-transforms') && !event.target.dataset.bulkSelect) dirty=true;if(event.target.closest("#columns") && event.target.dataset.field==="value")event.target.dataset.edited="true";});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
$("source-kind").onchange=()=>{sourceFields();dirty=true;};
$("new").onclick=()=>{if(!dirty||confirm("Discard unsaved changes and create a new pipeline?"))openDefinition(blank());};
$("pipelines").onclick=event=>{
  const remove=event.target.closest('[data-delete-pipeline]');
  if(remove){
    const item=saved.find(p=>p.id===remove.dataset.deletePipeline);if(!item)return;
    if(!confirm(`Delete saved pipeline "${item.name}"? Run history and exported files will be kept. If this pipeline is open, its current draft will remain available.`))return;
    return action(async()=>{
      await api('/api/pipelines/delete',{id:item.id});
      if(pipelineId===item.id){pipelineId=null;dirty=true;}
      await refreshSaved();notify('Saved pipeline deleted. History and exports were kept.');
    });
  }
  const button=event.target.closest('[data-pipeline]');
  if(button&&(!dirty||confirm('Discard unsaved changes?')))openDefinition(saved.find(p=>p.id===button.dataset.pipeline).spec,button.dataset.pipeline);
};
$("columns").onclick=event=>{const button=event.target.closest("[data-remove]");if(button){read();definition.columns.splice(Number(button.dataset.remove),1);renderColumns();dirty=true;}};
$("add").onclick=()=>action(async()=>{read();definition.columns.push({name:"",source:"",type:"string"});renderColumns();dirty=true;});
$("inspect").onclick=()=>action(async()=>{
  if(typeof templateIsPending==="function" && templateIsPending())throw new Error("Use Read binding columns in the template binding panel; mappings stay explicit.");
  if($("source-kind").value==="sqlserver_query"){
    const draft=source();
    if(!draft.connection_env)throw new Error("Select an approved query connection first.");
    if(!draft.query.sql.trim())throw new Error("Write the SELECT query first, then use Inspect query columns.");
  }
  if($("source-kind").value==="sqlserver" && !$("table").value){
    $("discovery").open=true;
    if(!$("connection").value)throw new Error("Select a configured database connection first.");
    await browseDiscovery($("schema").value?[$("schema").value]:[]);
    notify("Choose a table below, then Use this dataset to load its columns.");
    $("discovery-browser").scrollIntoView({behavior:"smooth",block:"center"});
    return;
  }
  const candidate=source(), generation=discoveryGeneration;
  const result=await api("/api/columns",{source:candidate});
  if(generation!==discoveryGeneration)return;
  $("available-columns").innerHTML=result.columns.map(c=>`<option value="${esc(c)}">`).join("");
  $("source-columns").innerHTML=result.columns.map(c=>`<span class="chip">${esc(c)}</span>`).join("");
  read(); setColumnPicker(result.columns,candidate);
  notify(`${result.columns.length} source columns available. Select some or all, then Add selected columns.`);
});
// A reload forgets which saved pipeline is on screen, so saving again would
// quietly create a twin under the same name. Ask instead of guessing.
// Dismissing a dialog must never be the branch that writes something. Escape
// and Cancel both mean "do nothing"; a same-name copy needs a new name instead.
const ABORT=Symbol("abort");
function saveTarget(spec){
  if(pipelineId)return pipelineId;
  const twins=saved.filter(item=>item.name===spec.name);
  if(!twins.length)return null;
  return confirm(`"${spec.name}" is already saved${twins.length>1?` (${twins.length} copies)`:""}.

OK — update it
Cancel — save nothing, so you can give this one another name`)
    ?twins[0].id:ABORT;
}
function describeSave(){
  const current=saved.find(item=>item.id===pipelineId);
  $("save-hint").textContent=current
    ?`Saving updates "${current.name}".`
    :"Saving creates a new saved pipeline.";
}
$("save").onclick=()=>action(async()=>{
  const spec=workingDefinition(), target=saveTarget(spec);
  if(target===ABORT){notify(`Nothing was saved. Rename this pipeline to keep it beside "${spec.name}".`);return;}
  const result=await api("/api/pipelines",{id:target,spec,provenance:typeof pipelineProvenance!=="undefined"?pipelineProvenance:undefined});
  pipelineId=result.id;dirty=false;await refreshSaved();
  notify(target?"Pipeline updated.":"Pipeline saved as a new entry. You can load it from the sidebar.");
});
$("preview").onclick=()=>action(async()=>{const report=typeof orderedDraft!=="undefined" && orderedDraft?await api("/api/ordered/preview",{spec:workingDefinition(),step_id:orderedDraft.steps[orderedIndex].id,diagnostics:true}):await api("/api/preview",{spec:read(),diagnostics:true});preview(report);notify("Preview complete. No export files were created.");});
$("run").onclick=()=>action(async()=>{
  const spec=workingDefinition();
  if(spec.destination&&spec.destination.kind==="sqlserver"&&!pipelineId)
    throw new Error("Save this pipeline before running a database export, so its table can be found again next time.");
  await api("/api/runs",{spec,pipeline_id:pipelineId});
  notify("Export started. Follow its progress in Run history.");await refreshRuns();
});
$("refresh").onclick=()=>action(refreshRuns);
$("advanced").ontoggle=()=>{if($("advanced").open){try{$("json").value=JSON.stringify(workingDefinition(),null,2);}catch(error){$("advanced").open=false;notify(error.message,true);}}};
$("json").oninput=()=>{dirty=true;};
$("apply").onclick=()=>action(async()=>{read();const next=JSON.parse($("json").value);await api("/api/validate",{spec:next});openDefinition(next,pipelineId);dirty=true;notify("Definition applied. Save the pipeline to keep these changes.");});
(async()=>{try{const bootstrap=await api("/api/bootstrap");token=bootstrap.token; maxOutputColumns=bootstrap.max_output_columns||4096; (bootstrap.source_connections||[]).forEach(name=>$("connection").add(new Option(name,name))); $("connection-note").textContent=(bootstrap.source_connections||[]).length?"Choose a configured reference, then Browse datasets to select a schema and table. Query approval is separate.":"No database connections are configured in this server process. Add an ETL_SQL_* connection or DB_HOST/DB_NAME/DB_USER/DB_PASS to the workspace .env and restart. CSV browsing is available."; if(bootstrap.output_directory)$("export-location").textContent=`Generated files: ${bootstrap.output_directory} (one folder per run). Download completed files from Run history; your browser chooses where downloaded copies are saved.`; openDefinition(bootstrap.example||blank());await refreshSaved();await refreshRuns();}catch(error){notify(error.message,true);}})();

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
let discoveryGeneration=0, discoveryNamespace=[], discoveryFolders=[], discoveryDatasets=[], discoverySource=null, discoveryColumns=[];
// The full column catalog from the last successful inspection, independent
// of discoverySource: applying a dataset to the pipeline (Use this dataset)
// re-renders the source fields and resets the discovery panel through the
// usual sourceEdited path, but a template field generated from what was just
// inspected should not disappear because of that.
let lastInspectedColumns=[];
let foldersCursor=null, datasetsCursor=null;
function resetDiscovery() {
  discoveryGeneration++;
  discoverySource=null;
  discoveryColumns=[];
  $("discovery-feedback").textContent="";
  for(const id of ["discovery-browser","discovery-selection","discovery-sample"]) $(id).hidden=true;
}
const discoveryInputs=new Set(["source-kind","source-path","source-delimiter","encoding","connection","schema","table","query-connection","query-sql","query-parameters","query-timeout"]);
function sourceEdited(event) { if(["source-kind","connection","schema"].includes(event.target.id))$("available-tables").replaceChildren(); if(discoveryInputs.has(event.target.id)){resetDiscovery();clearColumnPicker();$("available-columns").replaceChildren();$("source-columns").replaceChildren();$("mapping-guidance").textContent="Source settings changed. Reload columns and review existing mappings before preview or export.";} }
document.addEventListener("input",sourceEdited);
document.addEventListener("change",sourceEdited);
function discoveryContext() { return {connector:$("source-kind").value,connection_env:$("source-kind").value==="sqlserver_query"?$("query-connection").value:$("connection").value}; }
async function discoveryRequest(operation, values={}) {
  const generation=discoveryGeneration;
  $("discovery-feedback").textContent="Loading source information…";
  try {
    const result=await api(`/api/discovery/${operation}`,{...discoveryContext(),...values});
    if(generation===discoveryGeneration)$("discovery-feedback").textContent="Source information loaded.";
    return result;
  } catch(error) {
    if(generation===discoveryGeneration)$("discovery-feedback").textContent=error.message;
    throw error;
  }
}
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
  if(!more||folders){discoveryFolders=more?[...discoveryFolders,...(folders?.items||[])]:folders?.items||[];foldersCursor=folders?.next_cursor||null;}
  if(!more||datasets){discoveryDatasets=more?[...discoveryDatasets,...(datasets?.items||[])]:datasets?.items||[];datasetsCursor=datasets?.next_cursor||null;}
  if(!more)$("dataset-search").value="";
  $("discovery-browser").hidden=false;
  $("discovery-location").textContent=namespace.join(" / ")||(sql?"Configured database schemas":"Workspace folders and CSV files");
  $("discover-up").hidden=!namespace.length;
  $("discover-folders-next").hidden=!foldersCursor; $("discover-datasets-next").hidden=!datasetsCursor;
  $("discovery-folders").innerHTML=discoveryFolders.map((n,i)=>`<button data-folder="${i}">${esc(n[n.length-1])} /</button>`).join("");
  renderDatasets();
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
  discoveryColumns=columns.map(m=>m.column.name);
  lastInspectedColumns=columns;
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
  const names=[...discoveryColumns];
  read({allowTemplateDraft:true}); definition.source=selected; render(); dirty=true;
  setColumnPicker(names,selected);
  $("column-picker").scrollIntoView({behavior:"smooth",block:"center"});
  notify("Source selected. Choose some or all columns below, then Add selected columns. Existing mappings are unchanged.");
});

function renderDatasets() {
  if($("source-kind").value==="sqlserver")$("available-tables").innerHTML=discoveryDatasets.map(d=>`<option value="${esc(d.name)}">`).join("");
  const search=$("dataset-search").value.toLocaleLowerCase();
  $("discovery-datasets").innerHTML=discoveryDatasets.map((d,i)=>({d,i})).filter(({d})=>d.name.toLocaleLowerCase().includes(search)).map(({d,i})=>`<button data-dataset="${i}">${esc(d.name)} (${esc(d.kind)})</button>`).join("")||'<p class="hint">No matching datasets loaded. Choose a schema/folder or load more results.</p>';
}
$("dataset-search").oninput=renderDatasets;
let pickerColumns=[], pickerSelected=new Set();
function clearColumnPicker() {
  pickerColumns=[]; pickerSelected.clear(); closeBindingPicker(); $("column-picker").hidden=true;
}
function setColumnPicker(names,candidate) {
  $("column-picker").open=true; pickerColumns=[...new Set(names)]; pickerSelected.clear(); $("column-search").value="";
  $("picker-source").textContent=candidate.kind==="csv"?candidate.path:candidate.kind==="sqlserver"?`${candidate.schema}.${candidate.table}`:"Selected SQL query";
  $("available-columns").innerHTML=pickerColumns.map(name=>`<option value="${esc(name)}">`).join("");
  $("mapping-guidance").textContent="Choose columns above. Existing mappings are preserved; review their source bindings if you changed datasets.";
  renderColumnPicker();
  if(pickerColumns.length && pickerColumns.every(name=>definition.columns.some(column=>column.source===name)))$("column-picker").open=false;
}
function renderColumnPicker() {
  $("column-picker").hidden=!pickerColumns.length;
  const used=new Set((definition?.columns||[]).filter(c=>"source" in c).map(c=>c.source));
  for(const name of used)pickerSelected.delete(name);
  const search=$("column-search").value.toLocaleLowerCase();
  $("column-choices").innerHTML=pickerColumns.map((name,i)=>({name,i})).filter(({name})=>name.toLocaleLowerCase().includes(search)).map(({name,i})=>`<label><input type="checkbox" data-pick="${i}" ${used.has(name)||pickerSelected.has(name)?"checked":""}>${esc(name)}${used.has(name)?" (added; untick to remove)":""}</label>`).join("")||'<p>No matching columns.</p>';
  $("column-selection-count").textContent=`${pickerSelected.size} of ${pickerColumns.length} columns selected · ${pickerColumns.filter(name=>used.has(name)).length} already added`;
}
$("column-search").oninput=renderColumnPicker;
$("column-choices").onchange=event=>action(async()=>{
  const i=event.target.dataset.pick;if(i===undefined)return;
  const name=pickerColumns[Number(i)];read();
  const affected=definition.columns.filter(column=>column.source===name);
  if(!event.target.checked && affected.length){
    if(confirm(`Remove ${affected.length} mapping(s) using source column "${name}"? Their configured rules will also be removed.`)){
      definition.columns=definition.columns.filter(column=>column.source!==name);
      pickerSelected.delete(name);renderColumns();dirty=true;
      notify(`Removed ${affected.length} mapping(s). Other fields and rules are unchanged.`);
    }
  }else if(event.target.checked)pickerSelected.add(name);else pickerSelected.delete(name);
  renderColumnPicker();
});
$("columns-all").onclick=()=>action(async()=>{read();pickerSelected=new Set(pickerColumns);renderColumnPicker();});
$("columns-clear").onclick=()=>{pickerSelected.clear();renderColumnPicker();};
function addPickedColumns() {
  read();
  const used=new Set(definition.columns.filter(c=>"source" in c).map(c=>c.source));
  const selected=pickerColumns.filter(name=>pickerSelected.has(name)&&!used.has(name));
  if(!selected.length){notify("Select at least one unused source column.");return;}
  if(definition.columns.length+selected.length>maxOutputColumns)throw new Error(`A pipeline supports up to ${maxOutputColumns} output fields. Select fewer columns.`);
  const taken=new Set(definition.columns.map(c=>c.name.toLowerCase()));
  for(const source of selected){let name=source,n=2;while(taken.has(name.toLowerCase()))name=`${source}_${n++}`;taken.add(name.toLowerCase());definition.columns.push({name,source,type:"string"});}
  pickerSelected.clear();renderColumns();dirty=true;
  if(pickerColumns.every(name=>definition.columns.some(c=>c.source===name))){$("column-picker").open=false;$("mapping-card").querySelector(".section-body").scrollTop=0;}
  notify(`${selected.length} output fields added in source order. Review types and rules, then Processed Preview.`);
}
$("columns-add").onclick=()=>action(async()=>addPickedColumns());
$("columns-add-all").onclick=()=>action(async()=>{read();pickerSelected=new Set(pickerColumns);addPickedColumns();});

let bindingRow=null;
function closeBindingPicker(){bindingRow=null;$("binding-picker").hidden=true;}
async function loadAvailableColumns(){
  const candidate=source(), generation=discoveryGeneration;
  if(candidate.kind==="sqlserver" && !candidate.table){
    $("discovery").open=true;
    throw new Error("Choose a table first: Browse datasets → schema → table → Use this dataset.");
  }
  const result=await api("/api/columns",{source:candidate});
  if(generation!==discoveryGeneration)return false;
  setColumnPicker(result.columns,candidate);
  return true;
}
function renderBindingOptions(){
  const query=$("binding-search").value.toLocaleLowerCase();
  const matches=pickerColumns.map((name,index)=>({name,index})).filter(({name})=>name.toLocaleLowerCase().includes(query));
  $("binding-options").innerHTML=matches.slice(0,100).map(({name,index})=>`<button data-bind-column="${index}">${esc(name)}</button>`).join("");
  $("binding-status").textContent=matches.length?`${matches.length} matching columns${matches.length>100?"; showing first 100. Narrow the search to find another column.":""}`:"No matching columns.";
}
$("columns").addEventListener("click",event=>{
  const button=event.target.closest("[data-choose-source]");
  if(!button)return;
  action(async()=>{
    read();
    const row=button.closest("tr");
    if(!pickerColumns.length && !await loadAvailableColumns())return;
    if(!row.isConnected)return;
    bindingRow=row;$("binding-search").value="";
    $("binding-title").textContent=`Choose source for ${row.querySelector('[data-field="name"]').value||"unnamed output field"}`;
    renderBindingOptions();$("binding-picker").hidden=false;
    $("binding-picker").scrollIntoView({behavior:"smooth",block:"center"});$("binding-search").focus();
  });
});
$("binding-search").oninput=renderBindingOptions;
$("binding-close").onclick=closeBindingPicker;
$("binding-search").onkeydown=event=>{if(event.key==="Escape")closeBindingPicker();};
$("binding-options").onclick=event=>{
  const button=event.target.closest("[data-bind-column]");
  if(!button || !bindingRow?.isConnected)return;
  const input=bindingRow.querySelector('[data-field="value"]');
  input.value=pickerColumns[Number(button.dataset.bindColumn)];input.dataset.edited="true";
  dirty=true;read();renderColumnPicker();closeBindingPicker();input.focus();
};
$("columns").addEventListener("change",event=>{
  if(event.target.dataset.field!=="mode")return;
  const row=event.target.closest("tr"), literal=event.target.value==="literal";
  row.querySelector('[data-choose-source]').hidden=literal;
  const input=row.querySelector('[data-field="value"]');
  if(literal)input.removeAttribute("list");else input.setAttribute("list","available-columns");
  closeBindingPicker();
});

$("choose-table").onclick=()=>action(async()=>{
  $("discovery").open=true;
  if(!$("connection").value)throw new Error("Select a configured database connection first.");
  await browseDiscovery($("schema").value?[$("schema").value]:[]);
  $("discovery-browser").scrollIntoView({behavior:"smooth",block:"center"});
  $("dataset-search").focus();
});
