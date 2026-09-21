// Saving twice must not silently produce twins. Start the fixture server first.
// Regression: a page reload forgets which saved pipeline is on screen, so the
// next Save used to create a second row under the same name, and the sidebar
// gave no way to tell the two apart.
const {chromium}=require(process.env.ETL_PLAYWRIGHT||"playwright");
const assert=require("node:assert/strict");
const URL=process.env.ETL_TEST_URL||"http://127.0.0.1:8768";

const count=(page,name)=>page.evaluate(n=>saved.filter(item=>item.name===n).length,name);

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.ETL_CHROMIUM?{executablePath:process.env.ETL_CHROMIUM}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    let answer=true, asked=[];
    page.on("pageerror",e=>errors.push(e.message));
    page.on("dialog",d=>{asked.push(d.message());return answer?d.accept():d.dismiss();});
    await page.goto(URL);
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));

    // A name of its own, so the test does not depend on what other tests saved.
    const NAME="Save identity "+Date.now();
    await page.locator("#name").fill(NAME);

    // Before anything is saved, the hint says what Save will do.
    assert.equal(await page.locator("#save-hint").innerText(),"Saving creates a new saved pipeline.");
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"saved as a new entry"}).waitFor();
    const before=await count(page,NAME);
    assert.equal(before,1,"the first save creates exactly one");
    assert.equal(await page.locator("#save-hint").innerText(),`Saving updates "${NAME}".`);

    // Still linked: saving again updates in place and asks nothing.
    asked=[];
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Pipeline updated"}).waitFor();
    assert.equal(await count(page,NAME),before,"an unchanged link must not add a row");
    assert.deepEqual(asked,[],"no question while the link is intact");

    // A reload is what used to cause the twins: the link is gone.
    await page.reload();
    await page.waitForFunction(()=>document.querySelector("#name").value.includes("Customers"));
    await page.waitForFunction(()=>saved.length>0);
    await page.locator("#name").fill(NAME);
    assert.equal(await page.locator("#save-hint").innerText(),"Saving creates a new saved pipeline.");

    asked=[];answer=true;   // OK - update the existing one
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Pipeline updated"}).waitFor();
    assert.equal(asked.length,1,"saving after a reload asks before creating");
    assert.match(asked[0],/already saved/);
    assert.equal(await count(page,NAME),before,"answering OK updates instead of duplicating");

    // Cancelling or dismissing writes nothing at all: no branch of a dialog may
    // be the one that creates a duplicate.
    await page.reload();
    await page.waitForFunction(()=>saved.length>0);
    await page.locator("#name").fill(NAME);
    asked=[];answer=false;
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"Nothing was saved"}).waitFor();
    assert.equal(asked.length,1);
    assert.equal(await count(page,NAME),before,"dismissing must not create anything");

    // A separate copy is made by naming it, which cannot happen by accident.
    await page.locator("#name").fill(NAME+" (copy)");
    await page.getByRole("button",{name:"Save pipeline",exact:true}).click();
    await page.getByRole("status").filter({hasText:"saved as a new entry"}).waitFor();
    assert.equal(await count(page,NAME+" (copy)"),1);

    // Saved rows carry a date, so same-named survivors can be told apart.
    const entries=await page.locator("#pipelines .pipeline small").allInnerTexts();
    assert.ok(entries.every(text=>/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text)),"each saved row shows when it was saved");

    assert.deepEqual(errors,[]);
    console.log("Save identity passed: hint tracks the link, reload asks before duplicating, update in place, dismissal writes nothing, renamed copy, dated sidebar.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
