# Template application — proposed redesign

Design review requested before implementation. Nothing here is built yet.
Line references are to the current code.

Goal: remove manual work that carries no decision, while every mapping that
reaches a pipeline stays explicit and reviewed.

---

## 1. The current flow

`Apply selected revision` calls `apply_template` ([templates.py:157](etl/templates.py#L157)),
which returns `bindings = [None] * len(fields)` and an unchanged empty pipeline.
It deliberately binds nothing — `tests/test_templates.py:106` asserts exactly that.

The operator then does, per target:

| Step | Interaction | Decision content |
|---|---|---|
| choose mode | select | real, when the target is a literal |
| choose column | select | **none, when the names already agree** |
| lookup JSON | textarea | real |

`Read binding columns` ([templates.js:69](etl/static/templates.js#L69)) posts to
`/api/columns` and fills a `<datalist>`. It binds nothing and says so:
*"Source columns available. No bindings were selected."*

Finally `Generate pipeline mappings` posts every binding to `bind_template`
([templates.py:163](etl/templates.py#L163)), which rejects any unresolved target.

**Cost:** a 40-target template against a source that names its columns the same way
costs ~80 interactions, none of which carry a decision. That is the problem.

**What is right about it and must survive:** nothing is inferred, nothing is written
to `definition.columns` before the operator presses the final button, and the
generated pipeline holds no template reference.

---

## 2. The proposed flow

| | |
|---|---|
| 1 | **Apply selected revision** — unchanged, still binds nothing |
| 2 | **Match template to source** — loads the source columns if needed, then proposes |
| 3 | **Review mappings** — every target listed with its state; fix only what is open |
| 4 | **Apply mappings** — writes the pipeline, exactly as today |

Step 2 is new and **opt-in**. Skipping it leaves today's behaviour intact.

`apply` stays dumb on purpose: matching is a separate call, so the existing
contract — *apply binds nothing* — is preserved rather than weakened.

---

## 3. Auto-match rules

A pure function, no I/O:

```python
def propose_bindings(template, columns) -> list[Proposal]
```

For target name `T` and source column names `S`, three tiers are tried **in order**.
Let `fold(x) = NFKC(x).casefold()` and `key(x) = fold(x)` with `_`, `-`, `.` and
space removed.

| Tier | Rule | Tag |
|---|---|---|
| 1 | `s == T` | `identical` |
| 2 | `fold(s) == fold(T)` | `case` |
| 3 | `key(s) == key(T)` | `separators` |

Resolution:

- exactly **one** candidate in a tier → propose it, tagged with that tier
- **two or more** candidates → `ambiguous`; stop, do **not** fall through to a
  looser tier, since a looser tier can only add candidates. Report the candidates.
- **zero** candidates → try the next tier; after tier 3, `unmatched`
- a source column list containing the same name twice makes that name ambiguous
- empty or whitespace-only names never match

### Deliberately excluded

Edit distance, prefix/substring containment, synonym or translation tables,
position-based pairing (nth column to nth target), and type-based inference. Each
of these can connect two unrelated fields in a way that reads as correct.

Accent folding is also excluded: `ΠΟΣΟ` and `ΠΟΣΌ` stay distinct. Folding them is a
judgement about language, not an identity rule.

### Worked example

Source columns: `CUSTOMER_CODE`, `Amount`, `AMOUNT`, `ctry`, `country`, `Country`

| Target | Result | Why |
|---|---|---|
| `customer_code` | `CUSTOMER_CODE` · `case` | one case-insensitive candidate |
| `amount` | **ambiguous** | `Amount` and `AMOUNT` both fold to `amount` |
| `country` | `country` · `identical` | tier 1 resolves before `Country` is considered |
| `origin` | **unmatched** | no candidate in any tier |

`ctry` is never offered for `country`. That is the point.

---

## 4. Ambiguity and uncertainty

An ambiguous or unmatched target is left **empty**, exactly as an untouched target
is today, and blocks `Apply mappings` through the existing server check in
`bind_template`. No new enforcement path is introduced.

The review row states the case:

```
customer_code  ->  CUSTOMER_CODE     matched (case)
amount         ->  ?                 ambiguous: Amount, AMOUNT
country        ->  country           matched
origin         ->  ?                 needs mapping
```

Two further guarantees:

- **Re-running Match never overwrites an operator's choice.** It fills only targets
  that are still unset and reports how many it skipped, so it is safe to press again
  after changing the source.
- **A proposal is data, not a commitment.** It lives in the browser until
  `Apply mappings`; `definition.columns` is untouched before that, as today.

The loosest rule (`separators`) is marked differently from `identical`, because that
is the one worth a second look.

---

## 5. Wording

| Now | Proposed |
|---|---|
| `Read binding columns` | `Load source columns` |
| — | `Match template to source` *(new)* |
| `Explicit template bindings` | `Review mappings` |
| `Generate pipeline mappings` | `Apply mappings` |
| "Required and optional targets both need a source column or an explicit literal. No names are matched automatically." | "Obvious name matches are proposed for review. Anything uncertain stays open, and nothing is written until you press Apply mappings." |

Constants stay exactly where they are: the mode select keeps Source column, Text
literal, NULL literal, Empty-string literal, Boolean true and Boolean false. A
target bound to a literal is simply never proposed a source column.

---

## 6. Compatibility

| Area | Impact |
|---|---|
| Template files on disk | **None.** No new field; `format_version` stays 1 |
| Existing templates | **None.** Matching reads them, never rewrites them |
| Generated pipelines | **None.** Same shape, still no template reference |
| `apply_template` | **Unchanged**, still returns all-`None` |
| `bind_template` | **Unchanged** signature and validation |
| New `match` operation | Additive; an older client that never calls it behaves as today |
| `tests/templates_browser.cjs` | Button names and status text change; must be updated |
| `test_apply_copies_without_any_name_matching` | Assertion still true. Rename to `..._apply_never_binds_anything_by_itself` so it reads correctly next to a matcher |

---

## 7. Creating a template from an existing pipeline

Recommended, and small.

```python
def template_blueprint_from_pipeline(spec, name) -> dict
```

Copies per column: `name` to `output_name`, `type` to `target_type`, `transforms`,
`required`, `max_length`, and `lookup_required = "lookup" in column`.

Drops: `source`, `literal`, and everything outside `columns` — connection, table,
query and credentials are not in scope to begin with, since they live in
`spec["source"]`, which is never read.

`processing_version` comes from `spec["version"]`.

### The literal question

A constant such as `origin = "ERP-A"` is **source-specific by definition** — it is
the label of the system the data came from. Copying it into a reusable contract
would carry one source's identity into every other one. So literals are dropped, and
the response names them:

> *3 constant columns (`origin`, `batch`, `channel`) were not copied. Add them as
> targets if every source should produce them.*

### It fills the editor, it does not save

The button loads the template editor and stops. The operator names it and presses
`Create new template`, so no template is ever created without an explicit act —
the same rule the rest of the panel follows.

---

## 8. Tests to add

### Matching — pure unit tests

1. identical name matches, tagged `identical`
2. case-only difference matches, tagged `case`
3. separator/space difference matches, tagged `separators`
4. `Amount` + `AMOUNT` produces ambiguous, no binding, both candidates reported
5. `customer code` + `customer_code` produces ambiguous at tier 3
6. the same column name twice in the source is ambiguous
7. no candidate anywhere produces unmatched
8. **negative:** `customer_code` against `cust_code`, `code`, `customer_code_2` — all unmatched
9. **negative:** `ΠΟΣΟ` against `ΠΟΣΌ` — unmatched
10. tier 1 wins over a tier 2 candidate that also exists
11. proposals never mutate the template and never produce a literal binding
12. re-matching leaves an already-set binding alone and counts it as skipped

### Matching — integration

13. `propose_bindings` output fed to `bind_template` equals hand binding, byte for byte
14. a matched target with `lookup_required` still blocks until a lookup is supplied
15. `apply_template` still returns all-`None` — the contract did not move

### Pipeline to template

16. copies exactly the six permitted properties
17. drops `source` and `literal`; the blueprint contains no source key at any depth
18. the result passes `template_from_dict` unchanged
19. round trip: pipeline to template to apply to match gives identical columns, literals excepted
20. a pipeline with no columns is refused

### Browser

21. Match proposes, and the review list shows matched / needs mapping states
22. `Apply mappings` stays disabled while anything is open
23. an ambiguous target stays empty and lists its candidates
24. a literal-only target is still bindable by hand after matching
25. updated wording assertions

---

## Open question for the owner

Tier 3 (`customer_code` to `CUSTOMER CODE` to `CustomerCode`) is the one rule that
goes beyond "the names are the same". It is still deterministic and still refuses
ambiguity — but if you would rather the tool never look past case, say so and tiers
1–2 ship alone. Everything else in this plan is unaffected by that choice.

---

## 9. Seeding a template from what already exists

Two different reversals, often conflated. Both are worth having; they differ in
what they can know.

### 9a. From an existing pipeline

Covered in section 7. The pipeline already holds decided rules — type,
transforms, required, max_length, lookup — so the copy is faithful and nothing
has to be guessed.

### 9b. From a table or query directly

The operator points at a table and gets a template whose targets mirror its
columns, then edits it. This is the one that removes the most typing: 60 columns
otherwise means 60 hand-typed target names.

**What the code can already see.** `read_schema` builds `SourceColumn` with
`native_type`, `nullable`, `display_size`, `internal_size`, `precision` and
`scale` ([sources.py:117-123](etl/sources.py#L117-L123)) — but `inspect_source`
throws all of it away and returns names only ([sources.py:226](etl/sources.py#L226)),
so `/api/columns` never carries it. A separate read would be needed that keeps the
metadata. CSV has none of it: every column is `str`.

**Seeding is not binding.** The table is read once to produce a starting list.
The saved template holds no source, no connection and no binding — the moment it
is created it is as source-independent as one typed by hand. The rule in section 1
is untouched.

### What each property can be seeded from

| Template field | From a pipeline | From a table | From a CSV |
|---|---|---|---|
| `output_name` | column name | column name | header |
| `target_type` | exact | see below | `string` only |
| `transforms` | exact | none — not a source property | none |
| `required` | exact | `nullable == False` is a *suggestion* | unknown |
| `max_length` | exact | `internal_size`, guarded | unknown |
| `lookup_required` | `"lookup" in column` | never | never |

### The type question, and why the default should be `string`

We established earlier that **the target type is validation, not formatting** —
`string` under processing version 2 is lossless for every ODBC type tested, while
a stricter type can reject a row that the database was perfectly happy with.

Seeding `decimal` and `int` straight from the driver would quietly undo that. A
column that is `numeric(18,2)` in SQL Server but arrives through a view as text
would start failing conversion, and the template would be blamed.

So the proposal is:

- **default: every seeded target is `string`**, no transforms, not required
- the editor shows the database's own type next to each field as **information**
- a single explicit control — *Use the database types* — upgrades them in one go
  for the operator who wants that validation

The same reasoning applies to `required`: `NOT NULL` in the source says what the
database guarantees, not what your output file should reject. Seed it as a
suggestion the operator sees, not as a silent rule.

`max_length` is seeded from `internal_size` only when it is a positive integer
within a sane bound; `nvarchar(max)` reports a size that is not a length limit
anyone meant to type.

### Additional tests

26. seeding from a table produces `string` targets by default, whatever the driver reported
27. *Use the database types* maps each native type to the supported set, and refuses unknown ones rather than guessing
28. a seeded template contains no source, connection, table or query key at any depth
29. `nullable == False` seeds a suggestion, not `required: true`, unless the operator opts in
30. `max_length` is left empty for an out-of-range or missing `internal_size`
31. seeding from CSV yields `string` targets and no size or required flags
32. a seeded template, once saved, applies to a *different* source with no trace of the one it was seeded from
