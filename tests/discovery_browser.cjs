// Optional UI acceptance, against an isolated temporary Application at ETL_TEST_URL.
// ETL_PLAYWRIGHT may point at an existing Playwright installation.
const {chromium} = require(process.env.ETL_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.goto(process.env.ETL_TEST_URL || "http://127.0.0.1:8768");
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    const mappingBefore=await page.locator("#columns").innerHTML();
    await page.getByText("Discover a source",{exact:true}).click();
    await page.getByRole("button",{name:"Browse datasets",exact:true}).click();
    await page.getByRole("button",{name:"examples /",exact:true}).click();
    await page.getByRole("button",{name:"customers.csv (file)",exact:true}).click();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).waitFor();
    assert.match(await page.locator("#discovery-metadata").innerText(), /Unknown/);
    await page.getByLabel("Source sample limit (1–100)").fill("2");
    await page.getByRole("button",{name:"Read Source Sample",exact:true}).click();
    await page.getByRole("heading",{name:"Source Sample",exact:true}).waitFor();
    assert.equal(await page.locator("#discovery-sample-table tbody tr").count(),2);
    assert.match(await page.locator("#discovery-sample-status").innerText(), /limit reached/);
    assert.equal(await page.locator("#columns").innerHTML(),mappingBefore);
    assert.equal(await page.locator("#results").isVisible(),false);
    await page.getByRole("button",{name:"Use this dataset",exact:true}).click();
    assert.equal(await page.locator("#columns").innerHTML(),mappingBefore);
    assert.equal(await page.locator("#discovery-sample").isVisible(),false);
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    await page.getByRole("heading",{name:"Processed Preview",exact:true}).waitFor();
    assert.match(await page.locator("#counts").innerText(), /5[\s\S]*3[\s\S]*2/);

    // A source-only draft must remain unmapped; discovery cannot save or run it.
    page.on("dialog",dialog=>dialog.accept());
    await page.locator("#new").click();
    await page.getByLabel("File path inside workspace").fill("examples/customers.csv");
    await page.getByRole("button",{name:"Inspect CSV path",exact:true}).click();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).waitFor();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).click();
    assert.equal(await page.locator("#columns tr").count(),0);
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Choose 1–256 output columns"}).waitFor();

    // A sample response arriving after read options changed must be discarded.
    await page.getByRole("button",{name:"Inspect CSV path",exact:true}).click();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).waitFor();
    let release, started;
    const waiting=new Promise(resolve=>started=resolve);
    const gate=new Promise(resolve=>release=resolve);
    await page.route("**/api/discovery/sample",async route=>{const response=await route.fetch();started();await gate;await route.fulfill({response});});
    await page.getByRole("button",{name:"Read Source Sample",exact:true}).click();
    await waiting;
    await page.getByLabel("Encoding",{exact:true}).fill("utf-8");
    release();
    await page.waitForFunction(()=>!document.querySelector("#discover-browse").disabled);
    assert.equal(await page.locator("#discovery-sample").isVisible(),false);
    assert.equal(await page.locator("#discovery-selection").isVisible(),false);
    await page.unroute("**/api/discovery/sample");

    // Errors and mobile layout use the same discovery path.
    await page.getByLabel("File path inside workspace").fill("../outside.csv");
    await page.getByRole("button",{name:"Inspect CSV path",exact:true}).click();
    await page.getByRole("status").filter({hasText:"inside the workspace"}).waitFor();
    await page.getByLabel("File path inside workspace").fill("examples/customers.csv");
    await page.getByRole("button",{name:"Inspect CSV path",exact:true}).click();
    await page.getByRole("button",{name:"Read Source Sample",exact:true}).click();
    await page.getByRole("heading",{name:"Source Sample",exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,"Mobile overflow");
    assert.deepEqual(errors,[]);
    console.log("Discovery UI acceptance passed: browse, inspect, sample, source selection, unchanged/empty mappings, processed preview, stale response, errors, mobile.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
