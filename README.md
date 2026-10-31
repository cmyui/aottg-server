# AoTTG server

An independent Python multiplayer server for the AoTTG RC client, built for
self-hosting on macOS and Linux. The server manages connections, lobbies, rooms,
player properties, and gameplay event delivery. The game clients run movement,
combat, and titan simulation; the room's master client coordinates shared gameplay.

The original RC client can connect through **Multiplayer → LAN**. No client patch
or hosts-file change is required. Game assets and client SDK binaries are not
included in this repository.

## Run locally

Requirements: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run these commands from the repository directory:

```sh
uv sync --locked
uv run aottg-server
```

On macOS, double-click `Start Server.command` to run the same server in Terminal.
Stop it with Ctrl-C. Server state is held in memory; stopping it ends all rooms.

In the game, select **Multiplayer → LAN**, enter `127.0.0.1`, leave the port at
`5055`, and connect. Create a room from the lobby. The server listens on two UDP
ports: `5055` for the lobby/master endpoint and `5056` for the game endpoint.
Clients automatically reconnect to the game endpoint when creating or joining a
room.

For the Intel macOS RC app on Apple Silicon, Rosetta is required. If the legacy
Unity startup dialog crashes, enable **Only show this dialog if the option key is
down** in that dialog before starting. A windowed configuration is useful for
initial testing.

## Host on another machine

The same command works on Linux. Bind to the network interface and advertise the
IP address or hostname that players can reach:

```sh
uv sync --locked --no-dev
uv run --no-dev aottg-server --bind 0.0.0.0 --advertise 192.168.1.50
```

Replace the example address with the server's actual LAN or VPN address. Clients
enter that address in the game's LAN menu. Both UDP ports must be reachable; an
SSH TCP tunnel does not forward them. `--port N` changes the master port to `N`
and the game port to `N+1`. `--verbose` enables per-operation debugging.

The default bind address is loopback. For private games across the internet, use
a private VPN or restrict both UDP ports to your players. This implementation
accepts guest identities and does not verify account ownership. The game's room
password is a client-side feature, not server access control. Its legacy encryption
handshake is implemented for compatibility; it is not a modern authenticated
transport. Responses and gameplay events are sent without encryption.

## Architecture

| Package | Responsibility |
| --- | --- |
| `domain` | Typed room, player, session, event, and value models |
| `services` | Room lifecycle, property updates, recipient selection, event cache |
| `ports` | Typed connection and repository interfaces |
| `repositories` | In-memory implementations of repository interfaces |
| `controllers` | Validate operation parameters and translate requests/results |
| `adapters/photon` | Binary codec, legacy handshake, UDP reliability and message delivery |
| `__main__.py` | CLI configuration and explicit dependency composition |

Services have no dependency on sockets, asyncio, or Photon operation codes. All
room mutations run synchronously on one asyncio event loop. Repositories store
live domain objects; there is no database or cross-process state sharing.

The codec preserves byte, integer, float, array, table, dictionary, and custom
value distinctions. These matter to the client deserializer, even when Python
would normally represent several of them with the same built-in type. Gameplay
payloads are relayed without interpreting game-specific RPC contents.

The transport acknowledges and retransmits reliable messages, orders each channel,
deduplicates packets, rejects stale unreliable updates, and reassembles fragments.
It limits peers, queued commands, message size, nesting, and cached event count.
It is intended for small, trusted groups, not an unrestricted public service.

## Development and verification

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

Mypy runs in strict mode over the application and tests. CI runs these checks on
macOS and Linux with Python 3.11 and 3.13. Tests cover wire types, malformed input,
room isolation/capacity, property compare-and-swap, event routing/cache cleanup,
ordered delivery, duplicates, retransmission, and fragment reconstruction.

An additional integration harness uses the networking SDK from your own game
installation. It requires .NET 8 or newer and a running server:

```sh
dotnet run --project tools/InteropCheck \
  -p:GameManagedPath=/Applications/AottgRC.app/Contents/Data/Managed \
  -- 127.0.0.1:5055
```

It creates an isolated application namespace, connects three SDK clients, and
checks encrypted authentication, lobby discovery, redirects, room creation/join,
late-join replay, properties, a 24 KB fragmented event, unreliable delivery, host
departure, and empty-room removal. The SDK is referenced locally and must not be
committed. CI does not run this harness because the SDK is not distributed here.

## Compatibility scope

The target is the RC client using Photon UDP and GpBinaryV16. Supported operations
include guest authentication, default/named lobby entry, create/join by room name,
leave, property reads/updates, event relay/cache, and interest groups.

Not implemented: random matchmaking, join-or-create/rejoin, actor TTL, persisted
rooms, cloud authentication, friends, SQL lobbies, legacy cache-merge, TCP/WebSocket
transports, and modern Photon protocols. All custom room properties are currently
included in lobby updates. Compare-and-swap applies to custom/player properties;
reserved room metadata is not part of that comparison. Master selection on leave
uses the lowest remaining actor ID. Game-specific master-switch RPCs are relayed
without changing the server's master-recipient selection.

The game can load a local lobby and room. Full gameplay across two RC game
instances remains a separate manual compatibility check; the SDK harness verifies
network behavior but does not run Unity gameplay.

Protocol references:
[Photon binary protocol](https://doc.photonengine.com/server/v4/reference/binary-protocol),
[serialization](https://doc.photonengine.com/server/v4/reference/serialization-in-photon),
[load balancing](https://doc.photonengine.com/server/v4/applications/loadbalancing/application),
[cached events](https://doc.photonengine.com/pun/v1/gameplay/cached-events).
