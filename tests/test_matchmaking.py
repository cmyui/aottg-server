from aottg_server.domain.models import CreateRoom, MatchmakingMode, Session
from aottg_server.repositories.memory import InMemoryRoomRepository, InMemorySessionRepository
from aottg_server.services.matchmaking import MatchmakingService
from aottg_server.services.rooms import RoomService


def test_fill_uses_oldest_and_serial_rotates_matching_rooms() -> None:
    repository = InMemoryRoomRepository()
    rooms = RoomService(repository)
    matchmaking = MatchmakingService(repository, InMemorySessionRepository())
    first_host, second_host = Session("one", "one", "rc"), Session("two", "two", "rc")
    oldest = rooms.create(first_host, CreateRoom("first"), {}).room
    newest = rooms.create(second_host, CreateRoom("second"), {}).room
    requester = Session("requester", "requester", "rc")
    rooms.join(Session("three", "three", "rc"), "second", {})
    assert matchmaking.find_room(requester, "", {}, 0, MatchmakingMode.FILL_OLDEST) is oldest
    assert [
        matchmaking.find_room(requester, "", {}, 0, MatchmakingMode.SERIAL) for _ in range(4)
    ] == [oldest, newest, oldest, newest]
    rooms.leave(first_host)
    assert matchmaking.find_room(requester, "", {}, 0, MatchmakingMode.SERIAL) is newest
