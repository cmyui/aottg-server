"""Verify gameplay through three actual Unity clients, recording observed state."""

import argparse
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from control import command, snapshot

Snapshots = dict[str, dict[str, str]]
PLAYERS = ("host", "guest", "late")


@dataclass(frozen=True)
class Observation:
    check: str
    passed: bool
    clients: Snapshots


def ids(value: str) -> set[int]:
    return {int(item) for item in value.split(",") if item}


def positions(state: dict[str, str]) -> dict[int, tuple[float, ...]]:
    return {
        int(item.split(":")[0]): tuple(float(number) for number in item.split(":")[1].split(","))
        for item in state["heroPositions"].split(";")
        if item
    }


class GameplayRun:
    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.port = port
        self.observations: list[Observation] = []

    def send(self, player: str, name: str, *arguments: str) -> None:
        command(self.root / player, name, *arguments)

    def wait(
        self, description: str, condition: Callable[[Snapshots], bool], timeout: int = 60
    ) -> Snapshots:
        deadline = time.monotonic() + timeout
        latest: Snapshots = {}
        while time.monotonic() < deadline:
            try:
                latest = {name: snapshot(self.root / name) for name in PLAYERS}
                if condition(latest):
                    self.observations.append(Observation(description, True, latest))
                    self.save()
                    print(f"PASS: {description}", flush=True)
                    return latest
            except (FileNotFoundError, json.JSONDecodeError):
                pass  # A snapshot may be between its temporary write and rename.
            time.sleep(0.2)
        self.observations.append(Observation(description, False, latest))
        self.save()
        raise TimeoutError(f"{description}; inspect {self.root / 'report.json'} and the Unity logs")

    def save(self) -> None:
        (self.root / "report.json").write_text(
            json.dumps([asdict(item) for item in self.observations], indent=2)
        )

    def verify_logs(self) -> None:
        failures: list[str] = []
        for name in PLAYERS:
            lines = (self.root / f"{name}.log").read_text(errors="replace").splitlines()
            for line in lines:
                if re.search(
                    r"\b\w*Exception\b|Could not Instantiate|RPC method .*not found", line
                ):
                    failures.append(f"{name}: {line}")
        observed = {name: snapshot(self.root / name) for name in PLAYERS}
        self.observations.append(Observation("no Unity runtime exceptions", not failures, observed))
        self.save()
        if failures:
            raise RuntimeError("Unity runtime errors:\n" + "\n".join(failures[:20]))
        print("PASS: no Unity runtime exceptions", flush=True)

    def connect(self) -> None:
        self.wait(
            "all clients are ready to reconnect",
            lambda states: all(
                s["state"] in ("PeerCreated", "Disconnected") for s in states.values()
            ),
        )
        for name in PLAYERS:
            self.send(name, "connect", str(self.port))
        self.wait(
            "all clients reach the lobby",
            lambda states: all(s["state"] == "JoinedLobby" for s in states.values()),
        )

    def joined(self, names: tuple[str, ...], count: int) -> Snapshots:
        return self.wait(
            f"{count} players finish loading the room",
            lambda states: all(
                states[name]["state"] == "Joined"
                and states[name]["players"] == str(count)
                and states[name]["scene"] != "menu"
                and states[name]["mapLoaded"] == "True"
                and states[name]["logicLoaded"] == "True"
                for name in names
            ),
        )

    def city(self) -> None:
        room = f"Probe`The City`normal`120`day``{uuid4().hex}"
        self.send("host", "create", room)
        self.joined(("host",), 1)
        self.send("guest", "join", room)
        self.joined(("host", "guest"), 2)
        self.wait(
            "ten identical titans on both clients",
            lambda s: (
                len(ids(s["host"]["titans"])) == 10
                and ids(s["host"]["titans"]) == ids(s["guest"]["titans"])
            ),
        )
        for name in ("host", "guest"):
            self.send(name, "spawn")
        state = self.wait(
            "both player prefabs and costume properties replicate",
            lambda s: (
                len(ids(s["host"]["heroes"])) == 2
                and ids(s["host"]["heroes"]) == ids(s["guest"]["heroes"])
                and all(
                    s[name]["actor.2.dead"] == "False" and "statSPD=125" in s[name]["properties"]
                    for name in ("host", "guest")
                )
            ),
        )
        guest_view = next(view for view in ids(state["guest"]["heroes"]) if view // 1000 == 2)
        self.send("guest", "move", "10", "150", "20")
        self.wait(
            "player transform reaches the other Unity client",
            lambda s: (
                guest_view in positions(s["host"])
                and math.dist(positions(s["host"])[guest_view], (10, 150, 20)) < 25
            ),
        )
        self.send("guest", "chat", "probe-chat")
        self.wait(
            "chat reaches both clients",
            lambda s: all("probe-chat" in s[name]["chat"] for name in ("host", "guest")),
        )
        titan = min(ids(state["host"]["titans"]))
        self.send("guest", "approach-titan", str(titan))
        self.wait(
            "attacker reaches titan on the owner client",
            lambda s: (
                math.dist(positions(s["host"])[guest_view], positions(s["guest"])[guest_view]) < 2
            ),
        )
        self.send("host", "pause", "true")
        self.wait(
            "pause synchronizes",
            lambda s: all(float(s[name]["timeScale"]) < 0.1 for name in ("host", "guest")),
        )
        self.send("guest", "hit-titan", str(titan), "10000")
        self.wait(
            "guest damage kills host titan and awards synchronized score",
            lambda s: all(
                titan in ids(s[name]["deadTitans"])
                and s[name]["actor.2.kills"] == "1"
                and s[name]["actor.2.total_dmg"] == "10000"
                for name in ("host", "guest")
            ),
        )
        self.send("guest", "die")
        self.wait(
            "death destroys the player and updates the scoreboard",
            lambda s: all(
                guest_view not in ids(s[name]["heroes"])
                and s[name]["actor.2.dead"] == "True"
                and s[name]["actor.2.deaths"] == "1"
                for name in ("host", "guest")
            ),
        )
        self.send("host", "respawn", "2")
        self.wait(
            "targeted respawn creates a new player view",
            lambda s: all(
                len(ids(s[name]["heroes"])) == 2
                and guest_view not in ids(s[name]["heroes"])
                and s[name]["actor.2.dead"] == "False"
                for name in ("host", "guest")
            ),
        )
        self.send("late", "join", room)
        self.joined(PLAYERS, 3)
        self.wait(
            "late join restores prefabs, buffered titan death, score, and pause",
            lambda s: (
                ids(s["late"]["heroes"]) == ids(s["host"]["heroes"])
                and titan in ids(s["late"]["deadTitans"])
                and s["late"]["actor.2.kills"] == "1"
                and float(s["late"]["timeScale"]) < 0.1
            ),
        )
        self.send("host", "pause", "false")
        self.wait(
            "unpause synchronizes",
            lambda s: all(float(item["timeScale"]) == 1 for item in s.values()),
        )
        self.restart("restart replaces the round on all clients")
        self.send("host", "win")
        self.wait(
            "round victory and human score synchronize",
            lambda s: all(
                item["isWinning"] == "True" and item["humanScore"] == "1" for item in s.values()
            ),
        )
        self.restart("restart clears the victory state")
        self.send("host", "lose")
        self.wait(
            "round defeat and titan score synchronize",
            lambda s: all(
                item["isLosing"] == "True" and item["titanScore"] == "1" for item in s.values()
            ),
        )
        self.restart("restart clears the defeat state")
        self.send("host", "transfer", "2")
        self.wait(
            "explicit host transfer restarts with new host-owned titans",
            lambda s: all(
                item["master"] == "2"
                and len(ids(item["titans"])) == 10
                and all(view // 1000 == 2 for view in ids(item["titans"]))
                for item in s.values()
            ),
        )
        self.send("guest", "leave")
        self.wait(
            "departed host is removed and remaining host rebuilds the round",
            lambda s: all(
                s[name]["master"] == "1"
                and s[name]["players"] == "2"
                and len(ids(s[name]["titans"])) == 10
                and all(view // 1000 == 1 for view in ids(s[name]["titans"]))
                and "actor.2.dead" not in s[name]
                for name in ("host", "late")
            ),
        )

    def restart(self, description: str) -> None:
        before = {name: int(snapshot(self.root / name)["sceneLoads"]) for name in PLAYERS}
        self.send("host", "restart")
        self.wait(
            description,
            lambda s: all(
                int(s[name]["sceneLoads"]) > before[name]
                and s[name]["mapLoaded"] == "True"
                and s[name]["logicLoaded"] == "True"
                for name in PLAYERS
            ),
        )

    def custom(self) -> None:
        for name in ("late", "host"):
            self.send(name, "leave")
        self.wait(
            "all clients leave cleanly",
            lambda s: all(item["scene"] == "menu" and item["room"] == "" for item in s.values()),
        )
        self.connect()
        self.custom_map()

    def leave_room(self) -> None:
        for name in reversed(PLAYERS):
            self.send(name, "leave")
        self.wait(
            "room closes cleanly",
            lambda s: all(
                item["scene"] == "menu"
                and item["room"] == ""
                and item["state"] in ("PeerCreated", "Disconnected")
                for item in s.values()
            ),
        )
        self.connect()

    def modes(self) -> None:
        levels = (
            ("The Forest III", "The Forest", ""),
            ("Racing - Akina", "track - akina", ""),
            ("Annie", "The Forest", "Female Titan"),
            ("Colossal Titan", "Colossal Titan", "COLOSSAL_TITAN"),
            ("Trost", "Colossal Titan", "TITAN_EREN"),
            ("Cave Fight", "CaveFight", ""),
            ("The City III", "The City I", ""),
        )
        for index, (level, scene, prefab) in enumerate(levels):
            if index:
                self.leave_room()
            self.send("host", "reset-settings")
            if level == "Racing - Akina":
                self.send("host", "setting", "LegacyGameSettingsUI", "RacingStartTime", "1")
            room = f"Probe`{level}`normal`120`day``{uuid4().hex}"
            self.send("host", "create", room)
            self.joined(("host",), 1)
            self.send("guest", "join", room)
            self.joined(("host", "guest"), 2)
            self.send("late", "join", room)
            self.joined(PLAYERS, 3)

            def scene_replicated(
                states: Snapshots, expected_scene: str = scene, expected_prefab: str = prefab
            ) -> bool:
                return all(
                    item["scene"] == expected_scene and expected_prefab in item["networkObjects"]
                    for item in states.values()
                )

            if level != "Annie":
                for name in ("host", "guest"):
                    self.send(name, "spawn", "AHSS" if level == "Cave Fight" else "MIKASA")
                self.wait(
                    f"{level}: player spawns replicate",
                    lambda s: all(len(ids(item["heroes"])) == 2 for item in s.values()),
                )
            if level in ("Colossal Titan", "Trost"):
                # These modes create the mission NPC only after a player is ready
                # at the start of a round, matching the character-selection flow.
                self.restart(f"{level}: start mission with ready players")
            self.wait(f"{level}: scene and special prefabs replicate", scene_replicated)
            if level == "The Forest III":
                self.survival_wave()
            elif level == "Racing - Akina":
                self.wait(
                    "race countdown opens the course",
                    lambda s: all(item["startRacing"] == "True" for item in s.values()),
                )
                self.send("guest", "finish-race")
                self.wait(
                    "guest race finish reaches the host and result board",
                    lambda s: all(
                        "Probe-guest" in item["localRacingResult"] and item["isWinning"] == "True"
                        for item in s.values()
                    ),
                )
            elif level == "Cave Fight":
                state = snapshot(self.root / "guest")
                target = next(view for view in ids(state["heroes"]) if view // 1000 == 2)
                self.send("host", "kill-player", str(target))

                def victim_removed(states: Snapshots, victim: int = target) -> bool:
                    return all(
                        item["actor.2.dead"] == "True" and victim not in ids(item["heroes"])
                        for item in states.values()
                    )

                self.wait("AHSS player kill replicates death and removes victim", victim_removed)
            self.restart(f"{level}: synchronized restart")

    def survival_wave(self) -> None:
        state = self.wait(
            "first survival wave spawns three titans",
            lambda s: all(
                item["wave"] == "1" and len(ids(item["titans"])) == 3 for item in s.values()
            ),
        )
        self.send("guest", "die")
        self.wait(
            "fallen player awaits the next wave",
            lambda s: all(item["actor.2.dead"] == "True" for item in s.values()),
        )
        for titan in sorted(ids(state["host"]["titans"])):
            self.send("host", "approach-titan", str(titan))
            self.send("host", "hit-titan", str(titan), "10000")

            def titan_dead(states: Snapshots, view_id: int = titan) -> bool:
                return all(view_id in ids(item["deadTitans"]) for item in states.values())

            self.wait(f"survival titan {titan} dies on every client", titan_dead)
        self.wait(
            "wave advances and the fallen player respawns on every client",
            lambda s: all(
                item["wave"] == "2"
                and item["actor.2.dead"] == "False"
                and len(ids(item["titans"])) == 4
                for item in s.values()
            ),
        )

    def custom_map(self) -> None:
        # Original fixtures: 121 geometry objects force multiple RC map chunks.
        geometry = [
            f"custom,cuboid,default,1,1,1,0,1,1,1,1,1,{index * 3},0,100,0,0,0,1"
            for index in range(121)
        ]
        level = ";".join(["spawnpoint,playerC,0,5,0", "spawnpoint,titan,20,0,0", *geometry]) + ";"
        self.send("host", "setting", "LegacyGameSettingsUI", "LevelScript", level)
        logic = (
            'OnRoundStart()\n{\nGame.PrintMessage("probe-rc-round");\n}\n'
            'OnTitanDie("titan","player")\n{\nGame.PrintMessage("probe-rc-titan");\n}\n'
        )
        (self.root / "host/logic.txt").write_text(logic)
        self.send("host", "setting-file", "LegacyGameSettingsUI", "LogicScript", "logic.txt")
        self.send("host", "setting", "LegacyGameSettingsUI", "Motd", "probe-motd")
        self.send("host", "setting", "LegacyGameSettingsUI", "TitanHealthMode", "1")
        self.send("host", "setting", "LegacyGameSettingsUI", "TitanHealthMin", "5000")
        self.send("host", "setting", "LegacyGameSettingsUI", "TitanHealthMax", "5000")
        self.send("host", "setting", "LegacyGameSettingsUI", "TitanNumberEnabled", "true")
        self.send("host", "setting", "LegacyGameSettingsUI", "TitanNumber", "1")
        room = f"Probe`Custom`normal`120`day``{uuid4().hex}"
        self.send("host", "create", room)
        self.joined(("host",), 1)
        for name in ("guest", "late"):
            self.send(name, "join", room)
            self.joined(("host", name), 2 if name == "guest" else 3)
        self.wait(
            "fragmented RC map chunks create identical geometry and map hashes",
            lambda s: (
                len({item["mapHash"] for item in s.values()}) == 1
                and bool(s["host"]["mapHash"])
                and all(item["customGeometry"] == "121" for item in s.values())
            ),
        )
        self.wait(
            "RC settings synchronize to joining players",
            lambda s: all(
                item["setting.Motd"] == "probe-motd"
                and item["setting.TitanHealthMode"] == "1"
                and item["setting.TitanHealthMin"] == "5000"
                and item["setting.TitanHealthMax"] == "5000"
                for item in s.values()
            ),
        )
        self.restart("custom map reloads from the client cache on restart")
        self.wait(
            "RC round-start script executes and its output reaches every client",
            lambda s: all(
                "probe-rc-round" in item["chat"] and item["customGeometry"] == "121"
                for item in s.values()
            ),
        )
        self.send("host", "spawn")
        state = self.wait(
            "custom map spawns a player and titan",
            lambda s: all(
                len(ids(item["heroes"])) == 1 and len(ids(item["titans"])) == 1
                for item in s.values()
            ),
        )
        titan = str(min(ids(state["host"]["titans"])))
        self.send("host", "approach-titan", titan)
        self.send("host", "hit-titan", titan, "10000")
        self.wait(
            "RC titan-death script and natural round victory reach all clients",
            lambda s: all(
                "probe-rc-titan" in item["chat"]
                and item["isWinning"] == "True"
                and item["actor.1.kills"] == "1"
                for item in s.values()
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Directory produced by prepare.py")
    parser.add_argument("--port", type=int, default=5075)
    parser.add_argument("--scenario", choices=("all", "city", "custom", "modes"), default="all")
    args = parser.parse_args()
    root: Path = args.root.resolve()
    processes: list[subprocess.Popen[bytes]] = []
    run = GameplayRun(root, args.port)
    try:
        for name in PLAYERS:
            state = root / name / "snapshot.json"
            state.unlink(missing_ok=True)
            process = subprocess.Popen(
                [
                    str(root / f"{name}.app/Contents/MacOS/AottgRC"),
                    "-screen-fullscreen",
                    "0",
                    "-screen-width",
                    "800",
                    "-screen-height",
                    "600",
                    "-logFile",
                    str(root / f"{name}.log"),
                ],
                env={**os.environ, "AOTTG_PROBE_DIRECTORY": str(root / name)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
        run.wait(
            "Unity probes are ready", lambda s: all(item["ready"] == "true" for item in s.values())
        )
        for name in PLAYERS:
            run.send(name, "setting", "ProfileSettings", "Name", f"Probe-{name}")
        run.connect()
        if args.scenario in ("all", "city"):
            run.city()
        if args.scenario == "custom":
            # custom() begins by leaving the preceding city; a fresh run is already in a lobby.
            run.custom_map()
        elif args.scenario == "all":
            run.custom()
        if args.scenario == "all":
            run.leave_room()
        if args.scenario in ("all", "modes"):
            run.modes()
        run.verify_logs()
        print(
            f"PASS: {len(run.observations)} gameplay checks; report: {root / 'report.json'}",
            flush=True,
        )
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
