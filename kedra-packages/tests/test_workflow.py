"""Tests for the Kedra Workflow v1 keymap itself.

What this file guards is the *product*, not the plumbing: the shortcuts Kedra
ships, the GNOME defaults it takes away, the settings it deliberately does not
declare, and the fact that none of it is written into anybody's dconf.

The expectations live in `support.EXPECTED_KEYMAP` and are written out by hand.
Deriving them from the manifest would make every assertion here a tautology.

Nothing in this file reads or writes live GSettings or dconf. The one test that
consults the system reads *schema definitions* -- installed, read-only package
data -- and is skipped where GNOME is not installed.
"""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from support import (
    DATA_DIR,
    DISPLACED_GNOME_DEFAULTS,
    EXPECTED_KEYMAP,
    EXPECTED_WORKFLOW_SETTING_IDS,
    REPO_ROOT,
    normalise_binding,
)

from kedra_experience import manifests
from kedra_experience.packages import required_packages
from kedra_experience.resolve import resolve_preset

SYSTEM_SCHEMA_DIR = Path("/usr/share/glib-2.0/schemas")

WORKFLOW_PACKAGE = "kedra-experience-workflow"

# Schemas whose keys are keyboard shortcuts. Used to build the effective
# binding map when checking for clashes against installed GNOME defaults.
KEYBINDING_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
)

_QUOTED = re.compile('"([^"]*)"' + "|'([^']*)'")


def shipped_tree() -> manifests.Tree:
    report = manifests.Report()
    tree = manifests.load_tree(DATA_DIR, report)
    assert report.ok, report.rendered()
    return tree


def workflow_settings(tree) -> dict[str, dict]:
    return {
        entry["id"]: entry
        for entry in tree.modules["workflow"].get("settings", [])
        if isinstance(entry, dict)
    }


def schema_defaults() -> dict[tuple[str, str], tuple[str, ...]]:
    """Every shortcut GNOME binds by default, from the installed schema XML.

    Read-only introspection of package data. No session, no dconf, no gsettings
    process -- so this can never observe or disturb the running user.
    """
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    for path in sorted(SYSTEM_SCHEMA_DIR.glob("*.gschema.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for schema in root.findall("schema"):
            schema_id = schema.get("id")
            if schema_id not in KEYBINDING_SCHEMAS:
                continue
            for key in schema.findall("key"):
                if key.get("type") != "as":
                    continue
                default = key.find("default")
                if default is None or not default.text:
                    continue
                literals = tuple(
                    a or b for a, b in _QUOTED.findall(default.text) if (a or b)
                )
                found[(schema_id, key.get("name"))] = literals
    return found


class KeymapTests(unittest.TestCase):
    """The four behaviour groups, exactly as declared."""

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = workflow_settings(self.tree)

    def test_declares_exactly_the_expected_settings(self):
        self.assertEqual(set(self.settings), set(EXPECTED_WORKFLOW_SETTING_IDS))

    def test_close_window_is_super_q(self):
        entry = self.settings["workflow-close-window"]
        self.assertEqual(
            entry["target"],
            {"schema": "org.gnome.desktop.wm.keybindings", "key": "close"},
        )
        self.assertEqual(entry["value"], {"type": "as", "data": ["<Super>q", "<Alt>F4"]})

    def test_switch_to_workspace_bindings(self):
        for n in range(1, 6):
            with self.subTest(workspace=n):
                entry = self.settings[f"workflow-switch-workspace-{n}"]
                self.assertEqual(
                    entry["target"],
                    {
                        "schema": "org.gnome.desktop.wm.keybindings",
                        "key": f"switch-to-workspace-{n}",
                    },
                )
                self.assertIn(f"<Super>{n}", entry["value"]["data"])

    def test_move_to_workspace_bindings(self):
        for n in range(1, 6):
            with self.subTest(workspace=n):
                entry = self.settings[f"workflow-move-workspace-{n}"]
                self.assertEqual(
                    entry["target"],
                    {
                        "schema": "org.gnome.desktop.wm.keybindings",
                        "key": f"move-to-workspace-{n}",
                    },
                )
                self.assertIn(f"<Shift><Super>{n}", entry["value"]["data"])

    def test_every_declaration_matches_the_expected_keymap(self):
        declared = {
            (e["target"]["schema"], e["target"]["key"]): tuple(e["value"]["data"])
            for e in self.settings.values()
        }
        self.assertEqual(declared, {k: tuple(v) for k, v in EXPECTED_KEYMAP.items()})

    def test_every_setting_is_a_vendor_default(self):
        # policy 'default' is what keeps this out of the user's dconf. A managed
        # entry here would mean Kedra writes a keybinding, which v1 must not do.
        for setting_id, entry in self.settings.items():
            with self.subTest(setting=setting_id):
                self.assertEqual(entry["policy"], "default")
                self.assertEqual(entry["backend"], "gsettings")
                self.assertTrue(entry["description"].strip())
                self.assertEqual(entry["introduced_in"], "0.3.0")

    def test_upstream_bindings_are_retained_where_nothing_required_taking_them(self):
        # Alt+F4 and Super+Home still work. Kedra adds a shortcut; it removes an
        # upstream one only where the new keymap genuinely collides.
        self.assertIn("<Alt>F4", self.settings["workflow-close-window"]["value"]["data"])
        self.assertIn(
            "<Super>Home",
            self.settings["workflow-switch-workspace-1"]["value"]["data"],
        )
        self.assertIn(
            "<Shift><Super>Home",
            self.settings["workflow-move-workspace-1"]["value"]["data"],
        )


class ConflictTests(unittest.TestCase):
    """No combination may end up claimed by two actions."""

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = workflow_settings(self.tree)

    def test_kedra_declares_no_duplicate_combination(self):
        owners: dict[str, str] = {}
        for setting_id, entry in sorted(self.settings.items()):
            for binding in entry["value"]["data"]:
                combination = normalise_binding(binding)
                previous = owners.get(combination)
                self.assertIsNone(
                    previous,
                    f"{binding} is claimed by both '{previous}' and '{setting_id}'",
                )
                owners[combination] = setting_id

    def test_every_displaced_gnome_default_is_explicitly_neutralised(self):
        # Knowing what was taken is not enough; the taking must happen through
        # the same declarative layer, so `pacman -R` gives it back.
        declared = {
            (e["target"]["schema"], e["target"]["key"]): tuple(e["value"]["data"])
            for e in self.settings.values()
        }
        for target, upstream in DISPLACED_GNOME_DEFAULTS.items():
            with self.subTest(target=target):
                self.assertIn(
                    target,
                    declared,
                    f"{target} carries the upstream default {upstream} that Kedra "
                    f"claims, but nothing releases it",
                )
                self.assertEqual(declared[target], ())

    def test_neutralised_keys_are_only_those_kedra_actually_claims(self):
        # Clearing a GNOME default Kedra does not need would be gratuitous.
        cleared = {
            (e["target"]["schema"], e["target"]["key"])
            for e in self.settings.values()
            if not e["value"]["data"]
        }
        self.assertEqual(cleared, set(DISPLACED_GNOME_DEFAULTS))


@unittest.skipUnless(
    SYSTEM_SCHEMA_DIR.is_dir(), "installed GNOME GSettings schemas required"
)
class InstalledSchemaTests(unittest.TestCase):
    """Cross-check the declarations against the schemas actually installed."""

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = workflow_settings(self.tree)
        self.defaults = schema_defaults()
        if not self.defaults:
            self.skipTest("no keybinding schemas found")

    def test_every_target_schema_and_key_exists(self):
        # The guard against a manifest built from a guessed key name.
        for setting_id, entry in self.settings.items():
            target = (entry["target"]["schema"], entry["target"]["key"])
            with self.subTest(setting=setting_id):
                self.assertIn(
                    target,
                    self.defaults,
                    f"{target[0]}:{target[1]} is not an 'as' key in the installed schemas",
                )

    def test_no_unaccounted_clash_with_an_installed_gnome_default(self):
        """Every combination Kedra claims is either free, or explicitly released."""
        claimed: dict[str, str] = {}
        for entry in self.settings.values():
            for binding in entry["value"]["data"]:
                claimed[normalise_binding(binding)] = (
                    f"{entry['target']['schema']}:{entry['target']['key']}"
                )

        kedra_targets = {
            (e["target"]["schema"], e["target"]["key"]) for e in self.settings.values()
        }

        for target, bindings in self.defaults.items():
            if target in kedra_targets:
                continue  # Kedra replaces this key's value outright.
            for binding in bindings:
                combination = normalise_binding(binding)
                if combination not in claimed:
                    continue
                self.assertIn(
                    target,
                    DISPLACED_GNOME_DEFAULTS,
                    f"{binding} is claimed by Kedra ({claimed[combination]}) and by "
                    f"the GNOME default {target[0]}:{target[1]}, and is not in the "
                    f"declared displacement list",
                )

    def test_displacement_list_matches_the_installed_upstream_defaults(self):
        # If GNOME ever changes what owns Super+1..5, this fails rather than the
        # keymap silently clearing the wrong key.
        for target, upstream in DISPLACED_GNOME_DEFAULTS.items():
            with self.subTest(target=target):
                self.assertEqual(self.defaults.get(target), upstream)


class SuperEnterTests(unittest.TestCase):
    """Super+Enter is a documented blocker, and must stay one until it is not.

    GNOME 50 has no native terminal-launch keybinding key, so the shortcut needs
    a custom keybinding: a relocatable schema instance at a per-shortcut dconf
    path, which cannot be a vendor default (see test_vendor_defaults.py). That
    would make it the first `policy = "managed"` setting.

    Kedra Terminal v1 resolved the *product* half -- the tree now names a
    terminal, as `role = "terminal"` / org.gnome.Console.desktop -- so what is
    left is mechanism. It also decided the shortcut belongs to the `terminal`
    module rather than here, since it launches the application that module
    nominates.

    These tests fail the moment someone half-lands it.
    """

    def setUp(self):
        self.tree = shipped_tree()
        self.settings = workflow_settings(self.tree)
        self.doc = (REPO_ROOT / "docs" / "kedra-workflow.md").read_text(encoding="utf-8")

    def test_no_custom_keybinding_setting_is_declared(self):
        for setting_id, entry in self.settings.items():
            with self.subTest(setting=setting_id):
                self.assertNotIn("custom-keybinding", entry["target"]["schema"])
                self.assertNotEqual(
                    entry["target"]["schema"],
                    "org.gnome.settings-daemon.plugins.media-keys",
                )

    def test_no_terminal_binary_is_hardcoded_anywhere_in_the_tree(self):
        # Kedra names its terminal by desktop ID, in the terminal module's
        # application contract. A *binary* in a declared value would be the
        # wrong shape in the wrong layer, whichever module wrote it.
        for module_id, module in self.tree.modules.items():
            for entry in module.get("settings", []):
                if not isinstance(entry, dict):
                    continue
                data = entry.get("value", {}).get("data")
                text = " ".join(data) if isinstance(data, list) else str(data)
                for binary in ("kgx", "gnome-terminal", "blackbox", "xdg-terminal-exec"):
                    with self.subTest(module=module_id, binary=binary):
                        self.assertNotIn(binary, text)

    def test_no_workflow_declaration_binds_return(self):
        """Not merely "no custom-keybinding schema" -- no Return binding at all.

        A Workflow setting that bound Return through some other key would be the
        same layering mistake by a different route.
        """
        for setting_id, entry in self.settings.items():
            data = entry.get("value", {}).get("data")
            text = " ".join(data) if isinstance(data, list) else str(data)
            with self.subTest(setting=setting_id):
                self.assertNotIn("Return", text)
                self.assertNotIn("KP_Enter", text)

    def test_the_split_is_documented(self):
        for expected in (
            "Super+Enter",
            "custom-keybinding",
            "belongs to the\n`terminal` module",
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, self.doc)

    def test_the_document_no_longer_calls_it_blocked(self):
        """The claim changed, so the test that pinned the old claim changes too.

        Left as its own assertion rather than deleted: "Super+Enter is blocked"
        appearing again would mean somebody reverted the shortcut without
        updating the reasoning, which is exactly the drift these tests exist to
        catch.
        """
        self.assertNotIn("Super+Enter — blocked", self.doc)
        self.assertNotIn("not shipped anywhere yet", self.doc)

    @unittest.skipUnless(
        SYSTEM_SCHEMA_DIR.is_dir(), "installed GNOME GSettings schemas required"
    )
    def test_gnome_still_has_no_native_terminal_launch_key(self):
        """The premise of the whole design. If GNOME grows one, revisit it.

        A native terminal-launch key would be an ordinary key in a fixed-path
        schema, expressible as a vendor default -- which would make Kedra's only
        `managed` setting unnecessary.
        """
        media_keys = SYSTEM_SCHEMA_DIR / (
            "org.gnome.settings-daemon.plugins.media-keys.gschema.xml"
        )
        if not media_keys.is_file():
            self.skipTest("gnome-settings-daemon schemas not installed")

        root = ET.parse(media_keys).getroot()
        for schema in root.findall("schema"):
            if schema.get("id") != "org.gnome.settings-daemon.plugins.media-keys":
                continue
            names = {key.get("name") for key in schema.findall("key")}
            self.assertNotIn("terminal", names)
            break
        else:
            self.fail("media-keys schema not found in its own schema file")


class PackageWiringTests(unittest.TestCase):
    """The module must actually pull in the package that delivers its defaults."""

    def setUp(self):
        self.tree = shipped_tree()

    def test_workflow_requires_the_defaults_package(self):
        relations = {
            entry["name"]: entry["relation"]
            for entry in self.tree.modules["workflow"].get("packages", [])
        }
        self.assertEqual(relations.get(WORKFLOW_PACKAGE), "required")

    def test_no_other_module_claims_the_defaults_package(self):
        # One owner, so disabling workflow genuinely drops the requirement.
        owners = [
            module_id
            for module_id, module in self.tree.modules.items()
            for entry in module.get("packages", [])
            if entry.get("name") == WORKFLOW_PACKAGE
        ]
        self.assertEqual(owners, ["workflow"])

    def test_kedra_requires_it_and_vanilla_does_not(self):
        self.assertIn(
            WORKFLOW_PACKAGE,
            required_packages(self.tree, resolve_preset(self.tree, "kedra")),
        )
        self.assertEqual(
            required_packages(self.tree, resolve_preset(self.tree, "vanilla")), ()
        )

    def test_the_package_exists_in_the_repository(self):
        pkgbuild = REPO_ROOT / WORKFLOW_PACKAGE / "PKGBUILD"
        self.assertTrue(pkgbuild.is_file(), f"{pkgbuild} is missing")
        text = pkgbuild.read_text(encoding="utf-8")
        self.assertIn(f"pkgname={WORKFLOW_PACKAGE}", text)
        # It must install a vendor override, and must not ship compiled schemas.
        self.assertIn("usr/share/glib-2.0/schemas", text)
        self.assertNotIn("install -Dm644 gschemas.compiled", text)


if __name__ == "__main__":
    unittest.main()
