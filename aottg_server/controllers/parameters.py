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

    def strings(self, key: int) -> tuple[str, ...]:
        value = self.values.get(key)
        if value is None or isinstance(value, v.NullValue):
            return ()
        if not isinstance(value, (v.StringArray, v.TypedArray, v.ObjectArray)):
            raise ProtocolError(f"Parameter {key} must be a string array")
        items: list[str] = []
        for item in value.items:
            if not isinstance(item, v.StringValue):
                raise ProtocolError(f"Parameter {key} must contain strings")
            items.append(item.value)
        return tuple(items)

    def join_or_create(self, key: int) -> bool:
        value = self.values.get(key)
        if isinstance(value, v.BooleanValue):
            return value.value
        mode = self.integer(key)
        if mode not in (0, 1):
            raise ProtocolError("Only join and join-or-create are supported by this RC server")
        return mode == 1

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
