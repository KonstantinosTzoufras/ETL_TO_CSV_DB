"""Value-preserving field validations; dependency order belongs to the engine."""
from .conversions import text
from .models import FieldError


def required_error(field, value):
    if value is None or not text(value).strip():
        return FieldError(field, "required", "required value is missing", "required")
    return None


def max_length_error(field, value, maximum):
    if len(text(value)) > maximum:
        return FieldError(field, "max_length_exceeded", f"exceeds {maximum} characters", "max_length")
    return None


def lookup_error(field, value, allowed):
    if value is not None and value not in allowed:
        return FieldError(field, "lookup_missing", "value is absent from lookup", "lookup")
    return None
