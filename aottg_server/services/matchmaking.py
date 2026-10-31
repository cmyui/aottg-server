"""Lobby-scoped room selection and online friend lookup."""

from secrets import choice

from aottg_server.domain.models import (
    MatchmakingMode,
    Room,
    RoomError,
    RoomFailure,
    RoomKey,
    Session,
)
from aottg_server.domain.values import Properties, StringValue
from aottg_server.ports.repositories import RoomRepository, SessionRepository


class MatchmakingService:
    def __init__(self, rooms: RoomRepository, sessions: SessionRepository) -> None:
        self._rooms = rooms
        self._sessions = sessions
        self._serial_cursors: dict[tuple[str, str], RoomKey] = {}

    def find_room(
        self,
        session: Session,
        lobby: str,
        expected: Properties,
        max_players: int,
        mode: MatchmakingMode,
    ) -> Room | None:
        candidates = [
            room
            for room in self._rooms.list_all()
            if room.key.namespace == session.namespace
            and room.lobby_name == lobby
            and room.is_visible
            and room.is_open
            and len(room.players) < (room.max_players or 255)
            and (not max_players or room.max_players == max_players)
            and all(
                isinstance(key, StringValue)
                and key.value in room.lobby_property_names
                and room.properties.get(key) == value
                for key, value in expected.items()
            )
        ]
        if not candidates:
            return None
        if mode == MatchmakingMode.RANDOM:
            return choice(candidates)
        if mode == MatchmakingMode.FILL_OLDEST:
            return candidates[0]
        return self._next_serial_room(session.namespace, lobby, candidates)

    def _next_serial_room(self, namespace: str, lobby: str, candidates: list[Room]) -> Room:
        active_lobbies = {(room.key.namespace, room.lobby_name) for room in self._rooms.list_all()}
        self._serial_cursors = {
            key: cursor for key, cursor in self._serial_cursors.items() if key in active_lobbies
        }
        key = namespace, lobby
        previous = self._serial_cursors.get(key)
        keys = [room.key for room in candidates]
        index = (
            (keys.index(previous) + 1) % len(keys)
            if previous is not None and previous in keys
            else 0
        )
        chosen = candidates[index]
        self._serial_cursors[key] = chosen.key
        return chosen

    def find_friends(
        self, requester: Session, user_ids: tuple[str, ...]
    ) -> tuple[tuple[bool, str], ...]:
        if len(user_ids) > 512:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Too many friend IDs")
        online = {
            session.user_id: session
            for session in self._sessions.list_all()
            if session.namespace == requester.namespace
        }
        results: list[tuple[bool, str]] = []
        for user_id in user_ids:
            friend = online.get(user_id)
            room_name = friend.room_key.name if friend is not None and friend.room_key else ""
            results.append((friend is not None, room_name))
        return tuple(results)
