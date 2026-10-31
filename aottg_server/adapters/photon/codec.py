"""Bounded GpBinaryV16 codec; no room or connection behavior belongs here."""

import struct
from typing import assert_never

from aottg_server.domain import values as v

from .codes import MessageType, ValueType
from .messages import EventMessage, OperationRequest, OperationResponse, Parameters, ProtocolError

MAX_NESTING = 32
MESSAGE_MAGIC = 0xF3


class BinaryReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._position

    def read(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise ProtocolError("Truncated value")
        start = self._position
        self._position += size
        return self._data[start : self._position]

    def unsigned_byte(self) -> int:
        return self.read(1)[0]

    def unsigned_short(self) -> int:
        return int.from_bytes(self.read(2), "big")

    def signed_integer(self, size: int) -> int:
        return int.from_bytes(self.read(size), "big", signed=True)

    def read_value(self, kind: int = ValueType.DYNAMIC, depth: int = 0) -> v.Value:
        if depth > MAX_NESTING:
            raise ProtocolError("Value nesting limit exceeded")
        if kind in (ValueType.DYNAMIC, ValueType.NULL):
            kind = self.unsigned_byte()
        match kind:
            case ValueType.NULL:
                return v.NullValue()
            case ValueType.BYTE:
                return v.ByteValue(self.unsigned_byte())
            case ValueType.BOOLEAN:
                value = self.unsigned_byte()
                if value not in (0, 1):
                    raise ProtocolError("Invalid boolean")
                return v.BooleanValue(bool(value))
            case ValueType.SHORT:
                return v.ShortValue(self.signed_integer(2))
            case ValueType.INTEGER:
                return v.IntegerValue(self.signed_integer(4))
            case ValueType.LONG:
                return v.LongValue(self.signed_integer(8))
            case ValueType.FLOAT:
                return v.FloatValue(float(struct.unpack(">f", self.read(4))[0]))
            case ValueType.DOUBLE:
                return v.DoubleValue(float(struct.unpack(">d", self.read(8))[0]))
            case ValueType.STRING:
                return v.StringValue(self.read(self.unsigned_short()).decode("utf-8"))
            case ValueType.BYTES:
                return v.BytesValue(self.read(self.signed_integer(4)))
            case ValueType.CUSTOM:
                type_code = self.unsigned_byte()
                return v.CustomValue(type_code, self.read(self.unsigned_short()))
            case ValueType.TABLE:
                entries: v.Properties = {}
                for _ in range(self.unsigned_short()):
                    key = self.read_value(depth=depth + 1)
                    if not isinstance(
                        key, (v.ByteValue, v.ShortValue, v.IntegerValue, v.LongValue, v.StringValue)
                    ):
                        raise ProtocolError("Unsupported property key type")
                    entries[key] = self.read_value(depth=depth + 1)
                return v.PropertyTable(entries)
            case ValueType.OBJECT_ARRAY:
                return v.ObjectArray(
                    tuple(self.read_value(depth=depth + 1) for _ in range(self.unsigned_short()))
                )
            case ValueType.STRING_ARRAY:
                return v.StringArray(
                    tuple(
                        v.StringValue(self.read(self.unsigned_short()).decode("utf-8"))
                        for _ in range(self.unsigned_short())
                    )
                )
            case ValueType.ARRAY:
                count = self.unsigned_short()
                element_type = self.unsigned_byte()
                if element_type == ValueType.CUSTOM:
                    custom_type = self.unsigned_byte()
                    return v.TypedArray(
                        element_type,
                        tuple(
                            v.CustomValue(custom_type, self.read(self.unsigned_short()))
                            for _ in range(count)
                        ),
                        custom_type,
                    )
                return v.TypedArray(
                    element_type,
                    tuple(self.read_value(element_type, depth + 1) for _ in range(count)),
                )
            case ValueType.DICTIONARY:
                key_type, value_type = self.unsigned_byte(), self.unsigned_byte()
                return v.DictionaryValue(
                    key_type,
                    value_type,
                    tuple(
                        (
                            self.read_value(key_type, depth + 1),
                            self.read_value(value_type, depth + 1),
                        )
                        for _ in range(self.unsigned_short())
                    ),
                )
            case _:
                raise ProtocolError(f"Unsupported value type: {kind}")

    def read_parameters(self) -> Parameters:
        result: Parameters = {}
        for _ in range(self.unsigned_short()):
            key = self.unsigned_byte()
            result[key] = self.read_value()
        return result


def encode_value(value: v.Value, *, tagged: bool = True) -> bytes:
    kind: ValueType
    body: bytes
    match value:
        case v.NullValue():
            kind, body = ValueType.NULL, b""
        case v.ByteValue(number):
            kind, body = ValueType.BYTE, number.to_bytes(1, "big")
        case v.BooleanValue(flag):
            kind, body = ValueType.BOOLEAN, bytes([flag])
        case v.ShortValue(number):
            kind, body = ValueType.SHORT, number.to_bytes(2, "big", signed=True)
        case v.IntegerValue(number):
            kind, body = ValueType.INTEGER, number.to_bytes(4, "big", signed=True)
        case v.LongValue(number):
            kind, body = ValueType.LONG, number.to_bytes(8, "big", signed=True)
        case v.FloatValue(number):
            kind, body = ValueType.FLOAT, struct.pack(">f", number)
        case v.DoubleValue(number):
            kind, body = ValueType.DOUBLE, struct.pack(">d", number)
        case v.StringValue(text):
            raw = text.encode("utf-8")
            kind, body = ValueType.STRING, len(raw).to_bytes(2, "big") + raw
        case v.BytesValue(raw):
            kind, body = ValueType.BYTES, len(raw).to_bytes(4, "big") + raw
        case v.CustomValue(custom_type, raw):
            kind = ValueType.CUSTOM
            body = bytes([custom_type]) + len(raw).to_bytes(2, "big") + raw
        case v.PropertyTable(entries):
            kind = ValueType.TABLE
            body = len(entries).to_bytes(2, "big") + b"".join(
                encode_value(key) + encode_value(item) for key, item in entries.items()
            )
        case v.ObjectArray(items):
            kind = ValueType.OBJECT_ARRAY
            body = len(items).to_bytes(2, "big") + b"".join(encode_value(item) for item in items)
        case v.StringArray(items):
            kind = ValueType.STRING_ARRAY
            body = len(items).to_bytes(2, "big") + b"".join(
                encode_value(item, tagged=False) for item in items
            )
        case v.TypedArray(element_type, items, custom_type):
            kind = ValueType.ARRAY
            body = len(items).to_bytes(2, "big") + bytes([element_type])
            if element_type == ValueType.CUSTOM:
                if custom_type is None:
                    raise ProtocolError("Custom array requires a type code")
                body += bytes([custom_type])
                for item in items:
                    if not isinstance(item, v.CustomValue) or item.type_code != custom_type:
                        raise ProtocolError("Custom array element type mismatch")
                    body += len(item.payload).to_bytes(2, "big") + item.payload
            else:
                body += b"".join(
                    encode_value(item, tagged=element_type in (ValueType.DYNAMIC, ValueType.NULL))
                    for item in items
                )
        case v.DictionaryValue(key_type, value_type, pairs):
            kind = ValueType.DICTIONARY
            body = bytes([key_type, value_type]) + len(pairs).to_bytes(2, "big")
            body += b"".join(
                encode_value(key, tagged=key_type in (ValueType.DYNAMIC, ValueType.NULL))
                + encode_value(item, tagged=value_type in (ValueType.DYNAMIC, ValueType.NULL))
                for key, item in pairs
            )
        case _:
            assert_never(value)
    return (bytes([kind]) if tagged else b"") + body


def encode_parameters(parameters: Parameters) -> bytes:
    return len(parameters).to_bytes(2, "big") + b"".join(
        bytes([key]) + encode_value(value) for key, value in parameters.items()
    )


def decode_request(payload: bytes) -> OperationRequest:
    reader = BinaryReader(payload)
    if reader.unsigned_byte() != MESSAGE_MAGIC:
        raise ProtocolError("Invalid message magic")
    message_type = reader.unsigned_byte()
    if message_type not in (MessageType.OPERATION, MessageType.INTERNAL_OPERATION):
        raise ProtocolError("Expected an operation request")
    code = reader.unsigned_byte()
    parameters = reader.read_parameters()
    if reader.remaining:
        raise ProtocolError("Trailing operation data")
    return OperationRequest(code, parameters, message_type == MessageType.INTERNAL_OPERATION)


def encode_response(response: OperationResponse) -> bytes:
    message_type = MessageType.INTERNAL_RESPONSE if response.internal else MessageType.RESPONSE
    debug: v.Value = v.NullValue() if response.message is None else v.StringValue(response.message)
    return (
        bytes([MESSAGE_MAGIC, message_type, response.code])
        + response.return_code.to_bytes(2, "big", signed=True)
        + encode_value(debug)
        + encode_parameters(response.parameters)
    )


def encode_event(event: EventMessage) -> bytes:
    return bytes([MESSAGE_MAGIC, MessageType.EVENT, event.code]) + encode_parameters(
        event.parameters
    )
