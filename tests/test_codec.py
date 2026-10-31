"""Wire contracts must retain types when the server relays client-owned data."""

import pytest

from aottg_server.adapters.photon.codec import BinaryReader, decode_request, encode_value
from aottg_server.adapters.photon.codes import ValueType
from aottg_server.adapters.photon.messages import ProtocolError
from aottg_server.domain import values as v


@pytest.mark.parametrize(
    "value",
    [
        v.NullValue(),
        v.ByteValue(255),
        v.ShortValue(-123),
        v.IntegerValue(100000),
        v.LongValue(2**40),
        v.BooleanValue(True),
        v.FloatValue(1.5),
        v.DoubleValue(-3.25),
        v.StringValue("日本語"),
        v.BytesValue(b"\x00\xff"),
        v.CustomValue(86, bytes(12)),
        v.PropertyTable(
            {v.ByteValue(1): v.StringValue("rpc"), v.StringValue("id"): v.IntegerValue(3)}
        ),
        v.ObjectArray((v.NullValue(), v.StringValue("spawn"), v.CustomValue(86, bytes(12)))),
        v.StringArray((v.StringValue("one"), v.StringValue("two"))),
        v.TypedArray(ValueType.INTEGER, (v.IntegerValue(1), v.IntegerValue(2))),
        v.TypedArray(ValueType.CUSTOM, (v.CustomValue(86, bytes(12)),), 86),
        v.DictionaryValue(
            ValueType.STRING,
            ValueType.DYNAMIC,
            (
                (v.StringValue("position"), v.CustomValue(86, bytes(12))),
                (v.StringValue("health"), v.IntegerValue(100)),
            ),
        ),
    ],
)
def test_relay_preserves_value_type_and_content(value: v.Value) -> None:
    reader = BinaryReader(encode_value(value))
    assert reader.read_value() == value
    assert reader.remaining == 0


def test_authentication_wire_contract() -> None:
    request = decode_request(bytes.fromhex("f302e60002dc730003726331e17300034a6f65"))
    assert request.code == 230
    assert request.parameters == {220: v.StringValue("rc1"), 225: v.StringValue("Joe")}


@pytest.mark.parametrize("payload", [b"", b"\xf3", b"\xf3\x02\xe6\x00", b"\xf3\x04\x01\x00\x00"])
def test_rejects_invalid_operation_framing(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_request(payload)


@pytest.mark.parametrize("payload", [b"x\xff\xff\xff\xff", b"s\x00\x05abc", b"o\x02"])
def test_rejects_invalid_value_lengths_and_boolean(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        BinaryReader(payload).read_value()


def test_rejects_excessive_nesting() -> None:
    payload = b"z\x00\x01" * 40 + b"*"
    with pytest.raises(ProtocolError):
        BinaryReader(payload).read_value()


def test_legacy_array_wire_tags_match_the_shipped_sdk() -> None:
    assert encode_value(v.StringArray((v.StringValue("RC"),))) == bytes.fromhex("61000100025243")
    reader = BinaryReader(bytes.fromhex("6e0000000200000001ffffffff"))
    assert reader.read_value() == v.TypedArray(
        ValueType.INTEGER, (v.IntegerValue(1), v.IntegerValue(-1))
    )
    assert reader.remaining == 0
