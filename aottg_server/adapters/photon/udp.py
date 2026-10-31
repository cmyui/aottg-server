"""Reliable UDP adapter. Application operations are delegated through a port."""

import asyncio
import logging
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from aottg_server.ports.connections import ConnectionHandler, Delivery, ServerRole

from .codes import CommandType
from .datagrams import (
    CONTROL_CHANNEL,
    MAX_MESSAGE_BYTES,
    MAX_PENDING_COMMANDS,
    Command,
    Datagram,
    FragmentAssembly,
)
from .messages import ProtocolError

logger = logging.getLogger(__name__)
NetworkAddress = tuple[str, int]
CommandKey = tuple[int, int]
PEER_TIMEOUT_SECONDS = 20
MAX_IN_FLIGHT_COMMANDS = 64
MAX_OUTBOUND_COMMANDS = 32768
MAX_OUTBOUND_BYTES = 32 * 1024 * 1024
SEQUENCE_MASK = 0xFFFFFFFF
PACKET_FLUSH_SECONDS = 0.002


def next_sequence(sequence: int) -> int:
    return (sequence + 1) & SEQUENCE_MASK


def is_newer(sequence: int, previous: int) -> bool:
    distance = (sequence - previous) & SEQUENCE_MASK
    return 0 < distance < 0x80000000


INITIALIZE_REQUEST = b"\xf3\x00"
INITIALIZE_RESPONSE = b"\xf3\x01\x00\x00\x00\x00\x00"


def timestamp_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


@dataclass
class PendingCommand:
    command: Command
    sent_at: float
    attempts: int = 0


@dataclass(eq=False)
class UdpConnection:
    endpoint: "UdpEndpoint"
    address: NetworkAddress
    peer_id: int
    challenge: int
    mtu: int
    connection_id: str = field(default_factory=lambda: uuid4().hex)
    last_received_at: float = field(default_factory=time.monotonic)
    initialized: bool = False
    closed: bool = False
    outgoing_sequences: dict[int, int] = field(default_factory=dict)
    outgoing_unreliable_sequences: dict[int, int] = field(default_factory=dict)
    incoming_sequences: dict[int, int] = field(default_factory=dict)
    incoming_unreliable_sequences: dict[int, int] = field(default_factory=dict)
    receive_queue: dict[CommandKey, Command] = field(default_factory=dict)
    fragments: dict[CommandKey, FragmentAssembly] = field(default_factory=dict)
    pending_acknowledgments: dict[CommandKey, PendingCommand] = field(default_factory=dict)

    outbound_queue: deque[Command] = field(default_factory=deque)
    outbound_bytes: int = 0
    packet_commands: list[Command] = field(default_factory=list)
    packet_bytes: int = 12

    @property
    def role(self) -> ServerRole:
        return self.endpoint.role

    def transmit(self, command: Command) -> None:
        self.endpoint.send_command(self, command)

    def send_reliable(self, kind: CommandType, channel: int, payload: bytes = b"") -> None:
        if self.closed:
            return
        size = 12 + len(payload)
        if (
            len(self.outbound_queue) >= MAX_OUTBOUND_COMMANDS
            or self.outbound_bytes + size > MAX_OUTBOUND_BYTES
        ):
            logger.warning("Disconnecting slow consumer %s: outbound queue full", self.address)
            self.close()
            return
        sequence = next_sequence(self.outgoing_sequences.get(channel, 0))
        self.outgoing_sequences[channel] = sequence
        self.outbound_queue.append(Command(kind, channel, True, sequence, payload))
        self.outbound_bytes += size
        self._flush_outbound()

    def _flush_outbound(self) -> None:
        while self.outbound_queue and len(self.pending_acknowledgments) < MAX_IN_FLIGHT_COMMANDS:
            command = self.outbound_queue.popleft()
            self.outbound_bytes -= 12 + len(command.payload)
            self.pending_acknowledgments[command.channel, command.sequence] = PendingCommand(
                command,
                time.monotonic(),
            )
            self.transmit(command)

    def send(self, payload: bytes, delivery: Delivery = Delivery()) -> None:
        if self.closed:
            return
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolError("Message exceeds the configured size limit")
        if len(payload) > self.mtu - 28:
            self._send_fragments(payload, delivery.channel)
        elif delivery.reliable:
            self.send_reliable(CommandType.RELIABLE, delivery.channel, payload)
        else:
            sequence = next_sequence(self.outgoing_unreliable_sequences.get(delivery.channel, 0))
            self.outgoing_unreliable_sequences[delivery.channel] = sequence
            self.transmit(
                Command(
                    CommandType.UNRELIABLE,
                    delivery.channel,
                    False,
                    self.outgoing_sequences.get(delivery.channel, 0),
                    sequence.to_bytes(4, "big") + payload,
                )
            )

    def _send_fragments(self, payload: bytes, channel: int) -> None:
        fragment_size = self.mtu - 44
        count = (len(payload) + fragment_size - 1) // fragment_size
        start_sequence = next_sequence(self.outgoing_sequences.get(channel, 0))
        for index in range(count):
            offset = index * fragment_size
            header = struct.pack(">IIIII", start_sequence, count, index, len(payload), offset)
            self.send_reliable(
                CommandType.FRAGMENT, channel, header + payload[offset : offset + fragment_size]
            )

    def acknowledge(self, command: Command, remote_timestamp: int) -> None:
        self.transmit(
            Command(
                CommandType.ACKNOWLEDGE,
                command.channel,
                False,
                0,
                struct.pack(">II", command.sequence, remote_timestamp),
            )
        )

    def receive(self, command: Command, remote_timestamp: int) -> None:
        self.last_received_at = time.monotonic()
        if command.kind == CommandType.ACKNOWLEDGE:
            if len(command.payload) != 8:
                raise ProtocolError("Malformed acknowledgment")
            sequence = int.from_bytes(command.payload[:4], "big")
            self.pending_acknowledgments.pop((command.channel, sequence), None)
            self._flush_outbound()
        elif command.reliable:
            self.acknowledge(command, remote_timestamp)
            self._receive_reliable(command)
        else:
            self._dispatch(command)

    def _receive_reliable(self, command: Command) -> None:
        received = self.incoming_sequences.get(command.channel, 0)
        if not is_newer(command.sequence, received):
            return  # A retransmission is acknowledged, but never delivered twice.
        if (command.sequence - received) & SEQUENCE_MASK > MAX_PENDING_COMMANDS or len(
            self.receive_queue
        ) >= MAX_PENDING_COMMANDS:
            self.close()
            return
        self.receive_queue[command.channel, command.sequence] = command
        while not self.closed:
            expected_sequence = next_sequence(self.incoming_sequences.get(command.channel, 0))
            queued = self.receive_queue.pop((command.channel, expected_sequence), None)
            if queued is None:
                return
            self.incoming_sequences[command.channel] = expected_sequence
            self._dispatch(queued)

    def _dispatch(self, command: Command) -> None:
        match command.kind:
            case CommandType.DISCONNECT:
                self.close()
            case CommandType.PING | CommandType.FETCH_TIMESTAMP:
                return
            case CommandType.RELIABLE:
                self._deliver(command.payload, Delivery(command.channel))
            case CommandType.UNRELIABLE:
                self._receive_unreliable(command)
            case CommandType.FRAGMENT:
                self._receive_fragment(command)
            case _:
                raise ProtocolError(f"Unexpected command: {command.kind.name}")

    def _receive_unreliable(self, command: Command) -> None:
        if len(command.payload) < 4:
            raise ProtocolError("Truncated unreliable command")
        sequence = int.from_bytes(command.payload[:4], "big")
        if not is_newer(sequence, self.incoming_unreliable_sequences.get(command.channel, 0)):
            return
        if is_newer(command.sequence, self.incoming_sequences.get(command.channel, 0)):
            return  # A future movement update will supersede this out-of-order update.
        self.incoming_unreliable_sequences[command.channel] = sequence
        self._deliver(command.payload[4:], Delivery(command.channel, reliable=False))

    def _receive_fragment(self, command: Command) -> None:
        if len(command.payload) < 20:
            raise ProtocolError("Truncated fragment")
        start, count, index, total, offset = cast(
            tuple[int, int, int, int, int], struct.unpack_from(">IIIII", command.payload)
        )
        if (
            not 0 < count <= MAX_PENDING_COMMANDS
            or not 0 < total <= MAX_MESSAGE_BYTES
            or command.sequence != (start + index) & SEQUENCE_MASK
        ):
            raise ProtocolError("Invalid fragment header")
        key = command.channel, start
        assembly = self.fragments.get(key)
        if assembly is None:
            if len(self.fragments) >= 8:
                raise ProtocolError("Too many incomplete messages")
            assembly = FragmentAssembly(count, total, {})
            self.fragments[key] = assembly
        if (assembly.fragment_count, assembly.total_length) != (count, total):
            raise ProtocolError("Inconsistent fragment metadata")
        payload = assembly.add(index, offset, command.payload[20:])
        if payload is not None:
            del self.fragments[key]
            self._deliver(payload, Delivery(command.channel))

    def _deliver(self, payload: bytes, delivery: Delivery) -> None:
        if payload.startswith(INITIALIZE_REQUEST):
            if not self.initialized:
                self.initialized = True
                self.send(INITIALIZE_RESPONSE)
        elif self.initialized:
            self.endpoint.handler.received(self, payload, delivery)

    def retry_pending(self, now: float) -> None:
        for pending in tuple(self.pending_acknowledgments.values()):
            delay = min(0.3 * 2**pending.attempts, 2.0)
            if now - pending.sent_at <= delay:
                continue
            if pending.attempts >= 8:
                self.close()
                return
            pending.attempts += 1
            pending.sent_at = now
            self.transmit(pending.command)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.outbound_queue.clear()
            self.outbound_bytes = 0
            self.pending_acknowledgments.clear()
            self.receive_queue.clear()
            self.fragments.clear()
            self.endpoint.remove(self)


class UdpEndpoint(asyncio.DatagramProtocol):
    def __init__(
        self, handler: ConnectionHandler, role: ServerRole, max_connections: int = 512
    ) -> None:
        self.handler = handler
        self.role = role
        self._max_connections = max_connections
        self._connections: dict[NetworkAddress, UdpConnection] = {}
        self._next_peer_id = 1
        self._transport: asyncio.DatagramTransport | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._packet_timer: asyncio.TimerHandle | None = None
        self._buffered_connections: set[UdpConnection] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not isinstance(transport, asyncio.DatagramTransport):
            raise TypeError("UdpEndpoint requires a datagram transport")
        self._transport = transport
        self._tick()

    def connection_lost(self, exc: Exception | None) -> None:
        self._transport = None
        if self._packet_timer:
            self._packet_timer.cancel()
        if self._timer:
            self._timer.cancel()
        for connection in tuple(self._connections.values()):
            connection.close()

    def datagram_received(self, data: bytes, addr: NetworkAddress) -> None:
        try:
            datagram = Datagram.decode(data)
            self._receive(datagram, addr)
        except (ProtocolError, ValueError, UnicodeError, struct.error) as exc:
            logger.debug("Rejected %s packet from %s: %s", self.role.value, addr, exc)

    def error_received(self, exc: Exception) -> None:
        logger.debug("UDP socket error: %s", exc)

    def send_command(self, connection: UdpConnection, command: Command) -> None:
        size = 12 + len(command.payload)
        if (
            connection.packet_bytes + size > connection.mtu
            or len(connection.packet_commands) >= 255
        ):
            self._flush_connection(connection)
        connection.packet_commands.append(command)
        connection.packet_bytes += size
        self._buffered_connections.add(connection)
        if self._packet_timer is None:
            self._packet_timer = asyncio.get_running_loop().call_later(
                PACKET_FLUSH_SECONDS,
                self._flush_packets,
            )

    def _flush_connection(self, connection: UdpConnection) -> None:
        if connection.packet_commands:
            self.send_datagram(
                Datagram(
                    connection.peer_id,
                    timestamp_ms(),
                    connection.challenge,
                    tuple(connection.packet_commands),
                ),
                connection.address,
            )
            connection.packet_commands.clear()
            connection.packet_bytes = 12
        self._buffered_connections.discard(connection)

    def _flush_packets(self) -> None:
        self._packet_timer = None
        for connection in tuple(self._buffered_connections):
            self._flush_connection(connection)

    def send_datagram(self, datagram: Datagram, address: NetworkAddress) -> None:
        if self._transport is not None:
            self._transport.sendto(datagram.encode(), address)

    def _receive(self, datagram: Datagram, address: NetworkAddress) -> None:
        connection = self._connections.get(address)
        for command in datagram.commands:
            if command.kind == CommandType.CONNECT:
                connection = self._connect(datagram, command, address)
            elif (
                connection is not None
                and not connection.closed
                and connection.challenge == datagram.challenge
                and connection.peer_id == datagram.peer_id
            ):
                connection.receive(command, datagram.timestamp)

    def _connect(
        self, datagram: Datagram, command: Command, address: NetworkAddress
    ) -> UdpConnection | None:
        if (
            len(command.payload) != 32
            or command.channel != CONTROL_CHANNEL
            or command.sequence != 1
        ):
            raise ProtocolError("Invalid connection request")
        connection = self._connections.get(address)
        if connection and connection.challenge != datagram.challenge:
            connection.close()
            connection = None
        if connection is None:
            if len(self._connections) >= self._max_connections:
                return None
            mtu = max(576, min(1200, int.from_bytes(command.payload[2:4], "big")))
            connection = UdpConnection(self, address, self._next_peer_id, datagram.challenge, mtu)
            self._next_peer_id = self._next_peer_id % 65534 + 1
            self._connections[address] = connection
            connection.incoming_sequences[CONTROL_CHANNEL] = 1
            connection.send_reliable(
                CommandType.VERIFY_CONNECT,
                CONTROL_CHANNEL,
                connection.peer_id.to_bytes(2, "big") + command.payload[2:],
            )
            logger.info("%s connection established: %s", self.role.value, address)
        connection.acknowledge(command, datagram.timestamp)
        return connection

    def remove(self, connection: UdpConnection) -> None:
        self._flush_connection(connection)
        self._connections.pop(connection.address, None)
        self.handler.disconnected(connection)
        logger.info("%s disconnected: %s", self.role.value, connection.address)

    def _tick(self) -> None:
        now = time.monotonic()
        for connection in tuple(self._connections.values()):
            if now - connection.last_received_at > PEER_TIMEOUT_SECONDS:
                connection.close()
            else:
                connection.retry_pending(now)
        self._timer = asyncio.get_running_loop().call_later(0.1, self._tick)
