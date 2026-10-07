#!/usr/bin/env python3
"""The production GSettings adapter, against real GLib.

These are integration tests in the strict sense: real `.gschema.xml` files from
the installed GNOME schemas, compiled by the real `glib-compile-schemas`, read
and written through real `Gio.Settings`. What they are *not* is tests against
the developer's desktop -- see `support.IsolatedGSettings` for how the two are
separated, and `IsolationProofTests` for the assertion that the separation held.

The invariant most of this file exists for:

    effective value == default   does NOT imply   no explicit value is stored

Every ownership decision Kedra makes rests on being able to tell those apart.
If the adapter cannot, `original` is captured wrongly on first apply and a later
revert silently discards a value the user had chosen.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    REPO_ROOT,
    IsolatedGSettings,
    LiveStateProof,
    gsettings_available,
)

from kedra_experience.backends import BackendError  # noqa: E402
from kedra_experience.gsettings import EXPLICIT_VALUE_NOTE, GSettingsAdapter  # noqa: E402
from kedra_experience.shortcuts import (  # noqa: E402
    CHILD_SCHEMA,
    PARENT_KEY,
    PARENT_SCHEMA,
    TERMINAL_SHORTCUT_PATH,
)
from kedra_experience.values import TypedValue  # noqa: E402

CHILD_BINDING = f"gsettings:{CHILD_SCHEMA}:{TERMINAL_SHORTCUT_PATH}:binding"
CHILD_COMMAND = f"gsettings:{CHILD_SCHEMA}:{TERMINAL_SHORTCUT_PATH}:command"
CHILD_NAME = f"gsettings:{CHILD_SCHEMA}:{TERMINAL_SHORTCUT_PATH}:name"
CHILD_LOCKSCREEN = f"gsettings:{CHILD_SCHEMA}:{TERMINAL_SHORTCUT_PATH}:enable-in-lockscreen"
PARENT_LIST = f"gsettings:{PARENT_SCHEMA}:{PARENT_KEY}"

OTHER_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom7/"
OTHER_BINDING = f"gsettings:{CHILD_SCHEMA}:{OTHER_PATH}:binding"


requires_gsettings = unittest.skipUnless(
    gsettings_available(),
    "installed GNOME GSettings schemas, glib-compile-schemas and PyGObject required",
)


class AdapterTestCase(unittest.TestCase):
    """One isolated environment per test, torn down with it."""

    def setUp(self):
        # Measured before anything runs, so every test in this file carries its
        # own evidence that it left the user's desktop alone.
        self.live = LiveStateProof()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.iso = IsolatedGSettings(Path(tmp.name))
        self.addCleanup(self.iso.release)
        self.addCleanup(self._assert_live_state_untouched)
        self.adapter = self.iso.adapter()

    def _assert_live_state_untouched(self):
        self.assertTrue(
            self.live.unchanged(),
            f"this test modified live user state: {self.live.differences()}",
        )


@requires_gsettings
class FixedSchemaReads(AdapterTestCase):
    def test_an_unset_key_reads_as_no_stored_value(self):
        self.assertIsNone(self.adapter.read(PARENT_LIST))

    def test_an_unset_key_still_has_an_effective_value(self):
        # The schema default for custom-keybindings is the empty list, and that
        # is a real value even though nothing is stored.
        self.assertEqual(self.adapter.read_effective(PARENT_LIST), TypedValue("as", ()))

    def test_a_written_list_reads_back_with_its_type(self):
        value = TypedValue("as", ("/a/", "/b/"))
        self.adapter.apply(PARENT_LIST, value)
        self.assertEqual(self.adapter.read(PARENT_LIST), value)
        self.assertEqual(self.adapter.read_effective(PARENT_LIST), value)


@requires_gsettings
class RelocatableSchemaReads(AdapterTestCase):
    def test_the_child_schema_really_is_relocatable(self):
        """GLib's own answer, not Kedra's declared one.

        `RELOCATABLE_SCHEMAS` is data Kedra maintains by hand because the
        declarative layer may not perform I/O. This is where that data is
        checked against reality.
        """
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.new_from_directory(
            str(self.iso.schema_dir), None, True
        )
        self.assertIsNone(source.lookup(CHILD_SCHEMA, False).get_path())
        self.assertEqual(
            source.lookup(PARENT_SCHEMA, False).get_path(),
            "/org/gnome/settings-daemon/plugins/media-keys/",
        )

    def test_reading_an_instance_at_a_path(self):
        self.assertIsNone(self.adapter.read(CHILD_BINDING))
        self.assertEqual(self.adapter.read_effective(CHILD_BINDING), TypedValue("s", ""))

    def test_writing_and_reading_one_instance(self):
        self.adapter.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.assertEqual(self.adapter.read(CHILD_BINDING), TypedValue("s", "<Super>Return"))

    def test_two_instances_of_one_schema_are_independent(self):
        """The reason a path belongs in the target identity."""
        self.adapter.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.adapter.apply(OTHER_BINDING, TypedValue("s", "<Super>t"))

        self.assertEqual(self.adapter.read(CHILD_BINDING), TypedValue("s", "<Super>Return"))
        self.assertEqual(self.adapter.read(OTHER_BINDING), TypedValue("s", "<Super>t"))

        self.adapter.reset(CHILD_BINDING)
        self.assertIsNone(self.adapter.read(CHILD_BINDING))
        # The other instance is untouched -- resetting one shortcut must never
        # reach another.
        self.assertEqual(self.adapter.read(OTHER_BINDING), TypedValue("s", "<Super>t"))


@requires_gsettings
class ExplicitValueDetection(AdapterTestCase):
    """The invariant the whole ownership model depends on."""

    def test_the_note_is_stated_in_the_source(self):
        self.assertIn("get_user_value", EXPLICIT_VALUE_NOTE)

    def test_a_value_written_equal_to_the_default_is_still_explicit(self):
        # The exact trap. `binding` defaults to the empty string; writing the
        # empty string leaves the effective value unchanged, and yet a value is
        # now stored. An adapter that answered from the effective value would
        # call this unset and, on revert, unset a key the user had set.
        default = self.adapter.read_effective(CHILD_BINDING)
        self.assertEqual(default, TypedValue("s", ""))
        self.assertIsNone(self.adapter.read(CHILD_BINDING))

        self.adapter.apply(CHILD_BINDING, TypedValue("s", ""))

        self.assertEqual(self.adapter.read_effective(CHILD_BINDING), default)
        self.assertEqual(self.adapter.read(CHILD_BINDING), TypedValue("s", ""))
        self.assertTrue(self.adapter.has_user_value(CHILD_BINDING))

    def test_the_same_trap_on_the_parent_list(self):
        self.assertEqual(self.adapter.read_effective(PARENT_LIST), TypedValue("as", ()))
        self.assertFalse(self.adapter.has_user_value(PARENT_LIST))

        self.adapter.apply(PARENT_LIST, TypedValue("as", ()))

        self.assertEqual(self.adapter.read_effective(PARENT_LIST), TypedValue("as", ()))
        self.assertTrue(self.adapter.has_user_value(PARENT_LIST))

    def test_reset_removes_the_explicit_value_and_the_default_returns(self):
        self.adapter.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.assertTrue(self.adapter.has_user_value(CHILD_BINDING))

        self.adapter.reset(CHILD_BINDING)

        self.assertFalse(self.adapter.has_user_value(CHILD_BINDING))
        self.assertIsNone(self.adapter.read(CHILD_BINDING))
        self.assertEqual(self.adapter.read_effective(CHILD_BINDING), TypedValue("s", ""))

    def test_resetting_an_already_unset_key_is_not_an_error(self):
        # Post-condition semantics, which is what makes a retry after a partial
        # failure safe.
        self.adapter.reset(CHILD_BINDING)
        self.adapter.reset(CHILD_BINDING)
        self.assertIsNone(self.adapter.read(CHILD_BINDING))


@requires_gsettings
class ValueTyping(AdapterTestCase):
    def test_a_string_key_round_trips_exactly(self):
        for text in ("<Super>Return", "gapplication launch org.gnome.Console", "", "ünïcödé"):
            with self.subTest(value=text):
                self.adapter.apply(CHILD_COMMAND, TypedValue("s", text))
                self.assertEqual(self.adapter.read(CHILD_COMMAND), TypedValue("s", text))

    def test_a_boolean_key_reads_as_a_boolean(self):
        self.assertEqual(
            self.adapter.read_effective(CHILD_LOCKSCREEN), TypedValue("b", False)
        )
        self.adapter.apply(CHILD_LOCKSCREEN, TypedValue("b", True))
        self.assertEqual(self.adapter.read(CHILD_LOCKSCREEN), TypedValue("b", True))

    def test_an_empty_string_array_keeps_its_element_type(self):
        # The case a text round trip through `gsettings get` loses: `[]` alone
        # is untyped, and a value that came back untyped could not be compared
        # against a declaration.
        self.adapter.apply(PARENT_LIST, TypedValue("as", ()))
        value = self.adapter.read(PARENT_LIST)
        self.assertEqual(value.type, "as")
        self.assertEqual(value.data, ())

    def test_writing_the_wrong_type_is_refused_before_it_reaches_glib(self):
        with self.assertRaises(BackendError) as caught:
            self.adapter.apply(CHILD_BINDING, TypedValue("b", True))
        self.assertIn("expects type 's'", str(caught.exception))
        self.assertIsNone(self.adapter.read(CHILD_BINDING))

    def test_writing_a_list_to_a_string_key_is_refused(self):
        with self.assertRaises(BackendError):
            self.adapter.apply(CHILD_BINDING, TypedValue("as", ("a",)))


@requires_gsettings
class AdapterErrors(AdapterTestCase):
    """Every failure GLib would turn into an abort becomes a BackendError."""

    def test_a_missing_schema(self):
        with self.assertRaises(BackendError) as caught:
            self.adapter.read("gsettings:org.kedra.NoSuchSchema:some-key")
        self.assertIn("is not installed", str(caught.exception))

    def test_a_missing_key(self):
        with self.assertRaises(BackendError) as caught:
            self.adapter.read(f"gsettings:{PARENT_SCHEMA}:no-such-key")
        self.assertIn("has no key", str(caught.exception))

    def test_a_relocatable_schema_with_no_path(self):
        with self.assertRaises(BackendError) as caught:
            self.adapter.read(f"gsettings:{CHILD_SCHEMA}:binding")
        self.assertIn("relocatable", str(caught.exception))

    def test_a_fixed_schema_with_a_path(self):
        with self.assertRaises(BackendError) as caught:
            self.adapter.read(f"gsettings:{PARENT_SCHEMA}:{TERMINAL_SHORTCUT_PATH}:{PARENT_KEY}")
        self.assertIn("declares its own path", str(caught.exception))

    def test_a_malformed_target(self):
        with self.assertRaises(BackendError):
            self.adapter.read("not-a-target")

    def test_a_key_outside_the_value_vocabulary(self):
        """`org.gnome.Console scrollback-lines` is int64; the vocabulary has no 'x'.

        Refused rather than approximated: a lossy read would be recorded as
        `original` and written back later as if it were the user's own value.
        The key is real and is the exact one Kedra Terminal reviewed and chose
        not to ship -- see docs/kedra-terminal.md.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        iso = IsolatedGSettings(
            Path(tmp.name), extra_schemas=("org.gnome.Console.gschema.xml",)
        )
        adapter = iso.adapter()

        with self.assertRaises(BackendError) as caught:
            adapter.read("gsettings:org.gnome.Console:scrollback-lines")
        self.assertIn("outside the value vocabulary", str(caught.exception))
        self.assertIn("'x'", str(caught.exception))

    def test_a_write_to_a_read_only_store(self):
        read_only = self.iso.read_only_adapter()
        with self.assertRaises(BackendError) as caught:
            read_only.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.assertIn("not writable", str(caught.exception))

    def test_a_reset_on_a_read_only_store(self):
        read_only = self.iso.read_only_adapter()
        with self.assertRaises(BackendError) as caught:
            read_only.reset(CHILD_BINDING)
        self.assertIn("not writable", str(caught.exception))

    def test_a_failed_write_leaves_nothing_behind(self):
        read_only = self.iso.read_only_adapter()
        with self.assertRaises(BackendError):
            read_only.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.assertIsNone(read_only.read(CHILD_BINDING))


@requires_gsettings
class IsolationProofTests(AdapterTestCase):
    """The claim "this touched nothing of yours", measured rather than argued."""

    def test_writes_land_only_in_the_temporary_store(self):
        self.adapter.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.adapter.apply(PARENT_LIST, TypedValue("as", (TERMINAL_SHORTCUT_PATH,)))

        written = {path.name for path in self.iso.files_written()}
        self.assertEqual(
            written,
            {
                "settings.keyfile",
                "gschemas.compiled",
                "org.gnome.settings-daemon.plugins.media-keys.gschema.xml",
            },
        )
        contents = self.iso.store_contents()
        self.assertIn("kedra-terminal", contents)

    def test_the_temporary_store_is_not_under_the_user_home(self):
        home = str(Path.home().resolve())
        self.assertFalse(str(self.iso.store.resolve()).startswith(home + os.sep))

    def test_the_live_dconf_database_is_untouched(self):
        # setUp/cleanup already assert this for every test in the file; stating
        # it as its own test means the guarantee has a name in the output.
        self.adapter.apply(CHILD_BINDING, TypedValue("s", "<Super>Return"))
        self.adapter.apply(PARENT_LIST, TypedValue("as", ("/x/",)))
        self.adapter.reset(CHILD_BINDING)
        self.assertTrue(self.live.unchanged(), self.live.differences())

    def test_the_adapter_never_reads_the_systems_schemas(self):
        """A schema present on the system but absent from the fixture must fail.

        Proof that `schema_dir` really redirects the lookup rather than merely
        adding to it -- otherwise a test could pass by reading the developer's
        installed schemas.
        """
        with self.assertRaises(BackendError):
            self.adapter.read("gsettings:org.gnome.mutter:dynamic-workspaces")


@requires_gsettings
class ScrubbedEnvironmentTests(unittest.TestCase):
    """The adapter, driven from a process with no inherited environment.

    Everything above runs in-process with an explicitly constructed backend,
    which is isolation by construction. This runs the same operations in a
    subprocess started with `env -i` -- no HOME, no XDG_*, no
    DBUS_SESSION_BUS_ADDRESS, no DCONF_PROFILE, no GSETTINGS_BACKEND -- so
    nothing can be inherited from the developer's session even by accident.
    """

    SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
from kedra_experience.gsettings import GSettingsAdapter
from kedra_experience.values import TypedValue

adapter = GSettingsAdapter(schema_dir=sys.argv[2], backend=_backend(sys.argv[3]))
target = sys.argv[4]
out = {"initial_stored": adapter.read(target) and adapter.read(target).to_dict(),
       "initial_effective": adapter.read_effective(target).to_dict()}
adapter.apply(target, TypedValue("s", "<Super>Return"))
out["after_write"] = adapter.read(target).to_dict()
adapter.reset(target)
out["after_reset"] = adapter.read(target) and adapter.read(target).to_dict()
print(json.dumps(out))
"""

    HELPER = """
def _backend(path):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    return Gio.keyfile_settings_backend_new(path, "/", None)
"""

    def test_the_adapter_works_with_no_inherited_environment(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        live = LiveStateProof()
        iso = IsolatedGSettings(Path(tmp.name))

        script = Path(tmp.name) / "drive.py"
        script.write_text(self.HELPER + self.SCRIPT, encoding="utf-8")

        result = subprocess.run(
            [
                "env",
                "-i",
                f"PATH={os.environ.get('PATH', '/usr/bin')}",
                sys.executable,
                str(script),
                str(REPO_ROOT / "tools"),
                str(iso.schema_dir),
                str(iso.store),
                CHILD_BINDING,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        import json

        payload = json.loads(result.stdout)
        self.assertIsNone(payload["initial_stored"])
        self.assertEqual(payload["initial_effective"], {"type": "s", "data": ""})
        self.assertEqual(payload["after_write"], {"type": "s", "data": "<Super>Return"})
        self.assertIsNone(payload["after_reset"])

        self.assertTrue(live.unchanged(), live.differences())
        self.assertIn("kedra-terminal", iso.store_contents())


class AdapterContractTests(unittest.TestCase):
    """Things true without GLib installed at all."""

    def test_the_adapter_is_a_backend_adapter(self):
        from kedra_experience.backends import BackendAdapter

        self.assertTrue(issubclass(GSettingsAdapter, BackendAdapter))

    def test_constructing_one_performs_no_io(self):
        """Schema lookup is deferred, so building an adapter opens nothing.

        It matters because the CLI imports this module unconditionally, and a
        machine with no GNOME must stay able to validate manifests and run the
        offline suite.
        """
        live = LiveStateProof()
        GSettingsAdapter(schema_dir="/nonexistent/schemas")
        self.assertTrue(live.unchanged(), live.differences())

    def test_a_missing_schema_directory_becomes_a_backend_error(self):
        adapter = GSettingsAdapter(schema_dir="/nonexistent/schemas")
        with self.assertRaises(BackendError):
            adapter.read(CHILD_BINDING)


if __name__ == "__main__":
    unittest.main()
