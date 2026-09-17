"""Pure mapping and validation with bounded previews and streamed exports."""
import uuid
from typing import NamedTuple
from contextlib import ExitStack
from decimal import InvalidOperation
from itertools import islice
from pathlib import Path

from .sources import create_source, open_source
from .models import FieldError, Pipeline, RowResult, SourceDefinition, SourceRow
from .conversions import convert_value, text
from .transforms import apply_transform, apply_transforms
from .validations import required_error, max_length_error, lookup_error
from .serialization import field_mapping_from_dict, pipeline_from_dict
from .exporters import OutputWriter, RejectedWriter, excel_safe, legacy_projection
from .spec import ConfigError, require


def transform(value, column):
    """Legacy public API: version-1 transforms."""
    return apply_transforms(value, column.get("transforms", []), version=1)


def convert(value, kind):
    """Legacy public API: version-1 conversion, including empty-to-null."""
    return convert_value(value, kind, version=1)


def lookup_sets(spec, root):
    result = {}
    version = spec.get("version", 1)
    for column in spec["columns"]:
        if "lookup" not in column:
            continue
        lookup = column["lookup"]
        allowed = set()
        with open_source(lookup["source"], root) as (headers, rows):
            require(lookup["column"] in headers, f"Unknown lookup column: {lookup['column']}")
            for number, row in enumerate(rows):
                require(number < 100000, "Lookup exceeds 100,000 rows; narrow the lookup source")
                try:
                    value = apply_transforms(row[lookup["column"]], column.get("transforms", []), version=version)
                    if value is not None and (version == 2 or value != ""):
                        allowed.add(convert_value(value, column.get("type", "string"), version=version))
                except (ValueError, InvalidOperation, OverflowError):
                    raise ConfigError(f"Lookup contains an invalid {column.get('type', 'string')} value") from None
        result[column["name"]] = allowed
    return result


class PreparedColumn(NamedTuple):
    """One column's row-invariant settings, resolved once before the run."""

    name: str
    source: str | None
    literal: object
    transforms: tuple[str, ...]
    target_type: str
    required: bool
    max_length: int | None
    has_lookup: bool


def prepare_columns(pipeline: Pipeline) -> tuple[PreparedColumn, ...]:
    """Hoist the per-column settings out of the per-row loop.

    The rule mapping, transform names and target type never vary by row, so
    resolving them per value was pure overhead on wide sources.
    """
    prepared = []
    for column in pipeline.columns:
        rules = {rule.name: rule.parameters for rule in column.validations}
        prepared.append(PreparedColumn(
            name=column.name, source=column.source, literal=column.literal,
            transforms=tuple(item.name for item in column.transforms or ()),
            target_type=column.target_type or "string",
            required=bool(rules.get("required", {}).get("value")),
            max_length=rules["max_length"]["value"] if "max_length" in rules else None,
            has_lookup="lookup" in rules,
        ))
    return tuple(prepared)


def process_row(pipeline: Pipeline, row: SourceRow, lookups, prepared=None) -> RowResult:
    """One field flow for both execution modes; retain every completed stage."""
    require(pipeline.version in (1, 2), "Unsupported processing version")
    if prepared is None:
        prepared = prepare_columns(pipeline)
    version, values = pipeline.version, row.values
    transformed, converted, errors = {}, {}, []
    for name, source, literal, transforms, target_type, required, maximum, has_lookup in prepared:
        value = values[source] if source is not None else literal
        try:
            for operation in transforms:
                value = apply_transform(value, operation, version=version)
        except ConfigError:
            raise
        except (ValueError, InvalidOperation, OverflowError) as error:
            transformed[name] = value
            errors.append(FieldError(name, "invalid_transform_input", str(error), "transform"))
            continue
        transformed[name] = value
        if required:
            error = required_error(name, value)
            if error:
                errors.append(error)
        if maximum is not None:
            error = max_length_error(name, value, maximum)
            if error:
                errors.append(error)
        try:
            result = convert_value(value, target_type, version=version)
        except ConfigError:
            raise
        except (ValueError, InvalidOperation, OverflowError) as error:
            errors.append(FieldError(name, "invalid_type", str(error), "conversion"))
            continue
        converted[name] = result
        require(version == 1 or not has_lookup or name in lookups, f"Lookup not prepared: {name}")
        if name in lookups:
            error = lookup_error(name, result, lookups[name])
            if error:
                errors.append(error)
    return RowResult(row, transformed, converted, tuple(errors))


def map_row(row, columns, lookups):
    """Retained dictionary API, always version 1."""
    pipeline = Pipeline("legacy", SourceDefinition("csv"),
                        tuple(field_mapping_from_dict(column) for column in columns), {})
    return legacy_projection(process_row(pipeline, SourceRow(1, row), lookups))


def execute(spec, root, output_root=None, limit=None, progress=None, *, on_row=None):
    pipeline = pipeline_from_dict(spec)
    require(limit is None or type(limit) is int and 1 <= limit <= 1000, "Preview limit must be 1–1000")
    require(limit is not None or output_root is not None, "Full execution needs an output directory")
    names = [column["name"] for column in spec["columns"]]
    lookups = lookup_sets(spec, root)
    report = {"processed": 0, "valid": 0, "invalid": 0, "sample": [], "preview": limit is not None}
    with ExitStack() as stack:
        stream = stack.enter_context(create_source(pipeline.source, root, batch_size=limit if limit and pipeline.source.kind == "sqlserver_query" else 1000).open())
        headers = [column.name for column in stream.schema]
        rows = iter(stream)
        for column in spec["columns"]:
            require("source" not in column or column["source"] in headers, f"Unknown source column: {column.get('source')}")
        writer = rejected = None
        if output_root is not None and limit is None:
            directory = Path(output_root) / uuid.uuid4().hex
            directory.mkdir(parents=True)
            report["directory"] = str(directory)
            writer = OutputWriter(directory, names, spec["destination"], stack, version=pipeline.version)
            # Finalize write-only XLSX streams even if a later input row fails.
            # Failed runs never expose their partial files for download.
            stack.callback(writer.finish)
            rejected = RejectedWriter(directory, spec["destination"], stack, version=pipeline.version)
        prepared = prepare_columns(pipeline)
        for row in islice(rows, limit) if limit is not None else rows:
            result = process_row(pipeline, row, lookups, prepared)
            number = row.number
            if on_row is not None:
                on_row(result)
            report["processed"] += 1
            report["valid" if result.valid else "invalid"] += 1
            if pipeline.version == 1:
                mapped, errors = legacy_projection(result)
                sample = {"record": number, "values": {k: text(v) for k, v in mapped.items()}, "errors": errors}
            else:
                sample = result
            if len(report["sample"]) < (limit or 20):
                report["sample"].append(sample)
            if not result.valid and rejected:
                rejected.write_result(result)
            elif writer and result.valid:
                writer.write_result(result)
            if progress and number % 1000 == 0:
                progress({k: report[k] for k in ("processed", "valid", "invalid")})
    return report
