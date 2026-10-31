"""Exercise character abilities and weapon input in the original Unity simulation."""

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from control import snapshot
from missions import open_room

if TYPE_CHECKING:
    from run import GameplayRun, Snapshots


def hero_view(state: dict[str, str], actor: int = 2) -> str:
    return next(view for view in state["heroes"].split(",") if view and int(view) // 1000 == actor)


def arena(run: "GameplayRun", extra: str = "", guest: str = "MIKASA") -> None:
    run.send("host", "reset-settings")
    level = (
        "spawnpoint,playerC,0,5,0;spawnpoint,titan,300,0,300;"
        "custom,cuboid,default,200,1,200,0,1,1,1,1,1,0,-5,0,0,0,0,1;" + extra
    )
    run.send("host", "setting", "LegacyGameSettingsUI", "LevelScript", level)
    run.send("host", "setting", "LegacyGameSettingsUI", "TitanNumberEnabled", "true")
    run.send("host", "setting", "LegacyGameSettingsUI", "TitanNumber", "0")
    open_room(run, "Custom (No PT)" if guest == "EREN" else "Custom", ("MIKASA", guest))
    run.send("host", "move", "-200", "5", "-200")


def clear(run: "GameplayRun") -> None:
    for name in ("host", "guest", "late"):
        run.send(name, "clear-observations")


def ready(run: "GameplayRun", view: str) -> None:
    run.send("guest", "ground")
    run.wait(
        "player is grounded and ready for input",
        lambda s: (
            s["guest"].get(f"view.{view}.grounded") == "True"
            and s["guest"].get(f"view.{view}.state") == "Idle"
        ),
    )


def key(run: "GameplayRun", action: str, group: str = "Human", frames: int = 6) -> None:
    run.send("guest", "key", group, action, str(frames))


def animation(run: "GameplayRun", view: str, name: str, description: str) -> None:
    run.wait(
        description,
        lambda s: all(f"{view}:{name}" in item["animationsSeen"] for item in s.values()),
    )


def change_character(run: "GameplayRun", character: str, skill: str | None = None) -> str:
    run.send("guest", "die")
    run.wait(
        "character leaves the world",
        lambda s: all(item["actor.2.dead"] == "True" for item in s.values()),
    )
    run.send("guest", "spawn", character)
    state = run.wait(
        f"{character}: player and costume load on every client",
        lambda s: (
            all(item["actor.2.dead"] == "False" for item in s.values())
            and any(
                k.endswith(".skillId") and v == (skill or character.lower())
                for k, v in s["guest"].items()
            )
        ),
    )
    return hero_view(state["guest"])


def skills(run: "GameplayRun") -> None:
    arena(run)
    for character, expected in (
        ("MIKASA", "attack3_1"),
        ("LEVI", "attack5"),
        ("PETRA", "special_petra"),
        ("ARMIN", "special_armin"),
        ("MARCO", "special_marco_"),
        ("SASHA", "special_sasha"),
    ):
        view = (
            hero_view(snapshot(run.root / "guest"))
            if character == "MIKASA"
            else change_character(run, character)
        )
        ready(run, view)
        target = ""
        if character in ("ARMIN", "MARCO"):
            run.send("host", "skill-target", view)
            state = run.wait(
                "skill target reaches all clients",
                lambda s: all(bool(item["titans"]) for item in s.values()),
            )
            target = state["host"]["titans"].strip(",")
        clear(run)
        run.send("guest", "ready-skill")
        key(run, "AttackSpecial")
        animation(run, view, expected, f"{character}: skill animation reaches all clients")

        def cooldown_started(states: "Snapshots", player: str = view) -> bool:
            return float(states["guest"][f"view.{player}.skillCDDuration"]) > 0

        run.wait(f"{character}: skill consumes its cooldown", cooldown_started)
        if character == "ARMIN":
            animation(run, target, "laugh", "Armin: host-owned titan is stunned on every client")
        elif character == "MARCO":

            def taunted(states: "Snapshots", victim: str = target, player: str = view) -> bool:
                return states["host"].get(f"view.{victim}.tauntedBy") == player

            run.wait("Marco: master applies the guest's titan taunt", taunted)
        if target:
            run.send("host", "remove-target", target)
        if character == "SASHA":
            run.wait(
                "Sasha: speed buff activates",
                lambda s: any(
                    k.endswith(".currentBuff") and v == "SpeedUp" for k, v in s["guest"].items()
                ),
            )
            run.wait(
                "Sasha: speed buff expires",
                lambda s: all(
                    v == "NoBuff" for k, v in s["guest"].items() if k.endswith(".currentBuff")
                ),
            )
    view = change_character(run, "JEAN")
    run.send("host", "spawn-titan")
    state = run.wait(
        "Jean: grabber replicates", lambda s: all(bool(item["titans"]) for item in s.values())
    )
    titan = state["host"]["titans"].strip(",").split(",")[0]
    clear(run)
    run.send("host", "grab-player", titan, view)
    run.wait(
        "Jean: titan grab reaches the victim",
        lambda s: all(item.get(f"view.{view}.state") == "Grab" for item in s.values()),
    )
    key(run, "AttackSpecial")
    animation(run, view, "grabbed_jean", "Jean: escape animation reaches all clients")
    run.wait(
        "Jean: escape releases victim and clears the host's grabbed target",
        lambda s: (
            all(item[f"view.{view}.state"] != "Grab" for item in s.values())
            and s["host"].get(f"view.{titan}.grabbedTarget") == ""
            and s["guest"][f"view.{view}.escapeTimes"] == "0"
        ),
    )
    run.send("host", "remove-target", titan)
    view = change_character(run, "EREN", skill="petra")
    ready(run, view)
    clear(run)
    run.send("guest", "ready-skill")
    key(run, "AttackSpecial")
    animation(
        run, view, "special_petra", "Eren: player-titan maps enforce the original Petra fallback"
    )
    run.leave_room()
    arena(run, guest="EREN")
    view = hero_view(snapshot(run.root / "guest"))
    ready(run, view)
    run.send("guest", "ready-skill")
    key(run, "AttackSpecial")
    state = run.wait(
        "Eren: transformation spawns and links the shifter on every client",
        lambda s: all(
            item.get(f"view.{view}.titanForm") == "True" and "TITAN_EREN.view" in item
            for item in s.values()
        ),
    )
    eren = state["guest"]["TITAN_EREN.view"]
    run.wait(
        "Eren: transformation lands and finishes",
        lambda s: s["guest"].get("TITAN_EREN.animation") == "idle",
    )
    for action, expected in (
        ("AttackDefault", "attack_combo_001"),
        ("AttackSpecial", "attack_kick"),
        ("Roar", "born"),
    ):
        clear(run)
        key(run, action, "Shifter")
        animation(run, eren, expected, f"Eren: {action} replicates")
        run.wait(
            "Eren: action returns to idle",
            lambda s: s["guest"].get("TITAN_EREN.animation") == "idle",
        )
    run.wait(
        "Eren: transformation expires and restores the human on every client",
        lambda s: all(
            item.get(f"view.{view}.titanForm") == "False" and "TITAN_EREN.view" not in item
            for item in s.values()
        ),
    )


@dataclass(frozen=True)
class DamageTarget:
    view: str
    kills_before: int


def damage_target(run: "GameplayRun", cannon: bool = False) -> DamageTarget:
    run.send("host", "spawn-titan")
    run.send("host", "spawn-titan", "400", "0", "400")
    state = run.wait(
        "weapon targets replicate",
        lambda s: all(len(item["titans"].strip(",").split(",")) == 2 for item in s.values()),
    )
    titan = state["host"]["titans"].strip(",").split(",")[0]
    run.send("guest", "weapon-target", titan)
    # Allow the owner's representation of the attacker to catch up before damage.
    if not cannon:

        def attacker_reached(states: "Snapshots") -> bool:
            view = hero_view(states["guest"])
            positions = [
                tuple(float(number) for number in entry.split(":")[1].split(","))
                for name in ("host", "guest")
                for entry in states[name]["heroPositions"].split(";")
                if entry.startswith(view + ":")
            ]
            return len(positions) == 2 and math.dist(*positions) < 2

        run.wait("attacker reaches the target owner", attacker_reached)
    return DamageTarget(titan, int(state["host"]["actor.2.kills"]))


def killed_target(run: "GameplayRun", titan: DamageTarget, weapon: str) -> None:
    # A swing may miss during the transition from grounded to airborne. Retry
    # normal attack input while the target lives, as a player would; never force damage.
    def dead(state: dict[str, str]) -> bool:
        if weapon == "Cannon":
            # Cannon death sets hasDie only on the titan owner. Other clients
            # receive the death animation and eventual object destruction.
            return f"{titan.view}:die_" in state["animationsSeen"]
        return titan.view in state["deadTitans"].split(",")

    deadline = time.monotonic() + 20
    next_attack = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = snapshot(run.root / "guest")
        if dead(state):
            break
        if time.monotonic() >= next_attack:
            key(
                run,
                "CannonFire" if weapon == "Cannon" else "AttackDefault",
                "Interaction" if weapon == "Cannon" else "Human",
            )
            next_attack = time.monotonic() + 2
        time.sleep(0.1)
    run.wait(
        f"{weapon}: original collision handler kills the host-owned titan and credits the guest",
        lambda s: all(
            dead(item) and int(item["actor.2.kills"]) > titan.kills_before for item in s.values()
        ),
    )
    run.send("guest", "weapon-target", "0")


def weapons(run: "GameplayRun") -> None:
    arena(run)
    view = hero_view(snapshot(run.root / "guest"))
    ready(run, view)
    clear(run)
    key(run, "AttackDefault")
    animation(run, view, "attack", "Blades: normal attack animation reaches all clients")
    ready(run, view)
    clear(run)
    spares_before = int(snapshot(run.root / "guest")[f"view.{view}.currentBladeNum"])
    key(run, "Reload")
    animation(run, view, "changeBlade", "Blades: replacement animation replicates")
    run.wait(
        "Blades: replacement consumes a spare pair",
        lambda s: int(s["guest"][f"view.{view}.currentBladeNum"]) == spares_before - 1,
    )

    ready(run, view)
    titan = damage_target(run)
    key(run, "AttackDefault")
    killed_target(run, titan, "Blades")

    run.leave_room()
    arena(run, guest="AHSS")
    view = hero_view(snapshot(run.root / "guest"))
    for action, expected in (
        ("AttackDefault", "AHSS_shoot_l"),
        ("AttackDefault", "AHSS_shoot_r"),
        ("AttackSpecial", "AHSS_shoot_both"),
    ):
        ready(run, view)
        clear(run)
        key(run, action)
        animation(run, view, expected, f"AHSS: {expected} replicates")
        run.wait(
            "AHSS: shot effect is instantiated on every client",
            lambda s: all("shotGun" in item["objectsSeen"] for item in s.values()),
        )
        if expected != "AHSS_shoot_l":
            run.wait(
                "AHSS: both barrels are empty",
                lambda s: all(
                    s["guest"][f"view.{view}.{side}GunHasBullet"] == "False"
                    for side in ("left", "right")
                ),
            )
            ready(run, view)
            key(run, "Reload")
            run.wait(
                "AHSS: reload refills both barrels",
                lambda s: all(
                    s["guest"][f"view.{view}.{side}GunHasBullet"] == "True"
                    for side in ("left", "right")
                ),
            )

    ready(run, view)
    titan = damage_target(run)
    key(run, "AttackDefault")
    killed_target(run, titan, "AHSS")

    for prefab in ("CannonGround", "CannonWall"):
        run.leave_room()
        cannon(run, prefab)

    run.leave_room()
    arena(run)
    run.send("host", "setting", "LegacyGameSettingsUI", "BombModeEnabled", "true")
    run.restart("Thunder spears: bomb mode restarts with ready players")
    state = run.wait(
        "Thunder spears: equipment loads",
        lambda s: any(k.endswith(".skillId") and v == "bomb" for k, v in s["guest"].items()),
    )
    view = hero_view(state["guest"])
    run.wait(
        "Thunder spears: spawn immunity and initial cooldown expire",
        lambda s: (
            s["host"].get(f"view.{hero_view(s['host'], 1)}.bombImmune") == "False"
            and float(s["guest"][f"view.{view}.skillCDDuration"]) <= 0
        ),
    )
    clear(run)
    run.send("guest", "projectile-target", hero_view(snapshot(run.root / "host"), 1))
    key(run, "AttackSpecial")
    run.wait(
        "Thunder spears: projectile and explosion replicate",
        lambda s: all(
            "BombMain" in item["objectsSeen"] and "BombExplodeMain" in item["objectsSeen"]
            for item in s.values()
        ),
    )
    run.wait(
        "Thunder spears: explosion kills the other player and awards score",
        lambda s: all(
            int(item["actor.1.deaths"]) >= 1 and int(item["actor.2.kills"]) >= 1
            for item in s.values()
        ),
    )
    run.wait(
        "Thunder spears: expired projectile is removed everywhere",
        lambda s: all("BombMain" not in item["networkObjects"] for item in s.values()),
    )


def cannon(run: "GameplayRun", prefab: str) -> None:
    arena(run, f"photon,{prefab},0,0,15,0,0,0,1;")
    view = hero_view(snapshot(run.root / "guest"))
    state = run.wait(
        f"{prefab}: map prop replicates",
        lambda s: all((prefab + "Prop") in item["networkObjects"] for item in s.values()),
    )
    prop = next(
        entry.split(":")[0]
        for entry in state["host"]["networkObjects"].split("|")
        if (prefab + "Prop") in entry
    )
    run.send("guest", "use-cannon", prop)
    run.wait(
        f"{prefab}: master authorizes guest ownership and links rider",
        lambda s: all(item.get(f"view.{view}.isCannon") == "True" for item in s.values()),
    )
    clear(run)
    run.send("guest", "ready-skill")
    key(run, "CannonFire", "Interaction")
    run.wait(
        f"{prefab}: projectile and muzzle effect replicate",
        lambda s: all(
            "CannonBallObject" in item["objectsSeen"] and "boom2" in item["objectsSeen"]
            for item in s.values()
        ),
    )
    run.wait(
        f"{prefab}: flight projectile expires on every client",
        lambda s: all("CannonBallObject" not in item["networkObjects"] for item in s.values()),
    )
    titan = damage_target(run, cannon=True)
    run.send("guest", "ready-skill")
    key(run, "CannonFire", "Interaction")
    killed_target(run, titan, "Cannon")
    key(run, "Interact", "Interaction")
    run.wait(
        f"{prefab}: dismount returns the rider and restores the map prop",
        lambda s: all(
            item.get(f"view.{view}.isCannon") == "False"
            and (prefab + "Prop") in item["networkObjects"]
            and "CannonBallObject" not in item["networkObjects"]
            for item in s.values()
        ),
    )
