// Output encoding: an explicit UI control for the BOM/no-BOM choice, not
// buried in Advanced JSON. Start python -m tests.browser_fixture_server first.
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

    // Flip the shipped v1 demo to v2, keeping its already-valid source/columns.
    await page.evaluate(()=>openDefinition({...read(),version:2}));
    assert.equal(await page.locator("#output-encoding").inputValue(),"utf-8-sig","default keeps the BOM, for Windows/Excel readers");

    await page.locator("#output-encoding").selectOption("utf-8");
    await page.locator("#name").fill("Encoding test "+Date.now());
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"saved as a new entry"}).waitFor();
    assert.equal(await page.evaluate(()=>definition.destination.encoding),"utf-8");

    // Reloading the saved pipeline must show the choice that was actually saved.
    const name=await page.evaluate(()=>definition.name);
    await page.reload();
    await page.waitForFunction(()=>saved.length>0);
    await page.evaluate(n=>{const item=saved.find(p=>p.name===n);openDefinition(item.spec,item.id);}, name);
    assert.equal(await page.locator("#output-encoding").inputValue(),"utf-8","selection persists across reload");

    // v1 has no encoding concept at all; the server refuses the key outright.
    await page.evaluate(()=>openDefinition({...read(),version:1}));
    const v1spec=await page.evaluate(()=>read());
    assert.equal("encoding" in v1spec.destination,false,"v1 destination must never carry encoding");

    assert.deepEqual(errors,[]);
    console.log("Output encoding UI passed: v2 default keeps BOM, no-BOM choice persists, v1 omits the key entirely.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
