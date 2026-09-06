"""Static import isolation for collected tests and reusable support."""

from __future__ import annotations

import ast
from pathlib import Path


def _collected_imports(source: str, path: Path, collected: set[str]) -> tuple[tuple[int, str], ...]:
    parts = list(path.with_suffix("").parts)
    package = parts[:-1]
    diagnostics: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package[: len(package) - node.level + 1]
                base = ".".join([*prefix, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            modules = [base, *[f"{base}.{alias.name}" for alias in node.names]]
        for module in modules:
            if any(module == name or module.startswith(name + ".") for name in collected):
                diagnostics.add((node.lineno, module))
    return tuple(sorted(diagnostics))


def test_collected_test_import_isolation(subtests):
    cases = (
        (
            "A01",
            "tests/test_y.py",
            "def f():\n    from tests.test_x import helper\n",
            ((2, "tests.test_x"), (2, "tests.test_x.helper")),
        ),
        ("A02", "tests/test_y.py", "import tests.test_x as other\n", ((1, "tests.test_x"),)),
        ("A03", "tests/test_y.py", "from tests import test_x as other\n", ((1, "tests.test_x"),)),
        (
            "A04",
            "tests/test_y.py",
            "from .test_x import helper\n",
            ((1, "tests.test_x"), (1, "tests.test_x.helper")),
        ),
        ("A05", "tests/test_y.py", "from . import test_x\n", ((1, "tests.test_x"),)),
        ("A06", "tests/support/helpers.py", "from .. import test_x\n", ((1, "tests.test_x"),)),
        ("A07", "tests/test_y.py", "from tests.support import helper\n", ()),
        ("A08", "tests/test_y.py", "import tests.test_x_extra\n", ()),
        (
            "A09",
            "tests/support/__init__.py",
            "def f():\n    from .. import test_x\n",
            ((2, "tests.test_x"),),
        ),
    )
    for case, path, source, expected in cases:
        with subtests.test(case=case):
            assert _collected_imports(source, Path(path), {"tests.test_x"}) == expected
    with subtests.test(case="A10"):
        root = Path(__file__).resolve().parents[1]
        paths = sorted((root / "tests").rglob("*.py"))
        collected = {
            ".".join(path.relative_to(root).with_suffix("").parts)
            for path in paths
            if path.name.startswith("test_")
        }
        diagnostics = [
            (path.relative_to(root).as_posix(), line, module)
            for path in paths
            for line, module in _collected_imports(
                path.read_text(), path.relative_to(root), collected
            )
        ]
        assert diagnostics == []
