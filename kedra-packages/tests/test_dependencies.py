"""Tests for the module dependency model: hard requirements vs advisories.

Schema 4 split one overloaded relationship into two. `requires` means a module
cannot function correctly without another; `works_best_with` means they are
designed to be used together and the standard preset enables both. Only the
first can invalidate a profile.

The property that matters most is negative and easy to lose: **a soft
relationship must never enable a module or add a package.** It is asserted here
from both directions -- activation and package resolution -- because a
recommendation that quietly acts is worse than no recommendation at all.

No shipped module currently declares a hard requirement (see
`ShippedRelationshipTests`), so hard-requirement behaviour is exercised against
synthetic trees. That is the honest arrangement: the mechanism is real and
enforced, and the shipped data simply has no case for it yet.
"""

from __future__ import annotations

import unittest

from support import (
    DATA_DIR,
    TreeTestCase,
    base_preset_toml,
    derived_preset_toml,
    module_toml,
    shipped_tree_with_derived_preset,
    state,
)

from kedra_experience import manifests
from kedra_experience.packages import required_packages
from kedra_experience.resolve import (
    ADVISORY_CODES,
    ADVISORY_WORKS_BEST_WITH_DISABLED,
    Advisory,
    Diagnostics,
    RequirementViolation,
    advisories,
    diagnose,
    hard_requirement_violations,
    resolve_preset,
    resolve_state,
)

DESKTOP_PACKAGE = "kedra-experience-desktop"
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


class HardRequirementTests(TreeTestCase):
    """`requires` still means what it says, and still fails."""

    def _pair(self, *, b_enabled: bool, hard: bool):
        modules = {
            "alpha": module_toml(
                "alpha",
                requires=("beta",) if hard else (),
                works_best_with=() if hard else ("beta",),
            ),
            "beta": module_toml("beta"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"alpha": True, "beta": b_enabled})
            },
        )
        tree = self.load(root)
        return tree, resolve_preset(tree, "kedra")

    def test_a_violated_hard_requirement_is_reported(self):
        tree, resolution = self._pair(b_enabled=False, hard=True)
        self.assertEqual(
            hard_requirement_violations(tree, resolution),
            (RequirementViolation(module_id="alpha", requires="beta"),),
        )

    def test_a_violated_hard_requirement_makes_the_profile_invalid(self):
        tree, resolution = self._pair(b_enabled=False, hard=True)
        diagnostics = diagnose(tree, resolution)
        self.assertFalse(diagnostics.ok)
        # ...and is never quietly downgraded to an advisory.
        self.assertEqual(diagnostics.advisories, ())

    def test_a_satisfied_hard_requirement_is_silent(self):
        tree, resolution = self._pair(b_enabled=True, hard=True)
        self.assertEqual(diagnose(tree, resolution), Diagnostics())

    def test_a_hard_requirement_does_not_enable_the_required_module(self):
        # The violation is reported, not repaired. Auto-enabling would make a
        # user's explicit choice fail to stick.
        tree, resolution = self._pair(b_enabled=False, hard=True)
        self.assertNotIn("beta", resolution.enabled_module_ids)

    def test_a_disabled_module_with_an_unmet_requirement_is_not_reported(self):
        # Only *enabled* modules can have an unsatisfied requirement.
        modules = {
            "alpha": module_toml("alpha", requires=("beta",)),
            "beta": module_toml("beta"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"alpha": False, "beta": False})
            },
        )
        tree = self.load(root)
        self.assertEqual(diagnose(tree, resolve_preset(tree, "kedra")), Diagnostics())

    def test_violations_are_sorted_and_deterministic(self):
        modules = {
            "aa": module_toml("aa", requires=("zz", "mm")),
            "bb": module_toml("bb", requires=("zz",)),
            "mm": module_toml("mm"),
            "zz": module_toml("zz"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml(
                    "kedra", {"aa": True, "bb": True, "mm": False, "zz": False}
                )
            },
        )
        tree = self.load(root)
        resolution = resolve_preset(tree, "kedra")
        found = hard_requirement_violations(tree, resolution)
        self.assertEqual(
            [(v.module_id, v.requires) for v in found],
            [("aa", "mm"), ("aa", "zz"), ("bb", "zz")],
        )
        self.assertEqual(found, hard_requirement_violations(tree, resolution))


class AdvisoryTests(TreeTestCase):
    """`works_best_with` advises. It never acts."""

    def _tree(self, *, b_enabled: bool, reason: str = ""):
        modules = {
            "alpha": module_toml(
                "alpha",
                works_best_with={"beta": reason} if reason else ("beta",),
                packages="""
[[module.packages]]
name = "pkg-alpha"
relation = "required"
reason = "r"
""",
            ),
            "beta": module_toml(
                "beta",
                packages="""
[[module.packages]]
name = "pkg-beta"
relation = "required"
reason = "r"
""",
            ),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"alpha": True, "beta": b_enabled})
            },
        )
        tree = self.load(root)
        return tree, resolve_preset(tree, "kedra")

    def test_an_unmet_advisory_still_resolves(self):
        tree, resolution = self._tree(b_enabled=False)
        self.assertTrue(resolution.applies_experience_layer)
        self.assertEqual(resolution.enabled_module_ids, ("alpha",))

    def test_an_unmet_advisory_is_not_a_violation(self):
        tree, resolution = self._tree(b_enabled=False)
        self.assertEqual(hard_requirement_violations(tree, resolution), ())
        self.assertTrue(diagnose(tree, resolution).ok)

    def test_the_advisory_is_emitted(self):
        tree, resolution = self._tree(b_enabled=False, reason="beta makes alpha nicer")
        self.assertEqual(
            advisories(tree, resolution),
            (
                Advisory(
                    code=ADVISORY_WORKS_BEST_WITH_DISABLED,
                    module_id="alpha",
                    related_module_id="beta",
                    reason="beta makes alpha nicer",
                ),
            ),
        )

    def test_no_advisory_when_both_are_enabled(self):
        tree, resolution = self._tree(b_enabled=True)
        self.assertEqual(advisories(tree, resolution), ())

    def test_an_advisory_does_not_alter_module_activation(self):
        # The critical negative property, from the activation side.
        tree, resolution = self._tree(b_enabled=False)
        self.assertNotIn("beta", resolution.enabled_module_ids)

    def test_an_advisory_does_not_alter_package_resolution(self):
        # ...and from the package side. A recommendation that quietly installs
        # something is worse than no recommendation at all.
        tree, resolution = self._tree(b_enabled=False)
        self.assertEqual(required_packages(tree, resolution), ("pkg-alpha",))

    def test_advisories_are_sorted_and_deterministic(self):
        modules = {
            "aa": module_toml("aa", works_best_with=("zz", "mm")),
            "bb": module_toml("bb", works_best_with=("zz",)),
            "mm": module_toml("mm"),
            "zz": module_toml("zz"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml(
                    "kedra", {"aa": True, "bb": True, "mm": False, "zz": False}
                )
            },
        )
        tree = self.load(root)
        resolution = resolve_preset(tree, "kedra")
        found = advisories(tree, resolution)
        self.assertEqual(
            [(a.module_id, a.related_module_id) for a in found],
            [("aa", "mm"), ("aa", "zz"), ("bb", "zz")],
        )
        self.assertEqual(found, advisories(tree, resolution))

    def test_a_disabled_module_advises_nothing(self):
        modules = {
            "alpha": module_toml("alpha", works_best_with=("beta",)),
            "beta": module_toml("beta"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"alpha": False, "beta": False})
            },
        )
        tree = self.load(root)
        self.assertEqual(advisories(tree, resolve_preset(tree, "kedra")), ())

    def test_a_derived_preset_override_can_trigger_an_advisory(self):
        modules = {
            "alpha": module_toml("alpha", works_best_with=("beta",)),
            "beta": module_toml("beta"),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"alpha": True, "beta": True}),
                "custom": derived_preset_toml("custom", "kedra", {"beta": False}),
            },
        )
        tree = self.load(root)
        self.assertEqual(advisories(tree, resolve_preset(tree, "kedra")), ())
        self.assertEqual(len(advisories(tree, resolve_preset(tree, "custom"))), 1)

    def test_the_advisory_code_is_a_known_stable_value(self):
        # Consumers branch on the code, never on the rendered sentence.
        tree, resolution = self._tree(b_enabled=False)
        for advisory in advisories(tree, resolution):
            with self.subTest(advisory=advisory):
                self.assertIn(advisory.code, ADVISORY_CODES)

    def test_rendering_is_a_convenience_not_the_contract(self):
        advisory = Advisory(
            code=ADVISORY_WORKS_BEST_WITH_DISABLED,
            module_id="alpha",
            related_module_id="beta",
            reason="",
        )
        # Every fact is available structurally; describe() adds only wording.
        self.assertIn("alpha", advisory.describe())
        self.assertIn("beta", advisory.describe())
        self.assertFalse(advisory.describe().endswith(";"))


class ManifestGrammarTests(TreeTestCase):
    """Schema 4 accepts the new relation, and rejects the ways of misusing it."""

    def _reject(self, module_body: str, fragment: str):
        root = self.make_tree(
            modules={"alpha": module_body, "beta": module_toml("beta")},
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": True})},
        )
        _, report = self.load_dirty(root)
        self.assertFalse(report.ok)
        self.assertTrue(
            any(fragment in message for _, message in report.errors),
            f"expected an error containing {fragment!r}, got {report.errors}",
        )

    def test_a_reason_is_optional(self):
        root = self.make_tree(
            modules={
                "alpha": module_toml("alpha", works_best_with=("beta",)),
                "beta": module_toml("beta"),
            },
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": True})},
        )
        tree = self.load(root)
        self.assertEqual(
            tree.modules["alpha"]["works_best_with"], [{"id": "beta"}]
        )

    def test_a_bare_id_array_is_rejected(self):
        # The schema 3 shape of `requires`. Silently accepting it would make the
        # two relationship kinds look interchangeable.
        self._reject(
            module_toml("alpha").replace(
                "conflicts = []", 'conflicts = []\nworks_best_with = ["beta"]'
            ),
            "must be a table with 'id'",
        )

    def test_an_unknown_key_is_rejected(self):
        self._reject(
            module_toml("alpha").replace(
                "conflicts = []",
                'conflicts = []\n\n[[module.works_best_with]]\nid = "beta"\nstrength = "high"',
            ),
            "unknown key 'strength'",
        )

    def test_a_nonexistent_module_is_rejected(self):
        self._reject(
            module_toml("alpha", works_best_with=("ghost",)),
            "references nonexistent module 'ghost'",
        )

    def test_a_self_reference_is_rejected(self):
        self._reject(
            module_toml("alpha", works_best_with=("alpha",)),
            "must not reference the module itself",
        )

    def test_a_duplicate_reference_is_rejected(self):
        self._reject(
            module_toml("alpha").replace(
                "conflicts = []",
                'conflicts = []\n\n[[module.works_best_with]]\nid = "beta"\n'
                '\n[[module.works_best_with]]\nid = "beta"',
            ),
            "duplicate reference to module 'beta'",
        )

    def test_declaring_a_relationship_both_hard_and_soft_is_rejected(self):
        # A module cannot both need something and work fine without it.
        self._reject(
            module_toml("alpha", requires=("beta",), works_best_with=("beta",)),
            "either hard or advisory, never both",
        )

    def test_declaring_a_conflict_as_recommended_is_rejected(self):
        self._reject(
            module_toml("alpha", works_best_with=("beta",)).replace(
                "conflicts = []", 'conflicts = ["beta"]'
            ),
            "declared as a conflict",
        )

    def test_an_empty_reason_is_rejected(self):
        self._reject(
            module_toml("alpha", works_best_with={"beta": "   "}),
            "'reason' must be a non-empty string",
        )


class ShippedRelationshipTests(unittest.TestCase):
    """What the shipped tree actually declares, after the schema 4 audit."""

    # The audit's outcome, written out rather than derived: every module-to-module
    # relationship that survives, and its strength. An addition must be a
    # deliberate edit here, with evidence.
    EXPECTED_HARD: dict[str, tuple[str, ...]] = {}
    # `terminal -> appearance` was here until Kedra Terminal v1 and was removed
    # by that scope review: Terminal owns the terminal *application* contract,
    # which is exactly as complete with the appearance layer off, and Appearance
    # ships no terminal-facing value for its absence to degrade. An advisory
    # must name a consequence a user could observe; that one no longer did.
    EXPECTED_SOFT: dict[str, tuple[str, ...]] = {
        "workflow": ("desktop",),
    }

    def setUp(self):
        self.tree = shipped_tree()

    def test_hard_requirements_are_exactly_as_audited(self):
        found = {
            module_id: tuple(module["requires"])
            for module_id, module in self.tree.modules.items()
            if module.get("requires")
        }
        self.assertEqual(found, self.EXPECTED_HARD)

    def test_advisory_relationships_are_exactly_as_audited(self):
        found = {
            module_id: tuple(e["id"] for e in module["works_best_with"])
            for module_id, module in self.tree.modules.items()
            if module.get("works_best_with")
        }
        self.assertEqual(found, self.EXPECTED_SOFT)

    def test_every_advisory_states_its_consequence(self):
        # A soft relationship with nothing to say about the consequence is
        # indistinguishable from noise in a UI.
        for module_id, module in self.tree.modules.items():
            for entry in module.get("works_best_with", []):
                with self.subTest(module=module_id, related=entry["id"]):
                    self.assertTrue(entry.get("reason", "").strip())

    def test_the_layers_that_do_not_depend_on_desktop_no_longer_claim_to(self):
        # The audit's headline correction: these three carried
        # `requires = ["desktop"]` describing nothing that was ever true, so
        # switching Desktop off reported four failures for one honest choice.
        for module_id in ("appearance", "extensions", "integration"):
            module = self.tree.modules[module_id]
            with self.subTest(module=module_id):
                self.assertEqual(module["requires"], [])
                self.assertEqual(module.get("works_best_with", []), [])

    def test_no_shipped_profile_has_a_hard_requirement_violation(self):
        for preset_id in ("vanilla", "kedra"):
            with self.subTest(preset=preset_id):
                resolution = resolve_preset(self.tree, preset_id)
                self.assertEqual(hard_requirement_violations(self.tree, resolution), ())

    def test_disabling_any_user_selectable_module_keeps_the_profile_valid(self):
        # The product claim, narrowed to what the manifests actually permit and
        # still checked exhaustively: a Custom Kedra may switch off any one
        # module that is the user's to switch, and still be valid.
        for module_id in sorted(self.tree.modules):
            if not self.tree.modules[module_id]["user_selectable"]:
                continue
            with self.subTest(disabled=module_id):
                resolution = resolve_state(
                    self.tree, user_state("kedra", {module_id: False})
                )
                diagnostics = diagnose(self.tree, resolution)
                self.assertTrue(
                    diagnostics.ok,
                    f"disabling '{module_id}' produced {diagnostics.violations}",
                )

    def test_disabling_any_non_user_selectable_module_is_refused(self):
        # The other half, and the reason the first had to be narrowed. One
        # shipped module is structural: `integration`, which carries the
        # Experience layer's own delivery. `desktop` is core but selectable --
        # see modules/desktop.toml on why those are independent.
        checked = 0
        for module_id in sorted(self.tree.modules):
            if self.tree.modules[module_id]["user_selectable"]:
                continue
            checked += 1
            with self.subTest(disabled=module_id):
                resolution = resolve_state(
                    self.tree, user_state("kedra", {module_id: False})
                )
                # Not applied: the module keeps the preset's answer.
                self.assertIn(module_id, resolution.enabled_module_ids)
                diagnostics = diagnose(self.tree, resolution)
                self.assertFalse(diagnostics.ok)
                self.assertEqual(
                    [r.module_id for r in diagnostics.refused_overrides], [module_id]
                )
        self.assertEqual(checked, 1, "expected integration alone")

    def test_disabling_desktop_produces_exactly_one_advisory(self):
        # Was four "unsatisfied requirements" before the audit. This is the one
        # advisory the shipped tree declares, and it is reachable from an
        # ordinary Custom Kedra selection -- which is what `desktop` being
        # user-selectable is for.
        resolution = resolve_state(self.tree, user_state("kedra", {"desktop": False}))
        found = advisories(self.tree, resolution)
        self.assertEqual(
            [(a.module_id, a.related_module_id) for a in found],
            [("workflow", "desktop")],
        )

    def test_disabling_appearance_now_produces_no_advisory_at_all(self):
        # It produced `terminal -> appearance` until Kedra Terminal v1. Nothing
        # else claimed a relationship with the appearance layer, so switching it
        # off is now an entirely quiet, entirely valid choice -- which is what
        # the dependency model is supposed to say about a module no other module
        # actually reads.
        resolution = resolve_state(self.tree, user_state("kedra", {"appearance": False}))
        self.assertEqual(advisories(self.tree, resolution), ())

    def test_a_preset_may_disable_a_module_a_selection_may_not(self):
        # Preset machinery is deliberately unrestricted: a preset is distributed
        # Kedra data defining what a product is, so it may disable even a
        # structural module. Only caller-supplied overrides are constrained.
        tree = shipped_tree_with_derived_preset(self, "minimal", {"integration": False})
        resolution = resolve_preset(tree, "minimal")
        self.assertNotIn("integration", resolution.enabled_module_ids)
        self.assertEqual(resolution.refused_overrides, ())
        self.assertTrue(diagnose(tree, resolution).ok)

    def test_the_standard_kedra_preset_reports_nothing_at_all(self):
        # Both sides of every advisory pair are enabled, which is what the
        # preset is for. Advisories are a Custom Kedra diagnostic.
        self.assertEqual(
            diagnose(self.tree, resolve_preset(self.tree, "kedra")), Diagnostics()
        )

    def test_the_standard_kedra_preset_still_enables_the_intended_set(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertEqual(
            {m.id: m.enabled for m in resolution.modules},
            {
                "desktop": True,
                "workflow": True,
                "appearance": True,
                "terminal": True,
                "extensions": True,
                "integration": True,
                "extras": False,
            },
        )


class PackageResolutionIsUnaffectedTests(unittest.TestCase):
    """Package resolution follows ACTIVE modules, never recommended ones."""

    def setUp(self):
        self.tree = shipped_tree()

    def required_for_state(self, overrides: dict[str, bool]) -> tuple[str, ...]:
        return required_packages(
            self.tree, resolve_state(self.tree, user_state("kedra", overrides))
        )

    def test_vanilla_is_unchanged(self):
        self.assertEqual(
            required_packages(self.tree, resolve_preset(self.tree, "vanilla")), ()
        )

    def test_kedra_is_unchanged(self):
        required = required_packages(self.tree, resolve_preset(self.tree, "kedra"))
        self.assertEqual(
            required,
            (
                "gnome-console",
                "gnome-shell",
                # The `integration` module's two: the manifest tree itself and
                # the resolver that reads it. Without them a target would have
                # Kedra's GSettings defaults and no way to say which profile it
                # is running. See modules/integration.toml.
                "kedra-experience",
                DESKTOP_PACKAGE,
                "kedra-experience-runtime",
                WORKFLOW_PACKAGE,
            ),
        )

    def test_desktop_off_workflow_on_gives_the_workflow_package_only(self):
        required = self.required_for_state({"desktop": False})
        self.assertNotIn(DESKTOP_PACKAGE, required)
        self.assertIn(WORKFLOW_PACKAGE, required)

    def test_a_refused_override_cannot_drop_the_experience_packages(self):
        # The safety property the refusal exists for, on the module that still
        # has one: even a caller that ignores the diagnostics and reads only
        # `required` gets a complete set.
        resolution = resolve_state(self.tree, user_state("kedra", {"integration": False}))
        required = required_packages(self.tree, resolution)
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)
        self.assertFalse(diagnose(self.tree, resolution).ok)

    def test_desktop_on_workflow_off_gives_the_desktop_package_only(self):
        required = self.required_for_state({"workflow": False})
        self.assertIn(DESKTOP_PACKAGE, required)
        self.assertNotIn(WORKFLOW_PACKAGE, required)

    def test_the_advisory_adds_no_package(self):
        # workflow works best with desktop; with desktop off, the desktop
        # package must still be absent. Nothing about a recommendation installs.
        with_desktop = required_packages(
            self.tree, resolve_preset(self.tree, "kedra")
        )
        without = self.required_for_state({"desktop": False})
        self.assertEqual(
            set(with_desktop) - set(without), {DESKTOP_PACKAGE}
        )


if __name__ == "__main__":
    unittest.main()
