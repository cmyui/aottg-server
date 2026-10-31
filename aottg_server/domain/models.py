from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto

from .values import Properties, Value


class RoomFailure(Enum):
    NOT_FOUND = auto()
    ALREADY_EXISTS = auto()
    FULL = auto()
    CLOSED = auto()
    NOT_JOINED = auto()
    INVALID_REQUEST = auto()
    PROPERTY_CONFLICT = auto()
    CACHE_FULL = auto()


class RoomError(Exception):
    def __init__(self, reason: RoomFailure, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ReceiverGroup(IntEnum):
    OTHERS = 0
    ALL = 1
    MASTER = 2


class MatchmakingMode(IntEnum):
    FILL_OLDEST = 0
    SERIAL = 1
    RANDOM = 2


class CachePolicy(IntEnum):
    NONE = 0
    MERGE = 1
    REPLACE = 2
    REMOVE = 3
    ADD = 4
    ADD_GLOBAL = 5
    REMOVE_MATCHING = 6
    REMOVE_DEPARTED = 7


@dataclass(frozen=True)
class RoomKey:
    namespace: str
    name: str


@dataclass
class Session:
    connection_id: str
    user_id: str
    namespace: str
    lobby_name: str | None = None
    room_key: RoomKey | None = None
    actor_id: int | None = None
    interest_groups: set[int] = field(default_factory=set)


@dataclass
class Player:
    actor_id: int
    connection_id: str
    properties: Properties


@dataclass(frozen=True)
class CachedEvent:
    code: int
    sender_id: int
    payload: Value
    channel: int


@dataclass
class Room:
    key: RoomKey
    lobby_name: str
    max_players: int
    is_open: bool
    is_visible: bool
    cleanup_cache_on_leave: bool
    properties: Properties
    players: dict[int, Player] = field(default_factory=dict)
    cached_events: list[CachedEvent] = field(default_factory=list)
    lobby_property_names: tuple[str, ...] = ()
    next_actor_id: int = 1
    master_actor_id: int = 0


@dataclass(frozen=True)
class CreateRoom:
    name: str
    lobby_name: str = ""
    max_players: int = 0
    is_open: bool = True
    is_visible: bool = True
    cleanup_cache_on_leave: bool = True
    properties: Properties = field(default_factory=dict)
    lobby_property_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class JoinResult:
    room: Room
    player: Player
    cached_events: tuple[CachedEvent, ...]


@dataclass(frozen=True)
class LeaveResult:
    room: Room
    actor_id: int
    room_removed: bool


@dataclass(frozen=True)
class RaiseEvent:
    code: int
    payload: Value
    receivers: ReceiverGroup = ReceiverGroup.OTHERS
    cache_policy: CachePolicy = CachePolicy.NONE
    target_actors: tuple[int, ...] | None = None
    interest_group: int = 0
    channel: int = 0


@dataclass(frozen=True)
class RoomChanges:
    max_players: int | None = None
    is_open: bool | None = None
    is_visible: bool | None = None
    master_actor_id: int | None = None
    cleanup_cache_on_leave: bool | None = None
    lobby_property_names: tuple[str, ...] | None = None
