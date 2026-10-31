"""Command-line entry point and dependency composition."""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from functools import partial

from aottg_server.adapters.photon.dispatcher import MessageDispatcher
from aottg_server.adapters.photon.gateway import ClientGateway
from aottg_server.adapters.photon.udp import UdpEndpoint
from aottg_server.controllers.multiplayer import MultiplayerController
from aottg_server.ports.connections import ServerRole
from aottg_server.repositories.memory import InMemoryRoomRepository, InMemorySessionRepository
from aottg_server.services.events import EventService
from aottg_server.services.rooms import RoomService


@dataclass(frozen=True)
class ServerSettings:
    bind: str
    advertise: str
    port: int
    verbose: bool


def build_dispatcher(settings: ServerSettings) -> MessageDispatcher:
    sessions = InMemorySessionRepository()
    gateway = ClientGateway()
    controller = MultiplayerController(
        RoomService(InMemoryRoomRepository()),
        EventService(sessions),
        sessions,
        gateway,
        f"{settings.advertise}:{settings.port + 1}",
    )
    return MessageDispatcher(controller, gateway)


async def run(settings: ServerSettings) -> None:
    loop = asyncio.get_running_loop()
    dispatcher = build_dispatcher(settings)
    transports: list[asyncio.DatagramTransport] = []
    try:
        for role, port in (
            (ServerRole.MASTER, settings.port),
            (ServerRole.GAME, settings.port + 1),
        ):
            transport, _ = await loop.create_datagram_endpoint(
                partial(UdpEndpoint, dispatcher, role),
                local_addr=(settings.bind, port),
            )
            transports.append(transport)
            logging.info("%s listening on %s:%s", role.value, settings.bind, port)
        await asyncio.Event().wait()
    finally:
        for transport in transports:
            transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-hosted AoTTG RC multiplayer server")
    parser.add_argument("--bind", default="127.0.0.1", help="Local IPv4 interface to listen on")
    parser.add_argument(
        "--advertise", default="127.0.0.1", help="Game-server host reachable by clients"
    )
    parser.add_argument(
        "--port", type=int, default=5055, help="Master UDP port; game uses port + 1"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    settings = ServerSettings(args.bind, args.advertise, args.port, args.verbose)
    if not 1 <= settings.port <= 65534:
        parser.error("--port must be between 1 and 65534")
    if ":" in settings.advertise or not settings.advertise:
        parser.error("--advertise must be an IPv4 address or hostname without a port")
    logging.basicConfig(
        level=logging.DEBUG if settings.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
