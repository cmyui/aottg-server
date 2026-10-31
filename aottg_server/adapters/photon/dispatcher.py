"""Handle message framing and encryption before invoking operation controllers."""

import logging

from aottg_server.controllers.multiplayer import MultiplayerController
from aottg_server.domain.values import BytesValue
from aottg_server.ports.connections import ClientConnection, Delivery

from .codec import decode_request
from .codes import ReturnCode
from .encryption import LegacyCipher
from .gateway import ClientGateway
from .messages import OperationResponse, ProtocolError

logger = logging.getLogger(__name__)


class MessageDispatcher:
    def __init__(self, controller: MultiplayerController, gateway: ClientGateway) -> None:
        self._controller = controller
        self._gateway = gateway
        self._ciphers: dict[str, LegacyCipher] = {}

    def received(self, connection: ClientConnection, payload: bytes, delivery: Delivery) -> None:
        self._gateway.register(connection)
        if len(payload) < 2:
            raise ProtocolError("Truncated message header")
        if payload[1] & 0x80:
            cipher = self._ciphers.get(connection.connection_id)
            if cipher is None:
                raise ProtocolError("Encryption was not established")
            payload = bytes([payload[0], payload[1] & 0x7F]) + cipher.decrypt(payload[2:])
        request = decode_request(payload)
        if request.internal:
            if request.code != 0:
                raise ProtocolError("Unsupported internal operation")
            key = request.parameters.get(1)
            if not isinstance(key, BytesValue):
                raise ProtocolError("Handshake requires a public key")
            cipher = LegacyCipher(key.value)
            self._ciphers[connection.connection_id] = cipher
            self._gateway.respond(
                connection.connection_id,
                OperationResponse(
                    request.code,
                    {1: BytesValue(cipher.public_key)},
                    internal=True,
                ),
            )
            return
        logger.debug("%s operation %d", connection.role.value, request.code)
        try:
            self._controller.handle(connection, request, delivery)
        except (ProtocolError, ValueError) as exc:
            self._gateway.respond(
                connection.connection_id,
                OperationResponse(
                    request.code,
                    {},
                    ReturnCode.INVALID_PARAMETERS,
                    str(exc),
                ),
            )

    def disconnected(self, connection: ClientConnection) -> None:
        self._gateway.remove(connection.connection_id)
        self._ciphers.pop(connection.connection_id, None)
        self._controller.disconnected(connection.connection_id)
