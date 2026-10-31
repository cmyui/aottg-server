"""Exercise controllers through the real codec and dispatcher boundary."""

from dataclasses import dataclass, field

import pytest

from aottg_server.__main__ import ServerSettings, build_dispatcher
from aottg_server.adapters.photon.codec import BinaryReader, encode_parameters
from aottg_server.adapters.photon.dispatcher import MessageDispatcher
from aottg_server.adapters.photon.messages import Parameters
from aottg_server.domain import values as v
from aottg_server.ports.connections import Delivery, ServerRole


@dataclass
class Client:
    connection_id: str
    role: ServerRole = ServerRole.GAME
    messages: list[bytes] = field(default_factory=list)

    def send(self, payload: bytes, delivery: Delivery = Delivery()) -> None:
        self.messages.append(payload)

    def close(self) -> None:
        pass

    def request(self, dispatcher: MessageDispatcher, code: int, parameters: Parameters) -> None:
        dispatcher.received(
            self, bytes([0xF3, 2, code]) + encode_parameters(parameters), Delivery()
        )

    def response(self, code: int) -> tuple[int, Parameters]:
        payload = next(
            message for message in reversed(self.messages) if message[:3] == bytes([0xF3, 3, code])
        )
        reader = BinaryReader(payload[3:])
        status = reader.signed_integer(2)
        reader.read_value()  # Optional error description.
        return status, reader.read_parameters()

    def events(self, code: int) -> list[Parameters]:
        return [
            BinaryReader(message[3:]).read_parameters()
            for message in self.messages
            if message[:3] == bytes([0xF3, 4, code])
        ]


@pytest.fixture
def game() -> tuple[MessageDispatcher, Client, Client]:
    dispatcher = build_dispatcher(ServerSettings("127.0.0.1", "127.0.0.1", 5055, False))
    host, guest = Client("host"), Client("guest")
    for client in (host, guest):
        client.request(dispatcher, 230, {225: v.StringValue(client.connection_id)})
    host.request(dispatcher, 227, {255: v.StringValue("city")})
    guest.request(dispatcher, 226, {255: v.StringValue("city")})
    return dispatcher, host, guest


def test_host_transfer_routes_master_events_and_restores_late_joiner(
    game: tuple[MessageDispatcher, Client, Client],
) -> None:
    dispatcher, host, guest = game
    host.request(
        dispatcher,
        253,
        {244: v.ByteValue(208), 245: v.PropertyTable({v.ByteValue(1): v.IntegerValue(2)})},
    )
    host.request(
        dispatcher, 253, {244: v.ByteValue(200), 246: v.ByteValue(2), 245: v.StringValue("damage")}
    )
    assert len(guest.events(200)) == 1
    assert not host.events(200)
    late = Client("late")
    late.request(dispatcher, 230, {})
    late.request(dispatcher, 226, {255: v.StringValue("city")})
    assert late.events(208) == [
        {254: v.IntegerValue(0), 245: v.PropertyTable({v.ByteValue(1): v.IntegerValue(2)})}
    ]
    host.request(
        dispatcher,
        253,
        {244: v.ByteValue(208), 245: v.PropertyTable({v.ByteValue(1): v.IntegerValue(1)})},
    )
    assert host.response(253)[0] == -3
    dispatcher.disconnected(guest)
    late.request(dispatcher, 253, {244: v.ByteValue(200), 246: v.ByteValue(2)})
    assert len(host.events(200)) == 1


def test_only_master_can_send_kick_notification(
    game: tuple[MessageDispatcher, Client, Client],
) -> None:
    dispatcher, host, guest = game
    guest.request(
        dispatcher, 253, {244: v.ByteValue(203), 252: v.TypedArray(105, (v.IntegerValue(1),))}
    )
    assert guest.response(253)[0] == -3
    assert not host.events(203)
    host.request(
        dispatcher, 253, {244: v.ByteValue(203), 252: v.TypedArray(105, (v.IntegerValue(2),))}
    )
    assert len(guest.events(203)) == 1


@pytest.mark.parametrize("code", [198, 199])
def test_server_administration_is_never_relayed_or_cached(
    game: tuple[MessageDispatcher, Client, Client], code: int
) -> None:
    dispatcher, host, guest = game
    host.request(
        dispatcher,
        253,
        {
            244: v.ByteValue(code),
            245: v.PropertyTable({v.ByteValue(0): v.StringValue("private-admin-input")}),
            247: v.ByteValue(4),
            246: v.ByteValue(1),
        },
    )
    assert host.response(253)[0] == -3
    assert not host.events(code)
    assert not guest.events(code)
    late = Client("late")
    late.request(dispatcher, 230, {})
    late.request(dispatcher, 226, {255: v.StringValue("city")})
    assert not late.events(code)
    assert all(b"private-admin-input" not in message for message in host.messages)


def test_reserved_property_compare_and_swap_is_atomic(
    game: tuple[MessageDispatcher, Client, Client],
) -> None:
    dispatcher, host, _ = game
    host.request(
        dispatcher,
        252,
        {
            251: v.PropertyTable(
                {v.ByteValue(253): v.BooleanValue(False), v.StringValue("round"): v.IntegerValue(2)}
            ),
            231: v.PropertyTable({v.ByteValue(253): v.BooleanValue(False)}),
            250: v.BooleanValue(True),
        },
    )
    assert host.response(252)[0] == -2
    assert not host.events(253)
    host.request(dispatcher, 251, {})
    properties = host.response(251)[1][248]
    assert isinstance(properties, v.PropertyTable)
    assert properties.entries[v.ByteValue(253)] == v.BooleanValue(True)
    assert v.StringValue("round") not in properties.entries


def test_silent_property_update_is_not_broadcast(
    game: tuple[MessageDispatcher, Client, Client],
) -> None:
    dispatcher, host, guest = game
    host.request(
        dispatcher,
        252,
        {
            254: v.IntegerValue(1),
            251: v.PropertyTable({v.StringValue("name"): v.StringValue("one")}),
        },
    )
    assert host.response(252)[0] == 0
    assert not guest.events(253)


def test_round_restart_clears_all_owners_before_new_world_replay(
    game: tuple[MessageDispatcher, Client, Client],
) -> None:
    dispatcher, host, guest = game
    for client, event_code, cache_mode in ((host, 202, 4), (guest, 200, 4), (host, 202, 5)):
        client.request(
            dispatcher,
            253,
            {244: v.ByteValue(event_code), 247: v.ByteValue(cache_mode), 245: v.StringValue("old")},
        )
    # PUN DestroyAll first removes the entire cache (including global entries),
    # then tells existing clients to destroy their local objects and load the scene.
    host.request(dispatcher, 253, {244: v.ByteValue(0), 247: v.ByteValue(6), 246: v.ByteValue(2)})
    host.request(
        dispatcher,
        253,
        {244: v.ByteValue(207), 245: v.PropertyTable({v.ByteValue(0): v.IntegerValue(-1)})},
    )
    host.request(dispatcher, 253, {244: v.ByteValue(200), 245: v.StringValue("RPCLoadLevel")})
    new_spawn = v.PropertyTable(
        {v.ByteValue(0): v.StringValue("TITAN"), v.ByteValue(7): v.IntegerValue(1001)}
    )
    host.request(dispatcher, 253, {244: v.ByteValue(202), 247: v.ByteValue(4), 245: new_spawn})
    late = Client("late")
    late.request(dispatcher, 230, {})
    late.request(dispatcher, 226, {255: v.StringValue("city")})
    assert late.events(202) == [{254: v.IntegerValue(1), 245: new_spawn}]
    assert not late.events(200)
    assert not late.events(207)
    live_events = [message[2] for message in guest.messages if message[:2] == b"\xf3\x04"]
    assert live_events[-3:] == [200, 202, 255]  # Load, new world, late-player join.
    assert live_events[live_events.index(207) + 1] == 200


def test_boolean_join_or_create_and_lobby_property_filtering() -> None:
    dispatcher = build_dispatcher(ServerSettings("127.0.0.1", "127.0.0.1", 5055, False))
    host, observer = Client("host"), Client("observer", ServerRole.MASTER)
    for client in (host, observer):
        client.request(dispatcher, 230, {})
    host.request(
        dispatcher,
        226,
        {
            255: v.StringValue("city"),
            215: v.BooleanValue(True),
            248: v.PropertyTable(
                {
                    v.StringValue("map"): v.StringValue("city"),
                    v.StringValue("private"): v.IntegerValue(123),
                    v.ByteValue(250): v.StringArray((v.StringValue("map"),)),
                }
            ),
        },
    )
    assert host.response(226)[0] == 0
    observer.request(dispatcher, 229, {})
    listing = observer.events(230)[0][222]
    assert isinstance(listing, v.PropertyTable)
    room = listing.entries[v.StringValue("city")]
    assert isinstance(room, v.PropertyTable)
    assert v.StringValue("map") in room.entries
    assert v.StringValue("private") not in room.entries
    observer.request(
        dispatcher, 225, {248: v.PropertyTable({v.StringValue("map"): v.StringValue("city")})}
    )
    assert observer.response(225)[0] == 0
    observer.request(
        dispatcher, 225, {248: v.PropertyTable({v.StringValue("private"): v.IntegerValue(123)})}
    )
    assert observer.response(225)[0] == 32760
