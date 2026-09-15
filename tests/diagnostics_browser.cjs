// Run against an isolated temporary Application. Never use production history.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on("pageerror",e=>errors.push(e.message));
    await page.goto(process.env.ETL_TEST_URL||"http://127.0.0.1:8768");
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    await page.locator("#preview-table .diagnostic-row").first().waitFor();
    assert.equal(await page.locator("#preview-table .diagnostic-row").count(),5);
    const valid=page.locator("#preview-table .diagnostic-row").filter({hasText:/Record \d+ — Valid/}).first();
    await valid.locator(":scope > summary").click();
    await valid.locator(".diagnostic-field > summary").first().click();
    assert.match(await valid.innerText(),/Original value[\s\S]*Transformed value[\s\S]*Converted value/);
    const rejected=page.locator("#preview-table .diagnostic-row").filter({hasText:"Rejected:"}).first();
    await rejected.locator(":scope > summary").click();
    assert.match(await rejected.innerText(),/required|max_length|conversion/);

    // Existing v1 history has only legacy evidence; the new preview still has all stages.
    await page.getByRole("button",{name:"Run & export →",exact:true}).click();
    await page.locator("#runs .completed").first().waitFor();
    await page.getByRole("button",{name:"View rejection diagnostics",exact:true}).click();
    await page.locator("#run-diagnostics .diagnostic-row").first().waitFor();
    await page.locator("#run-diagnostics .diagnostic-row > summary").first().click();
    assert.match(await page.locator("#run-diagnostics").innerText(),/Historical v1/);
    assert.match(await page.locator("#run-diagnostics").innerText(),/Legacy mapped value \(stage unknown\)/);
    await page.getByRole("button",{name:"Close diagnostics",exact:true}).click();

    const spec={version:2,name:"Viewer exact values",source:{kind:"csv",path:"diagnostics.csv",delimiter:";"},destination:{kind:"csv"},columns:[
      {name:"identifier",literal:" 003 ",type:"string",transforms:["trim"]},
      {name:"bad",literal:"003",type:"int",max_length:2},
      {name:"null",literal:null},{name:"empty",literal:""},{name:"whitespace",literal:" \t "},
      {name:"multiline",source:"note"},
      {name:"decimal",literal:"12345678901234567890.4500",type:"decimal"},
      {name:"date",literal:"2024-02-29",type:"date"},
      {name:"datetime",literal:"2024-02-29T12:30:01",type:"datetime"},
      {name:"large",literal:"9223372036854775807",type:"int"}
    ]};
    await page.locator("#advanced > summary").click();
    await page.getByLabel("Pipeline JSON").fill(JSON.stringify(spec));
    await page.getByRole("button",{name:"Apply definition",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Definition applied"}).waitFor();
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    const row=page.locator("#preview-table .diagnostic-row").first();
    await row.locator(":scope > summary").click();
    for(const detail of await row.locator(".diagnostic-field").all())if(await detail.getAttribute("open")===null)await detail.locator(":scope > summary").click();
    const text=await row.innerText();
    for(const expected of ["Rejected: 1 fields, 2 errors","NULL","Empty string (length 0)","Whitespace-only string (length 3)","12345678901234567890.4500","2024-02-29","2024-02-29T12:30:01","9223372036854775807","Transformed value differs","Conversion failed — no converted value","max_length_exceeded","invalid_type","Ελλάδα"])
      assert.ok(text.includes(expected),expected);
    const multiline=row.locator(".diagnostic-field").filter({has:page.locator("summary").filter({hasText:/^multiline —/})});
    // textContent is used here because rendered innerText normalizes CRLF for layout.
    assert.ok((await multiline.locator("td pre").first().textContent()).includes("Ελλάδα\nsecond line"));

    await page.getByRole("button",{name:"Run & export →",exact:true}).click();
    const run=page.locator("#runs .run").filter({hasText:"Viewer exact values"});
    await run.locator(".completed").waitFor();
    await page.getByLabel("Pipeline name",{exact:true}).fill("Edited after run");
    await page.getByLabel("Field 1 name",{exact:true}).fill("edited_field");
    const sourceRequests=[];
    page.on("request",request=>{if(/\/api\/(preview|discovery|columns)/.test(request.url()))sourceRequests.push(request.url());});
    await run.getByRole("button",{name:"View rejection diagnostics",exact:true}).click();
    const historical=page.locator("#run-diagnostics .diagnostic-row").first();
    await historical.locator(":scope > summary").click();
    for(const detail of await historical.locator(".diagnostic-field").all())if(await detail.getAttribute("open")===null)await detail.locator(":scope > summary").click();
    const stored=await historical.innerText();
    assert.ok(stored.includes("identifier"));assert.ok(!stored.includes("edited_field"));
    assert.ok(stored.includes("12345678901234567890.4500"));assert.ok(stored.includes("9223372036854775807"));
    assert.equal(await page.locator("#run-diagnostics .diagnostic-row").count(),20);
    await page.getByRole("button",{name:"Next rejected rows",exact:true}).click();
    await page.locator("#run-diagnostics .diagnostic-row > summary").filter({hasText:"Record 21"}).waitFor();
    assert.equal(await page.locator("#run-diagnostics .diagnostic-row").count(),5);
    assert.equal(await page.getByRole("button",{name:"Next rejected rows",exact:true}).isVisible(),false);
    assert.deepEqual(sourceRequests,[]);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,"Mobile overflow");
    assert.deepEqual(errors,[]);
    console.log("Diagnostics browser acceptance passed: valid/rejected expansion, exact values, stage failures, positions, v1 history, v2 snapshot history, no source reread, mobile.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
