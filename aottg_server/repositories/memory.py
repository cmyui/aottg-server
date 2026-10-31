"""Process-local state; rooms live only while at least one player is connected."""

from aottg_server.domain.models import Room, RoomKey, Session


class InMemoryRoomRepository:
    def __init__(self) -> None:
        self._rooms: dict[RoomKey, Room] = {}

    def get(self, key: RoomKey) -> Room | None:
        return self._rooms.get(key)

    def add(self, room: Room) -> None:
        self._rooms[room.key] = room

    def remove(self, key: RoomKey) -> None:
        self._rooms.pop(key, None)

    def list_all(self) -> tuple[Room, ...]:
        return tuple(self._rooms.values())


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, connection_id: str) -> Session | None:
        return self._sessions.get(connection_id)

    def add(self, session: Session) -> None:
        self._sessions[session.connection_id] = session

    def remove(self, connection_id: str) -> None:
        self._sessions.pop(connection_id, None)

    def list_all(self) -> tuple[Session, ...]:
        return tuple(self._sessions.values())
