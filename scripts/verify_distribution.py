"""CI/user smoke check: install the built wheel and exercise it outside the repo."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist").glob("dutyloop-*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise SystemExit("Build a wheel with python -m build first")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    # Only fixture commits in the temporary demo use this identity.
    environment.update(GIT_AUTHOR_NAME="Fixture Maintainer", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Fixture Maintainer", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    with tempfile.TemporaryDirectory(prefix="dutyloop-wheel-") as temporary:
        directory = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(directory / "venv")
        executable = directory / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(executable), "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[-1])],
                       cwd=directory, env=environment, check=True, timeout=60)
        version = subprocess.check_output([str(executable), "-m", "dutyloop", "--version"],
                                          cwd=directory, env=environment, text=True, timeout=10)
        issue_help = subprocess.check_output([str(executable), "-m", "dutyloop", "issues", "run", "--help"],
                                             cwd=directory, env=environment, text=True, timeout=10)
        if "--plan-only" not in issue_help:
            raise RuntimeError("Installed wheel is missing the issue workflow CLI")
        # Import the actual issue controller outside source without contacting GitHub.
        subprocess.run([str(executable), "-c", "from dutyloop import issues; assert callable(issues.run)"],
                       cwd=directory, env=environment, check=True, timeout=10)
        demo = subprocess.run([str(executable), "-m", "dutyloop", "demo", "--directory", str(directory / "demo")],
                              cwd=directory, env=environment, text=True, capture_output=True, timeout=180)
        if demo.returncode:
            raise RuntimeError(f"Installed demo failed: {demo.stderr}")
        outcome = json.loads(demo.stdout)
        if not (outcome["simulated"] and outcome["accepted_tasks"] == 2 and outcome["original_unchanged"]
                and outcome["accepted_tests_passed"] and outcome["snapshot"]["status"] == "limited"):
            raise RuntimeError("Installed wheel did not complete the expected bounded demonstration")
        print(json.dumps({"version": version.strip(), "wheel": wheels[-1].name, "simulated": True,
                          "accepted_tasks": outcome["accepted_tasks"], "original_unchanged": True,
                          "verified": True, "issue_cli": True, "model_calls": 0}))


if __name__ == "__main__":
    main()
