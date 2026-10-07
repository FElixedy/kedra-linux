"""Tests for the Kedra Desktop fixed five-workspace model.

What this file guards is the *product*: exactly two settings, both vendor
defaults, the coupling to the Workflow keymap, and the package boundary that
makes "Vanilla keeps upstream GNOME workspaces" true by construction rather
than by intention.

Expectations come from `support.EXPECTED_WORKSPACE_MODEL`, written out by hand.
Deriving them from the manifest would make every assertion a tautology.

Nothing here reads or writes live GSettings or dconf. The one test that consults
the system reads *schema definitions* -- installed, read-only package data --
and is skipped where GNOME is not installed.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from support import (
    DATA_DIR,
    EXPECTED_DESKTOP_SETTING_IDS,
    EXPECTED_WORKSPACE_MODEL,
    GNOME_DESKTOP_SCOPED_DEFAULTS,
    GNOME_SHELL_OVERRIDE_FILE,
    KEDRA_WORKSPACE_COUNT,
    REPO_ROOT,
    state,
)

from kedra_experience import manifests
from kedra_experience.packages import required_packages
from kedra_experience.plan import build_plan
from kedra_experience.resolve import (
    ADVISORY_WORKS_BEST_WITH_DISABLED,
    Diagnostics,
    advisories,
    diagnose,
    resolve_preset,
    resolve_state,
)
from kedra_experience.values import TypedValue

SYSTEM_SCHEMA_DIR = Path("/usr/share/glib-2.0/schemas")

DESKTOP_PACKAGE = "kedra-experience-desktop"
WORKFLOW_PACKAGE = "kedra-experience-workflow"


def shipped_tree() -> manifests.Tree:
    report = manifests.Report()
    tree = manifests.load_tree(DATA_DIR, report)
    assert report.ok, report.rendered()
    return tree


def desktop_settings(tree) -> dict[str, dict]:
    return {
        entry["id"]: entry
        for entry in tree.modules["desktop"].get("settings", [])
        if isinstance(entry, dict)
    }


def user_state(base: str, overrides: dict[str, bool]):
    return state.UserState(
        base_preset=base,
        module_overrides=tuple(
            state.ModuleOverride(id=module_id, enabled=enabled)
            for module_id, enabled in overrides.items()
        ),
    )


class WorkspaceModelTests(unittest.TestCase):
    """Exactly the fixed five-workspace model, and nothing else."""

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = desktop_settings(self.tree)

    def test_declares_exactly_the_expected_settings(self):
        # Scope guard. Overview, focus, tiling, gestures, animations and
        # workspace names are all out of scope, and adding one has to be a
        # deliberate edit here.
        self.assertEqual(set(self.settings), set(EXPECTED_DESKTOP_SETTING_IDS))

    def test_dynamic_workspaces_is_turned_off(self):
        entry = self.settings["desktop-dynamic-workspaces"]
        self.assertEqual(
            entry["target"], {"schema": "org.gnome.mutter", "key": "dynamic-workspaces"}
        )
        self.assertEqual(entry["value"], {"type": "b", "data": False})

    def test_workspace_count_is_five(self):
        entry = self.settings["desktop-workspace-count"]
        self.assertEqual(
            entry["target"],
            {"schema": "org.gnome.desktop.wm.preferences", "key": "num-workspaces"},
        )
        self.assertEqual(
            entry["value"], {"type": "i", "data": KEDRA_WORKSPACE_COUNT}
        )

    def test_both_keys_are_declared_because_either_alone_is_incomplete(self):
        # num-workspaces is ignored while workspaces are dynamic; turning
        # dynamic off alone leaves GNOME's default of 4. Neither key on its own
        # produces "five workspaces always exist".
        targets = {
            (e["target"]["schema"], e["target"]["key"]) for e in self.settings.values()
        }
        self.assertEqual(targets, set(EXPECTED_WORKSPACE_MODEL))

    def test_every_declaration_matches_the_expected_model(self):
        declared = {
            (e["target"]["schema"], e["target"]["key"]): TypedValue(
                type=e["value"]["type"], data=e["value"]["data"]
            )
            for e in self.settings.values()
        }
        self.assertEqual(declared, EXPECTED_WORKSPACE_MODEL)

    def test_every_setting_is_a_vendor_default(self):
        for setting_id, entry in self.settings.items():
            with self.subTest(setting=setting_id):
                self.assertEqual(entry["policy"], "default")
                self.assertEqual(entry["backend"], "gsettings")
                self.assertTrue(entry["description"].strip())
                self.assertEqual(entry["introduced_in"], "0.3.0")


class OwnershipBoundaryTests(unittest.TestCase):
    """Desktop owns workspace topology; Workflow owns keyboard navigation."""

    def setUp(self):
        self.tree = shipped_tree()

    def test_workflow_does_not_declare_any_workspace_topology_setting(self):
        # Duplicating the count into Workflow would give one product decision
        # two owners, and the manifest validator would reject the second anyway.
        workflow_targets = {
            (e["target"]["schema"], e["target"]["key"])
            for e in self.tree.modules["workflow"].get("settings", [])
        }
        self.assertEqual(workflow_targets & set(EXPECTED_WORKSPACE_MODEL), set())

    def test_no_other_module_declares_workspace_topology(self):
        owners = {
            module_id
            for module_id, module in self.tree.modules.items()
            for entry in module.get("settings", [])
            if (entry["target"]["schema"], entry["target"]["key"])
            in EXPECTED_WORKSPACE_MODEL
        }
        self.assertEqual(owners, {"desktop"})

    def test_workflow_declares_desktop_as_an_advisory_relationship(self):
        # The declarative form of "Super+1..5 is designed for the Kedra
        # five-workspace model". Advisory, not hard: it records the coupling
        # without force-enabling anything and without invalidating a profile
        # that separates them -- see AdvisoryRelationshipTests.
        related = {
            entry["id"]
            for entry in self.tree.modules["workflow"].get("works_best_with", [])
        }
        self.assertIn("desktop", related)
        self.assertNotIn("desktop", self.tree.modules["workflow"]["requires"])

    def test_the_advisory_states_the_consequence(self):
        entry = next(
            e
            for e in self.tree.modules["workflow"]["works_best_with"]
            if e["id"] == "desktop"
        )
        self.assertTrue(entry.get("reason", "").strip())

    def test_desktop_does_not_depend_on_workflow(self):
        # Topology stands on its own; the keymap is what is designed around it.
        self.assertEqual(self.tree.modules["desktop"]["requires"], [])
        self.assertEqual(self.tree.modules["desktop"].get("works_best_with", []), [])


class AdvisoryRelationshipTests(unittest.TestCase):
    """Workflow-with-Desktop-off is valid, and advised rather than rejected."""

    def setUp(self):
        self.tree = shipped_tree()

    def test_the_full_kedra_preset_couples_them(self):
        resolution = resolve_preset(self.tree, "kedra")
        enabled = set(resolution.enabled_module_ids)
        self.assertIn("desktop", enabled)
        self.assertIn("workflow", enabled)
        # Both sides enabled, so the advisory does not fire.
        self.assertEqual(diagnose(self.tree, resolution), Diagnostics())

    def test_custom_kedra_may_break_the_coupling(self):
        # The product contract: `desktop` is user-selectable, so a Custom Kedra
        # may switch the fixed workspace model off and keep the keymap. The
        # resolver does not quietly switch desktop back on.
        resolution = resolve_state(self.tree, user_state("kedra", {"desktop": False}))
        self.assertNotIn("desktop", resolution.enabled_module_ids)
        self.assertIn("workflow", resolution.enabled_module_ids)
        self.assertEqual(resolution.refused_overrides, ())

    def test_the_profile_stays_valid(self):
        diagnostics = diagnose(
            self.tree, resolve_state(self.tree, user_state("kedra", {"desktop": False}))
        )
        self.assertTrue(diagnostics.ok)
        self.assertEqual(diagnostics.violations, ())
        self.assertEqual(diagnostics.refused_overrides, ())

    def test_and_the_consequence_is_advised(self):
        resolution = resolve_state(self.tree, user_state("kedra", {"desktop": False}))
        found = advisories(self.tree, resolution)
        self.assertEqual(
            [(a.module_id, a.related_module_id) for a in found], [("workflow", "desktop")]
        )
        self.assertEqual(found[0].code, ADVISORY_WORKS_BEST_WITH_DISABLED)
        self.assertIn("workspace", found[0].reason)

    def test_a_structural_module_still_may_not_be_switched(self):
        # The rule the desktop correction did not loosen. `integration` carries
        # the Experience layer's own delivery and is not the user's to switch.
        resolution = resolve_state(self.tree, user_state("kedra", {"integration": False}))
        self.assertIn("integration", resolution.enabled_module_ids)
        self.assertEqual(
            [r.module_id for r in resolution.refused_overrides], ["integration"]
        )
        self.assertFalse(diagnose(self.tree, resolution).ok)

    def test_disabling_workflow_advises_nothing(self):
        resolution = resolve_state(self.tree, user_state("kedra", {"workflow": False}))
        self.assertIn("desktop", resolution.enabled_module_ids)
        self.assertEqual(diagnose(self.tree, resolution), Diagnostics())

    def test_vanilla_has_nothing_to_report(self):
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertEqual(diagnose(self.tree, resolution), Diagnostics())


class PackageResolutionTests(unittest.TestCase):
    """Which profile installs the workspace model, and which genuinely does not."""

    def setUp(self):
        self.tree = shipped_tree()

    def required(self, resolution) -> tuple[str, ...]:
        return required_packages(self.tree, resolution)

    def test_vanilla_has_neither_defaults_package(self):
        required = self.required(resolve_preset(self.tree, "vanilla"))
        self.assertEqual(required, ())
        self.assertNotIn(DESKTOP_PACKAGE, required)
        self.assertNotIn(WORKFLOW_PACKAGE, required)

    def test_kedra_has_both(self):
        required = self.required(resolve_preset(self.tree, "kedra"))
        self.assertIn(DESKTOP_PACKAGE, required)
        self.assertIn(WORKFLOW_PACKAGE, required)

    def test_custom_kedra_with_desktop_off_drops_only_the_desktop_package(self):
        required = self.required(
            resolve_state(self.tree, user_state("kedra", {"desktop": False}))
        )
        self.assertNotIn(DESKTOP_PACKAGE, required)
        self.assertIn(WORKFLOW_PACKAGE, required)

    def test_a_refused_override_keeps_the_experience_packages(self):
        # The structural module, not the desktop one: overriding `integration`
        # is refused, so its packages stay.
        required = self.required(
            resolve_state(self.tree, user_state("kedra", {"integration": False}))
        )
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)

    def test_custom_kedra_with_workflow_off_keeps_the_desktop_package(self):
        required = self.required(
            resolve_state(self.tree, user_state("kedra", {"workflow": False}))
        )
        self.assertIn(DESKTOP_PACKAGE, required)
        self.assertNotIn(WORKFLOW_PACKAGE, required)

    def test_disabling_an_unrelated_module_keeps_both(self):
        for other in ("extensions", "terminal", "appearance", "integration"):
            with self.subTest(disabled=other):
                required = self.required(
                    resolve_state(self.tree, user_state("kedra", {other: False}))
                )
                self.assertIn(DESKTOP_PACKAGE, required)
                self.assertIn(WORKFLOW_PACKAGE, required)

    def test_desktop_owns_the_package_and_nothing_else_claims_it(self):
        owners = [
            module_id
            for module_id, module in self.tree.modules.items()
            for entry in module.get("packages", [])
            if entry.get("name") == DESKTOP_PACKAGE
        ]
        self.assertEqual(owners, ["desktop"])
        relations = {
            entry["name"]: entry["relation"]
            for entry in self.tree.modules["desktop"]["packages"]
        }
        self.assertEqual(relations.get(DESKTOP_PACKAGE), "required")

    def test_the_package_exists_in_the_repository(self):
        pkgbuild = REPO_ROOT / DESKTOP_PACKAGE / "PKGBUILD"
        self.assertTrue(pkgbuild.is_file(), f"{pkgbuild} is missing")
        text = pkgbuild.read_text(encoding="utf-8")
        self.assertIn(f"pkgname={DESKTOP_PACKAGE}", text)
        self.assertIn("usr/share/glib-2.0/schemas", text)
        # The schemas it overrides must be real dependencies: overriding a key
        # in an absent schema breaks every other package's schemas at hook time.
        self.assertIn("mutter", text)
        self.assertIn("gsettings-desktop-schemas", text)
        self.assertNotIn("install -Dm644 gschemas.compiled", text)


class NoManagedWriteTests(unittest.TestCase):
    """These defaults must never become something Kedra writes and owns."""

    def setUp(self):
        self.tree = shipped_tree()
        self.state = user_state("kedra", {})
        self.resolution = resolve_state(self.tree, self.state)

    def test_the_generic_planner_plans_zero_writes_for_them(self):
        plan = build_plan(
            tree=self.tree,
            resolution=self.resolution,
            user_state=self.state,
            observed={},
        )
        desktop_entries = [
            entry for entry in plan.entries if entry.module_id == "desktop"
        ]
        self.assertEqual(
            {e.setting_id for e in desktop_entries}, set(EXPECTED_DESKTOP_SETTING_IDS)
        )
        for entry in desktop_entries:
            with self.subTest(setting=entry.setting_id):
                self.assertEqual(entry.decision, "noop")
                self.assertFalse(entry.writes)
                self.assertFalse(entry.requires_confirmation)

    def test_no_managed_record_is_created_even_when_gnome_disagrees(self):
        # The upgrade case that matters: GNOME's value differs from Kedra's, and
        # Kedra still takes no ownership, because a vendor default is not a write.
        observed = {
            "gsettings:org.gnome.mutter:dynamic-workspaces": TypedValue("b", True),
            "gsettings:org.gnome.desktop.wm.preferences:num-workspaces": TypedValue(
                "i", 4
            ),
        }
        plan = build_plan(
            tree=self.tree,
            resolution=self.resolution,
            user_state=self.state,
            observed=observed,
        )
        # Scoped to this module: the shipped tree also contains Terminal's
        # `managed` Super+Enter shortcut, which does write. The claim under test
        # is that a *vendor default* never does.
        desktop_entries = [e for e in plan.entries if e.module_id == "desktop"]
        self.assertEqual([e for e in desktop_entries if e.writes], [])
        for entry in desktop_entries:
            with self.subTest(setting=entry.setting_id):
                self.assertEqual(entry.original_value, None)

    def test_a_user_who_changed_the_value_is_never_asked_about_it(self):
        # A user's explicit dconf value simply wins; Kedra has nothing recorded
        # to compare against, so there is no divergence to detect or confirm.
        observed = {
            "gsettings:org.gnome.desktop.wm.preferences:num-workspaces": TypedValue(
                "i", 9
            ),
        }
        plan = build_plan(
            tree=self.tree,
            resolution=self.resolution,
            user_state=self.state,
            observed=observed,
        )
        desktop_entries = [e for e in plan.entries if e.module_id == "desktop"]
        self.assertEqual([e for e in desktop_entries if e.requires_confirmation], [])
        self.assertEqual([e for e in desktop_entries if e.writes], [])


@unittest.skipUnless(
    SYSTEM_SCHEMA_DIR.is_dir(), "installed GNOME GSettings schemas required"
)
class InstalledSchemaTests(unittest.TestCase):
    """Cross-check the declarations against the schemas actually installed."""

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = desktop_settings(self.tree)

    def _key(self, schema_id: str, key_name: str):
        for path in SYSTEM_SCHEMA_DIR.glob("*.gschema.xml"):
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            for schema in root.findall("schema"):
                if schema.get("id") != schema_id:
                    continue
                for key in schema.findall("key"):
                    if key.get("name") == key_name:
                        return key
        return None

    def test_every_target_schema_key_exists_with_the_declared_type(self):
        # The guard against a manifest built from a remembered key name.
        expected_types = {"b": "b", "i": "i"}
        for setting_id, entry in self.settings.items():
            schema_id = entry["target"]["schema"]
            key_name = entry["target"]["key"]
            with self.subTest(setting=setting_id):
                key = self._key(schema_id, key_name)
                self.assertIsNotNone(
                    key, f"{schema_id}:{key_name} is not an installed GSettings key"
                )
                self.assertEqual(
                    key.get("type"), expected_types[entry["value"]["type"]]
                )

    def test_the_workspace_count_is_inside_gnome_own_range(self):
        key = self._key("org.gnome.desktop.wm.preferences", "num-workspaces")
        self.assertIsNotNone(key)
        rng = key.find("range")
        self.assertIsNotNone(rng, "num-workspaces is expected to declare a range")
        self.assertLessEqual(int(rng.get("min")), KEDRA_WORKSPACE_COUNT)
        self.assertGreaterEqual(int(rng.get("max")), KEDRA_WORKSPACE_COUNT)

    def test_gnome_ships_the_scoped_default_that_has_to_be_beaten(self):
        # Documents the conflict this module exists to resolve. If GNOME stops
        # shipping it, the `:GNOME` half of the override becomes unnecessary
        # rather than wrong -- but the change should be noticed.
        override = SYSTEM_SCHEMA_DIR / GNOME_SHELL_OVERRIDE_FILE
        if not override.is_file():
            self.skipTest(f"{GNOME_SHELL_OVERRIDE_FILE} not installed")
        text = override.read_text(encoding="utf-8")
        for (schema, key), value in GNOME_DESKTOP_SCOPED_DEFAULTS.items():
            with self.subTest(schema=schema, key=key):
                self.assertIn(f"[{schema}:GNOME]", text)
                self.assertIn(f"{key}={'true' if value.data else 'false'}", text)
                # ...and Kedra declares the opposite, which is the whole point.
                declared = EXPECTED_WORKSPACE_MODEL[(schema, key)]
                self.assertNotEqual(declared.data, value.data)


if __name__ == "__main__":
    unittest.main()
