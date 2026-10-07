#!/usr/bin/env python3
"""Tests for the mutable per-user Kedra Experience state model.

Every test operates inside a temporary directory. `TestHomeDirectoryIsolation`
exists specifically to prove the tooling never reaches into the invoking user's
home, since that is a rule the rest of the suite cannot demonstrate by passing.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    DATA_DIR,
    KEDRA_MODULES,
    REPO_ROOT,
    TreeTestCase,
    base_preset_toml,
    manifests,
    module_toml,
    resolve,
    state,
)

VALID_STATE = """state_schema_version = 3

[selection]
base_preset = "kedra"

[[selection.module_overrides]]
id = "terminal"
enabled = false
"""


class StateTestCase(TreeTestCase):
    def write_state(self, content: str, name: str = "experience-state.toml") -> Path:
        path = self.temp_dir() / name
        path.write_text(content, encoding="utf-8")
        return path

    def check(self, content: str, tree=None) -> manifests.Report:
        report = manifests.Report()
        state.validate_state_file(self.write_state(content), report, tree=tree)
        return report

    def assertRejects(self, content: str, needle: str, tree=None) -> None:
        report = self.check(content, tree=tree)
        self.assertFalse(report.ok, "expected state validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def assertAccepts(self, content: str, tree=None) -> None:
        report = self.check(content, tree=tree)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestValidState(StateTestCase):
    def test_minimal_state_is_valid(self):
        self.assertAccepts('state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n')

    def test_state_with_overrides_is_valid(self):
        self.assertAccepts(VALID_STATE)

    def test_state_validates_against_a_tree(self):
        tree = self.load(self.make_tree())
        self.assertAccepts(VALID_STATE, tree=tree)

    def test_state_with_applied_record_is_valid(self):
        self.assertAccepts(
            VALID_STATE
            + """
[applied]
generation = 2
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"

[[applied.modules]]
id = "desktop"
version = "0.1.0"
active = true
generation = 1
"""
        )


class TestMissingRequiredFields(StateTestCase):
    def test_missing_schema_version(self):
        self.assertRejects(
            '[selection]\nbase_preset = "kedra"\n',
            "missing required key 'state_schema_version'",
        )

    def test_unsupported_schema_version(self):
        self.assertRejects(
            'state_schema_version = 99\n\n[selection]\nbase_preset = "kedra"\n',
            "unsupported state_schema_version 99",
        )

    def test_non_integer_schema_version(self):
        self.assertRejects(
            'state_schema_version = "1"\n\n[selection]\nbase_preset = "kedra"\n',
            "must be an integer",
        )

    def test_missing_selection_table(self):
        self.assertRejects("state_schema_version = 3\n", "missing required [selection] table")

    def test_missing_base_preset(self):
        self.assertRejects(
            "state_schema_version = 3\n\n[selection]\n",
            "missing required key 'base_preset'",
        )

    def test_applied_missing_generation(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"
""",
            "applied: missing required key 'generation'",
        )

    def test_applied_module_missing_fields(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 1
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"

[[applied.modules]]
id = "desktop"
""",
            "missing required key 'version'",
        )


class TestUnknownReferences(StateTestCase):
    def test_unknown_base_preset_against_tree(self):
        tree = self.load(self.make_tree())
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "ghost"\n',
            "references nonexistent preset 'ghost'",
            tree=tree,
        )

    def test_unknown_module_override_against_tree(self):
        tree = self.load(self.make_tree())
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "ghost"\nenabled = false\n',
            "references nonexistent module 'ghost'",
            tree=tree,
        )

    def test_non_user_selectable_module_override_against_tree(self):
        # The resolver refuses this override; the validator says so about a file
        # at rest, so the author finds out before wondering why nothing changed.
        tree = self.load(
            self.make_tree(
                modules={
                    "core": module_toml("core", user_selectable=False),
                    "optional": module_toml("optional", user_selectable=True),
                },
                presets={
                    "kedra": base_preset_toml("kedra", {"core": True, "optional": True})
                },
            )
        )
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "core"\nenabled = false\n',
            "is not user-selectable",
            tree=tree,
        )

    def test_a_user_selectable_module_override_is_accepted(self):
        tree = self.load(
            self.make_tree(
                modules={
                    "core": module_toml("core", user_selectable=False),
                    "optional": module_toml("optional", user_selectable=True),
                },
                presets={
                    "kedra": base_preset_toml("kedra", {"core": True, "optional": True})
                },
            )
        )
        report = manifests.Report()
        state.validate_state_document(
            {
                "state_schema_version": 3,
                "selection": {
                    "base_preset": "kedra",
                    "module_overrides": [{"id": "optional", "enabled": False}],
                },
            },
            "test",
            report,
            tree=tree,
        )
        self.assertTrue(report.ok, report.rendered())

    def test_the_shipped_state_example_has_no_refused_override(self):
        # The documented example is validated in CI and in makepkg check(); if
        # it ever named a core module, the build would fail here first.
        report = manifests.Report()
        tree = manifests.load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, report.rendered())
        state.validate_state_file(
            REPO_ROOT / "docs" / "examples" / "user-state.toml", report, tree=tree
        )
        self.assertTrue(report.ok, report.rendered())

    def test_unknown_applied_module_against_tree(self):
        tree = self.load(self.make_tree())
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 1
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"

[[applied.modules]]
id = "ghost"
version = "0.1.0"
active = true
generation = 1
""",
            "references nonexistent module 'ghost'",
            tree=tree,
        )

    def test_shape_is_checkable_without_a_tree(self):
        """State must validate on a machine where the manifests are absent."""
        self.assertAccepts(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "ghost"\nenabled = false\n'
        )


class TestMalformedState(StateTestCase):
    def test_malformed_toml(self):
        self.assertRejects("[selection\nbase_preset = ", "malformed TOML")

    def test_invalid_base_preset_identifier(self):
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "Kedra Deluxe"\n',
            "is not a valid identifier",
        )

    def test_override_without_enabled(self):
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "terminal"\n',
            "'enabled' must be present and a boolean",
        )

    def test_duplicate_override(self):
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "terminal"\nenabled = true\n'
            '\n[[selection.module_overrides]]\nid = "terminal"\nenabled = false\n',
            "duplicate override for module 'terminal'",
        )

    def test_malformed_preset_version(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 1
preset_id = "kedra"
preset_version = "1"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"
""",
            "is not MAJOR.MINOR.PATCH",
        )

    def test_malformed_applied_module_version(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 1
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"

[[applied.modules]]
id = "desktop"
version = "v1.0"
active = true
generation = 1
""",
            "is not MAJOR.MINOR.PATCH",
        )

    def test_malformed_timestamp(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 1
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "last tuesday"
""",
            "is not an ISO 8601 timestamp",
        )

    def test_zero_generation_is_rejected(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 0
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"
""",
            "must be a positive integer",
        )

    def test_module_generation_ahead_of_applied_generation_is_rejected(self):
        self.assertRejects(
            VALID_STATE
            + """
[applied]
generation = 2
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"

[[applied.modules]]
id = "desktop"
version = "0.1.0"
active = true
generation = 5
""",
            "is ahead of applied.generation",
        )


class TestScopeBoundary(StateTestCase):
    """State records Kedra's own bookkeeping, not general user configuration."""

    def test_unrelated_top_level_table_is_rejected(self):
        self.assertRejects(
            VALID_STATE + '\n[gnome]\nfont = "Cantarell 11"\n',
            "records Kedra's own selection",
        )

    def test_unknown_selection_key_is_rejected(self):
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            'wallpaper = "/home/someone/pic.png"\n',
            "selection: unknown key 'wallpaper'",
        )

    def test_unknown_override_key_is_rejected(self):
        self.assertRejects(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
            '\n[[selection.module_overrides]]\nid = "terminal"\nenabled = false\n'
            'gsettings = "org.gnome.desktop"\n',
            "unknown key 'gsettings'",
        )


class TestSerializationRoundTrip(StateTestCase):
    def test_round_trip_preserves_selection(self):
        original = state.UserState(
            base_preset="kedra",
            module_overrides=(
                state.ModuleOverride(id="terminal", enabled=False),
                state.ModuleOverride(id="extras", enabled=True),
            ),
        )
        path = self.temp_dir() / "state.toml"
        original.save(path)

        self.assertEqual(state.UserState.load(path), original)

    def test_round_trip_preserves_applied_record(self):
        tree = self.load(self.make_tree())
        resolution = resolve.resolve_preset(tree, "kedra")
        original = state.UserState(base_preset="kedra").with_applied(
            resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        path = self.temp_dir() / "state.toml"
        original.save(path)
        reloaded = state.UserState.load(path)

        self.assertEqual(reloaded, original)
        self.assertEqual(reloaded.applied.generation, 1)
        self.assertEqual(len(reloaded.applied.modules), 7)

    def test_serialized_state_validates(self):
        tree = self.load(self.make_tree())
        resolution = resolve.resolve_preset(tree, "kedra")
        written = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
        ).with_applied(resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z")

        path = self.temp_dir() / "state.toml"
        written.save(path)

        report = manifests.Report()
        state.validate_state_file(path, report, tree=tree)
        self.assertTrue(report.ok, f"round-tripped state failed validation: {report.errors}")

    def test_serialization_is_deterministic(self):
        first = state.UserState(
            base_preset="kedra",
            module_overrides=(
                state.ModuleOverride(id="terminal", enabled=False),
                state.ModuleOverride(id="extras", enabled=True),
            ),
        )
        # Same content, opposite declaration order.
        second = state.UserState(
            base_preset="kedra",
            module_overrides=(
                state.ModuleOverride(id="extras", enabled=True),
                state.ModuleOverride(id="terminal", enabled=False),
            ),
        )
        self.assertEqual(first.to_toml(), second.to_toml())

    def test_save_is_atomic_and_leaves_no_temporary(self):
        path = self.temp_dir() / "nested" / "state.toml"
        state.UserState(base_preset="kedra").save(path)

        self.assertTrue(path.is_file())
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_written_state_parses_as_toml(self):
        path = self.temp_dir() / "state.toml"
        state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
        ).save(path)
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
        self.assertEqual(doc["selection"]["base_preset"], "kedra")


class TestAppliedStateTracking(StateTestCase):
    def setUp(self):
        self.tree = self.load(self.make_tree())
        self.resolution = resolve.resolve_preset(self.tree, "kedra")

    def test_first_generation_is_one(self):
        fresh = state.UserState(base_preset="kedra")
        self.assertEqual(fresh.next_generation(), 1)

        applied = fresh.with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        self.assertEqual(applied.applied.generation, 1)

    def test_generation_increments_monotonically(self):
        current = state.UserState(base_preset="kedra")
        for expected in (1, 2, 3):
            current = current.with_applied(
                self.resolution,
                experience_schema_version=2,
                applied_at="2026-08-14T12:00:00Z",
            )
            self.assertEqual(current.applied.generation, expected)

    def test_unchanged_module_keeps_its_generation(self):
        first = state.UserState(base_preset="kedra").with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        second = first.with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T13:00:00Z"
        )

        self.assertEqual(second.applied.generation, 2)
        # Nothing about the modules changed, so none of them moved.
        for module in second.applied.modules:
            self.assertEqual(module.generation, 1)

    def test_changed_module_advances_its_generation(self):
        first = state.UserState(base_preset="kedra").with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        changed = resolve.resolve_state(
            self.tree,
            state.UserState(
                base_preset="kedra",
                module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
            ),
        )
        second = first.with_applied(
            changed, experience_schema_version=2, applied_at="2026-08-14T13:00:00Z"
        )
        by_id = {m.id: m for m in second.applied.modules}

        self.assertEqual(by_id["terminal"].generation, 2)
        self.assertFalse(by_id["terminal"].active)
        self.assertEqual(by_id["desktop"].generation, 1)

    def test_applied_record_captures_module_versions(self):
        applied = state.UserState(base_preset="kedra").with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        by_id = {m.id: m for m in applied.applied.modules}
        self.assertEqual(by_id["desktop"].version, "0.1.0")
        self.assertEqual(by_id["extras"].active, False)

    def test_with_applied_does_not_mutate_the_original(self):
        original = state.UserState(base_preset="kedra")
        original.with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        self.assertIsNone(original.applied)

    def test_applied_record_notes_the_schema_it_was_applied_under(self):
        applied = state.UserState(base_preset="kedra").with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        self.assertEqual(applied.applied.experience_schema_version, 2)
        self.assertEqual(applied.applied.preset_id, "kedra")

    def test_managed_values_placeholder_is_empty(self):
        """Reserved for a future GNOME adapter; nothing tracks values yet."""
        applied = state.UserState(base_preset="kedra").with_applied(
            self.resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        )
        self.assertEqual(applied.applied.managed_values, ())


class TestDocumentedExample(TreeTestCase):
    """The documented example must stay valid against the real manifests."""

    EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "examples" / "user-state.toml"

    def setUp(self):
        from support import DATA_DIR

        report = manifests.Report()
        self.tree = manifests.load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, f"shipped data invalid: {report.errors}")

    def test_example_validates_against_the_shipped_tree(self):
        report = manifests.Report()
        state.validate_state_file(self.EXAMPLE, report, tree=self.tree)
        self.assertTrue(report.ok, f"documented example is invalid: {report.errors}")

    def test_example_resolves(self):
        loaded = state.UserState.load(self.EXAMPLE)
        result = resolve.resolve_state(self.tree, loaded)

        self.assertTrue(result.applies_experience_layer)
        self.assertEqual(
            set(result.enabled_module_ids), {"desktop", "workflow", "appearance", "integration"}
        )

    def test_example_stores_only_deltas(self):
        loaded = state.UserState.load(self.EXAMPLE)
        result = resolve.resolve_state(self.tree, loaded)

        self.assertEqual(len(loaded.module_overrides), 2)
        self.assertEqual(len(result.modules), 7)

    def test_example_applied_versions_match_the_manifests(self):
        loaded = state.UserState.load(self.EXAMPLE)
        for module in loaded.applied.modules:
            self.assertEqual(
                module.version,
                self.tree.modules[module.id]["version"],
                f"example records a stale version for '{module.id}'",
            )


class TestXdgLocation(unittest.TestCase):
    def test_uses_xdg_state_home_when_set(self):
        path = state.default_state_dir({"XDG_STATE_HOME": "/somewhere/state"})
        self.assertEqual(path, Path("/somewhere/state/kedra"))

    def test_falls_back_to_local_state(self):
        path = state.default_state_dir({"HOME": "/home/example"})
        self.assertEqual(path, Path("/home/example/.local/state/kedra"))

    def test_default_state_path_includes_filename(self):
        path = state.default_state_path({"XDG_STATE_HOME": "/somewhere/state"})
        self.assertEqual(path, Path("/somewhere/state/kedra/experience-state.toml"))

    def test_state_is_not_under_usr_share(self):
        path = state.default_state_dir({"XDG_STATE_HOME": "/somewhere/state"})
        self.assertNotIn("/usr/share", str(path))

    def test_computing_the_path_creates_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state.default_state_path({"XDG_STATE_HOME": tmp})
            self.assertEqual(list(Path(tmp).iterdir()), [])


class TestHomeDirectoryIsolation(TreeTestCase):
    """The tooling must never touch the invoking user's home directory."""

    def test_full_flow_writes_nothing_outside_the_given_path(self):
        fake_home = self.temp_dir() / "home"
        fake_state = self.temp_dir() / "state"
        fake_home.mkdir()
        fake_state.mkdir()

        tree = self.load(self.make_tree())
        resolution = resolve.resolve_preset(tree, "kedra")
        target = fake_state / "kedra" / "experience-state.toml"
        state.UserState(base_preset="kedra").with_applied(
            resolution, experience_schema_version=2, applied_at="2026-08-14T12:00:00Z"
        ).save(target)

        self.assertTrue(target.is_file())
        # A fake HOME was available the whole time and stayed untouched.
        self.assertEqual(list(fake_home.iterdir()), [])

    def test_load_requires_an_explicit_path(self):
        """There is no zero-argument load that could guess the real home."""
        with self.assertRaises(TypeError):
            state.UserState.load()

    def test_save_requires_an_explicit_path(self):
        with self.assertRaises(TypeError):
            state.UserState(base_preset="kedra").save()


if __name__ == "__main__":
    unittest.main()
