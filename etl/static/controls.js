"use strict";
// Local Select2 controls. Hidden original inputs remain the editor's value
// boundary; this layer does not process data or infer mappings.
window.EditorControls=(()=>{
  const jq=window.jQuery;
  let columnsRequest=null, catalogCache=new Map();
  const changed=input=>{input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}));};
  async function columnNames(){
    if(pickerColumns.length)return pickerColumns;
    if(!columnsRequest)columnsRequest=loadAvailableColumns().finally(()=>{columnsRequest=null;});
    if(!await columnsRequest)throw new Error("Source changed. Open the list again.");
    return pickerColumns;
  }
  async function catalog(kind){
    const connection=$("connection").value, schema=$("schema").value;
    if(!connection)throw new Error("Select a configured database connection first.");
    if(kind==="datasets"&&!schema)throw new Error("Select a schema first.");
    const key=JSON.stringify([kind,connection,kind==="datasets"?schema:""]);
    if(catalogCache.has(key))return catalogCache.get(key);
    const names=[];let cursor=null;
    for(let page=0;page<100;page++){
      const result=await api(`/api/discovery/${kind}`,{connector:"sqlserver",connection_env:connection,namespace:kind==="datasets"?[schema]:[],cursor});
      names.push(...result.items.map(item=>kind==="datasets"?item.name:item[item.length-1]));
      cursor=result.next_cursor;
      if(!cursor){catalogCache.set(key,names);return names;}
    }
    throw new Error("Catalog exceeds 10,000 entries. Use Browse datasets to page through it.");
  }
  function attachChoice(input,loadNames,label,onChoose){
    if(input.dataset.choiceBacked)return;
    const select=document.createElement("select");select.className="source-select";select.setAttribute("aria-label",label);
    select.add(new Option("", ""));if(input.value)select.add(new Option(input.value,input.value,true,true));
    input.after(select);input.dataset.choiceBacked="true";
    jq(select).select2({width:"100%",placeholder:"Choose…",minimumResultsForSearch:0,
      ajax:{delay:100,data:params=>({term:params.term||"",page:params.page||1}),
        transport(params,success,failure){
          let cancelled=false;const generation=discoveryGeneration;
          Promise.resolve().then(loadNames).then(names=>{
            if(cancelled||generation!==discoveryGeneration)return;
            const filtered=names.filter(name=>name.toLocaleLowerCase().includes(params.data.term.toLocaleLowerCase()));
            const start=(params.data.page-1)*100;
            success({results:filtered.slice(start,start+100).map(name=>({id:name,text:name})),pagination:{more:start+100<filtered.length}});
          }).catch(error=>{if(!cancelled){notify(error.message,true);failure();}});
          return {abort(){cancelled=true;}};
        }}
    }).on("select2:select",()=>{
      input.value=select.value;input.dataset.edited="true";changed(input);
      if(onChoose)onChoose();
    });
    jq(select).next('.select2').find('[role="combobox"]').attr('aria-label',label).removeAttr('aria-labelledby');
    input.addEventListener("input",()=>syncInput(input));
  }
  function syncInput(input){
    const select=input.nextElementSibling;if(!select?.classList.contains("source-select"))return;
    if(input.value && ![...select.options].some(o=>o.value===input.value))select.add(new Option(input.value,input.value));
    jq(select).val(input.value).trigger("change.select2");
  }
  function transformList(input){return input.value.split(",").map(x=>x.trim()).filter(Boolean);}
  function drawTransforms(input){
    const box=input.nextElementSibling, values=transformList(input);
    if(box.tagName==='DETAILS')box.querySelector('summary').textContent=values.length?values.join(' → '):'No transforms · Edit';
    box.querySelector(".transform-chain").innerHTML=values.map((value,index)=>`<span class="transform-chip"><span>${index+1}. ${esc(value)}</span><button type="button" data-transform-action="up" data-index="${index}" aria-label="Move transform ${index+1} earlier" ${index===0?"disabled":""}>↑</button><button type="button" data-transform-action="down" data-index="${index}" aria-label="Move transform ${index+1} later" ${index===values.length-1?"disabled":""}>↓</button><button type="button" data-transform-action="remove" data-index="${index}" aria-label="Remove transform ${index+1}">×</button></span>`).join("")||'<span class="hint">No transforms</span>';
  }
  function attachTransforms(input){
    if(input.dataset.transformBacked)return;
    input.dataset.transformBacked="true";
    const compact=!!input.closest('#columns');
    const box=document.createElement(compact?'details':'div');box.className="transform-control";
    box.innerHTML='<div class="transform-chain"></div><div class="transform-add"><select aria-label="Transform to add"><option value="trim">Trim edges</option><option value="empty_to_null">Empty string → NULL</option><option value="upper">Uppercase</option><option value="lower">Lowercase</option><option value="linebreaks_to_space">Line breaks → space</option></select><button type="button" data-transform-action="add">Add transform</button></div>';
    if(compact)box.prepend(document.createElement('summary'));
    input.after(box);drawTransforms(input);
    box.onclick=event=>{
      const button=event.target.closest("[data-transform-action]");if(!button)return;
      const values=transformList(input), index=Number(button.dataset.index), action=button.dataset.transformAction;
      if(action==="add")values.push(box.querySelector("select").value);
      if(action==="remove")values.splice(index,1);
      const next=index+(action==="up"?-1:1);
      if((action==="up"||action==="down")&&next>=0&&next<values.length)[values[index],values[next]]=[values[next],values[index]];
      input.value=values.join(", ");changed(input);drawTransforms(input);
    };
    input.addEventListener("input",()=>drawTransforms(input));
  }
  function mappingMode(row){
    const input=row.querySelector('[data-field="value"]'),literal=row.querySelector('[data-field="mode"]').value==="literal";
    input.classList.toggle("literal-value",literal);
    const select=input.nextElementSibling;
    if(select?.classList.contains("source-select")){select.hidden=literal;jq(select).next(".select2").toggle(!literal);syncInput(input);}
  }
  function mappings(){
    bulkUndo=null;$('bulk-status').textContent='';
    document.querySelectorAll('#columns tr').forEach((row,index)=>{
      row.classList.add('enhanced-row');
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.dataset.bulkSelect='true';checkbox.setAttribute('aria-label',`Select field ${index+1} for bulk actions`);
      if(!row.querySelector('[data-bulk-select]')){
        const name=row.querySelector('[data-field="name"]'),group=document.createElement('div');group.className='mapping-name';
        name.before(group);group.append(checkbox,name);
      }
      attachChoice(row.querySelector('[data-field="value"]'),columnNames,`Source column for field ${index+1}`);
      attachTransforms(row.querySelector('[data-field="transforms"]'));mappingMode(row);
    });
    updateBulk();
  }
  function destroyMappings(){jq('#columns .source-select').each(function(){if(jq(this).data('select2'))jq(this).select2('destroy');});}
  let bulkUndo=null;
  const bulkRows=()=>[...document.querySelectorAll('#columns tr')];
  const selectedRows=()=>bulkRows().filter(row=>row.querySelector('[data-bulk-select]')?.checked);
  function updateBulk(){
    const count=selectedRows().length;
    $('bulk-count').textContent=`${count} fields selected`;
    for(const id of ['bulk-append','bulk-replace','bulk-clear','bulk-remove'])$(id).disabled=!count;
    $('bulk-undo').disabled=!bulkUndo;
  }
  function initializeBulk(){
    attachTransforms($('bulk-chain'));
    $('bulk-remove').onclick=()=>action(async()=>{
      const selected=new Set(selectedRows());if(!selected.size)return;
      if(!confirm(`Remove ${selected.size} selected output fields and their configured rules? Unselected fields will stay unchanged.`))return;
      const rows=bulkRows();read();
      definition.columns=definition.columns.filter((column,index)=>!selected.has(rows[index]));
      renderColumns();dirty=true;notify(`Removed ${selected.size} output fields. Unselected mappings are unchanged.`);
    });
    for(const [id,select] of [['bulk-all',()=>true],['bulk-text',row=>row.querySelector('[data-field="type"]').value==='string'],['bulk-none',()=>false]]){
      $(id).onclick=()=>{bulkRows().forEach(row=>row.querySelector('[data-bulk-select]').checked=select(row));updateBulk();};
    }
    $('columns').addEventListener('change',event=>{if(event.target.dataset.bulkSelect)updateBulk();});
    $('columns').addEventListener('input',event=>{
      if(event.target.dataset.field==='transforms' && bulkUndo){bulkUndo=null;updateBulk();$('bulk-status').textContent='Undo cleared because transforms were edited individually.';}
    });
    for(const mode of ['append','replace','clear'])$('bulk-'+mode).onclick=()=>{
      const rows=selectedRows(),chain=transformList($('bulk-chain'));
      if(!rows.length)return;
      if(mode!=='clear'&&!chain.length){$('bulk-status').textContent='Add at least one transform first. Use Clear transforms to remove a chain.';return;}
      const summary=mode==='clear'?`Clear transforms from ${rows.length} fields?`:`${mode==='append'?'Append':'Replace with'} ${chain.join(' → ')} ${mode==='append'?'on':'for'} ${rows.length} fields?`;
      if(!confirm(summary))return;
      const edits=rows.map(row=>{
        const input=row.querySelector('[data-field="transforms"]'),before=input.value;
        const values=mode==='clear'?[]:mode==='append'?[...transformList(input),...chain]:chain;
        return {input,before,after:values.join(', ')};
      });
      edits.forEach(({input,after})=>{input.value=after;drawTransforms(input);});
      bulkUndo=edits;dirty=true;updateBulk();$('bulk-status').textContent=`Updated transforms on ${rows.length} fields. Undo is available until fields are reloaded or transforms are edited individually.`;
    };
    $('bulk-undo').onclick=()=>{
      if(!bulkUndo)return;
      const edits=bulkUndo;bulkUndo=null;
      edits.forEach(({input,before})=>{input.value=before;drawTransforms(input);});
      dirty=true;updateBulk();$('bulk-status').textContent=`Restored transforms on ${edits.length} fields.`;
    };
  }
  function templates(){
    document.querySelectorAll('[data-template="transforms"]').forEach(attachTransforms);
    document.querySelectorAll('[data-binding-source]').forEach(input=>attachChoice(input,columnNames,input.parentElement.firstChild.textContent.trim()));
  }
  function sync(){
    for(const id of ['schema','table'])syncInput($(id));
    for(const id of ['connection','query-connection','ordered-connection','template-select'])if(jq('#'+id).data('select2'))jq('#'+id).trigger('change.select2');
  }
  function initialize(){
    for(const id of ['connection','query-connection','ordered-connection','template-select']){
      const select=$(id);jq(select).select2({width:'100%'}).on('select2:select',()=>select.dispatchEvent(new Event('change',{bubbles:true})));
      jq(select).next('.select2').find('[role="combobox"]').attr('aria-label',select.getAttribute('aria-label')||select.closest('label').firstChild.textContent.trim()).removeAttr('aria-labelledby');
      new MutationObserver(()=>jq(select).trigger('change.select2')).observe(select,{childList:true,attributes:true,attributeFilter:['disabled']});
    }
    attachChoice($('schema'),()=>catalog('namespaces'),'Choose database schema',()=>{$('table').value='';changed($('table'));sync();});
    attachChoice($('table'),()=>catalog('datasets'),'Choose database table',()=>action(()=>loadAvailableColumns()));
    document.querySelectorAll('[data-section-link]').forEach(button=>button.onclick=()=>$(button.dataset.sectionLink).scrollIntoView({behavior:'smooth',block:'start'}));
    $('columns').addEventListener('change',event=>{if(event.target.dataset.field==='mode')mappingMode(event.target.closest('tr'));});
    initializeBulk();sync();mappings();templates();
  }
  document.addEventListener('DOMContentLoaded',initialize);
  return {mappings,destroyMappings,sync,templates,attachTransforms,refreshBulk:updateBulk};
})();
