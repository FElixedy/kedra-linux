"""Tests for GSettings vendor-default rendering.

Three layers, deliberately:

1. Pure rendering -- GVariant text, escaping, refusal to render what cannot be
   a vendor default. No GNOME required, runs anywhere.
2. Correspondence -- the shipped override and the shipped manifests say the
   same thing, checked in both directions so neither can gain an entry alone.
3. Reality -- the rendered file compiles with the real glib-compile-schemas
   against the real installed GNOME schemas, in a temporary directory, and the
   values GLib then reports are the ones the manifest asked for. Skipped where
   GNOME is not installed (CI), because a skip is honest and a mock is not.

Nothing here reads or writes the invoking user's GSettings or dconf. Layer 3
compiles into a scratch prefix and reads it back with GSETTINGS_BACKEND=memory,
so no dconf database is opened at all.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    DATA_DIR,
    EXPECTED_VENDOR_DEFAULTS,
    GNOME_DESKTOP_SCOPED_DEFAULTS,
    GNOME_SHELL_OVERRIDE_FILE,
    KEDRA_WORKSPACE_COUNT,
    TreeTestCase,
    typed,
)

from kedra_experience import manifests, vendordefaults
from kedra_experience.values import TypedValue

SYSTEM_SCHEMA_DIR = Path("/usr/share/glib-2.0/schemas")


def _unscoped(section: str) -> str:
    """`org.gnome.mutter:GNOME` -> `org.gnome.mutter`."""
    return section.split(":", 1)[0]


def shipped_tree() -> manifests.Tree:
    report = manifests.Report()
    tree = manifests.load_tree(DATA_DIR, report)
    assert report.ok, report.rendered()
    return tree


def parse_override(text: str) -> dict[str, dict[str, str]]:
    """Parse an override file into {schema: {key: raw GVariant text}}.

    Reads it the way glib-compile-schemas does: a GKeyFile whose values are
    taken raw, with no interpolation and no unescaping.
    """
    parser = configparser.RawConfigParser()
    parser.optionxform = str  # keys are case-sensitive GSettings key names
    parser.read_string(text)
    return {
        section: dict(parser.items(section)) for section in parser.sections()
    }


# The schemas the shipped override refers to. Compiling without them is not a
# failure worth reporting -- glib-compile-schemas is right to reject an override
# for a schema that is not installed -- so the test skips rather than fails on a
# machine that merely has GLib.
REQUIRED_SCHEMA_FILES = (
    "org.gnome.desktop.wm.keybindings.gschema.xml",
    "org.gnome.desktop.wm.preferences.gschema.xml",
    "org.gnome.mutter.gschema.xml",
    "org.gnome.shell.gschema.xml",
)


def have_glib_compiler() -> bool:
    if shutil.which("glib-compile-schemas") is None or not SYSTEM_SCHEMA_DIR.is_dir():
        return False
    return all((SYSTEM_SCHEMA_DIR / name).is_file() for name in REQUIRED_SCHEMA_FILES)


def compile_prefix(prefix: Path, *, with_gnome_overrides: bool = True) -> subprocess.CompletedProcess:
    """Copy the system schemas into `prefix` and compile it. Never touches /usr.

    GNOME's own `.gschema.override` files are copied in by default, because the
    thing worth testing is whether Kedra's defaults win *against those* -- a
    prefix containing only Kedra's file would prove nothing about precedence.
    """
    patterns = ["*.gschema.xml", "*.enums.xml"]
    if with_gnome_overrides:
        patterns.append("*.gschema.override")
    for pattern in patterns:
        for path in SYSTEM_SCHEMA_DIR.glob(pattern):
            shutil.copy(path, prefix / path.name)
    return subprocess.run(
        ["glib-compile-schemas", "--strict", str(prefix)],
        capture_output=True,
        text=True,
    )


_READBACK = """\
import json, sys
from gi.repository import Gio
prefix, schema, keys = sys.argv[1], sys.argv[2], sys.argv[3:]
source = Gio.SettingsSchemaSource.new_from_directory(prefix, None, True)
settings = Gio.Settings.new_full(source.lookup(schema, True), None, None)
print(json.dumps({k: settings.get_value(k).print_(True) for k in keys}))
"""


def have_pygobject() -> bool:
    return (
        subprocess.run(
            [sys.executable, "-c", "import gi; from gi.repository import Gio"],
            capture_output=True,
        ).returncode
        == 0
    )


def read_defaults(
    prefix: Path, schema: str, keys: list[str], *, desktop: str | None = "GNOME"
) -> dict[str, str]:
    """Read compiled defaults back through GLib itself, in a subprocess.

    A subprocess for two reasons. XDG_CURRENT_DESKTOP -- which is what selects
    between a generic override group and a `[schema:DESKTOP]` one -- is read by
    GLib during schema init and may be cached for the life of the process, so
    varying it in-process would silently test the same thing twice. And setting
    it in the test process would leak into everything after.

    GSETTINGS_BACKEND=memory means no dconf database is opened at all, so the
    invoking user's values are neither read nor written.
    """
    env = dict(os.environ, GSETTINGS_BACKEND="memory")
    if desktop is None:
        env.pop("XDG_CURRENT_DESKTOP", None)
    else:
        env["XDG_CURRENT_DESKTOP"] = desktop

    result = subprocess.run(
        [sys.executable, "-c", _READBACK, str(prefix), schema, *keys],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(f"read-back failed:\n{result.stderr}")
    return json.loads(result.stdout)


class GVariantTextTests(unittest.TestCase):
    """The value serializer. Typed, escaped, and narrow on purpose."""

    def test_renders_every_declared_value_type(self):
        cases = [
            (typed("b", True), "true"),
            (typed("b", False), "false"),
            (typed("i", 7), "7"),
            (typed("i", -7), "-7"),
            (typed("d", 1), "1.0"),
            (typed("d", 1.5), "1.5"),
            (typed("s", "kgx"), "'kgx'"),
            (typed("as", ["<Super>q"]), "['<Super>q']"),
            (typed("as", ["a", "b"]), "['a', 'b']"),
            (typed("ai", [1, 2]), "[1, 2]"),
        ]
        for value, expected in cases:
            with self.subTest(value=value.render()):
                self.assertEqual(vendordefaults.gvariant_text(value), expected)

    def test_empty_arrays_are_type_annotated(self):
        # Bare `[]` is untyped. glib-compile-schemas would accept it because it
        # knows the key's type, but the annotated form is unambiguous on its own.
        self.assertEqual(vendordefaults.gvariant_text(typed("as", [])), "@as []")
        self.assertEqual(vendordefaults.gvariant_text(typed("ai", [])), "@ai []")

    def test_quotes_and_backslashes_are_escaped(self):
        self.assertEqual(vendordefaults.gvariant_text(typed("s", "it's")), "'it\\'s'")
        self.assertEqual(vendordefaults.gvariant_text(typed("s", "a\\b")), "'a\\\\b'")
        self.assertEqual(
            vendordefaults.gvariant_text(typed("as", ["it's"])), "['it\\'s']"
        )

    def test_newline_in_a_value_is_refused(self):
        # A GKeyFile value is one line; silently emitting two would produce a
        # file that compiles into something nobody declared.
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.gvariant_text(typed("s", "a\nb"))

    def test_invalid_value_is_refused(self):
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.gvariant_text(TypedValue(type="as", data=[1]))

    def test_unknown_type_is_refused(self):
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.gvariant_text(TypedValue(type="(ss)", data=("a", "b")))


class RenderTests(TreeTestCase):
    """Which declarations become override entries, and which cannot."""

    def _tree_with(self, settings: str) -> manifests.Tree:
        from support import base_preset_toml, module_toml

        root = self.make_tree(
            modules={"solo": module_toml("solo", settings=settings)},
            presets={"kedra": base_preset_toml("kedra", {"solo": True})},
        )
        return self.load(root)

    def test_only_default_policy_gsettings_declarations_are_rendered(self):
        from support import gsettings_toml

        tree = self._tree_with(
            gsettings_toml("as-default", key="one", policy="default")
            + gsettings_toml("as-managed", key="two", policy="managed")
        )
        entries = vendordefaults.entries_for(
            vendordefaults.vendor_default_declarations(tree)
        )
        self.assertEqual([e.setting_id for e in entries], ["as-default"])

    def test_module_activation_does_not_affect_rendering(self):
        # A vendor default is delivered by a *package*. Rendering from a
        # resolution would make the shipped file depend on the builder's
        # profile, which is exactly the drift this design avoids.
        from support import base_preset_toml, gsettings_toml, module_toml

        settings = gsettings_toml("as-default", key="one", policy="default")
        root = self.make_tree(
            modules={"solo": module_toml("solo", settings=settings)},
            presets={"kedra": base_preset_toml("kedra", {"solo": False})},
        )
        tree = self.load(root)
        self.assertIn("one", vendordefaults.render_module(tree, "solo"))

    def test_relocatable_path_target_is_refused(self):
        # The Super+Enter shape. A per-path value cannot be a vendor default;
        # the renderer must say so rather than emit a section that would apply
        # to every instance of the schema.
        declaration = vendordefaults.SettingDeclaration(
            id="custom-binding",
            backend="gsettings",
            policy="default",
            target={
                "schema": "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
                "key": "binding",
                "path": "/org/gnome/settings-daemon/plugins/media-keys/"
                "custom-keybindings/kedra-terminal/",
            },
            value=typed("s", "<Super>Return"),
            module_id="workflow",
            module_version="0.3.0",
        )
        with self.assertRaises(vendordefaults.OverrideError) as caught:
            vendordefaults.entries_for([declaration])
        self.assertIn("managed", str(caught.exception))

    def test_two_declarations_for_one_key_are_refused(self):
        declarations = [
            vendordefaults.SettingDeclaration(
                id=f"setting-{n}",
                backend="gsettings",
                policy="default",
                target={"schema": "org.example.test", "key": "same-key"},
                value=typed("as", [f"<Super>{n}"]),
                module_id="solo",
                module_version="0.1.0",
            )
            for n in (1, 2)
        ]
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.entries_for(declarations)

    def test_output_is_deterministic_and_sorted(self):
        tree = shipped_tree()
        for module_id in vendordefaults.modules_with_vendor_defaults(tree):
            with self.subTest(module=module_id):
                first = vendordefaults.render_module(tree, module_id)
                self.assertEqual(first, vendordefaults.render_module(tree, module_id))

                # Schemas in sorted order, each immediately followed by its
                # desktop-scoped twin. Not raw lexicographic order of the
                # section lines: ']' sorts after ':', so `[X:GNOME]` would come
                # before `[X]` and the scoped copy would read as the primary one.
                sections = [
                    line.strip("[]") for line in first.splitlines() if line.startswith("[")
                ]
                schemas = [s for s in sections if ":" not in s]
                self.assertEqual(schemas, sorted(schemas))
                self.assertEqual(
                    sections,
                    [
                        section
                        for schema in schemas
                        for section in (schema, f"{schema}:{vendordefaults.DESKTOP_SCOPE}")
                    ],
                )
                for schema, keys in parse_override(first).items():
                    self.assertEqual(list(keys), sorted(keys))

    def test_header_marks_the_file_generated(self):
        tree = shipped_tree()
        for module_id in vendordefaults.modules_with_vendor_defaults(tree):
            with self.subTest(module=module_id):
                text = vendordefaults.render_module(tree, module_id)
                self.assertIn("GENERATED FILE", text)
                self.assertIn(
                    f"override_format_version: {vendordefaults.OVERRIDE_FORMAT_VERSION}",
                    text,
                )
                self.assertIn(f"'{module_id}' module", text)

    def test_a_module_without_vendor_defaults_renders_nothing(self):
        # An empty override would be a package that exists for no reason and a
        # removal that changes nothing.
        tree = shipped_tree()
        self.assertNotIn("appearance", vendordefaults.modules_with_vendor_defaults(tree))
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.render_module(tree, "appearance")

    def test_an_unknown_module_is_refused(self):
        with self.assertRaises(vendordefaults.OverrideError):
            vendordefaults.render_module(shipped_tree(), "no-such-module")


class ShippedOverrideTests(unittest.TestCase):
    """The overrides and the manifests must not be able to disagree.

    Runs per module, over `EXPECTED_VENDOR_DEFAULTS` -- so a new module that
    starts shipping an override without an entry there fails rather than going
    unchecked.
    """

    def setUp(self):
        self.tree = shipped_tree()
        self.modules = vendordefaults.modules_with_vendor_defaults(self.tree)

    def parsed_for(self, module_id: str) -> dict[str, dict[str, str]]:
        return parse_override(vendordefaults.render_module(self.tree, module_id))

    def test_exactly_the_expected_modules_ship_an_override(self):
        self.assertEqual(sorted(self.modules), sorted(EXPECTED_VENDOR_DEFAULTS))

    def test_every_declaration_appears_in_its_module_override(self):
        for module_id in self.modules:
            parsed = self.parsed_for(module_id)
            for declaration in vendordefaults.vendor_default_declarations(
                self.tree, module_id
            ):
                schema = declaration.target["schema"]
                key = declaration.target["key"]
                with self.subTest(module=module_id, setting=declaration.id):
                    self.assertIn(schema, parsed)
                    self.assertIn(key, parsed[schema])
                    self.assertEqual(
                        parsed[schema][key],
                        vendordefaults.gvariant_text(declaration.value),
                    )

    def test_no_override_contains_anything_its_manifest_does_not_declare(self):
        for module_id in self.modules:
            declared = {
                (d.target["schema"], d.target["key"])
                for d in vendordefaults.vendor_default_declarations(self.tree, module_id)
            }
            rendered = {
                (_unscoped(schema), key)
                for schema, keys in self.parsed_for(module_id).items()
                for key in keys
            }
            with self.subTest(module=module_id):
                self.assertEqual(rendered, declared)

    def test_each_override_matches_the_independent_expectation(self):
        # The second opinion. Deriving this from the manifest would make it a
        # tautology, so `EXPECTED_VENDOR_DEFAULTS` is written out by hand and a
        # shipped value cannot change without changing it too.
        for module_id, expected in EXPECTED_VENDOR_DEFAULTS.items():
            parsed = self.parsed_for(module_id)
            with self.subTest(module=module_id):
                self.assertEqual(
                    {
                        (_unscoped(schema), key): value
                        for schema, keys in parsed.items()
                        for key, value in keys.items()
                    },
                    {
                        target: vendordefaults.gvariant_text(value)
                        for target, value in expected.items()
                    },
                )

    def test_only_the_expected_schemas_are_touched(self):
        self.assertEqual(
            sorted({_unscoped(s) for s in self.parsed_for("workflow")}),
            ["org.gnome.desktop.wm.keybindings", "org.gnome.shell.keybindings"],
        )
        self.assertEqual(
            sorted({_unscoped(s) for s in self.parsed_for("desktop")}),
            ["org.gnome.desktop.wm.preferences", "org.gnome.mutter"],
        )

    def test_no_relocatable_schema_section(self):
        # A section for a relocatable schema would change the defaults of every
        # instance at every path -- including custom shortcuts the user made.
        for module_id in self.modules:
            for schema in self.parsed_for(module_id):
                with self.subTest(module=module_id, schema=schema):
                    self.assertNotIn("custom-keybinding", schema)

    def test_no_setting_is_a_managed_value(self):
        # Nothing shipped writes into a user's dconf. If this fails, a managed
        # setting was added without the real GSettings adapter to carry it.
        for module in self.tree.modules.values():
            for entry in module.get("settings", []):
                with self.subTest(setting=entry.get("id")):
                    self.assertEqual(entry.get("policy"), "default")


class DesktopScopeTests(unittest.TestCase):
    """Every section is emitted generically and scoped to GNOME.

    Not cosmetic: a desktop-scoped override beats a generic one regardless of
    which file it came from, so a generic-only section can lose to a `:GNOME`
    group GNOME shipped -- while looking correct in a diff.
    """

    def setUp(self):
        self.tree = shipped_tree()

    def test_each_schema_appears_both_generically_and_scoped(self):
        for module_id in vendordefaults.modules_with_vendor_defaults(self.tree):
            parsed = parse_override(vendordefaults.render_module(self.tree, module_id))
            generic = {s for s in parsed if ":" not in s}
            scoped = {s for s in parsed if s.endswith(f":{vendordefaults.DESKTOP_SCOPE}")}
            with self.subTest(module=module_id):
                self.assertEqual(len(parsed), len(generic) + len(scoped))
                self.assertEqual(
                    {f"{s}:{vendordefaults.DESKTOP_SCOPE}" for s in generic}, scoped
                )

    def test_the_two_copies_carry_identical_values(self):
        for module_id in vendordefaults.modules_with_vendor_defaults(self.tree):
            parsed = parse_override(vendordefaults.render_module(self.tree, module_id))
            for schema in (s for s in parsed if ":" not in s):
                with self.subTest(module=module_id, schema=schema):
                    self.assertEqual(
                        parsed[schema],
                        parsed[f"{schema}:{vendordefaults.DESKTOP_SCOPE}"],
                    )

    def test_generic_section_precedes_its_scoped_twin(self):
        for module_id in vendordefaults.modules_with_vendor_defaults(self.tree):
            text = vendordefaults.render_module(self.tree, module_id)
            for schema in {
                _unscoped(s) for s in parse_override(text)
            }:
                with self.subTest(module=module_id, schema=schema):
                    self.assertLess(
                        text.index(f"[{schema}]"),
                        text.index(f"[{schema}:{vendordefaults.DESKTOP_SCOPE}]"),
                    )


@unittest.skipUnless(
    have_glib_compiler(), "glib-compile-schemas and installed GNOME schemas required"
)
class SchemaCompilationTests(unittest.TestCase):
    """Layer 3: the files must be real, not merely well-formed.

    Both Kedra override files are installed into one scratch prefix *alongside
    GNOME's own overrides*, which is the arrangement a real system has. Anything
    less would prove the file parses, not that it wins.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.prefix = Path(cls._tmp.name)
        tree = shipped_tree()
        cls.modules = vendordefaults.modules_with_vendor_defaults(tree)
        for module_id in cls.modules:
            (cls.prefix / vendordefaults.override_filename(module_id)).write_text(
                vendordefaults.render_module(tree, module_id), encoding="utf-8"
            )
        cls.result = compile_prefix(cls.prefix)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_compiles_strictly(self):
        self.assertEqual(
            self.result.returncode, 0, f"glib-compile-schemas failed:\n{self.result.stderr}"
        )
        self.assertTrue((self.prefix / "gschemas.compiled").is_file())

    def test_gnome_own_overrides_were_present(self):
        # Otherwise the precedence assertions below would be vacuous.
        self.assertTrue((self.prefix / GNOME_SHELL_OVERRIDE_FILE).is_file())

    def test_kedra_files_sort_after_gnome_own(self):
        for module_id in self.modules:
            with self.subTest(module=module_id):
                self.assertGreater(
                    vendordefaults.override_filename(module_id),
                    GNOME_SHELL_OVERRIDE_FILE,
                )

    def test_nothing_is_written_outside_the_scratch_prefix(self):
        # The compiler is given an explicit directory and produces exactly one
        # artefact in it. The system schema directory is never an argument.
        self.assertNotIn(str(SYSTEM_SCHEMA_DIR), " ".join(self.result.args))

    def test_compiled_defaults_match_the_independent_expectation(self):
        if not have_pygobject():
            self.skipTest("pygobject not installed")

        for module_id, expected in EXPECTED_VENDOR_DEFAULTS.items():
            by_schema: dict[str, list[str]] = {}
            for (schema, key) in expected:
                by_schema.setdefault(schema, []).append(key)

            for schema, keys in by_schema.items():
                actual = read_defaults(self.prefix, schema, keys)
                for key in keys:
                    with self.subTest(module=module_id, schema=schema, key=key):
                        self.assertEqual(
                            actual[key],
                            vendordefaults.gvariant_text(expected[(schema, key)]),
                        )

    def test_kedra_workspace_model_resolves_in_a_gnome_session(self):
        """The headline claim: fixed workspaces, and exactly five of them."""
        if not have_pygobject():
            self.skipTest("pygobject not installed")

        mutter = read_defaults(self.prefix, "org.gnome.mutter", ["dynamic-workspaces"])
        prefs = read_defaults(
            self.prefix, "org.gnome.desktop.wm.preferences", ["num-workspaces"]
        )
        self.assertEqual(mutter["dynamic-workspaces"], "false")
        self.assertEqual(prefs["num-workspaces"], str(KEDRA_WORKSPACE_COUNT))

    def test_the_workspace_model_does_not_depend_on_desktop_detection(self):
        # The generic half of each section. Emitting only [schema:GNOME] would
        # make the value silently absent wherever XDG_CURRENT_DESKTOP is unset.
        if not have_pygobject():
            self.skipTest("pygobject not installed")

        for desktop in ("GNOME", "GNOME-Classic:GNOME", "ubuntu:GNOME", None):
            with self.subTest(desktop=desktop):
                mutter = read_defaults(
                    self.prefix, "org.gnome.mutter", ["dynamic-workspaces"],
                    desktop=desktop,
                )
                prefs = read_defaults(
                    self.prefix, "org.gnome.desktop.wm.preferences", ["num-workspaces"],
                    desktop=desktop,
                )
                self.assertEqual(mutter["dynamic-workspaces"], "false")
                self.assertEqual(prefs["num-workspaces"], str(KEDRA_WORKSPACE_COUNT))


@unittest.skipUnless(
    have_glib_compiler(), "glib-compile-schemas and installed GNOME schemas required"
)
class DesktopScopePrecedenceTests(unittest.TestCase):
    """Why `vendordefaults.DESKTOP_SCOPE` exists.

    The claim it rests on: a desktop-scoped `[schema:GNOME]` override beats a
    generic one *regardless of file order*, so a generic-only 95_ file loses to
    GNOME's scoped 00_ file inside a GNOME session -- and looks fine everywhere
    a developer would casually check.

    That decides the shape of every override Kedra ships, so it is verified
    against the real GLib rather than trusted. Both branches are exercised: the
    naive generic-only file must fail, and the file Kedra actually ships must
    win.
    """

    SCHEMA = "org.gnome.mutter"
    KEY = "dynamic-workspaces"

    def _prefix(self, kedra_override: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prefix = Path(tmp.name)
        (prefix / "95_kedra-probe.gschema.override").write_text(
            kedra_override, encoding="utf-8"
        )
        result = compile_prefix(prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        return prefix

    def test_gnome_really_does_ship_the_scoped_default_this_works_around(self):
        text = (SYSTEM_SCHEMA_DIR / GNOME_SHELL_OVERRIDE_FILE).read_text(encoding="utf-8")
        self.assertIn(f"[{self.SCHEMA}:GNOME]", text)
        for (schema, key), value in GNOME_DESKTOP_SCOPED_DEFAULTS.items():
            with self.subTest(schema=schema, key=key):
                self.assertIn(f"{key}={'true' if value.data else 'false'}", text)

    def test_a_generic_only_override_loses_inside_a_gnome_session(self):
        if not have_pygobject():
            self.skipTest("pygobject not installed")

        prefix = self._prefix(f"[{self.SCHEMA}]\n{self.KEY}=false\n")
        # Later filename, and still beaten: scope wins over file order.
        self.assertEqual(
            read_defaults(prefix, self.SCHEMA, [self.KEY])[self.KEY], "true"
        )
        # ...but it does take effect where no desktop is declared, which is what
        # makes the failure so easy to miss.
        self.assertEqual(
            read_defaults(prefix, self.SCHEMA, [self.KEY], desktop=None)[self.KEY],
            "false",
        )

    def test_the_shape_kedra_ships_wins_everywhere(self):
        if not have_pygobject():
            self.skipTest("pygobject not installed")

        scope = vendordefaults.DESKTOP_SCOPE
        prefix = self._prefix(
            f"[{self.SCHEMA}]\n{self.KEY}=false\n\n"
            f"[{self.SCHEMA}:{scope}]\n{self.KEY}=false\n"
        )
        for desktop in ("GNOME", "GNOME-Classic:GNOME", None):
            with self.subTest(desktop=desktop):
                self.assertEqual(
                    read_defaults(prefix, self.SCHEMA, [self.KEY], desktop=desktop)[
                        self.KEY
                    ],
                    "false",
                )


@unittest.skipUnless(
    have_glib_compiler(), "glib-compile-schemas and installed GNOME schemas required"
)
class RelocatableSchemaLimitTests(unittest.TestCase):
    """The finding that makes Super+Enter a managed setting rather than a default.

    `vendordefaults.RELOCATABLE_SCHEMA_NOTE` claims that an override on a
    relocatable schema applies to every instance at every path. That claim
    decides the whole Super+Enter design, so it is verified against the real
    GLib rather than trusted.
    """

    SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

    def test_a_relocatable_override_leaks_to_every_path(self):
        try:
            import gi  # noqa: F401
            from gi.repository import Gio
        except ImportError:
            self.skipTest("pygobject not installed")

        source_xml = SYSTEM_SCHEMA_DIR / (
            "org.gnome.settings-daemon.plugins.media-keys.gschema.xml"
        )
        if not source_xml.is_file():
            self.skipTest("gnome-settings-daemon schemas not installed")

        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            shutil.copy(source_xml, prefix / source_xml.name)
            (prefix / "99_probe.gschema.override").write_text(
                f"[{self.SCHEMA}]\nbinding='<Super>Return'\n", encoding="utf-8"
            )
            result = subprocess.run(
                ["glib-compile-schemas", "--strict", str(prefix)],
                capture_output=True,
                text=True,
            )
            # GLib accepts it -- which is the problem. It does not scope.
            self.assertEqual(result.returncode, 0, result.stderr)

            os.environ["GSETTINGS_BACKEND"] = "memory"
            source = Gio.SettingsSchemaSource.new_from_directory(str(prefix), None, True)
            schema = source.lookup(self.SCHEMA, False)
            self.assertIsNotNone(schema)
            self.assertIsNone(schema.get_path(), "schema is expected to be relocatable")

            base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
            for path in (base + "custom0/", base + "kedra-terminal/"):
                with self.subTest(path=path):
                    settings = Gio.Settings.new_full(schema, None, path)
                    # An unrelated shortcut the user made would inherit this.
                    self.assertEqual(settings.get_string("binding"), "<Super>Return")


if __name__ == "__main__":
    unittest.main()
