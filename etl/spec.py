"""Versioned pipeline contract, shared by the CLI, UI and engine."""
import codecs
import re

MAX_OUTPUT_COLUMNS = 4096
# One output file per distinct value would let a near-unique column (an id, a
# timestamp) explode into thousands of files and file handles. This governs
# split_by only; the normal single-file case has no such ceiling.
MAX_SPLIT_GROUPS = 200


class ConfigError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConfigError(message)


def keys(value, allowed, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(not set(value) - set(allowed), f"Unknown {label} options: {set(value) - set(allowed)}")


def source_spec(source):
    require(isinstance(source, dict), "Source must be an object")
    kind = source.get("kind")
    if kind == "csv":
        keys(source, {"kind", "path", "delimiter", "encoding"}, "CSV source")
        require(isinstance(source.get("path"), str) and source["path"].strip(), "CSV path is required")
        delimiter = source.get("delimiter", ";")
        require(isinstance(delimiter, str) and len(delimiter) == 1 and delimiter not in '\r\n\x00"', "Choose a single CSV delimiter")
        try:
            codecs.lookup(source.get("encoding", "utf-8-sig"))
        except (LookupError, TypeError):
            raise ConfigError("Unknown CSV encoding") from None
    elif kind == "xml":
        # No delimiter/encoding concept: the file's own <?xml encoding?>
        # declaration governs how it is read, exactly as any XML parser would.
        keys(source, {"kind", "path"}, "XML source")
        require(isinstance(source.get("path"), str) and source["path"].strip(), "XML path is required")
    elif kind == "sqlserver_query":
        from .queries import query_from_dict
        keys(source, {"kind", "connection_env", "query"}, "SQL query source")
        require(isinstance(source.get("connection_env"), str) and re.fullmatch(r"ETL_SQL_[A-Z0-9_]+", source["connection_env"]), "Connection variable must start with ETL_SQL_")
        query_from_dict(source.get("query"))
    elif kind == "sqlserver":
        keys(source, {"kind", "connection_env", "schema", "table"}, "SQL Server source")
        require(isinstance(source.get("connection_env"), str) and re.fullmatch(r"ETL_SQL_[A-Z0-9_]+", source["connection_env"]), "Connection variable must start with ETL_SQL_")
        for key in ("schema", "table"):
            require(isinstance(source.get(key), str) and 0 < len(source[key]) <= 128 and "\x00" not in source[key], f"SQL {key} is required (maximum 128 characters)")
    else:
        raise ConfigError("Source kind must be csv, xml or sqlserver")


TYPES = {"string", "int", "decimal", "float", "bool", "date", "datetime"}
TRANSFORMS = {"trim", "upper", "lower", "empty_to_null", "linebreaks_to_space", "collapse_spaces"}


def destination_spec(destination, version):
    require(type(version) is int and version in (1, 2), "Pipeline version must be 1 or 2")
    require(isinstance(destination, dict), "destination must be an object")
    require(destination.get("kind") in {"csv", "xlsx", "xml", "sqlserver"}, "Destination must be csv, xlsx, xml or sqlserver")
    if destination["kind"] == "sqlserver":
        # A separate, deliberately small shape: no delimiter/encoding/null_value
        # concepts apply to a table, and split_by is refused by omission - one
        # pipeline claims one table, not several under a moving name.
        keys(destination, {"kind", "connection_env", "table"}, "destination")
        require(isinstance(destination.get("connection_env"), str) and re.fullmatch(r"ETL_SQL_[A-Z0-9_]+", destination["connection_env"]),
                "Connection variable must start with ETL_SQL_")
        if "table" in destination:
            require(isinstance(destination["table"], str) and destination["table"].strip() and len(destination["table"]) <= 100,
                    "table must be a non-empty string of at most 100 characters")
        return
    allowed = {"kind", "delimiter", "split_by"}
    if version == 2:
        allowed |= {"encoding", "null_value", "formula_policy", "binary_format"}
    keys(destination, allowed, "destination")
    delimiter = destination.get("delimiter", ";")
    require(isinstance(delimiter, str) and len(delimiter) == 1 and delimiter not in '\r\n\x00"', "Choose a single export delimiter")
    require(destination.get("encoding", "utf-8-sig") in ("utf-8", "utf-8-sig"), "Export encoding must be utf-8 or utf-8-sig")
    require(isinstance(destination.get("null_value", ""), str), "null_value must be a string (empty explicitly permits NULL/empty collapse)")
    require(destination.get("binary_format", "base64") in ("base64", "hex"), "binary_format must be base64 or hex")
    require(destination.get("formula_policy", "preserve") in ("preserve", "apostrophe"), "formula_policy must be preserve or apostrophe")
    if "split_by" in destination:
        require(isinstance(destination["split_by"], str) and destination["split_by"].strip() and len(destination["split_by"]) <= 128,
                "split_by must name an output column (maximum 128 characters)")


def validate(spec):
    keys(spec, {"version", "name", "source", "columns", "destination"}, "pipeline")
    require(type(spec.get("version")) is int and spec["version"] in (1, 2), "Pipeline version must be 1 or 2")
    require(isinstance(spec.get("name"), str) and 0 < len(spec["name"].strip()) <= 120, "Name must contain 1–120 characters")
    source_spec(spec.get("source"))
    columns = spec.get("columns")
    require(isinstance(columns, list) and 0 < len(columns) <= MAX_OUTPUT_COLUMNS, f"Choose 1–{MAX_OUTPUT_COLUMNS} output columns")
    names = set()
    for column in columns:
        keys(column, {"name", "source", "literal", "type", "transforms", "required", "max_length", "lookup"}, "column")
        name = column.get("name")
        require(isinstance(name, str) and name.strip() and len(name) <= 128, "Each column needs a name (maximum 128 characters)")
        require(name.casefold() not in names, f"Duplicate output name: {name}")
        names.add(name.casefold())
        require(("source" in column) != ("literal" in column), f"{name}: choose a source OR a literal")
        if "source" in column:
            require(isinstance(column["source"], str) and column["source"], f"{name}: source column is required")
        else:
            require(column["literal"] is None or type(column["literal"]) in (str, int, float, bool), f"{name}: literal must be a scalar")
        require(column.get("type", "string") in TYPES, f"{name}: unsupported type")
        transforms = column.get("transforms", [])
        require(isinstance(transforms, list) and all(isinstance(t, str) and t in TRANSFORMS for t in transforms), f"{name}: unsupported transform")
        require(type(column.get("required", False)) is bool, f"{name}: required must be true or false")
        if "max_length" in column:
            require(type(column["max_length"]) is int and column["max_length"] > 0, f"{name}: max_length must be positive")
        if "lookup" in column:
            lookup = column["lookup"]
            keys(lookup, {"source", "column"}, "lookup")
            source_spec(lookup.get("source"))
            require(lookup["source"]["kind"] != "sqlserver_query", "Query sources are not supported as lookup dependencies")
            require(isinstance(lookup.get("column"), str) and lookup["column"], f"{name}: lookup column required")
    destination = spec.get("destination")
    destination_spec(destination, spec["version"])
    if isinstance(destination, dict) and "split_by" in destination:
        require(destination["split_by"].casefold() in names, f"split_by ({destination['split_by']}) must name an existing output column")
    return spec
