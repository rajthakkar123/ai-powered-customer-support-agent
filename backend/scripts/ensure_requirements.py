"""Install any backend requirements missing from the active Python environment.

Run from backend/: python -m scripts.ensure_requirements [requirements file]
pip decides what is satisfied, so version pins and transitive dependencies are honoured.
"""

import json
import subprocess
import sys
from pathlib import Path

DEFAULT_REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
PIP = [sys.executable, "-m", "pip", "--disable-pip-version-check"]


def pending_requirements(requirements: Path) -> list[str]:
    """Return name==version for each distribution pip would install; empty when satisfied."""
    completed = subprocess.run(
        [*PIP, "install", "--dry-run", "--quiet", "--report", "-", "-r", str(requirements)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "pip could not resolve the requirements.")
    if not completed.stdout.strip():
        return []
    report = json.loads(completed.stdout)
    return [
        f"{item['metadata']['name']}=={item['metadata']['version']}"
        for item in report.get("install", [])
    ]


def main() -> int:
    requirements = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REQUIREMENTS
    print(f"Checking backend requirements in {requirements.name}...", flush=True)
    try:
        missing = pending_requirements(requirements)
    except RuntimeError as error:
        print(f"Requirement check failed: {error}", file=sys.stderr)
        return 1
    if not missing:
        print("All backend requirements are installed.")
        return 0
    # Flush so this line precedes pip's own output when the launcher pipes the console.
    print(f"Installing missing backend requirements: {', '.join(missing)}", flush=True)
    return subprocess.run([*PIP, "install", "-r", str(requirements)], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
