# Data flow: the architecture is frozen after Stage 5

- **SourceRow = what was read.** Original native values and source position.
- **RowResult = what happened during processing.** Separate original, transformed
  and successfully converted values, field errors and row validity.
- **Exporter = how the final value is represented.** Output formatting belongs
  here, never in source reading or business-value processing.

## Processing order

Source/literal value → configured transforms in order → required → max length
→ target-type conversion → lookup → field errors / RowResult → export.

Independent validation errors accumulate. Lookup is skipped if conversion fails.
A failed conversion has no entry in `converted_values`; its transformed value
remains available for diagnosis. Preview and full execution share the processor.

## Verified real example: dbo.BRANDS, ID 14

This example was read from the real SQL Server during the Stage-5 walkthrough.
The v2 mapping used the target types below, **no transforms**, and no additional
required, max-length or lookup rules. CSV used an explicit `\N` NULL token and
formula policy `preserve`. (The *default* NULL token changed after this
walkthrough - see the NULL row's note below.)

| Field / target type | Original value | Transformed value | Converted value | Validation result | Exported CSV field |
| --- | --- | --- | --- | --- | --- |
| `ID` / int | `14` | `14` | `14` | No errors | `14` |
| `CODE` / string | `"DISCBRAND-1"` | `"DISCBRAND-1"` | `"DISCBRAND-1"` | No errors | `DISCBRAND-1` |
| `DESCR` / string | `"ΕΚΠΤΩΣΗ ΕΙΔΟΥΣ"` | `"ΕΚΠΤΩΣΗ ΕΙΔΟΥΣ"` | `"ΕΚΠΤΩΣΗ ΕΙΔΟΥΣ"` | No errors | `ΕΚΠΤΩΣΗ ΕΙΔΟΥΣ` |
| `PERFORMANCE1` / decimal | `Decimal("0.0000")` | `Decimal("0.0000")` | `Decimal("0.0000")` | No errors | `0.0000` |
| `IDBRANDFACT` / optional string | `None` | `None` | `None` | No errors; optional NULL accepted | `\N` |

The driver supplied Decimal and None directly. The exporter alone rendered them
as `0.0000` and `\N`. A literal empty string would instead produce an empty CSV
field. **Default `null_value` is now `""` (a plain empty field), not `\N`**:
SQL Server's own bulk-import tools have no `\N` convention, so a NULL written
that way used to land as literal, fatal text the moment a destination column
wasn't a string. `\N` above is what this example got by explicitly asking for
it - the default output no longer distinguishes NULL from empty text unless a
pipeline sets `null_value` itself. Values in the three stage maps happen to be
equal in this example because no transform was requested and the explicit
target conversions preserved them.

`RowResult.valid` was true and `errors` was empty. This confirms the configured
mapping, not every possible business rule for a product. SourceRow.number was 1
in that read; it is execution-relative and is not the database ID 14 or a stable
SQL ordering guarantee. Original values include the other source columns too.

Only SELECT queries were used. The walkthrough's temporary CSV files were
removed; no values were written back to SQL Server.

## Boundaries to keep

- Do not implicitly trim, change case, infer dates or normalize identifiers.
- Keep NULL distinct from empty text until the explicit export policy applies.
- Keep Decimal precision; never round-trip through float.
- Keep ERP names such as BRANDS in pipeline definitions/examples, not core logic.
- No new refactor stage is authorized. Full policies and verification are in
  [ETL_PWS_DOULEYEI_EL.md](ETL_PWS_DOULEYEI_EL.md).
