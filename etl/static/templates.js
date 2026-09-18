"use strict";
let templateDraft=null, templateEditorRevision=null;
function templateIsPending(){return templateDraft!==null;}
function clearTemplateDraft(){templateDraft=null;$("template-bindings").hidden=true;$("template-source-columns").replaceChildren();}
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
    row.querySelector("[data-binding-state]").textContent=`${templateDraft.template.fields[i].required?"Required":"Optional"} · ${bound?"Bound":"Unmapped"}${lookup&&!lookupReady?" · Lookup unresolved":""}`;
  });
  $("template-binding-status").textContent=unresolved?`${unresolved} targets unresolved. Save, preview and run require generated mappings.`:"All bindings supplied. Generate mappings to validate the existing pipeline rules.";
}
function renderBindings(){
  const template=templateDraft.template;
  $("template-copy-name").textContent=`Copied ${template.name}, revision ${template.revision} · processing v${template.processing_version}`;
  $("template-binding-fields").innerHTML=template.fields.map(f=>`<div class="template-binding"><h3>${esc(f.output_name)} → ${esc(f.target_type)}</h3><p class="hint">Transforms: ${esc(f.transforms.join(" → ")||"none")}${f.max_length===null?"":` · Max length ${f.max_length}`}</p><p data-binding-state></p><label>Bind ${esc(f.output_name)}<select data-binding-mode aria-label="Bind ${esc(f.output_name)}"><option value="">Unmapped — choose explicitly</option><option value="source">Source column</option><option value="text">Text literal</option><option value="null">NULL literal</option><option value="empty">Empty-string literal</option><option value="true">Boolean true literal</option><option value="false">Boolean false literal</option></select></label><label data-source-label hidden>Source column for ${esc(f.output_name)}<input data-binding-source list="template-source-columns"></label><label data-text-label hidden>Text literal for ${esc(f.output_name)}<textarea data-binding-text rows="2"></textarea></label>${f.lookup_required?`<label>Lookup rule for ${esc(f.output_name)}<textarea data-binding-lookup rows="4" placeholder='Existing lookup JSON: {"source": {...}, "column": "..."}'></textarea></label><p class="hint">Configure the existing lookup source and column explicitly. Connection references belong to this pipeline only.</p>`:""}</div>`).join("");
  $("template-bindings").hidden=false;updateBindingState();window.EditorControls?.templates();
}
$("template-refresh").onclick=()=>action(()=>listTemplates());
$("template-load").onclick=()=>action(async()=>{loadTemplateEditor(await templateApi("read",selectedTemplate()));notify("Revision loaded into the template editor. Pipelines are unchanged.");});
$("template-create").onclick=()=>action(async()=>{const value=await templateApi("create",{definition:blueprint()});loadTemplateEditor(value);await listTemplates(value);notify("Template created with immutable revision 1.");});
$("template-revise").onclick=()=>action(async()=>{if(!templateEditorRevision)throw new Error("Load a revision before saving a new revision.");const value=await templateApi("revision",{id:templateEditorRevision.id,base_revision:templateEditorRevision.revision,definition:blueprint()});loadTemplateEditor(value);await listTemplates(value);notify("New revision saved. Existing copied drafts, pipelines and runs are unchanged.");});
$("template-add-field").onclick=()=>{addTemplateField();dirty=true;};
$("template-fields").onclick=event=>{if(event.target.closest("[data-remove-target]")){event.target.closest(".template-field").remove();dirty=true;}};
$("template-apply").onclick=()=>action(async()=>{
  if(templateDraft)throw new Error("A copied template is already pending. Finish it or start a new draft.");
  const request={...selectedTemplate(),spec:read()};
  const result=await templateApi("apply",request);
  // Refuse a late response if the editor changed while the request was running.
  if(JSON.stringify(read())!==JSON.stringify(request.spec))throw new Error("Draft changed while applying; apply again.");
  templateDraft=result;renderBindings();dirty=true;notify("Template copied. Every source/literal and lookup binding remains explicit.");
});
$("template-read-columns").onclick=()=>action(async()=>{const selected=source();const result=await api("/api/columns",{source:selected});if(JSON.stringify(source())!==JSON.stringify(selected))throw new Error("Source changed; read columns again.");$("template-source-columns").innerHTML=result.columns.map(c=>`<option value="${esc(c)}">`).join("");notify("Source columns available. No bindings were selected.");});
$("template-binding-fields").oninput=()=>{updateBindingState();dirty=true;};
$("template-binding-fields").onchange=()=>{updateBindingState();dirty=true;};
$("template-generate").onclick=()=>action(async()=>{
  if(!templateDraft)return;
  const copy=templateDraft, spec=read({allowTemplateDraft:true}), bindings=bindingValues();
  const generated=await templateApi("bind",{template:copy.template,spec,bindings});
  if(templateDraft!==copy || JSON.stringify(read({allowTemplateDraft:true}))!==JSON.stringify(spec) || JSON.stringify(bindingValues())!==JSON.stringify(bindings))throw new Error("Bindings changed while generating; generate again.");
  clearTemplateDraft();definition=generated;render();dirty=true;notify("Mappings generated. The pipeline is independent of the template; review with Processed Preview.");
});
document.addEventListener("input",event=>{if(discoveryInputs.has(event.target.id))$("template-source-columns").replaceChildren();});
addTemplateField();
