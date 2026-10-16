import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_architecture.py"
check_backend = cast(Callable[[Path], list[str]], runpy.run_path(str(SCRIPT))["check_backend"])


def write_package(root: Path, sources: dict[str, str]) -> Path:
    package = root / "geoplan"
    for name, source in sources.items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return package


def test_accepts_directed_backend_dependencies(tmp_path: Path) -> None:
    package = write_package(
        tmp_path,
        {
            "__init__.py": "",
            "domain/__init__.py": "",
            "domain/geo.py": "class Coordinate: pass",
            "navigation/routing.py": "from geoplan.domain.geo import Coordinate",
        },
    )
    assert check_backend(package) == []


def test_rejects_domain_dependency_through_package_import(tmp_path: Path) -> None:
    package = write_package(
        tmp_path,
        {
            "__init__.py": "",
            "domain/__init__.py": "",
            "domain/geo.py": "from ..api import routes",
            "api/__init__.py": "",
            "api/routes.py": "",
        },
    )
    assert any("Domain depends on implementation" in error for error in check_backend(package))


def test_detects_relative_backend_cycle(tmp_path: Path) -> None:
    package = write_package(
        tmp_path,
        {
            "__init__.py": "",
            "a.py": "from . import b",
            "b.py": "from . import a",
        },
    )
    assert any("Import cycle" in error for error in check_backend(package))


def test_reports_missing_backend_module(tmp_path: Path) -> None:
    package = write_package(tmp_path, {"a.py": "from geoplan.removed import Planner"})
    assert any("Unresolved local import" in error for error in check_backend(package))
