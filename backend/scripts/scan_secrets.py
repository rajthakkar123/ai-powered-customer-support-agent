"""Fail the quality gate if committed files contain credentials.

Run from backend/: python -m scripts.scan_secrets
Real ``.env`` files are never opened (only ``.env.example`` is scanned), and findings print
the file, line, and rule only, never the matched value.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {
    ".git",
    ".venv",
    ".devtools",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "htmlcov",
    "data",
}
SKIPPED_SUFFIXES = {".sqlite3", ".pyc", ".exe", ".png", ".jpg", ".ico", ".whl", ".zip"}
RULES = {
    "openai_api_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    # Assignments of long literal secrets in application code; tests use obvious fakes.
    "hardcoded_secret": re.compile(
        r"(?i)\b(?:api_?key|secret|password|passwd|token)\b\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']"
    ),
}
# Test fixtures intentionally contain fake credentials such as "agent-one-secret".
GENERIC_RULE_EXEMPT_DIRS = {"tests"}


def is_env_file(path: Path) -> bool:
    return path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example")


def candidate_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRS or part.startswith("tmp") for part in relative.parts[:-1]):
            continue
        if not path.is_file() or is_env_file(path) or path.suffix.lower() in SKIPPED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files)


def scan(root: Path = PROJECT_ROOT) -> list[str]:
    findings = []
    for path in candidate_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(root)
        generic_allowed = not GENERIC_RULE_EXEMPT_DIRS.intersection(relative.parts)
        for number, line in enumerate(lines, start=1):
            if "secret-scan: allow" in line:
                continue
            for rule, pattern in RULES.items():
                if rule == "hardcoded_secret" and not generic_allowed:
                    continue
                if pattern.search(line):
                    findings.append(f"{relative.as_posix()}:{number}: {rule}")
    return findings


def main() -> int:
    findings = scan()
    for finding in findings:
        print(f"POSSIBLE SECRET {finding}")
    print(f"Secret scan: {len(findings)} finding(s) in {PROJECT_ROOT.name}/ (.env files skipped)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
