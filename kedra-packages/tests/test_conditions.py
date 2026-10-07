#!/usr/bin/env python3
"""Tests for the structured conditional-dependency grammar (schema 2).

The grammar is a closed set of typed records. These tests exist as much to pin
down what it deliberately *cannot* express as to check what it can: if someone
later adds boolean composition or an expression parser, the negative cases here
should be the thing that makes them stop and reconsider.

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
    base_preset_toml,
    manifests,
    module_toml,
)


def packages_block(body: str) -> str:
    return "\n" + body.strip() + "\n"


class ConditionTestCase(TreeTestCase):
    def tree_with_packages(self, body: str):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["terminal"] = module_toml("terminal", packages=packages_block(body))
        return self.load_dirty(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def assertRejects(self, body: str, needle: str) -> None:
        _, report = self.tree_with_packages(body)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        self.assertIn(needle, " | ".join(m for _, m in report.errors))

    def assertAccepts(self, body: str) -> None:
        _, report = self.tree_with_packages(body)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestValidConditions(ConditionTestCase):
    def test_package_installed_condition(self):
        self.assertAccepts(
            """
[[module.packages]]
name = "gnome-terminal"
relation = "conditional"
condition = { type = "package-installed", package = "gnome-terminal" }
"""
        )

    def test_module_enabled_condition(self):
        self.assertAccepts(
            """
[[module.packages]]
name = "gnome-tweaks"
relation = "conditional"
condition = { type = "module-enabled", module = "appearance" }
"""
        )

    def test_condition_may_be_written_as_a_subtable(self):
        """Inline table and dotted-table forms are the same TOML value."""
        self.assertAccepts(
            """
[[module.packages]]
name = "gnome-terminal"
relation = "conditional"

[module.packages.condition]
type = "package-installed"
package = "gnome-terminal"
"""
        )

    def test_condition_with_reason_is_accepted(self):
        self.assertAccepts(
            """
[[module.packages]]
name = "gnome-terminal"
relation = "conditional"
condition = { type = "package-installed", package = "gnome-terminal" }
reason = "Only configured when already present."
"""
        )

    def test_every_declared_condition_type_has_exactly_one_operand(self):
        self.assertEqual(
            set(manifests.CONDITION_TYPES),
            {"package-installed", "module-enabled"},
        )
        for operand in manifests.CONDITION_TYPES.values():
            self.assertIsInstance(operand, str)


class TestInvalidConditionType(ConditionTestCase):
    def test_unknown_type_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "phase-of-moon", value = "waxing" }
""",
            "invalid type 'phase-of-moon'",
        )

    def test_missing_type_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { package = "gnome-terminal" }
""",
            "missing required key 'type'",
        )

    def test_wrong_operand_for_type_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "package-installed", module = "appearance" }
""",
            "is not valid for type 'package-installed'",
        )

    def test_missing_operand_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "module-enabled" }
""",
            "requires a non-empty 'module'",
        )

    def test_empty_operand_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "package-installed", package = "" }
""",
            "requires a non-empty 'package'",
        )


class TestNoExpressionLanguage(ConditionTestCase):
    """The grammar must stay a typed record, not become a language."""

    def test_expression_string_is_rejected_with_migration_guidance(self):
        self.assertRejects(
            """
[[module.packages]]
name = "gnome-terminal"
relation = "conditional"
condition = "installed(gnome-terminal)"
""",
            "expression strings such as",
        )

    def test_expression_string_names_the_schema_that_removed_it(self):
        self.assertRejects(
            """
[[module.packages]]
name = "gnome-terminal"
relation = "conditional"
condition = "installed(gnome-terminal)"
""",
            "removed in schema_version 2",
        )

    def test_boolean_composition_is_not_expressible(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "package-installed", package = "a", any_of = ["b"] }
""",
            "is not valid for type",
        )

    def test_non_table_non_string_condition_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = 42
""",
            "'condition' must be a table",
        )


class TestModuleReferences(ConditionTestCase):
    def test_module_enabled_referencing_nonexistent_module_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "module-enabled", module = "ghost" }
""",
            "condition references nonexistent module 'ghost'",
        )

    def test_module_enabled_with_invalid_identifier_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "module-enabled", module = "Not An ID" }
""",
            "is not a valid module ID",
        )

    def test_package_installed_is_not_cross_checked_against_modules(self):
        """Package names are pacman's namespace, not the module namespace."""
        self.assertAccepts(
            """
[[module.packages]]
name = "x"
relation = "conditional"
condition = { type = "package-installed", package = "some-package-not-a-module" }
"""
        )


class TestRelationAndConditionPairing(ConditionTestCase):
    def test_conditional_without_condition_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "conditional"
""",
            "relation 'conditional' requires a 'condition'",
        )

    def test_condition_on_non_conditional_relation_is_rejected(self):
        self.assertRejects(
            """
[[module.packages]]
name = "x"
relation = "required"
condition = { type = "package-installed", package = "y" }
""",
            "only meaningful for relation 'conditional'",
        )

    def test_other_relations_still_accept_no_condition(self):
        self.assertAccepts(
            """
[[module.packages]]
name = "a"
relation = "required"

[[module.packages]]
name = "b"
relation = "recommended"

[[module.packages]]
name = "c"
relation = "optional"
"""
        )


class TestShippedConditions(unittest.TestCase):
    """The shipped manifests must exercise the grammar, not just define it."""

    def setUp(self):
        report = manifests.Report()
        self.tree = manifests.load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, f"shipped data invalid: {report.errors}")

    def shipped_conditions(self):
        for module in self.tree.modules.values():
            for entry in module.get("packages", []):
                if "condition" in entry:
                    yield module["id"], entry

    def test_shipped_conditions_are_all_structured(self):
        found = list(self.shipped_conditions())
        self.assertTrue(found, "expected the shipped tree to use conditional relations")
        for module_id, entry in found:
            self.assertIsInstance(
                entry["condition"], dict, f"{module_id} still uses a string condition"
            )

    def test_the_shipped_conditions_are_exactly_the_audited_set(self):
        """Written out rather than derived, so a new one is a deliberate edit.

        Both condition types were exercised by shipped data until Kedra Terminal
        v1, which removed the tree's only `package-installed` relation: a
        gnome-terminal entry claiming it would be "configured only if the user
        already has it", in a module that has never declared a setting. It
        configured nothing, so it was metadata a reader had to disprove.

        `package-installed` is still exercised, on synthetic trees in the
        grammar tests above. Keeping a placeholder relation in shipped data for
        the sake of coverage would be paying in product honesty for a test.
        """
        found = {
            (module_id, entry["name"], entry["condition"]["type"])
            for module_id, entry in self.shipped_conditions()
        }
        self.assertEqual(found, {("extras", "gnome-tweaks", "module-enabled")})

    def test_the_grammar_still_defines_both_types(self):
        # The removal above is a data decision, not a schema narrowing.
        self.assertEqual(
            set(manifests.CONDITION_TYPES), {"package-installed", "module-enabled"}
        )

    def test_no_shipped_manifest_contains_a_string_condition(self):
        for path in (DATA_DIR / "modules").glob("*.toml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('condition = "', text, f"{path.name} has a string condition")


if __name__ == "__main__":
    unittest.main()
