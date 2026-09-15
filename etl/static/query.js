"use strict";
function querySource() {
  let parameters;
  try { parameters=JSON.parse($("query-parameters").value); }
  catch { throw new Error("Parameters must be valid JSON; values were not submitted."); }
  return {kind:"sqlserver_query",connection_env:$("query-connection").value,query:{format_version:1,dialect:"tsql",sql:$("query-sql").dataset.edited?$("query-sql").value:($("query-sql").originalSql??$("query-sql").value),parameters,timeout_seconds:Number($("query-timeout").value)}};
}
function renderQuery(source) {
  const query=source.kind==="sqlserver_query"?source.query:null;
  $("query-sql").originalSql=query?.sql||"";
  $("query-sql").dataset.edited="";
  $("query-sql").value=query?.sql||"";
  $("query-parameters").value=JSON.stringify(query?.parameters||[],null,2);
  $("query-timeout").value=query?.timeout_seconds??60;
  if(source.kind==="sqlserver_query") {
    const reference=source.connection_env;
    if(![...$("query-connection").options].some(o=>o.value===reference))$("query-connection").add(new Option(reference+" (approval checked on use)",reference));
    $("query-connection").value=reference;
  }
}
$("query-sql").oninput=()=>{$("query-sql").dataset.edited="true";};
$("query-connections").onclick=()=>action(async()=>{
  const selected=$("query-connection").value;
  const result=await api("/api/query/connections",{});
  $("query-connection").replaceChildren(new Option("Select an approved connection",""));
  result.connections.forEach(name=>$("query-connection").add(new Option(name,name)));
  if(result.connections.includes(selected))$("query-connection").value=selected;
  resetDiscovery();
  notify(result.connections.length?"Approved references loaded. Select one explicitly.":"No query connections approved. An administrator must configure a read-only account first.");
});
$("query-validate").onclick=()=>action(async()=>{
  await api("/api/query/validate",{query:querySource().query});
  notify("Query syntax and parameters validated locally. Database access has not been attempted.");
});
$("query-inspect").onclick=()=>action(async()=>{
  const generation=++discoveryGeneration;
  discoverySource=null; $("discovery-selection").hidden=true; $("discovery-sample").hidden=true;
  const candidate=querySource();
  const configured=await discoveryRequest("configure",{dataset_key:"sqlserver_query:"+candidate.connection_env,options:{query:candidate.query}});
  if(generation!==discoveryGeneration)return;
  const columns=await discoveryRequest("columns",{source:configured});
  if(generation!==discoveryGeneration)return;
  discoverySource=configured;
  $("discovery").open=true; $("discovery-name").textContent="SQL Query";
  const known=value=>value===null?"Unknown":String(value);
  $("discovery-metadata").innerHTML=`<table><thead><tr><th>Position</th><th>Name</th><th>Read type</th><th>Nullable</th><th>Precision</th><th>Scale</th></tr></thead><tbody>${columns.map((m,i)=>`<tr>${[i+1,m.column.name,m.column.native_type,m.column.nullable,m.column.precision,m.column.scale].map(v=>`<td>${esc(known(v))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  $("discovery-selection").hidden=false;
  $("available-columns").innerHTML=columns.map(m=>`<option value="${esc(m.column.name)}">`).join("");
  notify("Query columns inspected. No mappings created; use the dataset and bind fields explicitly.");
});
