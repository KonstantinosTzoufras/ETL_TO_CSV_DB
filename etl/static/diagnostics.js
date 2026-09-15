"use strict";
// Presentation only. All stages and error classifications come from the API.
globalThis.Diagnostics = (()=>{
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function cell(value) {
    if(!value.available)return `<span class="diagnostic-missing">${escape(value.label)}</span>`;
    return `<span class="diagnostic-type">${escape(value.label)}</span><pre>${escape(value.text)}</pre>${value.type==="string"?`<details class="escaped-value"><summary>Escaped text</summary><pre>${escape(value.escaped)}</pre></details>`:""}`;
  }
  function field(item) {
    const errors=item.errors.map(e=>`<li><strong>${escape(e.stage??"Stage not recorded")}</strong> · <code>${escape(e.code??"Code not recorded")}</code><p>${escape(e.message)}</p></li>`).join("");
    return `<details class="diagnostic-field" ${item.errors.length?"open":""}><summary>${escape(item.name)} — ${escape(item.states.join(" · ")||"Legacy stages not recorded")}${item.errors.length?` · ${item.errors.length} errors`:""}</summary><p class="hint">${escape(item.origin)}</p><div class="table-scroll"><table><thead><tr><th>Original value</th><th>Transformed value</th><th>Converted value</th>${item.legacy_value?"<th>Legacy mapped value (stage unknown)</th>":""}</tr></thead><tbody><tr><td>${cell(item.original)}</td><td>${cell(item.transformed)}</td><td>${cell(item.converted)}</td>${item.legacy_value?`<td>${cell(item.legacy_value)}</td>`:""}</tr></tbody></table></div>${errors?`<ul class="diagnostic-errors">${errors}</ul>`:'<p class="hint">No field errors recorded.</p>'}</details>`;
  }
  function rows(items) {
    return items.map(row=>{
      const position=row.source_position;
      const location=position.line_start!=null?` · CSV lines ${position.line_start}–${position.line_end}`:row.source_kind==="sqlserver"?" · SQL execution-relative position; order not guaranteed":" · Physical lines not recorded";
      return `<details class="diagnostic-row"><summary><span class="${row.valid?"valid":"invalid"}">Record ${escape(row.record)} — ${escape(row.summary)}</span></summary><p class="hint">Source record ${escape(row.record)}${escape(location)}</p>${row.legacy?'<p class="diagnostic-missing">Historical v1 diagnostics did not record separate transformed/converted stages or error codes/stages. Missing evidence is not reconstructed.</p>':""}${row.fields.map(field).join("")}</details>`;
    }).join("")||'<p class="muted">No rows to display.</p>';
  }
  return {cell, rows};
})();
if(typeof module!=="undefined")module.exports=globalThis.Diagnostics;
