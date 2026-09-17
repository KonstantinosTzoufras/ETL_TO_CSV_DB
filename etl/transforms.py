"""Only explicit transforms; callers supply their configured order."""
from .conversions import text
from .spec import ConfigError


# Bound once: these tables were rebuilt on every value in the hot path.
_STRING_OPS = {"trim": str.strip, "upper": str.upper, "lower": str.lower}
_OPERATIONS = frozenset(_STRING_OPS) | {"empty_to_null"}


def apply_transform(value, operation, *, version):
    if operation not in _OPERATIONS:
        raise ConfigError(f"Unsupported transform: {operation}")
    if operation == "empty_to_null":
        return None if value == "" else value
    if value is None:
        return None
    if version == 2:
        if not isinstance(value, str):
            raise ValueError(f"{operation} requires text")
        return _STRING_OPS[operation](value)  # text() is the identity here.
    return _STRING_OPS[operation](text(value))


def apply_transforms(value, operations, *, version):
    for operation in operations:
        value = apply_transform(value, operation, version=version)
    return value
