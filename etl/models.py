"""Small immutable domain values; no source, UI, persistence or export logic.

The definition codec owns validation. Models preserve omitted options rather
than inserting defaults. RowResult retains processing stages for both versions;
the engine projects version-1 results into its historical dictionary API.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from uuid import UUID


class _Unset(Enum):
    VALUE = "omitted"


UNSET = _Unset.VALUE  # A literal of None is a value, not an omitted literal.


# Exact types, so a hit here can never also be a Mapping, sequence or model.
_SCALARS = frozenset({str, int, float, bool, type(None), bytes, Decimal, date, datetime, time, UUID})


def _freeze(value):
    """Copy containers before freezing: never expose a caller-owned backing map."""
    # Scalars dominate real rows; testing them first skips an abstract-base
    # isinstance per value. Exact type matching keeps this order-independent.
    if type(value) in _SCALARS or value is UNSET:
        return value
    if isinstance(value, _Immutable):
        return value
    if isinstance(value, Mapping):
        # One walk: checking keys separately doubled the per-row iteration.
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Domain mappings require string keys")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"Unsupported domain value: {type(value).__name__}")


class _Immutable:
    __slots__ = ()

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, _freeze(getattr(self, item.name)))


@dataclass(frozen=True, slots=True)
class QueryParameter(_Immutable):
    name: str
    type: str
    value: str | int | bool | None


@dataclass(frozen=True, slots=True)
class QueryDefinition(_Immutable):
    format_version: int
    dialect: str
    sql: str
    parameters: tuple[QueryParameter, ...]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class SourceDefinition(_Immutable):
    kind: str
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Transform(_Immutable):
    name: str


@dataclass(frozen=True, slots=True)
class ValidationRule(_Immutable):
    name: str
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FieldMapping(_Immutable):
    name: str
    source: str | None = None
    literal: str | int | float | bool | None | _Unset = UNSET
    target_type: str | None = None  # None means the v1 key was omitted.
    transforms: tuple[Transform, ...] | None = None  # None != explicit [].
    validations: tuple[ValidationRule, ...] = ()

    def __post_init__(self):
        _Immutable.__post_init__(self)
        if (self.source is not None) == (self.literal is not UNSET):
            raise ValueError("A field mapping requires exactly one source or literal")


@dataclass(frozen=True, slots=True)
class Pipeline(_Immutable):
    name: str
    source: SourceDefinition
    columns: tuple[FieldMapping, ...]
    destination: Mapping[str, object]
    version: int = 1


@dataclass(frozen=True, slots=True)
class QueryExportStep(_Immutable):
    id: str
    name: str
    query: QueryDefinition
    columns: tuple[FieldMapping, ...]
    destination: Mapping[str, object]
    processing_version: int = 2


@dataclass(frozen=True, slots=True)
class OrderedQueryPipeline(_Immutable):
    name: str
    connection_env: str
    steps: tuple[QueryExportStep, ...]
    failure_policy: str = "stop"
    format_version: int = 1
    max_parallel_steps: int = 1


@dataclass(frozen=True, slots=True)
class SourceColumn(_Immutable):
    name: str
    native_type: str | None = None
    nullable: bool | None = None
    display_size: int | None = None
    internal_size: int | None = None
    precision: int | None = None
    scale: int | None = None


@dataclass(frozen=True, slots=True)
class SourceRow(_Immutable):
    number: int
    values: Mapping[str, object]
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class FieldError(_Immutable):
    field: str
    code: str
    message: str
    stage: str


@dataclass(frozen=True, slots=True)
class RowResult(_Immutable):
    source: SourceRow
    transformed_values: Mapping[str, object]
    converted_values: Mapping[str, object]
    errors: tuple[FieldError, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def original_values(self) -> Mapping[str, object]:
        return self.source.values


@dataclass(frozen=True, slots=True)
class PipelineRun(_Immutable):
    id: str
    pipeline: Pipeline  # The captured definition, not a live saved-pipeline ID.
    status: str
    started: str
    finished: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult(_Immutable):
    processed: int
    valid: int
    invalid: int
    sample: tuple[RowResult, ...] = ()
    preview: bool = False
    directory: str | None = None
