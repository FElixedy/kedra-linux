"""Running `kedraexperiencepackages` more than once in one installer process.

The Experience page can be revisited. A user may resolve Kedra, go back, and
choose Vanilla -- and what the installer then installs must depend only on the
LAST answer.

The property under test is therefore not "the right packages are added" (that is
test_packages.py) but *nothing survives that the last resolve did not ask for*.
The case that matters most is the one with no packages in it at all: Vanilla
resolves to an empty set, and a module that only replaced its operation when it
had something to put there would leave the previous Kedra operation standing.
`packages@online` would then install the Experience into a system whose user
chose vanilla GNOME, and no page, log line or summary would say so.

Every test here drives the real resolver twice against one GlobalStorage, which
is exactly what a re-run is.
"""

from __future__ import annotations

import json
import unittest

from support import InstallerModuleTestCase, write_fake_resolver

SELECTION = "kedraExperienceSelection"
RESOLVED = "kedraExperienceResolvedSelection"
APPLIES = "kedraExperienceAppliesLayer"

KEDRA = {"base_preset": "kedra"}
VANILLA = {"base_preset": "vanilla"}
CUSTOM_SMALLER = {
    "base_preset": "kedra",
    "module_overrides": [{"id": "workflow", "enabled": False}],
}
CUSTOM_SMALLEST = {
    "base_preset": "kedra",
    "module_overrides": [
        {"id": "workflow", "enabled": False},
        {"id": "desktop", "enabled": False},
        {"id": "terminal", "enabled": False},
    ],
}

# An operation belonging to somebody else. Present in every test, because
# "removes its own" is only half the requirement: the other half is that
# netinstall's GNOME group survives untouched.
OTHER = {"install": ["gdm", "gnome-shell", "nautilus"], "source": "netinstall@"}


class RerunTestCase(InstallerModuleTestCase):
    module_name = "kedraexperiencepackages"

    def setUp(self):
        super().setUp()
        self.gs.insert("packageOperations", [dict(OTHER)])

    def resolve(self, selection: dict):
        """One pass of the page + job, as a re-run does it."""
        self.gs.insert(SELECTION, json.dumps(selection))
        return self.module.run()

    def experience_operations(self) -> list:
        return [
            op
            for op in self.package_operations()
            if op.get("source") == "kedraexperiencepackages"
        ]


# ---------------------------------------------------------------------------
# A. Kedra -> Vanilla: the contamination this suite exists for
# ---------------------------------------------------------------------------


class KedraThenVanillaTests(RerunTestCase):
    def rerun(self):
        self.assertIsNone(self.resolve(KEDRA))
        self.assertTrue(self.injected_packages(), "the first pass must have added one")
        self.assertIsNone(self.resolve(VANILLA))

    def test_the_previous_experience_operation_is_removed(self):
        self.rerun()
        self.assertEqual(self.experience_operations(), [])

    def test_no_kedra_package_survives_into_the_transaction(self):
        self.rerun()
        every = [
            name
            for operation in self.package_operations()
            for name in operation.get("install", []) + operation.get("try_install", [])
        ]
        self.assertEqual([n for n in every if n.startswith("kedra-")], [])

    def test_the_final_operations_are_exactly_what_other_modules_asked_for(self):
        self.rerun()
        self.assertEqual(self.package_operations(), [OTHER])

    def test_the_layer_state_ends_up_false(self):
        self.rerun()
        self.assertIs(self.gs.value(APPLIES), False)

    def test_the_recorded_selection_is_the_vanilla_one(self):
        # The state job persists what is here, so a stale Kedra selection would
        # write a profile the user did not choose.
        self.rerun()
        self.assertEqual(json.loads(self.gs.value(RESOLVED))["base_preset"], "vanilla")


# ---------------------------------------------------------------------------
# B. A custom non-empty profile -> Vanilla
# ---------------------------------------------------------------------------


class CustomThenVanillaTests(RerunTestCase):
    def test_the_previous_operation_is_removed(self):
        self.assertIsNone(self.resolve(CUSTOM_SMALLER))
        self.assertTrue(self.injected_packages())
        self.assertIsNone(self.resolve(VANILLA))
        self.assertEqual(self.experience_operations(), [])
        self.assertEqual(self.package_operations(), [OTHER])

    def test_it_holds_for_the_smallest_custom_profile_too(self):
        self.assertIsNone(self.resolve(CUSTOM_SMALLEST))
        self.assertTrue(self.injected_packages())
        self.assertIsNone(self.resolve(VANILLA))
        self.assertEqual(self.injected_packages(), [])
        self.assertIs(self.gs.value(APPLIES), False)


# ---------------------------------------------------------------------------
# C. Kedra -> a smaller custom profile: replaced, never accumulated
# ---------------------------------------------------------------------------


class KedraThenSmallerCustomTests(RerunTestCase):
    def rerun(self):
        self.assertIsNone(self.resolve(KEDRA))
        self.first = list(self.injected_packages())
        self.assertIsNone(self.resolve(CUSTOM_SMALLER))

    def test_there_is_still_exactly_one_operation_of_ours(self):
        self.rerun()
        self.assertEqual(len(self.experience_operations()), 1)

    def test_the_operation_is_the_new_one_and_not_a_union(self):
        self.rerun()
        expected = self.resolve_directly(CUSTOM_SMALLER)["packages"]["required"]
        self.assertEqual(self.injected_packages(), expected)

    def test_a_package_the_new_profile_dropped_is_gone(self):
        self.rerun()
        self.assertIn("kedra-experience-workflow", self.first)
        self.assertNotIn("kedra-experience-workflow", self.injected_packages())

    def test_other_modules_are_still_untouched(self):
        self.rerun()
        self.assertIn(OTHER, self.package_operations())


# ---------------------------------------------------------------------------
# D. Vanilla -> Kedra: the ordinary way round
# ---------------------------------------------------------------------------


class VanillaThenKedraTests(RerunTestCase):
    def test_the_operation_is_added_normally(self):
        self.assertIsNone(self.resolve(VANILLA))
        self.assertEqual(self.experience_operations(), [])
        self.assertIsNone(self.resolve(KEDRA))
        self.assertEqual(len(self.experience_operations()), 1)
        self.assertEqual(
            self.injected_packages(),
            self.resolve_directly(KEDRA)["packages"]["required"],
        )

    def test_the_layer_state_ends_up_true(self):
        self.resolve(VANILLA)
        self.resolve(KEDRA)
        self.assertIs(self.gs.value(APPLIES), True)


# ---------------------------------------------------------------------------
# E. Kedra -> Kedra: idempotent
# ---------------------------------------------------------------------------


class KedraTwiceTests(RerunTestCase):
    def test_nothing_is_duplicated(self):
        self.assertIsNone(self.resolve(KEDRA))
        first = json.dumps(self.package_operations(), sort_keys=True)
        self.assertIsNone(self.resolve(KEDRA))
        self.assertEqual(len(self.experience_operations()), 1)
        self.assertEqual(json.dumps(self.package_operations(), sort_keys=True), first)

    def test_running_it_three_times_changes_nothing_either(self):
        for _unused in range(3):
            self.assertIsNone(self.resolve(KEDRA))
        self.assertEqual(len(self.experience_operations()), 1)


# ---------------------------------------------------------------------------
# F. A resolver failure during a re-run
# ---------------------------------------------------------------------------


class FailedRerunTests(RerunTestCase):
    """The documented behaviour: fail closed, and mutate nothing.

    All validation happens before GlobalStorage is written, so a failed re-run
    has nothing to roll back -- the previous, valid operation is simply still
    there. That is safe because the failure ends the installation: Calamares
    stops on a job that returns an error, so `packages@online` never runs and the
    stale operation is never carried out.

    The alternative -- removing the operation on failure -- would be strictly
    worse. It would turn a resolver that cannot answer into a silent downgrade of
    the user's choice if anything ever chose to continue past the error.
    """

    def use_resolver(self, path):
        self.libcalamares.job.configuration["resolver"] = str(path)

    def test_the_installation_is_stopped(self):
        self.assertIsNone(self.resolve(KEDRA))
        self.use_resolver(write_fake_resolver(self.tmp, stdout="not json"))
        result = self.resolve(VANILLA)
        self.assertIsNotNone(result)

    def test_the_previously_valid_operation_is_left_exactly_as_it_was(self):
        self.assertIsNone(self.resolve(KEDRA))
        before = json.dumps(self.package_operations(), sort_keys=True)
        self.use_resolver(write_fake_resolver(self.tmp, stdout="not json"))
        self.assertIsNotNone(self.resolve(VANILLA))
        self.assertEqual(json.dumps(self.package_operations(), sort_keys=True), before)

    def test_the_recorded_layer_state_is_left_as_it_was(self):
        self.assertIsNone(self.resolve(KEDRA))
        self.use_resolver(write_fake_resolver(self.tmp, stdout="not json"))
        self.assertIsNotNone(self.resolve(VANILLA))
        self.assertIs(self.gs.value(APPLIES), True)

    def test_a_resolver_that_vanished_between_runs_stops_the_installation(self):
        self.assertIsNone(self.resolve(KEDRA))
        self.use_resolver(self.tmp / "no-longer-here")
        result = self.resolve(VANILLA)
        self.assertIsNotNone(result)
        self.assertEqual(len(self.experience_operations()), 1)

    def test_an_invalid_profile_on_the_second_pass_stops_the_installation(self):
        self.assertIsNone(self.resolve(KEDRA))
        result = self.resolve(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "integration", "enabled": False}],
            }
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(self.experience_operations()), 1)


if __name__ == "__main__":
    unittest.main()
