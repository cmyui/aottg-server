"""ENet framing. Parsing validates complete packets before changing peer state."""

import struct
from dataclasses import dataclass
from typing import cast

from .codes import CommandType
from .messages import ProtocolError

PACKET_HEADER = struct.Struct(">HBBII")
COMMAND_HEADER = struct.Struct(">BBBBII")
CONTROL_CHANNEL = 255
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_PENDING_COMMANDS = 2048


@dataclass(frozen=True)
class Command:
    kind: CommandType
    channel: int
    reliable: bool
    sequence: int
    payload: bytes = b""

    def encode(self) -> bytes:
        return (
            COMMAND_HEADER.pack(
                self.kind,
                self.channel,
                int(self.reliable),
                0,
                COMMAND_HEADER.size + len(self.payload),
                self.sequence,
            )
            + self.payload
        )


@dataclass(frozen=True)
class Datagram:
    peer_id: int
    timestamp: int
    challenge: int
    commands: tuple[Command, ...]

    def encode(self) -> bytes:
        return PACKET_HEADER.pack(
            self.peer_id,
            0,
            len(self.commands),
            self.timestamp,
            self.challenge,
        ) + b"".join(command.encode() for command in self.commands)

    @classmethod
    def decode(cls, data: bytes) -> "Datagram":
        if len(data) < PACKET_HEADER.size:
            raise ProtocolError("Truncated datagram")
        peer_id, flags, count, timestamp, challenge = cast(
            tuple[int, int, int, int, int], PACKET_HEADER.unpack_from(data)
        )
        if flags != 0:
            raise ProtocolError("Encrypted datagram framing and CRC mode are unsupported")
        position = PACKET_HEADER.size
        commands: list[Command] = []
        for _ in range(count):
            if position + COMMAND_HEADER.size > len(data):
                raise ProtocolError("Truncated command header")
            kind, channel, flags, _, size, sequence = cast(
                tuple[int, int, int, int, int, int], COMMAND_HEADER.unpack_from(data, position)
            )
            if size < COMMAND_HEADER.size or position + size > len(data):
                raise ProtocolError("Invalid command length")
            commands.append(
                Command(
                    CommandType(kind),
                    channel,
                    bool(flags & 1),
                    sequence,
                    data[position + COMMAND_HEADER.size : position + size],
                )
            )
            position += size
        if position != len(data):
            raise ProtocolError("Trailing datagram data")
        return cls(peer_id, timestamp, challenge, tuple(commands))


@dataclass
class FragmentAssembly:
    fragment_count: int
    total_length: int
    parts: dict[int, tuple[int, bytes]]

    def add(self, index: int, offset: int, payload: bytes) -> bytes | None:
        if not 0 <= index < self.fragment_count or offset + len(payload) > self.total_length:
            raise ProtocolError("Invalid fragment bounds")
        self.parts[index] = (offset, payload)
        if len(self.parts) != self.fragment_count:
            return None
        message = bytearray()
        for fragment_index in range(self.fragment_count):
            part_offset, part = self.parts[fragment_index]
            if part_offset != len(message):
                raise ProtocolError("Overlapping or missing fragment data")
            message.extend(part)
        if len(message) != self.total_length:
            raise ProtocolError("Incomplete fragmented message")
        return bytes(message)
