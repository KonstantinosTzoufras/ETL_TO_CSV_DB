// Database-table destination: UI toggling and the save-before-run gate.
// No real SQL Server here - that needs the user's explicit approval to test
// against a live database. This proves the UI wiring and the client-side
// guard only.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
const URL=process.env.ETL_TEST_URL||"http://127.0.0.1:8768";

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));page.on("dialog",d=>d.accept());
    await page.goto(URL);
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));

    // CSV-only controls are visible by default; the DB ones are not.
    assert.equal(await page.locator("#output-delimiter").isVisible(),true);
    assert.equal(await page.locator("#export-connection-field").isVisible(),false);

    await page.locator("#format").selectOption("sqlserver");
    assert.equal(await page.locator("#output-delimiter").isVisible(),false);
    assert.equal(await page.locator("#split-by").isVisible(),false);
    assert.equal(await page.locator("#export-connection-field").isVisible(),true);

    // No ETL_EXPORT_CONNECTIONS is set for this fixture: the list comes back empty.
    await page.getByRole("button",{name:"Refresh export connections",exact:true}).click();
    await page.getByRole("status").filter({hasText:"No database connections are approved"}).waitFor();

    // Running an unsaved pipeline against a database destination is refused
    // client-side, before any request reaches the server - the run count
    // (shared with other tests on this fixture server) must not move at all.
    const before=await page.locator("#runs .run").count();
    await page.locator("#name").fill("DB export "+Date.now());
    await page.getByRole("button",{name:"Run & export →",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Save this pipeline"}).waitFor();
    await page.waitForTimeout(300);
    assert.equal(await page.locator("#runs .run").count(),before,"no run request must have reached the server");

    // Switching back to CSV restores the normal controls.
    await page.locator("#format").selectOption("csv");
    assert.equal(await page.locator("#output-delimiter").isVisible(),true);
    assert.equal(await page.locator("#export-connection-field").isVisible(),false);

    assert.deepEqual(errors,[]);
    console.log("Database export UI passed: field toggling, empty approval list, save-before-run gate, format switch-back.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
