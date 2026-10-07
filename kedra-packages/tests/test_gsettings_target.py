#!/usr/bin/env python3
"""The GSettings target model, with relocatable-schema support.

Schema 6 gave a target an optional `path`. Everything here is about the two
questions that opens up:

    when is a path required, forbidden or malformed?
    does a path change a target's *identity*?

The second is the one with teeth. A managed record refers to a target by its
canonical string, so if two instances of one relocatable schema canonicalise the
same way, Kedra cannot tell two shortcuts apart -- and reverting one would
revert the other.

Nothing here performs I/O. This is the declarative half of the backend, and it
has no method that could reach a session.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import TreeTestCase, gsettings_toml, module_toml  # noqa: E402

from kedra_experience import backends  # noqa: E402
from kedra_experience.backends import (  # noqa: E402
    BackendError,
    GSettingsSpec,
    RELOCATABLE_SCHEMAS,
    parse_gsettings_target,
)

SPEC = GSettingsSpec()

CHILD = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
PARENT = "org.gnome.settings-daemon.plugins.media-keys"
KEDRA_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/"
OTHER_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"


def problems(**target) -> list[str]:
    return SPEC.target_problems(target)


class FixedSchemaTargets(unittest.TestCase):
    """The pre-existing shape must keep working, unchanged and unrenamed."""

    def test_a_fixed_target_is_still_valid(self):
        self.assertEqual(problems(schema="org.gnome.mutter", key="dynamic-workspaces"), [])

    def test_a_fixed_target_canonicalises_as_it_always_did(self):
        # Load-bearing: every managed record written before relocatable support
        # existed holds this exact string, and re-spelling it would orphan them.
        self.assertEqual(
            SPEC.canonical_target({"schema": "org.gnome.mutter", "key": "dynamic-workspaces"}),
            "gsettings:org.gnome.mutter:dynamic-workspaces",
        )

    def test_a_path_on_a_fixed_schema_is_rejected(self):
        found = problems(schema="org.gnome.mutter", key="dynamic-workspaces", path=KEDRA_PATH)
        self.assertTrue(any("only valid for a relocatable schema" in p for p in found), found)

    def test_the_parent_list_is_a_fixed_target(self):
        self.assertEqual(problems(schema=PARENT, key="custom-keybindings"), [])


class RelocatableTargets(unittest.TestCase):
    def test_a_relocatable_target_with_a_path_is_valid(self):
        self.assertEqual(problems(schema=CHILD, key="binding", path=KEDRA_PATH), [])

    def test_a_relocatable_schema_without_a_path_is_rejected(self):
        found = problems(schema=CHILD, key="binding")
        self.assertTrue(any("must supply 'path'" in p for p in found), found)

    def test_the_custom_keybinding_schema_is_known_to_be_relocatable(self):
        # The set is declared data rather than discovered, so it is asserted
        # rather than assumed. tests/test_gsettings_adapter.py checks the same
        # fact against the schema GLib actually has installed.
        self.assertIn(CHILD, RELOCATABLE_SCHEMAS)


class TargetIdentity(unittest.TestCase):
    """A path is part of who a target is."""

    def test_the_path_appears_in_the_canonical_target(self):
        self.assertEqual(
            SPEC.canonical_target({"schema": CHILD, "key": "binding", "path": KEDRA_PATH}),
            f"gsettings:{CHILD}:{KEDRA_PATH}:binding",
        )

    def test_two_instances_of_one_schema_are_distinguishable(self):
        kedra = SPEC.canonical_target({"schema": CHILD, "key": "binding", "path": KEDRA_PATH})
        other = SPEC.canonical_target({"schema": CHILD, "key": "binding", "path": OTHER_PATH})
        self.assertNotEqual(kedra, other)

    def test_two_keys_at_one_path_are_distinguishable(self):
        binding = SPEC.canonical_target({"schema": CHILD, "key": "binding", "path": KEDRA_PATH})
        command = SPEC.canonical_target({"schema": CHILD, "key": "command", "path": KEDRA_PATH})
        self.assertNotEqual(binding, command)

    def test_a_canonical_target_round_trips(self):
        for target in (
            {"schema": "org.gnome.mutter", "key": "dynamic-workspaces"},
            {"schema": CHILD, "key": "binding", "path": KEDRA_PATH},
        ):
            with self.subTest(target=target):
                parsed = parse_gsettings_target(SPEC.canonical_target(target))
                self.assertEqual(parsed.schema, target["schema"])
                self.assertEqual(parsed.key, target["key"])
                self.assertEqual(parsed.path, target.get("path"))

    def test_parsing_rejects_a_malformed_target(self):
        for bad in (
            "org.gnome.mutter:dynamic-workspaces",       # no backend prefix
            "gsettings:org.gnome.mutter",                # no key
            "gsettings:notdotted:key",                   # not a schema ID
            "gsettings:org.gnome.mutter:Bad_Key",        # not a key name
            f"gsettings:{CHILD}:relative/path/:binding",  # not absolute
            "gsettings:a:b:c:d",                         # too many components
        ):
            with self.subTest(target=bad):
                with self.assertRaises(BackendError):
                    parse_gsettings_target(bad)


class MalformedPaths(unittest.TestCase):
    """A path is stored forever, so the shapes that must never enter one."""

    BAD = {
        "relative": "org/gnome/x/",
        "no trailing slash": "/org/gnome/x",
        "root": "/",
        "empty": "",
        "double slash": "/org//gnome/x/",
        "parent traversal": "/org/gnome/../x/",
        "dot segment": "/org/gnome/./x/",
        "whitespace": "/org/gnome/a b/",
        "a command line": "gapplication launch org.gnome.Console",
        "a binary path": "/usr/bin/kgx",
        "a colon": "/org/gnome/a:b/",
        "not a string": 17,
    }

    def test_every_malformed_path_is_rejected(self):
        for label, path in self.BAD.items():
            with self.subTest(path=label):
                found = problems(schema=CHILD, key="binding", path=path)
                self.assertTrue(
                    any("not an absolute GSettings/dconf path" in p for p in found),
                    f"{label} ({path!r}) was accepted: {found}",
                )


class SchemaIdsThatAreNotSchemaIds(unittest.TestCase):
    """A command line or a filesystem path must never pass as a schema."""

    NOT_SCHEMAS = (
        "gapplication launch org.gnome.Console",
        "/usr/bin/kgx",
        "kgx",
        "/org/gnome/settings-daemon/plugins/media-keys/",
        "flatpak run com.raggesilver.BlackBox",
        "org.gnome.Console.desktop --new-window",
        "",
        "org.gnome.mutter; rm -rf /",
    )

    def test_each_is_rejected_as_a_schema(self):
        for candidate in self.NOT_SCHEMAS:
            with self.subTest(schema=candidate):
                found = problems(schema=candidate, key="binding")
                self.assertTrue(
                    any("not a valid GSettings schema ID" in p for p in found),
                    f"{candidate!r} was accepted as a schema: {found}",
                )

    def test_the_message_says_what_a_schema_is_not(self):
        found = problems(schema="/usr/bin/kgx", key="binding")
        joined = " ".join(found)
        self.assertIn("dotted reverse-DNS", joined)
        self.assertIn("command line", joined)


class ManifestLevelValidation(TreeTestCase):
    """The same rules, reached through a module manifest."""

    def _errors(self, settings: str) -> str:
        root = self.make_tree(
            modules={"terminal": module_toml("terminal", settings=settings)},
            presets=None,
        )
        _, report = self.load_dirty(root)
        return " | ".join(message for _, message in report.errors)

    def test_a_relocatable_target_without_a_path_is_reported(self):
        text = gsettings_toml(
            "example-one", schema=CHILD, key="binding", value_type="s", data="<Super>Return"
        )
        self.assertIn("must supply 'path'", self._errors(text))

    def test_a_hand_written_setting_may_not_claim_a_resource(self):
        """`resource` and `member` are generated, never declared.

        A composite resource is declared as a resource. Letting a manifest write
        the two fields by hand would let it half-assemble one -- claiming
        membership of a list, for instance, without any of the coordination that
        makes membership safe.
        """
        text = gsettings_toml("example-one") + '\nresource = "made-up"\n'
        self.assertIn("unknown key 'resource'", self._errors(text))

    def test_a_hand_written_setting_may_not_claim_list_membership(self):
        text = gsettings_toml("example-one") + '\nmember = "/x/"\n'
        self.assertIn("unknown key 'member'", self._errors(text))


class ListMembershipAlgebra(unittest.TestCase):
    """The two functions the whole preservation guarantee rests on."""

    KEDRA = KEDRA_PATH
    USER = ("/x/custom0/", "/x/custom1/")

    def test_adding_appends_and_preserves_order(self):
        self.assertEqual(
            backends.list_with_member(self.USER, self.KEDRA),
            self.USER + (self.KEDRA,),
        )

    def test_adding_twice_does_not_duplicate(self):
        once = backends.list_with_member(self.USER, self.KEDRA)
        self.assertEqual(backends.list_with_member(once, self.KEDRA), once)

    def test_removing_takes_only_that_entry(self):
        full = self.USER + (self.KEDRA,) + ("/x/custom2/",)
        self.assertEqual(
            backends.list_without_member(full, self.KEDRA),
            self.USER + ("/x/custom2/",),
        )

    def test_removing_an_absent_entry_changes_nothing(self):
        self.assertEqual(backends.list_without_member(self.USER, self.KEDRA), self.USER)

    def test_removing_clears_every_duplicate(self):
        doubled = (self.KEDRA, "/x/custom0/", self.KEDRA)
        self.assertEqual(
            backends.list_without_member(doubled, self.KEDRA), ("/x/custom0/",)
        )

    def test_neither_function_rewrites_a_neighbour(self):
        # The property the guarantee actually is: no entry other than `member`
        # is ever produced, dropped or altered.
        for operation in (backends.list_with_member, backends.list_without_member):
            with self.subTest(operation=operation.__name__):
                result = operation(self.USER, self.KEDRA)
                self.assertEqual(
                    tuple(item for item in result if item != self.KEDRA), self.USER
                )


if __name__ == "__main__":
    unittest.main()
