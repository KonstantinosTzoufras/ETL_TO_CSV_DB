# Template demo — one blueprint, three sources

Files for the worked example in [../../TEMPLATES_EXPLAINED_EL.md](../../TEMPLATES_EXPLAINED_EL.md).

| File | What it shows |
|---|---|
| `erp_a.csv` | semicolon-separated, padded values, lowercase country codes |
| `erp_b.csv` | comma-separated, different column names, one column nobody wants |
| `erp_c.csv` | the same shape as A, but every row breaks a different rule |
| `erp_*_pipeline.json` | what the template produced after binding — generated, not hand-written |

The three pipelines came from one template whose targets are `customer_code`
(string, trim, required, max 10), `amount` (decimal), `country` (string, upper)
and `origin` (string). The template itself is not stored here: it holds no source
and no binding, so the pipelines are what there is to compare.

Run one:

```powershell
.\.venv\Scripts\python.exe -m etl run examples/templates_demo/erp_a_pipeline.json
```

`erp_c` is the instructive one: 4 rows in, 1 valid, 3 rejected at three different
stages — max_length, required and conversion.
