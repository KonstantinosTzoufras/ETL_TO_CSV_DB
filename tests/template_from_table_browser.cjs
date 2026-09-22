// Generate template fields from an inspected table's columns, instead of
// typing every one by hand. Start python -m tests.browser_fixture_server first.
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

    await page.getByText("Reusable mapping templates",{exact:true}).click();
    await page.locator("#mapping-templates summary").filter({hasText:"Create or revise a template"}).click();

    // Nothing inspected yet: the button must say so rather than silently no-op.
    await page.getByRole("button",{name:"Generate fields from inspected table",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Inspect a database table"}).waitFor();
    assert.equal(await page.locator("#template-fields .template-field").count(),1,
                 "the refusal must not have added anything on top of the one starting row");

    // Inspect a real dataset through the existing discovery flow.
    await page.getByText("Discover a source",{exact:true}).click();
    await page.getByRole("button",{name:"Browse datasets",exact:true}).click();
    await page.getByRole("button",{name:"examples /",exact:true}).click();
    await page.getByRole("button",{name:"customers.csv (file)",exact:true}).click();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).waitFor();
    await page.getByRole("button",{name:"Use this dataset",exact:true}).click();

    await page.getByRole("button",{name:"Generate fields from inspected table",exact:true}).click();
    await page.getByRole("status").filter({hasText:"fields proposed"}).waitFor();

    // customers.csv: id;name;email;joined;country - appended after the one
    // pre-existing empty row the editor always starts with.
    const rows=page.locator("#template-fields .template-field");
    assert.equal(await rows.count(),6,"one starting row plus five generated fields");
    const names=await rows.locator('[data-template="output_name"]').evaluateAll(inputs=>inputs.map(i=>i.value));
    assert.deepEqual(names.slice(1),["id","name","email","joined","country"],"catalog order, not resorted");
    const types=await rows.locator('[data-template="target_type"]').evaluateAll(selects=>selects.map(s=>s.value));
    // CSV has no declared catalog type, so every proposal falls back to string.
    assert.ok(types.slice(1).every(t=>t==="string"),"no declared type available means a plain string guess, not a blocked field");

    // A second inspection appends again rather than replacing; the operator
    // controls removal explicitly, same as any other manually-added field.
    await page.getByRole("button",{name:"Generate fields from inspected table",exact:true}).click();
    await page.getByRole("status").filter({hasText:"fields proposed"}).waitFor();
    assert.equal(await rows.count(),11);

    assert.deepEqual(errors,[]);
    console.log("Template-from-table passed: refusal without an inspected source, catalog-order fields generated, string fallback with no declared type, repeat appends rather than replaces.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
