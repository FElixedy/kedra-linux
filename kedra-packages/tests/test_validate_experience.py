#!/usr/bin/env python3
"""Tests for tools/validate-experience.py.

Stdlib `unittest` only, matching the validator's own no-dependency rule.

Each negative test builds a minimal but otherwise-valid data tree in a temp
directory, breaks exactly one thing, and asserts the validator both fails and
says why. Building the tree rather than checking in dozens of broken fixture
files keeps the failure being tested visible in the test itself.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = REPO_ROOT / "tools" / "validate-experience.py"
DATA_DIR = REPO_ROOT / "kedra-experience" / "data" / "experience"
CUSTOM_EXAMPLE = REPO_ROOT / "docs" / "examples" / "custom-kedra.toml"


def load_validator():
    """Import the validator by path — it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("validate_experience", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via
    # sys.modules[cls.__module__], which fails if the module is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = load_validator()


DESCRIPTOR = """
schema_version = 6

[experience]
layout_version = 1
data_format = "toml"
presets_dir = "presets"
modules_dir = "modules"
default_preset = "kedra"
"""

MODULE_DESKTOP = """
schema_version = 6

[module]
id = "desktop"
name = "Kedra Desktop"
description = "Baseline shell behaviour."
version = "0.1.0"
tier = "core"
user_selectable = false
requires = []
conflicts = []

[[module.packages]]
name = "gnome-shell"
relation = "required"
"""

PRESET_KEDRA = """
schema_version = 6

[preset]
id = "kedra"
name = "Kedra Experience"
description = "Curated GNOME."
version = "0.1.0"
kind = "base"
applies_experience_layer = true

[[preset.modules]]
id = "desktop"
enabled = true
"""


class TreeBuilder:
    """A minimal valid data tree that individual tests then break."""

    def __init__(self, root: Path):
        self.root = root
        (root / "modules").mkdir(parents=True)
        (root / "presets").mkdir(parents=True)
        self.write("experience.toml", DESCRIPTOR)
        self.write("modules/desktop.toml", MODULE_DESKTOP)
        self.write("presets/kedra.toml", PRESET_KEDRA)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.write_text(content.lstrip(), encoding="utf-8")
        return path


def with_module_key(source: str, line: str) -> str:
    """Insert a line into the [module] table.

    Appending to the end of the document would land the key inside the last
    [[module.packages]] table instead, which tests a different thing.
    """
    marker = 'tier = "core"'
    assert marker in source, "fixture no longer contains the insertion point"
    return source.replace(marker, f"{marker}\n{line}", 1)


class ValidatorTestCase(unittest.TestCase):
    def build(self) -> TreeBuilder:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return TreeBuilder(Path(tmp.name))

    def run_validator(self, tree: TreeBuilder) -> validator.Report:
        report = validator.Report()
        validator.validate_tree(tree.root, report)
        return report

    def assertRejects(self, tree: TreeBuilder, needle: str) -> None:
        report = self.run_validator(tree)
        self.assertFalse(report.ok, "expected validation to fail, but it passed")
        messages = " | ".join(m for _, m in report.errors)
        self.assertIn(needle, messages)

    def assertAccepts(self, tree: TreeBuilder) -> None:
        report = self.run_validator(tree)
        self.assertTrue(report.ok, f"unexpected errors: {report.errors}")


class TestShippedData(unittest.TestCase):
    """The manifests this package actually installs must always validate."""

    def test_package_data_is_valid(self):
        report = validator.Report()
        validator.validate_tree(DATA_DIR, report)
        self.assertTrue(report.ok, f"shipped data is invalid: {report.errors}")

    def test_custom_example_is_valid(self):
        report = validator.Report()
        validator.validate_preset(CUSTOM_EXAMPLE, report)
        self.assertTrue(report.ok, f"custom example is invalid: {report.errors}")

    def test_every_initial_module_is_present(self):
        expected = {
            "desktop",
            "workflow",
            "appearance",
            "terminal",
            "extensions",
            "integration",
            "extras",
        }
        found = {p.stem for p in (DATA_DIR / "modules").glob("*.toml")}
        self.assertEqual(expected, found)

    def test_vanilla_preset_applies_no_layer(self):
        report = validator.Report()
        preset = validator.validate_preset(DATA_DIR / "presets" / "vanilla.toml", report)
        self.assertTrue(report.ok, f"vanilla preset is invalid: {report.errors}")
        self.assertIs(preset["applies_experience_layer"], False)
        self.assertEqual(preset.get("modules", []), [])
        self.assertNotIn("module_overrides", preset)


class TestMalformedManifests(ValidatorTestCase):
    def test_unparseable_toml_is_reported(self):
        tree = self.build()
        tree.write("modules/desktop.toml", "[module\nid = ")
        self.assertRejects(tree, "malformed TOML")

    def test_missing_module_table(self):
        tree = self.build()
        tree.write("modules/desktop.toml", "schema_version = 6\n")
        self.assertRejects(tree, "missing required [module] table")

    def test_unknown_key_is_reported(self):
        tree = self.build()
        tree.write("modules/desktop.toml", with_module_key(MODULE_DESKTOP, 'nonsense = "x"'))
        self.assertRejects(tree, "module: unknown key 'nonsense'")


class TestVersionAndSchemaFields(ValidatorTestCase):
    def test_fixtures_track_the_current_schema_version(self):
        """The fixtures in this file hardcode the grammar version.

        Without this, bumping SCHEMA_VERSION makes eight unrelated tests fail
        with 'unsupported schema_version', which reads as a validator bug rather
        than as 'update the fixtures'. One clear failure beats eight confusing
        ones.
        """
        # 99 is deliberate: test_unsupported_schema_version needs a version the
        # validator does not speak.
        deliberately_invalid = {99}
        found = {
            int(match)
            for match in re.findall(
                r"schema_version = (\d+)",
                Path(__file__).read_text(encoding="utf-8"),
            )
        } - deliberately_invalid
        self.assertEqual(
            found,
            {validator.SCHEMA_VERSION},
            f"fixtures in this file declare {sorted(found)}; the validator speaks "
            f"{validator.SCHEMA_VERSION}",
        )

    def test_missing_schema_version(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("schema_version = 6", ""))
        self.assertRejects(tree, "missing required top-level key 'schema_version'")

    def test_unsupported_schema_version(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("schema_version = 6", "schema_version = 99"))
        self.assertRejects(tree, "unsupported schema_version 99")

    def test_missing_module_version(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('version = "0.1.0"\n', ""))
        self.assertRejects(tree, "missing required key 'version'")

    def test_non_semver_module_version(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('version = "0.1.0"', 'version = "1"'))
        self.assertRejects(tree, "not MAJOR.MINOR.PATCH")

    def test_modules_version_independently(self):
        """Two modules at different versions is the normal case, not an error."""
        tree = self.build()
        tree.write(
            "modules/workflow.toml",
            MODULE_DESKTOP.replace('id = "desktop"', 'id = "workflow"')
            .replace('version = "0.1.0"', 'version = "3.2.1"')
            .replace('tier = "core"', 'tier = "optional"'),
        )
        tree.write("presets/kedra.toml", PRESET_KEDRA + '\n[[preset.modules]]\nid = "workflow"\nenabled = true\n')
        self.assertAccepts(tree)

    def test_missing_descriptor(self):
        tree = self.build()
        (tree.root / "experience.toml").unlink()
        self.assertRejects(tree, "missing experience.toml root descriptor")

    def test_descriptor_missing_layout_version(self):
        tree = self.build()
        tree.write("experience.toml", DESCRIPTOR.replace("layout_version = 1\n", ""))
        self.assertRejects(tree, "missing required key 'layout_version'")


class TestDuplicateModuleIds(ValidatorTestCase):
    def test_duplicate_module_id_across_files(self):
        tree = self.build()
        tree.write("modules/desktop-copy.toml", MODULE_DESKTOP)
        self.assertRejects(tree, "duplicate module ID 'desktop'")

    def test_filename_must_match_module_id(self):
        tree = self.build()
        (tree.root / "modules" / "desktop.toml").unlink()
        tree.write("modules/other.toml", MODULE_DESKTOP)
        self.assertRejects(tree, "filename does not match module ID 'desktop'")

    def test_duplicate_module_reference_within_preset(self):
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA + '\n[[preset.modules]]\nid = "desktop"\nenabled = false\n')
        self.assertRejects(tree, "duplicate reference to module 'desktop'")


class TestPresetModuleReferences(ValidatorTestCase):
    def test_preset_referencing_nonexistent_module(self):
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA + '\n[[preset.modules]]\nid = "ghost"\nenabled = true\n')
        self.assertRejects(tree, "references nonexistent module 'ghost'")

    def test_default_preset_must_exist(self):
        tree = self.build()
        tree.write("experience.toml", DESCRIPTOR.replace('default_preset = "kedra"', 'default_preset = "ghost"'))
        self.assertRejects(tree, "default_preset references nonexistent preset 'ghost'")

    def test_module_enabled_flag_is_required(self):
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA.replace("enabled = true", ""))
        self.assertRejects(tree, "'enabled' must be present and a boolean")


class TestDependencyMetadata(ValidatorTestCase):
    def test_invalid_package_relation(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('relation = "required"', 'relation = "suggests"'))
        self.assertRejects(tree, "invalid relation 'suggests'")

    def test_missing_package_relation(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('relation = "required"\n', ""))
        self.assertRejects(tree, "missing required key 'relation'")

    def test_conditional_relation_requires_condition(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('relation = "required"', 'relation = "conditional"'))
        self.assertRejects(tree, "requires a 'condition'")

    def test_condition_without_conditional_relation(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP + '\ncondition = "installed(x)"\n')
        self.assertRejects(tree, "only meaningful for relation 'conditional'")

    def test_all_four_relations_accepted(self):
        tree = self.build()
        tree.write(
            "modules/desktop.toml",
            MODULE_DESKTOP
            + """
[[module.packages]]
name = "a"
relation = "recommended"

[[module.packages]]
name = "b"
relation = "optional"

[[module.packages]]
name = "c"
relation = "conditional"
condition = { type = "package-installed", package = "c" }
""",
        )
        self.assertAccepts(tree)

    def test_module_requires_nonexistent_module(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("requires = []", 'requires = ["ghost"]'))
        self.assertRejects(tree, "references nonexistent module 'ghost'")

    def test_module_cannot_require_itself(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("requires = []", 'requires = ["desktop"]'))
        self.assertRejects(tree, "must not reference the module itself")

    def test_dependency_cycle_is_detected(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("requires = []", 'requires = ["workflow"]'))
        tree.write(
            "modules/workflow.toml",
            MODULE_DESKTOP.replace('id = "desktop"', 'id = "workflow"').replace(
                "requires = []", 'requires = ["desktop"]'
            ),
        )
        self.assertRejects(tree, "dependency cycle")


class TestTierAndKindValues(ValidatorTestCase):
    def test_invalid_module_tier(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('tier = "core"', 'tier = "essential"'))
        self.assertRejects(tree, "invalid module.tier 'essential'")

    def test_every_valid_tier_accepted(self):
        for tier in ("core", "recommended", "optional"):
            with self.subTest(tier=tier):
                tree = self.build()
                tree.write("modules/desktop.toml", MODULE_DESKTOP.replace('tier = "core"', f'tier = "{tier}"'))
                self.assertAccepts(tree)

    def test_invalid_preset_kind(self):
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA.replace('kind = "base"', 'kind = "snapshot"'))
        self.assertRejects(tree, "invalid preset.kind 'snapshot'")

    def test_non_boolean_user_selectable(self):
        tree = self.build()
        tree.write("modules/desktop.toml", MODULE_DESKTOP.replace("user_selectable = false", 'user_selectable = "no"'))
        self.assertRejects(tree, "user_selectable must be a boolean")


class TestActivationStateSeparation(ValidatorTestCase):
    """Activation belongs to presets; a module must not declare its own."""

    def test_module_declaring_enabled_is_rejected(self):
        for key in ("enabled", "default_enabled", "active", "enabled_by_default"):
            with self.subTest(key=key):
                tree = self.build()
                tree.write("modules/desktop.toml", with_module_key(MODULE_DESKTOP, f"{key} = true"))
                self.assertRejects(tree, "activation is declared by presets")

    def test_core_tier_does_not_imply_enabled(self):
        """A core module left disabled by a preset is legal, not contradictory."""
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA.replace("enabled = true", "enabled = false"))
        self.assertAccepts(tree)


class TestVanillaSemantics(ValidatorTestCase):
    def test_vanilla_shape_is_accepted(self):
        tree = self.build()
        tree.write(
            "presets/vanilla.toml",
            """
schema_version = 6

[preset]
id = "vanilla"
name = "Vanilla GNOME"
description = "Upstream GNOME."
version = "0.1.0"
kind = "base"
applies_experience_layer = false
modules = []
""",
        )
        self.assertAccepts(tree)

    def test_disabled_layer_must_not_list_modules(self):
        """Vanilla must not be expressible as 'Kedra with everything off'."""
        tree = self.build()
        tree.write(
            "presets/vanilla.toml",
            """
schema_version = 6

[preset]
id = "vanilla"
name = "Vanilla GNOME"
description = "Upstream GNOME."
version = "0.1.0"
kind = "base"
applies_experience_layer = false

[[preset.modules]]
id = "desktop"
enabled = false
""",
        )
        self.assertRejects(tree, "must be expressed as an empty module set")


class TestDerivedPresets(ValidatorTestCase):
    def _derived(self, body: str) -> str:
        return (
            """
schema_version = 6

[preset]
id = "custom"
name = "Custom Kedra"
description = "Kedra with overrides."
version = "0.1.0"
kind = "derived"
applies_experience_layer = true
"""
            + body
        )

    def test_derived_preset_with_overrides_is_accepted(self):
        tree = self.build()
        tree.write(
            "presets/custom.toml",
            self._derived('base_preset = "kedra"\n\n[[preset.module_overrides]]\nid = "desktop"\nenabled = false\n'),
        )
        self.assertAccepts(tree)

    def test_derived_preset_requires_base(self):
        tree = self.build()
        tree.write("presets/custom.toml", self._derived('\n[[preset.module_overrides]]\nid = "desktop"\nenabled = false\n'))
        self.assertRejects(tree, "requires 'base_preset'")

    def test_derived_preset_base_must_exist(self):
        tree = self.build()
        tree.write("presets/custom.toml", self._derived('base_preset = "ghost"\n'))
        self.assertRejects(tree, "base_preset references nonexistent preset 'ghost'")

    def test_derived_preset_must_not_clone_module_list(self):
        tree = self.build()
        tree.write(
            "presets/custom.toml",
            self._derived('base_preset = "kedra"\n\n[[preset.modules]]\nid = "desktop"\nenabled = true\n'),
        )
        self.assertRejects(tree, "would clone its base instead of inheriting from it")

    def test_base_preset_may_not_carry_overrides(self):
        tree = self.build()
        tree.write("presets/kedra.toml", PRESET_KEDRA + '\n[[preset.module_overrides]]\nid = "desktop"\nenabled = false\n')
        self.assertRejects(tree, "only valid for a derived preset")

    def test_override_referencing_nonexistent_module(self):
        tree = self.build()
        tree.write(
            "presets/custom.toml",
            self._derived('base_preset = "kedra"\n\n[[preset.module_overrides]]\nid = "ghost"\nenabled = true\n'),
        )
        self.assertRejects(tree, "references nonexistent module 'ghost'")


class TestHelpers(unittest.TestCase):
    def test_semver_accepts_valid(self):
        for value in ("0.1.0", "1.0.0", "12.34.56"):
            self.assertTrue(validator.is_semver(value), value)

    def test_semver_rejects_invalid(self):
        for value in ("1", "1.0", "1.0.0-rc1", "01.0.0", "v1.0.0", 1, None):
            self.assertFalse(validator.is_semver(value), repr(value))

    def test_identifier_rules(self):
        for value in ("desktop", "kedra-workflow", "gnome2"):
            self.assertTrue(validator.is_identifier(value), value)
        for value in ("Desktop", "2fast", "with_underscore", "", None):
            self.assertFalse(validator.is_identifier(value), repr(value))


if __name__ == "__main__":
    unittest.main()
