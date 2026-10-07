"""Exact JSON types, in both directions between Calamares and the runtime.

`bool(x)` is not validation. It reads a missing field as False, the string
"false" as True, and 0 as False -- so a resolver that answered
`"applies_experience_layer": "false"` would install the Experience layer and
record nothing, and one that answered `"written": "false"` would let an
installation finish reporting a profile that was never written. Neither failure
has a symptom until somebody logs in.

So every field either module acts on has to have exactly the JSON type it is
documented to have, and anything else stops the installation.

The documents below are the REAL resolver's answer with one field replaced.
Building them by hand would mean maintaining a second idea of the envelope here,
which would drift; taking the real one and breaking exactly one thing keeps each
test about the one thing it names.
"""

from __future__ import annotations

import copy
import json
import unittest

from support import InstallerModuleTestCase, write_fake_resolver

SELECTION = "kedraExperienceSelection"
RESOLVED = "kedraExperienceResolvedSelection"
APPLIES = "kedraExperienceAppliesLayer"

OTHER = {"install": ["gdm", "gnome-shell"], "source": "netinstall@"}


# ---------------------------------------------------------------------------
# The resolve document
# ---------------------------------------------------------------------------


class ResolveEnvelopeTests(InstallerModuleTestCase):
    module_name = "kedraexperiencepackages"

    def setUp(self):
        super().setUp()
        self.gs.insert("packageOperations", [dict(OTHER)])
        self.valid = self.resolve_directly({"base_preset": "kedra"})

    def answer(self, document, status: int = 0):
        """Point the module at a resolver that answers exactly `document`."""
        self.libcalamares.job.configuration["resolver"] = str(
            write_fake_resolver(
                self.tmp, stdout=json.dumps(document, separators=(",", ":")), status=status
            )
        )
        self.gs.insert(SELECTION, json.dumps({"base_preset": "kedra"}))
        return self.module.run()

    def with_field(self, path, value):
        """A copy of the real document with one field set (or removed)."""
        document = copy.deepcopy(self.valid)
        target = document
        for key in path[:-1]:
            target = target[key]
        if value is REMOVED:
            target.pop(path[-1], None)
        else:
            target[path[-1]] = value
        return document

    def expect_rejected(self, path, value):
        with self.subTest(field=".".join(path), value=value):
            self.setUp()
            result = self.answer(self.with_field(path, value))
            self.assertIsNotNone(result, "the installation should have been stopped")
            self.assertEqual(self.injected_packages(), [], "no package may be injected")
            self.assertFalse(self.gs.contains(RESOLVED))
            self.assertEqual(
                self.package_operations(), [OTHER],
                "another module's operation must not be disturbed either",
            )

    # -- the real document is the control -------------------------------------

    def test_the_unmodified_real_document_is_accepted(self):
        # Without this, every test below could be passing for the wrong reason.
        self.assertIsNone(self.answer(self.valid))
        self.assertTrue(self.injected_packages())

    # -- applies_experience_layer ---------------------------------------------

    def test_applies_experience_layer_must_be_a_boolean(self):
        for value in (REMOVED, "false", "true", None, 0, 1, [], {}, "yes"):
            self.expect_rejected(["applies_experience_layer"], value)

    def test_the_string_false_is_not_false(self):
        # The specific coercion this validation exists to stop: bool("false") is
        # True, so the layer would be applied by a resolver that denied it.
        result = self.answer(self.with_field(["applies_experience_layer"], "false"))
        self.assertIsNotNone(result)
        self.assertFalse(self.gs.contains(APPLIES))

    # -- resolve_schema_version -----------------------------------------------

    def test_the_schema_version_must_be_an_integer(self):
        for value in (REMOVED, True, False, "2", 2.0, None, [2]):
            self.expect_rejected(["resolve_schema_version"], value)

    def test_a_boolean_schema_version_is_not_version_one(self):
        # isinstance(True, int) is True in Python, and True == 1.
        result = self.answer(self.with_field(["resolve_schema_version"], True))
        self.assertIsNotNone(result)
        self.assertIn("version mismatch", result[0].lower())

    # -- diagnostics ----------------------------------------------------------

    def test_diagnostics_must_be_an_object(self):
        for value in (REMOVED, None, [], "ok", True):
            self.expect_rejected(["diagnostics"], value)

    def test_diagnostics_ok_must_be_a_boolean(self):
        for value in (REMOVED, "true", "false", None, 1, 0, {}):
            self.expect_rejected(["diagnostics", "ok"], value)

    def test_the_string_true_does_not_make_a_profile_valid(self):
        result = self.answer(self.with_field(["diagnostics", "ok"], "true"))
        self.assertIsNotNone(result)
        self.assertIn("malformed", result[1])

    def test_the_refusal_collections_must_be_lists(self):
        for key in ("refused_overrides", "violations"):
            for value in (REMOVED, None, {}, "none", 0):
                self.expect_rejected(["diagnostics", key], value)

    def test_advisories_must_be_a_list_when_present(self):
        for value in (None, {}, "none"):
            self.expect_rejected(["diagnostics", "advisories"], value)

    def test_a_diagnostics_entry_must_be_an_object(self):
        for value in (["a message"], [None], [["message"]]):
            self.expect_rejected(["diagnostics", "violations"], value)

    def test_a_diagnostics_message_must_be_a_string(self):
        self.expect_rejected(["diagnostics", "violations"], [{"message": 42}])

    # -- packages -------------------------------------------------------------

    def test_the_package_envelope_must_be_present_and_an_object(self):
        for value in (REMOVED, None, [], "kedra-experience"):
            self.expect_rejected(["packages"], value)

    def test_required_must_be_a_list_of_non_empty_strings(self):
        for value in (
            REMOVED, None, "kedra-experience", {}, [""], [None], [1],
            [True], [["kedra-experience"]], [{"name": "kedra-experience"}],
            ["kedra-experience", ""],
        ):
            self.expect_rejected(["packages", "required"], value)

    # -- selection and preset -------------------------------------------------

    def test_the_resolved_selection_must_be_an_object(self):
        for value in (REMOVED, None, [], "kedra", True):
            self.expect_rejected(["selection"], value)

    def test_the_preset_must_be_an_object_naming_itself(self):
        for value in (REMOVED, None, [], "kedra"):
            self.expect_rejected(["preset"], value)
        for value in (REMOVED, None, 1, "", True, ["kedra"]):
            self.expect_rejected(["preset", "id"], value)

    # -- the envelope as a whole ----------------------------------------------

    def test_a_document_that_is_not_an_object_is_rejected(self):
        for text in ("[1, 2, 3]", '"kedra"', "42", "true", "null"):
            with self.subTest(document=text):
                self.setUp()
                self.libcalamares.job.configuration["resolver"] = str(
                    write_fake_resolver(self.tmp, stdout=text)
                )
                self.gs.insert(SELECTION, json.dumps({"base_preset": "kedra"}))
                self.assertIsNotNone(self.module.run())
                self.assertEqual(self.package_operations(), [OTHER])


# ---------------------------------------------------------------------------
# The install-state result
# ---------------------------------------------------------------------------


VALID_RESULT = {
    "install_state_schema_version": 1,
    "written": True,
    "path": "/target/home/alice/.local/state/kedra/experience-state.toml",
    "preset_id": "kedra",
    "selection": {"base_preset": "kedra", "module_overrides": []},
    "applied_recorded": False,
    "uid": 1000,
    "gid": 1000,
}


class InstallStateEnvelopeTests(InstallerModuleTestCase):
    module_name = "kedraexperiencestate"

    def answer(self, result, applies=True, status: int = 0):
        text = result if isinstance(result, str) else json.dumps(result)
        self.libcalamares.job.configuration["resolver"] = str(
            write_fake_resolver(self.tmp, stdout=text, status=status)
        )
        self.gs.insert("rootMountPoint", str(self.tmp))
        self.gs.insert("username", "alice")
        self.gs.insert(RESOLVED, json.dumps({"base_preset": "kedra"}))
        self.gs.insert(APPLIES, applies)
        return self.module.run()

    def with_field(self, key, value):
        result = dict(VALID_RESULT)
        if value is REMOVED:
            result.pop(key, None)
        else:
            result[key] = value
        return result

    def expect_rejected(self, key, value, applies=True):
        with self.subTest(field=key, value=value):
            self.setUp()
            self.assertIsNotNone(self.answer(self.with_field(key, value), applies))

    def test_the_valid_result_is_accepted(self):
        self.assertIsNone(self.answer(VALID_RESULT))

    def test_written_must_be_a_boolean(self):
        for value in (REMOVED, "true", "false", None, 1, 0, [], {}):
            self.expect_rejected("written", value)

    def test_the_string_false_is_not_a_written_file(self):
        # bool("false") is True: without an exact check this would agree with an
        # installer that believed the layer applies, and the installation would
        # be reported as successful with no state file on disk.
        result = self.answer(self.with_field("written", "false"), applies=True)
        self.assertIsNotNone(result)
        self.assertIn("not recorded", result[0])

    def test_the_schema_version_must_be_an_integer(self):
        for value in (REMOVED, True, False, "1", 1.0, None):
            self.expect_rejected("install_state_schema_version", value)

    def test_a_written_result_must_name_the_file_it_wrote(self):
        for value in (REMOVED, None, "", 42, []):
            self.expect_rejected("path", value)

    def test_applied_recorded_must_be_a_boolean_when_present(self):
        for value in ("false", None, 0, 1, [], {}):
            self.expect_rejected("applied_recorded", value)

    def test_an_installer_may_not_report_settings_as_already_applied(self):
        # The installer applies nothing. A runtime claiming otherwise would have
        # created ownership records for values nobody wrote.
        result = self.answer(self.with_field("applied_recorded", True))
        self.assertIsNotNone(result)
        self.assertIn("applied", result[1])

    def test_the_preset_name_and_selection_must_have_their_types(self):
        for value in (None, 1, [], {}):
            self.expect_rejected("preset_id", value)
        for value in (None, 1, [], "kedra"):
            self.expect_rejected("selection", value)

    def test_a_result_that_is_not_an_object_is_rejected(self):
        for text in ("[1, 2, 3]", '"written"', "42", "true", "null"):
            with self.subTest(result=text):
                self.setUp()
                result = self.answer(text)
                self.assertIsNotNone(result)
                self.assertIn("not an object", result[1])

    def test_a_missing_layer_decision_stops_the_installation(self):
        # kedraexperiencepackages validated and inserted this. Its absence means
        # this job is running without the one that resolves, and there is nothing
        # to check the runtime's answer against.
        self.gs.insert("rootMountPoint", str(self.tmp))
        self.gs.insert("username", "alice")
        self.gs.insert(RESOLVED, json.dumps({"base_preset": "kedra"}))
        self.libcalamares.job.configuration["resolver"] = str(
            write_fake_resolver(self.tmp, stdout=json.dumps(VALID_RESULT))
        )
        for value in (None, "true", 1, REMOVED):
            with self.subTest(applies=value):
                if value is REMOVED:
                    self.gs.remove(APPLIES)
                else:
                    self.gs.insert(APPLIES, value)
                self.assertIsNotNone(self.module.run())


class _Removed:
    def __repr__(self):
        return "<field absent>"


REMOVED = _Removed()


if __name__ == "__main__":
    unittest.main()
