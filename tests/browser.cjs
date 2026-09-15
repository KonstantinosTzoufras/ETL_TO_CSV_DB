// Optional smoke test: npm install --no-save playwright, then node tests/browser.cjs.
// Uses the synthetic bundled demo and saves a demo pipeline in the local app.
const { chromium } = require(process.env.ETL_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto("http://127.0.0.1:8765");
    await page.getByLabel("Pipeline name",{exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    await page.getByRole("button",{name:"Read source columns",exact:true}).click();
    await page.getByRole("status").filter({hasText:"5 source columns"}).waitFor();
    await page.getByRole("button",{name:"Preview 100 rows",exact:true}).click();
    await page.getByRole("heading",{name:"Preview results",exact:true}).waitFor();
    assert.match(await page.locator("#counts").innerText(),/5[\s\S]*3[\s\S]*2/);
    assert.match(await page.locator("#preview-table").innerText(),/required value is missing/);
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Pipeline saved"}).waitFor();
    await page.getByRole("button",{name:"Run & export →",exact:true}).click();
    await page.locator("#runs .completed").first().waitFor();
    const downloadPromise=page.waitForEvent("download");
    await page.locator("#runs").getByRole("link",{name:"Download CSV",exact:true}).first().click();
    const download=await downloadPromise;
    assert.equal(download.suggestedFilename(),"valid.csv");
    assert.match(await fs.readFile(await download.path(),"utf8"),/Maria Papadopoulou/);
    await page.getByLabel("Field 1 name",{exact:true}).fill("full_name");
    await page.getByRole("button",{name:"Preview 100 rows",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Duplicate output name"}).waitFor();
    await page.getByLabel("Field 1 name",{exact:true}).fill("customer_id");
    await page.getByRole("button",{name:"Preview 100 rows",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Preview complete"}).waitFor();
    await fs.mkdir("data/qa",{recursive:true});
    await page.screenshot({path:"data/qa/studio-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:"data/qa/studio-mobile.png",fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,"Mobile page overflows horizontally");
    assert.deepEqual(errors,[]);
    console.log("Browser smoke test passed: inspect, preview, save, run, download, validation, mobile layout.");
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
