"""Kedra Terminal v1 -- what the shipped `terminal` module actually claims.

The product decisions this file pins down, each of which was a deliberate
"no" that a later change could quietly undo:

* the Kedra terminal is GNOME Console, named as a desktop ID;
* Terminal v1 ships *zero* GSettings overrides, because every candidate key in
  org.gnome.Console was reviewed and upstream was judged right;
* Terminal owns no shell, prompt, font, palette, alias or dotfile;
* an enabled Terminal module requires gnome-console -- and a profile that does
  not enable it does not, however many GNOME packages happen to be installed on
  the machine running these tests.

Nothing here reads live GSettings, launches an application or consults a
package database.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from support import DATA_DIR, REPO_ROOT

from kedra_experience import manifests, state, values
from kedra_experience.applications import (
    ROLE_TERMINAL,
    application_contracts,
    declared_applications,
    terminal_application,
)
from kedra_experience.packages import package_requirements, required_packages
from kedra_experience.resolve import resolve_preset, resolve_state
from kedra_experience.vendordefaults import (
    OverrideError,
    modules_with_vendor_defaults,
    render_module,
)

# The contract, written out independently of the manifest. A test that reads the
# manifest and asserts the manifest agrees with itself proves nothing; changing
# the shipped terminal must mean changing this too.
TERMINAL_DESKTOP_ID = "org.gnome.Console.desktop"
TERMINAL_APPLICATION_ID = "org.gnome.Console"
TERMINAL_PACKAGE = "gnome-console"

# Things Terminal v1 must not have acquired. The user's own machine may well
# have every one of them installed; none is a Kedra Experience dependency.
FORBIDDEN_PACKAGES = frozenset(
    {
        "blackbox",
        "blackbox-terminal",
        "starship",
        "fish",
        "fisher",
        "ttf-firacode-nerd",
        "nerd-fonts",
        "cachyos-fish-config",
        "gnome-terminal",
        "kitty",
        "alacritty",
        "xdg-terminal-exec",
    }
)

# Substrings that must not appear in any *value* the module declares -- reasons,
# package names, application fields. Comments are deliberately not scanned: the
# manifest explains at length what Terminal does not own, and a test that
# forbade the words would force deleting the explanation.
FORBIDDEN_VALUE_SUBSTRINGS = (
    "/home/",
    "~/",
    "$HOME",
    "/usr/bin",
    "/etc/",
    ".config/fish",
    "starship",
    "blackbox",
    "flatpak run",
    "kgx",
)


def shipped_tree() -> manifests.Tree:
    report = manifests.Report()
    tree = manifests.load_tree(DATA_DIR, report)
    assert report.ok, report.rendered()
    return tree


def user_state(base: str, overrides: dict[str, bool]) -> state.UserState:
    return state.UserState(
        base_preset=base,
        module_overrides=tuple(
            state.ModuleOverride(id=module_id, enabled=enabled)
            for module_id, enabled in overrides.items()
        ),
    )


def strings(node) -> list[str]:
    """Every string anywhere in a parsed manifest value."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in strings(value)]
    if isinstance(node, list):
        return [s for item in node for s in strings(item)]
    return []


class ContractTests(unittest.TestCase):
    """GNOME Console is the declared terminal, as an application ID."""

    def setUp(self):
        self.tree = shipped_tree()
        self.module = self.tree.modules["terminal"]
        self.kedra = resolve_preset(self.tree, "kedra")

    def test_the_terminal_role_is_filled_by_gnome_console(self):
        contract = terminal_application(self.tree, self.kedra)
        self.assertIsNotNone(contract, "the Kedra preset declares no terminal")
        self.assertEqual(contract.role, ROLE_TERMINAL)
        self.assertEqual(contract.desktop_id, TERMINAL_DESKTOP_ID)
        self.assertEqual(contract.application_id, TERMINAL_APPLICATION_ID)
        self.assertEqual(contract.package, TERMINAL_PACKAGE)
        self.assertEqual(contract.module_id, "terminal")

    def test_the_terminal_module_owns_the_role_alone(self):
        owners = [
            module_id
            for module_id, module in self.tree.modules.items()
            for entry in module.get("applications", [])
            if entry.get("role") == ROLE_TERMINAL
        ]
        self.assertEqual(owners, ["terminal"])

    def test_the_tree_declares_exactly_one_contract(self):
        declared = declared_applications(self.tree)
        self.assertEqual(
            [(c.role, c.desktop_id) for c in declared],
            [(ROLE_TERMINAL, TERMINAL_DESKTOP_ID)],
        )

    def test_the_contract_is_a_desktop_id_and_not_a_binary_path(self):
        entry = self.module["applications"][0]
        self.assertTrue(manifests.is_desktop_id(entry["desktop_id"]))
        self.assertNotIn("/", entry["desktop_id"])
        self.assertNotIn(" ", entry["desktop_id"])

    def test_the_contract_carries_no_command_representation(self):
        # Whatever is added here later, it must not be an exec line: the
        # launch mechanism belongs to the consumer, via GAppInfo.
        for entry in self.module.get("applications", []):
            self.assertEqual(
                set(entry) - manifests.APPLICATION_KNOWN_KEYS,
                set(),
                "an application contract grew a key outside the schema",
            )
            for forbidden in ("command", "exec", "binary", "path", "args", "shell"):
                self.assertNotIn(forbidden, entry)

    def test_a_future_selector_can_replace_the_terminal_in_one_place(self):
        # The extensibility claim, stated as a property rather than a promise:
        # everything a consumer needs is on the contract, so swapping the
        # nominated terminal is an edit to this one table plus its package
        # relation -- no caller learns a path, and no other module is touched.
        contract = terminal_application(self.tree, self.kedra)
        self.assertEqual(
            {contract.role, contract.desktop_id, contract.package},
            {ROLE_TERMINAL, TERMINAL_DESKTOP_ID, TERMINAL_PACKAGE},
        )
        self.assertTrue(contract.reason.strip(), "the contract states no reason")

    @unittest.skipUnless(
        Path("/usr/share/applications").is_dir(), "no desktop-file directory"
    )
    def test_the_desktop_file_exists_where_gnome_console_installs_it(self):
        """The premise of the contract, checked read-only against the system.

        Skipped rather than failed when gnome-console is not installed: a
        machine without it is not a broken manifest, and the resolver must never
        depend on what happens to be installed here.
        """
        desktop = Path("/usr/share/applications") / TERMINAL_DESKTOP_ID
        if not desktop.is_file():
            self.skipTest("gnome-console is not installed on this machine")
        self.assertTrue(desktop.is_file())


class ScopeTests(unittest.TestCase):
    """What Terminal v1 deliberately does not own."""

    def setUp(self):
        self.tree = shipped_tree()
        self.module = self.tree.modules["terminal"]
        self.raw = tomllib.loads(
            (DATA_DIR / "modules" / "terminal.toml").read_text(encoding="utf-8")
        )

    def test_the_module_declares_no_settings_at_all(self):
        # The v1 outcome, and the reason there is no kedra-experience-terminal
        # package: every org.gnome.Console key was reviewed and upstream kept.
        self.assertEqual(self.module.get("settings", []), [])

    def test_the_empty_setting_list_is_explicit_rather_than_absent(self):
        # An omitted key and a reviewed "nothing to ship" read identically to
        # tooling. The manifest states it, so the decision is visible.
        self.assertIn("settings", self.raw["module"])
        self.assertEqual(self.raw["module"]["settings"], [])

    def test_the_module_renders_no_vendor_override(self):
        self.assertNotIn("terminal", modules_with_vendor_defaults(self.tree))
        with self.assertRaises(OverrideError):
            render_module(self.tree, "terminal")

    def test_no_kedra_experience_terminal_package_was_created(self):
        # Not for symmetry with desktop and workflow: a defaults package with no
        # defaults is a package whose removal changes nothing.
        self.assertFalse((REPO_ROOT / "kedra-experience-terminal").exists())
        names = {entry["name"] for entry in self.module.get("packages", [])}
        self.assertNotIn("kedra-experience-terminal", names)

    def test_int64_support_was_not_added_speculatively(self):
        # org.gnome.Console's scrollback-lines is type 'x'. Kedra keeps the
        # upstream 10000, so nothing shipped needs int64 -- and the value
        # vocabulary must not grow a type no declaration uses.
        self.assertNotIn("x", values.VALUE_TYPES)

    def test_no_forbidden_package_is_related_to_the_terminal_module(self):
        for entry in self.module.get("packages", []):
            with self.subTest(package=entry["name"]):
                self.assertNotIn(entry["name"], FORBIDDEN_PACKAGES)

    def test_no_forbidden_package_appears_anywhere_in_the_tree(self):
        for module_id, module in self.tree.modules.items():
            for entry in module.get("packages", []):
                with self.subTest(module=module_id, package=entry["name"]):
                    self.assertNotIn(entry["name"], FORBIDDEN_PACKAGES)

    def test_no_declared_value_names_a_user_path_or_a_command(self):
        for text in strings(self.module):
            for forbidden in FORBIDDEN_VALUE_SUBSTRINGS:
                with self.subTest(value=text[:60], forbidden=forbidden):
                    self.assertNotIn(forbidden, text.lower())

    def test_the_module_claims_no_appearance_owned_setting(self):
        # Belt and braces on the empty setting list: if Terminal ever grows
        # settings, none of them may be a palette or theme key.
        for entry in self.module.get("settings", []):
            schema = entry.get("target", {}).get("schema", "")
            key = entry.get("target", {}).get("key", "")
            self.assertNotIn(key, {"theme", "livery", "custom-liveries", "transparency"})
            self.assertNotIn("interface", schema)

    def test_the_advisory_to_appearance_is_gone(self):
        # Part of the same review: Terminal is semantically complete without the
        # appearance layer, so the relationship named no consequence.
        self.assertEqual(self.module.get("works_best_with", []), [])
        self.assertEqual(self.module.get("requires", []), [])

    def test_the_gnome_terminal_conditional_relation_is_gone(self):
        names = {entry["name"] for entry in self.module.get("packages", [])}
        self.assertNotIn("gnome-terminal", names)
        relations = {entry["relation"] for entry in self.module.get("packages", [])}
        self.assertEqual(relations, {"required"})


class PackageSemanticsTests(unittest.TestCase):
    """Vanilla / Kedra / Custom, from data alone.

    Whether gnome-console happens to be installed on the machine running these
    tests is irrelevant and must stay irrelevant: every assertion below is about
    what a *profile requires*, computed from manifests. Nothing consults pacman.
    """

    def setUp(self):
        self.tree = shipped_tree()

    def required(self, resolution) -> tuple[str, ...]:
        return required_packages(self.tree, resolution)

    def test_vanilla_requires_nothing_and_has_no_terminal_contract(self):
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertFalse(resolution.applies_experience_layer)
        self.assertEqual(self.required(resolution), ())
        self.assertNotIn(TERMINAL_PACKAGE, self.required(resolution))
        self.assertEqual(application_contracts(self.tree, resolution), ())
        self.assertIsNone(terminal_application(self.tree, resolution))

    def test_the_contract_exists_in_the_tree_even_for_vanilla(self):
        # The invariant the declared/effective split exists for: Vanilla must
        # not acquire a dependency merely because the declaration is in the
        # package data it never applies.
        self.assertEqual(len(declared_applications(self.tree)), 1)
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertEqual(application_contracts(self.tree, resolution), ())

    def test_kedra_requires_gnome_console(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertIn(TERMINAL_PACKAGE, self.required(resolution))
        contract = terminal_application(self.tree, resolution)
        self.assertEqual(contract.desktop_id, TERMINAL_DESKTOP_ID)

    def test_the_requirement_is_attributed_to_the_terminal_module(self):
        resolution = resolve_preset(self.tree, "kedra")
        owners = {
            r.module_id
            for r in package_requirements(self.tree, resolution)
            if r.name == TERMINAL_PACKAGE
        }
        self.assertEqual(owners, {"terminal"})

    def test_custom_with_terminal_disabled_requires_no_console(self):
        resolution = resolve_state(self.tree, user_state("kedra", {"terminal": False}))
        self.assertTrue(resolution.applies_experience_layer)
        self.assertNotIn(TERMINAL_PACKAGE, self.required(resolution))
        self.assertIsNone(terminal_application(self.tree, resolution))
        # ...and the rest of the Kedra profile is untouched by that choice.
        self.assertIn("gnome-shell", self.required(resolution))

    def test_custom_with_terminal_enabled_requires_console(self):
        resolution = resolve_state(self.tree, user_state("kedra", {"terminal": True}))
        self.assertIn(TERMINAL_PACKAGE, self.required(resolution))
        self.assertIsNotNone(terminal_application(self.tree, resolution))

    def test_no_other_module_would_pull_console_in(self):
        # So "terminal disabled" genuinely drops the requirement rather than
        # having it arrive from somewhere else.
        owners = [
            module_id
            for module_id, module in self.tree.modules.items()
            for entry in module.get("packages", [])
            if entry["name"] == TERMINAL_PACKAGE
        ]
        self.assertEqual(owners, ["terminal"])

    def test_resolution_is_deterministic(self):
        for preset_id in ("vanilla", "kedra"):
            with self.subTest(preset=preset_id):
                resolution = resolve_preset(self.tree, preset_id)
                self.assertEqual(self.required(resolution), self.required(resolution))
                self.assertEqual(
                    application_contracts(self.tree, resolution),
                    application_contracts(self.tree, resolution),
                )

    def test_nothing_in_this_path_can_shell_out(self):
        """"Required by this profile" must never mean "installed here".

        The distinction is structural -- there is no package-database code in
        this layer at all -- so it is checked structurally: every process-
        spawning entry point is replaced with a detonator for the duration.
        """
        import os
        import subprocess

        def boom(*args, **kwargs):
            raise AssertionError("package resolution tried to spawn a process")

        patched = [
            (subprocess, "run"),
            (subprocess, "Popen"),
            (subprocess, "check_output"),
            (os, "system"),
        ]
        saved = [(obj, name, getattr(obj, name)) for obj, name in patched]
        for obj, name in patched:
            setattr(obj, name, boom)
        try:
            for preset_id in ("vanilla", "kedra"):
                resolution = resolve_preset(self.tree, preset_id)
                required_packages(self.tree, resolution)
                package_requirements(self.tree, resolution)
                application_contracts(self.tree, resolution)
        finally:
            for obj, name, original in saved:
                setattr(obj, name, original)


class DocumentationTests(unittest.TestCase):
    """The decisions are only durable if they are written down."""

    def setUp(self):
        self.terminal_doc = (REPO_ROOT / "docs" / "kedra-terminal.md").read_text(
            encoding="utf-8"
        )
        self.workflow_doc = (REPO_ROOT / "docs" / "kedra-workflow.md").read_text(
            encoding="utf-8"
        )

    def test_the_terminal_document_states_the_contract(self):
        for expected in (
            "GNOME Console",
            TERMINAL_DESKTOP_ID,
            TERMINAL_PACKAGE,
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal_doc)

    def test_the_terminal_document_states_every_non_ownership(self):
        for expected in (
            "login shell",
            "Starship",
            "dotfile",
            "palette",
            "Black Box",
            "fish",
            "Appearance",
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal_doc)

    def test_the_terminal_document_explains_why_nothing_is_overridden(self):
        for expected in ("scrollback-lines", "audible-bell", "10000", "upstream"):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal_doc)

    def test_super_enter_is_documented_as_pending_in_both_documents(self):
        for doc, label in (
            (self.terminal_doc, "kedra-terminal.md"),
            (self.workflow_doc, "kedra-workflow.md"),
        ):
            with self.subTest(doc=label):
                self.assertIn("Super+Enter", doc)
                self.assertIn("custom-keybinding", doc)

    def test_the_ownership_split_is_documented(self):
        for expected in ("Terminal", "Workflow", "Appearance"):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal_doc)


if __name__ == "__main__":
    unittest.main()
