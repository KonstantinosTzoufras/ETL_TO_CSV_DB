const {chromium}=require(process.env.ETL_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
 try {
  const page=await browser.newPage(),errors=[];let accept=false;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>accept?d.accept():d.dismiss());
  await page.goto(process.env.ETL_TEST_URL||'http://127.0.0.1:8768');
  await page.waitForFunction(()=>document.querySelector('#name').value.includes('Customers'));
  await page.locator('#name').fill('Deletion acceptance');
  await page.locator('#save').click();
  await page.locator('#notice').filter({hasText:/Pipeline (saved|updated)/}).waitFor();
  const id=await page.evaluate(()=>pipelineId);
  await page.locator('#name').fill('Unsaved edit preserved');
  await page.locator(`[data-delete-pipeline="${id}"]`).click();
  assert.equal(await page.locator(`[data-pipeline="${id}"]`).count(),1);
  accept=true;await page.locator(`[data-delete-pipeline="${id}"]`).click();
  await page.locator('#notice').filter({hasText:'Saved pipeline deleted'}).waitFor();
  assert.equal(await page.locator(`[data-pipeline="${id}"]`).count(),0);
  assert.equal(await page.locator('#name').inputValue(),'Unsaved edit preserved');
  assert.equal(await page.evaluate(()=>pipelineId),null);
  assert.equal(await page.evaluate(()=>dirty),true);
  await page.locator('#save').click();
  await page.locator('#notice').filter({hasText:/Pipeline (saved|updated)/}).waitFor();
  assert.notEqual(await page.evaluate(()=>pipelineId),id);
  assert.deepEqual(errors,[]);
  console.log('Pipeline deletion UI passed: cancel, delete, preserve draft, save as new ID.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
