"""Validate protocol parameters before constructing service requests."""

from aottg_server.adapters.photon.messages import Parameters, ProtocolError
from aottg_server.domain import values as v


class RequestParameters:
    def __init__(self, values: Parameters) -> None:
        self.values = values

    def text(self, key: int, default: str = "") -> str:
        value = self.values.get(key)
        if value is None:
            return default
        if isinstance(value, v.StringValue):
            return value.value
        raise ProtocolError(f"Parameter {key} must be a string")

    def integer(self, key: int, default: int = 0) -> int:
        value = self.values.get(key)
        if value is None:
            return default
        if isinstance(value, (v.ByteValue, v.ShortValue, v.IntegerValue, v.LongValue)):
            return value.value
        raise ProtocolError(f"Parameter {key} must be an integer")

    def flag(self, key: int, default: bool = False) -> bool:
        value = self.values.get(key)
        if value is None:
            return default
        if isinstance(value, v.BooleanValue):
            return value.value
        raise ProtocolError(f"Parameter {key} must be a boolean")

    def properties(self, key: int) -> v.Properties:
        value = self.values.get(key)
        if value is None:
            return {}
        if isinstance(value, v.PropertyTable):
            return dict(value.entries)
        raise ProtocolError(f"Parameter {key} must be a property table")

    def actor_ids(self, key: int) -> tuple[int, ...] | None:
        value = self.values.get(key)
        if value is None:
            return None
        if not isinstance(value, (v.TypedArray, v.ObjectArray)):
            raise ProtocolError(f"Parameter {key} must be an integer array")
        actors: list[int] = []
        for item in value.items:
            if not isinstance(item, v.IntegerValue):
                raise ProtocolError("Actor IDs must be 32-bit integers")
            actors.append(item.value)
        return tuple(actors)

    def bytes(self, key: int) -> bytes | None:
        value = self.values.get(key)
        if value is None:
            return None
        if isinstance(value, v.BytesValue):
            return value.value
        raise ProtocolError(f"Parameter {key} must be a byte array")
