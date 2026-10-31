"""Translate reserved Photon properties into domain room settings."""

from aottg_server.adapters.photon.codes import RoomProperty as RP
from aottg_server.adapters.photon.messages import ProtocolError
from aottg_server.domain import values as v
from aottg_server.domain.models import CreateRoom, Room, RoomChanges


def integer_property(properties: v.Properties, key: RP, default: int) -> int:
    value = properties.get(v.ByteValue(key))
    if value is None:
        return default
    if isinstance(value, (v.ByteValue, v.IntegerValue)):
        return value.value
    raise ProtocolError(f"Room property {key.name} must be an integer")


def boolean_property(properties: v.Properties, key: RP, default: bool) -> bool:
    value = properties.get(v.ByteValue(key))
    if value is None:
        return default
    if isinstance(value, v.BooleanValue):
        return value.value
    raise ProtocolError(f"Room property {key.name} must be a boolean")


def custom_properties(properties: v.Properties) -> v.Properties:
    reserved = {v.ByteValue(key) for key in RP}
    return {key: value for key, value in properties.items() if key not in reserved}


def create_request(name: str, lobby: str, properties: v.Properties) -> CreateRoom:
    return CreateRoom(
        name=name,
        lobby_name=lobby,
        max_players=integer_property(properties, RP.MAX_PLAYERS, 10),
        is_open=boolean_property(properties, RP.IS_OPEN, True),
        is_visible=boolean_property(properties, RP.IS_VISIBLE, True),
        cleanup_cache_on_leave=boolean_property(properties, RP.CLEANUP_CACHE_ON_LEAVE, True),
        properties=custom_properties(properties),
    )


def room_changes(properties: v.Properties) -> RoomChanges:
    maximum = integer_property(properties, RP.MAX_PLAYERS, 0)
    if not 0 <= maximum <= 255:
        raise ProtocolError("Room capacity is outside the byte range")
    return RoomChanges(
        max_players=maximum if v.ByteValue(RP.MAX_PLAYERS) in properties else None,
        is_open=boolean_property(properties, RP.IS_OPEN, True)
        if v.ByteValue(RP.IS_OPEN) in properties
        else None,
        is_visible=boolean_property(properties, RP.IS_VISIBLE, True)
        if v.ByteValue(RP.IS_VISIBLE) in properties
        else None,
        master_actor_id=integer_property(properties, RP.MASTER_CLIENT_ID, 0)
        if v.ByteValue(RP.MASTER_CLIENT_ID) in properties
        else None,
    )


def room_properties(room: Room, *, removed: bool = False) -> v.PropertyTable:
    properties = dict(room.properties)
    properties.update(
        {
            v.ByteValue(RP.MAX_PLAYERS): v.ByteValue(room.max_players),
            v.ByteValue(RP.IS_OPEN): v.BooleanValue(room.is_open),
            v.ByteValue(RP.IS_VISIBLE): v.BooleanValue(room.is_visible),
            v.ByteValue(RP.PLAYER_COUNT): v.ByteValue(len(room.players)),
            v.ByteValue(RP.MASTER_CLIENT_ID): v.IntegerValue(room.master_actor_id),
            v.ByteValue(RP.CLEANUP_CACHE_ON_LEAVE): v.BooleanValue(room.cleanup_cache_on_leave),
        }
    )
    if removed:
        properties[v.ByteValue(RP.REMOVED)] = v.BooleanValue(True)
    return v.PropertyTable(properties)


def actor_properties(room: Room) -> v.PropertyTable:
    return v.PropertyTable(
        {
            v.IntegerValue(actor_id): v.PropertyTable(dict(player.properties))
            for actor_id, player in room.players.items()
        }
    )
