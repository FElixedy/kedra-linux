#!/usr/bin/env python3
"""Accelerator conflicts, against real GLib and real GNOME schemas.

The offline suite (tests/test_conflicts.py) proves the logic. This proves the
logic is talking to the same thing GNOME is: genuine
`org.gnome.settings-daemon.plugins.media-keys` schemas, compiled by the real
`glib-compile-schemas`, read through real `Gio.Settings`, with accelerators
parsed by the same GTK function gnome-settings-daemon parses them with.

The scenario is the one that prompted the work. The reference machine has a
custom shortcut at `custom1/` bound to `<Super>Return` launching Black Box.
That shortcut is *evidence*, not fixture: it is never read, written or touched
here. It is recreated inside a temporary keyfile store instead, and every test
fingerprints the developer's real dconf before and after.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    DATA_DIR,
    IsolatedGSettings,
    LiveStateProof,
    gsettings_available,
)

from kedra_experience import accelerators, ownership, state  # noqa: E402
from kedra_experience.conflicts import (  # noqa: E402
    CONFLICT_EXTERNAL,
    CONFLICT_SELF,
    PARENT_TARGET,
    child_target,
    discover_custom_shortcuts,
    report_for_shortcut,
)
from kedra_experience.execute import execute_plan  # noqa: E402
from kedra_experience.manifests import SCHEMA_VERSION, Report, load_tree  # noqa: E402
from kedra_experience.observed import observe  # noqa: E402
from kedra_experience.plan import build_plan, declarations_for  # noqa: E402
from kedra_experience.resolve import resolve_state  # noqa: E402
from kedra_experience.shortcuts import TERMINAL_SHORTCUT_PATH, module_shortcuts  # noqa: E402
from kedra_experience.values import TypedValue  # noqa: E402

ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
KEDRA = TERMINAL_SHORTCUT_PATH
CUSTOM0 = ROOT + "custom0/"
CUSTOM1 = ROOT + "custom1/"

SHORTCUT_ID = "terminal-launch-shortcut"

# What the reference machine actually has, recreated in isolation.
NOTES = {"name": "notes", "binding": "<Super>n", "command": "gapplication launch org.gnome.Notes"}
BLACKBOX = {
    "name": "blackbox",
    "binding": "<Super>Return",
    "command": "flatpak run com.raggesilver.BlackBox",
}

requires_real_gsettings = unittest.skipUnless(
    gsettings_available() and accelerators.available(),
    "installed GNOME schemas, glib-compile-schemas, PyGObject and GTK required",
)


@requires_real_gsettings
class RealConflictTestCase(unittest.TestCase):
    """One isolated GSettings environment per test, plus a live-state guard."""

    def setUp(self):
        self.live = LiveStateProof()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.iso = IsolatedGSettings(Path(tmp.name))
        self.addCleanup(self.iso.release)
        self.addCleanup(self._assert_live_state_untouched)
        self.adapter = self.iso.adapter()

        report = Report()
        self.tree = load_tree(DATA_DIR, report)
        assert report.ok, report.errors
        self.shortcut = module_shortcuts(
            "terminal", self.tree.modules["terminal"], self.tree.modules
        )[0]

    def _assert_live_state_untouched(self):
        self.assertTrue(
            self.live.unchanged(),
            f"this test modified live user state: {self.live.differences()}",
        )

    # -- the full read/plan/execute cycle against the real backend ---------

    def cycle(self, user_state=None, operation=ownership.OPERATION_APPLY):
        """Observe, check conflicts, plan and execute -- all through GLib."""
        user_state = user_state or state.UserState(base_preset="kedra")
        conflict = report_for_shortcut(self.adapter, self.shortcut)

        resolution = resolve_state(self.tree, user_state)
        # Read through the same rule production would use: stored values for
        # scalars, the effective list for membership. See `observed.observe`.
        # Only the shortcut's own targets: the isolated fixture holds the
        # media-keys schemas and nothing else, and every other setting in the
        # tree is `policy = "default"`, which the planner leaves alone anyway.
        # Declarations *and* existing records: a disabled module declares
        # nothing, and observing only declarations would hide the values Kedra
        # still owns and has to give back.
        applied = user_state.applied
        watched = [
            d
            for d in declarations_for(self.tree, resolution).values()
            if d.resource == SHORTCUT_ID
        ] + [
            r
            for r in (applied.managed_values if applied else ())
            if r.resource == SHORTCUT_ID
        ]
        observed = observe(self.adapter, watched)
        plan = build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=operation,
            conflicts={SHORTCUT_ID: conflict},
        )
        result = execute_plan(
            plan=plan,
            adapters={"gsettings": self.adapter},
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA_VERSION,
            applied_at="2026-01-02T00:00:00Z",
        )
        return plan, result

    def stored(self, path: str, key: str):
        """The *explicitly stored* value, or None. Not the effective one."""
        return self.adapter.read(child_target(path, key))


class DiscoveryAgainstRealSchemas(RealConflictTestCase):
    def test_it_finds_both_seeded_shortcuts(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)

        found = discover_custom_shortcuts(self.adapter)

        self.assertEqual(found.registered_paths, (CUSTOM0, CUSTOM1))
        self.assertEqual(
            [(s.name, s.binding, s.command) for s in found.shortcuts],
            [
                (NOTES["name"], NOTES["binding"], NOTES["command"]),
                (BLACKBOX["name"], BLACKBOX["binding"], BLACKBOX["command"]),
            ],
        )

    def test_a_registered_but_unconfigured_path_reads_as_unbound(self):
        """A real relocatable instance always resolves; its keys read as ''.

        This is what a stale entry looks like through GSettings, and the reason
        "unreadable" is a genuinely different state from "empty".
        """
        self.adapter.apply(PARENT_TARGET, TypedValue("as", (CUSTOM0,)))

        found = discover_custom_shortcuts(self.adapter)
        one = found.shortcuts[0]

        self.assertTrue(one.readable)
        self.assertFalse(one.configured)
        self.assertEqual(one.binding, "")

    def test_discovery_writes_nothing(self):
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        before = self.iso.store_contents()

        discover_custom_shortcuts(self.adapter)

        self.assertEqual(self.iso.store_contents(), before)

    def test_discovery_leaves_the_store_byte_identical(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        before = self.iso.store.read_bytes()

        for _ in range(3):
            discover_custom_shortcuts(self.adapter)
            report_for_shortcut(self.adapter, self.shortcut)

        self.assertEqual(self.iso.store.read_bytes(), before)


class TheBlackBoxScenario(RealConflictTestCase):
    """The exact situation on the reference machine, reproduced in isolation."""

    def setUp(self):
        super().setUp()
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        self.before_list = self.iso.registered_paths()
        self.before_custom0 = self.iso.shortcut_snapshot(CUSTOM0)
        self.before_custom1 = self.iso.shortcut_snapshot(CUSTOM1)
        self.plan, self.result = self.cycle()

    def test_the_conflict_is_detected(self):
        report = self.plan.conflicts[SHORTCUT_ID]
        self.assertTrue(report.blocked)
        self.assertEqual([f.type for f in report.blocking], [CONFLICT_EXTERNAL])
        self.assertEqual(report.blocking[0].path, CUSTOM1)
        self.assertEqual(report.blocking[0].name, "blackbox")

    def test_the_unrelated_shortcut_that_does_not_clash_is_not_reported(self):
        report = self.plan.conflicts[SHORTCUT_ID]
        self.assertNotIn(CUSTOM0, [f.path for f in report.findings])

    def test_kedra_performs_zero_creation_writes(self):
        self.assertEqual(self.plan.writes, ())
        self.assertEqual(self.result.writes, ())
        self.assertFalse(self.result.committed)

    def test_no_kedra_child_value_is_stored(self):
        """Not merely absent from the list -- never written at all."""
        for key in ("name", "binding", "command"):
            with self.subTest(key=key):
                self.assertIsNone(self.stored(KEDRA, key))

    def test_the_kedra_path_never_enters_the_list(self):
        self.assertNotIn(KEDRA, self.iso.registered_paths())

    def test_the_parent_list_is_semantically_unchanged(self):
        self.assertEqual(self.iso.registered_paths(), self.before_list)

    def test_both_user_shortcuts_are_semantically_unchanged(self):
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM0), self.before_custom0)
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM1), self.before_custom1)

    def test_no_ownership_record_is_created(self):
        self.assertIsNone(self.result.state.applied)

    def test_the_run_reports_success_with_four_blocked_entries(self):
        self.assertTrue(self.result.ok)
        self.assertEqual(len(self.plan.blocked), 4)


class ConflictRemovedThenRetried(RealConflictTestCase):
    """The user rebinds their shortcut; Kedra's next run creates its own."""

    def test_the_full_sequence(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)

        blocked_plan, blocked = self.cycle()
        self.assertTrue(blocked_plan.conflicts[SHORTCUT_ID].blocked)
        self.assertEqual(blocked.writes, ())

        # The user moves Black Box to Super+B, in GNOME Settings. Kedra is not
        # involved; the write is made the way a user's would be.
        self.adapter.apply(child_target(CUSTOM1, "binding"), TypedValue("s", "<Super>b"))

        plan, result = self.cycle(user_state=blocked.state)

        self.assertFalse(plan.conflicts[SHORTCUT_ID].blocked)
        self.assertEqual(len(plan.writes), 4)
        self.assertTrue(result.ok)

        # Kedra's shortcut now exists, appended after the user's two.
        self.assertEqual(self.iso.registered_paths(), (CUSTOM0, CUSTOM1, KEDRA))
        self.assertEqual(
            self.iso.shortcut_snapshot(KEDRA),
            {
                "name": "Kedra Terminal",
                "binding": "<Super>Return",
                "command": "gapplication launch org.gnome.Console",
            },
        )
        # And theirs kept the change *they* made, not one Kedra made for them.
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM1)["binding"], "<Super>b")
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM0), NOTES)

    def test_removing_the_conflicting_shortcut_entirely_also_frees_it(self):
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        _, blocked = self.cycle()
        self.assertEqual(blocked.writes, ())

        # The user deletes the shortcut: GNOME clears the values and drops the
        # path from the list.
        for key in ("name", "binding", "command"):
            self.adapter.reset(child_target(CUSTOM1, key))
        self.adapter.apply(PARENT_TARGET, TypedValue("as", ()))

        plan, result = self.cycle(user_state=blocked.state)

        self.assertFalse(plan.conflicts[SHORTCUT_ID].blocked)
        self.assertTrue(result.ok)
        self.assertEqual(self.iso.registered_paths(), (KEDRA,))


class ModifierOrderAgainstRealGtk(RealConflictTestCase):
    """A differently-spelled equivalent still blocks, through the whole stack."""

    def test_an_equivalently_spelled_accelerator_conflicts(self):
        # GNOME Settings writes <Shift><Super>1; a hand-edited value might carry
        # <Super><Shift>1. One combination, two spellings.
        self.iso.seed_custom_shortcut(
            CUSTOM0, name="theirs", binding="<Super><Shift>1", command="true"
        )

        report = report_for_shortcut(self.adapter, self.shortcut)
        self.assertFalse(report.blocked)  # Kedra wants <Super>Return, not this

        found = discover_custom_shortcuts(self.adapter)
        held = found.by_path(CUSTOM0).accelerator()
        self.assertEqual(held, accelerators.parse("<Shift><Super>1"))
        self.assertEqual(held.canonical, "<Shift><Super>1")

    def test_a_reordered_super_return_still_blocks(self):
        # <Super><Shift>Return is a *different* combination; the one that must
        # block is an actual reordering of Kedra's own.
        self.iso.seed_custom_shortcut(
            CUSTOM0, name="theirs", binding="<Super>Return", command="true"
        )
        blocked = report_for_shortcut(self.adapter, self.shortcut)
        self.assertTrue(blocked.blocked)

        self.adapter.apply(
            child_target(CUSTOM0, "binding"), TypedValue("s", "<Super><Shift>Return")
        )
        freed = report_for_shortcut(self.adapter, self.shortcut)
        self.assertFalse(freed.blocked)


class KedraOwnedThenExternalConflictAppears(RealConflictTestCase):
    """Kedra registered first; the user adds a clashing shortcut afterwards."""

    def test_neither_shortcut_is_touched(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        _, created = self.cycle()
        self.assertTrue(created.ok)
        self.assertEqual(self.iso.registered_paths(), (CUSTOM0, KEDRA))
        kedra_before = self.iso.shortcut_snapshot(KEDRA)

        # The user now creates their own <Super>Return shortcut.
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        list_before = self.iso.registered_paths()

        plan, result = self.cycle(user_state=created.state)

        report = plan.conflicts[SHORTCUT_ID]
        self.assertTrue(report.blocked)
        self.assertEqual([f.path for f in report.blocking], [CUSTOM1])
        self.assertEqual(
            [f.path for f in report.findings if f.type == CONFLICT_SELF], [KEDRA]
        )

        # Nothing was written by that run, in either direction.
        self.assertEqual(result.writes, ())
        self.assertEqual(self.iso.registered_paths(), list_before)
        self.assertEqual(self.iso.shortcut_snapshot(KEDRA), kedra_before)
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM1), BLACKBOX)

        # And Kedra still owns its four values -- it did not abandon them.
        self.assertEqual(
            sorted(r.setting_id for r in result.state.applied.managed_values),
            sorted(
                f"{SHORTCUT_ID}-{part}"
                for part in ("binding", "command", "name", "registration")
            ),
        )


class DisableUnderConflict(RealConflictTestCase):
    """A conflict must not trap Kedra's shortcut in place."""

    def test_terminal_can_still_be_switched_off_cleanly(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        _, created = self.cycle()
        self.assertEqual(self.iso.registered_paths(), (CUSTOM0, KEDRA))

        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)

        off = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
            applied=created.state.applied,
        )
        plan, result = self.cycle(user_state=off)

        self.assertEqual(plan.blocked, ())
        self.assertTrue(result.ok)
        # Kedra's path is gone; both of the user's remain, in order.
        self.assertEqual(self.iso.registered_paths(), (CUSTOM0, CUSTOM1))
        for key in ("name", "binding", "command"):
            self.assertIsNone(self.stored(KEDRA, key))
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM1), BLACKBOX)


class RestoreUnderConflict(RealConflictTestCase):
    """Explicit restore may reclaim Kedra's resource, never free the accelerator."""

    def test_restore_does_not_steal_the_accelerator(self):
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        before = self.iso.shortcut_snapshot(CUSTOM1)
        list_before = self.iso.registered_paths()

        plan, result = self.cycle(operation=ownership.OPERATION_RESTORE)

        self.assertEqual(len(plan.blocked), 4)
        self.assertEqual(result.writes, ())
        self.assertEqual(self.iso.shortcut_snapshot(CUSTOM1), before)
        self.assertEqual(self.iso.registered_paths(), list_before)
        for key in ("name", "binding", "command"):
            self.assertIsNone(self.stored(KEDRA, key))


class ParentListIntegrityUnderRealGSettings(RealConflictTestCase):
    def test_a_duplicate_entry_is_neither_deduplicated_nor_reordered(self):
        self.iso.seed_custom_shortcut(CUSTOM0, **NOTES)
        self.adapter.apply(PARENT_TARGET, TypedValue("as", (CUSTOM0, CUSTOM1, CUSTOM0)))
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        before = self.iso.registered_paths()

        found = discover_custom_shortcuts(self.adapter)
        self.assertEqual(found.duplicate_paths, (CUSTOM0,))

        _, result = self.cycle()

        self.assertEqual(result.writes, ())
        self.assertEqual(self.iso.registered_paths(), before)

    def test_a_stale_path_survives_a_successful_apply(self):
        self.adapter.apply(PARENT_TARGET, TypedValue("as", (CUSTOM0,)))

        plan, result = self.cycle()

        self.assertEqual(plan.blocked, ())
        self.assertTrue(result.ok)
        # Appended, not tidied: the empty entry the user was part-way through
        # creating is exactly where they left it.
        self.assertEqual(self.iso.registered_paths(), (CUSTOM0, KEDRA))
        self.assertFalse(discover_custom_shortcuts(self.adapter).by_path(CUSTOM0).configured)


class IsolationProof(RealConflictTestCase):
    """The developer's own Black Box shortcut is evidence, never fixture."""

    def test_everything_lands_in_the_temporary_store(self):
        self.iso.seed_custom_shortcut(CUSTOM1, **BLACKBOX)
        self.cycle()

        contents = self.iso.store_contents()
        self.assertIn("custom-keybindings", contents)
        self.assertIn("blackbox", contents)
        self.assertTrue(self.live.unchanged(), self.live.differences())

    def test_the_real_user_shortcut_list_is_never_read(self):
        """Proof the fixture is not the developer's machine.

        The isolated store starts empty, so if the adapter were reaching the
        live session this would find the real `custom1/` entry.
        """
        self.assertEqual(self.iso.registered_paths(), ())
        self.assertEqual(discover_custom_shortcuts(self.adapter).shortcuts, ())


if __name__ == "__main__":
    unittest.main()
