// Split-by-value export, end to end. Start python -m tests.browser_fixture_server first.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
const URL=process.env.ETL_TEST_URL||"http://127.0.0.1:8768";

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.goto(URL);
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));

    // No split by default, and the option list mirrors the mapped output fields.
    assert.equal(await page.locator("#split-by").inputValue(),"");
    const options=await page.locator("#split-by option").allTextContents();
    assert.ok(options.includes("country"),"the mapped output field is offered");

    await page.locator("#name").fill("Split export "+Date.now());
    await page.locator("#split-by").selectOption("country");
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:/saved as a new entry/}).waitFor();
    await page.getByRole("button",{name:"Run & export →",exact:true}).click();
    await page.locator("#runs .completed").first().waitFor();

    // Two distinct countries in the fixture (GR, CY) collapse the single
    // "Download CSV" link into two named, individually clickable ones.
    const run=page.locator(".run").first();
    await run.getByText("GR",{exact:true}).waitFor();
    await run.getByText("CY",{exact:true}).waitFor();
    assert.equal(await run.locator(".split-downloads").count(),0,"two files show inline, not behind a disclosure");
    const downloadPromise=page.waitForEvent("download");
    await run.getByText("GR",{exact:true}).click();
    const download=await downloadPromise;
    assert.match(download.suggestedFilename(),/^valid/);

    // Reopening a saved split pipeline restores the chosen field.
    await page.locator("#new").click();
    await page.locator("[data-pipeline]").first().click();
    await page.waitForFunction(()=>document.getElementById("split-by").value!=="");
    assert.equal(await page.locator("#split-by").inputValue(),"country");

    assert.deepEqual(errors,[]);
    console.log("Split export passed: field list, save, run, per-group downloads, reload restores the choice.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
