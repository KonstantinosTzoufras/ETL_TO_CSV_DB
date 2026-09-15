"""Only explicit transforms; callers supply their configured order."""
from .conversions import text
from .spec import ConfigError


def apply_transform(value, operation, *, version):
    if operation not in {"trim", "upper", "lower", "empty_to_null"}:
        raise ConfigError(f"Unsupported transform: {operation}")
    if operation == "empty_to_null":
        return None if value == "" else value
    if value is None:
        return None
    if version == 2 and not isinstance(value, str):
        raise ValueError(f"{operation} requires text")
    return {"trim": str.strip, "upper": str.upper, "lower": str.lower}[operation](text(value))


def apply_transforms(value, operations, *, version):
    for operation in operations:
        value = apply_transform(value, operation, version=version)
    return value
