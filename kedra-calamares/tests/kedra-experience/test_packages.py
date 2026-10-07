"""`kedraexperiencepackages`: turning a profile into target package additions.

The property under test throughout is *the installer adds exactly what the
resolver said, and nothing it decided for itself*. So almost every assertion
compares against `resolve_directly()` -- a second, independent invocation of the
real resolver -- rather than against a package list written out here. A list
written here would be the very duplication this module exists to avoid, and it
would go stale the first time the manifests changed.

The few literal package names that do appear are in tests about *products*
(Vanilla installs no Kedra package; Kedra installs the workspace override), where
naming them is the point of the test.
"""

from __future__ import annotations

import json
import unittest

from support import InstallerModuleTestCase, write_fake_resolver

SELECTION = "kedraExperienceSelection"
RESOLVED = "kedraExperienceResolvedSelection"
APPLIES = "kedraExperienceAppliesLayer"


class PackagesTestCase(InstallerModuleTestCase):
    module_name = "kedraexperiencepackages"

    def run_with(self, selection: dict):
        self.set_selection(SELECTION, selection)
        return self.module.run()


# ---------------------------------------------------------------------------
# A. Vanilla
# ---------------------------------------------------------------------------


class VanillaTests(PackagesTestCase):
    def test_it_succeeds(self):
        self.assertIsNone(self.run_with({"base_preset": "vanilla"}))

    def test_no_experience_package_is_injected(self):
        self.run_with({"base_preset": "vanilla"})
        self.assertEqual(self.injected_packages(), [])

    def test_no_package_operation_is_added_at_all(self):
        # Not "an empty install operation" -- nothing.
        self.run_with({"base_preset": "vanilla"})
        self.assertEqual(
            [op for op in self.package_operations()
             if op.get("source") == "kedraexperiencepackages"],
            [],
        )

    def test_no_kedra_package_reaches_the_transaction(self):
        self.run_with({"base_preset": "vanilla"})
        every = [
            name
            for operation in self.package_operations()
            for name in operation.get("install", []) + operation.get("try_install", [])
        ]
        self.assertEqual([n for n in every if n.startswith("kedra-")], [])

    def test_it_records_that_no_layer_applies(self):
        self.run_with({"base_preset": "vanilla"})
        self.assertFalse(self.gs.value(APPLIES))

    def test_an_existing_gnome_group_is_left_untouched(self):
        gnome = {"install": ["gdm", "gnome-shell", "nautilus"], "source": "netinstall@"}
        self.gs.insert("packageOperations", [dict(gnome)])
        self.run_with({"base_preset": "vanilla"})
        self.assertEqual(self.package_operations(), [gnome])


# ---------------------------------------------------------------------------
# B. Kedra
# ---------------------------------------------------------------------------


class KedraTests(PackagesTestCase):
    selection = {"base_preset": "kedra"}

    def test_it_succeeds(self):
        self.assertIsNone(self.run_with(self.selection))

    def test_the_injected_set_is_exactly_what_the_resolver_required(self):
        self.run_with(self.selection)
        expected = self.resolve_directly(self.selection)["packages"]["required"]
        self.assertEqual(self.injected_packages(), expected)

    def test_the_experience_packages_are_injected(self):
        self.run_with(self.selection)
        injected = self.injected_packages()
        for name in (
            "kedra-experience",
            "kedra-experience-runtime",
            "kedra-experience-desktop",
            "kedra-experience-workflow",
        ):
            with self.subTest(package=name):
                self.assertIn(name, injected)

    def test_non_kedra_requirements_come_through_too(self):
        self.run_with(self.selection)
        injected = self.injected_packages()
        self.assertIn("gnome-console", injected)
        self.assertIn("gnome-shell", injected)

    def test_only_required_packages_are_injected(self):
        # Recommended and optional relations are decisions to surface, not to act
        # on silently. None of them may reach the transaction.
        self.run_with(self.selection)
        document = self.resolve_directly(self.selection)
        injected = set(self.injected_packages())
        for relation in ("recommended", "optional"):
            for name in document["packages"][relation]:
                if name not in document["packages"]["required"]:
                    with self.subTest(relation=relation, package=name):
                        self.assertNotIn(name, injected)

    def test_the_operation_is_labelled_with_its_source(self):
        # So a re-run replaces it rather than stacking, and so a reader of
        # GlobalStorage can tell who asked for what.
        self.run_with(self.selection)
        sources = [op.get("source") for op in self.package_operations()]
        self.assertEqual(sources.count("kedraexperiencepackages"), 1)

    def test_running_twice_does_not_duplicate_the_operation(self):
        self.run_with(self.selection)
        self.module.run()
        sources = [op.get("source") for op in self.package_operations()]
        self.assertEqual(sources.count("kedraexperiencepackages"), 1)

    def test_it_appends_rather_than_replacing_other_package_lists(self):
        gnome = {"install": ["gdm", "gnome-shell"], "source": "netinstall@"}
        self.gs.insert("packageOperations", [dict(gnome)])
        self.run_with(self.selection)
        self.assertIn(gnome, self.package_operations())
        self.assertTrue(self.injected_packages())

    def test_a_package_already_in_the_gnome_group_is_not_filtered_out(self):
        # Union, not semantic deduplication. pacman installs a name requested
        # twice once; filtering here would mean holding an opinion about what the
        # other list contains.
        self.gs.insert(
            "packageOperations",
            [{"install": ["gnome-shell", "gdm"], "source": "netinstall@"}],
        )
        self.run_with(self.selection)
        self.assertIn("gnome-shell", self.injected_packages())

    def test_it_records_the_resolved_selection_for_the_state_step(self):
        self.run_with(self.selection)
        recorded = json.loads(self.gs.value(RESOLVED))
        self.assertEqual(recorded["base_preset"], "kedra")
        self.assertEqual(recorded["module_overrides"], [])

    def test_it_records_that_the_layer_applies(self):
        self.run_with(self.selection)
        self.assertTrue(self.gs.value(APPLIES))


# ---------------------------------------------------------------------------
# C, D, E. Custom profiles
# ---------------------------------------------------------------------------


class CustomTests(PackagesTestCase):
    def custom(self, module_id: str, enabled: bool = False) -> dict:
        return {
            "base_preset": "kedra",
            "module_overrides": [{"id": module_id, "enabled": enabled}],
        }

    # -- C. desktop=false -------------------------------------------------

    def test_desktop_off_drops_the_desktop_package(self):
        self.assertIsNone(self.run_with(self.custom("desktop")))
        self.assertNotIn("kedra-experience-desktop", self.injected_packages())

    def test_desktop_off_keeps_the_workflow_package(self):
        self.run_with(self.custom("desktop"))
        self.assertIn("kedra-experience-workflow", self.injected_packages())

    def test_desktop_off_keeps_the_experience_core_and_runtime(self):
        self.run_with(self.custom("desktop"))
        injected = self.injected_packages()
        self.assertIn("kedra-experience", injected)
        self.assertIn("kedra-experience-runtime", injected)

    def test_desktop_off_still_carries_the_advisory(self):
        # The advisory is information about a valid profile. It must not stop the
        # installation, and it must still be there to be shown.
        document = self.resolve_directly(self.custom("desktop"))
        self.assertTrue(document["diagnostics"]["ok"])
        self.assertEqual(
            [a["code"] for a in document["diagnostics"]["advisories"]],
            ["works-best-with-disabled"],
        )
        self.assertIsNone(self.run_with(self.custom("desktop")))

    def test_desktop_off_matches_the_resolver_exactly(self):
        self.run_with(self.custom("desktop"))
        self.assertEqual(
            self.injected_packages(),
            self.resolve_directly(self.custom("desktop"))["packages"]["required"],
        )

    # -- D. workflow=false ------------------------------------------------

    def test_workflow_off_drops_the_workflow_package(self):
        self.assertIsNone(self.run_with(self.custom("workflow")))
        self.assertNotIn("kedra-experience-workflow", self.injected_packages())

    def test_workflow_off_keeps_the_desktop_package(self):
        self.run_with(self.custom("workflow"))
        self.assertIn("kedra-experience-desktop", self.injected_packages())

    def test_workflow_off_matches_the_resolver_exactly(self):
        self.run_with(self.custom("workflow"))
        self.assertEqual(
            self.injected_packages(),
            self.resolve_directly(self.custom("workflow"))["packages"]["required"],
        )

    # -- E. terminal=false ------------------------------------------------

    def test_terminal_off_drops_the_terminal_package(self):
        self.assertIsNone(self.run_with(self.custom("terminal")))
        self.assertNotIn("gnome-console", self.injected_packages())

    def test_terminal_off_drops_the_application_contract(self):
        # The package and the contract go together, and both are the resolver's
        # answer rather than something the installer knows.
        document = self.resolve_directly(self.custom("terminal"))
        self.assertEqual(document["applications"], [])

    def test_terminal_off_keeps_everything_else(self):
        self.run_with(self.custom("terminal"))
        injected = self.injected_packages()
        for name in (
            "kedra-experience",
            "kedra-experience-runtime",
            "kedra-experience-desktop",
            "kedra-experience-workflow",
        ):
            with self.subTest(package=name):
                self.assertIn(name, injected)

    def test_the_deltas_are_recorded_for_persistence(self):
        self.run_with(self.custom("workflow"))
        recorded = json.loads(self.gs.value(RESOLVED))
        self.assertEqual(
            recorded["module_overrides"], [{"id": "workflow", "enabled": False}]
        )


# ---------------------------------------------------------------------------
# F. An invalid selection
# ---------------------------------------------------------------------------


class InvalidSelectionTests(PackagesTestCase):
    invalid = {
        "base_preset": "kedra",
        "module_overrides": [{"id": "integration", "enabled": False}],
    }

    def test_the_installation_is_stopped(self):
        result = self.run_with(self.invalid)
        self.assertIsNotNone(result)
        title, message = result
        self.assertIn("not valid", title)
        self.assertIn("integration", message)

    def test_no_package_is_injected(self):
        self.run_with(self.invalid)
        self.assertEqual(self.injected_packages(), [])

    def test_no_partial_package_set_is_left_behind(self):
        gnome = {"install": ["gdm"], "source": "netinstall@"}
        self.gs.insert("packageOperations", [dict(gnome)])
        self.run_with(self.invalid)
        self.assertEqual(self.package_operations(), [gnome])

    def test_nothing_is_recorded_for_the_state_step(self):
        self.run_with(self.invalid)
        self.assertFalse(self.gs.contains(RESOLVED))

    def test_a_module_that_vanished_between_listing_and_resolving(self):
        # The page offered it; the manifests no longer have it. The resolver
        # refuses, and so does the installation.
        result = self.run_with(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "no-such-module", "enabled": False}],
            }
        )
        self.assertIsNotNone(result)
        self.assertEqual(self.injected_packages(), [])

    def test_an_unknown_preset_stops_the_installation(self):
        result = self.run_with({"base_preset": "not-a-preset"})
        self.assertIsNotNone(result)
        self.assertEqual(self.injected_packages(), [])


# ---------------------------------------------------------------------------
# G. Resolver failure modes -- fail closed, every one
# ---------------------------------------------------------------------------


class ResolverFailureTests(PackagesTestCase):
    def use_resolver(self, path) -> None:
        self.libcalamares.job.configuration["resolver"] = str(path)

    def expect_failure(self, selection=None):
        result = self.run_with(selection or {"base_preset": "kedra"})
        self.assertIsNotNone(result, "the installation should have been stopped")
        self.assertEqual(self.injected_packages(), [], "no package may be injected")
        self.assertFalse(self.gs.contains("kedraExperienceResolvedSelection"))
        return result

    def test_a_missing_resolver_stops_the_installation(self):
        self.use_resolver(self.tmp / "does-not-exist")
        title, message = self.expect_failure()
        self.assertIn("missing", title.lower())
        self.assertIn("kedra-experience-runtime", message)

    def test_output_that_is_not_json_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="this is not json"))
        _, message = self.expect_failure()
        self.assertIn("not valid JSON", message)

    def test_json_that_is_not_an_object_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="[1, 2, 3]"))
        _, message = self.expect_failure()
        self.assertIn("unexpected shape", message)

    def test_an_unsupported_document_version_stops_the_installation(self):
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps({"resolve_schema_version": 99, "packages": {"required": []}}),
            )
        )
        title, message = self.expect_failure()
        self.assertIn("version mismatch", title.lower())
        self.assertIn("99", message)

    def test_exit_one_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="", status=1))
        title, _ = self.expect_failure()
        self.assertIn("could not be resolved", title)

    def test_exit_two_stops_the_installation(self):
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps(
                    {
                        "resolve_schema_version": 2,
                        "packages": {"required": ["something"]},
                        "diagnostics": {
                            "ok": False,
                            "violations": [],
                            "refused_overrides": [{"message": "refused for a reason"}],
                        },
                    }
                ),
                status=2,
            )
        )
        _, message = self.expect_failure()
        self.assertIn("refused for a reason", message)

    def test_an_unexpected_exit_status_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="{}", status=77))
        _, message = self.expect_failure()
        self.assertIn("77", message)

    def test_diagnostics_ok_false_stops_the_installation_even_at_exit_zero(self):
        # A resolver that says "fine" in its exit status and "not fine" in its
        # document is not to be believed on the exit status.
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps(
                    {
                        "resolve_schema_version": 2,
                        "packages": {"required": ["something"]},
                        "diagnostics": {"ok": False, "violations": [], "refused_overrides": []},
                    }
                ),
                status=0,
            )
        )
        self.expect_failure()

    def test_missing_diagnostics_stops_the_installation(self):
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps(
                    {"resolve_schema_version": 2, "packages": {"required": ["x"]}}
                ),
            )
        )
        _, message = self.expect_failure()
        self.assertIn("no diagnostics", message)

    def test_a_malformed_package_list_stops_the_installation(self):
        for required in ("not-a-list", [1, 2], ["", "ok"], None):
            with self.subTest(required=required):
                self.setUp()
                self.use_resolver(
                    write_fake_resolver(
                        self.tmp,
                        stdout=json.dumps(
                            {
                                "resolve_schema_version": 2,
                                "packages": {"required": required},
                                "diagnostics": {
                                    "ok": True, "violations": [], "refused_overrides": []
                                },
                            }
                        ),
                    )
                )
                _, message = self.expect_failure()
                self.assertIn("malformed", message)

    def test_a_missing_package_set_stops_the_installation(self):
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps(
                    {
                        "resolve_schema_version": 2,
                        "diagnostics": {"ok": True, "violations": [], "refused_overrides": []},
                    }
                ),
            )
        )
        _, message = self.expect_failure()
        self.assertIn("no package set", message)

    def test_an_empty_selection_string_stops_the_installation(self):
        self.gs.insert(SELECTION, "   ")
        result = self.module.run()
        self.assertIsNotNone(result)
        self.assertEqual(self.injected_packages(), [])


class NoSelectionTests(PackagesTestCase):
    def test_an_installer_without_the_experience_page_does_nothing(self):
        # A sequence that never showed the page has nothing to resolve. That is
        # not an error, and it adds nothing -- which is a Vanilla target.
        self.assertIsNone(self.module.run())
        self.assertEqual(self.package_operations(), [])


if __name__ == "__main__":
    unittest.main()
