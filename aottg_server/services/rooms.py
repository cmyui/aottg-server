"""Room lifecycle and property changes, independent of Photon and asyncio."""

from aottg_server.domain.models import (
    CreateRoom,
    JoinResult,
    LeaveResult,
    Player,
    Room,
    RoomChanges,
    RoomError,
    RoomFailure,
    RoomKey,
    Session,
)
from aottg_server.domain.values import NullValue, Properties
from aottg_server.ports.repositories import RoomRepository


class RoomService:
    def __init__(self, rooms: RoomRepository, max_rooms: int = 32) -> None:
        self.rooms = rooms
        self._max_rooms = max_rooms

    def validate_creation(self, session: Session, request: CreateRoom) -> None:
        if not request.name or len(request.name.encode("utf-8")) > 1024:
            raise RoomError(
                RoomFailure.INVALID_REQUEST, "Room name must contain 1–1024 UTF-8 bytes"
            )
        if not 0 <= request.max_players <= 255:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Player limit must be between 0 and 255")
        if self.rooms.get(RoomKey(session.namespace, request.name)) is not None:
            raise RoomError(RoomFailure.ALREADY_EXISTS, "Room already exists")
        if len(self.rooms.list_all()) >= self._max_rooms:
            raise RoomError(RoomFailure.FULL, "Server room limit reached")

    def create(
        self, session: Session, request: CreateRoom, player_properties: Properties
    ) -> JoinResult:
        self._require_not_joined(session)
        self.validate_creation(session, request)
        room = Room(
            RoomKey(session.namespace, request.name),
            request.lobby_name,
            request.max_players,
            request.is_open,
            request.is_visible,
            request.cleanup_cache_on_leave,
            dict(request.properties),
            lobby_property_names=request.lobby_property_names,
        )
        self.rooms.add(room)
        return self._add_player(room, session, player_properties)

    def find_joinable(self, session: Session, name: str) -> Room:
        room = self.rooms.get(RoomKey(session.namespace, name))
        if room is None:
            raise RoomError(RoomFailure.NOT_FOUND, "Room does not exist")
        if not room.is_open:
            raise RoomError(RoomFailure.CLOSED, "Room is closed")
        if len(room.players) >= (room.max_players or 255):
            raise RoomError(RoomFailure.FULL, "Room is full")
        return room

    def transfer_master(self, session: Session, actor_id: int) -> Room:
        room = self.require_room(session)
        self.require_master(room, session)
        if actor_id not in room.players:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Unknown master client")
        room.master_actor_id = actor_id
        return room

    @staticmethod
    def require_master(room: Room, session: Session) -> None:
        if session.actor_id != room.master_actor_id:
            raise RoomError(
                RoomFailure.INVALID_REQUEST, "Only the master client can perform this action"
            )

    def join_or_create(
        self, session: Session, request: CreateRoom, player_properties: Properties
    ) -> JoinResult:
        if self.rooms.get(RoomKey(session.namespace, request.name)) is None:
            return self.create(session, request, player_properties)
        return self.join(session, request.name, player_properties)

    def join(self, session: Session, name: str, player_properties: Properties) -> JoinResult:
        self._require_not_joined(session)
        return self._add_player(self.find_joinable(session, name), session, player_properties)

    def _add_player(self, room: Room, session: Session, properties: Properties) -> JoinResult:
        player = Player(room.next_actor_id, session.connection_id, dict(properties))
        room.next_actor_id += 1
        room.players[player.actor_id] = player
        if room.master_actor_id == 0:
            room.master_actor_id = player.actor_id
        session.room_key, session.actor_id = room.key, player.actor_id
        session.lobby_name = None
        return JoinResult(room, player, tuple(room.cached_events))

    def require_room(self, session: Session) -> Room:
        room = self.rooms.get(session.room_key) if session.room_key is not None else None
        if room is None or session.actor_id not in room.players:
            raise RoomError(RoomFailure.NOT_JOINED, "Join a room first")
        return room

    def leave(self, session: Session) -> LeaveResult | None:
        if session.room_key is None or session.actor_id is None:
            return None
        room = self.require_room(session)
        actor_id = session.actor_id
        del room.players[actor_id]
        session.room_key, session.actor_id = None, None
        session.interest_groups.clear()
        if room.cleanup_cache_on_leave:
            room.cached_events = [
                event for event in room.cached_events if event.sender_id != actor_id
            ]
        if room.master_actor_id == actor_id:
            room.master_actor_id = min(room.players, default=0)
        removed = not room.players
        if removed:
            self.rooms.remove(room.key)
        return LeaveResult(room, actor_id, removed)

    def update_properties(
        self,
        session: Session,
        target_actor: int,
        updates: Properties,
        expected: Properties,
        changes: RoomChanges = RoomChanges(),
        expected_changes: RoomChanges = RoomChanges(),
    ) -> Room:
        room = self.require_room(session)
        if target_actor:
            player = room.players.get(target_actor)
            if player is None:
                raise RoomError(RoomFailure.INVALID_REQUEST, "Unknown player")
            destination = player.properties
        else:
            destination = room.properties
        metadata_matches = all(
            wanted is None or actual == wanted
            for actual, wanted in (
                (room.max_players, expected_changes.max_players),
                (room.is_open, expected_changes.is_open),
                (room.is_visible, expected_changes.is_visible),
                (room.master_actor_id, expected_changes.master_actor_id),
                (room.cleanup_cache_on_leave, expected_changes.cleanup_cache_on_leave),
                (room.lobby_property_names, expected_changes.lobby_property_names),
            )
        )
        if not metadata_matches or any(
            destination.get(key, NullValue()) != value for key, value in expected.items()
        ):
            raise RoomError(RoomFailure.PROPERTY_CONFLICT, "Properties changed concurrently")
        if changes.master_actor_id is not None:
            self.require_master(room, session)
        if changes.master_actor_id is not None and changes.master_actor_id not in room.players:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Unknown master client")
        for key, value in updates.items():
            if isinstance(value, NullValue):
                destination.pop(key, None)
            else:
                destination[key] = value
        if changes.max_players is not None:
            room.max_players = changes.max_players
        if changes.is_open is not None:
            room.is_open = changes.is_open
        if changes.is_visible is not None:
            room.is_visible = changes.is_visible
        if changes.master_actor_id is not None:
            room.master_actor_id = changes.master_actor_id
        if changes.cleanup_cache_on_leave is not None:
            room.cleanup_cache_on_leave = changes.cleanup_cache_on_leave
        if changes.lobby_property_names is not None:
            room.lobby_property_names = changes.lobby_property_names
        return room

    @staticmethod
    def _require_not_joined(session: Session) -> None:
        if session.room_key is not None:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Already in a room")
