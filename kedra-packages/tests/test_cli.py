#!/usr/bin/env python3
"""Tests for the internal developer CLI.

Every invocation is given explicit paths inside temporary directories. The CLI
is developer infrastructure, so these tests cover exit codes and machine
output rather than presentation.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    DATA_DIR,
    KEDRA_MODULES,
    TreeTestCase,
    base_preset_toml,
    derived_preset_toml,
    module_toml,
)
from kedra_experience import cli, state  # noqa: E402


class CliTestCase(TreeTestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as exc:  # argparse and _load_tree_or_exit
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()


class TestValidateManifests(CliTestCase):
    def test_valid_tree_exits_zero(self):
        root = self.make_tree()
        code, out, _ = self.run_cli(["--data-dir", str(root), "validate-manifests"])
        self.assertEqual(code, 0)
        self.assertIn("all manifests valid", out)

    def test_shipped_tree_is_valid(self):
        code, _, _ = self.run_cli(["--data-dir", str(DATA_DIR), "validate-manifests"])
        self.assertEqual(code, 0)

    def test_broken_tree_exits_nonzero(self):
        root = self.make_tree()
        (root / "modules" / "desktop.toml").write_text("[module\n", encoding="utf-8")
        code, _, err = self.run_cli(["--data-dir", str(root), "validate-manifests"])
        self.assertEqual(code, 1)
        self.assertIn("malformed TOML", err)


class TestResolveCommand(CliTestCase):
    def test_resolve_preset_as_json(self):
        root = self.make_tree()
        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "resolve", "--preset", "kedra", "--format", "json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["preset_id"], "kedra")
        self.assertEqual(len(payload["modules"]), 7)

    def test_resolve_defaults_to_the_trees_default_preset(self):
        root = self.make_tree(default_preset="kedra")
        code, out, _ = self.run_cli(["--data-dir", str(root), "resolve", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["preset_id"], "kedra")

    def test_resolve_text_output_marks_the_unapplied_layer(self):
        root = self.make_tree(
            presets={
                "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                "vanilla": base_preset_toml("vanilla", {}, applies=False),
            }
        )
        code, out, _ = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "vanilla"])
        self.assertEqual(code, 0)
        self.assertIn("NOT APPLIED", out)
        self.assertIn("modules: (none)", out)

    def test_advisories_are_reported_on_stdout_and_do_not_fail(self):
        """An advisory is information about a valid profile, not an error."""
        root = self.make_tree(
            modules={
                "alpha": module_toml(
                    "alpha", works_best_with={"beta": "alpha is nicer with beta"}
                ),
                "beta": module_toml("beta"),
            },
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": False})},
        )
        code, out, err = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "kedra"])
        self.assertEqual(code, 0)
        self.assertIn("advisories:", out)
        self.assertIn("works best with 'beta'", out)
        self.assertIn("alpha is nicer with beta", out)
        # Never described as an error, and never on the error stream.
        self.assertNotIn("error", out.lower())
        self.assertEqual(err, "")

    def test_a_hard_requirement_violation_is_an_error_and_fails(self):
        root = self.make_tree(
            modules={
                "alpha": module_toml("alpha", requires=("beta",)),
                "beta": module_toml("beta"),
            },
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": False})},
        )
        code, out, err = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "kedra"])
        self.assertEqual(code, 1)
        self.assertIn("errors:", err)
        self.assertIn("requires 'beta'", err)
        self.assertNotIn("advisories:", out)

    def test_the_two_severities_are_reported_separately(self):
        root = self.make_tree(
            modules={
                "alpha": module_toml("alpha", requires=("beta",), works_best_with=("gamma",)),
                "beta": module_toml("beta"),
                "gamma": module_toml("gamma"),
            },
            presets={
                "kedra": base_preset_toml(
                    "kedra", {"alpha": True, "beta": False, "gamma": False}
                )
            },
        )
        code, out, err = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "kedra"])
        self.assertEqual(code, 1)
        self.assertIn("works best with 'gamma'", out)
        self.assertIn("requires 'beta'", err)
        # The advisory must not be restated as an error, or vice versa.
        self.assertNotIn("gamma", err)
        self.assertNotIn("requires 'beta'", out)

    def test_json_output_separates_them_too(self):
        root = self.make_tree(
            modules={
                "alpha": module_toml("alpha", works_best_with=("beta",)),
                "beta": module_toml("beta"),
            },
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": False})},
        )
        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "resolve", "--preset", "kedra", "--format", "json"]
        )
        self.assertEqual(code, 0)
        diagnostics = json.loads(out)["diagnostics"]
        self.assertTrue(diagnostics["ok"])
        self.assertEqual(diagnostics["violations"], [])
        self.assertEqual(len(diagnostics["advisories"]), 1)
        self.assertEqual(
            diagnostics["advisories"][0]["code"], "works-best-with-disabled"
        )

    def test_the_shipped_kedra_preset_reports_neither(self):
        code, out, err = self.run_cli(
            ["--data-dir", str(DATA_DIR), "resolve", "--preset", "kedra"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("advisories:", out)
        self.assertEqual(err, "")

    def test_resolve_unknown_preset_exits_nonzero(self):
        root = self.make_tree()
        code, _, err = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "ghost"])
        self.assertEqual(code, 1)
        self.assertIn("unknown preset 'ghost'", err)

    def test_resolve_reports_inheritance_cycles(self):
        root = self.make_tree(
            presets={
                "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                "alpha": derived_preset_toml("alpha", "beta", {}),
                "beta": derived_preset_toml("beta", "alpha", {}),
            }
        )
        code, _, err = self.run_cli(["--data-dir", str(root), "resolve", "--preset", "alpha"])
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)

    def test_resolve_from_a_state_file(self):
        root = self.make_tree()
        state_path = self.temp_dir() / "state.toml"
        state.UserState(
            base_preset="kedra",
            module_overrides=(
                state.ModuleOverride(id="terminal", enabled=False),
                state.ModuleOverride(id="extensions", enabled=False),
            ),
        ).save(state_path)

        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "resolve", "--state", str(state_path), "--format", "json"]
        )
        self.assertEqual(code, 0)
        modules = {m["id"]: m["enabled"] for m in json.loads(out)["modules"]}
        self.assertEqual(
            modules,
            {
                "desktop": True,
                "workflow": True,
                "appearance": True,
                "terminal": False,
                "extensions": False,
                "integration": True,
                "extras": False,
            },
        )

    def test_preset_and_state_are_mutually_exclusive(self):
        root = self.make_tree()
        code, _, _ = self.run_cli(
            ["--data-dir", str(root), "resolve", "--preset", "kedra", "--state", "x.toml"]
        )
        self.assertNotEqual(code, 0)


class TestValidateStateCommand(CliTestCase):
    def test_valid_state_exits_zero(self):
        root = self.make_tree()
        state_path = self.temp_dir() / "state.toml"
        state.UserState(base_preset="kedra").save(state_path)

        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "validate-state", str(state_path)]
        )
        self.assertEqual(code, 0)
        self.assertIn("state valid", out)

    def test_invalid_state_exits_nonzero(self):
        root = self.make_tree()
        state_path = self.temp_dir() / "state.toml"
        state_path.write_text("state_schema_version = 3\n", encoding="utf-8")

        code, _, err = self.run_cli(
            ["--data-dir", str(root), "validate-state", str(state_path)]
        )
        self.assertEqual(code, 1)
        self.assertIn("missing required [selection] table", err)

    def test_unknown_module_is_caught_against_the_tree(self):
        root = self.make_tree()
        state_path = self.temp_dir() / "state.toml"
        state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="ghost", enabled=False),),
        ).save(state_path)

        code, _, err = self.run_cli(
            ["--data-dir", str(root), "validate-state", str(state_path)]
        )
        self.assertEqual(code, 1)
        self.assertIn("nonexistent module 'ghost'", err)

    def test_no_tree_skips_reference_checks(self):
        state_path = self.temp_dir() / "state.toml"
        state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="ghost", enabled=False),),
        ).save(state_path)

        code, _, _ = self.run_cli(["validate-state", str(state_path), "--no-tree"])
        self.assertEqual(code, 0)

    def test_state_path_argument_is_required(self):
        code, _, _ = self.run_cli(["validate-state"])
        self.assertNotEqual(code, 0)


class TestPlanCommand(CliTestCase):
    """The plan command reads fixtures only; it never touches a session."""

    def setting_tree(self, value="<Super>y"):
        from support import gsettings_toml, module_toml

        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow", settings=gsettings_toml("example-a", key="key-a", data=[value])
        )
        return self.make_tree(
            modules=modules,
            presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
        )

    def write_state(self, *, managed=()):
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=1,
                preset_id="kedra",
                preset_version="0.1.0",
                experience_schema_version=3,
                applied_at="2026-08-14T12:00:00Z",
                managed_values=tuple(managed),
            )
        path = self.temp_dir() / "state.toml"
        state.UserState(base_preset="kedra", applied=applied).save(path)
        return path

    def write_observed(self, target, value=None, absent=False):
        path = self.temp_dir() / "observed.toml"
        if absent:
            body = f'[[observed]]\ntarget = "{target}"\nabsent = true\n'
        else:
            body = f'[[observed]]\ntarget = "{target}"\ntype = "as"\ndata = ["{value}"]\n'
        path.write_text("observed_schema_version = 1\n\n" + body, encoding="utf-8")
        return path

    def managed_record(self, last_applied="<Super>x"):
        from support import typed

        return state.ManagedValue(
            setting_id="example-a",
            backend="gsettings",
            target="gsettings:org.example.test:key-a",
            module_id="workflow",
            module_version="0.1.0",
            policy="managed",
            generation=1,
            last_applied=typed("as", [last_applied]),
            original=typed("as", ["<Super>o"]),
            original_present=True,
        )

    def test_first_apply_plan_as_json(self):
        root = self.setting_tree()
        state_path = self.write_state()
        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "plan", str(state_path), "--format", "json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "apply")
        self.assertEqual(payload["entries"][0]["decision"], "apply")

    def test_plan_preserves_a_user_change(self):
        root = self.setting_tree(value="<Super>y")
        state_path = self.write_state(managed=[self.managed_record("<Super>x")])
        observed = self.write_observed("gsettings:org.example.test:key-a", "<Super>z")

        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "plan", str(state_path), str(observed), "--format", "json"]
        )
        self.assertEqual(code, 0)
        entry = json.loads(out)["entries"][0]
        self.assertEqual(entry["decision"], "preserve-user-change")
        self.assertFalse(entry["writes"])
        self.assertTrue(entry["requires_confirmation"])

    def test_explicit_reset_overrides_the_same_divergence(self):
        root = self.setting_tree(value="<Super>y")
        state_path = self.write_state(managed=[self.managed_record("<Super>x")])
        observed = self.write_observed("gsettings:org.example.test:key-a", "<Super>z")

        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "plan", str(state_path), str(observed),
             "--operation", "restore-defaults", "--format", "json"]
        )
        self.assertEqual(code, 0)
        entry = json.loads(out)["entries"][0]
        self.assertEqual(entry["decision"], "force-apply")
        self.assertTrue(entry["writes"])
        self.assertTrue(entry["requires_confirmation"])

    def test_text_output_flags_entries_needing_confirmation(self):
        root = self.setting_tree(value="<Super>y")
        state_path = self.write_state(managed=[self.managed_record("<Super>x")])
        observed = self.write_observed("gsettings:org.example.test:key-a", "<Super>z")

        code, out, _ = self.run_cli(
            ["--data-dir", str(root), "plan", str(state_path), str(observed)]
        )
        self.assertEqual(code, 0)
        self.assertIn("need confirmation", out)
        self.assertIn("preserve-user-change", out)

    def test_plan_output_is_deterministic(self):
        root = self.setting_tree()
        state_path = self.write_state()
        argv = ["--data-dir", str(root), "plan", str(state_path), "--format", "json"]
        self.assertEqual(self.run_cli(argv)[1], self.run_cli(argv)[1])

    def test_malformed_observed_fixture_exits_nonzero(self):
        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.temp_dir() / "observed.toml"
        observed.write_text("observed_schema_version = 99\n", encoding="utf-8")

        code, _, err = self.run_cli(
            ["--data-dir", str(root), "plan", str(state_path), str(observed)]
        )
        self.assertEqual(code, 1)
        self.assertIn("unsupported observed_schema_version", err)

    def test_plan_writes_nothing_to_disk(self):
        root = self.setting_tree()
        state_path = self.write_state()
        before = state_path.read_text()
        observed = self.write_observed("gsettings:org.example.test:key-a", "<Super>z")

        self.run_cli(["--data-dir", str(root), "plan", str(state_path), str(observed)])

        self.assertEqual(state_path.read_text(), before)
        self.assertEqual(list(state_path.parent.iterdir()), [state_path])

    def test_state_argument_is_required(self):
        code, _, _ = self.run_cli(["plan"])
        self.assertNotEqual(code, 0)


class TestExecuteCommand(CliTestCase):
    """Offline execution against a simulated backend; never a live desktop."""

    TARGET = "gsettings:org.example.test:key-a"

    def setting_tree(self, value="<Super>y", policy="managed"):
        from support import gsettings_toml, module_toml

        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow",
            version="0.2.0",
            settings=gsettings_toml("example-a", key="key-a", data=[value], policy=policy),
        )
        return self.make_tree(
            modules=modules,
            presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
        )

    def write_state(self, *, managed=()):
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=1,
                preset_id="kedra",
                preset_version="0.1.0",
                experience_schema_version=3,
                applied_at="2026-08-14T12:00:00Z",
                managed_values=tuple(managed),
            )
        path = self.temp_dir() / "experience-state.toml"
        state.UserState(base_preset="kedra", applied=applied).save(path)
        return path

    def write_observed(self, value):
        path = self.temp_dir() / "observed.toml"
        path.write_text(
            "observed_schema_version = 1\n\n"
            f'[[observed]]\ntarget = "{self.TARGET}"\ntype = "as"\ndata = ["{value}"]\n',
            encoding="utf-8",
        )
        return path

    def managed_record(self, last_applied="<Super>x", original="<Super>o"):
        from support import typed

        return state.ManagedValue(
            setting_id="example-a",
            backend="gsettings",
            target=self.TARGET,
            module_id="workflow",
            module_version="0.1.0",
            policy="managed",
            generation=1,
            last_applied=typed("as", [last_applied]),
            original=typed("as", [original]),
            original_present=True,
        )

    def execute(self, root, state_path, observed=None, *extra):
        argv = ["--data-dir", str(root), "execute", str(state_path)]
        if observed is not None:
            argv.append(str(observed))
        argv += ["--applied-at", "2026-08-14T13:00:00Z", *extra]
        return self.run_cli(argv)

    def test_first_apply_writes_state_and_simulated_backend(self):
        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        code, out, _ = self.execute(root, state_path, observed, "--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)

        self.assertTrue(payload["committed"])
        self.assertTrue(payload["state_written"])
        self.assertEqual(payload["changes"][0]["record_action"], "created")
        self.assertEqual(
            payload["simulated_backend"][self.TARGET], {"type": "as", "data": ["<Super>y"]}
        )

    def test_state_file_is_updated_in_place(self):
        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        self.execute(root, state_path, observed)
        reloaded = state.UserState.load(state_path)

        self.assertIsNotNone(reloaded.applied)
        record = reloaded.applied.managed("example-a")
        self.assertEqual(record.original.data, ("<Super>o",))
        self.assertEqual(record.last_applied.data, ("<Super>y",))

    def test_dry_run_does_not_write_state(self):
        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")
        before = state_path.read_text()

        code, out, _ = self.execute(
            root, state_path, observed, "--dry-run", "--format", "json"
        )
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["state_written"])
        self.assertEqual(state_path.read_text(), before)

    def test_user_divergence_is_preserved_and_nothing_is_written(self):
        root = self.setting_tree(value="<Super>y")
        state_path = self.write_state(managed=[self.managed_record("<Super>x")])
        observed = self.write_observed("<Super>z")
        before = state_path.read_text()

        code, out, _ = self.execute(root, state_path, observed, "--format", "json")
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(payload["changes"][0]["decision"], "preserve-user-change")
        self.assertFalse(payload["changes"][0]["wrote"])
        self.assertFalse(payload["committed"])
        self.assertEqual(state_path.read_text(), before)
        self.assertEqual(
            payload["simulated_backend"][self.TARGET], {"type": "as", "data": ["<Super>z"]}
        )

    def test_restore_defaults_overwrites_divergence(self):
        root = self.setting_tree(value="<Super>y")
        state_path = self.write_state(managed=[self.managed_record("<Super>x")])
        observed = self.write_observed("<Super>z")

        code, out, _ = self.execute(
            root, state_path, observed, "--operation", "restore-defaults", "--format", "json"
        )
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(payload["changes"][0]["decision"], "force-apply")
        self.assertEqual(
            payload["simulated_backend"][self.TARGET], {"type": "as", "data": ["<Super>y"]}
        )
        # The original is preserved through a forced re-assert.
        record = state.UserState.load(state_path).applied.managed("example-a")
        self.assertEqual(record.original.data, ("<Super>o",))

    def test_default_policy_writes_nothing(self):
        root = self.setting_tree(policy="default")
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        code, out, _ = self.execute(root, state_path, observed, "--format", "json")
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertFalse(payload["committed"])
        self.assertEqual(payload["changes"][0]["record_action"], "none")
        self.assertEqual(
            payload["simulated_backend"][self.TARGET], {"type": "as", "data": ["<Super>o"]}
        )

    def test_text_output_shows_backend_and_state(self):
        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        code, out, _ = self.execute(root, state_path, observed)
        self.assertEqual(code, 0)
        self.assertIn("simulated backend:", out)
        self.assertIn("state written: True", out)
        self.assertIn("record created", out)

    def test_output_is_deterministic(self):
        root = self.setting_tree()
        observed = self.write_observed("<Super>o")
        first = self.execute(root, self.write_state(), observed, "--format", "json")[1]
        second = self.execute(root, self.write_state(), observed, "--format", "json")[1]
        self.assertEqual(first, second)

    def test_execution_holds_the_state_lock(self):
        from kedra_experience import locking

        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        with locking.state_lock(state_path):
            code, _, err = self.execute(root, state_path, observed)

        self.assertEqual(code, 1)
        self.assertIn("state lock", err)

    def test_lock_is_released_after_execution(self):
        from kedra_experience import locking

        root = self.setting_tree()
        state_path = self.write_state()
        observed = self.write_observed("<Super>o")

        self.execute(root, state_path, observed)
        with locking.state_lock(state_path):
            pass

    def test_state_argument_is_required(self):
        code, _, _ = self.run_cli(["execute"])
        self.assertNotEqual(code, 0)


class TestShortcutsCommand(CliTestCase):
    """Inspection only: it resolves, expands and prints. It applies nothing."""

    def shortcuts(self, *args):
        return self.run_cli(["--data-dir", str(DATA_DIR), "shortcuts", *args])

    def test_kedra_shows_the_terminal_shortcut_and_its_four_settings(self):
        code, out, _ = self.shortcuts("--preset", "kedra")
        self.assertEqual(code, 0)
        self.assertIn("terminal-launch-shortcut", out)
        self.assertIn("<Super>Return", out)
        self.assertIn("gapplication launch org.gnome.Console", out)
        for suffix in ("-name", "-command", "-binding", "-registration"):
            with self.subTest(setting=suffix):
                self.assertIn(f"terminal-launch-shortcut{suffix}", out)

    def test_it_says_where_the_command_came_from(self):
        _, out, _ = self.shortcuts("--preset", "kedra")
        self.assertIn("derived from application role 'terminal'", out)

    def test_it_labels_the_registration_as_a_membership(self):
        _, out, _ = self.shortcuts("--preset", "kedra")
        self.assertIn("membership", out)

    def test_vanilla_has_the_declaration_but_no_effective_shortcut(self):
        code, out, _ = self.shortcuts("--preset", "vanilla")
        self.assertEqual(code, 0)
        declared, effective = out.split("effective:")
        self.assertIn("terminal-launch-shortcut", declared)
        self.assertIn("(none)", effective)
        self.assertNotIn("<Super>Return", effective)

    def test_json_output_is_structured(self):
        code, out, _ = self.shortcuts("--preset", "kedra", "--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload["effective"]), 1)
        shortcut = payload["effective"][0]
        self.assertEqual(shortcut["command_role"], "terminal")
        self.assertEqual(len(shortcut["settings"]), 4)
        members = [s["member"] for s in shortcut["settings"] if s["member"]]
        self.assertEqual(members, [shortcut["path"]])

    def test_it_accepts_a_state_file(self):
        state_path = self.temp_dir() / "state.toml"
        state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
        ).save(state_path)
        code, out, _ = self.shortcuts("--state", str(state_path))
        self.assertEqual(code, 0)
        self.assertIn("(none)", out.split("effective:")[1])

    def test_it_performs_no_backend_operation(self):
        """Structural: the command has no adapter and cannot acquire one."""
        adapter = cli.InMemoryAdapter()
        self.shortcuts("--preset", "kedra")
        self.assertEqual(adapter.operations, [])


BLACKBOX_FIXTURE = """observed_schema_version = 1

[[observed]]
target = "gsettings:org.gnome.settings-daemon.plugins.media-keys:custom-keybindings"
type = "as"
data = ["/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"]

[[observed]]
target = "gsettings:org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/:name"
type = "s"
data = "blackbox"

[[observed]]
target = "gsettings:org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/:binding"
type = "s"
data = "<Super>Return"

[[observed]]
target = "gsettings:org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/:command"
type = "s"
data = "flatpak run com.raggesilver.BlackBox"
"""


class TestShortcutConflictsCommand(CliTestCase):
    """Offline conflict reporting, against a fixture rather than a session."""

    def setUp(self):
        self.dir = self.temp_dir()
        self.state_path = self.dir / "state.toml"
        state.UserState(base_preset="kedra").save(self.state_path)

    def conflicts(self, *args):
        return self.run_cli(
            ["--data-dir", str(DATA_DIR), "shortcut-conflicts", str(self.state_path), *args]
        )

    def fixture(self, text=BLACKBOX_FIXTURE):
        path = self.dir / "observed.toml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_a_clean_system_reports_clear(self):
        code, out, _ = self.conflicts()
        self.assertEqual(code, 0)
        self.assertIn("clear", out)
        self.assertIn("backend writes during discovery: 0", out)

    @unittest.skipUnless(
        __import__("kedra_experience.accelerators", fromlist=["x"]).available(),
        "GTK required",
    )
    def test_the_black_box_conflict_is_reported_and_exits_nonzero(self):
        code, out, _ = self.conflicts(self.fixture())
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)
        self.assertIn("blackbox", out)
        self.assertIn("custom1", out)
        self.assertIn("will not alter that shortcut", out)

    @unittest.skipUnless(
        __import__("kedra_experience.accelerators", fromlist=["x"]).available(),
        "GTK required",
    )
    def test_json_output_carries_the_structured_finding(self):
        code, out, _ = self.conflicts(self.fixture(), "--format", "json")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        report = payload["conflicts"]["terminal-launch-shortcut"]
        self.assertTrue(report["blocked"])
        self.assertEqual(report["findings"][0]["type"], "external")
        self.assertEqual(report["findings"][0]["name"], "blackbox")
        self.assertEqual(payload["backend_writes"], [])

    @unittest.skipUnless(
        __import__("kedra_experience.accelerators", fromlist=["x"]).available(),
        "GTK required",
    )
    def test_the_plan_command_surfaces_the_conflict(self):
        code, out, _ = self.run_cli(
            ["--data-dir", str(DATA_DIR), "plan", str(self.state_path), self.fixture()]
        )
        self.assertEqual(code, 0)
        self.assertIn("blocked by conflict", out)
        self.assertIn("blocked-by-conflict", out)

    @unittest.skipUnless(
        __import__("kedra_experience.accelerators", fromlist=["x"]).available(),
        "GTK required",
    )
    def test_execute_writes_nothing_under_a_conflict(self):
        code, out, _ = self.run_cli(
            ["--data-dir", str(DATA_DIR), "execute", str(self.state_path), self.fixture()]
        )
        self.assertEqual(code, 0)
        self.assertIn("(0 wrote, 0 failed)", out)
        self.assertIn("state written: False", out)
        # The user's shortcut is still exactly as the fixture described it.
        self.assertIn("flatpak run com.raggesilver.BlackBox", out)
        self.assertNotIn("kedra-terminal", out.split("simulated backend:")[1])


class TestApplicationsCommand(CliTestCase):
    def test_it_shows_the_derived_launch_command(self):
        code, out, _ = self.run_cli(
            ["--data-dir", str(DATA_DIR), "applications", "--preset", "kedra"]
        )
        self.assertEqual(code, 0)
        self.assertIn("gapplication launch org.gnome.Console", out)

    def test_vanilla_has_no_launch_command(self):
        code, out, _ = self.run_cli(
            ["--data-dir", str(DATA_DIR), "applications", "--preset", "vanilla"]
        )
        self.assertEqual(code, 0)
        self.assertIn("terminal launch command: (none", out)

    def test_json_carries_the_launch_command(self):
        _, out, _ = self.run_cli(
            ["--data-dir", str(DATA_DIR), "applications", "--preset", "kedra",
             "--format", "json"]
        )
        payload = json.loads(out)
        self.assertEqual(
            payload["effective"][0]["launch_command"],
            "gapplication launch org.gnome.Console",
        )


class TestStatePathCommand(CliTestCase):
    def test_prints_a_path_under_kedra(self):
        code, out, _ = self.run_cli(["state-path"])
        self.assertEqual(code, 0)
        printed = out.strip()
        self.assertTrue(printed.endswith("kedra/experience-state.toml"))
        self.assertNotIn("/usr/share", printed)

    def test_does_not_create_the_path(self):
        _, out, _ = self.run_cli(["state-path"])
        # Purely a computation; the CLI must not have created anything.
        self.assertFalse(Path(out.strip()).exists() and Path(out.strip()).is_dir())


class TestCliSurface(CliTestCase):
    def test_subcommand_is_required(self):
        code, _, _ = self.run_cli([])
        self.assertNotEqual(code, 0)

    def test_help_marks_the_tool_as_internal(self):
        parser = cli.build_parser()
        self.assertIn("Not a user-facing profile manager", parser.description)

    def test_command_set_is_deliberately_small(self):
        """A guard against this growing into a profile manager by accident."""
        subparsers = [
            action
            for action in cli.build_parser()._subparsers._group_actions
            if hasattr(action, "choices")
        ][0]
        self.assertEqual(
            set(subparsers.choices),
            {
                "validate-manifests",
                "resolve",
                "validate-state",
                "state-path",
                "plan",
                "execute",
                "vendor-defaults",
                "packages",
                "applications",
                "shortcuts",
                "shortcut-conflicts",
                # Reads one module's declared version so a generated package's
                # check() can assert its own pkgver still matches. A query, and
                # the smallest one that makes the version rule mechanical.
                "module-version",
            },
        )

    def test_no_subcommand_applies_to_a_live_desktop(self):
        """A real GSettings adapter exists; no CLI path may reach one.

        `execute` runs against the in-memory fake and always has. Adding a live
        apply command is a product decision, not a refactor -- so the CLI is
        asserted never to construct the production adapter.
        """
        import ast

        source = (
            Path(cli.__file__).read_text(encoding="utf-8")
            if hasattr(cli, "__file__")
            else ""
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn("gsettings", node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("gsettings", alias.name)
        self.assertNotIn("GSettingsAdapter", source)


if __name__ == "__main__":
    unittest.main()
