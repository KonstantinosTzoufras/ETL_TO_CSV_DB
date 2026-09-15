"""Presentation of existing processing evidence; never reads or processes a source."""
import base64
from collections.abc import Mapping
import csv
import hashlib
import hmac
import json
from pathlib import Path

from .serialization import json_default, row_result_to_dict
from .spec import ConfigError, require

MAX_PAGE_BYTES = 4 * 1024 * 1024
HEADERS = ["record_number", "source_json", "mapped_json", "errors_json"]


def value_cell(value):
    """All display text is explicit and exact, including integers beyond JS range."""
    if value is None:
        return {"available": True, "type": "null", "text": "", "label": "NULL"}
    if isinstance(value, str):
        label = "Empty string (length 0)" if value == "" else f"Whitespace-only string (length {len(value)})" if value.isspace() else "String"
        return {"available": True, "type": "string", "text": value, "label": label,
                "escaped": json.dumps(value, ensure_ascii=False)}
    if type(value) in (int, float, bool):
        kind = {int: "integer", float: "float", bool: "boolean"}[type(value)]
        return {"available": True, "type": kind, "text": json.dumps(value), "label": kind.title()}
    tagged = value if isinstance(value, Mapping) else json_default(value)
    require(isinstance(tagged, Mapping) and tagged.get("$type") in {"decimal", "date", "datetime", "time", "uuid", "bytes", "integer"}
            and isinstance(tagged.get("value"), str), "Unsupported stored diagnostic value")
    kind = tagged["$type"]
    return {"available": True, "type": kind, "text": tagged["value"],
            "label": "Bytes (base64)" if kind == "bytes" else kind.title()}


def missing(label):
    return {"available": False, "label": label}


def present_row(diagnostic, spec, *, legacy=False):
    """Adapt a RowResult dictionary and its actual mapping snapshot for display."""
    original = diagnostic["original_values"]
    transformed = diagnostic.get("transformed_values", {})
    converted = diagnostic.get("converted_values", {})
    grouped = diagnostic["errors"]
    require(all(isinstance(v, dict) for v in (original, transformed, converted, grouped)), "Malformed stored diagnostic stages")
    fields = []
    for mapping in spec["columns"]:
        name = mapping["name"]
        errors = grouped.get(name, [])
        require(isinstance(errors, list), "Malformed stored field errors")
        if legacy:
            require(all(isinstance(e, str) for e in errors), "Malformed legacy field errors")
            errors = [{"field": name, "code": None, "stage": None, "message": e} for e in errors]
        else:
            require(all(isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("field", "code", "stage", "message")) for e in errors), "Malformed stored field errors")
        if "source" in mapping:
            before = value_cell(original[mapping["source"]]) if mapping["source"] in original else missing("Not recorded")
            origin = "Source: " + mapping["source"]
        else:
            before = value_cell(mapping["literal"])
            origin = "Literal from pipeline snapshot"
        after = value_cell(transformed[name]) if name in transformed else missing("Not recorded" if legacy else "Stage unavailable")
        conversion_failed = any(e["stage"] == "conversion" for e in errors)
        final = value_cell(converted[name]) if name in converted else missing("Conversion failed — no converted value" if conversion_failed else "Not recorded" if legacy else "Conversion not completed")
        states = []
        if before["available"] and after["available"]:
            states.append("Unchanged" if before == after else "Transformed value differs")
        if any(e["stage"] == "transform" for e in errors):
            states.append("Transform failure")
        if conversion_failed:
            states.append("Conversion failure")
        if any(e["stage"] in ("required", "max_length", "lookup", "validation") for e in errors):
            states.append("Validation failure")
        if final["available"]:
            states.append("Successfully converted")
        field = {"name": name, "origin": origin, "original": before, "transformed": after,
                 "converted": final, "states": states, "errors": errors}
        if legacy:
            mapped = diagnostic.get("legacy_values", {})
            field["legacy_value"] = value_cell(mapped[name]) if name in mapped else missing("Not recorded")
        fields.append(field)
    # Include every recorded error in the summary; reject malformed orphan fields.
    require(not set(grouped) - {f["name"] for f in fields}, "Stored errors do not match the run mapping snapshot")
    count = sum(len(f["errors"]) for f in fields)
    failed_fields = sum(bool(f["errors"]) for f in fields)
    valid = diagnostic.get("valid", count == 0)
    require(type(valid) is bool and valid == (count == 0), "Inconsistent stored row validity")
    return {"record": str(diagnostic["record"]), "source_position": diagnostic.get("source_position", {"line_start": None, "line_end": None}),
            "source_kind": spec["source"]["kind"], "valid": valid, "legacy": legacy,
            "summary": "Valid" if valid else f"Rejected: {failed_fields} fields, {count} errors",
            "fields": fields}


def present_result(result, spec):
    return present_row(row_result_to_dict(result), spec)


def _cursor(value, secret):
    payload = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    return payload + "." + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _decode_cursor(token, secret, identity):
    try:
        payload, signature = token.split(".")
        require(hmac.compare_digest(signature, hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()), "Invalid diagnostics cursor")
        value = json.loads(base64.urlsafe_b64decode(payload))
        require(value[:3] == identity and type(value[3]) is int and value[3] >= 0, "Rejection file changed; reopen diagnostics")
        return value[3]
    except (ValueError, TypeError, IndexError, AttributeError):
        raise ConfigError("Invalid or expired diagnostics cursor; reopen diagnostics") from None


def rejected_page(run, data_root, secret, *, cursor=None, limit=20):
    """Read only an existing completed run's CSV; signed seek cursors avoid rescans."""
    require(run is not None and run["status"] == "completed", "Diagnostics require a completed run")
    require(type(limit) is int and 1 <= limit <= 100, "Diagnostics limit must be 1–100")
    require(cursor is None or isinstance(cursor, str) and len(cursor) <= 4096, "Invalid diagnostics cursor")
    directory = run["report"].get("directory")
    require(isinstance(directory, str), "Stored rejection file is unavailable")
    path = (Path(directory) / "rejected.csv").resolve()
    require(path.is_relative_to((Path(data_root) / "runs").resolve()) and path.is_file(), "Stored rejection file is unavailable")
    stat = path.stat()
    identity = [run["id"], stat.st_size, stat.st_mtime_ns]
    spec = run["spec"]  # Historical snapshot, never the current editor/saved pipeline.
    legacy = spec["version"] == 1
    destination = spec["destination"]
    delimiter = ";" if legacy else destination.get("delimiter", ";")
    rows, size, next_cursor = [], 0, None
    try:
        with path.open(encoding=destination.get("encoding", "utf-8-sig"), newline="") as handle:
            # readline instead of TextIOWrapper iteration keeps tell/seek enabled.
            reader = csv.reader(iter(handle.readline, ""), delimiter=delimiter, strict=True)
            require(next(reader, None) == HEADERS, "Unrecognized stored rejection layout")
            if cursor:
                handle.seek(_decode_cursor(cursor, secret, identity))
                reader = csv.reader(iter(handle.readline, ""), delimiter=delimiter, strict=True)
            for _ in range(limit):
                position = handle.tell()
                record = next(reader, None)
                if record is None:
                    break
                require(len(record) == 4, "Malformed stored rejection record")
                number, source, mapped, errors = record
                mapped = json.loads(mapped)
                require(isinstance(mapped, dict), "Malformed stored rejection stages")
                diagnostic = {"record": int(number), "original_values": json.loads(source), "errors": json.loads(errors), "valid": False}
                if legacy:
                    diagnostic["legacy_values"] = mapped
                else:
                    require(set(mapped) == {"source_position", "transformed_values", "converted_values"}, "Incomplete stored rejection stages")
                    diagnostic.update(mapped)
                row = present_row(diagnostic, spec, legacy=legacy)
                row_size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                require(row_size <= MAX_PAGE_BYTES, "Diagnostic row is too large for the viewer; use the rejected CSV download")
                if size + row_size > MAX_PAGE_BYTES:
                    next_cursor = _cursor([*identity, position], secret)
                    break
                rows.append(row)
                size += row_size
            else:
                if handle.tell() < stat.st_size:
                    next_cursor = _cursor([*identity, handle.tell()], secret)
    except (csv.Error, UnicodeError, json.JSONDecodeError, KeyError, TypeError, OverflowError):
        raise ConfigError("Cannot display this stored rejection file; it is malformed or exceeds CSV reader limits. The original download is unchanged.") from None
    return {"run_id": run["id"], "name": run["name"], "version": spec["version"], "rows": rows,
            "next_cursor": next_cursor, "legacy": legacy}
