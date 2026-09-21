// Provenance: informational only, shown when a pipeline is opened. Start the
// fixture server first.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
const URL=process.env.ETL_TEST_URL||"http://127.0.0.1:8768";

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));page.on("dialog",d=>d.accept());
    const openEditor=async()=>{
      await page.evaluate(()=>{document.querySelector("#mapping-templates details").open=true;});
      await page.getByRole("button",{name:"Add target field",exact:true}).waitFor();
    };
    await page.goto(URL);
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));

    // A hand-built pipeline (no template) shows no provenance at all.
    assert.equal(await page.locator("#provenance-note").isVisible(),false);

    // Build a template, bind it, save the binding, apply mappings.
    await page.getByText("Reusable mapping templates",{exact:true}).click();
    await openEditor();
    await page.getByLabel("Template name",{exact:true}).fill("Provenance target");
    await page.locator("#template-fields .template-field").first().getByLabel("Output name",{exact:true}).fill("note");
    await page.getByRole("button",{name:"Create new template",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Template created"}).waitFor();

    await page.locator("#new").click();
    await page.getByLabel("File path inside workspace").fill("diagnostics.csv");
    await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
    await page.getByRole("heading",{name:"Review mappings",exact:true}).waitFor();
    await page.getByLabel("Bind note",{exact:true}).selectOption("source");
    await page.getByRole('combobox',{name:'Source column for note',exact:true}).click();
    await page.locator(".select2-results__option").getByText("note",{exact:true}).click();
    await page.getByLabel("Name this binding",{exact:true}).fill("Provenance binding");
    await page.getByRole("button",{name:"Save as new binding",exact:true}).click();
    await page.getByRole("status").filter({hasText:"saved for this source"}).waitFor();
    await page.getByRole("button",{name:"Apply mappings",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Mappings applied"}).waitFor();

    const pipelineName="Provenance pipeline "+Date.now();
    await page.locator("#name").fill(pipelineName);
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:/saved as a new entry/}).waitFor();

    // Right after saving, the note resolves the template and binding by name.
    await page.getByText(/Generated from template "Provenance target"/).waitFor();
    let text=await page.locator("#provenance-note").innerText();
    assert.match(text,/revision 1/);
    assert.match(text,/using the saved binding "Provenance binding"/);
    assert.match(text,/independent of both/);

    // A reload forgets nothing: reopening the saved pipeline shows it again.
    await page.reload();
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    await page.locator("button.pipeline").filter({hasText:pipelineName}).click();
    await page.getByText(/Generated from template "Provenance target"/).waitFor();

    // Switching to a different, unrelated pipeline clears it - no stale carry-over.
    await page.locator("#new").click();
    assert.equal(await page.locator("#provenance-note").isVisible(),false);

    assert.deepEqual(errors,[]);
    console.log("Provenance display passed: hidden by default, resolves template+binding names, survives reload, clears on switch.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
