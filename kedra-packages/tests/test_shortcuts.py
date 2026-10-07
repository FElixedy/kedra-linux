#!/usr/bin/env python3
"""Super+Enter: the custom-shortcut resource, end to end and offline.

What this file is really about is one promise:

    Kedra owns *its* shortcut. It does not own the list that shortcut lives in,
    and it does not own a shortcut the user has made their own.

Everything else -- the four expanded settings, the membership projection, the
execution phases, the resource-cohesion rule -- exists to keep that promise
under the awkward cases: a user who rebinds it, a user who adds their own
shortcut afterwards, a module that gets switched off, a write that fails
half-way.

No test here touches a live desktop. The backend is `InMemoryAdapter`; the real
one is exercised in tests/test_gsettings_adapter.py.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import DATA_DIR, REPO_ROOT, TreeTestCase, module_toml  # noqa: E402

from kedra_experience import ownership, shortcuts, state  # noqa: E402
from kedra_experience.applications import (  # noqa: E402
    terminal_application,
    terminal_launch_command,
)
from kedra_experience.backends import InMemoryAdapter  # noqa: E402
from kedra_experience.conflicts import ConflictReport  # noqa: E402
from kedra_experience.execute import execute_plan  # noqa: E402
from kedra_experience.manifests import (  # noqa: E402
    SCHEMA_VERSION,
    Report,
    load_tree,
    module_setting_entries,
)
from kedra_experience.packages import required_packages  # noqa: E402
from kedra_experience.plan import (  # noqa: E402
    PHASE_DEREGISTER,
    PHASE_REGISTER,
    PHASE_VALUE,
    build_plan,
    project_membership,
)
from kedra_experience.resolve import resolve_preset, resolve_state  # noqa: E402
from kedra_experience.values import TypedValue  # noqa: E402

# The shipped contract, written out by hand. A test that reads the manifest and
# checks the manifest agrees with itself proves nothing; changing any of this
# must mean changing this table too.
SHORTCUT_ID = "terminal-launch-shortcut"
SHORTCUT_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/"
)
SHORTCUT_NAME = "Kedra Terminal"
SHORTCUT_BINDING = "<Super>Return"
SHORTCUT_COMMAND = "gapplication launch org.gnome.Console"

CHILD = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
PARENT = "org.gnome.settings-daemon.plugins.media-keys"

T_NAME = f"gsettings:{CHILD}:{SHORTCUT_PATH}:name"
T_BINDING = f"gsettings:{CHILD}:{SHORTCUT_PATH}:binding"
T_COMMAND = f"gsettings:{CHILD}:{SHORTCUT_PATH}:command"
T_LIST = f"gsettings:{PARENT}:custom-keybindings"

ID_NAME = f"{SHORTCUT_ID}-name"
ID_BINDING = f"{SHORTCUT_ID}-binding"
ID_COMMAND = f"{SHORTCUT_ID}-command"
ID_REGISTRATION = f"{SHORTCUT_ID}-registration"

# Two shortcuts the user made, one before Kedra and one after. Neither may ever
# be written, removed or reordered by anything Kedra does.
USER_BEFORE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
USER_ALSO_BEFORE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"
USER_AFTER = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom2/"


def shipped_tree():
    report = Report()
    tree = load_tree(DATA_DIR, report)
    assert report.ok, report.errors
    return tree


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


class ShippedDeclarationTests(unittest.TestCase):
    """What modules/terminal.toml actually claims."""

    def setUp(self):
        with (DATA_DIR / "modules" / "terminal.toml").open("rb") as handle:
            self.manifest = tomllib.load(handle)["module"]

    def test_the_terminal_module_declares_exactly_one_shortcut(self):
        declared = self.manifest["shortcuts"]
        self.assertEqual(len(declared), 1)
        self.assertEqual(declared[0]["id"], SHORTCUT_ID)

    def test_the_declaration_is_managed(self):
        # Kedra's first and only `managed` declaration. If a second appears,
        # this is the test that makes someone justify it.
        self.assertEqual(self.manifest["shortcuts"][0]["policy"], "managed")

    def test_the_declared_values(self):
        declared = self.manifest["shortcuts"][0]
        self.assertEqual(declared["path"], SHORTCUT_PATH)
        self.assertEqual(declared["name"], SHORTCUT_NAME)
        self.assertEqual(declared["binding"], SHORTCUT_BINDING)

    def test_the_command_is_a_typed_role_reference_not_a_command(self):
        declared = self.manifest["shortcuts"][0]
        self.assertEqual(declared["command"], {"application_role": "terminal"})

    def test_the_manifest_declares_no_command_string_at_all(self):
        """The desktop ID is stated once; no declared value names a binary.

        Checked over the manifest's *values* rather than its text: the prose
        above the declaration names `kgx` and `flatpak run` precisely in order
        to say they are rejected, and a check that could not tell an example
        from a declaration would force the reasoning out of the file.
        """
        forbidden = ("kgx", "/usr/bin", "flatpak", "Exec=", "BlackBox")

        def walk(value, trail="module"):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from walk(item, f"{trail}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from walk(item, f"{trail}[{index}]")
            elif isinstance(value, str):
                yield trail, value

        for trail, text in walk(self.manifest):
            # `reason` and `description` are prose fields inside the data; they
            # are allowed to name the alternatives, same as a comment.
            if trail.endswith(("reason", "description")):
                continue
            for needle in forbidden:
                with self.subTest(where=trail, text=needle):
                    self.assertNotIn(needle, text)

    def test_the_terminal_module_still_declares_no_ordinary_settings(self):
        # Terminal v1's reviewed "we ship no org.gnome.Console override" stands.
        # Super+Enter is a shortcut resource, not a Console preference.
        self.assertEqual(self.manifest["settings"], [])

    def test_the_module_version_was_bumped(self):
        self.assertEqual(self.manifest["version"], "0.5.0")


class ShortcutValidationTests(TreeTestCase):
    """Every reason a shortcut declaration is refused."""

    BASE = {
        "id": SHORTCUT_ID,
        "policy": "managed",
        "path": SHORTCUT_PATH,
        "name": SHORTCUT_NAME,
        "binding": SHORTCUT_BINDING,
        "command": "gapplication launch org.example.Thing",
    }

    def problems(self, **overrides):
        entry = dict(self.BASE)
        entry.update(overrides)
        for key in [k for k, v in entry.items() if v is None]:
            del entry[key]
        return shortcuts.shortcut_problems(entry, "shortcut")

    def test_the_base_declaration_is_valid(self):
        self.assertEqual(self.problems(), [])

    def test_a_missing_key_is_reported(self):
        self.assertTrue(any("missing required key 'binding'" in p for p in self.problems(binding=None)))

    def test_an_unknown_key_is_reported(self):
        entry = dict(self.BASE)
        entry["colour"] = "blue"
        self.assertTrue(
            any("unknown key 'colour'" in p for p in shortcuts.shortcut_problems(entry, "s"))
        )

    def test_policy_default_is_refused_with_the_reason(self):
        found = self.problems(policy="default")
        self.assertTrue(any("relocatable schema" in p for p in found), found)
        self.assertTrue(any("only be 'managed'" in p for p in found), found)

    def test_a_path_outside_the_custom_keybindings_root_is_refused(self):
        found = self.problems(path="/org/gnome/desktop/wm/keybindings/kedra-terminal/")
        self.assertTrue(any("must live there" in p for p in found), found)

    def test_a_path_outside_the_kedra_namespace_is_refused(self):
        """`custom3/` is GNOME's to hand out, not Kedra's to take."""
        found = self.problems(
            path="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom3/"
        )
        self.assertTrue(any("Kedra namespace" in p for p in found), found)

    def test_a_malformed_path_is_refused(self):
        found = self.problems(path="not/a/path")
        self.assertTrue(any("absolute GSettings/dconf path" in p for p in found), found)

    def test_an_empty_name_or_binding_is_refused(self):
        for key in ("name", "binding"):
            with self.subTest(key=key):
                found = self.problems(**{key: "   "})
                self.assertTrue(any("non-empty string" in p for p in found), found)

    def test_an_unknown_command_source_is_refused(self):
        found = self.problems(command={"binary_path": "/usr/bin/kgx"})
        self.assertTrue(any("unknown source key" in p for p in found), found)

    def test_a_command_source_with_no_recognised_key_is_refused(self):
        found = self.problems(command={})
        self.assertTrue(any("needs exactly one of" in p for p in found), found)

    def test_a_role_nothing_fills_is_refused(self):
        found = shortcuts.shortcut_problems(
            {**self.BASE, "command": {"application_role": "browser"}}, "s", modules={}
        )
        self.assertTrue(any("no module in this tree nominates" in p for p in found), found)

    def test_a_duplicate_shortcut_id_is_reported_by_the_tree(self):
        body = _shortcut_toml() + _shortcut_toml()
        root = self.make_tree(modules={"terminal": module_toml("terminal", settings=body)})
        _, report = self.load_dirty(root)
        self.assertTrue(
            any("duplicate shortcut ID" in message for _, message in report.errors),
            report.errors,
        )


def _shortcut_toml(
    shortcut_id: str = SHORTCUT_ID,
    *,
    path: str = SHORTCUT_PATH,
    binding: str = SHORTCUT_BINDING,
    command: str = '"gapplication launch org.example.Thing"',
) -> str:
    return f"""
[[module.shortcuts]]
id = "{shortcut_id}"
policy = "managed"
path = "{path}"
name = "Test Shortcut"
binding = "{binding}"
command = {command}
"""


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


class ExpansionTests(unittest.TestCase):
    """One declaration becomes four settings, and exactly which four."""

    def setUp(self):
        self.tree = shipped_tree()
        self.entries = {
            entry["id"]: entry
            for entry in module_setting_entries(
                "terminal", self.tree.modules["terminal"], self.tree.modules
            )
        }

    def test_it_expands_into_exactly_four_settings(self):
        self.assertEqual(
            sorted(self.entries),
            sorted([ID_NAME, ID_BINDING, ID_COMMAND, ID_REGISTRATION]),
        )

    def test_the_three_child_settings_target_the_kedra_path(self):
        for setting_id, key, value in (
            (ID_NAME, "name", SHORTCUT_NAME),
            (ID_BINDING, "binding", SHORTCUT_BINDING),
            (ID_COMMAND, "command", SHORTCUT_COMMAND),
        ):
            with self.subTest(setting=setting_id):
                entry = self.entries[setting_id]
                self.assertEqual(
                    entry["target"],
                    {"schema": CHILD, "path": SHORTCUT_PATH, "key": key},
                )
                self.assertEqual(entry["value"], {"type": "s", "data": value})
                self.assertIsNone(entry.get("member"))

    def test_the_registration_is_a_membership_claim_on_the_parent_list(self):
        entry = self.entries[ID_REGISTRATION]
        self.assertEqual(entry["target"], {"schema": PARENT, "key": "custom-keybindings"})
        self.assertEqual(entry["member"], SHORTCUT_PATH)
        # The value is the claim -- one element -- not a list snapshot.
        self.assertEqual(entry["value"], {"type": "as", "data": [SHORTCUT_PATH]})

    def test_every_expanded_setting_names_the_same_resource(self):
        self.assertEqual(
            {entry["resource"] for entry in self.entries.values()}, {SHORTCUT_ID}
        )

    def test_every_expanded_setting_is_managed(self):
        self.assertEqual({entry["policy"] for entry in self.entries.values()}, {"managed"})

    def test_enable_in_lockscreen_is_deliberately_not_written(self):
        """A key left at its upstream default is a key Kedra does not own."""
        self.assertNotIn("enable-in-lockscreen", shortcuts.CHILD_KEYS)
        for entry in self.entries.values():
            self.assertNotEqual(entry["target"].get("key"), "enable-in-lockscreen")


class CanonicalPathTests(unittest.TestCase):
    """The path is persistent identity, so its properties are asserted."""

    def test_it_is_the_declared_path(self):
        self.assertEqual(shortcuts.TERMINAL_SHORTCUT_PATH, SHORTCUT_PATH)

    def test_it_is_deterministic(self):
        self.assertEqual(shortcuts.kedra_shortcut_path("terminal"), SHORTCUT_PATH)
        self.assertEqual(shortcuts.kedra_shortcut_path("terminal"), SHORTCUT_PATH)

    def test_it_carries_no_user_or_machine_data(self):
        import getpass
        import socket

        for leak in (getpass.getuser(), socket.gethostname(), str(Path.home())):
            if not leak:
                continue
            with self.subTest(leak=leak):
                self.assertNotIn(leak, SHORTCUT_PATH)

    def test_it_has_one_trailing_slash(self):
        self.assertTrue(SHORTCUT_PATH.endswith("/"))
        self.assertFalse(SHORTCUT_PATH.endswith("//"))
        self.assertTrue(SHORTCUT_PATH.startswith("/"))

    def test_it_cannot_collide_with_a_gnome_generated_path(self):
        """GNOME Settings generates `<root>/custom<N>/` and nothing else."""
        segment = SHORTCUT_PATH[len(shortcuts.SHORTCUT_PATH_ROOT):].rstrip("/")
        self.assertTrue(segment.startswith("kedra-"))
        for n in range(0, 64):
            self.assertNotEqual(segment, f"custom{n}")


# ---------------------------------------------------------------------------
# Command derivation
# ---------------------------------------------------------------------------


class LaunchCommandTests(unittest.TestCase):
    def setUp(self):
        self.tree = shipped_tree()

    def test_the_command_is_derived_from_the_terminal_role(self):
        contract = terminal_application(self.tree, resolve_preset(self.tree, "kedra"))
        self.assertEqual(contract.desktop_id, "org.gnome.Console.desktop")
        self.assertEqual(contract.application_id, "org.gnome.Console")
        self.assertEqual(contract.launch_command, SHORTCUT_COMMAND)

    def test_the_expanded_command_matches_the_contract(self):
        entries = {
            e["id"]: e
            for e in module_setting_entries(
                "terminal", self.tree.modules["terminal"], self.tree.modules
            )
        }
        contract = terminal_application(self.tree, resolve_preset(self.tree, "kedra"))
        self.assertEqual(entries[ID_COMMAND]["value"]["data"], contract.launch_command)

    def test_vanilla_can_derive_no_launch_command(self):
        vanilla = resolve_preset(self.tree, "vanilla")
        self.assertIsNone(terminal_application(self.tree, vanilla))
        self.assertIsNone(terminal_launch_command(self.tree, vanilla))

    def test_the_command_names_no_binary_and_no_absolute_path(self):
        command = SHORTCUT_COMMAND
        self.assertNotIn("kgx", command)
        self.assertNotIn("/usr/bin", command)
        self.assertNotIn("/", command)
        self.assertNotIn("flatpak", command)
        self.assertNotIn("BlackBox", command)

    def test_changing_the_desktop_id_changes_the_command(self):
        """The property that makes a terminal selector a one-line edit."""
        self.assertEqual(
            shortcuts.launch_command_for_desktop_id("org.gnome.Ptyxis.desktop"),
            "gapplication launch org.gnome.Ptyxis",
        )

    def test_a_non_desktop_id_cannot_produce_a_command(self):
        for bad in ("kgx", "/usr/bin/kgx", "org.gnome.Console", ""):
            with self.subTest(value=bad):
                with self.assertRaises(shortcuts.ShortcutError):
                    shortcuts.launch_command_for_desktop_id(bad)

    def test_a_word_needing_quoting_is_quoted(self):
        # Never reached by a validated desktop ID, which is the point of
        # validating them; implemented anyway so it cannot one day emit a
        # broken command.
        self.assertEqual(shortcuts.quote_exec_word("plain"), "plain")
        self.assertEqual(shortcuts.quote_exec_word("two words"), '"two words"')
        self.assertEqual(shortcuts.quote_exec_word('say"hi'), '"say\\"hi"')
        self.assertEqual(shortcuts.quote_exec_word("a;b"), '"a;b"')

    def test_a_newline_is_refused_outright(self):
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.quote_exec_word("kgx\nrm -rf /")

    def test_every_shipped_desktop_id_quotes_to_itself(self):
        for contract in self.tree.modules["terminal"]["applications"]:
            desktop_id = contract["desktop_id"]
            app_id = shortcuts.application_id_from_desktop_id(desktop_id)
            self.assertEqual(shortcuts.quote_exec_word(app_id), app_id)


@unittest.skipUnless(
    Path("/usr/share/applications/org.gnome.Console.desktop").is_file(),
    "gnome-console must be installed to check its desktop metadata",
)
class InstalledDesktopMetadataTests(unittest.TestCase):
    """`gapplication launch` only works for a D-Bus activatable application.

    Read-only: the desktop file is parsed as text. Nothing is launched.
    """

    def test_gnome_console_is_dbus_activatable(self):
        text = Path("/usr/share/applications/org.gnome.Console.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("DBusActivatable=true", text)

    def test_the_requirement_is_documented_in_the_source(self):
        self.assertIn("DBusActivatable", shortcuts.LAUNCH_REQUIREMENT)

    def test_the_shipped_terminal_passes_the_launchability_seam(self):
        """The check a future terminal selector must run before nominating one.

        Not part of manifest validation: a build machine with no desktop cannot
        answer it, and a manifest must validate identically everywhere.
        """
        self.assertEqual(
            shortcuts.launchability_problems("org.gnome.Console.desktop"), []
        )

    def test_an_uninstalled_application_is_reported_as_such(self):
        problems = shortcuts.launchability_problems("org.kedra.NoSuchApp.desktop")
        self.assertTrue(any("is installed" in p for p in problems), problems)

    def test_a_binary_name_is_not_a_desktop_id(self):
        for bad in ("kgx", "/usr/bin/kgx", "org.gnome.Console"):
            with self.subTest(value=bad):
                problems = shortcuts.launchability_problems(bad)
                self.assertTrue(
                    any("not a desktop-file ID" in p for p in problems), problems
                )


# ---------------------------------------------------------------------------
# Membership projection
# ---------------------------------------------------------------------------


class MembershipProjectionTests(unittest.TestCase):
    """The whole list reduced to the one question Kedra may ask of it."""

    def test_a_present_member_projects_to_the_claim(self):
        observed = TypedValue("as", (USER_BEFORE, SHORTCUT_PATH, USER_AFTER))
        self.assertEqual(
            project_membership(SHORTCUT_PATH, observed),
            TypedValue("as", (SHORTCUT_PATH,)),
        )

    def test_an_absent_member_projects_to_nothing(self):
        observed = TypedValue("as", (USER_BEFORE, USER_AFTER))
        self.assertIsNone(project_membership(SHORTCUT_PATH, observed))

    def test_an_unset_list_projects_to_nothing(self):
        self.assertIsNone(project_membership(SHORTCUT_PATH, None))

    def test_the_projection_never_carries_a_neighbour(self):
        # The property that makes the ownership records safe: whatever else is
        # in the list, the projection mentions only Kedra's own entry.
        observed = TypedValue("as", (USER_BEFORE, SHORTCUT_PATH, USER_AFTER))
        projected = project_membership(SHORTCUT_PATH, observed)
        self.assertEqual(projected.data, (SHORTCUT_PATH,))
        self.assertNotIn(USER_BEFORE, projected.data)
        self.assertNotIn(USER_AFTER, projected.data)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class ShortcutLifecycleTestCase(unittest.TestCase):
    """A shipped tree, a real state file in memory, and an in-memory backend."""

    def setUp(self):
        self.tree = shipped_tree()

    def state_for(self, preset: str, *, terminal: bool | None = None, managed=()):
        overrides = ()
        if terminal is not None:
            overrides = (state.ModuleOverride(id="terminal", enabled=terminal),)
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=1,
                preset_id=preset,
                preset_version="1.0.0",
                experience_schema_version=SCHEMA_VERSION,
                applied_at="2026-01-01T00:00:00Z",
                modules=(
                    state.AppliedModule(
                        id="terminal", version="0.5.0", active=True, generation=1
                    ),
                ),
                managed_values=tuple(managed),
            )
        return state.UserState(base_preset=preset, module_overrides=overrides, applied=applied)

    def clear_conflicts(self):
        """A report saying the accelerator was checked and is free.

        Passed explicitly by every test that is *not* about conflicts, because
        omitting it does not mean "no conflict" -- it means "nobody looked", and
        the planner blocks on that. Spelling it out is the point: a test cannot
        accidentally exercise the happy path without asserting the check ran.
        """
        return {
            SHORTCUT_ID: ConflictReport.clear(
                accelerator=SHORTCUT_BINDING, resource=SHORTCUT_ID
            )
        }

    def plan_for(
        self, user_state, observed, operation=ownership.OPERATION_APPLY, conflicts=None
    ):
        resolution = resolve_state(self.tree, user_state)
        return build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=operation,
            conflicts=self.clear_conflicts() if conflicts is None else conflicts,
        ), resolution

    def run_plan(
        self,
        user_state,
        observed,
        *,
        operation=ownership.OPERATION_APPLY,
        adapter=None,
        conflicts=None,
    ):
        plan, resolution = self.plan_for(user_state, observed, operation, conflicts)
        adapter = adapter or InMemoryAdapter(
            values={t: v for t, v in observed.items() if v is not None},
            # The parent list's schema default is the empty list, which is what
            # a machine that has never had a custom shortcut reads.
            defaults={T_LIST: TypedValue("as", ())},
        )
        result = execute_plan(
            plan=plan,
            adapters={"gsettings": adapter},
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA_VERSION,
            applied_at="2026-01-02T00:00:00Z",
        )
        return plan, result, adapter

    def kedra_records(self, result):
        return {r.setting_id: r for r in result.state.applied.managed_values}


class CaseOneFirstApply(ShortcutLifecycleTestCase):
    """CASE 1 -- a fresh Kedra user, with two shortcuts of their own already."""

    def setUp(self):
        super().setUp()
        self.observed = {T_LIST: TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE))}
        self.plan, self.result, self.adapter = self.run_plan(
            self.state_for("kedra"), self.observed
        )

    def test_all_four_settings_are_applied(self):
        self.assertEqual(len(self.plan.writes), 4)
        self.assertTrue(self.result.ok)
        self.assertTrue(self.result.committed)

    def test_the_child_values_are_written(self):
        snapshot = self.adapter.snapshot()
        self.assertEqual(snapshot[T_NAME], TypedValue("s", SHORTCUT_NAME))
        self.assertEqual(snapshot[T_BINDING], TypedValue("s", SHORTCUT_BINDING))
        self.assertEqual(snapshot[T_COMMAND], TypedValue("s", SHORTCUT_COMMAND))

    def test_the_path_is_appended_to_the_list_and_nothing_else_moves(self):
        self.assertEqual(
            self.adapter.snapshot()[T_LIST],
            TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE, SHORTCUT_PATH)),
        )

    def test_ownership_records_are_created_for_all_four(self):
        records = self.kedra_records(self.result)
        self.assertEqual(
            sorted(records), sorted([ID_NAME, ID_BINDING, ID_COMMAND, ID_REGISTRATION])
        )
        for record in records.values():
            self.assertEqual(record.resource, SHORTCUT_ID)
            self.assertEqual(record.policy, "managed")

    def test_the_membership_record_names_the_member_and_claims_nothing_else(self):
        record = self.kedra_records(self.result)[ID_REGISTRATION]
        self.assertEqual(record.member, SHORTCUT_PATH)
        self.assertEqual(record.last_applied, TypedValue("as", (SHORTCUT_PATH,)))
        # The decisive assertion: the record does not remember the user's list.
        self.assertNotIn(USER_BEFORE, record.last_applied.data)
        self.assertFalse(record.original_present)

    def test_registration_happens_last(self):
        """Ordering, so no window exists where a live shortcut has no command."""
        order = [c.setting_id for c in self.result.changes if c.wrote]
        self.assertEqual(order[-1], ID_REGISTRATION)
        self.assertEqual(
            self.plan.entries[-1].phase, PHASE_REGISTER
        )
        for entry in self.plan.entries[:-1]:
            self.assertEqual(entry.phase, PHASE_VALUE)

    def test_a_second_apply_changes_nothing(self):
        """Idempotence: re-running must not append the path twice."""
        observed = dict(self.adapter.snapshot())
        plan, result, adapter = self.run_plan(self.result.state, observed)
        self.assertEqual(plan.writes, ())
        self.assertFalse(result.committed)
        self.assertEqual(
            adapter.snapshot()[T_LIST],
            TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE, SHORTCUT_PATH)),
        )

    def test_a_first_apply_on_an_empty_list_works_too(self):
        plan, result, adapter = self.run_plan(self.state_for("kedra"), {})
        self.assertEqual(adapter.snapshot()[T_LIST], TypedValue("as", (SHORTCUT_PATH,)))
        self.assertTrue(result.ok)


class CaseTwoUnchangedUpdate(ShortcutLifecycleTestCase):
    """CASE 2 -- Kedra changes its recommendation and the user has not touched it."""

    def test_a_changed_binding_is_applied_when_nothing_diverged(self):
        records = [
            _record(ID_BINDING, T_BINDING, TypedValue("s", "<Super>t")),
        ]
        user_state = self.state_for("kedra", managed=records)
        observed = {T_BINDING: TypedValue("s", "<Super>t")}

        plan, result, adapter = self.run_plan(user_state, observed)
        entry = next(e for e in plan.entries if e.setting_id == ID_BINDING)

        self.assertEqual(entry.decision, ownership.UPDATE)
        self.assertEqual(adapter.snapshot()[T_BINDING], TypedValue("s", SHORTCUT_BINDING))

    def test_the_original_is_preserved_across_an_update(self):
        records = [
            _record(
                ID_BINDING,
                T_BINDING,
                TypedValue("s", "<Super>t"),
                original=TypedValue("s", "<Super>x"),
            )
        ]
        _, result, _ = self.run_plan(
            self.state_for("kedra", managed=records), {T_BINDING: TypedValue("s", "<Super>t")}
        )
        record = self.kedra_records(result)[ID_BINDING]
        self.assertTrue(record.original_present)
        self.assertEqual(record.original, TypedValue("s", "<Super>x"))


class CaseThreeUserChangedTheBinding(ShortcutLifecycleTestCase):
    """CASE 3 -- the user rebinds Kedra's shortcut. It stays rebound."""

    def setUp(self):
        super().setUp()
        self.records = _full_resource_records()
        self.user_state = self.state_for("kedra", managed=self.records)
        self.observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", "<Super>t"),  # the user's change
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (SHORTCUT_PATH,)),
        }

    def test_the_users_binding_is_preserved_on_a_normal_update(self):
        plan, result, adapter = self.run_plan(self.user_state, self.observed)
        entry = next(e for e in plan.entries if e.setting_id == ID_BINDING)

        self.assertEqual(entry.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertFalse(entry.writes)
        self.assertTrue(entry.requires_confirmation)
        self.assertEqual(adapter.snapshot()[T_BINDING], TypedValue("s", "<Super>t"))

    def test_the_record_keeps_pointing_at_what_kedra_wrote(self):
        """The evidence that makes the divergence detectable forever."""
        _, result, _ = self.run_plan(self.user_state, self.observed)
        # Nothing was committed, so state is the input state -- and the record
        # still says Kedra last wrote <Super>Return.
        record = next(r for r in self.user_state.applied.managed_values if r.setting_id == ID_BINDING)
        self.assertEqual(record.last_applied, TypedValue("s", SHORTCUT_BINDING))
        self.assertFalse(result.committed)

    def test_the_rest_of_the_resource_is_left_alone(self):
        plan, _, _ = self.run_plan(self.user_state, self.observed)
        for setting_id in (ID_NAME, ID_COMMAND, ID_REGISTRATION):
            with self.subTest(setting=setting_id):
                entry = next(e for e in plan.entries if e.setting_id == setting_id)
                self.assertEqual(entry.decision, ownership.NOOP)


class CaseFourUserChangedTheCommand(ShortcutLifecycleTestCase):
    """CASE 4 -- the user points Kedra's shortcut at a different application."""

    def test_a_changed_command_is_preserved(self):
        records = _full_resource_records()
        observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", "flatpak run com.raggesilver.BlackBox"),
            T_LIST: TypedValue("as", (SHORTCUT_PATH,)),
        }
        plan, _, adapter = self.run_plan(self.state_for("kedra", managed=records), observed)
        entry = next(e for e in plan.entries if e.setting_id == ID_COMMAND)

        self.assertEqual(entry.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertEqual(
            adapter.snapshot()[T_COMMAND],
            TypedValue("s", "flatpak run com.raggesilver.BlackBox"),
        )

    def test_a_changed_name_is_preserved_too(self):
        """Explicitly defined rather than left to inference.

        `name` is only a label in GNOME Settings, so treating it as less
        important than `binding` would be defensible -- and is still not what
        happens. Every value of the resource follows the same rule, because a
        rule with an exception is a rule nobody can predict.
        """
        records = _full_resource_records()
        observed = {
            T_NAME: TypedValue("s", "My Terminal"),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (SHORTCUT_PATH,)),
        }
        plan, _, adapter = self.run_plan(self.state_for("kedra", managed=records), observed)
        entry = next(e for e in plan.entries if e.setting_id == ID_NAME)

        self.assertEqual(entry.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertEqual(adapter.snapshot()[T_NAME], TypedValue("s", "My Terminal"))


class CaseFiveDisableUnchanged(ShortcutLifecycleTestCase):
    """CASE 5 -- Terminal switched off, shortcut untouched by the user."""

    def setUp(self):
        super().setUp()
        # The list the user has now: two shortcuts from before Kedra, Kedra's,
        # and one they created afterwards.
        self.list_now = TypedValue(
            "as", (USER_BEFORE, USER_ALSO_BEFORE, SHORTCUT_PATH, USER_AFTER)
        )
        self.observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: self.list_now,
        }
        # `original` for the membership record says the path was NOT in the list
        # before Kedra -- which is what makes removing it correct.
        self.user_state = self.state_for("kedra", terminal=False, managed=_full_resource_records())
        self.plan, self.result, self.adapter = self.run_plan(self.user_state, self.observed)

    def test_only_kedras_entry_leaves_the_list(self):
        """The guarantee, stated as a single assertion."""
        self.assertEqual(
            self.adapter.snapshot()[T_LIST],
            TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE, USER_AFTER)),
        )

    def test_the_shortcut_created_after_kedra_survives(self):
        # The case a list *snapshot* would have destroyed: restoring the list as
        # Kedra first found it would delete custom2 as well.
        self.assertIn(USER_AFTER, self.adapter.snapshot()[T_LIST].data)

    def test_the_child_values_are_reset(self):
        for target in (T_NAME, T_BINDING, T_COMMAND):
            with self.subTest(target=target):
                self.assertTrue(self.adapter.is_unset(target))

    def test_every_kedra_record_is_removed(self):
        self.assertEqual(self.result.state.applied.managed_values, ())

    def test_deregistration_happens_first(self):
        order = [c.setting_id for c in self.result.changes if c.attempted]
        self.assertEqual(order[0], ID_REGISTRATION)
        registration = next(e for e in self.plan.entries if e.setting_id == ID_REGISTRATION)
        self.assertEqual(registration.phase, PHASE_DEREGISTER)

    def test_the_run_succeeded(self):
        self.assertTrue(self.result.ok)
        self.assertTrue(self.result.committed)

    def test_an_empty_list_is_unset_rather_than_written_empty(self):
        """With nothing else in the list, the key returns to untouched."""
        user_state = self.state_for("kedra", terminal=False, managed=_full_resource_records())
        observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (SHORTCUT_PATH,)),
        }
        _, _, adapter = self.run_plan(user_state, observed)
        self.assertTrue(adapter.is_unset(T_LIST))


class CaseFiveWhenThePathPredatedKedra(ShortcutLifecycleTestCase):
    """The mirror case: Kedra found its own path already registered."""

    def test_a_pre_existing_membership_is_not_removed(self):
        records = _full_resource_records(
            registration_original=TypedValue("as", (SHORTCUT_PATH,))
        )
        observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (USER_BEFORE, SHORTCUT_PATH)),
        }
        _, result, adapter = self.run_plan(
            self.state_for("kedra", terminal=False, managed=records), observed
        )
        # Kedra did not put it there, so Kedra does not take it away.
        self.assertEqual(
            adapter.snapshot()[T_LIST], TypedValue("as", (USER_BEFORE, SHORTCUT_PATH))
        )
        self.assertEqual(result.state.applied.managed_values, ())
        registration = next(c for c in result.changes if c.setting_id == ID_REGISTRATION)
        self.assertTrue(registration.succeeded)
        self.assertFalse(registration.wrote)


class CaseSixDisableAfterUserEdit(ShortcutLifecycleTestCase):
    """CASE 6 -- Terminal switched off after the user made the shortcut theirs.

    The least destructive outcome, and the one this file exists to pin down:
    Kedra drops every claim and deletes nothing. The user is left with a
    working, registered shortcut bound the way they bound it.
    """

    def setUp(self):
        super().setUp()
        self.observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", "<Super>t"),  # the user's change
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (USER_BEFORE, SHORTCUT_PATH, USER_AFTER)),
        }
        self.plan, self.result, self.adapter = self.run_plan(
            self.state_for("kedra", terminal=False, managed=_full_resource_records()),
            self.observed,
        )

    def test_nothing_is_written_at_all(self):
        self.assertEqual([c for c in self.result.changes if c.wrote], [])

    def test_the_users_shortcut_still_exists_and_is_still_registered(self):
        snapshot = self.adapter.snapshot()
        self.assertEqual(snapshot[T_BINDING], TypedValue("s", "<Super>t"))
        self.assertEqual(snapshot[T_NAME], TypedValue("s", SHORTCUT_NAME))
        self.assertEqual(snapshot[T_COMMAND], TypedValue("s", SHORTCUT_COMMAND))
        self.assertIn(SHORTCUT_PATH, snapshot[T_LIST].data)

    def test_kedra_stops_claiming_every_part_of_it(self):
        self.assertEqual(self.result.state.applied.managed_values, ())
        resource_ids = {ID_NAME, ID_BINDING, ID_COMMAND, ID_REGISTRATION}
        seen = set()
        for change in self.result.changes:
            if change.setting_id not in resource_ids:
                continue
            seen.add(change.setting_id)
            with self.subTest(setting=change.setting_id):
                self.assertEqual(change.decision, ownership.DROP_RECORD)
        self.assertEqual(seen, resource_ids)

    def test_the_cohesion_reason_names_the_resource(self):
        entry = next(e for e in self.plan.entries if e.setting_id == ID_NAME)
        self.assertIn(SHORTCUT_ID, entry.reason)

    def test_the_list_is_untouched_including_the_users_own_entries(self):
        self.assertEqual(self.adapter.snapshot()[T_LIST], self.observed[T_LIST])


class WhenTheUserDeletedTheShortcutThemselves(ShortcutLifecycleTestCase):
    """A diverged *membership* is not a reason to keep the child values.

    The user removed the shortcut in GNOME Settings, which takes the path out of
    the list. Cleaning up the values Kedra wrote at a path nothing points at is
    tidying, not destruction -- so cohesion deliberately does not fire.
    """

    def test_the_child_values_are_still_cleaned_up(self):
        observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", SHORTCUT_BINDING),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (USER_BEFORE, USER_AFTER)),  # Kedra's is gone
        }
        plan, result, adapter = self.run_plan(
            self.state_for("kedra", terminal=False, managed=_full_resource_records()),
            observed,
        )
        registration = next(e for e in plan.entries if e.setting_id == ID_REGISTRATION)
        self.assertEqual(registration.decision, ownership.DROP_RECORD)
        for target in (T_NAME, T_BINDING, T_COMMAND):
            with self.subTest(target=target):
                self.assertTrue(adapter.is_unset(target))
        self.assertEqual(
            adapter.snapshot()[T_LIST], TypedValue("as", (USER_BEFORE, USER_AFTER))
        )
        self.assertEqual(result.state.applied.managed_values, ())


class CaseSevenExplicitRestore(ShortcutLifecycleTestCase):
    """CASE 7 -- restore Kedra defaults, which may overwrite a user change."""

    def setUp(self):
        super().setUp()
        self.observed = {
            T_NAME: TypedValue("s", SHORTCUT_NAME),
            T_BINDING: TypedValue("s", "<Super>t"),
            T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
            T_LIST: TypedValue("as", (USER_BEFORE, SHORTCUT_PATH)),
        }
        self.plan, self.result, self.adapter = self.run_plan(
            self.state_for("kedra", managed=_full_resource_records()),
            self.observed,
            operation=ownership.OPERATION_RESTORE,
        )

    def test_the_binding_is_reclaimed(self):
        entry = next(e for e in self.plan.entries if e.setting_id == ID_BINDING)
        self.assertEqual(entry.decision, ownership.FORCE_APPLY)
        self.assertEqual(self.adapter.snapshot()[T_BINDING], TypedValue("s", SHORTCUT_BINDING))

    def test_it_requires_confirmation(self):
        entry = next(e for e in self.plan.entries if e.setting_id == ID_BINDING)
        self.assertTrue(entry.requires_confirmation)

    def test_a_restore_never_happens_by_way_of_an_upgrade(self):
        # The same inputs under a normal apply must not reach FORCE_APPLY.
        plan, _ = self.plan_for(
            self.state_for("kedra", managed=_full_resource_records()),
            self.observed,
            ownership.OPERATION_APPLY,
        )
        self.assertEqual(plan.by_decision(ownership.FORCE_APPLY), ())

    def test_the_users_own_shortcut_is_still_not_touched(self):
        self.assertIn(USER_BEFORE, self.adapter.snapshot()[T_LIST].data)


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


class PartialFailureTests(ShortcutLifecycleTestCase):
    def test_a_failed_child_write_does_not_register_the_shortcut(self):
        """The ordering guarantee under failure, not just under success."""
        adapter = InMemoryAdapter(
            defaults={T_LIST: TypedValue("as", ())}, fail_apply={T_COMMAND}
        )
        plan, result, adapter = self.run_plan(
            self.state_for("kedra"), {}, adapter=adapter
        )

        self.assertFalse(result.ok)
        failed = next(c for c in result.changes if c.setting_id == ID_COMMAND)
        self.assertFalse(failed.succeeded)
        self.assertIsNotNone(failed.error)

        # Registration still happens -- entries are independent, and a shortcut
        # registered with two of three values is what the *next* run repairs.
        # What must never happen is a record claiming the command was written.
        records = self.kedra_records(result)
        self.assertNotIn(ID_COMMAND, records)
        self.assertIn(ID_NAME, records)

    def test_a_failed_registration_leaves_no_registration_record(self):
        adapter = InMemoryAdapter(
            defaults={T_LIST: TypedValue("as", ())}, fail_apply={T_LIST}
        )
        _, result, adapter = self.run_plan(self.state_for("kedra"), {}, adapter=adapter)

        self.assertFalse(result.ok)
        self.assertTrue(result.partial)
        records = self.kedra_records(result)
        self.assertNotIn(ID_REGISTRATION, records)
        # The three child values did land, and are recorded truthfully.
        self.assertEqual(
            sorted(records), sorted([ID_NAME, ID_BINDING, ID_COMMAND])
        )
        self.assertNotIn(T_LIST, adapter.snapshot())

    def test_a_retry_after_a_failed_registration_reconciles(self):
        adapter = InMemoryAdapter(
            defaults={T_LIST: TypedValue("as", (USER_BEFORE,))}, fail_apply={T_LIST}
        )
        _, first, adapter = self.run_plan(self.state_for("kedra"), {}, adapter=adapter)
        self.assertFalse(first.ok)

        # The transient failure clears; the next run sees committed state plus a
        # fresh observation and attempts only what is still outstanding.
        adapter.fail_apply.clear()
        observed = dict(adapter.snapshot())
        observed.setdefault(T_LIST, TypedValue("as", (USER_BEFORE,)))
        plan, second, adapter = self.run_plan(first.state, observed, adapter=adapter)

        self.assertTrue(second.ok)
        self.assertEqual([e.setting_id for e in plan.writes], [ID_REGISTRATION])
        self.assertEqual(
            adapter.snapshot()[T_LIST], TypedValue("as", (USER_BEFORE, SHORTCUT_PATH))
        )
        self.assertEqual(len(self.kedra_records(second)), 4)

    def test_a_failed_child_cleanup_keeps_that_record(self):
        """Removal order under failure: deregistered, cleanup still owed."""
        adapter = InMemoryAdapter(
            values={
                T_NAME: TypedValue("s", SHORTCUT_NAME),
                T_BINDING: TypedValue("s", SHORTCUT_BINDING),
                T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
                T_LIST: TypedValue("as", (USER_BEFORE, SHORTCUT_PATH)),
            },
            defaults={T_LIST: TypedValue("as", ())},
            fail_reset={T_BINDING},
        )
        observed = dict(adapter.snapshot())
        _, result, adapter = self.run_plan(
            self.state_for("kedra", terminal=False, managed=_full_resource_records()),
            observed,
            adapter=adapter,
        )

        self.assertFalse(result.ok)
        # Deregistration succeeded, so the user's list is already correct.
        self.assertEqual(adapter.snapshot()[T_LIST], TypedValue("as", (USER_BEFORE,)))
        # And the one value that could not be reset is still claimed, honestly.
        records = self.kedra_records(result)
        self.assertEqual(sorted(records), [ID_BINDING])
        self.assertEqual(records[ID_BINDING].last_applied, TypedValue("s", SHORTCUT_BINDING))

    def test_a_retry_after_a_failed_cleanup_finishes_the_job(self):
        adapter = InMemoryAdapter(
            values={
                T_NAME: TypedValue("s", SHORTCUT_NAME),
                T_BINDING: TypedValue("s", SHORTCUT_BINDING),
                T_COMMAND: TypedValue("s", SHORTCUT_COMMAND),
                T_LIST: TypedValue("as", (USER_BEFORE, SHORTCUT_PATH)),
            },
            defaults={T_LIST: TypedValue("as", ())},
            fail_reset={T_BINDING},
        )
        first_state = self.state_for("kedra", terminal=False, managed=_full_resource_records())
        _, first, adapter = self.run_plan(first_state, dict(adapter.snapshot()), adapter=adapter)

        adapter.fail_reset.clear()
        _, second, adapter = self.run_plan(
            first.state, dict(adapter.snapshot()), adapter=adapter
        )

        self.assertTrue(second.ok)
        self.assertTrue(adapter.is_unset(T_BINDING))
        self.assertEqual(second.state.applied.managed_values, ())
        self.assertEqual(adapter.snapshot()[T_LIST], TypedValue("as", (USER_BEFORE,)))

    def test_a_run_that_only_fails_commits_nothing(self):
        adapter = InMemoryAdapter(
            defaults={T_LIST: TypedValue("as", ())},
            fail_apply={T_NAME, T_BINDING, T_COMMAND, T_LIST},
        )
        _, result, _ = self.run_plan(self.state_for("kedra"), {}, adapter=adapter)
        self.assertFalse(result.ok)
        self.assertFalse(result.committed)
        self.assertIsNone(result.generation)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfileTests(ShortcutLifecycleTestCase):
    """Vanilla / Kedra / Custom, and the Workflow independence claim."""

    def test_vanilla_plans_nothing_and_writes_nothing(self):
        plan, result, adapter = self.run_plan(self.state_for("vanilla"), {})
        self.assertEqual(plan.entries, ())
        self.assertFalse(result.committed)
        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot(), {})

    def test_vanilla_requires_no_gnome_console(self):
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertNotIn("gnome-console", required_packages(self.tree, resolution))

    def test_kedra_wants_the_shortcut_and_requires_gnome_console(self):
        resolution = resolve_preset(self.tree, "kedra")
        self.assertIn("gnome-console", required_packages(self.tree, resolution))
        plan, _ = self.plan_for(self.state_for("kedra"), {})
        self.assertEqual(len(plan.by_resource(SHORTCUT_ID)), 4)

    def test_custom_with_terminal_false_wants_no_shortcut(self):
        user_state = self.state_for("kedra", terminal=False)
        resolution = resolve_state(self.tree, user_state)
        self.assertNotIn("gnome-console", required_packages(self.tree, resolution))
        plan, _ = self.plan_for(user_state, {})
        self.assertEqual(plan.by_resource(SHORTCUT_ID), ())
        self.assertEqual(plan.writes, ())

    def test_custom_with_terminal_true_wants_the_shortcut(self):
        user_state = self.state_for("kedra", terminal=True)
        resolution = resolve_state(self.tree, user_state)
        self.assertIn("gnome-console", required_packages(self.tree, resolution))
        plan, _ = self.plan_for(user_state, {})
        self.assertEqual(len(plan.by_resource(SHORTCUT_ID)), 4)

    def test_the_shortcut_does_not_depend_on_the_workflow_module(self):
        """Super+Enter belongs to Terminal, and owes Workflow nothing.

        With Workflow off, Kedra's terminal shortcut is still declared, still
        planned and still written -- and no workflow package is required for it.
        """
        user_state = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="workflow", enabled=False),),
        )
        resolution = resolve_state(self.tree, user_state)
        plan = build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed={},
            conflicts=self.clear_conflicts(),
        )
        self.assertEqual(len(plan.by_resource(SHORTCUT_ID)), 4)
        self.assertNotIn(
            "kedra-experience-workflow", required_packages(self.tree, resolution)
        )

    def test_the_terminal_module_declares_no_module_relationship(self):
        module = self.tree.modules["terminal"]
        self.assertEqual(module.get("requires", []), [])
        self.assertEqual(module.get("works_best_with", []), [])

    def test_workflow_still_does_not_declare_super_enter(self):
        text = (DATA_DIR / "modules" / "workflow.toml").read_text(encoding="utf-8")
        self.assertNotIn("[[module.shortcuts]]", text)
        for entry in self.tree.modules["workflow"].get("settings", []):
            self.assertNotIn("Return", str(entry.get("value", {})))


class VanillaIsolationTests(unittest.TestCase):
    """The strongest statement of the Vanilla guarantee, checked structurally."""

    def setUp(self):
        self.tree = shipped_tree()

    def test_the_vanilla_preset_applies_no_module(self):
        resolution = resolve_preset(self.tree, "vanilla")
        self.assertFalse(resolution.applies_experience_layer)
        self.assertEqual(resolution.modules, ())

    def test_a_vanilla_plan_cannot_reach_a_backend(self):
        user_state = state.UserState(base_preset="vanilla")
        resolution = resolve_preset(self.tree, "vanilla")
        adapter = InMemoryAdapter()
        plan = build_plan(
            tree=self.tree, resolution=resolution, user_state=user_state, observed={}
        )
        execute_plan(
            plan=plan,
            adapters={"gsettings": adapter},
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA_VERSION,
            applied_at="2026-01-01T00:00:00Z",
        )
        # Not "no writes" -- no operations of any kind, read included.
        self.assertEqual(adapter.operations, [])

    def test_the_declaration_still_exists_in_the_tree_vanilla_ships(self):
        # Declared is not required. The manifest is in the package Vanilla
        # installs; what makes it inert is that no module is enabled.
        self.assertEqual(len(shortcuts.declared_shortcuts(self.tree)), 1)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class MembershipStateTests(unittest.TestCase):
    """The two record fields schema 3 added, and why a bump was needed."""

    def test_the_state_schema_version_is_three(self):
        self.assertEqual(state.STATE_SCHEMA_VERSION, 3)

    def test_a_membership_record_round_trips(self):
        record = _record(
            ID_REGISTRATION,
            T_LIST,
            TypedValue("as", (SHORTCUT_PATH,)),
            resource=SHORTCUT_ID,
            member=SHORTCUT_PATH,
        )
        user_state = state.UserState(
            base_preset="kedra",
            applied=state.AppliedRecord(
                generation=1,
                preset_id="kedra",
                preset_version="1.0.0",
                experience_schema_version=SCHEMA_VERSION,
                applied_at="2026-01-01T00:00:00Z",
                managed_values=(record,),
            ),
        )
        import tomllib as _tomllib

        parsed = _tomllib.loads(user_state.to_toml())
        stored = parsed["applied"]["managed_values"][0]
        self.assertEqual(stored["member"], SHORTCUT_PATH)
        self.assertEqual(stored["resource"], SHORTCUT_ID)

        restored = state.UserState.from_dict(parsed)
        self.assertEqual(restored.applied.managed_values[0], record)

    def test_a_scalar_record_writes_neither_field(self):
        record = _record(
            "workflow-close-window", T_BINDING, TypedValue("s", "x"), resource=None
        )
        user_state = state.UserState(
            base_preset="kedra",
            applied=state.AppliedRecord(
                generation=1,
                preset_id="kedra",
                preset_version="1.0.0",
                experience_schema_version=SCHEMA_VERSION,
                applied_at="2026-01-01T00:00:00Z",
                managed_values=(record,),
            ),
        )
        text = user_state.to_toml()
        self.assertNotIn("member =", text)
        self.assertNotIn("resource =", text)

    def _validate(self, member_line: str, last_applied: str) -> list[str]:
        doc = tomllib.loads(
            f"""state_schema_version = 3

[selection]
base_preset = "kedra"

[applied]
generation = 1
preset_id = "kedra"
preset_version = "1.0.0"
experience_schema_version = {SCHEMA_VERSION}
applied_at = "2026-01-01T00:00:00Z"

[[applied.managed_values]]
setting_id = "{ID_REGISTRATION}"
backend = "gsettings"
target = "{T_LIST}"
module_id = "terminal"
module_version = "0.5.0"
policy = "managed"
generation = 1
last_applied = {last_applied}
original = {{ present = false }}
{member_line}
"""
        )
        report = Report()
        state.validate_state_document(doc, "state.toml", report)
        return [message for _, message in report.errors]

    def test_a_membership_record_may_not_snapshot_the_list(self):
        """The record shape that would delete a user's shortcuts is refused.

        A membership record whose `last_applied` holds the surrounding list is
        claiming ownership of entries Kedra never wrote. Rejecting it in the
        validator means the destructive shape cannot exist on disk at all, not
        merely that no current code path produces one.
        """
        errors = self._validate(
            f'member = "{SHORTCUT_PATH}"',
            f'{{ type = "as", data = ["{USER_BEFORE}", "{SHORTCUT_PATH}"] }}',
        )
        self.assertTrue(
            any("must be exactly" in message for message in errors), errors
        )

    def test_a_well_formed_membership_record_validates(self):
        self.assertEqual(
            self._validate(
                f'member = "{SHORTCUT_PATH}"',
                f'{{ type = "as", data = ["{SHORTCUT_PATH}"] }}',
            ),
            [],
        )

    def test_a_member_that_is_not_a_path_is_rejected(self):
        errors = self._validate(
            'member = "gapplication launch org.gnome.Console"',
            '{ type = "as", data = ["gapplication launch org.gnome.Console"] }',
        )
        self.assertTrue(
            any("absolute GSettings/dconf path" in message for message in errors), errors
        )

    def test_the_state_file_records_no_unrelated_list_entry(self):
        """No entry of the user's list may appear anywhere in Kedra's state."""
        tree = shipped_tree()
        user_state = state.UserState(base_preset="kedra")
        resolution = resolve_state(tree, user_state)
        plan = build_plan(
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            observed={T_LIST: TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE))},
            conflicts={
                SHORTCUT_ID: ConflictReport.clear(
                    accelerator=SHORTCUT_BINDING, resource=SHORTCUT_ID
                )
            },
        )
        adapter = InMemoryAdapter(
            values={T_LIST: TypedValue("as", (USER_BEFORE, USER_ALSO_BEFORE))},
            defaults={T_LIST: TypedValue("as", ())},
        )
        result = execute_plan(
            plan=plan,
            adapters={"gsettings": adapter},
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA_VERSION,
            applied_at="2026-01-01T00:00:00Z",
        )
        text = result.state.to_toml()
        self.assertIn(SHORTCUT_PATH, text)
        for foreign in (USER_BEFORE, USER_ALSO_BEFORE, USER_AFTER):
            with self.subTest(entry=foreign):
                self.assertNotIn(foreign, text)


class MigrationBoundaryTests(ShortcutLifecycleTestCase):
    """Moving a shortcut's path is a migration, and executing without one is refused."""

    def test_a_moved_path_refuses_to_execute(self):
        from kedra_experience.execute import MigrationRequired, check_migrations

        stale_path = (
            "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-old/"
        )
        record = _record(
            ID_BINDING,
            f"gsettings:{CHILD}:{stale_path}:binding",
            TypedValue("s", SHORTCUT_BINDING),
        )
        user_state = self.state_for("kedra", managed=[record])

        with self.assertRaises(MigrationRequired) as caught:
            check_migrations(self.tree, user_state)
        # Named explicitly, because the failure mode without this check is two
        # shortcuts: values still live at the old path, and a new set at the new.
        self.assertIn(stale_path, str(caught.exception))
        self.assertIn(SHORTCUT_PATH, str(caught.exception))

    def test_the_current_path_executes_normally(self):
        from kedra_experience.execute import check_migrations

        check_migrations(self.tree, self.state_for("kedra", managed=_full_resource_records()))


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.terminal = (REPO_ROOT / "docs" / "kedra-terminal.md").read_text(encoding="utf-8")
        self.experience = (REPO_ROOT / "docs" / "kedra-experience.md").read_text(
            encoding="utf-8"
        )

    def test_the_terminal_document_states_the_shortcut(self):
        for expected in (
            SHORTCUT_PATH,
            SHORTCUT_BINDING,
            SHORTCUT_COMMAND,
            "gapplication",
            "custom-keybinding",
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal)

    def test_the_terminal_document_explains_list_membership(self):
        for expected in ("membership", "unrelated"):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal.lower())

    def test_the_experience_document_describes_the_real_adapter(self):
        for expected in ("GSettingsAdapter", "get_user_value", "relocatable"):
            with self.subTest(text=expected):
                self.assertIn(expected, self.experience)

    def test_super_enter_is_no_longer_described_as_blocked(self):
        self.assertNotIn("Not implemented, and intentionally so", self.terminal)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    setting_id,
    target,
    last_applied,
    *,
    original=None,
    resource=SHORTCUT_ID,
    member=None,
):
    return state.ManagedValue(
        setting_id=setting_id,
        backend="gsettings",
        target=target,
        module_id="terminal",
        module_version="0.5.0",
        policy="managed",
        generation=1,
        last_applied=last_applied,
        original=original,
        original_present=original is not None,
        resource=resource,
        member=member,
    )


def _full_resource_records(*, registration_original=None):
    """Records for a shortcut Kedra has already applied once, cleanly."""
    return (
        _record(ID_NAME, T_NAME, TypedValue("s", SHORTCUT_NAME)),
        _record(ID_BINDING, T_BINDING, TypedValue("s", SHORTCUT_BINDING)),
        _record(ID_COMMAND, T_COMMAND, TypedValue("s", SHORTCUT_COMMAND)),
        _record(
            ID_REGISTRATION,
            T_LIST,
            TypedValue("as", (SHORTCUT_PATH,)),
            original=registration_original,
            member=SHORTCUT_PATH,
        ),
    )


if __name__ == "__main__":
    unittest.main()
