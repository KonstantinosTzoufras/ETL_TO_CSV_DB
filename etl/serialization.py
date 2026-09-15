"""Lossless structural conversion between existing v1 JSON and domain models.

No engine semantics or storage formats are changed. Omitted keys, explicit
defaults, literal scalar types, and configured sequence order are preserved.
JSON whitespace and object-key order are not part of the round-trip contract.
"""
import json
from collections.abc import Mapping

from .models import FieldMapping, Pipeline, SourceDefinition, Transform, UNSET, ValidationRule
from .spec import require, validate


def _plain(value):
    """Produce a fresh mutable JSON tree; never share model containers."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _source_from_dict(value):
    return SourceDefinition(value["kind"], {key: item for key, item in value.items() if key != "kind"})


def _source_to_dict(source):
    require(isinstance(source, SourceDefinition), "Expected SourceDefinition")
    require("kind" not in source.options, "Source options must not redefine kind")
    return {"kind": source.kind, **_plain(source.options)}


def pipeline_from_dict(definition: dict) -> Pipeline:
    """Validate and detach a v1 definition from all caller-owned containers."""
    validate(definition)  # Reuse the existing public validation contract.
    columns = []
    for column in definition["columns"]:
        rules = []
        for name in ("required", "max_length"):
            if name in column:
                rules.append(ValidationRule(name, {"value": column[name]}))
        if "lookup" in column:
            lookup = column["lookup"]
            rules.append(ValidationRule("lookup", {
                "source": _source_from_dict(lookup["source"]), "column": lookup["column"],
            }))
        columns.append(FieldMapping(
            name=column["name"], source=column.get("source"),
            literal=column.get("literal", UNSET), target_type=column.get("type"),
            transforms=tuple(Transform(name) for name in column["transforms"]) if "transforms" in column else None,
            validations=tuple(rules),
        ))
    return Pipeline(
        name=definition["name"], source=_source_from_dict(definition["source"]),
        columns=tuple(columns), destination=definition["destination"], version=definition["version"],
    )


def pipeline_to_dict(pipeline: Pipeline) -> dict:
    """Return a fresh v1 dictionary accepted by execute(), validate() and Store."""
    require(isinstance(pipeline, Pipeline), "Expected Pipeline")
    columns = []
    for mapping in pipeline.columns:
        require(isinstance(mapping, FieldMapping), "Expected FieldMapping")
        column = {"name": mapping.name}
        if mapping.source is not None:
            column["source"] = mapping.source
        else:
            column["literal"] = mapping.literal
        if mapping.target_type is not None:
            column["type"] = mapping.target_type
        if mapping.transforms is not None:
            require(all(isinstance(item, Transform) for item in mapping.transforms), "Expected Transform")
            column["transforms"] = [item.name for item in mapping.transforms]
        for rule in mapping.validations:
            require(isinstance(rule, ValidationRule), "Expected ValidationRule")
            require(rule.name not in column, f"Duplicate validation rule: {rule.name}")
            if rule.name in {"required", "max_length"}:
                require(set(rule.parameters) == {"value"}, f"{rule.name}: expected a value parameter")
                column[rule.name] = rule.parameters["value"]
            elif rule.name == "lookup":
                require(set(rule.parameters) == {"source", "column"}, "lookup: expected source and column parameters")
                column["lookup"] = {
                    "source": _source_to_dict(rule.parameters["source"]), "column": rule.parameters["column"],
                }
            else:
                require(False, f"Unsupported v1 validation rule: {rule.name}")
        columns.append(column)
    definition = {
        "version": pipeline.version, "name": pipeline.name,
        "source": _source_to_dict(pipeline.source), "columns": columns,
        "destination": _plain(pipeline.destination),
    }
    return validate(definition)


def pipeline_from_json(payload: str | bytes) -> Pipeline:
    """Use the same JSON number decoding as existing v1 callers.

    Exact decimal literals should be JSON strings: precision already lost by
    decoding a JSON number as float cannot be recovered by a model layer.
    """
    return pipeline_from_dict(json.loads(payload))


def pipeline_to_json(pipeline: Pipeline, *, indent: int | None = None) -> str:
    return json.dumps(pipeline_to_dict(pipeline), ensure_ascii=False, indent=indent)
