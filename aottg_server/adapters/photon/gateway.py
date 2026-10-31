"""Encode outbound messages and deliver them to registered client connections."""

from collections.abc import Iterable

from aottg_server.ports.connections import ClientConnection, Delivery

from .codec import encode_event, encode_response
from .messages import EventMessage, OperationResponse


class ClientGateway:
    def __init__(self) -> None:
        self._connections: dict[str, ClientConnection] = {}

    def register(self, connection: ClientConnection) -> None:
        self._connections[connection.connection_id] = connection

    def remove(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)

    def respond(self, connection_id: str, response: OperationResponse) -> None:
        connection = self._connections.get(connection_id)
        if connection is not None:
            connection.send(encode_response(response))

    def publish(
        self, connection_id: str, event: EventMessage, delivery: Delivery = Delivery()
    ) -> None:
        self.publish_many((connection_id,), event, delivery)

    def publish_many(
        self, connection_ids: Iterable[str], event: EventMessage, delivery: Delivery = Delivery()
    ) -> None:
        payload = encode_event(event)
        for connection_id in connection_ids:
            connection = self._connections.get(connection_id)
            if connection is not None:
                connection.send(payload, delivery)
