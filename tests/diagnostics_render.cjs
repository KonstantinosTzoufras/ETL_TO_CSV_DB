// Pure rendering tests; no browser or processing implementation.
const {test}=require("node:test");
const assert=require("node:assert/strict");
const Diagnostics=require("../etl/static/diagnostics.js");
const cell=(type,text,label=type)=>({available:true,type,text,label,escaped:JSON.stringify(text)});
const field={name:"identifier",origin:"Source: code",original:cell("string"," 003 "),transformed:cell("string","003"),converted:cell("string","003"),states:["Transformed value differs","Successfully converted"],errors:[]};
const row={record:"7",source_position:{line_start:9,line_end:11},source_kind:"csv",valid:true,legacy:false,summary:"Valid",fields:[field]};
test("valid row, source positions, stage separation and changes",()=>{
  const html=Diagnostics.rows([row]);
  for(const value of ["Record 7", "Valid", "CSV lines 9–11", "Original value", "Transformed value", "Converted value", " 003 ", "Transformed value differs", "Successfully converted"])assert.ok(html.includes(value),value);
});
test("rejected multiple errors retain codes and stages without a fake converted value",()=>{
  const errors=[{stage:"max_length",code:"max_length_exceeded",message:"Too long"},{stage:"conversion",code:"invalid_type",message:"Invalid integer"}];
  const html=Diagnostics.rows([{...row,valid:false,summary:"Rejected: 1 fields, 2 errors",fields:[{...field,errors,states:["Conversion failure","Validation failure"],converted:{available:false,label:"Conversion failed — no converted value"}}]}]);
  for(const value of ["Rejected", "max_length_exceeded", "conversion", "invalid_type", "Too long", "Invalid integer", "Conversion failed — no converted value"])assert.ok(html.includes(value),value);
  assert.match(html,/diagnostic-field" open/);
});
test("NULL empty whitespace and exact multiline text",()=>{
  assert.match(Diagnostics.cell(cell("null","","NULL")),/NULL/);
  assert.match(Diagnostics.cell(cell("string","","Empty string (length 0)")),/Empty string/);
  assert.match(Diagnostics.cell(cell("string"," \t ","Whitespace-only string (length 3)")),/Whitespace-only/);
  assert.ok(Diagnostics.cell(cell("string","Ελλάδα\r\nnext")).includes("Ελλάδα\r\nnext"));
});
test("Decimal date datetime large integer are display text without numeric conversion",()=>{
  for(const [type,text] of [["decimal","12345678901234567890.4500"],["integer","9223372036854775807"],["date","2024-02-29"],["datetime","2024-02-29T12:30:01.123456"]])assert.ok(Diagnostics.cell(cell(type,text)).includes(text));
});
test("untrusted field values names and errors are escaped",()=>{
  const payload='<img src=x onerror="alert(1)">';
  const html=Diagnostics.rows([{...row,fields:[{...field,name:payload,original:cell("string",payload),errors:[{stage:payload,code:payload,message:payload}]}]}]);
  assert.ok(!html.includes("<img"));assert.ok(html.includes("&lt;img"));
});
test("legacy missing evidence and SQL positions are explicit",()=>{
  const html=Diagnostics.rows([{...row,legacy:true,source_kind:"sqlserver",source_position:{line_start:null,line_end:null},fields:[{...field,legacy_value:cell("string","003")}]}]);
  assert.ok(html.includes("Historical v1"));assert.ok(html.includes("Legacy mapped value (stage unknown)"));assert.ok(html.includes("SQL execution-relative"));
});
