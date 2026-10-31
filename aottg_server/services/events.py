"""Recipient selection and cached gameplay events for late joiners."""

from aottg_server.domain.models import (
    CachedEvent,
    CachePolicy,
    RaiseEvent,
    ReceiverGroup,
    Room,
    RoomError,
    RoomFailure,
    Session,
)
from aottg_server.domain.values import NullValue, PropertyTable
from aottg_server.ports.repositories import SessionRepository


class EventService:
    def __init__(self, sessions: SessionRepository, max_cached_events: int = 4096) -> None:
        self._sessions = sessions
        self._max_cached_events = max_cached_events

    def raise_event(self, room: Room, sender: Session, request: RaiseEvent) -> tuple[str, ...]:
        if sender.actor_id is None or sender.actor_id not in room.players:
            raise RoomError(RoomFailure.NOT_JOINED, "Join a room before sending events")
        if request.cache_policy in (
            CachePolicy.REMOVE,
            CachePolicy.REMOVE_MATCHING,
            CachePolicy.REMOVE_DEPARTED,
        ):
            self._remove_cached(room, sender, request)
            return ()
        self._cache(room, sender.actor_id, request)
        recipients: list[str] = []
        for actor_id, player in room.players.items():
            if request.target_actors is not None:
                if actor_id not in request.target_actors:
                    continue
            elif request.receivers == ReceiverGroup.OTHERS and actor_id == sender.actor_id:
                continue
            elif request.receivers == ReceiverGroup.MASTER and actor_id != room.master_actor_id:
                continue
            session = self._sessions.get(player.connection_id)
            if request.interest_group and (
                session is None or request.interest_group not in session.interest_groups
            ):
                continue
            recipients.append(player.connection_id)
        return tuple(recipients)

    def _cache(self, room: Room, actor_id: int, request: RaiseEvent) -> None:
        if request.cache_policy == CachePolicy.NONE:
            return
        if request.target_actors is not None or request.interest_group:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Cannot cache targeted/group events")
        if request.cache_policy == CachePolicy.MERGE:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Legacy cache merge is unsupported")
        if request.cache_policy == CachePolicy.REPLACE:
            room.cached_events = [
                event
                for event in room.cached_events
                if (event.sender_id, event.code) != (actor_id, request.code)
            ]
        if len(room.cached_events) >= self._max_cached_events:
            raise RoomError(RoomFailure.CACHE_FULL, "Room event cache is full")
        owner = 0 if request.cache_policy == CachePolicy.ADD_GLOBAL else actor_id
        room.cached_events.append(
            CachedEvent(request.code, owner, request.payload, request.channel)
        )

    @staticmethod
    def _remove_cached(room: Room, sender: Session, request: RaiseEvent) -> None:
        def matches(event: CachedEvent) -> bool:
            if request.cache_policy == CachePolicy.REMOVE_DEPARTED:
                return event.sender_id != 0 and event.sender_id not in room.players
            if request.code and event.code != request.code:
                return False
            owners = request.target_actors
            if request.cache_policy == CachePolicy.REMOVE:
                owners = (sender.actor_id,) if sender.actor_id is not None else ()
            if owners is not None and event.sender_id not in owners:
                return False
            if isinstance(request.payload, NullValue):
                return True
            if isinstance(request.payload, PropertyTable) and isinstance(
                event.payload, PropertyTable
            ):
                return all(
                    event.payload.entries.get(key) == value
                    for key, value in request.payload.entries.items()
                )
            return request.payload == event.payload

        room.cached_events = [event for event in room.cached_events if not matches(event)]
