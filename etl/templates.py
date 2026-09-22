"""Source-independent blueprints, immutable local revisions and explicit binding.

Nothing in execution imports this module. Generated pipelines are ordinary v1/v2
definitions and contain no template reference.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
import unicodedata
import uuid

from .models import _Immutable, Transform
from .serialization import pipeline_from_dict, pipeline_to_dict
from .spec import ConfigError, TYPES, TRANSFORMS, MAX_OUTPUT_COLUMNS, keys, require, validate


@dataclass(frozen=True, slots=True)
class TemplateField(_Immutable):
    output_name: str
    target_type: str
    transforms: tuple[Transform, ...] = ()
    required: bool = False
    max_length: int | None = None
    lookup_required: bool = False


@dataclass(frozen=True, slots=True)
class MappingTemplate(_Immutable):
    id: str
    name: str
    revision: int
    fields: tuple[TemplateField, ...]
    format_version: int = 1
    processing_version: int = 2


def template_from_dict(value):
    keys(value, {"id", "name", "revision", "format_version", "processing_version", "fields"}, "template")
    require(isinstance(value.get("id"), str) and re.fullmatch(r"[0-9a-f]{32}", value["id"]), "Invalid template ID")
    require(type(value.get("revision")) is int and value["revision"] > 0, "Template revision must be positive")
    require(type(value.get("format_version")) is int and value["format_version"] == 1, "Unsupported template format")
    version = value.get("processing_version", 2)
    require(type(version) is int and version in (1, 2), "Template processing version must be 1 or 2")
    require(isinstance(value.get("name"), str) and 0 < len(value["name"].strip()) <= 120, "Template name must contain 1–120 characters")
    require(isinstance(value.get("fields"), list) and 1 <= len(value["fields"]) <= MAX_OUTPUT_COLUMNS, f"Template needs 1–{MAX_OUTPUT_COLUMNS} target fields")
    fields, names = [], set()
    for item in value["fields"]:
        keys(item, {"output_name", "target_type", "transforms", "required", "max_length", "lookup_required"}, "template field")
        name = item.get("output_name")
        require(isinstance(name, str) and name.strip() and len(name) <= 128, "Target field name is required (maximum 128 characters)")
        require(name.casefold() not in names, "Duplicate template target name")
        names.add(name.casefold())
        kind = item.get("target_type")
        require(isinstance(kind, str) and kind in TYPES, "Unsupported template target type")
        transforms = item.get("transforms", [])
        require(isinstance(transforms, list) and all(isinstance(t, str) and t in TRANSFORMS for t in transforms), "Unsupported template transforms")
        for flag in ("required", "lookup_required"):
            require(type(item.get(flag, False)) is bool, f"{flag} must be a boolean")
        maximum = item.get("max_length")
        require(maximum is None or type(maximum) is int and maximum > 0, "max_length must be a positive integer or null")
        fields.append(TemplateField(name, kind, tuple(Transform(t) for t in transforms), item.get("required", False), maximum, item.get("lookup_required", False)))
    return MappingTemplate(value["id"], value["name"], value["revision"], tuple(fields), 1, version)


def template_to_dict(template):
    return {"id": template.id, "name": template.name, "revision": template.revision,
            "format_version": template.format_version, "processing_version": template.processing_version,
            "fields": [{"output_name": f.output_name, "target_type": f.target_type,
                        "transforms": [t.name for t in f.transforms], "required": f.required,
                        "max_length": f.max_length, "lookup_required": f.lookup_required} for f in template.fields]}


def _blueprint(value, template_id, revision):
    keys(value, {"name", "format_version", "processing_version", "fields"}, "template blueprint")
    return template_from_dict({**value, "id": template_id, "revision": revision})


class TemplateStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.lock = threading.Lock()

    def _folder(self, template_id):
        require(isinstance(template_id, str) and re.fullmatch(r"[0-9a-f]{32}", template_id), "Invalid template ID")
        folder = (self.directory / template_id).resolve()
        require(folder.is_relative_to(self.directory), "Invalid template directory")
        return folder

    def read(self, template_id, revision):
        require(type(revision) is int and revision > 0, "Invalid template revision")
        path = (self._folder(template_id) / f"{revision}.json").resolve()
        require(path.is_relative_to(self.directory) and path.is_file(), "Template revision not found")
        require(path.stat().st_size <= 1_000_000, "Template revision exceeds 1 MB")
        with path.open(encoding="utf-8") as handle:
            value = template_from_dict(json.load(handle))
        require(value.id == template_id and value.revision == revision, "Template file identity mismatch")
        return value

    def list(self):
        if not self.directory.exists():
            return []
        result = []
        for folder in sorted(self.directory.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{32}", folder.name) or not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdecimal() else 0):
                if path.stem.isdecimal():
                    item = self.read(folder.name, int(path.stem))
                    result.append({"id": item.id, "name": item.name, "revision": item.revision,
                                   "processing_version": item.processing_version, "field_count": len(item.fields)})
        return result

    def _publish(self, template):
        text = json.dumps(template_to_dict(template), ensure_ascii=False, indent=2)
        require(len(text.encode("utf-8")) <= 1_000_000, "Template revision exceeds 1 MB")
        folder = self._folder(template.id)
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / (uuid.uuid4().hex + ".tmp")
        destination = folder / f"{template.revision}.json"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # Atomic publication with no overwrite, including competing processes.
            os.link(temporary, destination)
        except FileExistsError:
            raise ConfigError("Template revision already exists; reload before saving") from None
        finally:
            temporary.unlink(missing_ok=True)
        return template

    def create(self, blueprint):
        with self.lock:
            return self._publish(_blueprint(blueprint, uuid.uuid4().hex, 1))

    def revise(self, template_id, base_revision, blueprint):
        with self.lock:
            self.read(template_id, base_revision)
            revisions = [int(p.stem) for p in self._folder(template_id).glob("*.json") if p.stem.isdecimal()]
            require(base_revision == max(revisions), "A newer revision exists; load it before saving")
            return self._publish(_blueprint(blueprint, template_id, base_revision + 1))


def _empty_draft(spec, template):
    require(isinstance(spec, dict) and spec.get("columns") == [], "Templates apply only to drafts with no mappings")
    require(type(spec.get("version")) is int and spec["version"] == template.processing_version,
            "Template and pipeline processing versions differ; no version was changed")
    # Reuse the existing pipeline validator for the other sections, without
    # creating a placeholder mapping in the real draft.
    validate({**spec, "columns": [{"name": "draft_check", "literal": None}]})
    return json.loads(json.dumps(spec, allow_nan=False))


def _fold(name):
    """Case and width folded, so CUSTOMER_CODE and customer_code agree."""
    return unicodedata.normalize("NFKC", name).casefold()


def _key(name):
    """_fold plus separators, so CustomerCode and customer code agree too.

    Accents are deliberately kept: folding them would equate two distinct Greek
    names, which is a judgement about language rather than an identity rule.
    """
    folded = _fold(name)
    for character in ("_", "-", ".", " "):
        folded = folded.replace(character, "")
    return folded


# Ordered loosest-last. Each is an identity rule, never a similarity score:
# no edit distance, prefixes, synonyms, position or type is ever consulted.
_TIERS = (("identical", lambda name: name), ("case", _fold), ("separators", _key))


def propose_bindings(template, columns):
    """Suggest a source column per target where exactly one name agrees.

    Pure: reads nothing, writes nothing and never returns a literal binding.
    A target with no single agreeing column is left for the operator, and one
    with several is reported as ambiguous rather than resolved by a looser rule.
    """
    # Accept either the stored model or the copied dict the browser holds.
    template = template if isinstance(template, MappingTemplate) else template_from_dict(template)
    require(isinstance(columns, list) and all(isinstance(c, str) for c in columns), "Source columns must be a list of names")
    usable = [column for column in columns if column.strip()]
    proposals = []
    for target in template.fields:
        proposals.append(_propose_one(target.output_name, usable))
    return proposals


def _propose_one(output_name, columns):
    # Target names are non-blank by validation; blank source columns are dropped
    # by the caller, so both sides here are real names.
    for rule, normalise in _TIERS:
        wanted = normalise(output_name)
        candidates = [column for column in columns if normalise(column) == wanted]
        if len(candidates) == 1:
            return {"binding": {"source": candidates[0]}, "status": "matched", "rule": rule, "candidates": candidates}
        if len(candidates) > 1:
            # A looser tier can only add candidates, so stop rather than widen.
            return {"binding": None, "status": "ambiguous", "rule": rule, "candidates": candidates}
    return {"binding": None, "status": "unmatched", "rule": None, "candidates": []}


def match_template(template, columns, locked=None):
    """propose_bindings, but never disturbing a target the operator already set.

    `locked` is one boolean per target. The values themselves stay in the browser:
    matching decides nothing about them, so it has no reason to receive them.
    """
    proposals = propose_bindings(template, columns)
    require(locked is None or (isinstance(locked, list) and all(type(f) is bool for f in locked)),
            "locked must be a list of booleans, one per target")
    require(locked is None or len(locked) == len(proposals), "locked must cover every target")
    for proposal, is_locked in zip(proposals, locked or [False] * len(proposals)):
        if is_locked:
            proposal.update(binding=None, status="kept", rule=None, candidates=[])
    counts = {"matched": 0, "ambiguous": 0, "unmatched": 0, "kept": 0}
    for proposal in proposals:
        counts[proposal["status"]] += 1
    return {"proposals": proposals, "counts": counts}


def apply_template(template, spec):
    template = template_from_dict(template_to_dict(template))
    return {"pipeline": _empty_draft(spec, template), "template": template_to_dict(template),
            "bindings": [None for _ in template.fields]}


def bind_template(template_copy, spec, bindings):
    """No store lookup: the copied definition alone supplies the target rules."""
    template = template_from_dict(template_copy)
    pipeline = _empty_draft(spec, template)
    require(isinstance(bindings, list) and len(bindings) == len(template.fields), "Resolve every target field explicitly")
    columns = []
    for target, binding in zip(template.fields, bindings):
        require(isinstance(binding, dict), f"{target.output_name}: unmapped target (optional targets also need an explicit binding)")
        keys(binding, {"source", "literal", "lookup"}, "target binding")
        require(("source" in binding) != ("literal" in binding), f"{target.output_name}: select a source column OR a literal")
        require(not target.lookup_required or "lookup" in binding, f"{target.output_name}: lookup requirement is unresolved")
        require(target.lookup_required or "lookup" not in binding, f"{target.output_name}: template has no lookup requirement")
        column = {"name": target.output_name, "type": target.target_type,
                  "transforms": [t.name for t in target.transforms], "required": target.required, **binding}
        if target.max_length is not None:
            column["max_length"] = target.max_length
        columns.append(column)
    pipeline["columns"] = columns
    # Materialize the already-supported FieldMapping models and portable format.
    return pipeline_to_dict(pipeline_from_dict(pipeline))


# SQL Server's own type name (sys.columns/TYPE_NAME), lowercased, to the
# pipeline's own target_type vocabulary. Anything not listed here defaults to
# "string": an unrecognised type still needs a name and a guess, and a
# proposal the operator has to loosen is safer than one that silently drops
# the column or blocks Create outright.
_SQL_TYPE_TO_TARGET = {
    "char": "string", "varchar": "string", "text": "string", "xml": "string", "uniqueidentifier": "string",
    "nchar": "string", "nvarchar": "string", "ntext": "string",
    "tinyint": "int", "smallint": "int", "int": "int", "bigint": "int",
    "decimal": "decimal", "numeric": "decimal", "money": "decimal", "smallmoney": "decimal",
    "float": "float", "real": "float",
    "bit": "bool",
    "date": "date",
    "datetime": "datetime", "datetime2": "datetime", "smalldatetime": "datetime", "datetimeoffset": "datetime",
}
# nchar/nvarchar/ntext store two bytes per character; sys.columns.max_length
# is in bytes either way, so only these need converting back to characters.
_WIDE_CHAR_TYPES = {"nchar", "nvarchar", "ntext"}


def _field_from_column(entry):
    """One template field, guessed from a database column's catalog entry.

    A proposal, not a decision: this fills in exactly what 'Add target
    field' would have produced by hand - the operator still reviews every
    row, renames what they want and removes what they don't, before Create.
    """
    require(isinstance(entry, dict), "Column entry must be an object")
    column = entry.get("column")
    require(isinstance(column, dict) and isinstance(column.get("name"), str) and column["name"].strip(),
            "Column entry needs a name")
    declared = entry.get("declared_type")
    target_type = _SQL_TYPE_TO_TARGET.get(declared.lower(), "string") if isinstance(declared, str) else "string"
    max_length = None
    if target_type == "string":
        raw = entry.get("max_length_bytes")
        if type(raw) is int and raw > 0:
            max_length = raw // 2 if declared.lower() in _WIDE_CHAR_TYPES else raw
    return {"output_name": column["name"], "target_type": target_type, "transforms": [],
            "required": column.get("nullable") is False, "max_length": max_length, "lookup_required": False}


def fields_from_columns(columns):
    """Template fields proposed from a table's own columns, in catalog order.

    Takes exactly what /api/discovery (columns operation) returns for a SQL
    Server table: never a raw connection, schema or table name, so this stays
    the same pure/no-I/O shape as the rest of the template machinery.
    """
    require(isinstance(columns, list) and columns, "At least one column is required")
    require(len(columns) <= MAX_OUTPUT_COLUMNS, f"At most {MAX_OUTPUT_COLUMNS} columns are supported")
    fields = [_field_from_column(entry) for entry in columns]
    names = [field["output_name"].casefold() for field in fields]
    require(len(names) == len(set(names)), "Source has duplicate column names; rename before generating fields")
    return fields


def dispatch(store, operation, body):
    allowed = {
        "list": set(), "read": {"id", "revision"}, "create": {"definition"},
        "revision": {"id", "base_revision", "definition"},
        "apply": {"id", "revision", "spec"}, "bind": {"template", "spec", "bindings"},
        "match": {"template", "columns", "locked"}, "from_columns": {"columns"},
    }
    require(operation in allowed, "Unknown template operation")
    keys(body, allowed[operation], "template request")
    if operation == "list":
        return store.list()
    if operation == "read":
        return template_to_dict(store.read(body.get("id"), body.get("revision")))
    if operation == "create":
        return template_to_dict(store.create(body.get("definition")))
    if operation == "revision":
        return template_to_dict(store.revise(body.get("id"), body.get("base_revision"), body.get("definition")))
    if operation == "apply":
        return apply_template(store.read(body.get("id"), body.get("revision")), body.get("spec"))
    if operation == "match":
        return match_template(body.get("template"), body.get("columns"), body.get("locked"))
    if operation == "from_columns":
        return {"fields": fields_from_columns(body.get("columns"))}
    return bind_template(body.get("template"), body.get("spec"), body.get("bindings"))
