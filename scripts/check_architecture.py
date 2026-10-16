"""Check backend package direction and import cycles without importing application code."""

import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "src" / "geoplan"


def check_backend(package: Path = PACKAGE) -> list[str]:
    errors: list[str] = []
    if not package.is_dir():
        return [f"Missing application package: {package}"]
    modules: dict[str, tuple[Path, bool]] = {}
    for path in package.rglob("*.py"):
        parts = path.relative_to(package).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        modules[".".join(("geoplan", *parts))] = (path, is_package)

    graph: dict[str, set[str]] = {}
    for module, (path, is_package) in modules.items():
        imports: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                dependency = node.module or ""
                if node.level:
                    context = module if is_package else module.rpartition(".")[0]
                    try:
                        dependency = resolve_name("." * node.level + dependency, context)
                    except ImportError:
                        errors.append(f"Invalid relative import in {module}: {dependency}")
                        continue
                if dependency:
                    imports.add(dependency)
                    imports.update(
                        f"{dependency}.{alias.name}"
                        for alias in node.names
                        if f"{dependency}.{alias.name}" in modules
                    )
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
        graph[module] = imports
        for dependency in imports:
            if dependency.split(".")[0] in {"src", "benchmarks", "scenarios", "tests"}:
                errors.append(f"{module} imports development code: {dependency}")
            if dependency.startswith("geoplan.") and dependency not in modules:
                errors.append(f"Unresolved local import: {module} -> {dependency}")
            if module.startswith("geoplan.domain.") and dependency.startswith("geoplan."):
                if not dependency.startswith("geoplan.domain."):
                    errors.append(f"Domain depends on implementation: {module} -> {dependency}")
            if module.startswith("geoplan.domain.") and dependency.split(".")[0] in {
                "fastapi",
                "starlette",
                "uvicorn",
            }:
                errors.append(f"Domain imports transport: {module} -> {dependency}")
            if dependency.startswith("geoplan.api.") and not module.startswith(
                ("geoplan.api.", "geoplan.bootstrap")
            ):
                errors.append(f"Implementation imports HTTP: {module} -> {dependency}")
            if dependency == "geoplan.planning.engine" and not module.startswith(
                ("geoplan.api.", "geoplan.planning.", "geoplan.composition")
            ):
                errors.append(f"Algorithm depends on orchestration: {module} -> {dependency}")

    visited: set[str] = set()
    active: list[str] = []

    def visit(module: str) -> None:
        if module in active:
            errors.append("Import cycle: " + " -> ".join((*active, module)))
            return
        if module in visited:
            return
        active.append(module)
        for dependency in sorted(graph[module]):
            if dependency in graph:
                visit(dependency)
        active.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return errors


if __name__ == "__main__":
    failures = check_backend()
    if failures:
        raise SystemExit("\n".join(failures))
    print("Backend architecture passed: import direction, local imports and cycles.")
