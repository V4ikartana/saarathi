"""Build the self-contained engine binary the desktop app ships as a Tauri sidecar.

    uv run --group release python scripts/build_engine.py

Produces desktop/src-tauri/binaries/saarathi-<target-triple>[.exe] (the name Tauri's externalBin expects) and checks
that the binary runs. No Python is needed on the machine that runs the result.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop" / "src-tauri" / "binaries"


def target_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    return next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("host: "))


def main() -> int:
    work = ROOT / "build" / "engine"
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--name",
            "saarathi",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "build"),
            "--specpath",
            str(work),
            "--paths",
            str(ROOT / "src"),
            "--collect-submodules",
            "saarathi",
            "--copy-metadata",
            "saarathi",
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            str(ROOT / "packaging" / "engine_entry.py"),
        ],
        check=True,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    built = work / "dist" / f"saarathi{suffix}"
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"saarathi-{target_triple()}{suffix}"
    shutil.copy2(built, target)
    version = subprocess.run([str(target), "version"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"built {target} ({target.stat().st_size // 1_000_000} MB), reports version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
