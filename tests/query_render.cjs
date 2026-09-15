// Preserve untouched SQL literals across textarea CRLF normalization.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('query editor preserves exact SQL and typed parameter snapshot until explicitly edited',()=>{
 const elements=new Map();
 const $=id=>{
  if(!elements.has(id))elements.set(id,{dataset:{},options:[{value:'ETL_SQL_TEST'}],_value:'',
    set value(v){this._value=String(v).replace(/\r\n?/g,'\n');},get value(){return this._value;}});
  return elements.get(id);
 };
 const source={kind:'sqlserver_query',connection_env:'ETL_SQL_TEST',query:{format_version:1,dialect:'tsql',sql:"SELECT 'line one\r\nline two' AS Note, ? AS Amount, ? AS Missing, ? AS Empty;\r\n-- end",parameters:[{name:'p',type:'decimal',value:'123.4500'},{name:'p',type:'string',value:null},{name:'p',type:'string',value:''}],timeout_seconds:60}};
 const context=vm.createContext({$,source});
 vm.runInContext(fs.readFileSync('etl/static/query.js','utf8'),context);
 vm.runInContext('renderQuery(source); result=querySource();',context);
 assert.deepEqual(JSON.parse(JSON.stringify(context.result)),source);
 assert.notEqual($('query-sql').value,source.query.sql);
 $('query-sql').value='SELECT 1 AS changed';$('query-sql').oninput();
 vm.runInContext('result=querySource();',context);
 assert.equal(context.result.query.sql,'SELECT 1 AS changed');
});
