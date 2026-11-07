"""Test against actual teammate branches without merging or altering their files.

Run from the repository root: python3 -m part4.check_team
Requires git fetch origin first. Only committed Python files are exported.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="part4-team-") as folder:
        base = Path(folder)
        exports = {
            "origin/vikas": ("peer_download/",),
            "origin/romita-tracker": ("tracker/tracker.py",),
            "origin/lohita": ("tracker/tracker_queries.py", "tracker/tracker_format.py"),
        }
        for branch, prefixes in exports.items():
            commit = subprocess.check_output(["git", "rev-parse", branch], cwd=root, text=True).strip()
            print(f"Testing {branch}: {commit}", flush=True)
            files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", branch],
                                            cwd=root, text=True).splitlines()
            chosen = [f for f in files if f.endswith(".py") and any(f.startswith(p) for p in prefixes)]
            if not chosen:
                raise RuntimeError("Missing teammate files on " + branch)
            for name in chosen:
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(subprocess.check_output(["git", "show", f"{branch}:{name}"], cwd=root))
        env = os.environ.copy()
        env["PART4_TEAM_CODE"] = str(base)
        return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "part4/tests",
                                "-p", "test_team.py", "-v"], cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
