"use strict";
let templateDraft=null, templateEditorRevision=null, sourceColumns=[], matchProposals=null;
// The binding currently loaded, and the stamp the next save will carry.
let loadedProfile=null, pipelineProvenance=null;
const profileApi=(operation,body={})=>api(`/api/profiles/${operation}`,body);
function templateIsPending(){return templateDraft!==null;}
function clearTemplateDraft(){templateDraft=null;matchProposals=null;sourceColumns=[];loadedProfile=null;$("template-bindings").hidden=true;$("template-source-columns").replaceChildren();$("template-match-summary").textContent="";$("profile-summary").textContent="";}
const templateApi=(operation,body={})=>api(`/api/templates/${operation}`,body);
function selectedTemplate(){const value=$("template-select").value;if(!value)throw new Error("Select a template revision.");const [id,revision]=value.split(":");return {id,revision:Number(revision)};}
async function listTemplates(select=null){
  const items=await templateApi("list");
  $("template-select").innerHTML='<option value="">Select a revision</option>'+items.map(t=>`<option value="${esc(t.id)}:${t.revision}">${esc(t.name)} · revision ${t.revision} · processing v${t.processing_version}</option>`).join("");
  if(select)$("template-select").value=`${select.id}:${select.revision}`;
}
function addTemplateField(field={output_name:"",target_type:"string",transforms:[],required:false,lookup_required:false}){
  const wrapper=document.createElement("div");wrapper.className="template-field";
  wrapper.innerHTML=`<label class="grow">Output name<input data-template="output_name" value="${esc(field.output_name)}"></label><label>Target type<select data-template="target_type">${["string","int","decimal","float","bool","date","datetime"].map(t=>`<option ${t===field.target_type?"selected":""}>${t}</option>`).join("")}</select></label><label>Max length<input data-template="max_length" type="number" min="1" value="${esc(field.max_length??"")}"></label><label class="transforms">Ordered transforms<input data-template="transforms" placeholder="trim, empty_to_null" value="${esc(field.transforms.join(", "))}"></label><div class="checks"><label class="check"><input data-template="required" type="checkbox" ${field.required?"checked":""}>Required</label><label class="check"><input data-template="lookup_required" type="checkbox" ${field.lookup_required?"checked":""}>Lookup required</label></div><button class="remove" data-remove-target>Remove target</button>`;
  $("template-fields").append(wrapper);
  window.EditorControls?.templates();
}
function blueprint(){return {format_version:1,name:$("template-name").value,processing_version:Number($("template-version").value),fields:[...$("template-fields").children].map(row=>{
  const input=k=>row.querySelector(`[data-template="${k}"]`);
  return {output_name:input("output_name").value,target_type:input("target_type").value,transforms:input("transforms").value.split(",").map(s=>s.trim()).filter(Boolean),required:input("required").checked,max_length:input("max_length").value===""?null:Number(input("max_length").value),lookup_required:input("lookup_required").checked};
})};}
function loadTemplateEditor(template){templateEditorRevision={id:template.id,revision:template.revision};$("template-name").value=template.name;$("template-version").value=String(template.processing_version);$("template-fields").replaceChildren();template.fields.forEach(addTemplateField);$("template-editor-origin").textContent=`Editing ${template.name}, revision ${template.revision}. Saving creates a new revision.`;}
function bindingRows(){return [...$("template-binding-fields").children];}
function lockedTargets(){return bindingRows().map(row=>{
  const mode=row.querySelector("[data-binding-mode]").value;
  return mode!=="" && (mode!=="source" || row.querySelector("[data-binding-source]").value!=="");
});}
function bindingValues(){return bindingRows().map(row=>{
  const mode=row.querySelector("[data-binding-mode]").value;
  if(!mode)return null;
  let binding;
  if(mode==="source"){const name=row.querySelector("[data-binding-source]").value;if(!name)return null;binding={source:name};}
  else binding={literal:mode==="null"?null:mode==="empty"?"":mode==="true"?true:mode==="false"?false:row.querySelector("[data-binding-text]").value};
  const lookup=row.querySelector("[data-binding-lookup]");
  if(lookup && lookup.value.trim())binding.lookup=JSON.parse(lookup.value);
  return binding;
});}
function updateBindingState(){
  if(!templateDraft)return;
  let unresolved=0;
  bindingRows().forEach((row,i)=>{
    const mode=row.querySelector("[data-binding-mode]").value;
    row.querySelector("[data-source-label]").hidden=mode!=="source";
    row.querySelector("[data-text-label]").hidden=mode!=="text";
    const bound=mode && (mode!=="source" || row.querySelector("[data-binding-source]").value!=="");
    const lookup=row.querySelector("[data-binding-lookup]");
    let lookupReady=!lookup;
    if(lookup){try{const v=JSON.parse(lookup.value);lookupReady=!!v&&typeof v==="object"&&!!v.source&&typeof v.column==="string"&&v.column.length>0;}catch{lookupReady=false;}}
    if(!bound||!lookupReady)unresolved++;
    const note=matchProposals&&matchProposals[i];
    let matchText="";
    if(note&&note.status==="matched"&&bound)matchText=` · proposed by name (${note.rule})`;
    if(note&&note.status==="ambiguous")matchText=` · ambiguous: ${note.candidates.join(", ")}`;
    if(note&&note.status==="unmatched")matchText=" · no column of this name";
    if(note&&note.status==="profile")matchText=` · ${note.note}`;
    row.classList.toggle("binding-open",!bound||!lookupReady);
    row.querySelector("[data-binding-state]").textContent=`${templateDraft.template.fields[i].required?"Required":"Optional"} · ${bound?"Bound":"Needs mapping"}${lookup&&!lookupReady?" · Lookup unresolved":""}${matchText}`;
  });
  $("template-binding-status").textContent=unresolved?`${unresolved} targets still need mapping. Save, preview and run require applied mappings.`:"Every target is mapped. Apply mappings to write them into the pipeline.";
  $("template-generate").disabled=unresolved>0;
}
function renderBindings(){
  const template=templateDraft.template;
  $("template-copy-name").textContent=`Copied ${template.name}, revision ${template.revision} · processing v${template.processing_version}`;
  $("template-binding-fields").innerHTML=template.fields.map(f=>`<div class="template-binding"><h3>${esc(f.output_name)} → ${esc(f.target_type)}</h3><p class="hint">Transforms: ${esc(f.transforms.join(" → ")||"none")}${f.max_length===null?"":` · Max length ${f.max_length}`}</p><p data-binding-state></p><label>Bind ${esc(f.output_name)}<select data-binding-mode aria-label="Bind ${esc(f.output_name)}"><option value="">Unmapped — choose explicitly</option><option value="source">Source column</option><option value="text">Text literal</option><option value="null">NULL literal</option><option value="empty">Empty-string literal</option><option value="true">Boolean true literal</option><option value="false">Boolean false literal</option></select></label><label data-source-label hidden>Source column for ${esc(f.output_name)}<input data-binding-source list="template-source-columns"></label><label data-text-label hidden>Text literal for ${esc(f.output_name)}<textarea data-binding-text rows="2"></textarea></label>${f.lookup_required?`<label>Lookup rule for ${esc(f.output_name)}<textarea data-binding-lookup rows="4" placeholder='Existing lookup JSON: {"source": {...}, "column": "..."}'></textarea></label><p class="hint">Configure the existing lookup source and column explicitly. Connection references belong to this pipeline only.</p>`:""}</div>`).join("");
  $("template-bindings").hidden=false;$("profile-name").value=defaultProfileName();
  updateBindingState();window.EditorControls?.templates();
  listProfiles().catch(error=>notify(error.message,true));
}
// action() restores each button to the state it had before the request, so the
// Apply guard has to be recomputed afterwards rather than set during it.
window.TemplateState={refresh:updateBindingState};
// --- Saved bindings -------------------------------------------------------
// A profile is loaded, reviewed and applied by hand. Nothing here resolves a
// target on its own, and nothing reaches the pipeline before Apply mappings.

function bindingByTarget(){
  const values=bindingValues(), fields=templateDraft.template.fields, result={};
  fields.forEach((field,index)=>{if(values[index])result[field.output_name]=values[index];});
  return result;
}
function writeBinding(row,binding){
  const mode=row.querySelector("[data-binding-mode]"), input=row.querySelector("[data-binding-source]");
  const text=row.querySelector("[data-binding-text]");
  if("source" in binding){mode.value="source";input.value=binding.source;}
  else {
    const value=binding.literal;
    mode.value=value===null?"null":value===""?"empty":value===true?"true":value===false?"false":"text";
    if(mode.value==="text")text.value=value;
  }
  const lookup=row.querySelector("[data-binding-lookup]");
  if(lookup&&binding.lookup)lookup.value=JSON.stringify(binding.lookup);
  for(const element of [mode,input,text,lookup])if(element)element.dispatchEvent(new Event("input",{bubbles:true}));
}
function defaultProfileName(){
  const chosen=source();
  // Split on both separators; fromCharCode(92) avoids an escaped literal here.
  if(chosen.kind==="csv")return (chosen.path||"").split("/").pop().split(String.fromCharCode(92)).pop();
  const connection=(chosen.connection_env||"").replace(/^ETL_SQL_/,"");
  if(chosen.kind==="sqlserver")return `${connection} · ${chosen.schema}.${chosen.table}`;
  return `${connection} · query`;
}
async function listProfiles(select=null){
  if(!templateDraft)return;
  const items=await profileApi("list",{template_id:templateDraft.template.id,source:source()});
  const label={exact:"for this source",'same-dataset':"same table, another connection",other:"another source"};
  $("profile-select").innerHTML='<option value="">No saved binding selected</option>'+
    items.map(item=>`<option value="${esc(item.id)}">${esc(item.name)} · ${esc(label[item.rank])}</option>`).join("");
  if(select)$("profile-select").value=select;
  const exact=items.filter(item=>item.rank==="exact").length;
  $("profile-summary").textContent=items.length
    ?`${items.length} saved for this template · ${exact} for this source. Pick one and press Load binding.`
    :"No saved bindings yet for this template. Map the targets once, then save them for next time.";
  return items;
}
function describeReconcile(result){
  const c=result.counts, parts=[`${c.restored} restored`];
  if(c.missing)parts.push(`${c.missing} source column${c.missing>1?"s":""} gone`);
  if(c.new)parts.push(`${c.new} not in the binding`);
  if(c.removed)parts.push(`${c.removed} dropped (${result.removed.join(", ")} no longer in the template)`);
  if(result.revision_changed)parts.push(`written for revision ${result.authored_revision}`);
  for(const change of result.rule_changes||[])parts.push(`${change.target}: ${change.rules.join(", ")} changed since`);
  return parts.join(" · ");
}
$("profile-load").onclick=()=>action(async()=>{
  const id=$("profile-select").value;
  if(!id)throw new Error("Select a saved binding first.");
  const columns=sourceColumns.length?sourceColumns:await loadSourceColumns();
  const result=await profileApi("reconcile",{id,template:templateDraft.template,columns});
  result.entries.forEach((entry,index)=>{if(entry.binding)writeBinding(bindingRows()[index],entry.binding);});
  matchProposals=result.entries.map(entry=>({status:"profile",rule:null,candidates:[],note:entry.note}));
  loadedProfile=result.profile;
  $("profile-name").value=result.profile.name;
  $("profile-summary").textContent=`Loaded "${result.profile.name}" · ${describeReconcile(result)}`;
  updateBindingState();dirty=true;
  notify(`Binding loaded. ${result.counts.restored} targets restored; review before applying.`);
});
$("profile-save").onclick=()=>action(async()=>saveProfile(null));
$("profile-update").onclick=()=>action(async()=>{
  if(!loadedProfile)throw new Error("Load a saved binding before updating one.");
  await saveProfile(loadedProfile);
});
async function saveProfile(existing){
  const name=$("profile-name").value.trim();
  if(!name)throw new Error("Give the binding a name first.");
  const saved=await profileApi("save",{
    id:existing?existing.id:undefined, name, template_id:templateDraft.template.id,
    revision:templateDraft.template.revision, source:source(),
    schema_snapshot:sourceColumns, bindings:bindingByTarget(),
    updated:existing?existing.updated:undefined});
  loadedProfile={id:saved.id,name:saved.name,updated:saved.updated};
  await listProfiles(saved.id);
  notify(existing?`Binding "${saved.name}" updated.`:`Binding "${saved.name}" saved for this source.`);
}
$("profile-delete").onclick=()=>action(async()=>{
  const id=$("profile-select").value;
  if(!id)throw new Error("Select a saved binding first.");
  const chosen=$("profile-select").selectedOptions[0].textContent;
  if(!confirm(`Delete the saved binding "${chosen}"? Pipelines already generated from it are unaffected.`))return;
  await profileApi("delete",{id});
  if(loadedProfile&&loadedProfile.id===id)loadedProfile=null;
  await listProfiles();
  notify("Saved binding deleted. No pipeline changed.");
});
$("template-refresh").onclick=()=>action(()=>listTemplates());
$("template-load").onclick=()=>action(async()=>{loadTemplateEditor(await templateApi("read",selectedTemplate()));notify("Revision loaded into the template editor. Pipelines are unchanged.");});
$("template-create").onclick=()=>action(async()=>{const value=await templateApi("create",{definition:blueprint()});loadTemplateEditor(value);await listTemplates(value);notify("Template created with immutable revision 1.");});
$("template-revise").onclick=()=>action(async()=>{if(!templateEditorRevision)throw new Error("Load a revision before saving a new revision.");const value=await templateApi("revision",{id:templateEditorRevision.id,base_revision:templateEditorRevision.revision,definition:blueprint()});loadTemplateEditor(value);await listTemplates(value);notify("New revision saved. Existing copied drafts, pipelines and runs are unchanged.");});
$("template-add-field").onclick=()=>{addTemplateField();dirty=true;};
$("template-generate-fields").onclick=()=>action(async()=>{
  if(!lastInspectedColumns.length)throw new Error("Inspect a database table above (Discover a source) first.");
  const {fields}=await templateApi("from_columns",{columns:lastInspectedColumns});
  fields.forEach(addTemplateField);
  notify(`${fields.length} fields proposed from the table's own columns. Review each one - types and lengths are a guess from the catalog, not a decision.`);
});
$("template-fields").onclick=event=>{if(event.target.closest("[data-remove-target]")){event.target.closest(".template-field").remove();dirty=true;}};
$("template-apply").onclick=()=>action(async()=>{
  if(templateDraft)throw new Error("A copied template is already pending. Finish it or start a new draft.");
  const request={...selectedTemplate(),spec:read()};
  const result=await templateApi("apply",request);
  // Refuse a late response if the editor changed while the request was running.
  if(JSON.stringify(read())!==JSON.stringify(request.spec))throw new Error("Draft changed while applying; apply again.");
  templateDraft=result;renderBindings();dirty=true;notify("Template copied. Every source/literal and lookup binding remains explicit.");
});
async function loadSourceColumns(){
  const selected=source();
  const result=await api("/api/columns",{source:selected});
  // Refuse a late response: the columns must belong to the source on screen.
  if(JSON.stringify(source())!==JSON.stringify(selected))throw new Error("Source changed; load the columns again.");
  sourceColumns=result.columns;
  $("template-source-columns").innerHTML=result.columns.map(c=>`<option value="${esc(c)}">`).join("");
  return result.columns;
}
$("template-load-columns").onclick=()=>action(async()=>{const columns=await loadSourceColumns();notify(`${columns.length} source columns loaded. Nothing was bound.`);});
$("template-match").onclick=()=>action(async()=>{
  if(!templateDraft)throw new Error("Apply a template revision first.");
  const columns=sourceColumns.length?sourceColumns:await loadSourceColumns();
  const locked=lockedTargets();
  const result=await templateApi("match",{template:templateDraft.template,columns,locked});
  // A proposal is only a suggestion until it is written into a row below.
  result.proposals.forEach((proposal,index)=>{
    if(proposal.status!=="matched")return;
    const row=bindingRows()[index];
    const mode=row.querySelector("[data-binding-mode]"), input=row.querySelector("[data-binding-source]");
    mode.value="source";input.value=proposal.binding.source;
    for(const element of [mode,input])element.dispatchEvent(new Event("input",{bubbles:true}));
  });
  // Keep a profile's note on rows matching left alone, so the panel still says
  // where each restored binding came from.
  matchProposals=result.proposals.map((proposal,index)=>
    proposal.status==="kept"&&matchProposals&&matchProposals[index]?matchProposals[index]:proposal);
  const c=result.counts;
  $("template-match-summary").textContent=`${c.matched} proposed by name · ${c.ambiguous} ambiguous · ${c.unmatched} with no matching column · ${c.kept} left as you set them. Nothing is written until Apply mappings.`;
  updateBindingState();dirty=true;
  notify(`${c.matched} targets proposed. Review them, then Apply mappings.`);
});
$("template-binding-fields").oninput=()=>{updateBindingState();dirty=true;};
$("template-binding-fields").onchange=()=>{updateBindingState();dirty=true;};
$("template-generate").onclick=()=>action(async()=>{
  if(!templateDraft)return;
  const copy=templateDraft, spec=read({allowTemplateDraft:true}), bindings=bindingValues();
  const generated=await templateApi("bind",{template:copy.template,spec,bindings});
  if(templateDraft!==copy || JSON.stringify(read({allowTemplateDraft:true}))!==JSON.stringify(spec) || JSON.stringify(bindingValues())!==JSON.stringify(bindings))throw new Error("Bindings changed while generating; generate again.");
  const stamp={template_id:copy.template.id,template_revision:copy.template.revision};
  if(loadedProfile){stamp.binding_profile_id=loadedProfile.id;stamp.binding_profile_updated=loadedProfile.updated;}
  clearTemplateDraft();definition=generated;pipelineProvenance=stamp;render();dirty=true;
  notify("Mappings applied. The pipeline is independent of the template and the binding; review with Processed Preview.");
});
document.addEventListener("input",event=>{if(discoveryInputs.has(event.target.id))$("template-source-columns").replaceChildren();});
addTemplateField();
