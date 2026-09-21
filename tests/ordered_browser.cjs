const {chromium}=require(process.env.ETL_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.setDefaultTimeout(10000);page.on('pageerror',e=>{errors.push(e.message);console.error(e.message);});page.on('dialog',d=>d.accept());
  await page.goto(process.env.ETL_TEST_URL||'http://127.0.0.1:8769');
  await page.waitForFunction(()=>document.querySelector('#name').value.includes('Customers'));
  await page.getByText('Advanced exports',{exact:true}).click();
  await page.locator('#new-ordered').click();
  assert.equal(await page.locator('#ordered-policy').inputValue(),'stop');
  assert.equal(await page.evaluate(()=>dirty),false,'Opening ordered drafts must not trigger connection edits');
  await page.evaluate(()=>window.EditorControls.sync());
  assert.equal(await page.evaluate(()=>dirty),false,'Select2 refresh must not capture or edit ordered steps');
  await page.locator('#ordered-name').fill('Browser ordered acceptance');
  await page.locator('#ordered-connections').click();
  await page.locator('#notice').filter({hasText:'Shared connection references loaded'}).waitFor();
  await page.locator('#ordered-connection + .select2 .select2-selection').click();
  await page.locator('.select2-results__option').getByText('ETL_SQL_QUERY_TEST',{exact:true}).click();
  assert.equal(await page.locator('#query-connection').inputValue(),'ETL_SQL_QUERY_TEST');
  assert.equal(await page.locator('#query-connection').isDisabled(),true);
  assert.equal(await page.locator('#source-kind').isDisabled(),true);
  async function fillStep(id,name,column,type='string'){
   await page.locator('#ordered-step-id').fill(id);await page.locator('#name').fill(name);
   await page.locator('#query-sql').fill('SELECT Code, Name, Amount FROM dbo.BRANDS');
   await page.locator('#add').click();
   await page.getByLabel('Field 1 name',{exact:true}).fill('Code');
   if(column==='MissingColumn'){
   // Deliberately invalid historical binding for failure-policy acceptance.
   await page.getByLabel('Field 1 value',{exact:true}).evaluate((e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));},column);
  } else {await page.locator('#columns tr').first().locator('.source-select + .select2 .select2-selection').click();
  await page.locator('.select2-results__option').getByText(column,{exact:true}).click();}
   await page.getByLabel('Field 1 type',{exact:true}).selectOption(type);
  }
  await fillStep('customers','Customers','Code','int');
  await page.locator('#ordered-add').click();
  await fillStep('orders','Orders','MissingColumn');
  await page.locator('#ordered-add').click();
  await fillStep('balances','Balances','Amount','decimal');
  await page.locator('#ordered-up').click();
  assert.match(await page.locator('#ordered-steps').innerText(),/1\. Customers[\s\S]*2\. Balances[\s\S]*3\. Orders/);
  await page.locator('#ordered-down').click();
  await page.locator('[data-ordered-index="0"]').click();
  await page.locator('#preview').click();
  await page.locator('#notice').filter({hasText:'Preview complete'}).waitFor();
  assert.match(await page.locator('#counts').innerText(),/25/);
  assert.equal(await page.locator('.ordered-run').count(),0);
  await page.locator('#save').click();await page.locator('#notice').filter({hasText:/Pipeline (saved|updated)/}).waitFor();
  await page.locator('#run').click();
  await page.waitForFunction(()=>document.querySelector('.ordered-run')?.textContent.includes('skipped'));
  let card=page.locator('.ordered-run');assert.match(await card.innerText(),/failed/);
  assert.equal(await card.locator('a', {hasText:'Download customers.csv'}).count(),1);
  assert.equal(await card.locator('a', {hasText:'Download orders.csv'}).count(),0);
  await card.locator('[data-diagnostics][data-step="customers"]').click();
  await page.locator('#run-diagnostics').waitFor({state:'visible'});
  assert.equal(await page.locator('#run-diagnostics-rows > .diagnostic-row').count(),20);
  await page.locator('#run-diagnostics-next').click();
  await page.waitForFunction(()=>document.querySelectorAll('#run-diagnostics-rows > .diagnostic-row').length===5);
  await page.locator('#run-diagnostics-close').click();
  await page.locator('#ordered-policy').selectOption('continue');
  await page.locator('#run').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.ordered-run')].some(e=>e.textContent.includes('completed_with_errors')));
  card=page.locator('.ordered-run').filter({hasText:'completed_with_errors'});
  assert.equal(await card.locator('a', {hasText:'Download balances.csv'}).count(),1);
  // The stored stop policy and all step definitions survive unsaved edits.
  await page.locator('#pipelines [data-pipeline]').first().click();
  assert.equal(await page.locator('#ordered-policy').inputValue(),'stop');
  assert.equal(await page.locator('[data-ordered-index]').count(),3);
  await page.locator('[data-ordered-index="2"]').click();
  assert.equal(await page.getByLabel('Field 1 value',{exact:true}).inputValue(),'Amount');
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2));
  await page.locator('#new').click();
  assert.equal(await page.locator('#ordered-editor').isVisible(),false);
  assert.equal(await page.locator('#source-kind').isDisabled(),false);
  assert.deepEqual(errors,[]);
  console.log('Ordered browser acceptance passed: shared connection, steps, explicit mappings, reorder, selected preview, stop/continue, completed downloads, diagnostics pagination, snapshot reload, single-mode return, mobile.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
