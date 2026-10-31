"""Translate client operations to service calls and protocol responses."""

import logging
from uuid import uuid4

from aottg_server.adapters.photon.codes import (
    EventCode,
    OperationCode,
    ReturnCode,
    ValueType,
)
from aottg_server.adapters.photon.codes import (
    ParameterCode as P,
)
from aottg_server.adapters.photon.gateway import ClientGateway
from aottg_server.adapters.photon.messages import EventMessage, OperationRequest, OperationResponse
from aottg_server.domain import values as v
from aottg_server.domain.models import (
    CachePolicy,
    JoinResult,
    RaiseEvent,
    ReceiverGroup,
    Room,
    RoomChanges,
    RoomError,
    RoomFailure,
    Session,
)
from aottg_server.ports.connections import ClientConnection, Delivery, ServerRole
from aottg_server.ports.repositories import SessionRepository
from aottg_server.services.events import EventService
from aottg_server.services.rooms import RoomService

from .parameters import RequestParameters
from .room_properties import (
    actor_properties,
    create_request,
    custom_properties,
    room_changes,
    room_properties,
)

logger = logging.getLogger(__name__)
FAILURE_CODES = {
    RoomFailure.NOT_FOUND: ReturnCode.ROOM_NOT_FOUND,
    RoomFailure.ALREADY_EXISTS: ReturnCode.ROOM_ALREADY_EXISTS,
    RoomFailure.FULL: ReturnCode.ROOM_FULL,
    RoomFailure.CLOSED: ReturnCode.ROOM_CLOSED,
    RoomFailure.NOT_JOINED: ReturnCode.INVALID_OPERATION,
    RoomFailure.INVALID_REQUEST: ReturnCode.INVALID_PARAMETERS,
    RoomFailure.PROPERTY_CONFLICT: ReturnCode.INVALID_OPERATION,
    RoomFailure.CACHE_FULL: ReturnCode.INVALID_OPERATION,
}


class MultiplayerController:
    def __init__(
        self,
        rooms: RoomService,
        events: EventService,
        sessions: SessionRepository,
        gateway: ClientGateway,
        game_address: str,
    ) -> None:
        self._rooms = rooms
        self._events = events
        self._sessions = sessions
        self._gateway = gateway
        self._game_address = game_address

    def handle(
        self, connection: ClientConnection, request: OperationRequest, delivery: Delivery
    ) -> None:
        try:
            if request.code == OperationCode.AUTHENTICATE:
                self._authenticate(connection, request)
                return
            session = self._sessions.get(connection.connection_id)
            if session is None:
                self._respond(
                    connection.connection_id,
                    request.code,
                    ReturnCode.AUTHENTICATION_FAILED,
                    "Authenticate first",
                )
                return
            self._dispatch(connection, session, request, delivery)
        except RoomError as exc:
            self._respond(
                connection.connection_id, request.code, FAILURE_CODES[exc.reason], str(exc)
            )

    def _authenticate(self, connection: ClientConnection, request: OperationRequest) -> None:
        if self._sessions.get(connection.connection_id) is not None:
            self._respond(
                connection.connection_id,
                request.code,
                ReturnCode.INVALID_OPERATION,
                "Already authenticated",
            )
            return
        parameters = RequestParameters(request.parameters)
        if parameters.integer(P.AUTH_TYPE, 255) != 255:
            self._respond(
                connection.connection_id,
                request.code,
                ReturnCode.AUTHENTICATION_FAILED,
                "Custom authentication is unsupported",
            )
            return
        user_id = parameters.text(P.USER_ID) or uuid4().hex
        namespace = parameters.text(P.APPLICATION_ID) + ":" + parameters.text(P.APP_VERSION)
        self._sessions.add(Session(connection.connection_id, user_id, namespace))
        self._gateway.respond(
            connection.connection_id,
            OperationResponse(
                request.code,
                {P.USER_ID: v.StringValue(user_id)},
            ),
        )

    def _dispatch(
        self,
        connection: ClientConnection,
        session: Session,
        request: OperationRequest,
        delivery: Delivery,
    ) -> None:
        code = request.code
        if code in (OperationCode.JOIN_LOBBY, OperationCode.LEAVE_LOBBY):
            if connection.role != ServerRole.MASTER:
                raise RoomError(
                    RoomFailure.INVALID_REQUEST, "Lobby operations require master server"
                )
            self._lobby(session, request)
        elif code in (OperationCode.CREATE_ROOM, OperationCode.JOIN_ROOM):
            self._join_room(connection, session, request)
        elif code == OperationCode.RAISE_EVENT:
            self._raise_event(session, request, delivery)
        elif code == OperationCode.SET_PROPERTIES:
            self._set_properties(session, request)
        elif code == OperationCode.GET_PROPERTIES:
            room = self._rooms.require_room(session)
            self._gateway.respond(
                session.connection_id,
                OperationResponse(
                    code,
                    {
                        P.ROOM_PROPERTIES: room_properties(room),
                        P.PLAYER_PROPERTIES: actor_properties(room),
                    },
                ),
            )
        elif code == OperationCode.LEAVE_ROOM:
            self._leave_room(session)
            self._respond(session.connection_id, code)
        elif code == OperationCode.CHANGE_GROUPS:
            self._change_groups(session, request)
        else:
            self._respond(
                session.connection_id,
                code,
                ReturnCode.INVALID_OPERATION,
                "Operation is not supported by this server",
            )

    def _lobby(self, session: Session, request: OperationRequest) -> None:
        session.lobby_name = (
            RequestParameters(request.parameters).text(P.LOBBY_NAME)
            if request.code == OperationCode.JOIN_LOBBY
            else None
        )
        self._respond(session.connection_id, request.code)
        if session.lobby_name is not None:
            rooms: v.Properties = {
                v.StringValue(room.key.name): room_properties(room)
                for room in self._rooms.rooms.list_all()
                if room.key.namespace == session.namespace
                and room.is_visible
                and room.lobby_name == session.lobby_name
            }
            self._gateway.publish(
                session.connection_id,
                EventMessage(
                    EventCode.ROOM_LIST,
                    {P.ROOM_LIST: v.PropertyTable(rooms)},
                ),
            )
            self._publish_statistics(session)

    def _join_room(
        self, connection: ClientConnection, session: Session, request: OperationRequest
    ) -> None:
        parameters = RequestParameters(request.parameters)
        name = parameters.text(P.ROOM_NAME)
        creating = request.code == OperationCode.CREATE_ROOM
        if creating and not name:
            name = uuid4().hex
        creation = create_request(
            name, parameters.text(P.LOBBY_NAME), parameters.properties(P.ROOM_PROPERTIES)
        )
        if parameters.integer(P.JOIN_MODE) != 0:
            raise RoomError(
                RoomFailure.INVALID_REQUEST, "Rejoin and join-or-create are unsupported"
            )
        if connection.role == ServerRole.MASTER:
            if creating:
                self._rooms.validate_creation(session, creation)
            else:
                self._rooms.find_joinable(session, name)
            self._gateway.respond(
                session.connection_id,
                OperationResponse(
                    request.code,
                    {
                        P.ADDRESS: v.StringValue(self._game_address),
                        P.ROOM_NAME: v.StringValue(name),
                    },
                ),
            )
            return
        player_properties = parameters.properties(P.PLAYER_PROPERTIES)
        result = (
            self._rooms.create(session, creation, player_properties)
            if creating
            else self._rooms.join(session, name, player_properties)
        )
        self._publish_join(session, request.code, result)
        logger.info("Actor %s joined room %r", result.player.actor_id, name)

    def _publish_join(self, session: Session, operation: int, result: JoinResult) -> None:
        room, player = result.room, result.player
        actors = v.TypedArray(ValueType.INTEGER, tuple(v.IntegerValue(key) for key in room.players))
        self._gateway.respond(
            session.connection_id,
            OperationResponse(
                operation,
                {
                    P.ACTOR_NUMBER: v.IntegerValue(player.actor_id),
                    P.ACTOR_LIST: actors,
                    P.PLAYER_PROPERTIES: actor_properties(room),
                    P.ROOM_PROPERTIES: room_properties(room),
                },
            ),
        )
        joined = EventMessage(
            EventCode.JOIN,
            {
                P.ACTOR_NUMBER: v.IntegerValue(player.actor_id),
                P.ACTOR_LIST: actors,
                P.PLAYER_PROPERTIES: v.PropertyTable(dict(player.properties)),
            },
        )
        for member in tuple(room.players.values()):
            self._gateway.publish(member.connection_id, joined)
        for cached in result.cached_events:
            self._gateway.publish(
                session.connection_id,
                EventMessage(
                    cached.code,
                    {
                        P.ACTOR_NUMBER: v.IntegerValue(cached.sender_id),
                        P.DATA: cached.payload,
                    },
                ),
                Delivery(cached.channel),
            )
        self._publish_room_change(room)

    def _raise_event(self, session: Session, request: OperationRequest, delivery: Delivery) -> None:
        parameters = RequestParameters(request.parameters)
        event = RaiseEvent(
            code=parameters.integer(P.EVENT_CODE),
            payload=request.parameters.get(P.DATA, v.NullValue()),
            receivers=ReceiverGroup(parameters.integer(P.RECEIVER_GROUP)),
            cache_policy=CachePolicy(parameters.integer(P.CACHE_MODE)),
            target_actors=parameters.actor_ids(P.ACTOR_LIST),
            interest_group=parameters.integer(P.INTEREST_GROUP),
            channel=delivery.channel,
        )
        if not 0 <= event.code <= 255 or not 0 <= event.interest_group <= 255:
            raise RoomError(RoomFailure.INVALID_REQUEST, "Invalid event code or interest group")
        room = self._rooms.require_room(session)
        recipients = self._events.raise_event(room, session, event)
        message = EventMessage(
            event.code,
            {
                P.ACTOR_NUMBER: v.IntegerValue(session.actor_id or 0),
                P.DATA: event.payload,
            },
        )
        for connection_id in recipients:
            self._gateway.publish(connection_id, message, delivery)

    def _set_properties(self, session: Session, request: OperationRequest) -> None:
        parameters = RequestParameters(request.parameters)
        target_actor = parameters.integer(P.ACTOR_NUMBER)
        properties = parameters.properties(P.PROPERTIES)
        changes = room_changes(properties) if target_actor == 0 else RoomChanges()
        updates = custom_properties(properties) if target_actor == 0 else properties
        room = self._rooms.update_properties(
            session, target_actor, updates, parameters.properties(P.EXPECTED_VALUES), changes
        )
        self._respond(session.connection_id, request.code)
        if parameters.flag(P.BROADCAST, True):
            event = EventMessage(
                EventCode.PROPERTIES_CHANGED,
                {
                    P.TARGET_ACTOR: v.IntegerValue(target_actor),
                    P.PROPERTIES: v.PropertyTable(properties),
                    P.ACTOR_NUMBER: v.IntegerValue(session.actor_id or 0),
                },
            )
            for player in tuple(room.players.values()):
                self._gateway.publish(player.connection_id, event)
        self._publish_room_change(room)

    def _change_groups(self, session: Session, request: OperationRequest) -> None:
        self._rooms.require_room(session)
        parameters = RequestParameters(request.parameters)
        removed, added = parameters.bytes(P.REMOVE_GROUPS), parameters.bytes(P.ADD_GROUPS)
        if removed is not None:
            if removed:
                session.interest_groups.difference_update(removed)
            else:
                session.interest_groups.clear()
        if added is not None:
            session.interest_groups.update(added if added else range(1, 256))
        self._respond(session.connection_id, request.code)

    def disconnected(self, connection_id: str) -> None:
        session = self._sessions.get(connection_id)
        if session is not None:
            self._leave_room(session)
            self._sessions.remove(connection_id)

    def _leave_room(self, session: Session) -> None:
        result = self._rooms.leave(session)
        if result is None:
            return
        event = EventMessage(
            EventCode.LEAVE,
            {
                P.ACTOR_NUMBER: v.IntegerValue(result.actor_id),
                P.ACTOR_LIST: v.TypedArray(
                    ValueType.INTEGER, tuple(v.IntegerValue(actor) for actor in result.room.players)
                ),
            },
        )
        for player in tuple(result.room.players.values()):
            self._gateway.publish(player.connection_id, event)
        self._publish_room_change(result.room, removed=result.room_removed)

    def _publish_room_change(self, room: Room, *, removed: bool = False) -> None:
        event = EventMessage(
            EventCode.ROOM_LIST_UPDATE,
            {
                P.ROOM_LIST: v.PropertyTable(
                    {
                        v.StringValue(room.key.name): room_properties(
                            room, removed=removed or not room.is_visible
                        ),
                    }
                )
            },
        )
        for session in self._sessions.list_all():
            if session.namespace == room.key.namespace and session.lobby_name == room.lobby_name:
                self._gateway.publish(session.connection_id, event)
                self._publish_statistics(session)

    def _publish_statistics(self, session: Session) -> None:
        rooms = [
            room for room in self._rooms.rooms.list_all() if room.key.namespace == session.namespace
        ]
        self._gateway.publish(
            session.connection_id,
            EventMessage(
                EventCode.LOBBY_STATISTICS,
                {
                    P.PEER_COUNT: v.IntegerValue(sum(len(room.players) for room in rooms)),
                    P.GAME_COUNT: v.IntegerValue(len(rooms)),
                    P.MASTER_PEER_COUNT: v.IntegerValue(
                        sum(
                            other.namespace == session.namespace and other.room_key is None
                            for other in self._sessions.list_all()
                        )
                    ),
                },
            ),
        )

    def _respond(
        self,
        connection_id: str,
        operation: int,
        code: ReturnCode = ReturnCode.OK,
        message: str | None = None,
    ) -> None:
        self._gateway.respond(connection_id, OperationResponse(operation, {}, code, message))
