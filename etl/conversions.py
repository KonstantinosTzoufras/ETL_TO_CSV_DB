"""Explicit target-type conversion. No field validation or diagnostic formatting."""
import math
import re
from datetime import date, datetime
from decimal import Decimal

from .spec import ConfigError


def text(value):
    """Existing scalar-to-text policy, also used by the legacy output boundary."""
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def convert_value(value, kind, *, version):
    if version not in (1, 2):
        raise ConfigError("Pipeline version must be 1 or 2")
    if value is None or (version == 1 and value == ""):
        return None
    if version == 2 and kind == "decimal" and isinstance(value, float):
        raise ValueError("decimal conversion requires text, integer or Decimal; float precision cannot be recovered")
    raw = value
    value = text(value)
    if kind == "string":
        # Bytes have no faithful text form, and str() would emit a Python repr.
        # Version 2 hands them to the exporter, which owns their representation
        # (base64:) exactly as diagnostics already tag them. V1 is unchanged.
        return raw if version == 2 and type(raw) is bytes else value
    if kind == "int":
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value) or not -(2**63) <= int(value) < 2**63:
            raise ValueError("expected a 64-bit integer without leading zeros")
        return int(value)
    if kind in {"decimal", "float"}:
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
            raise ValueError("expected a number using a decimal point")
        if kind == "decimal":
            number = Decimal(raw) if version == 2 and type(raw) in (str, int, Decimal) else Decimal(value)
        else:
            number = float(value)
        if not (number.is_finite() if isinstance(number, Decimal) else math.isfinite(number)):
            raise ValueError("number must be finite")
        return number
    if kind == "bool":
        if value.lower() not in {"0", "1", "true", "false"}:
            raise ValueError("expected 0, 1, true or false")
        return value.lower() in {"1", "true"}
    if kind == "date":
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("expected YYYY-MM-DD")
        return date.fromisoformat(value)
    if kind == "datetime":
        if not re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]", value):
            raise ValueError("expected an ISO date and time")
        return datetime.fromisoformat(value)
    raise ConfigError(f"Unsupported type: {kind}")
