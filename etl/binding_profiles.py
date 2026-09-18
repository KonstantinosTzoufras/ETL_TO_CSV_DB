"""Saved target-to-source bindings for one dataset, reusable across pipelines.

A profile is a stored copy of the `bindings` list `bind_template` already accepts,
plus the identity of the source it was written for. Nothing here reaches the
engine: reconciliation produces a proposal the operator reviews, and only
`bind_template` ever turns bindings into a pipeline.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .spec import ConfigError, keys, require, source_spec
from .templates import template_from_dict

FORMAT_VERSION = 1
MAX_NAME = 120
_ID = re.compile(r"[0-9a-f]{32}")


def source_key(source):
    """A non-secret descriptor of which dataset a profile was written against.

    Read options are excluded: a CSV read with a different delimiter is still the
    same file. No connection string, host or password can reach this - only the
    name of the environment variable, which saved pipelines already carry.
    """
    source_spec(source)
    kind = source["kind"]
    if kind == "csv":
        return {"kind": kind, "path": source["path"]}
    if kind == "sqlserver":
        return {"kind": kind, "connection_env": source["connection_env"],
                "schema": source["schema"], "table": source["table"]}
    # The SQL text is the dataset, so it is what identifies it. An edited query
    # yields a different key on purpose: its columns may have changed.
    statement = json.dumps(source["query"], sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:32]
    return {"kind": kind, "connection_env": source["connection_env"], "query_digest": digest}


def key_text(key):
    """Canonical form, so equal keys compare equal as stored strings."""
    return json.dumps(key, sort_keys=True, ensure_ascii=False)


def default_name(source):
    """A name the operator will recognise, before they rename it."""
    key = source_key(source)
    if key["kind"] == "csv":
        return key["path"].replace("\\", "/").rsplit("/", 1)[-1][:MAX_NAME]
    connection = key["connection_env"].removeprefix("ETL_SQL_")
    if key["kind"] == "sqlserver":
        return f"{connection} · {key['schema']}.{key['table']}"[:MAX_NAME]
    return f"{connection} · query {key['query_digest'][:8]}"[:MAX_NAME]


def _binding(target, value):
    require(isinstance(value, dict), f"{target}: binding must be an object")
    keys(value, {"source", "literal", "lookup"}, f"{target} binding")
    require(("source" in value) != ("literal" in value), f"{target}: a binding holds a source column or a literal")
    if "source" in value:
        require(isinstance(value["source"], str) and value["source"].strip(), f"{target}: source column name is required")
    if "lookup" in value:
        lookup = value["lookup"]
        require(isinstance(lookup, dict) and isinstance(lookup.get("source"), dict)
                and isinstance(lookup.get("column"), str) and lookup["column"].strip(),
                f"{target}: lookup needs a source and a column")
    return value


def profile_from_dict(value):
    keys(value, {"format_version", "id", "name", "template_id", "authored_revision",
                 "source_key", "schema_snapshot", "bindings", "updated"}, "binding profile")
    require(value.get("format_version") == FORMAT_VERSION, "Unsupported binding profile format")
    for field in ("id", "template_id"):
        require(isinstance(value.get(field), str) and _ID.fullmatch(value[field]), f"Invalid {field}")
    require(isinstance(value.get("name"), str) and 0 < len(value["name"].strip()) <= MAX_NAME,
            f"Profile name must contain 1-{MAX_NAME} characters")
    require(type(value.get("authored_revision")) is int and value["authored_revision"] > 0,
            "Profile revision must be positive")
    require(isinstance(value.get("source_key"), dict) and isinstance(value["source_key"].get("kind"), str),
            "Profile needs a source key")
    snapshot = value.get("schema_snapshot", [])
    require(isinstance(snapshot, list) and all(isinstance(name, str) for name in snapshot),
            "schema_snapshot must be a list of column names")
    bindings = value.get("bindings")
    require(isinstance(bindings, dict), "Profile bindings must be an object keyed by target name")
    for target, binding in bindings.items():
        require(isinstance(target, str) and target.strip(), "Profile binding targets must be names")
        _binding(target, binding)
    require(isinstance(value.get("updated"), str) and value["updated"], "Profile needs an updated timestamp")
    return {**value, "schema_snapshot": list(snapshot)}


def build_profile(name, template_id, authored_revision, source, schema_snapshot, bindings,
                  profile_id=None, updated=None):
    """Assemble and validate a profile. Callers never hand-build the dictionary."""
    return profile_from_dict({
        "format_version": FORMAT_VERSION,
        "id": profile_id or uuid.uuid4().hex,
        "name": name,
        "template_id": template_id,
        "authored_revision": authored_revision,
        "source_key": source_key(source),
        "schema_snapshot": list(schema_snapshot or []),
        "bindings": bindings,
        "updated": updated or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def rank(profile_key, source):
    """How strongly a stored profile relates to the source now on screen.

    Ranking only ever orders a list the operator chooses from. Nothing is
    selected, loaded or applied because of it.
    """
    current = source_key(source)
    if profile_key == current:
        return "exact"
    # The same table reached through another connection: DEV and PROD are
    # genuinely the same binding, and hiding that would be unhelpful.
    if (profile_key.get("kind") == current.get("kind") == "sqlserver"
            and (profile_key.get("schema"), profile_key.get("table")) == (current.get("schema"), current.get("table"))):
        return "same-dataset"
    return "other"


_RULES = ("target_type", "transforms", "required", "max_length", "lookup_required")


def reconcile(profile, template, columns=None):
    """Line the stored bindings up against the template and source of the moment.

    Returns one entry per current target, in template order. Never writes, never
    resolves anything it is unsure about, and never drops a target silently.
    """
    profile = profile_from_dict(profile)
    template = template_from_dict(template)
    require(profile["template_id"] == template.id, "That profile belongs to a different template")
    require(columns is None or (isinstance(columns, list) and all(isinstance(c, str) for c in columns)),
            "Source columns must be a list of names")
    available = None if columns is None else set(columns)
    stored = dict(profile["bindings"])
    entries = []
    for field in template.fields:
        entries.append(_reconcile_one(field, stored.pop(field.output_name, None), available))
    # Anything the template no longer declares is reported, never carried over.
    removed = sorted(stored)
    return {"entries": entries, "removed": removed,
            "revision_changed": profile["authored_revision"] != template.revision,
            "authored_revision": profile["authored_revision"],
            "counts": _counts(entries, removed)}


def _reconcile_one(field, binding, available):
    entry = {"target": field.output_name, "binding": None, "status": "new", "note": ""}
    if binding is None:
        entry["note"] = "not in the profile"
        return entry
    if "source" in binding and available is not None and binding["source"] not in available:
        # Naming the column is the whole point: it is what the operator must look for.
        return {**entry, "status": "missing", "note": f"{binding['source']} is no longer in the source"}
    entry["binding"], entry["status"] = binding, "restored"
    entry["note"] = "constant from the profile" if "literal" in binding else "from the profile"
    return entry


def _counts(entries, removed):
    counts = {"restored": 0, "missing": 0, "new": 0, "removed": len(removed)}
    for entry in entries:
        counts[entry["status"]] += 1
    return counts


def rule_changes(profile_template, template):
    """Targets whose rules moved between the authored revision and this one.

    A binding says where a value comes from; a rule says what must be true of it.
    They are independent, so a binding stays valid - but a target that became
    `decimal` may now reject rows the operator was happy with, so it is reported.
    """
    was = {f.output_name: f for f in template_from_dict(profile_template).fields}
    changes = []
    for field in template_from_dict(template).fields:
        previous = was.get(field.output_name)
        if previous is None:
            continue
        moved = [rule for rule in _RULES if _rule_value(previous, rule) != _rule_value(field, rule)]
        if moved:
            changes.append({"target": field.output_name, "rules": moved,
                            "before": {rule: _rule_value(previous, rule) for rule in moved},
                            "after": {rule: _rule_value(field, rule) for rule in moved}})
    return changes


def _rule_value(field, rule):
    value = getattr(field, rule)
    return [transform.name for transform in value] if rule == "transforms" else value


def bindings_for_binding(entries):
    """The ordered list `bind_template` takes, with unresolved targets left None."""
    return [entry["binding"] if entry["status"] == "restored" else None for entry in entries]


_STAMP = {"template_id": _ID, "binding_profile_id": _ID}


def provenance(value):
    """Validate an informational stamp about how a pipeline was authored.

    Traceability only. Nothing reads this back to resolve, change or re-generate
    a pipeline, and it is stored beside the definition rather than inside it so
    that nothing downstream is even able to.
    """
    if value is None:
        return None
    keys(value, {"template_id", "template_revision", "binding_profile_id", "binding_profile_updated"}, "provenance")
    for field, pattern in _STAMP.items():
        if field in value:
            require(isinstance(value[field], str) and pattern.fullmatch(value[field]), f"Invalid {field} in provenance")
    if "template_revision" in value:
        require(type(value["template_revision"]) is int and value["template_revision"] > 0, "Invalid template revision in provenance")
    if "binding_profile_updated" in value:
        require(isinstance(value["binding_profile_updated"], str) and 0 < len(value["binding_profile_updated"]) <= 64,
                "Invalid binding profile timestamp in provenance")
    return {field: value[field] for field in sorted(value)} or None

def dispatch(store, templates, operation, body):
    allowed = {
        "list": {"template_id", "source"},
        "read": {"id"},
        "save": {"id", "name", "template_id", "revision", "source", "schema_snapshot", "bindings", "updated"},
        "delete": {"id"},
        "reconcile": {"id", "template", "columns"},
    }
    require(operation in allowed, "Unknown binding profile operation")
    keys(body, allowed[operation], "binding profile request")
    if operation == "list":
        return _list(store, body)
    if operation == "read":
        return store.read_profile(body.get("id"))
    if operation == "save":
        return _save(store, templates, body)
    if operation == "delete":
        store.delete_profile(body.get("id"))
        return {"deleted": True}
    return _reconcile(store, templates, body)


def _list(store, body):
    template_id = body.get("template_id")
    require(isinstance(template_id, str) and _ID.fullmatch(template_id), "Invalid template_id")
    source = body.get("source")
    items = []
    for row in store.list_profiles(template_id):
        item = {"id": row["id"], "name": row["name"], "updated": row["updated"],
                "source_key": json.loads(row["source_key"])}
        item["rank"] = rank(item["source_key"], source) if source else "other"
        items.append(item)
    order = {"exact": 0, "same-dataset": 1, "other": 2}
    items.sort(key=lambda item: (order[item["rank"]], item["name"].casefold()))
    return items


def _save(store, templates, body):
    template_id, revision = body.get("template_id"), body.get("revision")
    # The template must exist and the revision must be real: a profile that names
    # a revision nobody can read is untraceable later.
    templates.read(template_id, revision)
    profile = build_profile(
        name=body.get("name"), template_id=template_id, authored_revision=revision,
        source=body.get("source"), schema_snapshot=body.get("schema_snapshot"),
        bindings=body.get("bindings"), profile_id=body.get("id"))
    return store.save_profile(profile, expected_updated=body.get("updated"))


def _reconcile(store, templates, body):
    profile = store.read_profile(body.get("id"))
    template = body.get("template")
    require(isinstance(template, dict), "Reconcile needs the template being edited")
    result = reconcile(profile, template, body.get("columns"))
    authored = templates.read(profile["template_id"], profile["authored_revision"])
    result["rule_changes"] = rule_changes(_dict(authored), template)
    result["profile"] = {"id": profile["id"], "name": profile["name"], "updated": profile["updated"]}
    return result


def _dict(template):
    from .templates import template_to_dict
    return template_to_dict(template)
