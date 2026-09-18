// Isolated UI acceptance: start python -m tests.browser_fixture_server first.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));page.on("dialog",d=>d.accept());
    await page.goto(process.env.ETL_TEST_URL||"http://127.0.0.1:8768");
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    const originalMappingCount=await page.locator("#columns tr").count();
    await page.getByText("Reusable mapping templates",{exact:true}).click();
    // The step list names this panel too, so target the disclosure itself.
    await page.locator("#mapping-templates summary").filter({hasText:"Create or revise a template"}).click();
    await page.getByLabel("Template name",{exact:true}).fill("UI target template");
    await page.locator("#template-fields .template-field").first().getByLabel("Output name",{exact:true}).fill("note");
    await page.locator("#template-fields .template-field").first().getByLabel("Required",{exact:true}).check();
    await page.locator("#template-fields .template-field").first().getByLabel("Lookup required",{exact:true}).check();
    for(const name of ["Empty","Null","Literal"]){await page.getByRole("button",{name:"Add target field",exact:true}).click();await page.locator("#template-fields .template-field").last().getByLabel("Output name",{exact:true}).fill(name);}
    await page.getByRole("button",{name:"Create new template",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Template created"}).waitFor();
    const firstSelection=await page.locator("#template-select").inputValue();
    await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"no mappings"}).waitFor();
    assert.equal(await page.locator("#columns tr").count(),originalMappingCount);
    await page.locator("#new").click();
    assert.equal(await page.evaluate(()=>definition.version),2,'New pipelines use conservative v2');
    assert.equal(await page.evaluate(()=>definition.destination.null_value),undefined,'Keep the v2 NULL token default');
    // Deliberately create a legacy draft to exercise the version boundary.
    await page.evaluate(()=>openDefinition({...read(),version:1}));
    await page.getByLabel("File path inside workspace").fill("diagnostics.csv");
    await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"processing versions differ"}).waitFor();
    await page.locator("#new").click();
    await page.getByLabel("File path inside workspace").fill("diagnostics.csv");
    await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
    await page.getByRole("heading",{name:"Review mappings",exact:true}).waitFor();
    for(const mode of await page.locator("[data-binding-mode]").all())assert.equal(await mode.inputValue(),"");
    assert.match(await page.locator("#template-binding-status").innerText(),/4 targets still need mapping/);
    await page.getByRole("button",{name:"Load source columns",exact:true}).click();
    await page.getByRole("status").filter({hasText:"source columns loaded"}).waitFor();
    assert.equal(await page.getByLabel("Bind note",{exact:true}).inputValue(),"");
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Resolve template bindings"}).waitFor();
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    assert.equal(await page.locator("#columns tr").count(),0);
    // Apply stays disabled while anything is open; the row state names what.
    // The server-side refusals behind it are covered in tests/test_templates.py.
    assert.equal(await page.locator("#template-generate").isDisabled(),true,"unbound targets block Apply");
    await page.getByLabel("Bind note",{exact:true}).selectOption("source");
    await page.getByRole('combobox',{name:'Source column for note',exact:true}).click();
    await page.locator(".select2-results__option").getByText("note",{exact:true}).click();
    assert.equal(await page.locator("#template-generate").isDisabled(),true,"an unresolved lookup still blocks Apply");
    await page.getByLabel("Lookup rule for note",{exact:true}).fill(JSON.stringify({source:{kind:"csv",path:"diagnostics.csv",delimiter:";"},column:"note"}));
    assert.match(await page.locator("#template-binding-status").innerText(),/3 targets still need mapping/);
    await page.getByLabel("Bind Empty",{exact:true}).selectOption("empty");
    await page.getByLabel("Bind Null",{exact:true}).selectOption("null");
    await page.getByLabel("Bind Literal",{exact:true}).selectOption("text");
    await page.getByLabel("Text literal for Literal",{exact:true}).fill("003\nΕλλάδα");
    // An edit to the library must not mutate the already copied target rules.
    await page.getByLabel("Template name",{exact:true}).fill("Edited library template");
    await page.locator("#template-fields .template-field").first().getByLabel("Transform to add",{exact:true}).selectOption("upper");
    await page.locator("#template-fields .template-field").first().getByRole("button",{name:"Add transform",exact:true}).click();
    await page.getByRole("button",{name:"Save new revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"New revision saved"}).waitFor();
    assert.match(await page.locator("#template-copy-name").innerText(),/UI target template, revision 1/);
    await page.getByRole("button",{name:"Apply mappings",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Mappings applied"}).waitFor();
    await page.locator("#advanced > summary").click();
    const generated=JSON.parse(await page.getByLabel("Pipeline JSON").inputValue());
    assert.equal(generated.version,2);assert.deepEqual(generated.columns.map(c=>c.name),["note","Empty","Null","Literal"]);
    assert.deepEqual(generated.columns[0].transforms,[]);assert.equal(generated.columns[1].literal,"");assert.equal(generated.columns[2].literal,null);
    assert.equal(generated.columns[3].literal,"003\nΕλλάδα");assert.equal("template" in generated,false);
    await page.getByRole("button",{name:"Processed Preview (100 rows)",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Preview complete"}).waitFor();
    assert.equal(await page.locator("#preview-table .diagnostic-row").count(),25);
    const previewResponse=await page.request.post((process.env.ETL_TEST_URL||"http://127.0.0.1:8768")+"/api/preview",{headers:{"X-ETL-Token":(await (await page.request.get((process.env.ETL_TEST_URL||"http://127.0.0.1:8768")+"/api/bootstrap")).json()).token},data:{spec:generated}});
    assert.equal((await previewResponse.json()).valid,25);
    await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"no mappings"}).waitFor();
    await page.locator("#template-select").selectOption(firstSelection);
    await page.getByRole("button",{name:"Load revision into editor",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Revision loaded"}).waitFor();
    assert.equal(await page.getByLabel("Template name",{exact:true}).inputValue(),"UI target template");
    await page.getByRole("button",{name:"Save new revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"newer revision exists"}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,"Mobile overflow");
    assert.deepEqual(errors,[]);
    console.log("Templates UI acceptance passed: creation/revisions, explicit bindings, NULL/empty/multiline, no matching, required lookup, version/mapping guards, independent pipeline preview, mobile.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
