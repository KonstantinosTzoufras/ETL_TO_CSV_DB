const {chromium}=require(process.env.ETL_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],prompts=[];
  let accept=true;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>{prompts.push(d.message());return accept?d.accept():d.dismiss();});
  await page.route('**/api/bootstrap',async route=>{
   const response=await route.fetch(),body=await response.json();
   body.example.columns=Array.from({length:120},(_,i)=>({name:`Field${i}`,literal:i===119?42:null,type:i===119?'int':'string',transforms:['lower']}));
   await route.fulfill({response,json:body});
  });
  await page.goto(process.env.ETL_TEST_URL||'http://127.0.0.1:8768');
  await page.waitForFunction(()=>document.querySelectorAll('[data-bulk-select]').length===120);
  const rows=page.locator('#columns tr'), chain=page.locator('#bulk-transforms .transform-control');
  const values=()=>page.locator('#columns [data-field=transforms]').evaluateAll(inputs=>inputs.map(e=>e.value));
  await page.locator('#bulk-text').click();
  assert.equal(await page.locator('#bulk-count').innerText(),'119 fields selected');
  for(const name of ['trim','empty_to_null']){
   await chain.locator('select').selectOption(name);
   await chain.getByRole('button',{name:'Add transform',exact:true}).click();
  }
  assert.equal(await page.evaluate(()=>dirty),false,'Selection and composing bulk rules do not edit mappings');
  await page.locator('#bulk-append').click();
  assert.match(prompts.at(-1),/119 fields/);
  let actual=await values();assert.ok(actual.slice(0,119).every(v=>v==='lower, trim, empty_to_null'));assert.equal(actual[119],'lower');
  // Undo leaves unrelated edits intact and preserves typed literals.
  await rows.first().locator('[data-field=name]').fill('Renamed');
  await page.locator('#bulk-undo').click();assert.ok((await values()).every(v=>v==='lower'));
  assert.equal(await rows.first().locator('[data-field=name]').inputValue(),'Renamed');
  await page.locator('#bulk-all').click();
  await chain.getByRole('button',{name:'Move transform 2 earlier',exact:true}).click();
  await page.locator('#bulk-replace').click();assert.ok((await values()).every(v=>v==='empty_to_null, trim'));
  assert.equal(await page.evaluate(()=>read().columns[0].literal),null);
  assert.equal(await page.evaluate(()=>read().columns[119].literal),42);
  accept=false;await page.locator('#bulk-clear').click();assert.ok((await values()).every(v=>v==='empty_to_null, trim'));accept=true;
  await page.locator('#bulk-clear').click();assert.ok((await values()).every(v=>v===''));
  await page.locator('#bulk-undo').click();assert.ok((await values()).every(v=>v==='empty_to_null, trim'));
  await page.locator('#bulk-none').click();assert.equal(await page.locator('#bulk-append').isDisabled(),true);
  await rows.first().locator('[data-bulk-select]').check();
  await page.locator('#bulk-clear').click();actual=await values();assert.equal(actual[0],'');assert.equal(actual[1],'empty_to_null, trim');
  await rows.first().locator('.transform-control summary').click();
  await rows.first().getByRole('button',{name:'Add transform',exact:true}).click();
  assert.equal(await page.locator('#bulk-undo').isDisabled(),true,'Individual transform edit invalidates bulk undo');
  await page.locator('#bulk-clear').click();
  await page.locator('#save').click();
  await page.waitForFunction(()=>!busy);
  assert.doesNotMatch(await page.locator('#notice').innerText(),/error|failed/i);
  await page.locator('#add').click();
  assert.equal(await page.locator('#bulk-undo').isDisabled(),true,'Changing field structure invalidates undo');
  assert.equal(await page.locator('#bulk-clear').isDisabled(),true,'Changing field structure resets selection');
  await page.locator('#bulk-text').click();
  accept=false;await page.locator('#bulk-remove').click();
  assert.equal(await rows.count(),121,'Cancelled bulk removal preserves fields');accept=true;
  await page.locator('#bulk-remove').click();
  assert.equal(await rows.count(),1,'Remove selected leaves unselected integer field');
  assert.equal(await page.evaluate(()=>read().columns[0].literal),42);
  assert.equal(await page.locator('#bulk-remove').isDisabled(),true);
  await page.locator('#bulk-all').click();await page.locator('#bulk-none').click();
  assert.equal(await page.locator('#bulk-remove').isDisabled(),true,'Deselect all does not remove fields');
  await page.locator('#bulk-all').click();await page.locator('#bulk-remove').click();
  assert.equal(await rows.count(),0,'Bulk removal can clear all mappings');
  await page.locator('#new').click();
  assert.equal(await page.locator('#bulk-undo').isDisabled(),true);
  assert.equal(await page.locator('#bulk-count').innerText(),'0 fields selected');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log('Bulk transforms passed: 120 fields, explicit selection, ordered append/replace/clear, cancel, undo, typed literals, save, draft reset, mobile.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
