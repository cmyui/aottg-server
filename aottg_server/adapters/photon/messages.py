from dataclasses import dataclass
from typing import TypeAlias

from aottg_server.domain.values import Value

Parameters: TypeAlias = dict[int, Value]


class ProtocolError(ValueError):
    """Input is malformed or outside the supported client protocol."""


@dataclass(frozen=True)
class OperationRequest:
    code: int
    parameters: Parameters
    internal: bool = False


@dataclass(frozen=True)
class OperationResponse:
    code: int
    parameters: Parameters
    return_code: int = 0
    message: str | None = None
    internal: bool = False


@dataclass(frozen=True)
class EventMessage:
    code: int
    parameters: Parameters
