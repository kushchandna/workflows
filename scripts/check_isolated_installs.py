"""Install and test each workspace package in its own clean environment.

The workspace shares one venv, which can hide undeclared dependencies. This
builds every package's wheel, installs each one alone (siblings come only
from the built wheels, and only if declared), and runs its tests against
the installed wheel.
"""

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = "3.11"


def run(cmd: list[str | Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, cwd=ROOT, capture_output=True, text=True)


def check(
    package: Path, dist: Path, venvs: Path
) -> list[subprocess.CompletedProcess[str]]:
    name = tomllib.loads((package / "pyproject.toml").read_text())["project"]["name"]
    venv = venvs / name
    python = venv / "bin" / "python"
    wheel = next(dist.glob(name.replace("-", "_") + "-*.whl"))
    steps = [
        ["uv", "venv", "--quiet", venv, "--python", PYTHON],
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            python,
            "--find-links",
            dist,
            wheel,
            "pytest",
            "pytest-asyncio",
        ],
        [
            python,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            package.relative_to(ROOT) / "tests",
        ],
    ]
    results = []
    for step in steps:
        results.append(run(step))
        if results[-1].returncode != 0:
            break
    return results


def main() -> int:
    packages = sorted(p.parent for p in ROOT.glob("packages/*/*/pyproject.toml"))
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "dist"
        build = run(["uv", "build", "--quiet", "--all-packages", "--out-dir", dist])
        if build.returncode != 0:
            print(build.stdout + build.stderr)
            print("build: FAILED")
            return 1
        for package in packages:
            name = tomllib.loads((package / "pyproject.toml").read_text())["project"][
                "name"
            ]
            results = check(package, dist, Path(tmp) / "venvs")
            if results[-1].returncode == 0:
                print(f"{name}: ok")
            else:
                failed = True
                print(f"{name}: FAILED")
                print(results[-1].stdout + results[-1].stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
