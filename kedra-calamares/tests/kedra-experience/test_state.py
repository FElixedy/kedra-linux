"""`kedraexperiencestate`: recording the chosen profile for the installed user.

Every test builds a target root that looks like what Calamares' `users` step
leaves behind -- an /etc/passwd and a home directory -- and drives the real
`kedra-experience install-state` against it. Nothing here knows the state file's
format, its path or its ownership rules, for the same reason the module does
not: those belong to the Experience runtime.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from support import InstallerModuleTestCase, write_fake_resolver

RESOLVED = "kedraExperienceResolvedSelection"
APPLIES = "kedraExperienceAppliesLayer"

STATE_RELATIVE = ".local/state/kedra/experience-state.toml"


class StateTestCase(InstallerModuleTestCase):
    module_name = "kedraexperiencestate"

    def setUp(self):
        super().setUp()
        self.target = self.tmp / "target"
        (self.target / "etc").mkdir(parents=True)
        (self.target / "home" / "alice").mkdir(parents=True)
        uid, gid = os.getuid(), os.getgid()
        (self.target / "etc" / "passwd").write_text(
            f"root:x:0:0::/root:/bin/bash\nalice:x:{uid}:{gid}::/home/alice:/bin/bash\n",
            encoding="utf-8",
        )
        self.gs.insert("rootMountPoint", str(self.target))
        self.gs.insert("username", "alice")
        self.state_file = self.target / "home" / "alice" / STATE_RELATIVE

    def record(self, selection: dict, applies: bool = True):
        self.gs.insert(RESOLVED, json.dumps(selection))
        self.gs.insert(APPLIES, applies)
        return self.module.run()

    def home_contents(self):
        return sorted(
            str(p.relative_to(self.target / "home" / "alice"))
            for p in (self.target / "home" / "alice").rglob("*")
        )


# ---------------------------------------------------------------------------
# B. Kedra
# ---------------------------------------------------------------------------


class KedraStateTests(StateTestCase):
    def test_it_succeeds(self):
        self.assertIsNone(self.record({"base_preset": "kedra", "module_overrides": []}))

    def test_the_state_file_is_written(self):
        self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertTrue(self.state_file.is_file())

    def test_the_selection_is_what_was_chosen(self):
        self.record({"base_preset": "kedra", "module_overrides": []})
        text = self.state_file.read_text(encoding="utf-8")
        self.assertIn('base_preset = "kedra"', text)

    def test_no_applied_state_is_written(self):
        # The installer applied nothing, so it claims nothing. A fake [applied]
        # block would later be read as "Kedra already set these values", and the
        # ownership layer would preserve user changes that never happened.
        self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertNotIn("[applied]", self.state_file.read_text(encoding="utf-8"))

    def test_no_gnome_setting_is_written_anywhere_in_the_target(self):
        self.record({"base_preset": "kedra", "module_overrides": []})
        for path in (self.target / "home" / "alice").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                for marker in ("org.gnome", "gsettings", "dconf", "<Super>"):
                    self.assertNotIn(marker, text, f"{path} mentions {marker}")

    def test_exactly_one_file_is_created(self):
        self.record({"base_preset": "kedra", "module_overrides": []})
        files = [
            p for p in (self.target / "home" / "alice").rglob("*") if p.is_file()
        ]
        self.assertEqual(files, [self.state_file])

    def test_nothing_outside_the_home_is_touched(self):
        before = sorted(str(p) for p in (self.target / "etc").rglob("*"))
        self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertEqual(sorted(str(p) for p in (self.target / "etc").rglob("*")), before)


# ---------------------------------------------------------------------------
# A. Vanilla
# ---------------------------------------------------------------------------


class VanillaStateTests(StateTestCase):
    def test_it_succeeds(self):
        self.assertIsNone(
            self.record({"base_preset": "vanilla", "module_overrides": []}, applies=False)
        )

    def test_no_state_file_is_written(self):
        self.record({"base_preset": "vanilla", "module_overrides": []}, applies=False)
        self.assertFalse(self.state_file.exists())

    def test_the_home_is_left_completely_empty(self):
        self.record({"base_preset": "vanilla", "module_overrides": []}, applies=False)
        self.assertEqual(self.home_contents(), [])

    def test_a_run_with_no_selection_at_all_does_nothing(self):
        # An installer sequence whose Experience page never ran.
        self.assertIsNone(self.module.run())
        self.assertEqual(self.home_contents(), [])


# ---------------------------------------------------------------------------
# Custom
# ---------------------------------------------------------------------------


class CustomStateTests(StateTestCase):
    def test_only_the_deltas_are_stored(self):
        self.record(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "workflow", "enabled": False}],
            }
        )
        text = self.state_file.read_text(encoding="utf-8")
        self.assertEqual(text.count("[[selection.module_overrides]]"), 1)
        self.assertIn('id = "workflow"', text)

    def test_the_resolved_module_list_is_not_copied(self):
        self.record(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "desktop", "enabled": False}],
            }
        )
        text = self.state_file.read_text(encoding="utf-8")
        for module in ("terminal", "extensions", "appearance", "integration", "extras"):
            self.assertNotIn(module, text)


# ---------------------------------------------------------------------------
# Failure modes -- fail closed
# ---------------------------------------------------------------------------


class StateFailureTests(StateTestCase):
    def use_resolver(self, path) -> None:
        self.libcalamares.job.configuration["resolver"] = str(path)

    def test_a_missing_resolver_stops_the_installation(self):
        self.use_resolver(self.tmp / "gone")
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)
        self.assertIn("missing", result[0].lower())

    def test_a_missing_target_root_stops_the_installation(self):
        self.gs.remove("rootMountPoint")
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)

    def test_a_missing_home_stops_the_installation(self):
        # install-state refuses rather than creating a home of its own, which is
        # what makes the sequence position (after `users`) enforceable.
        import shutil

        shutil.rmtree(self.target / "home" / "alice")
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)
        self.assertIn("not recorded", result[0])

    def test_an_invalid_selection_stops_the_installation(self):
        result = self.record(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "integration", "enabled": False}],
            }
        )
        self.assertIsNotNone(result)
        self.assertFalse(self.state_file.exists())

    def test_a_non_json_result_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="not json"))
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)
        self.assertIn("not valid JSON", result[1])

    def test_an_unsupported_result_version_stops_the_installation(self):
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps({"install_state_schema_version": 42, "written": True}),
            )
        )
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)
        self.assertIn("version mismatch", result[0].lower())

    def test_a_disagreement_about_whether_to_write_stops_the_installation(self):
        # The installer thinks the layer applies; the runtime wrote nothing. One
        # of them is wrong, and continuing would produce a system whose profile
        # nobody can explain.
        self.use_resolver(
            write_fake_resolver(
                self.tmp,
                stdout=json.dumps(
                    {"install_state_schema_version": 1, "written": False, "path": None}
                ),
            )
        )
        result = self.record({"base_preset": "kedra", "module_overrides": []}, applies=True)
        self.assertIsNotNone(result)
        self.assertIn("disagree", result[1])

    def test_a_nonzero_exit_stops_the_installation(self):
        self.use_resolver(write_fake_resolver(self.tmp, stdout="", status=1))
        result = self.record({"base_preset": "kedra", "module_overrides": []})
        self.assertIsNotNone(result)

    def test_an_installation_without_a_user_records_nothing_and_continues(self):
        # No account to record a per-user selection against. Warned, not failed:
        # a system with no user is a legitimate, if unusual, install.
        self.gs.remove("username")
        self.assertIsNone(self.record({"base_preset": "kedra", "module_overrides": []}))
        self.assertFalse(self.state_file.exists())


if __name__ == "__main__":
    unittest.main()
