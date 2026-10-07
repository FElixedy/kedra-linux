#!/usr/bin/env python3
"""Tests for change plans.

Plans are built from a manifest tree, a user state, and an observed-value
fixture. No test here provides a live backend, and one test asserts the fake
adapter records zero operations during planning -- planning must be inert.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    KEDRA_MODULES,
    TreeTestCase,
    backends,
    base_preset_toml,
    gsettings_toml,
    module_toml,
    observed as observed_mod,
    ownership,
    plan as plan_mod,
    resolve,
    state,
    typed,
)

X = typed("as", ["<Super>x"])
Y = typed("as", ["<Super>y"])
Z = typed("as", ["<Super>z"])
O = typed("as", ["<Super>o"])

TARGET_A = "gsettings:org.example.test:key-a"
TARGET_B = "gsettings:org.example.test:key-b"


class PlanTestCase(TreeTestCase):
    def tree_with(self, *, workflow_settings: str = "", desktop_settings: str = ""):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml("workflow", settings=workflow_settings)
        modules["desktop"] = module_toml("desktop", settings=desktop_settings)
        return self.load(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def make_state(self, *, overrides=(), managed=(), generation=1):
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=generation,
                preset_id="kedra",
                preset_version="0.1.0",
                experience_schema_version=3,
                applied_at="2026-08-14T12:00:00Z",
                managed_values=tuple(managed),
            )
        return state.UserState(
            base_preset="kedra",
            module_overrides=tuple(overrides),
            applied=applied,
        )

    def record(self, setting_id, target, last_applied, original=O, present=True, module="workflow"):
        return state.ManagedValue(
            setting_id=setting_id,
            backend="gsettings",
            target=target,
            module_id=module,
            module_version="0.1.0",
            policy="managed",
            generation=1,
            last_applied=last_applied,
            original=original,
            original_present=present,
        )

    def build(self, tree, user_state, observed, operation=ownership.OPERATION_APPLY):
        resolution = resolve.resolve_state(tree, user_state)
        return plan_mod.build_plan(
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=operation,
        )


class TestPlanContents(PlanTestCase):
    def test_first_apply_plan(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>x"])
        )
        plan = self.build(tree, self.make_state(), {})

        self.assertEqual(len(plan.entries), 1)
        entry = plan.entries[0]
        self.assertEqual(entry.setting_id, "example-a")
        self.assertEqual(entry.backend, "gsettings")
        self.assertEqual(entry.target, TARGET_A)
        self.assertEqual(entry.module_id, "workflow")
        self.assertEqual(entry.decision, ownership.APPLY)
        self.assertEqual(entry.desired_value, X)
        self.assertIsNone(entry.current_value)
        self.assertTrue(entry.writes)

    def test_entry_carries_a_reason(self):
        tree = self.tree_with(workflow_settings=gsettings_toml("example-a", key="key-a"))
        plan = self.build(tree, self.make_state(), {})
        self.assertTrue(plan.entries[0].reason.strip())

    def test_confirmation_flag_is_set_on_preserved_user_changes(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(
            managed=[self.record("example-a", TARGET_A, X)]
        )
        plan = self.build(tree, user_state, {TARGET_A: Z})

        entry = plan.entries[0]
        self.assertEqual(entry.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertTrue(entry.requires_confirmation)
        self.assertFalse(entry.writes)
        self.assertEqual(len(plan.requires_confirmation), 1)
        self.assertEqual(len(plan.writes), 0)

    def test_confirmation_flag_is_clear_on_a_safe_update(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X)])
        plan = self.build(tree, user_state, {TARGET_A: X})

        entry = plan.entries[0]
        self.assertEqual(entry.decision, ownership.UPDATE)
        self.assertFalse(entry.requires_confirmation)
        self.assertTrue(entry.writes)

    def test_plan_reports_current_desired_and_original(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X, original=O)])
        entry = self.build(tree, user_state, {TARGET_A: Z}).entries[0]

        self.assertEqual(entry.current_value, Z)
        self.assertEqual(entry.desired_value, Y)
        self.assertEqual(entry.original_value, O)

    def test_disabled_module_settings_are_withdrawn(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>x"])
        )
        user_state = self.make_state(
            overrides=[state.ModuleOverride(id="workflow", enabled=False)],
            managed=[self.record("example-a", TARGET_A, X)],
        )
        plan = self.build(tree, user_state, {TARGET_A: X})

        entry = plan.entries[0]
        self.assertEqual(entry.decision, ownership.REVERT_TO_ORIGINAL)
        self.assertEqual(entry.desired_value, None)
        self.assertTrue(entry.writes)

    def test_disabled_module_with_user_change_drops_the_record(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>x"])
        )
        user_state = self.make_state(
            overrides=[state.ModuleOverride(id="workflow", enabled=False)],
            managed=[self.record("example-a", TARGET_A, X)],
        )
        plan = self.build(tree, user_state, {TARGET_A: Z})

        self.assertEqual(plan.entries[0].decision, ownership.DROP_RECORD)
        self.assertFalse(plan.entries[0].writes)

    def test_plan_generation_is_the_next_generation(self):
        tree = self.tree_with(workflow_settings=gsettings_toml("example-a", key="key-a"))
        plan = self.build(tree, self.make_state(), {})
        self.assertEqual(plan.generation, 1)

        with_applied = self.make_state(
            managed=[self.record("example-a", TARGET_A, X)], generation=4
        )
        self.assertEqual(self.build(tree, with_applied, {}).generation, 5)

    def test_empty_plan_when_nothing_declares_settings(self):
        tree = self.tree_with()
        plan = self.build(tree, self.make_state(), {})
        self.assertEqual(plan.entries, ())


class TestDeterminism(PlanTestCase):
    def test_entries_are_sorted_by_setting_id(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-zulu", key="key-a")
            + gsettings_toml("example-alpha", key="key-b"),
        )
        plan = self.build(tree, self.make_state(), {})
        ids = [e.setting_id for e in plan.entries]

        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, ["example-alpha", "example-zulu"])

    def test_repeated_planning_is_byte_identical(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a"),
            desktop_settings=gsettings_toml("example-b", key="key-b"),
        )
        user_state = self.make_state()
        first = self.build(tree, user_state, {}).to_json()
        second = self.build(tree, user_state, {}).to_json()
        self.assertEqual(first, second)

    def test_declaration_order_does_not_affect_output(self):
        forward = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a")
            + gsettings_toml("example-b", key="key-b")
        )
        backward = self.tree_with(
            workflow_settings=gsettings_toml("example-b", key="key-b")
            + gsettings_toml("example-a", key="key-a")
        )
        self.assertEqual(
            self.build(forward, self.make_state(), {}).to_json(),
            self.build(backward, self.make_state(), {}).to_json(),
        )

    def test_json_is_parseable_and_versioned(self):
        tree = self.tree_with(workflow_settings=gsettings_toml("example-a", key="key-a"))
        payload = json.loads(self.build(tree, self.make_state(), {}).to_json())

        self.assertEqual(
            payload["plan_schema_version"], plan_mod.PLAN_SCHEMA_VERSION
        )
        self.assertEqual(payload["operation"], "apply")
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["writes"], 1)

    def test_plan_entries_are_immutable(self):
        tree = self.tree_with(workflow_settings=gsettings_toml("example-a", key="key-a"))
        plan = self.build(tree, self.make_state(), {})
        with self.assertRaises(Exception):
            plan.entries[0].decision = "apply"


class TestNoSideEffects(PlanTestCase):
    def test_planning_performs_no_backend_operations(self):
        """The fake adapter must be untouched: planning reads a fixture only."""
        adapter = backends.InMemoryAdapter(values={TARGET_A: X})
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X)])

        self.build(tree, user_state, {TARGET_A: X})

        self.assertEqual(adapter.operations, [])
        self.assertEqual(adapter.writes, [])

    def test_planning_does_not_mutate_the_state(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X)])
        before = user_state.to_toml()

        self.build(tree, user_state, {TARGET_A: Z})

        self.assertEqual(user_state.to_toml(), before)

    def test_planner_has_no_read_method(self):
        """Structural guarantee: the planner cannot reach a session."""
        self.assertFalse(hasattr(plan_mod, "read"))
        for name in dir(plan_mod):
            self.assertNotIn("gsettings", name.lower())


class TestNoDesktopToolInvocation(unittest.TestCase):
    """Nothing in the package may drive a desktop tool as a subprocess.

    The guarantee is narrower than it was and stated more precisely, because
    the package now *does* reach a live desktop -- through `gsettings`, using
    GLib directly. What must remain absent is the process-spawning capability:
    no module may import `subprocess`, call `os.system`/`os.exec*`/`popen`, or
    evaluate a string.

    Checked by walking each module's AST rather than by searching its text.
    Substring matching cannot tell an import from a docstring that explains why
    there is no import, and `gsettings.py` has to explain exactly that. Parsing
    is also the stronger check: it sees `from os import system` and an aliased
    import, which a text search does not.
    """

    SOURCES = sorted(
        (Path(__file__).resolve().parent.parent / "tools" / "kedra_experience").glob("*.py")
    )

    FORBIDDEN_MODULES = frozenset({"subprocess", "pty", "commands"})
    FORBIDDEN_CALLS = frozenset(
        {"system", "popen", "eval", "exec", "spawnl", "spawnv", "spawnlp", "spawnvp"}
    )

    def _trees(self):
        for path in self.SOURCES:
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_sources_exist(self):
        self.assertGreaterEqual(len(self.SOURCES), 11)

    def test_no_module_imports_a_process_spawner(self):
        for path, tree in self._trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0],
                            self.FORBIDDEN_MODULES,
                            f"{path.name} imports {alias.name}",
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        node.module.split(".")[0],
                        self.FORBIDDEN_MODULES,
                        f"{path.name} imports from {node.module}",
                    )

    def test_no_module_calls_a_process_spawner_or_evaluates_a_string(self):
        for path, tree in self._trees():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None
                )
                if name is None:
                    continue
                self.assertNotIn(
                    name,
                    self.FORBIDDEN_CALLS,
                    f"{path.name} calls {name}()",
                )

    def _string_literals(self, tree):
        """Every string constant in a module, docstrings excluded.

        Prose has to be free to name a program in order to explain why it is
        not run; a *literal* naming one is the thing worth catching. Docstrings
        are stripped by comparing against the set of docstring nodes rather than
        by heuristics on position.
        """
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = getattr(node, "body", None)
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_desktop_tool_is_named_as_a_command(self):
        """`gsettings` and `dconf` are libraries here, never command lines.

        The real adapter uses Gio directly, so there is no reason for either
        program's name to head a command string -- and if one ever did, it would
        mean somebody had reintroduced the lossy text round trip the adapter
        exists to avoid. `dconf dump` is called out on its own: reading a user's
        whole database is forbidden regardless of how it is spelled.
        """
        for path, tree in self._trees():
            for literal in self._string_literals(tree):
                head = literal.lstrip()
                for program in ("gsettings ", "dconf "):
                    self.assertFalse(
                        head.startswith(program),
                        f"{path.name} has a literal that begins a {program.strip()} "
                        f"command line: {literal!r}",
                    )
                # Banned wherever it appears, not only at the start: reading a
                # user's whole database is forbidden however it is spelled.
                self.assertNotIn(
                    "dconf dump", literal, f"{path.name} mentions a dconf dump"
                )

    def test_only_shortcuts_builds_a_command_string(self):
        """The one allowed command string, and the one place it is built.

        GNOME types a custom keybinding's `command` as a string, so a launch
        command has to be produced somewhere. That exception is confined to
        `shortcuts.launch_command_for_application_id`; no manifest, no state
        file and no other module contains one.
        """
        from kedra_experience import shortcuts

        self.assertEqual(shortcuts.LAUNCH_PROGRAM, "gapplication")

        offenders = sorted(
            {
                path.name
                for path, tree in self._trees()
                if path.name != "shortcuts.py"
                and any(
                    shortcuts.LAUNCH_PROGRAM in literal
                    for literal in self._string_literals(tree)
                )
            }
        )
        self.assertEqual(offenders, [], "a launch command escaped shortcuts.py")


class TestWorkedExamples(PlanTestCase):
    """The two scenarios the architecture is judged on."""

    def test_user_change_survives_a_kedra_upgrade(self):
        # Kedra 0.1.0 wrote X; the user changed it to Z; Kedra now wants Y.
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X, original=O)])
        plan = self.build(tree, user_state, {TARGET_A: Z})
        entry = plan.entries[0]

        self.assertEqual(entry.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertFalse(entry.writes)
        self.assertEqual(entry.current_value, Z)   # the user's value stands
        self.assertEqual(entry.desired_value, Y)
        self.assertEqual(len(plan.writes), 0)

    def test_explicit_reset_overrides_the_same_divergence(self):
        tree = self.tree_with(
            workflow_settings=gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record("example-a", TARGET_A, X, original=O)])
        plan = self.build(
            tree, user_state, {TARGET_A: Z}, operation=ownership.OPERATION_RESTORE
        )
        entry = plan.entries[0]

        self.assertEqual(entry.decision, ownership.FORCE_APPLY)
        self.assertTrue(entry.writes)
        self.assertTrue(entry.requires_confirmation)
        self.assertEqual(entry.desired_value, Y)


class TestObservedFixtures(unittest.TestCase):
    def parse(self, doc):
        return observed_mod.parse_observed(doc)

    def test_valid_fixture(self):
        result = self.parse(
            {
                "observed_schema_version": 1,
                "observed": [
                    {"target": TARGET_A, "type": "as", "data": ["<Super>x"]},
                    {"target": TARGET_B, "absent": True},
                ],
            }
        )
        self.assertEqual(result[TARGET_A], X)
        self.assertIsNone(result[TARGET_B])

    def test_missing_schema_version_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError):
            self.parse({"observed": []})

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError):
            self.parse({"observed_schema_version": 99, "observed": []})

    def test_absent_with_a_value_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError) as ctx:
            self.parse(
                {
                    "observed_schema_version": 1,
                    "observed": [{"target": TARGET_A, "absent": True, "type": "s", "data": "x"}],
                }
            )
        self.assertIn("absent but also carries a value", str(ctx.exception))

    def test_duplicate_target_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError):
            self.parse(
                {
                    "observed_schema_version": 1,
                    "observed": [
                        {"target": TARGET_A, "type": "s", "data": "a"},
                        {"target": TARGET_A, "type": "s", "data": "b"},
                    ],
                }
            )

    def test_malformed_value_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError):
            self.parse(
                {
                    "observed_schema_version": 1,
                    "observed": [{"target": TARGET_A, "type": "i", "data": "not-an-int"}],
                }
            )

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(observed_mod.ObservedError):
            self.parse(
                {
                    "observed_schema_version": 1,
                    "observed": [{"target": TARGET_A, "type": "s", "data": "a", "extra": 1}],
                }
            )

    def test_missing_target_from_fixture_is_treated_as_unset(self):
        result = self.parse({"observed_schema_version": 1, "observed": []})
        self.assertIsNone(result.get(TARGET_A))


class TestInMemoryAdapter(unittest.TestCase):
    """The only backend adapter that exists; a test double, by design."""

    def test_read_apply_reset_cycle(self):
        adapter = backends.InMemoryAdapter()
        self.assertIsNone(adapter.read(TARGET_A))

        adapter.apply(TARGET_A, X)
        self.assertEqual(adapter.read(TARGET_A), X)

        adapter.reset(TARGET_A)
        self.assertIsNone(adapter.read(TARGET_A))

    def test_operations_are_recorded(self):
        adapter = backends.InMemoryAdapter()
        adapter.apply(TARGET_A, X)
        adapter.read(TARGET_A)
        adapter.reset(TARGET_A)

        self.assertEqual([op[0] for op in adapter.operations], ["apply", "read", "reset"])
        self.assertEqual([op[0] for op in adapter.writes], ["apply", "reset"])

    def test_the_adapter_set_is_exactly_the_fake_and_the_real_one(self):
        """Two adapters, and a test that makes a third a deliberate act.

        `GSettingsAdapter` is the production backend; `InMemoryAdapter` is what
        every offline test runs against. A new `BackendAdapter` subclass is a new
        way for this package to change a system, so it should not be able to
        appear without someone editing this line.
        """
        from kedra_experience import gsettings  # noqa: F401 - registers the subclass

        subclasses = {c.__name__ for c in backends.BackendAdapter.__subclasses__()}
        self.assertEqual(subclasses, {"InMemoryAdapter", "GSettingsAdapter"})

    def test_planning_never_constructs_the_real_adapter(self):
        """The planner is inert, and remains inert now that a real one exists."""
        import ast

        planner = Path(__file__).resolve().parent.parent / "tools" / "kedra_experience" / "plan.py"
        tree = ast.parse(planner.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("gsettings", node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("gsettings", alias.name)

    def test_the_gsettings_spec_cannot_perform_io(self):
        spec = backends.SPECS["gsettings"]
        for method in ("read", "apply", "reset"):
            self.assertFalse(hasattr(spec, method), f"spec exposes {method}()")


if __name__ == "__main__":
    unittest.main()
