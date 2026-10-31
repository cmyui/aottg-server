import struct

import pytest

from aottg_server.adapters.photon.codes import CommandType
from aottg_server.adapters.photon.datagrams import Command, Datagram, FragmentAssembly
from aottg_server.adapters.photon.messages import ProtocolError
from aottg_server.adapters.photon.udp import NetworkAddress, UdpConnection, UdpEndpoint
from aottg_server.ports.connections import ClientConnection, Delivery, ServerRole


class RecordingHandler:
    def __init__(self) -> None:
        self.messages: list[tuple[bytes, Delivery]] = []
        self.departures: list[str] = []

    def received(self, connection: ClientConnection, payload: bytes, delivery: Delivery) -> None:
        self.messages.append((payload, delivery))

    def disconnected(self, connection: ClientConnection) -> None:
        self.departures.append(connection.connection_id)


class RecordingEndpoint(UdpEndpoint):
    def __init__(self, handler: RecordingHandler) -> None:
        super().__init__(handler, ServerRole.GAME)
        self.sent: list[Datagram] = []

    def send_datagram(self, datagram: Datagram, address: NetworkAddress) -> None:
        self.sent.append(datagram)


@pytest.fixture
def peer() -> tuple[UdpConnection, RecordingEndpoint, RecordingHandler]:
    handler = RecordingHandler()
    endpoint = RecordingEndpoint(handler)
    connection = UdpConnection(endpoint, ("127.0.0.1", 9999), 1, 1234, 1200, initialized=True)
    return connection, endpoint, handler


def test_reliable_delivery_orders_packets_and_deduplicates(
    peer: tuple[UdpConnection, RecordingEndpoint, RecordingHandler],
) -> None:
    connection, endpoint, handler = peer
    second = Command(CommandType.RELIABLE, 0, True, 2, b"second")
    first = Command(CommandType.RELIABLE, 0, True, 1, b"first")
    connection.receive(second, 100)
    assert not handler.messages
    connection.receive(first, 101)
    connection.receive(first, 102)
    assert handler.messages == [(b"first", Delivery()), (b"second", Delivery())]
    assert len(endpoint.sent) == 3  # Duplicates still need acknowledgments.


def test_missing_ack_retransmits_until_ack_arrives(
    peer: tuple[UdpConnection, RecordingEndpoint, RecordingHandler],
) -> None:
    connection, endpoint, _ = peer
    connection.send(b"spawn")
    pending = connection.pending_acknowledgments[0, 1]
    connection.retry_pending(pending.sent_at + 1)
    assert len(endpoint.sent) == 2
    assert endpoint.sent[0].commands == endpoint.sent[1].commands
    connection.receive(Command(CommandType.ACKNOWLEDGE, 0, False, 0, struct.pack(">II", 1, 0)), 0)
    connection.retry_pending(pending.sent_at + 100)
    assert len(endpoint.sent) == 2
    assert not connection.pending_acknowledgments


def test_unreliable_updates_discard_stale_messages(
    peer: tuple[UdpConnection, RecordingEndpoint, RecordingHandler],
) -> None:
    connection, _, handler = peer
    for sequence in (2, 1, 2, 3):
        connection.receive(
            Command(
                CommandType.UNRELIABLE, 0, False, 0, sequence.to_bytes(4, "big") + bytes([sequence])
            ),
            0,
        )
    assert handler.messages == [
        (b"\x02", Delivery(reliable=False)),
        (b"\x03", Delivery(reliable=False)),
    ]


def test_fragment_reassembly_after_reordering(
    peer: tuple[UdpConnection, RecordingEndpoint, RecordingHandler],
) -> None:
    connection, endpoint, handler = peer
    payload = bytes(range(256)) * 100
    connection.send(payload, Delivery(channel=1))
    fragments = [packet.commands[0] for packet in endpoint.sent]
    assert len(fragments) > 1
    for fragment in reversed(fragments):
        connection.receive(fragment, 10)
    assert handler.messages == [(payload, Delivery(channel=1))]
    assert not connection.fragments


def test_retry_exhaustion_disconnects_and_releases_session(
    peer: tuple[UdpConnection, RecordingEndpoint, RecordingHandler],
) -> None:
    connection, _, handler = peer
    connection.send(b"spawn")
    pending = connection.pending_acknowledgments[0, 1]
    pending.attempts = 8
    connection.retry_pending(pending.sent_at + 3)
    assert connection.closed
    assert handler.departures == [connection.connection_id]


@pytest.mark.parametrize("payload", [b"", bytes(11), bytes.fromhex("000100010000000000000000")])
def test_malformed_datagram_is_rejected(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        Datagram.decode(payload)


def test_overlapping_fragments_are_rejected() -> None:
    assembly = FragmentAssembly(2, 6, {})
    assert assembly.add(0, 0, b"abcd") is None
    with pytest.raises(ProtocolError):
        assembly.add(1, 2, b"ef")
