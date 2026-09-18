// Saved bindings end to end. Start python -m tests.browser_fixture_server first.
// The point being proved: binding once, then reusing it, with review in between.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
const URL=process.env.ETL_TEST_URL||"http://127.0.0.1:8768";

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on("pageerror",e=>errors.push(e.message));page.on("dialog",d=>d.accept());
    // Toggling a <summary> blindly closes it when it is already open.
    const openEditor=async()=>{
      await page.evaluate(()=>{document.querySelector("#mapping-templates details").open=true;});
      await page.getByRole("button",{name:"Add target field",exact:true}).waitFor();
    };
    await page.goto(URL);
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));

    // A template whose target names deliberately resemble nothing in the source.
    await page.getByText("Reusable mapping templates",{exact:true}).click();
    await openEditor();
    await page.getByLabel("Template name",{exact:true}).fill("Saved binding target");
    const fields=page.locator("#template-fields .template-field");
    await fields.first().getByLabel("Output name",{exact:true}).fill("customer_code");
    for(const name of ["origin","note"]){
      await page.getByRole("button",{name:"Add target field",exact:true}).click();
      await fields.last().getByLabel("Output name",{exact:true}).fill(name);
    }
    await page.getByRole("button",{name:"Create new template",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Template created"}).waitFor();

    async function freshDraft(){
      await page.locator("#new").click();
      await page.getByLabel("File path inside workspace").fill("diagnostics.csv");
      await page.getByRole("button",{name:"Apply selected revision",exact:true}).click();
      await page.getByRole("heading",{name:"Review mappings",exact:true}).waitFor();
    }

    // --- First time: nothing saved, bind by hand. ------------------------
    await freshDraft();
    await page.waitForFunction(()=>document.getElementById("profile-summary").textContent.includes("No saved bindings"));
    assert.equal(await page.locator("#template-generate").isDisabled(),true,"Apply is blocked while targets are open");

    // "note" is the only name the source shares; matching must find just that one.
    await page.getByRole("button",{name:"Match remaining by name",exact:true}).click();
    await page.getByRole("status").filter({hasText:"targets proposed"}).waitFor();
    assert.match(await page.locator("#template-match-summary").innerText(),/1 proposed by name/);
    assert.equal(await page.getByLabel("Bind customer_code",{exact:true}).inputValue(),"","no rule reaches customer_code");

    await page.getByLabel("Bind customer_code",{exact:true}).selectOption("source");
    await page.getByRole('combobox',{name:'Source column for customer_code',exact:true}).click();
    // customer_code <- note: the judgement no naming rule could ever produce.
    await page.locator(".select2-results__option").getByText("note",{exact:true}).click();
    await page.getByLabel("Bind origin",{exact:true}).selectOption("text");
    await page.getByLabel("Text literal for origin",{exact:true}).fill("ERP-A");
    await page.waitForFunction(()=>!document.getElementById("template-generate").disabled);

    // The default name comes from the source, and is editable.
    assert.equal(await page.getByLabel("Name this binding",{exact:true}).inputValue(),"diagnostics.csv");
    await page.getByLabel("Name this binding",{exact:true}).fill("ERP A · customers");
    await page.getByRole("button",{name:"Save as new binding",exact:true}).click();
    await page.getByRole("status").filter({hasText:"saved for this source"}).waitFor();
    await page.getByRole("button",{name:"Apply mappings",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Mappings applied"}).waitFor();
    const first=await page.evaluate(()=>definition.columns.map(c=>[c.name,c.source??c.literal]));
    assert.deepEqual(first,[["customer_code","note"],["origin","ERP-A"],["note","note"]]);

    // The stamp records how it was authored, and stays out of the definition.
    const stamp=await page.evaluate(()=>pipelineProvenance);
    assert.ok(stamp.template_id&&stamp.binding_profile_id,"provenance names the template and the binding");
    assert.equal(await page.evaluate(()=>"provenance" in definition),false,"never inside the pipeline");

    // --- Second time: the saved binding does the work. -------------------
    await freshDraft();
    await page.waitForFunction(()=>document.getElementById("profile-summary").textContent.includes("1 for this source"));
    assert.match(await page.locator("#profile-select").innerText(),/ERP A · customers · for this source/);
    await page.locator("#profile-select").selectOption({label:"ERP A · customers · for this source"});
    await page.getByRole("button",{name:"Load binding",exact:true}).click();
    await page.getByRole("status").filter({hasText:"3 targets restored"}).waitFor();
    assert.match(await page.locator("#profile-summary").innerText(),/3 restored/);
    // Loading writes nothing to the pipeline; only Apply does.
    assert.equal(await page.evaluate(()=>definition.columns.length),0,"load never touches the pipeline");
    await page.getByRole("button",{name:"Apply mappings",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Mappings applied"}).waitFor();
    assert.deepEqual(await page.evaluate(()=>definition.columns.map(c=>[c.name,c.source??c.literal])),first);

    // --- A target the binding cannot know about stays open. --------------
    await openEditor();
    await page.getByRole("button",{name:"Load revision into editor",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Revision loaded"}).waitFor();
    await page.getByRole("button",{name:"Add target field",exact:true}).click();
    await fields.last().getByLabel("Output name",{exact:true}).fill("vat_number");
    await page.getByRole("button",{name:"Save new revision",exact:true}).click();
    await page.getByRole("status").filter({hasText:"New revision saved"}).waitFor();

    await freshDraft();
    await page.locator("#profile-select").selectOption({label:"ERP A · customers · for this source"});
    await page.getByRole("button",{name:"Load binding",exact:true}).click();
    await page.getByRole("status").filter({hasText:"3 targets restored"}).waitFor();
    const summary=await page.locator("#profile-summary").innerText();
    assert.match(summary,/1 not in the binding/,"the new target is reported");
    assert.match(summary,/written for revision 1/,"the revision gap is stated");
    assert.equal(await page.locator("#template-generate").isDisabled(),true,"an open target blocks Apply");
    assert.equal(await page.getByLabel("Bind vat_number",{exact:true}).inputValue(),"");

    // Deleting the binding leaves the generated pipelines alone.
    await page.locator("#profile-select").selectOption({label:"ERP A · customers · for this source"});
    await page.getByRole("button",{name:"Delete binding",exact:true}).click();
    await page.getByRole("status").filter({hasText:"No pipeline changed"}).waitFor();
    await page.waitForFunction(()=>document.getElementById("profile-summary").textContent.includes("No saved bindings"));

    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,"Mobile overflow");
    assert.deepEqual(errors,[]);
    console.log("Binding profiles passed: hand binding, save, reuse on a second pipeline, provenance stamp, new-target reporting, revision gap, delete, mobile.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
