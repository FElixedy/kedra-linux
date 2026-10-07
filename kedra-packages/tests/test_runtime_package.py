"""What `kedra-experience-runtime` is allowed to contain.

The runtime package installs a *subset* of this source tree, and the subset is
the whole safety argument for shipping it: the installed command cannot write a
GSettings value because the module that could is not on the target's disk. A
list in a PKGBUILD would make that claim a promise; these tests make it a
checked property.

Three things are verified, all by reading the source rather than by importing
it, so a test failure names the import that broke the boundary instead of
crashing on it:

1. `RUNTIME_MODULES` is exactly the transitive import closure of `prodcli`.
   Too small and the installed package is broken; too large and it ships code
   nobody asked for.
2. No module in the closure imports anything that mutates a system.
3. The shipped CLI exposes no mutating verb.
"""

from __future__ import annotations

import ast
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from support import REPO_ROOT  # noqa: F401 - imported for its sys.path setup

from kedra_experience import RUNTIME_MODULES, UNSHIPPED_MODULES

PACKAGE_DIR = REPO_ROOT / "tools" / "kedra_experience"

# The one module the runtime is entered through. Everything it can reach is
# shipped; everything it cannot is not.
ENTRY_MODULE = "prodcli"


def _module_path(name: str) -> Path:
    return PACKAGE_DIR / f"{name}.py"


def _local_imports(name: str) -> set[str]:
    """Sibling modules `name` imports, including inside function bodies.

    Deliberately AST-based and deliberately not import-time: several modules
    import a sibling lazily inside a function precisely to keep a heavy or
    optional dependency off the import path, and a lazy import still puts the
    file on the target. `ast.walk` sees both.
    """
    tree = ast.parse(_module_path(name).read_text(encoding="utf-8"), filename=str(name))
    found: set[str] = set()
    for node in ast.walk(tree):
        # `from .sibling import thing` -- level 1 is this package.
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            found.add(node.module.split(".")[0])
        # `from . import sibling as alias`
        elif isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
            found.update(alias.name for alias in node.names)
    return found


def _closure(entry: str) -> set[str]:
    """Every sibling module reachable from `entry`, plus the package __init__."""
    # __init__ is unconditionally present: importing any submodule executes it.
    seen = {entry, "__init__"}
    pending = [entry]
    while pending:
        current = pending.pop()
        for name in _local_imports(current):
            if name not in seen and _module_path(name).is_file():
                seen.add(name)
                pending.append(name)
    return seen


class RuntimeClosureTests(unittest.TestCase):
    def test_declared_set_is_exactly_the_import_closure(self):
        self.assertEqual(
            sorted(_closure(ENTRY_MODULE)),
            sorted(RUNTIME_MODULES),
            "RUNTIME_MODULES must be exactly what prodcli can reach: a missing "
            "module makes the installed command fail on a target, and an extra "
            "one ships code the runtime does not use",
        )

    def test_every_declared_module_exists(self):
        for name in RUNTIME_MODULES:
            with self.subTest(module=name):
                self.assertTrue(
                    _module_path(name).is_file(), f"{name}.py is declared but absent"
                )

    def test_the_set_is_sorted_and_unique(self):
        # The PKGBUILD installs this list verbatim; a duplicate would install a
        # file twice and a reordering would make package diffs noisy.
        self.assertEqual(list(RUNTIME_MODULES), sorted(set(RUNTIME_MODULES)))


class UnshippedModuleTests(unittest.TestCase):
    def test_no_unshipped_module_is_packaged(self):
        overlap = sorted(set(RUNTIME_MODULES) & set(UNSHIPPED_MODULES))
        self.assertEqual(
            overlap,
            [],
            "the developer CLI, the vendor-default renderer and the observed-value "
            "fixture reader have no business on a target",
        )

    def test_every_unshipped_module_actually_exists(self):
        # Guards the guard: a typo in UNSHIPPED_MODULES would make the overlap
        # check above pass vacuously for that name.
        for name in UNSHIPPED_MODULES:
            with self.subTest(module=name):
                self.assertTrue(_module_path(name).is_file())

    def test_the_observed_fixture_reader_is_not_reachable(self):
        # The production path observes a live session through the adapter.
        # Shipping a way to feed the planner values from a file would be a way
        # to make it write things nobody's desktop actually said.
        self.assertNotIn("observed", _closure(ENTRY_MODULE))

    def test_no_shipped_module_imports_gi_at_module_scope(self):
        # PyGObject IS a dependency now, but importing it eagerly would make
        # every command -- `profiles`, `resolve`, `install-state`, none of which
        # needs a desktop -- fail on a machine without it. All three `import gi`
        # sites stay inside function bodies.
        for name in RUNTIME_MODULES:
            tree = ast.parse(_module_path(name).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[0], "gi", name)
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertNotEqual((node.module or "").split(".")[0], "gi", name)

    def test_shipped_modules_import_no_third_party_package(self):
        # Stdlib only, by rule. Anything else would become an undeclared
        # dependency of kedra-experience-runtime.
        allowed = set(RUNTIME_MODULES) | {
            "__future__", "abc", "argparse", "ast", "contextlib", "dataclasses",
            "datetime", "errno", "fcntl", "json", "os", "pathlib", "sys",
            "time", "tomllib", "typing",
        }
        for name in RUNTIME_MODULES:
            tree = ast.parse(_module_path(name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root == "gi":
                            continue  # lazy, and guarded by the test above
                        self.assertIn(root, allowed, f"{name} imports {root}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    root = node.module.split(".")[0]
                    if root == "gi":
                        continue
                    self.assertIn(root, allowed, f"{name} imports from {root}")


class ShippedSurfaceTests(unittest.TestCase):
    def test_the_cli_surface_is_exactly_this(self):
        """The whole shipped surface, pinned.

        Three of these do not touch a desktop at all; `apply` and `unmanage`
        do, in the user's own session, and only for `policy = "managed"`
        resources. Adding a verb should require editing this test, and editing
        it should require saying which kind it is.
        """
        from kedra_experience.prodcli import build_parser

        actions = build_parser()._subparsers._group_actions  # noqa: SLF001
        choices = sorted(actions[0].choices)
        self.assertEqual(
            choices, ["apply", "install-state", "profiles", "resolve", "unmanage"]
        )

    def test_no_force_or_restore_verb_is_offered(self):
        # restore-to-defaults reclaims a resource over a user's own divergence.
        # It is not something a login should be able to do, and there is
        # deliberately no --operation switch that would smuggle it in.
        from kedra_experience.prodcli import build_parser

        parser = build_parser()
        for verb in ("restore", "restore-defaults", "force", "execute", "plan"):
            with self.subTest(verb=verb):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args([verb])

    def test_apply_takes_no_operation_switch(self):
        from kedra_experience.prodcli import build_parser

        parser = build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--operation", "restore-defaults"])

    def test_the_developer_cli_is_not_shipped(self):
        # The audit's finding, kept true: `cli` carries development-only
        # assumptions and an in-memory execution path, so renaming it was never
        # an option and it must stay out of the package.
        self.assertNotIn("cli", RUNTIME_MODULES)

    def test_vendordefaults_is_not_shipped(self):
        # Build-time rendering. It writes files, it is only ever called by
        # makepkg, and a target has no use for it.
        self.assertNotIn("vendordefaults", RUNTIME_MODULES)


if __name__ == "__main__":
    unittest.main()
