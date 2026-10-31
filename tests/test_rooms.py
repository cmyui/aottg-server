import pytest

from aottg_server.domain.models import CreateRoom, RoomChanges, RoomError, RoomFailure, Session
from aottg_server.domain.values import IntegerValue, NullValue, StringValue
from aottg_server.repositories.memory import InMemoryRoomRepository
from aottg_server.services.rooms import RoomService


def test_actor_identity_master_migration_and_empty_room_cleanup() -> None:
    repository = InMemoryRoomRepository()
    rooms = RoomService(repository)
    host = Session("host", "host", "rc")
    guest = Session("guest", "guest", "rc")
    first = rooms.create(host, CreateRoom("city"), {})
    second = rooms.join(guest, "city", {})
    assert first.player.actor_id == 1
    assert second.player.actor_id == 2
    rooms.leave(host)
    assert second.room.master_actor_id == second.player.actor_id
    assert host.room_key is None and host.actor_id is None
    rejoined = rooms.join(host, "city", {})
    assert rejoined.player.actor_id == 3
    rooms.leave(host)
    rooms.leave(guest)
    assert repository.list_all() == ()


def test_namespaces_isolate_rooms_and_capacity_is_enforced() -> None:
    rooms = RoomService(InMemoryRoomRepository())
    host = Session("one", "one", "v1")
    other_version = Session("two", "two", "v2")
    guest = Session("three", "three", "v1")
    rooms.create(host, CreateRoom("city", max_players=1), {})
    rooms.create(other_version, CreateRoom("city"), {})
    with pytest.raises(RoomError) as failure:
        rooms.join(guest, "city", {})
    assert failure.value.reason == RoomFailure.FULL
    assert guest.room_key is None


def test_closed_room_rejects_new_players() -> None:
    rooms = RoomService(InMemoryRoomRepository())
    host = Session("host", "host", "rc")
    rooms.create(host, CreateRoom("city"), {})
    rooms.update_properties(host, 0, {}, {}, RoomChanges(is_open=False))
    with pytest.raises(RoomError) as failure:
        rooms.join(Session("guest", "guest", "rc"), "city", {})
    assert failure.value.reason == RoomFailure.CLOSED


def test_property_compare_and_swap_is_atomic_and_null_removes_keys() -> None:
    rooms = RoomService(InMemoryRoomRepository())
    host = Session("host", "host", "rc")
    key = StringValue("round")
    room = rooms.create(host, CreateRoom("city", properties={key: IntegerValue(1)}), {}).room
    with pytest.raises(RoomError) as failure:
        rooms.update_properties(
            host, 0, {key: IntegerValue(3)}, {key: IntegerValue(2)}, RoomChanges(is_open=False)
        )
    assert failure.value.reason == RoomFailure.PROPERTY_CONFLICT
    assert room.properties[key] == IntegerValue(1)
    assert room.is_open
    rooms.update_properties(host, 0, {key: IntegerValue(2)}, {key: IntegerValue(1)})
    assert room.properties[key] == IntegerValue(2)
    rooms.update_properties(host, 0, {key: NullValue()}, {})
    assert key not in room.properties


@pytest.mark.parametrize("capacity", [10, 64, 255])
def test_full_room_has_unique_members_and_rejects_overflow(capacity: int) -> None:
    rooms = RoomService(InMemoryRoomRepository())
    host = Session("0", "0", "rc")
    room = rooms.create(host, CreateRoom("city", max_players=capacity), {}).room
    for index in range(1, capacity):
        rooms.join(Session(str(index), str(index), "rc"), "city", {})
    assert len(room.players) == capacity
    assert len({player.actor_id for player in room.players.values()}) == capacity
    with pytest.raises(RoomError) as failure:
        rooms.join(Session("overflow", "overflow", "rc"), "city", {})
    assert failure.value.reason == RoomFailure.FULL
