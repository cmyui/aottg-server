"""PUN Classic control events that affect server-side recipient selection."""

from aottg_server.adapters.photon.codes import EventCode, ParameterCode
from aottg_server.adapters.photon.messages import EventMessage, ProtocolError
from aottg_server.domain.models import CachePolicy, RaiseEvent, ReceiverGroup, Session
from aottg_server.domain.values import ByteValue, IntegerValue, PropertyTable
from aottg_server.services.rooms import RoomService


class PunEventController:
    def __init__(self, rooms: RoomService) -> None:
        self._rooms = rooms

    def before_relay(self, session: Session, request: RaiseEvent) -> None:
        if request.code not in (EventCode.TRANSFER_MASTER, EventCode.CLOSE_CONNECTION):
            return
        room = self._rooms.require_room(session)
        self._rooms.require_master(room, session)
        if request.cache_policy != CachePolicy.NONE or request.interest_group:
            raise ProtocolError("Control events cannot be cached or interest-group scoped")
        if request.code == EventCode.TRANSFER_MASTER:
            if request.target_actors is not None or request.receivers != ReceiverGroup.OTHERS:
                raise ProtocolError("Master transfer must reach all other players")
            if not isinstance(request.payload, PropertyTable):
                raise ProtocolError("Master transfer requires a property table")
            actor = request.payload.entries.get(ByteValue(1))
            if not isinstance(actor, IntegerValue):
                raise ProtocolError("Master transfer requires an actor ID")
            self._rooms.transfer_master(session, actor.value)
        elif not request.target_actors or any(
            actor not in room.players for actor in request.target_actors
        ):
            raise ProtocolError("Kick requires existing target actors")

    @staticmethod
    def master_snapshot(actor_id: int) -> EventMessage:
        # RC derives the initial master from actor order. Sender 0 identifies the
        # server snapshot, allowing late joiners to restore an explicit transfer.
        return EventMessage(
            EventCode.TRANSFER_MASTER,
            {
                ParameterCode.ACTOR_NUMBER: IntegerValue(0),
                ParameterCode.DATA: PropertyTable({ByteValue(1): IntegerValue(actor_id)}),
            },
        )
