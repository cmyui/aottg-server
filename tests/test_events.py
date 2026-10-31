import pytest

from aottg_server.domain.models import (
    CachePolicy,
    CreateRoom,
    RaiseEvent,
    ReceiverGroup,
    Room,
    RoomError,
    Session,
)
from aottg_server.domain.values import (
    ByteValue,
    IntegerValue,
    NullValue,
    PropertyTable,
    StringValue,
)
from aottg_server.repositories.memory import InMemoryRoomRepository, InMemorySessionRepository
from aottg_server.services.events import EventService
from aottg_server.services.rooms import RoomService


@pytest.fixture
def game() -> tuple[RoomService, EventService, Room, Session, Session]:
    sessions = InMemorySessionRepository()
    rooms = RoomService(InMemoryRoomRepository())
    host, guest = Session("host", "one", "rc"), Session("guest", "two", "rc")
    sessions.add(host)
    sessions.add(guest)
    room = rooms.create(host, CreateRoom("city"), {}).room
    rooms.join(guest, "city", {})
    return rooms, EventService(sessions), room, host, guest


def test_recipient_selection_and_interest_groups(
    game: tuple[RoomService, EventService, Room, Session, Session],
) -> None:
    _, events, room, host, guest = game
    assert events.raise_event(room, host, RaiseEvent(200, NullValue())) == ("guest",)
    assert events.raise_event(
        room,
        guest,
        RaiseEvent(
            200,
            NullValue(),
            receivers=ReceiverGroup.MASTER,
        ),
    ) == ("host",)
    assert events.raise_event(
        room,
        host,
        RaiseEvent(
            200,
            NullValue(),
            target_actors=(host.actor_id or 0,),
        ),
    ) == ("host",)
    assert events.raise_event(room, host, RaiseEvent(200, NullValue(), interest_group=3)) == ()
    guest.interest_groups.add(3)
    assert events.raise_event(room, host, RaiseEvent(200, NullValue(), interest_group=3)) == (
        "guest",
    )


def test_late_join_cache_and_owner_cleanup(
    game: tuple[RoomService, EventService, Room, Session, Session],
) -> None:
    rooms, events, room, host, _ = game
    events.raise_event(
        room, host, RaiseEvent(200, StringValue("host rpc"), cache_policy=CachePolicy.ADD)
    )
    events.raise_event(
        room,
        host,
        RaiseEvent(
            201,
            StringValue("scene"),
            cache_policy=CachePolicy.ADD_GLOBAL,
        ),
    )
    late = Session("late", "three", "rc")
    result = rooms.join(late, "city", {})
    assert [event.code for event in result.cached_events] == [200, 201]
    rooms.leave(host)
    assert [event.code for event in room.cached_events] == [201]


def test_cache_removal_matches_rpc_subset(
    game: tuple[RoomService, EventService, Room, Session, Session],
) -> None:
    _, events, room, host, _ = game
    for view in (1, 2):
        events.raise_event(
            room,
            host,
            RaiseEvent(
                200,
                PropertyTable({ByteValue(0): IntegerValue(view), ByteValue(1): StringValue("rpc")}),
                cache_policy=CachePolicy.ADD,
            ),
        )
    events.raise_event(
        room,
        host,
        RaiseEvent(
            200,
            PropertyTable({ByteValue(0): IntegerValue(1)}),
            cache_policy=CachePolicy.REMOVE_MATCHING,
        ),
    )
    assert len(room.cached_events) == 1
    assert room.cached_events[0].payload == PropertyTable(
        {
            ByteValue(0): IntegerValue(2),
            ByteValue(1): StringValue("rpc"),
        }
    )


def test_cache_limit_does_not_append_rejected_event(
    game: tuple[RoomService, EventService, Room, Session, Session],
) -> None:
    _, _, room, host, _ = game
    events = EventService(InMemorySessionRepository(), max_cached_events=1)
    request = RaiseEvent(200, NullValue(), cache_policy=CachePolicy.ADD)
    events.raise_event(room, host, request)
    with pytest.raises(RoomError):
        events.raise_event(room, host, request)
    assert len(room.cached_events) == 1


def test_targeted_and_group_events_are_delivered_without_caching(
    game: tuple[RoomService, EventService, Room, Session, Session],
) -> None:
    _, events, room, host, guest = game
    assert events.raise_event(
        room,
        host,
        RaiseEvent(
            200,
            NullValue(),
            cache_policy=CachePolicy.ADD,
            target_actors=(guest.actor_id or 0,),
        ),
    ) == ("guest",)
    assert not room.cached_events
    assert events.raise_event(
        room,
        guest,
        RaiseEvent(
            200,
            NullValue(),
            cache_policy=CachePolicy.ADD,
            receivers=ReceiverGroup.MASTER,
        ),
    ) == ("host",)
    assert not room.cached_events
