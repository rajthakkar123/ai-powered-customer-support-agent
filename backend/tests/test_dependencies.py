"""Every third-party import in the application must be declared in requirements.txt."""

import ast
import importlib.metadata
import re
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOT = BACKEND_ROOT / "support_agent"
REQUIREMENTS = BACKEND_ROOT / "requirements.txt"


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_distributions() -> set[str]:
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if requirement and not requirement.startswith("-"):
            names.add(normalize(re.split(r"[\s\[<>=!~;@]", requirement, maxsplit=1)[0]))
    return names


def third_party_imports() -> dict[str, set[str]]:
    """Map each top-level non-stdlib module to the application files that import it."""
    imports: dict[str, set[str]] = {}
    for path in APPLICATION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                top_level = module.split(".", 1)[0]
                if top_level not in sys.stdlib_module_names and top_level != "support_agent":
                    imports.setdefault(top_level, set()).add(str(path.relative_to(BACKEND_ROOT)))
    return imports


class DependencyDeclarationTests(unittest.TestCase):
    def test_third_party_imports_are_installed_and_declared(self) -> None:
        declared = declared_distributions()
        providers = importlib.metadata.packages_distributions()
        for module, files in sorted(third_party_imports().items()):
            with self.subTest(module=module):
                distributions = {normalize(name) for name in providers.get(module, [])}
                self.assertTrue(
                    distributions,
                    f"'{module}' is imported by {sorted(files)} but is not installed.",
                )
                self.assertTrue(
                    distributions & declared,
                    f"'{module}' (from {sorted(distributions)}) is imported by {sorted(files)} "
                    "but missing from requirements.txt.",
                )


if __name__ == "__main__":
    unittest.main()
