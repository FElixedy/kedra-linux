"""The installer's own configuration, checked against the real metadata.

The page fails loudly at install time if `basePreset` names a profile the
Experience metadata does not define -- but discovering that while somebody is
installing is much too late. These tests check the *shipped* configuration
against the *shipped* metadata, so a misconfigured installer cannot be built in
the first place.

The failure this guards against is specifically nasty: a bad `basePreset` used
to leave Vanilla and Kedra working and silently drop Custom, so the installer
looked correct while offering two products instead of three. Only somebody who
already knew a third existed would notice.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from support import InstallerModuleTestCase, MODULES_DIR


def read_conf_value(path: Path, key: str) -> str | None:
    """The value of a top-level scalar key in a Calamares .conf.

    A three-line reader rather than a YAML dependency: these files are checked
    into this repository, the keys are plain scalars, and adding a build-time
    dependency to read four of them would be a poor trade.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(rf'^\s*{re.escape(key)}\s*:\s*"?([^"#]+?)"?\s*(?:#.*)?$', line)
        if match:
            return match.group(1)
    return None


class BasePresetConfigurationTests(InstallerModuleTestCase):
    """`basePreset` must name a profile the resolver actually offers."""

    conf = MODULES_DIR / "kedraexperience" / "kedraexperience.conf"

    def setUp(self):
        super().setUp()
        completed = subprocess.run(
            [str(self.resolver), "profiles", "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.profiles = json.loads(completed.stdout)
        self.base_preset = read_conf_value(self.conf, "basePreset")

    def preset(self, preset_id: str) -> dict | None:
        for entry in self.profiles["presets"]:
            if entry["id"] == preset_id:
                return entry
        return None

    def test_the_config_declares_a_base_preset(self):
        self.assertTrue(self.base_preset, f"no basePreset in {self.conf}")

    def test_the_base_preset_exists_in_the_metadata(self):
        self.assertIsNotNone(
            self.preset(self.base_preset),
            f"basePreset '{self.base_preset}' is not defined by the Experience "
            f"metadata; Custom profiles could not be offered",
        )

    def test_the_base_preset_may_be_offered_to_a_user(self):
        self.assertTrue(
            self.preset(self.base_preset)["user_selectable"],
            f"basePreset '{self.base_preset}' is not user-selectable",
        )

    def test_the_base_preset_applies_the_experience_layer(self):
        # Deriving a Custom profile from a preset that applies no layer is
        # refused by the resolver, so configuring one here would ship an
        # installer whose Custom option could never resolve.
        self.assertTrue(self.preset(self.base_preset)["applies_experience_layer"])

    def test_the_base_preset_offers_at_least_one_selectable_module(self):
        selectable = [
            m for m in self.preset(self.base_preset)["modules"] if m["user_selectable"]
        ]
        self.assertTrue(
            selectable,
            f"'{self.base_preset}' offers no user-selectable module, so a Custom "
            f"profile could not be built from it",
        )

    def test_a_custom_profile_from_this_base_actually_resolves(self):
        # End to end: take the first module the page would offer, switch it off,
        # and confirm the resolver accepts the result. This is the configuration
        # actually working, not merely looking plausible.
        selectable = [
            m for m in self.preset(self.base_preset)["modules"] if m["user_selectable"]
        ]
        selection = {
            "base_preset": self.base_preset,
            "module_overrides": [{"id": selectable[0]["id"], "enabled": False}],
        }
        document = self.resolve_directly(selection)
        self.assertTrue(document["diagnostics"]["ok"], document["diagnostics"])

    def test_non_selectable_modules_are_never_offered(self):
        # The page filters on the flag; this asserts the metadata still has
        # something for it to filter, so the test cannot pass vacuously.
        modules = self.preset(self.base_preset)["modules"]
        self.assertTrue(
            [m for m in modules if not m["user_selectable"]],
            "the metadata marks every module selectable, so this check proves nothing",
        )


class ResolverPathConfigurationTests(InstallerModuleTestCase):
    """All three modules must agree on where the resolver lives."""

    def resolver_paths(self) -> dict[str, str | None]:
        return {
            name: read_conf_value(MODULES_DIR / name / f"{name}.conf", "resolver")
            for name in (
                "kedraexperience",
                "kedraexperiencepackages",
                "kedraexperiencestate",
            )
        }

    def test_every_module_names_a_resolver(self):
        for name, path in self.resolver_paths().items():
            with self.subTest(module=name):
                self.assertTrue(path, f"{name}.conf declares no resolver")

    def test_they_all_name_the_same_one(self):
        # Three different resolvers would mean three different answers to what
        # the Kedra Experience is.
        self.assertEqual(len(set(self.resolver_paths().values())), 1)

    def test_it_is_the_path_the_runtime_package_installs(self):
        self.assertEqual(
            set(self.resolver_paths().values()), {"/usr/bin/kedra-experience"}
        )


class SchemaVersionConfigurationTests(InstallerModuleTestCase):
    """The document versions the installer accepts must match what it gets."""

    def test_the_page_expects_the_profiles_version_the_resolver_emits(self):
        completed = subprocess.run(
            [str(self.resolver), "profiles", "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        emitted = json.loads(completed.stdout)["profiles_schema_version"]
        configured = read_conf_value(
            MODULES_DIR / "kedraexperience" / "kedraexperience.conf",
            "profilesSchemaVersion",
        )
        self.assertEqual(int(configured), emitted)

    def test_the_packages_job_accepts_the_resolve_version_the_resolver_emits(self):
        emitted = self.resolve_directly({"base_preset": "vanilla"})["resolve_schema_version"]
        conf = (MODULES_DIR / "kedraexperiencepackages" / "kedraexperiencepackages.conf").read_text(
            encoding="utf-8"
        )
        match = re.search(r"supportedResolveSchemas:\s*\[([^\]]*)\]", conf)
        self.assertIsNotNone(match, "no supportedResolveSchemas in the config")
        supported = [int(v) for v in match.group(1).replace(" ", "").split(",") if v]
        self.assertIn(emitted, supported)


if __name__ == "__main__":
    unittest.main()
