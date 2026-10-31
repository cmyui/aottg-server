"""Typed values carried by game properties and events.

Integer widths and collection element types must survive a relay unchanged:
Unity clients distinguish a byte-keyed property from an integer-keyed property.
"""

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True)
class NullValue:
    pass


@dataclass(frozen=True)
class ByteValue:
    value: int


@dataclass(frozen=True)
class BooleanValue:
    value: bool


@dataclass(frozen=True)
class ShortValue:
    value: int


@dataclass(frozen=True)
class IntegerValue:
    value: int


@dataclass(frozen=True)
class LongValue:
    value: int


@dataclass(frozen=True)
class FloatValue:
    value: float


@dataclass(frozen=True)
class DoubleValue:
    value: float


@dataclass(frozen=True)
class StringValue:
    value: str


@dataclass(frozen=True)
class BytesValue:
    value: bytes


@dataclass(frozen=True)
class CustomValue:
    type_code: int
    payload: bytes


ScalarValue: TypeAlias = (
    NullValue
    | ByteValue
    | BooleanValue
    | ShortValue
    | IntegerValue
    | LongValue
    | FloatValue
    | DoubleValue
    | StringValue
)
PropertyKey: TypeAlias = ByteValue | ShortValue | IntegerValue | LongValue | StringValue


@dataclass(frozen=True)
class PropertyTable:
    entries: dict[PropertyKey, "Value"]


@dataclass(frozen=True)
class ObjectArray:
    items: tuple["Value", ...]


@dataclass(frozen=True)
class StringArray:
    items: tuple[StringValue, ...]


@dataclass(frozen=True)
class TypedArray:
    element_type: int
    items: tuple["Value", ...]
    custom_type: int | None = None


@dataclass(frozen=True)
class DictionaryValue:
    key_type: int
    value_type: int
    entries: tuple[tuple["Value", "Value"], ...]


Value: TypeAlias = (
    ScalarValue
    | BytesValue
    | CustomValue
    | PropertyTable
    | ObjectArray
    | StringArray
    | TypedArray
    | DictionaryValue
)
Properties: TypeAlias = dict[PropertyKey, Value]
