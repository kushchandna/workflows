"""Install and test each workspace package in its own clean environment.

The workspace shares one venv, which can hide undeclared dependencies. This
builds every package's wheel, installs each one alone (siblings come only
from the built wheels, and only if declared), and runs its tests against
the installed wheel. Siblings are pinned to their wheels with uv overrides,
so a same-named package on PyPI can never be picked instead.
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


def write_overrides(dist: Path) -> Path:
    """Pin every workspace package to its built wheel.

    Overrides only apply to packages something requires, so an undeclared
    sibling is still missing from the environment.
    """
    lines = [
        f"{wheel.name.split('-')[0].replace('_', '-')} @ {wheel.as_uri()}"
        for wheel in sorted(dist.glob("*.whl"))
    ]
    path = dist / "overrides.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def check(
    package: Path, dist: Path, overrides: Path, venvs: Path
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
            "--override",
            overrides,
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
        overrides = write_overrides(dist)
        for package in packages:
            name = tomllib.loads((package / "pyproject.toml").read_text())["project"][
                "name"
            ]
            results = check(package, dist, overrides, Path(tmp) / "venvs")
            if results[-1].returncode == 0:
                print(f"{name}: ok")
            else:
                failed = True
                print(f"{name}: FAILED")
                print(results[-1].stdout + results[-1].stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
