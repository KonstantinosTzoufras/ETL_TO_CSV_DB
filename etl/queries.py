"""Immutable query codec and fail-closed SQL Server query safety policy.

SQL is parsed, never rewritten. Credentials and approvals are deployment state,
not pipeline options. This is defense in depth, not a database permission sandbox.
"""
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlglot import exp
from sqlglot.dialects.tsql import TSQL
from sqlglot.errors import ErrorLevel

from .models import QueryDefinition, QueryParameter
from .spec import ConfigError


class QueryError(ConfigError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check(condition, code, message):
    if not condition:
        raise QueryError(code, message)


def exact(value, names, label):
    check(isinstance(value, dict) and set(value) == set(names),
          "QUERY_INVALID", f"{label}: unexpected or missing options")


# No fallback Command nodes or parser warning text (which can include SQL).
class _Parser(TSQL.Parser):
    def _warn_unsupported(self):
        raise QueryError("QUERY_UNSUPPORTED", "Query syntax is unsupported")

    def _parse_command(self):
        raise QueryError("QUERY_UNSUPPORTED", "Only the supported SELECT subset is allowed")


# Both node classes and populated arguments are allowlisted. A new parser feature
# cannot become executable merely because its parent happens to be a Select.
_ALLOWED = {
    "Select": "expressions from_ joins where group having order distinct limit",
    "From": "this", "Table": "this db alias", "TableAlias": "this",
    "Identifier": "this quoted", "Column": "this table",
    "Alias": "this alias", "Subquery": "this alias",
    "Join": "this side kind on", "Where": "this", "Having": "this",
    "Group": "expressions", "Order": "expressions", "Ordered": "this desc nulls_first",
    "Distinct": "", "Limit": "expression", "Star": "", "Placeholder": "",
    "Literal": "this is_string", "National": "this", "Null": "",
    "Paren": "this", "Neg": "this", "Not": "this",
    "Between": "this low high", "In": "this expressions query", "Exists": "this",
    "Case": "this ifs default", "If": "this true false",
    "Coalesce": "this expressions is_nvl is_null", "Nullif": "this expression",
    "Upper": "this", "Lower": "this", "Length": "this",
    # TRIM, LTRIM and RTRIM all parse to Trim; position and expression carry
    # the LEADING/TRAILING/BOTH keyword and an optional character literal.
    "Trim": "this position expression",
    "Abs": "this", "Round": "this decimals truncate",
    "Sum": "this", "Avg": "this", "Min": "this expressions", "Max": "this expressions",
    "Count": "this expressions big_int",
    "Cast": "this to", "DataType": "this expressions nested", "DataTypeParam": "this",
}
for _name in ("Add", "Sub", "Mul", "Div", "Mod", "EQ", "NEQ", "GT", "GTE", "LT", "LTE", "And", "Or", "Is", "Like"):
    _ALLOWED[_name] = "this expression"
_ALLOWED = {name: set(args.split()) for name, args in _ALLOWED.items()}


def validate_sql(sql):
    """Return positional marker count and directly referenced schema/object pairs."""
    check(isinstance(sql, str) and 0 < len(sql) <= 32768 and "\x00" not in sql,
          "QUERY_INVALID", "SQL must contain 1–32768 characters without NUL")
    try:
        tokens = TSQL().tokenize(sql)
        # Semicolons inside strings/comments are not separator tokens.
        separators = [i for i, token in enumerate(tokens) if token.token_type.name == "SEMICOLON"]
        check(not separators or separators == [len(tokens) - 1], "QUERY_INVALID", "Exactly one SELECT statement is allowed")
        check(tokens and tokens[0].token_type.name == "SELECT", "QUERY_UNSUPPORTED", "The statement must start with SELECT (comments allowed)")
        # Block execution constructs even if a parser version consumes them as
        # aliases or drops syntax. Quoted identifiers and strings are exempt.
        forbidden = {"INTO", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "EXEC", "EXECUTE", "GO", "WITH", "OPTION", "FOR", "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "NEXT", "USE", "SET", "DECLARE", "WAITFOR", "UNION", "INTERSECT", "EXCEPT", "APPLY"}
        for token in tokens:
            if token.token_type.name not in {"STRING", "NATIONAL_STRING", "IDENTIFIER"}:
                check(token.text.upper() not in forbidden, "QUERY_UNSUPPORTED", "Query contains unsupported SQL syntax")
        trees = _Parser(error_level=ErrorLevel.RAISE).parse(tokens[:-1] if separators else tokens, sql)
        check(len(trees) == 1 and type(trees[0]) is exp.Select, "QUERY_UNSUPPORTED", "Exactly one SELECT is required")
        tables = set()
        placeholders = 0
        nodes = list(trees[0].walk())
        check(len(nodes) <= 4096, "QUERY_UNSUPPORTED", "Query exceeds the syntax complexity limit")
        for node in nodes:
            allowed = _ALLOWED.get(type(node).__name__)
            check(allowed is not None, "QUERY_UNSUPPORTED", "Query contains an unsupported expression")
            populated = {k for k, v in node.args.items() if v is not None and v is not False and v != []}
            check(not populated - allowed, "QUERY_UNSUPPORTED", "Query contains unsupported expression options")
            if isinstance(node, exp.Identifier):
                check(isinstance(node.this, str) and 0 < len(node.this) <= 128 and not node.this.startswith(("#", "@")),
                      "QUERY_UNSUPPORTED", "Temporary objects and variables are not allowed")
            if type(node) is exp.DataType:
                check(node.this.value in {"INT", "BIGINT", "DECIMAL", "FLOAT", "DOUBLE", "BOOLEAN", "DATE", "DATETIME", "DATETIME2", "TEXT", "VARCHAR", "NVARCHAR"},
                      "QUERY_UNSUPPORTED", "Only supported scalar CAST types are allowed")
            if type(node) is exp.Table:
                check(type(node.this) is exp.Identifier and type(node.args.get("db")) is exp.Identifier,
                      "QUERY_UNSUPPORTED", "Use local schema-qualified tables/views only")
                tables.add((node.db, node.name))
            if type(node) is exp.Join:
                check(node.args.get("side", "") in ("", "LEFT", "RIGHT", "FULL") and node.args.get("kind", "") in ("", "INNER", "OUTER", "CROSS"),
                      "QUERY_UNSUPPORTED", "Unsupported join")
            if type(node) is exp.Placeholder:
                placeholders += 1
        check(placeholders == sum(t.token_type.name == "PLACEHOLDER" and t.text == "?" for t in tokens),
              "QUERY_UNSUPPORTED", "Only positional question-mark parameters are supported")
        return placeholders, tuple(sorted(tables))
    except QueryError:
        raise
    except Exception:
        # Parse/token errors may contain SQL and sensitive inline literals.
        raise QueryError("QUERY_INVALID", "SQL cannot be parsed within the supported T-SQL subset") from None


def parameter_value(parameter):
    """Validate and decode a bound scalar, preserving the definition spelling."""
    check(isinstance(parameter, QueryParameter), "PARAMETER_INVALID", "Expected QueryParameter")
    check(isinstance(parameter.name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", parameter.name),
          "PARAMETER_INVALID", "Parameter labels must be identifiers of 1–64 characters")
    kind, value = parameter.type, parameter.value
    check(isinstance(kind, str) and kind in {"string", "int", "decimal", "bool", "date", "datetime"},
          "PARAMETER_INVALID", "Unsupported parameter type")
    if value is None:
        return None
    try:
        if kind == "string":
            check(type(value) is str and len(value) <= 32768, "PARAMETER_INVALID", "String parameter must be text of at most 32768 characters")
            value.encode("utf-16-le")  # Reject invalid surrogate text before connecting.
            return value
        if kind == "bool":
            check(type(value) is bool, "PARAMETER_INVALID", "Boolean parameter requires true or false")
            return value
        if kind == "int":
            check(type(value) is int or type(value) is str and re.fullmatch(r"-?(0|[1-9][0-9]*)", value),
                  "PARAMETER_INVALID", "Integer parameter requires a canonical integer")
            check(type(value) is not int or abs(value) <= 9007199254740991, "PARAMETER_INVALID", "Large integer parameters must be JSON strings")
            result = int(value)
            check(-(2**63) <= result < 2**63, "PARAMETER_INVALID", "Integer parameter exceeds SQL bigint range")
            return result
        check(type(value) is str and len(value) <= 100, "PARAMETER_INVALID", "Decimal/date/datetime parameters require canonical strings")
        if kind == "decimal":
            check(re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value), "PARAMETER_INVALID", "Decimal parameter requires plain decimal text")
            result = Decimal(value)
            check(max(len(result.as_tuple().digits), -result.as_tuple().exponent) <= 38, "PARAMETER_INVALID", "Decimal parameter exceeds SQL precision 38")
            return result
        if kind == "date":
            check(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "PARAMETER_INVALID", "Date parameter requires YYYY-MM-DD")
            return date.fromisoformat(value)
        check(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?", value),
              "PARAMETER_INVALID", "Datetime parameter requires timezone-naive ISO text with seconds")
        return datetime.fromisoformat(value)
    except QueryError:
        raise
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise QueryError("PARAMETER_INVALID", "Parameter value is invalid for its declared type") from None


def query_from_dict(value):
    exact(value, {"format_version", "dialect", "sql", "parameters", "timeout_seconds"}, "Query")
    check(type(value["format_version"]) is int and value["format_version"] == 1 and value["dialect"] == "tsql", "QUERY_INVALID", "Supported query format is version 1 / tsql")
    check(type(value["timeout_seconds"]) is int and 1 <= value["timeout_seconds"] <= 300, "QUERY_INVALID", "Query timeout must be 1–300 seconds")
    check(isinstance(value["parameters"], list) and len(value["parameters"]) <= 256, "PARAMETER_INVALID", "Parameters must be an ordered list of at most 256 scalars")
    parameters = []
    for item in value["parameters"]:
        exact(item, {"name", "type", "value"}, "Parameter")
        check(item["value"] is None or type(item["value"]) in (str, int, bool), "PARAMETER_INVALID", "Parameter value must be a supported scalar")
        parameter = QueryParameter(**item)
        parameter_value(parameter)
        parameters.append(parameter)
    count, _ = validate_sql(value["sql"])
    check(count == len(parameters), "PARAMETER_COUNT_MISMATCH", "SQL marker count must equal the ordered parameter count")
    return QueryDefinition(value["format_version"], value["dialect"], value["sql"], tuple(parameters), value["timeout_seconds"])


def query_to_dict(query):
    check(isinstance(query, QueryDefinition), "QUERY_INVALID", "Expected QueryDefinition")
    check(all(isinstance(p, QueryParameter) for p in query.parameters), "PARAMETER_INVALID", "Expected QueryParameter values")
    result = {"format_version": query.format_version, "dialect": query.dialect, "sql": query.sql,
              "parameters": [{"name": p.name, "type": p.type, "value": p.value} for p in query.parameters],
              "timeout_seconds": query.timeout_seconds}
    query_from_dict(result)  # Revalidate even manually constructed models.
    return result


def connection_policies():
    """Administrator-owned process configuration, never supplied by HTTP/spec."""
    try:
        policies = json.loads(os.environ.get("ETL_QUERY_CONNECTIONS", "{}"))
        check(isinstance(policies, dict), "QUERY_POLICY_INVALID", "Invalid query connection policy")
        for reference, policy in policies.items():
            check(re.fullmatch(r"ETL_SQL_[A-Z0-9_]+", reference), "QUERY_POLICY_INVALID", "Invalid query connection policy")
            exact(policy, {"read_only", "objects", "max_timeout_seconds"}, "Connection policy")
            check(policy["read_only"] is True and type(policy["max_timeout_seconds"]) is int and 1 <= policy["max_timeout_seconds"] <= 300,
                  "QUERY_POLICY_INVALID", "Policy requires read-only attestation and a bounded timeout")
            check(isinstance(policy["objects"], list) and all(isinstance(pair, list) and len(pair) == 2 and all(isinstance(s, str) and s for s in pair) for pair in policy["objects"]),
                  "QUERY_POLICY_INVALID", "Policy objects must be [schema, name] pairs")
        return policies
    except QueryError:
        raise
    except (ValueError, TypeError):
        raise QueryError("QUERY_POLICY_INVALID", "Invalid query connection policy") from None


def authorize(reference, query):
    query_to_dict(query)
    policy = connection_policies().get(reference)
    check(policy is not None, "QUERY_PERMISSION_DENIED", "Connection is not approved for query sources")
    check(query.timeout_seconds <= policy["max_timeout_seconds"], "QUERY_PERMISSION_DENIED", "Query timeout exceeds this connection's approved limit")
    _, tables = validate_sql(query.sql)
    # Exact spelling is conservative for databases with case-sensitive names.
    check(set(tables) <= {tuple(pair) for pair in policy["objects"]}, "QUERY_PERMISSION_DENIED", "Query references an object outside the approved set")
    return query
