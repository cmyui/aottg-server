"""Build the opt-in probe and prepare disposable macOS game copies."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path, help="Installed AottgRC.app (read-only source)")
    parser.add_argument("output", type=Path, help="New directory for disposable game copies")
    args = parser.parse_args()
    output: Path = args.output.resolve()
    if output.is_relative_to(args.game.resolve()):
        parser.error("Output must be outside the installed app")
    if output.exists():
        parser.error("Output must not exist; choose a fresh test directory")
    managed = args.game.resolve() / "Contents/Data/Managed"
    if not (managed / "Assembly-CSharp.dll").is_file():
        parser.error("Game does not contain the expected Unity assemblies")
    tool = Path(__file__).resolve().parent
    sdks = subprocess.check_output(["dotnet", "--list-sdks"], text=True)
    matches = re.findall(r"^(\S+) \[(.+)\]$", sdks, re.MULTILINE)
    if not matches:
        parser.error("Install the .NET SDK first")
    version, root = matches[-1]
    compiler = Path(root) / version / "Roslyn/bincore/csc.dll"
    output.mkdir(parents=True)
    manifest = {
        "assemblies": {
            name: hashlib.sha256((managed / name).read_bytes()).hexdigest()
            for name in ("Assembly-CSharp.dll", "Photon3Unity3D.dll", "UnityEngine.dll")
        },
        "probe_source_sha256": hashlib.sha256((tool / "GameplayProbe.cs").read_bytes()).hexdigest(),
        "dotnet_sdk": version,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    probe = output / "GameplayProbe.dll"
    references = [
        "mscorlib",
        "System",
        "System.Core",
        "UnityEngine",
        "Assembly-CSharp",
        "Photon3Unity3D",
    ]
    subprocess.run(
        [
            "dotnet",
            str(compiler),
            "/nologo",
            "/noconfig",
            "/nostdlib+",
            "/target:library",
            "/langversion:7.3",
            f"/out:{probe}",
            *[f"/r:{managed / (name + '.dll')}" for name in references],
            str(tool / "GameplayProbe.cs"),
        ],
        check=True,
    )
    subprocess.run(["dotnet", "build", str(tool / "Patcher")], check=True)
    for name in ("host", "guest", "late"):
        destination = output / f"{name}.app"
        shutil.copytree(args.game, destination, symlinks=True)
        copied_managed = destination / "Contents/Data/Managed"
        subprocess.run(
            [
                "dotnet",
                "run",
                "--no-build",
                "--project",
                str(tool / "Patcher"),
                "--",
                str(managed / "Assembly-CSharp.dll"),
                str(copied_managed / "Assembly-CSharp.dll"),
                str(probe),
            ],
            check=True,
        )
        shutil.copy2(probe, copied_managed / probe.name)
        (output / name).mkdir()
    print(f"Prepared host, guest, and late-join copies in {output}")


if __name__ == "__main__":
    main()
