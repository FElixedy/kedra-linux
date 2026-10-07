#!/usr/bin/env python3
"""Tests for setting declarations, typed values and migration records.

Every fixture is synthetic: schema IDs are under org.example.*, and no test
declares a value any real Kedra module ships. Nothing here reads or writes
GSettings, dconf, or any part of the invoking user's session.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    DATA_DIR,
    KEDRA_MODULES,
    TreeTestCase,
    backends,
    base_preset_toml,
    gsettings_toml,
    manifests,
    module_toml,
    settings,
    typed,
    values,
)

ValueError_ = values.ValueError_


class SettingsTreeTestCase(TreeTestCase):
    def tree_with_settings(self, *blocks: str, module: str = "workflow"):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules[module] = module_toml(module, settings="".join(blocks))
        return self.load_dirty(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def assertRejects(self, *blocks: str, needle: str) -> None:
        _, report = self.tree_with_settings(*blocks)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def assertAccepts(self, *blocks: str) -> None:
        _, report = self.tree_with_settings(*blocks)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestValidDeclarations(SettingsTreeTestCase):
    def test_valid_gsettings_declaration(self):
        self.assertAccepts(gsettings_toml("example-close-window"))

    def test_both_policies_are_accepted(self):
        self.assertAccepts(gsettings_toml("example-a", key="key-a", policy="managed"))
        self.assertAccepts(gsettings_toml("example-b", key="key-b", policy="default"))

    def test_multiple_settings_in_one_module(self):
        self.assertAccepts(
            gsettings_toml("example-one", key="key-one"),
            gsettings_toml("example-two", key="key-two"),
        )

    def test_every_value_type_is_accepted(self):
        cases = {
            "b": True,
            "i": 42,
            "d": 1.5,
            "s": "hello",
            "as": ["a", "b"],
            "ai": [1, 2],
        }
        for index, (value_type, data) in enumerate(cases.items()):
            with self.subTest(type=value_type):
                self.assertAccepts(
                    gsettings_toml(
                        f"example-{index}", key=f"key-{index}",
                        value_type=value_type, data=data,
                    )
                )

    def test_declaration_exposes_canonical_target(self):
        tree, report = self.tree_with_settings(
            gsettings_toml("example-close-window", schema="org.example.wm", key="close")
        )
        self.assertTrue(report.ok, report.errors)
        entry = tree.modules["workflow"]["settings"][0]
        declaration = settings.build_declaration(entry, "workflow", "0.1.0")
        self.assertEqual(declaration.canonical_target, "gsettings:org.example.wm:close")
        self.assertTrue(declaration.creates_managed_record)

    def test_default_policy_creates_no_managed_record(self):
        tree, _ = self.tree_with_settings(
            gsettings_toml("example-default", policy="default")
        )
        declaration = settings.build_declaration(
            tree.modules["workflow"]["settings"][0], "workflow", "0.1.0"
        )
        self.assertFalse(declaration.creates_managed_record)


class TestUnknownAndMissingKeys(SettingsTreeTestCase):
    def test_unknown_key_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one") + 'nonsense = "x"\n',
            needle="unknown key 'nonsense'",
        )

    def test_missing_required_keys_are_rejected(self):
        for key in ("id", "backend", "policy", "target", "value"):
            with self.subTest(key=key):
                block = gsettings_toml("example-one")
                block = "\n".join(
                    line for line in block.splitlines() if not line.startswith(f"{key} =")
                )
                self.assertRejects(block, needle=f"missing required key '{key}'")

    def test_invalid_setting_id_is_rejected(self):
        self.assertRejects(
            gsettings_toml("Example Setting"), needle="is not a valid setting ID"
        )

    def test_no_command_backend_exists(self):
        """There is deliberately no way to ask a manifest to run something."""
        self.assertNotIn("command", backends.SPECS)
        self.assertNotIn("shell", backends.SPECS)
        self.assertNotIn("exec", backends.SPECS)
        self.assertEqual(set(backends.SPECS), {"gsettings"})


class TestInvalidBackend(SettingsTreeTestCase):
    def test_unknown_backend_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'backend = "gsettings"', 'backend = "dconf-raw"'
            ),
            needle="unknown backend 'dconf-raw'",
        )

    def test_shell_backend_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'backend = "gsettings"', 'backend = "shell"'
            ),
            needle="unknown backend 'shell'",
        )


class TestInvalidPolicy(SettingsTreeTestCase):
    def test_unknown_policy_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", policy="enforced"),
            needle="invalid policy 'enforced'",
        )

    def test_policy_set_is_small_and_stable(self):
        self.assertEqual(settings.SETTING_POLICIES, {"default", "managed"})


class TestMalformedTarget(SettingsTreeTestCase):
    def test_missing_schema_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'target = { schema = "org.example.test", key = "example-key" }',
                'target = { key = "example-key" }',
            ),
            needle="target is missing 'schema'",
        )

    def test_missing_key_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'target = { schema = "org.example.test", key = "example-key" }',
                'target = { schema = "org.example.test" }',
            ),
            needle="target is missing 'key'",
        )

    def test_malformed_schema_id_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", schema="notdotted"),
            needle="is not a valid GSettings schema ID",
        )

    def test_malformed_key_name_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", key="Not_A_Key"),
            needle="is not a valid GSettings key name",
        )

    def test_unknown_target_key_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'key = "example-key" }', 'key = "example-key", value_type = "s" }'
            ),
            needle="target has unknown key 'value_type'",
        )

    def test_a_path_on_a_fixed_schema_is_rejected(self):
        """`path` became a known key in schema 6, not a universally valid one.

        A fixed schema declares its own path, so a target that also supplies one
        either repeats it or contradicts it -- and if it contradicts it, GLib
        aborts the process rather than raising.
        """
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'key = "example-key" }', 'key = "example-key", path = "/x/" }'
            ),
            needle="only valid for a relocatable schema",
        )


class TestInvalidTypedValues(SettingsTreeTestCase):
    def test_unknown_value_type_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", value_type="mv", data=["x"]),
            needle="unknown value type 'mv'",
        )

    def test_type_and_data_mismatch_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", value_type="s", data=["x"]),
            needle="type 's' requires a string",
        )

    def test_array_element_type_is_checked(self):
        self.assertRejects(
            gsettings_toml("example-one", value_type="as", data=["ok", 5]),
            needle="type 'as' requires string elements",
        )

    def test_missing_value_data_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one").replace(
                'value = { type = "as", data = ["<Super>q"] }', 'value = { type = "as" }'
            ),
            needle="value is missing 'data'",
        )

    def test_out_of_range_integer_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-one", value_type="i", data=2**31),
            needle="out of 32-bit range",
        )

    def test_boolean_is_not_an_integer(self):
        self.assertEqual(values.TypedValue("i", True).problems()[:1][0][:24], "type 'i' requires an int")


class TestUniqueness(TreeTestCase):
    def two_module_tree(self, first: str, second: str):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml("workflow", settings=first)
        modules["desktop"] = module_toml("desktop", settings=second)
        return self.load_dirty(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def assertRejects(self, first: str, second: str, needle: str) -> None:
        _, report = self.two_module_tree(first, second)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def test_duplicate_setting_id_within_a_module(self):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow",
            settings=gsettings_toml("example-dup", key="key-a")
            + gsettings_toml("example-dup", key="key-b"),
        )
        _, report = self.load_dirty(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )
        self.assertFalse(report.ok)
        self.assertIn("duplicate setting ID", " | ".join(m for _, m in report.errors))

    def test_duplicate_setting_id_across_modules(self):
        self.assertRejects(
            gsettings_toml("example-dup", key="key-a"),
            gsettings_toml("example-dup", key="key-b"),
            needle="unique across the whole Experience tree",
        )

    def test_conflicting_duplicate_target_is_rejected(self):
        self.assertRejects(
            gsettings_toml("example-a", key="shared-key", data=["<Super>q"]),
            gsettings_toml("example-b", key="shared-key", data=["<Super>w"]),
            needle="with a different value",
        )

    def test_duplicate_target_with_same_value_is_also_rejected(self):
        """Two owners of one key is ambiguous even when they agree today."""
        self.assertRejects(
            gsettings_toml("example-a", key="shared-key", data=["<Super>q"]),
            gsettings_toml("example-b", key="shared-key", data=["<Super>q"]),
            needle="may have only one owner",
        )

    def test_distinct_targets_are_fine(self):
        _, report = self.two_module_tree(
            gsettings_toml("example-a", key="key-a"),
            gsettings_toml("example-b", key="key-b"),
        )
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestMigrationRecords(SettingsTreeTestCase):
    def tree_with_migration(self, migration: str, settings_block: str = ""):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow", settings=settings_block, migrations=migration
        )
        return self.load_dirty(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def assertMigrationRejects(self, migration: str, needle: str, settings_block: str = ""):
        _, report = self.tree_with_migration(migration, settings_block)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def assertMigrationAccepts(self, migration: str, settings_block: str = ""):
        _, report = self.tree_with_migration(migration, settings_block)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")

    def test_valid_rename_record(self):
        self.assertMigrationAccepts(
            """
[[module.migrations]]
type = "rename-setting"
module_version = "0.2.0"
from_id = "example-old-name"
to_id = "example-new-name"
""",
            gsettings_toml("example-new-name"),
        )

    def test_valid_remove_record(self):
        self.assertMigrationAccepts(
            """
[[module.migrations]]
type = "remove-setting"
module_version = "0.2.0"
setting_id = "example-withdrawn"
on_managed = "revert"
"""
        )

    def test_valid_retarget_record(self):
        self.assertMigrationAccepts(
            """
[[module.migrations]]
type = "retarget-setting"
module_version = "0.2.0"
setting_id = "example-moved"
from_target = "gsettings:org.example.old:example-key"
""",
            gsettings_toml("example-moved"),
        )

    def test_valid_change_default_record(self):
        self.assertMigrationAccepts(
            """
[[module.migrations]]
type = "change-default"
module_version = "0.2.0"
setting_id = "example-tweaked"
reason = "New recommendation."
""",
            gsettings_toml("example-tweaked"),
        )

    def test_invalid_migration_type_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "reticulate-splines"
module_version = "0.2.0"
""",
            "invalid migration type 'reticulate-splines'",
        )

    def test_script_migrations_are_not_expressible(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "script"
module_version = "0.2.0"
command = "rm -rf /"
""",
            "invalid migration type 'script'",
        )

    def test_missing_module_version_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "remove-setting"
setting_id = "example-withdrawn"
on_managed = "drop"
""",
            "requires 'module_version'",
        )

    def test_non_semver_module_version_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "remove-setting"
module_version = "2"
setting_id = "example-withdrawn"
on_managed = "drop"
""",
            "is not MAJOR.MINOR.PATCH",
        )

    def test_wrong_keys_for_type_are_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "remove-setting"
module_version = "0.2.0"
setting_id = "example-withdrawn"
on_managed = "drop"
to_id = "example-other"
""",
            "is not valid for migration type 'remove-setting'",
        )

    def test_invalid_on_managed_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "remove-setting"
module_version = "0.2.0"
setting_id = "example-withdrawn"
on_managed = "delete-everything"
""",
            "invalid on_managed 'delete-everything'",
        )

    def test_rename_to_nonexistent_setting_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "rename-setting"
module_version = "0.2.0"
from_id = "example-old"
to_id = "example-ghost"
""",
            "to_id 'example-ghost' does not name a declared setting",
        )

    def test_rename_from_a_still_declared_id_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "rename-setting"
module_version = "0.2.0"
from_id = "example-current"
to_id = "example-current"
""",
            "is still declared",
            gsettings_toml("example-current"),
        )

    def test_remove_of_a_still_declared_setting_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "remove-setting"
module_version = "0.2.0"
setting_id = "example-current"
on_managed = "revert"
""",
            "is still declared",
            gsettings_toml("example-current"),
        )

    def test_retarget_of_a_nonexistent_setting_is_rejected(self):
        self.assertMigrationRejects(
            """
[[module.migrations]]
type = "retarget-setting"
module_version = "0.2.0"
setting_id = "example-ghost"
from_target = "gsettings:org.example.old:example-key"
""",
            "does not name a declared setting",
        )


class TestShippedTreeDeclaresNoSettings(unittest.TestCase):
    """What the shipped tree is allowed to declare.

    Until Kedra Workflow v1 no module declared anything at all. That invariant
    has been spent, and its replacement is narrower rather than absent: exactly
    one module declares settings, and every one of them is a vendor default.

    The moment a second module declares something, or any declaration becomes
    `managed`, this fails -- which is the intent. A managed setting means Kedra
    writes into a user's dconf, and that needs a real backend adapter, an
    ownership record and a revert path, none of which exist yet.
    """

    # Modules permitted to declare settings today. Widening this is a deliberate
    # edit, reviewed alongside whatever the new module writes.
    MODULES_WITH_SETTINGS = {"desktop", "workflow"}

    def setUp(self):
        report = manifests.Report()
        self.tree = manifests.load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, f"shipped data invalid: {report.errors}")

    def test_only_the_expected_modules_declare_settings(self):
        declaring = {
            module_id
            for module_id, module in self.tree.modules.items()
            if module.get("settings", [])
        }
        self.assertEqual(declaring, self.MODULES_WITH_SETTINGS)

    def test_every_shipped_setting_is_a_vendor_default(self):
        for module_id, module in self.tree.modules.items():
            for entry in module.get("settings", []):
                with self.subTest(module=module_id, setting=entry.get("id")):
                    self.assertEqual(entry.get("policy"), "default")
                    self.assertEqual(entry.get("backend"), "gsettings")

    def test_no_shipped_setting_creates_a_managed_record(self):
        """The property the missing backend adapter depends on."""
        for module_id, module in self.tree.modules.items():
            for entry in module.get("settings", []):
                declaration = settings.build_declaration(
                    entry, module_id, module["version"]
                )
                with self.subTest(setting=declaration.id):
                    self.assertFalse(declaration.creates_managed_record)

    def test_no_module_declares_a_migration(self):
        for module_id, module in self.tree.modules.items():
            self.assertEqual(module.get("migrations", []), [], module_id)

    def test_settings_and_migrations_keys_are_present(self):
        """Present and empty, so adding one is an edit rather than a new key."""
        for module in self.tree.modules.values():
            self.assertIn("settings", module)
            self.assertIn("migrations", module)


if __name__ == "__main__":
    unittest.main()
