"""Only explicit transforms; callers supply their configured order."""
import re

from .conversions import text
from .spec import ConfigError


def _linebreaks_to_space(value):
    # CRLF first, as one line break, not two spaces.
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


_SPACE_RUN = re.compile(r"[ \t]+")
# Real text: control characters this specific enough never appear in it, so
# they round-trip exactly - unlike a plausible "word", which risks colliding
# with real content.
_LINEBREAK_SENTINEL = "\x00LB\x00"


def _collapse_spaces(value):
    """Squeeze/trim run-of-the-mill spacing without disturbing embedded line
    breaks: a naive `\\s+` collapse would eat a real line break together with
    the spaces around it. Line breaks are parked behind a sentinel for the
    collapse, then restored exactly where they were.
    """
    if _LINEBREAK_SENTINEL in value:
        raise ValueError("collapse_spaces requires text without NUL bytes")
    protected = value.replace("\r\n", _LINEBREAK_SENTINEL).replace("\r", _LINEBREAK_SENTINEL).replace("\n", _LINEBREAK_SENTINEL)
    collapsed = _SPACE_RUN.sub(" ", protected).strip(" \t")
    return collapsed.replace(_LINEBREAK_SENTINEL, "\n")


# Bound once: these tables were rebuilt on every value in the hot path.
_STRING_OPS = {"trim": str.strip, "upper": str.upper, "lower": str.lower,
               "linebreaks_to_space": _linebreaks_to_space, "collapse_spaces": _collapse_spaces}
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
