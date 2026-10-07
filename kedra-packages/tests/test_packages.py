"""Tests for computing a resolved profile's package set.

The question these answer is the one that makes Vanilla genuinely vanilla:
whether a Kedra default reaches a desktop is decided by package presence, not
by a configuration write. So "does this profile require
kedra-experience-workflow?" must be answerable from data alone, deterministically
and without consulting pacman.
"""

from __future__ import annotations

import unittest

from support import (
    DATA_DIR,
    TreeTestCase,
    base_preset_toml,
    derived_preset_toml,
    module_toml,
)

from kedra_experience import manifests, state
from kedra_experience.packages import (
    RELATION_ORDER,
    package_requirements,
    packages_by_relation,
    required_packages,
)
from kedra_experience.resolve import resolve_preset, resolve_state

WORKFLOW_PACKAGE = "kedra-experience-workflow"


def shipped_tree() -> manifests.Tree:
    report = manifests.Report()
    tree = manifests.load_tree(DATA_DIR, report)
    assert report.ok, report.rendered()
    return tree


def user_state(base: str, overrides: dict[str, bool]):
    return state.UserState(
        base_preset=base,
        module_overrides=tuple(
            state.ModuleOverride(id=module_id, enabled=enabled)
            for module_id, enabled in overrides.items()
        ),
    )


class ShippedProfileTests(unittest.TestCase):
    """Vanilla / Kedra / Custom, against the manifests that actually ship."""

    def setUp(self):
        self.tree = shipped_tree()

    def test_vanilla_requires_no_package_at_all(self):
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertFalse(resolution.applies_experience_layer)
        self.assertEqual(required_packages(self.tree, resolution), ())
        self.assertEqual(package_requirements(self.tree, resolution), ())

    def test_vanilla_does_not_require_the_workflow_defaults(self):
        # Stated separately from the "no packages" case: this is the invariant
        # that keeps upstream GNOME defaults on a Vanilla system, and it must
        # not become true only by accident of Vanilla requiring nothing.
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertNotIn(WORKFLOW_PACKAGE, required_packages(self.tree, resolution))

    def test_kedra_requires_the_workflow_defaults(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertIn(WORKFLOW_PACKAGE, required_packages(self.tree, resolution))

    def test_custom_kedra_with_workflow_disabled_drops_it(self):
        resolution = resolve_state(
            self.tree, user_state("kedra", {"workflow": False})
        )
        self.assertNotIn(WORKFLOW_PACKAGE, required_packages(self.tree, resolution))
        # ...and the profile is still Kedra, not Vanilla.
        self.assertTrue(resolution.applies_experience_layer)
        self.assertIn("gnome-shell", required_packages(self.tree, resolution))

    def test_disabling_an_unrelated_module_keeps_the_workflow_defaults(self):
        for other in ("extensions", "terminal", "appearance", "integration"):
            with self.subTest(disabled=other):
                resolution = resolve_state(
                    self.tree, user_state("kedra", {other: False})
                )
                self.assertIn(
                    WORKFLOW_PACKAGE, required_packages(self.tree, resolution)
                )

    def test_no_shipped_relation_needs_a_package_database(self):
        # Kedra Terminal v1 removed the tree's only `package-installed` relation
        # (a gnome-terminal entry that configured nothing), so every shipped
        # relation is now settleable from data alone. The invariant that an
        # unsettled condition must never leak into the required set is exercised
        # on a synthetic tree in RelationSemanticsTests, where it can be stated
        # without a placeholder relation in shipped data.
        resolution = resolve_preset(self.tree, "kedra")
        unresolved = [
            r for r in package_requirements(self.tree, resolution) if not r.resolved
        ]
        self.assertEqual(unresolved, [])
        self.assertNotIn("gnome-terminal", required_packages(self.tree, resolution))

    def test_terminal_requires_gnome_console_and_vanilla_does_not(self):
        # The Terminal v1 package semantics, from the profile side. The contract
        # is declared in the tree either way; only an *enabled* module turns it
        # into a requirement.
        kedra = resolve_preset(self.tree, "kedra")
        vanilla = resolve_preset(self.tree, "vanilla")
        self.assertIn("gnome-console", required_packages(self.tree, kedra))
        self.assertNotIn("gnome-console", required_packages(self.tree, vanilla))

        without = resolve_state(self.tree, user_state("kedra", {"terminal": False}))
        self.assertNotIn("gnome-console", required_packages(self.tree, without))

    def test_output_is_sorted_and_deduplicated(self):
        resolution = resolve_preset(self.tree, "kedra")
        names = required_packages(self.tree, resolution)
        self.assertEqual(list(names), sorted(set(names)))
        # gnome-shell is required by three enabled modules and appears once.
        self.assertEqual(names.count("gnome-shell"), 1)

    def test_requirements_record_the_module_that_asked(self):
        resolution = resolve_preset(self.tree, "kedra")
        owners = {
            r.module_id
            for r in package_requirements(self.tree, resolution)
            if r.name == WORKFLOW_PACKAGE
        }
        self.assertEqual(owners, {"workflow"})

    def test_deterministic_across_calls(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertEqual(
            package_requirements(self.tree, resolution),
            package_requirements(self.tree, resolution),
        )


class RelationSemanticsTests(TreeTestCase):
    """Relation strength, conditions and grouping, on synthetic trees."""

    def _tree(self, modules: dict[str, str], presets: dict[str, str]):
        return self.load(self.make_tree(modules=modules, presets=presets))

    def test_only_required_relations_reach_the_required_set(self):
        packages = """
[[module.packages]]
name = "pkg-required"
relation = "required"
reason = "r"

[[module.packages]]
name = "pkg-recommended"
relation = "recommended"
reason = "r"

[[module.packages]]
name = "pkg-optional"
relation = "optional"
reason = "r"
"""
        tree = self._tree(
            {"solo": module_toml("solo", packages=packages)},
            {"kedra": base_preset_toml("kedra", {"solo": True})},
        )
        resolution = resolve_preset(tree, "kedra")
        self.assertEqual(required_packages(tree, resolution), ("pkg-required",))

        grouped = packages_by_relation(tree, resolution)
        self.assertEqual(sorted(grouped), sorted(RELATION_ORDER))
        self.assertEqual(grouped["recommended"], ("pkg-recommended",))
        self.assertEqual(grouped["optional"], ("pkg-optional",))
        self.assertEqual(grouped["conditional"], ())

    def test_a_disabled_module_contributes_nothing(self):
        packages = """
[[module.packages]]
name = "pkg-required"
relation = "required"
reason = "r"
"""
        tree = self._tree(
            {"solo": module_toml("solo", packages=packages)},
            {"kedra": base_preset_toml("kedra", {"solo": False})},
        )
        self.assertEqual(required_packages(tree, resolve_preset(tree, "kedra")), ())

    def test_module_enabled_condition_is_evaluated_from_the_resolution(self):
        packages = """
[[module.packages]]
name = "pkg-conditional"
relation = "conditional"
condition = { type = "module-enabled", module = "other" }
reason = "r"
"""
        modules = {
            "solo": module_toml("solo", packages=packages),
            "other": module_toml("other"),
        }
        on = self._tree(
            modules, {"kedra": base_preset_toml("kedra", {"solo": True, "other": True})}
        )
        off = self._tree(
            modules, {"kedra": base_preset_toml("kedra", {"solo": True, "other": False})}
        )
        self.assertEqual(
            [r.name for r in package_requirements(on, resolve_preset(on, "kedra"))],
            ["pkg-conditional"],
        )
        self.assertEqual(
            package_requirements(off, resolve_preset(off, "kedra")), ()
        )

    def test_a_derived_preset_override_changes_the_package_set(self):
        packages = """
[[module.packages]]
name = "pkg-required"
relation = "required"
reason = "r"
"""
        tree = self._tree(
            {"solo": module_toml("solo", packages=packages)},
            {
                "kedra": base_preset_toml("kedra", {"solo": True}),
                "custom": derived_preset_toml("custom", "kedra", {"solo": False}),
            },
        )
        self.assertEqual(
            required_packages(tree, resolve_preset(tree, "kedra")), ("pkg-required",)
        )
        self.assertEqual(required_packages(tree, resolve_preset(tree, "custom")), ())


if __name__ == "__main__":
    unittest.main()
