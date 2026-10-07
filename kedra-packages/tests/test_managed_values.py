#!/usr/bin/env python3
"""Tests for managed-value ownership records in per-user state.

All state lives in temporary directories. Nothing reads dconf, GSettings, or
the invoking user's real state file.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    KEDRA_MODULES,
    TreeTestCase,
    base_preset_toml,
    gsettings_toml,
    manifests,
    module_toml,
    state,
    typed,
)

X = typed("as", ["<Super>x"])
O = typed("as", ["<Super>o"])
TARGET_A = "gsettings:org.example.test:key-a"

HEADER = """state_schema_version = 3

[selection]
base_preset = "kedra"

[applied]
generation = 2
preset_id = "kedra"
preset_version = "0.1.0"
experience_schema_version = 3
applied_at = "2026-08-14T12:00:00Z"
"""

VALID_RECORD = """
[[applied.managed_values]]
setting_id = "example-a"
backend = "gsettings"
target = "gsettings:org.example.test:key-a"
module_id = "workflow"
module_version = "0.2.0"
policy = "managed"
generation = 2
last_applied = { type = "as", data = ["<Super>x"] }
original = { present = true, type = "as", data = ["<Super>o"] }
"""


class ManagedValueTestCase(TreeTestCase):
    def tree_with_setting(self, setting_id="example-a", key="key-a"):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow", settings=gsettings_toml(setting_id, key=key)
        )
        return self.load(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def check(self, content: str, tree=None) -> manifests.Report:
        path = self.temp_dir() / "experience-state.toml"
        path.write_text(content, encoding="utf-8")
        report = manifests.Report()
        state.validate_state_file(path, report, tree=tree)
        return report

    def assertRejects(self, content: str, needle: str, tree=None) -> None:
        report = self.check(content, tree=tree)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def assertAccepts(self, content: str, tree=None) -> None:
        report = self.check(content, tree=tree)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestValidManagedRecords(ManagedValueTestCase):
    def test_valid_record(self):
        self.assertAccepts(HEADER + VALID_RECORD)

    def test_valid_record_against_a_tree(self):
        self.assertAccepts(HEADER + VALID_RECORD, tree=self.tree_with_setting())

    def test_record_with_absent_original(self):
        self.assertAccepts(
            HEADER
            + VALID_RECORD.replace(
                'original = { present = true, type = "as", data = ["<Super>o"] }',
                "original = { present = false }",
            )
        )

    def test_empty_managed_values_is_valid(self):
        self.assertAccepts(HEADER)


class TestMissingAndUnknownKeys(ManagedValueTestCase):
    def test_missing_required_keys_are_rejected(self):
        for key in (
            "setting_id", "backend", "target", "module_id",
            "module_version", "policy", "generation", "last_applied", "original",
        ):
            with self.subTest(key=key):
                record = "\n".join(
                    line for line in VALID_RECORD.splitlines()
                    if not line.startswith(f"{key} =")
                )
                self.assertRejects(HEADER + record, f"missing required key '{key}'")

    def test_unknown_key_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD + 'gsettings_dump = "everything"\n',
            "unknown key 'gsettings_dump'",
        )

    def test_no_place_to_store_unrelated_configuration(self):
        """The record shape is closed: a broad snapshot has nowhere to go."""
        self.assertEqual(
            state.MANAGED_VALUE_KNOWN_KEYS,
            {
                "resource",
                "member",
                "setting_id", "backend", "target", "module_id", "module_version",
                "policy", "generation", "last_applied", "original",
            },
        )


class TestMalformedRecords(ManagedValueTestCase):
    def test_malformed_last_applied_value(self):
        self.assertRejects(
            HEADER
            + VALID_RECORD.replace(
                'last_applied = { type = "as", data = ["<Super>x"] }',
                'last_applied = { type = "as", data = "not-an-array" }',
            ),
            "last_applied",
        )

    def test_unknown_last_applied_type(self):
        self.assertRejects(
            HEADER
            + VALID_RECORD.replace('type = "as", data = ["<Super>x"]', 'type = "zz", data = []'),
            "unknown value type 'zz'",
        )

    def test_malformed_original_value(self):
        self.assertRejects(
            HEADER
            + VALID_RECORD.replace(
                'original = { present = true, type = "as", data = ["<Super>o"] }',
                'original = { present = true, type = "i", data = "nope" }',
            ),
            "original",
        )

    def test_original_without_present_flag(self):
        self.assertRejects(
            HEADER
            + VALID_RECORD.replace(
                'original = { present = true, type = "as", data = ["<Super>o"] }',
                'original = { type = "as", data = ["<Super>o"] }',
            ),
            "'present' must be a boolean",
        )

    def test_absent_original_carrying_a_value_is_rejected(self):
        self.assertRejects(
            HEADER
            + VALID_RECORD.replace(
                'original = { present = true, type = "as", data = ["<Super>o"] }',
                'original = { present = false, type = "as", data = ["<Super>o"] }',
            ),
            "meaningless when present = false",
        )

    def test_invalid_backend_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('backend = "gsettings"', 'backend = "shell"'),
            "unknown backend 'shell'",
        )

    def test_invalid_policy_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('policy = "managed"', 'policy = "enforced"'),
            "invalid policy 'enforced'",
        )

    def test_invalid_setting_id_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('setting_id = "example-a"', 'setting_id = "Bad ID"'),
            "is not a valid setting ID",
        )

    def test_malformed_module_version_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('module_version = "0.2.0"', 'module_version = "2"'),
            "is not MAJOR.MINOR.PATCH",
        )

    def test_empty_target_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace(
                'target = "gsettings:org.example.test:key-a"', 'target = ""'
            ),
            "non-empty canonical target string",
        )


class TestReferencesAndUniqueness(ManagedValueTestCase):
    def test_unknown_setting_reference_against_a_tree(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('setting_id = "example-a"', 'setting_id = "example-ghost"'),
            "references nonexistent setting 'example-ghost'",
            tree=self.tree_with_setting(),
        )

    def test_unknown_module_reference_against_a_tree(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace('module_id = "workflow"', 'module_id = "ghost"'),
            "references nonexistent module 'ghost'",
            tree=self.tree_with_setting(),
        )

    def test_shape_is_checkable_without_a_tree(self):
        """State stays validatable where the manifests are not installed."""
        self.assertAccepts(
            HEADER + VALID_RECORD.replace('setting_id = "example-a"', 'setting_id = "example-ghost"')
        )

    def test_duplicate_setting_record_is_rejected(self):
        self.assertRejects(HEADER + VALID_RECORD + VALID_RECORD, "duplicate record for setting")

    def test_duplicate_target_across_records_is_rejected(self):
        second = VALID_RECORD.replace('setting_id = "example-a"', 'setting_id = "example-b"')
        self.assertRejects(HEADER + VALID_RECORD + second, "is already claimed by setting")


class TestGenerationTracking(ManagedValueTestCase):
    def test_generation_ahead_of_applied_generation_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace("generation = 2\nlast_applied", "generation = 9\nlast_applied"),
            "is ahead of applied.generation",
        )

    def test_zero_generation_is_rejected(self):
        self.assertRejects(
            HEADER + VALID_RECORD.replace("generation = 2\nlast_applied", "generation = 0\nlast_applied"),
            "must be a positive integer",
        )

    def test_older_generation_is_allowed(self):
        """A value untouched by recent applies keeps its older generation."""
        self.assertAccepts(
            HEADER + VALID_RECORD.replace("generation = 2\nlast_applied", "generation = 1\nlast_applied")
        )


class TestRoundTrip(ManagedValueTestCase):
    def managed(self, **kwargs):
        defaults = dict(
            setting_id="example-a",
            backend="gsettings",
            target=TARGET_A,
            module_id="workflow",
            module_version="0.2.0",
            policy="managed",
            generation=2,
            last_applied=X,
            original=O,
            original_present=True,
        )
        defaults.update(kwargs)
        return state.ManagedValue(**defaults)

    def with_applied(self, *records):
        return state.UserState(
            base_preset="kedra",
            applied=state.AppliedRecord(
                generation=2,
                preset_id="kedra",
                preset_version="0.1.0",
                experience_schema_version=3,
                applied_at="2026-08-14T12:00:00Z",
                managed_values=tuple(records),
            ),
        )

    def test_round_trip_preserves_a_managed_record(self):
        original = self.with_applied(self.managed())
        path = self.temp_dir() / "state.toml"
        original.save(path)

        self.assertEqual(state.UserState.load(path), original)

    def test_round_trip_preserves_absent_original(self):
        original = self.with_applied(self.managed(original=None, original_present=False))
        path = self.temp_dir() / "state.toml"
        original.save(path)
        reloaded = state.UserState.load(path)

        self.assertEqual(reloaded, original)
        self.assertFalse(reloaded.applied.managed_values[0].original_present)
        self.assertIsNone(reloaded.applied.managed_values[0].original)

    def test_round_trip_across_value_types(self):
        cases = [
            typed("b", True),
            typed("i", 7),
            typed("d", 1.5),
            typed("s", "hello"),
            typed("as", ["a", "b"]),
            typed("ai", [1, 2, 3]),
        ]
        for index, value in enumerate(cases):
            with self.subTest(type=value.type):
                original = self.with_applied(
                    self.managed(setting_id=f"example-{index}", last_applied=value)
                )
                path = self.temp_dir() / f"state-{index}.toml"
                original.save(path)
                self.assertEqual(state.UserState.load(path), original)

    def test_serialized_records_validate(self):
        tree = self.tree_with_setting()
        written = self.with_applied(self.managed())
        path = self.temp_dir() / "state.toml"
        written.save(path)

        report = manifests.Report()
        state.validate_state_file(path, report, tree=tree)
        self.assertTrue(report.ok, f"round-tripped state failed validation: {report.errors}")

    def test_records_are_sorted_deterministically(self):
        first = self.with_applied(
            self.managed(setting_id="example-b", target=TARGET_A + "b"),
            self.managed(setting_id="example-a"),
        )
        second = self.with_applied(
            self.managed(setting_id="example-a"),
            self.managed(setting_id="example-b", target=TARGET_A + "b"),
        )
        self.assertEqual(first.to_toml(), second.to_toml())

    def test_written_state_parses_as_toml(self):
        path = self.temp_dir() / "state.toml"
        self.with_applied(self.managed()).save(path)
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
        record = doc["applied"]["managed_values"][0]

        self.assertEqual(record["setting_id"], "example-a")
        self.assertEqual(record["last_applied"], {"type": "as", "data": ["<Super>x"]})
        self.assertIs(record["original"]["present"], True)

    def test_managed_lookup_helper(self):
        applied = self.with_applied(self.managed()).applied
        self.assertIsNotNone(applied.managed("example-a"))
        self.assertIsNone(applied.managed("example-missing"))


class TestNoBroadSnapshot(ManagedValueTestCase):
    """Only explicitly declared targets are ever recorded."""

    def test_records_are_per_setting_not_per_schema(self):
        record = state.ManagedValue(
            setting_id="example-a",
            backend="gsettings",
            target=TARGET_A,
            module_id="workflow",
            module_version="0.2.0",
            policy="managed",
            generation=1,
            last_applied=X,
        )
        # A single concrete target, not a schema, a path prefix or a glob.
        self.assertEqual(record.target.count(":"), 2)
        self.assertNotIn("*", record.target)

    def test_current_observed_is_not_persisted(self):
        """Documented choice: read live, never store a stale copy."""
        self.assertNotIn("current_observed", state.MANAGED_VALUE_KNOWN_KEYS)
        self.assertFalse(
            hasattr(
                state.ManagedValue(
                    setting_id="a", backend="gsettings", target=TARGET_A,
                    module_id="workflow", module_version="0.1.0", policy="managed",
                    generation=1, last_applied=X,
                ),
                "current_observed",
            )
        )


if __name__ == "__main__":
    unittest.main()
