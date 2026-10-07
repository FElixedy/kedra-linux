"""The application-contract grammar and its resolution (schema 5).

`module.applications` answers "which application fills this role", once, as
data. The tests here are about the *grammar* and the fold over a resolution, on
synthetic trees. What the shipped tree actually nominates -- GNOME Console, and
nothing else -- is in test_terminal.py.

Two properties carry most of the weight, and both are about what the contract
refuses to be:

* an application is a desktop-file ID, never a binary path or a command line;
* a declaration is not a requirement. Only an *enabled* module contributes one,
  which is what keeps Vanilla free of a dependency it never asked for.
"""

from __future__ import annotations

import unittest

from support import (
    TreeTestCase,
    application_toml,
    base_preset_toml,
    derived_preset_toml,
    module_toml,
    required_package_toml,
)

from kedra_experience import manifests
from kedra_experience.applications import (
    ROLE_TERMINAL,
    application_contracts,
    application_for_role,
    declared_applications,
    terminal_application,
)
from kedra_experience.resolve import resolve_preset


class GrammarTests(TreeTestCase):
    """One module, one contract, and every way of getting it wrong."""

    def _tree(self, *, applications: str, packages: str | None = None):
        return self.make_tree(
            modules={
                "terminal": module_toml(
                    "terminal",
                    packages=required_package_toml() if packages is None else packages,
                    applications=applications,
                )
            },
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )

    def _accept(self, **kwargs) -> manifests.Tree:
        return self.load(self._tree(**kwargs))

    def _reject(self, expected: str, **kwargs) -> None:
        _, report = self.load_dirty(self._tree(**kwargs))
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn(expected, joined)

    def test_a_minimal_contract_is_accepted(self):
        tree = self._accept(applications=application_toml())
        entry = tree.modules["terminal"]["applications"][0]
        self.assertEqual(entry["role"], "terminal")
        self.assertEqual(entry["desktop_id"], "org.example.Terminal.desktop")
        self.assertEqual(entry["package"], "pkg-terminal")

    def test_a_reason_is_optional_but_must_not_be_blank(self):
        self._accept(applications=application_toml(reason="because"))
        self._reject(
            "'reason' must be a non-empty string",
            applications=application_toml(reason="   "),
        )

    def test_each_required_key_is_required(self):
        for field, kwargs in (
            ("role", {"role": None}),
            ("desktop_id", {"desktop_id": None}),
            ("package", {"package": None}),
        ):
            with self.subTest(missing=field):
                self._reject(
                    f"missing required key '{field}'",
                    applications=application_toml(**kwargs),
                )

    def test_an_unknown_key_is_rejected(self):
        # The closed-key rule is what stops a command or an argv list being
        # smuggled in beside the desktop ID.
        for extra in ('command = "kgx"', 'exec = "/usr/bin/kgx"', 'args = ["--tab"]'):
            with self.subTest(extra=extra):
                self._reject(
                    "unknown key", applications=application_toml(extra=extra)
                )

    def test_the_role_vocabulary_is_closed(self):
        self._reject(
            "unknown application role 'editor'",
            applications=application_toml(role="editor"),
        )

    def test_the_only_role_defined_today_is_terminal(self):
        # A guard against the vocabulary growing speculatively: a role is added
        # when a consumer for it exists, not in anticipation of one.
        self.assertEqual(set(manifests.APPLICATION_ROLES), {"terminal"})
        self.assertEqual(ROLE_TERMINAL, "terminal")

    def test_a_module_cannot_fill_one_role_twice(self):
        self._reject(
            "role 'terminal' is already filled by this module",
            applications=application_toml()
            + application_toml(desktop_id="org.example.Other.desktop"),
        )

    def test_the_key_must_be_an_array_of_tables(self):
        # Written inline rather than through the fixture helper: this is a bare
        # `[module]` key, so it has to precede the sub-tables the helper emits.
        module = f"""schema_version = {manifests.SCHEMA_VERSION}

[module]
id = "terminal"
name = "Module terminal"
description = "Test module terminal."
version = "0.1.0"
tier = "optional"
user_selectable = true
requires = []
conflicts = []
applications = "org.gnome.Console.desktop"
"""
        root = self.make_tree(
            modules={"terminal": module},
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )
        _, report = self.load_dirty(root)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("module.applications must be an array of tables", joined)

    def test_an_entry_must_be_a_table(self):
        module = f"""schema_version = {manifests.SCHEMA_VERSION}

[module]
id = "terminal"
name = "Module terminal"
description = "Test module terminal."
version = "0.1.0"
tier = "optional"
user_selectable = true
requires = []
conflicts = []
applications = ["org.gnome.Console.desktop"]
"""
        root = self.make_tree(
            modules={"terminal": module},
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )
        _, report = self.load_dirty(root)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("module.applications[0]: must be a table", joined)


class DesktopIdTests(TreeTestCase):
    """The contract is a desktop ID. Everything path- or command-shaped is out."""

    def _load(self, desktop_id: str):
        root = self.make_tree(
            modules={
                "terminal": module_toml(
                    "terminal",
                    packages=required_package_toml(),
                    applications=application_toml(desktop_id=desktop_id),
                )
            },
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )
        return self.load_dirty(root)

    def assertAccepted(self, desktop_id: str) -> None:
        _, report = self._load(desktop_id)
        self.assertTrue(report.ok, f"{desktop_id!r} rejected: {report.errors}")

    def assertRejected(self, desktop_id: str) -> None:
        _, report = self._load(desktop_id)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("is not a desktop-file ID", joined)

    def test_a_real_desktop_id_is_accepted(self):
        for desktop_id in (
            "org.gnome.Console.desktop",
            "org.gnome.Terminal.desktop",
            # Single-segment and hyphenated IDs are legal desktop files. Whether
            # Kedra would ever nominate one is a product question, not a
            # grammatical one -- this check is about shape only.
            "firefox.desktop",
            "gnome-terminal.desktop",
            "com.raggesilver.BlackBox.desktop",
        ):
            with self.subTest(desktop_id=desktop_id):
                self.assertAccepted(desktop_id)

    def test_a_binary_name_is_rejected(self):
        self.assertRejected("kgx")

    def test_an_absolute_path_is_rejected(self):
        for path in ("/usr/bin/kgx", "/usr/bin/kgx.desktop", "../kgx.desktop"):
            with self.subTest(path=path):
                self.assertRejected(path)

    def test_a_command_line_is_rejected(self):
        for command in (
            "flatpak run org.gnome.Console",
            "kgx --tab",
            "org.gnome.Console.desktop --tab",
            "sh -c 'kgx'",
        ):
            with self.subTest(command=command):
                self.assertRejected(command)

    def test_an_application_id_without_the_suffix_is_rejected(self):
        # The bare app ID is derivable from the desktop ID, never the other way
        # round, so the file name is what gets declared.
        self.assertRejected("org.gnome.Console")

    def test_structurally_broken_ids_are_rejected(self):
        for broken in (".desktop", "org..Console.desktop", "1console.desktop", ""):
            with self.subTest(desktop_id=broken):
                self.assertRejected(broken)

    def test_the_application_id_is_derived_from_the_desktop_id(self):
        root = self.make_tree(
            modules={
                "terminal": module_toml(
                    "terminal",
                    packages=required_package_toml(),
                    applications=application_toml(
                        desktop_id="org.gnome.Console.desktop"
                    ),
                )
            },
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )
        tree = self.load(root)
        contract = terminal_application(tree, resolve_preset(tree, "kedra"))
        self.assertEqual(contract.desktop_id, "org.gnome.Console.desktop")
        self.assertEqual(contract.application_id, "org.gnome.Console")


class PackageCrossCheckTests(TreeTestCase):
    """A nominated application must be one the profile actually installs."""

    def _report(self, packages: str, package: str = "pkg-terminal"):
        root = self.make_tree(
            modules={
                "terminal": module_toml(
                    "terminal",
                    packages=packages,
                    applications=application_toml(package=package),
                )
            },
            presets={"kedra": base_preset_toml("kedra", {"terminal": True})},
        )
        return self.load_dirty(root)[1]

    def test_a_required_relation_satisfies_the_contract(self):
        self.assertTrue(self._report(required_package_toml()).ok)

    def test_an_undeclared_package_is_rejected(self):
        report = self._report(required_package_toml("something-else"))
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("is not declared by this module as a 'required'", joined)

    def test_a_recommended_relation_is_not_enough(self):
        # The point of the cross-check: "the Kedra terminal is X" and "X is
        # merely recommended" cannot both be true.
        packages = """
[[module.packages]]
name = "pkg-terminal"
relation = "recommended"
reason = "r"
"""
        report = self._report(packages)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("is not declared by this module as a 'required'", joined)

    def test_a_conditional_relation_is_not_enough(self):
        packages = """
[[module.packages]]
name = "pkg-terminal"
relation = "conditional"
condition = { type = "package-installed", package = "pkg-terminal" }
reason = "r"
"""
        report = self._report(packages)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("is not declared by this module as a 'required'", joined)


class RoleUniquenessTests(TreeTestCase):
    """A role has one owner across the whole tree."""

    def test_two_modules_cannot_both_fill_the_terminal_role(self):
        modules = {
            "terminal": module_toml(
                "terminal",
                packages=required_package_toml(),
                applications=application_toml(),
            ),
            "extras": module_toml(
                "extras",
                packages=required_package_toml("pkg-other"),
                applications=application_toml(
                    desktop_id="org.example.Other.desktop", package="pkg-other"
                ),
            ),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"terminal": True, "extras": True})
            },
        )
        _, report = self.load_dirty(root)
        joined = " | ".join(message for _, message in report.errors)
        self.assertIn("has one owner across the whole Experience tree", joined)

    def test_uniqueness_does_not_depend_on_activation(self):
        # Two owners is a data error even if a preset would only ever enable
        # one of them -- otherwise the tree would be valid or invalid depending
        # on which profile happened to read it.
        modules = {
            "terminal": module_toml(
                "terminal",
                packages=required_package_toml(),
                applications=application_toml(),
            ),
            "extras": module_toml(
                "extras",
                packages=required_package_toml("pkg-other"),
                applications=application_toml(
                    desktop_id="org.example.Other.desktop", package="pkg-other"
                ),
            ),
        }
        root = self.make_tree(
            modules=modules,
            presets={
                "kedra": base_preset_toml("kedra", {"terminal": True, "extras": False})
            },
        )
        _, report = self.load_dirty(root)
        self.assertFalse(report.ok)


class ResolutionTests(TreeTestCase):
    """Declared versus effective: the distinction Vanilla depends on."""

    def setUp(self):
        super().setUp()
        self.root = self.make_tree(
            modules={
                "terminal": module_toml(
                    "terminal",
                    packages=required_package_toml(),
                    applications=application_toml(
                        desktop_id="org.example.Terminal.desktop",
                        reason="because",
                    ),
                ),
                "extras": module_toml("extras"),
            },
            presets={
                "kedra": base_preset_toml(
                    "kedra", {"terminal": True, "extras": False}
                ),
                "off": base_preset_toml("off", {"terminal": False, "extras": False}),
                "novanilla": base_preset_toml(
                    "novanilla", {}, applies=False
                ),
                "custom-off": derived_preset_toml(
                    "custom-off", "kedra", {"terminal": False}
                ),
            },
        )
        self.tree = self.load(self.root)

    def contracts(self, preset_id: str):
        return application_contracts(self.tree, resolve_preset(self.tree, preset_id))

    def test_an_enabled_module_contributes_its_contract(self):
        found = self.contracts("kedra")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].role, "terminal")
        self.assertEqual(found[0].desktop_id, "org.example.Terminal.desktop")
        self.assertEqual(found[0].package, "pkg-terminal")
        self.assertEqual(found[0].module_id, "terminal")
        self.assertEqual(found[0].reason, "because")

    def test_a_disabled_module_contributes_nothing(self):
        self.assertEqual(self.contracts("off"), ())
        self.assertIsNone(
            application_for_role(
                self.tree, resolve_preset(self.tree, "off"), ROLE_TERMINAL
            )
        )

    def test_a_preset_without_the_experience_layer_contributes_nothing(self):
        # The Vanilla shape. The declaration is still in the tree.
        self.assertEqual(self.contracts("novanilla"), ())
        self.assertEqual(len(declared_applications(self.tree)), 1)

    def test_a_derived_override_drops_the_contract(self):
        self.assertEqual(self.contracts("custom-off"), ())

    def test_declared_ignores_activation_entirely(self):
        declared = declared_applications(self.tree)
        self.assertEqual([c.desktop_id for c in declared], ["org.example.Terminal.desktop"])

    def test_an_unfilled_role_answers_none_rather_than_guessing(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertIsNone(application_for_role(self.tree, resolution, "editor"))

    def test_output_is_deterministic(self):
        self.assertEqual(self.contracts("kedra"), self.contracts("kedra"))
        self.assertEqual(
            declared_applications(self.tree), declared_applications(self.tree)
        )


if __name__ == "__main__":
    unittest.main()
