"""Complete the original boss objectives without synthesizing victory events."""

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from control import snapshot

if TYPE_CHECKING:
    from run import GameplayRun, Snapshots


def open_room(
    run: "GameplayRun", level: str, characters: tuple[str, str] = ("MIKASA", "MIKASA")
) -> None:
    room = f"Probe`{level}`normal`120`day``{uuid4().hex}"
    run.send("host", "create", room)
    run.joined(("host",), 1)
    for name, count in (("guest", 2), ("late", 3)):
        run.send(name, "join", room)
        run.joined(("host", name), count)
    for name, character in zip(("host", "guest"), characters, strict=True):
        run.send(name, "spawn", character)
    run.wait(
        f"{level}: two players are ready",
        lambda s: all(
            item.get("actor.2.dead") == "False"
            and item.get("actor.1.character") == characters[0]
            and item.get("actor.2.character") == characters[1]
            for item in s.values()
        ),
    )


def defeat_titans(run: "GameplayRun") -> None:
    state = snapshot(run.root / "host")
    for titan in state["titans"].strip(",").split(","):
        if titan:
            run.send("host", "approach-titan", titan)
            run.send("host", "hit-titan", titan, "100000")


def bosses(run: "GameplayRun", mission: str | None = None) -> None:
    for index, (level, component) in enumerate(
        (("Annie", "FEMALE_TITAN"), ("Colossal Titan", "COLOSSAL_TITAN"), ("Trost", "TITAN_EREN"))
    ):
        if mission is not None and level != mission:
            continue
        if index and mission is None:
            run.leave_room()
        run.send("host", "reset-settings")
        open_room(run, level)
        if level != "Annie":
            run.restart(f"{level}: mission starts with ready players")

        def boss_exists(states: "Snapshots", kind: str = component) -> bool:
            return all(kind + ".view" in item for item in states.values())

        state = run.wait(f"{level}: boss exists on every client", boss_exists)
        if level == "Colossal Titan":
            run.wait(
                "Colossal Titan: opening emergence completes",
                lambda s: s["host"].get("COLOSSAL_TITAN.state") not in (None, "wait", "null"),
            )
        view = state["host"][component + ".view"]
        if level == "Trost":
            # Let every original escort phase and waypoint run; kill incoming titans
            # using a real player's damage RPC rather than changing NPC mission state.
            deadline = time.monotonic() + 360
            phases: set[str] = set()
            route_counts: set[str] = set()
            while time.monotonic() < deadline:
                current = snapshot(run.root / "host")
                phase = current.get("TITAN_EREN.rockPhase", "")
                route_counts.add(current.get("TITAN_EREN.routeRemaining", ""))
                if phase not in phases:
                    phases.add(phase)
                    print(f"Trost escort reached phase {phase}", flush=True)
                if current["isLosing"] == "True":
                    raise RuntimeError("Escort was killed; inspect the Unity report")
                if current["isWinning"] == "True":
                    break
                defeat_titans(run)
                time.sleep(0.2)

            print(
                f"Trost observed phases={sorted(phases)}, route counts={sorted(route_counts)}",
                flush=True,
            )

            def escort_completed(
                states: "Snapshots", observed: set[str] = phases, route: set[str] = route_counts
            ) -> bool:
                return (
                    all(item["isWinning"] == "True" for item in states.values())
                    and {"0", "1", "2", "3", "4", "5", "7", "8"} <= observed
                    # The original route selects its first waypoint twice, removing
                    # entries 8 and 7 in consecutive physics ticks at that waypoint.
                    # Count 7 can disappear between snapshots; later legs persist.
                    and {"8", "6", "5", "4", "3", "2", "1"} <= route
                    and states["host"].get("TITAN_EREN.rockHitGround") == "True"
                    and all(
                        all(
                            "rock:" + action in item["animationsSeen"]
                            for action in ("lift", "move", "set")
                        )
                        for item in states.values()
                    )
                )

            run.wait(
                "Trost: escort carries the rock through the route and seals the gate",
                escort_completed,
            )
            run.restart("Trost: restart after objective completion")
            run.wait("Trost: new escort spawns", lambda s: "TITAN_EREN.view" in s["host"])
            run.send("host", "trost-hit")
            run.wait(
                "Trost: escort death triggers shared mission defeat",
                lambda s: all(item["isLosing"] == "True" for item in s.values()),
            )
        else:
            run.send("host", "approach-titan", view)
            run.send("host", "hit-titan", view, "100000")

            def boss_dead(states: "Snapshots", kind: str = component) -> bool:
                return all(
                    item.get(kind + ".hasDie") == "True" and item["actor.1.kills"] == "1"
                    for item in states.values()
                )

            run.wait(f"{level}: lethal damage replicates boss death and killer score", boss_dead)
            if level == "Colossal Titan":
                run.send("host", "move", "0", "5", "600")
            if level == "Annie":
                run.wait(
                    "Annie: death summons the original fifteen reinforcements",
                    lambda s: all(
                        len(item["titans"].strip(",").split(",")) == 15 for item in s.values()
                    ),
                )
                run.wait(
                    "Annie: corpse and buffered boss view are removed",
                    lambda s: all("FEMALE_TITAN.view" not in item for item in s.values()),
                )
                defeat_titans(run)
            run.wait(
                f"{level}: full mission completion and human score reach every client",
                lambda s: all(
                    item["isWinning"] == "True" and item["humanScore"] == "1" for item in s.values()
                ),
            )
            run.restart(f"{level}: completed mission restarts cleanly")


def capture(run: "GameplayRun") -> None:
    run.send("host", "reset-settings")
    open_room(run, "The City III")
    state = run.wait(
        "Capture: checkpoints load on every client",
        lambda s: all(any(key.startswith("checkpoint.") for key in item) for item in s.values()),
    )
    checkpoints = sorted(
        int(key.split(".")[1]) for key in state["host"] if key.startswith("checkpoint.")
    )
    for point in checkpoints:
        if snapshot(run.root / "host")[f"checkpoint.{point}"] == "Human":
            continue
        defeat_titans(run)
        run.send("guest", "capture-point", str(point))
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            defeat_titans(run)
            current = snapshot(run.root / "host")
            if current[f"checkpoint.{point}"] == "Human":
                break
            time.sleep(0.2)

        def captured(states: "Snapshots", checkpoint: int = point) -> bool:
            return all(item.get(f"checkpoint.{checkpoint}") == "Human" for item in states.values())

        run.wait(f"Capture: checkpoint {point} changes ownership on every client", captured)
    run.wait(
        "Capture: controlling every checkpoint produces shared victory",
        lambda s: all(
            item["isWinning"] == "True"
            and all(
                value == "Human" for key, value in item.items() if key.startswith("checkpoint.")
            )
            for item in s.values()
        ),
    )
    run.restart("Capture: completed objective restarts cleanly")
