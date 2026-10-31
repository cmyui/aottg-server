from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ServerRole(Enum):
    MASTER = "master"
    GAME = "game"


@dataclass(frozen=True)
class Delivery:
    channel: int = 0
    reliable: bool = True


class ClientConnection(Protocol):
    @property
    def connection_id(self) -> str: ...

    @property
    def role(self) -> ServerRole: ...

    def send(self, payload: bytes, delivery: Delivery = Delivery()) -> None: ...

    def close(self) -> None: ...


class ConnectionHandler(Protocol):
    def received(
        self, connection: ClientConnection, payload: bytes, delivery: Delivery
    ) -> None: ...

    def disconnected(self, connection: ClientConnection) -> None: ...
