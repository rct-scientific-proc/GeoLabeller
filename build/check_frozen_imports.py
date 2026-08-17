"""Fail the build if the application imports a module the freeze excludes.

setup.py trims the frozen output by listing modules cx_Freeze should leave out.
Nothing checks that list against what the code actually imports, so excluding a
module the application needs produces a build that succeeds, an installer that
installs, and an executable that dies on startup with ModuleNotFoundError.

That shipped: "html" was excluded while app/shortcuts.py imported html.escape,
and because app/main_window.py imports shortcuts at module scope, 1.2.1 and
1.3.0 could not start at all.

Run from anywhere:

    python build/check_frozen_imports.py

Exits non-zero and names the offending module and the file that imports it.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = ROOT / "build" / "setup.py"
# Everything that ends up in the frozen application. Tests are not packaged, so
# what they import has no bearing on what the executable needs.
SOURCES = [ROOT / "main.py", *sorted((ROOT / "app").glob("*.py"))]


def excluded_modules() -> set[str]:
    """The `excludes` list from setup.py's build_exe options.

    Read with ast rather than by importing setup.py, which would run a
    cx_Freeze setup() call as a side effect.
    """
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "build_exe_options" for t in node.targets):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if getattr(key, "value", None) == "excludes":
                return {el.value for el in value.elts}
    raise SystemExit("could not find build_exe_options['excludes'] in setup.py")


def imported_modules(path: Path) -> set[str]:
    """Dotted module names imported by *path*, at any indentation.

    A guarded or function-local import still fails when it runs, so nesting is
    not a reason to skip one.
    """
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import - part of the application itself.
            if node.level == 0 and node.module:
                found.add(node.module)
    return found


def main() -> int:
    excludes = excluded_modules()
    problems = []
    for source in SOURCES:
        for module in sorted(imported_modules(source)):
            # "concurrent" excluded also rules out "concurrent.futures".
            parts = module.split(".")
            hit = next((".".join(parts[:i + 1]) for i in range(len(parts))
                        if ".".join(parts[:i + 1]) in excludes), None)
            if hit:
                problems.append((source.relative_to(ROOT), module, hit))

    if problems:
        print("Frozen build would be missing modules the application imports:\n")
        for source, module, hit in problems:
            via = f" (excluded as '{hit}')" if hit != module else ""
            print(f"  {source} imports '{module}'{via}")
        print("\nRemove it from build_exe_options['excludes'] in build/setup.py,")
        print("or stop importing it. Leaving it excluded ships an executable")
        print("that fails on startup with ModuleNotFoundError.")
        return 1

    print(f"Frozen imports OK: {len(SOURCES)} source files, "
          f"none import any of the {len(excludes)} excluded modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
