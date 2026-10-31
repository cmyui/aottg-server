"""Send a command to a running, explicitly enabled local Unity probe."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4


def snapshot(directory: Path) -> dict[str, str]:
    value: object = json.loads((directory / "snapshot.json").read_text())
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("Invalid probe snapshot")
    return value


def command(directory: Path, name: str, *arguments: str) -> None:
    request_id = uuid4().hex
    if any("\n" in argument or "\r" in argument for argument in (name, *arguments)):
        raise ValueError("Command arguments must occupy one line")
    temporary = directory / "command.tmp"
    temporary.write_text("\n".join((request_id, name, *arguments)))
    temporary.replace(directory / "command.txt")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = directory / "result.txt"
        if response.exists():
            lines = response.read_text().splitlines()
            if lines and lines[0] == request_id:
                if len(lines) < 2 or lines[1] != "OK":
                    raise RuntimeError("\n".join(lines[1:]))
                try:
                    if snapshot(directory).get("lastCommand") == request_id:
                        return
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
        time.sleep(0.1)
    raise TimeoutError(f"Unity did not execute {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("command", nargs="?", default="snapshot")
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    if args.command == "snapshot":
        print(json.dumps(snapshot(args.directory), indent=2))
    else:
        command(args.directory, args.command, *args.arguments)
        print(f"Executed {args.command}")


if __name__ == "__main__":
    main()
