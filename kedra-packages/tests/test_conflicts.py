#!/usr/bin/env python3
"""Accelerator conflicts: discovery, normalization, classification, planning.

The promise under test:

    Kedra never takes a keyboard accelerator an unrelated shortcut already
    holds, and never alters that shortcut to free it -- and when it cannot tell
    whether the accelerator is free, it does not register at all.

The second half matters as much as the first. "Could not check" must never
collapse into "no conflict", because the cost of guessing wrong is a key
combination that does one of two things depending on which grab GNOME
registered first.

Offline: the backend is `InMemoryAdapter`. The real GSettings path is exercised
in tests/test_gsettings_conflicts.py.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import DATA_DIR  # noqa: E402

from kedra_experience import accelerators, conflicts, ownership, state  # noqa: E402
from kedra_experience.backends import BackendError, InMemoryAdapter  # noqa: E402
from kedra_experience.conflicts import (  # noqa: E402
    CONFLICT_EXTERNAL,
    CONFLICT_MALFORMED,
    CONFLICT_SELF,
    CONFLICT_UNDETERMINED,
    ConflictReport,
    CustomShortcut,
    child_target,
    classify,
    discover_custom_shortcuts,
    report_for_shortcut,
)
from kedra_experience.execute import execute_plan  # noqa: E402
from kedra_experience.manifests import SCHEMA_VERSION, Report, load_tree  # noqa: E402
from kedra_experience.plan import build_plan  # noqa: E402
from kedra_experience.resolve import resolve_state  # noqa: E402
from kedra_experience.shortcuts import TERMINAL_SHORTCUT_PATH, module_shortcuts  # noqa: E402
from kedra_experience.values import TypedValue  # noqa: E402

ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
KEDRA = TERMINAL_SHORTCUT_PATH
CUSTOM0 = ROOT + "custom0/"
CUSTOM1 = ROOT + "custom1/"
CUSTOM2 = ROOT + "custom2/"

PARENT_TARGET = conflicts.PARENT_TARGET
SHORTCUT_ID = "terminal-launch-shortcut"
SHORTCUT_BINDING = "<Super>Return"

T_NAME = child_target(KEDRA, "name")
T_BINDING = child_target(KEDRA, "binding")
T_COMMAND = child_target(KEDRA, "command")

requires_gtk = unittest.skipUnless(
    accelerators.available(), "GTK is required for accelerator normalization"
)


def fake(registered=(), shortcuts=None, *, fail_read=()):
    """An adapter describing a system with these custom shortcuts.

    Values are *defaults*, not explicit writes, so `adapter.writes` stays empty
    unless something actually writes -- which is how the read-only claim is
    tested rather than asserted.
    """
    defaults = {}
    if registered is not None:
        defaults[PARENT_TARGET] = TypedValue("as", tuple(registered))
    for path, fields in (shortcuts or {}).items():
        for key in ("name", "binding", "command"):
            defaults[child_target(path, key)] = TypedValue("s", fields.get(key, ""))
    return InMemoryAdapter(defaults=defaults, fail_read=set(fail_read))


# ---------------------------------------------------------------------------
# Accelerator normalization
# ---------------------------------------------------------------------------


@requires_gtk
class NormalizationTests(unittest.TestCase):
    def test_identical_text_is_equal(self):
        self.assertTrue(accelerators.equivalent("<Super>Return", "<Super>Return"))

    def test_modifier_order_does_not_matter(self):
        """The reason raw-string comparison is not good enough.

        GNOME Settings writes one order and a hand-edited dconf value may carry
        another; they are one combination, and a checker that missed that would
        register a shortcut straight into a live conflict.
        """
        self.assertTrue(accelerators.equivalent("<Super><Shift>1", "<Shift><Super>1"))
        self.assertEqual(
            accelerators.parse("<Super><Shift>1"), accelerators.parse("<Shift><Super>1")
        )

    def test_equivalent_spellings_hash_alike(self):
        self.assertEqual(
            {accelerators.parse("<Super><Shift>1"), accelerators.parse("<Shift><Super>1")},
            {accelerators.parse("<Shift><Super>1")},
        )

    def test_an_alias_canonicalises(self):
        # <Primary> is GTK's portable spelling of Control.
        self.assertEqual(accelerators.parse("<Primary>q").canonical, "<Control>q")
        self.assertTrue(accelerators.equivalent("<Primary>q", "<Control>q"))

    def test_different_accelerators_are_not_equal(self):
        for other in ("<Super>t", "<Shift><Super>Return", "Return", "<Control>Return"):
            with self.subTest(other=other):
                self.assertFalse(accelerators.equivalent("<Super>Return", other))

    def test_the_empty_accelerator_is_unbound(self):
        self.assertIsNone(accelerators.parse(""))
        self.assertIsNone(accelerators.parse("   "))

    def test_two_unbound_accelerators_do_not_clash(self):
        # Answering True here would make every pair of blank shortcuts a
        # conflict, and a half-created shortcut would block Kedra forever.
        self.assertFalse(accelerators.equivalent("", ""))

    def test_an_invalid_accelerator_raises(self):
        for bad in ("nonsense-!!", "<NoSuchMod>x", "<Super><Nope>q"):
            with self.subTest(value=bad):
                with self.assertRaises(accelerators.AcceleratorError):
                    accelerators.parse(bad)

    def test_a_modifier_with_no_key_is_unbound(self):
        """GTK accepts "<Super>"; it still names no key, so nothing is grabbed.

        Reported as unbound rather than as a combination, or two shortcuts that
        both grab nothing would be declared in conflict with each other.
        """
        self.assertIsNone(accelerators.parse("<Super>"))

    def test_a_non_string_raises(self):
        with self.assertRaises(accelerators.AcceleratorError):
            accelerators.parse(17)

    def test_the_canonical_form_is_gtks_own(self):
        self.assertEqual(accelerators.parse("<Super>Return").canonical, "<Super>Return")
        self.assertEqual(accelerators.parse("<Super><Shift>1").canonical, "<Shift><Super>1")


class NoFallbackParserTests(unittest.TestCase):
    """There is deliberately no hand-written accelerator parser."""

    def test_the_decision_is_recorded_in_the_source(self):
        self.assertIn("no conflict", accelerators.NO_FALLBACK_NOTE)
        self.assertIn("unknown", accelerators.NO_FALLBACK_NOTE)

    def test_unavailability_is_a_distinct_exception(self):
        # A caller must be able to tell "your text is wrong" from "this machine
        # cannot tell you". Handling them together is how an unparseable
        # environment gets treated as an unparseable value.
        self.assertFalse(
            issubclass(accelerators.AcceleratorUnavailable, accelerators.AcceleratorError)
        )

    def test_the_module_names_no_modifier_vocabulary_of_its_own(self):
        """Proof by absence: a fallback parser would need one.

        If somebody ever adds `SHIFT_MASK = 1` or a keysym table here, this
        fails -- which is the moment to ask whether GTK's answer is being
        second-guessed.
        """
        source = (
            Path(__file__).resolve().parent.parent
            / "tools"
            / "kedra_experience"
            / "accelerators.py"
        ).read_text(encoding="utf-8")
        for smell in ("SHIFT_MASK", "CONTROL_MASK", "MOD1_MASK", "keysym", "KEYSYMS"):
            with self.subTest(text=smell):
                self.assertNotIn(smell, source)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class DiscoveryTests(unittest.TestCase):
    def test_a_system_with_no_custom_shortcuts(self):
        found = discover_custom_shortcuts(fake(registered=()))
        self.assertEqual(found.registered_paths, ())
        self.assertEqual(found.shortcuts, ())
        self.assertTrue(found.ok)

    def test_an_unset_list_is_a_system_with_none(self):
        found = discover_custom_shortcuts(InMemoryAdapter())
        self.assertEqual(found.shortcuts, ())
        self.assertTrue(found.ok)

    def test_one_shortcut(self):
        found = discover_custom_shortcuts(
            fake(
                registered=(CUSTOM1,),
                shortcuts={
                    CUSTOM1: {
                        "name": "blackbox",
                        "binding": "<Super>Return",
                        "command": "flatpak run com.raggesilver.BlackBox",
                    }
                },
            )
        )
        self.assertEqual(len(found.shortcuts), 1)
        one = found.shortcuts[0]
        self.assertEqual(one.path, CUSTOM1)
        self.assertEqual(one.name, "blackbox")
        self.assertEqual(one.binding, "<Super>Return")
        self.assertEqual(one.command, "flatpak run com.raggesilver.BlackBox")
        self.assertTrue(one.readable)
        self.assertEqual(one.binding_state, conflicts.BINDING_BOUND)

    def test_multiple_shortcuts_keep_the_users_order(self):
        found = discover_custom_shortcuts(
            fake(
                registered=(CUSTOM2, CUSTOM0, CUSTOM1),
                shortcuts={p: {"binding": "<Super>F1"} for p in (CUSTOM0, CUSTOM1, CUSTOM2)},
            )
        )
        self.assertEqual(found.registered_paths, (CUSTOM2, CUSTOM0, CUSTOM1))

    def test_a_stale_path_is_reported_not_repaired(self):
        """Registered but never configured: a blank row in GNOME Settings.

        A user part-way through creating a shortcut looks exactly like this.
        Removing it would destroy work in progress, so it is read and left.
        """
        found = discover_custom_shortcuts(
            fake(registered=(CUSTOM0,), shortcuts={CUSTOM0: {}})
        )
        one = found.shortcuts[0]
        self.assertTrue(one.readable)
        self.assertFalse(one.configured)
        self.assertEqual(one.binding_state, conflicts.BINDING_UNBOUND)
        self.assertEqual(found.registered_paths, (CUSTOM0,))

    def test_a_partially_configured_shortcut(self):
        found = discover_custom_shortcuts(
            fake(registered=(CUSTOM0,), shortcuts={CUSTOM0: {"name": "half done"}})
        )
        one = found.shortcuts[0]
        self.assertEqual(one.name, "half done")
        self.assertEqual(one.binding, "")
        self.assertTrue(one.configured)
        self.assertEqual(one.binding_state, conflicts.BINDING_UNBOUND)

    def test_an_unreadable_shortcut_does_not_stop_the_others(self):
        """One broken entry must not take the whole planner down."""
        adapter = fake(
            registered=(CUSTOM0, CUSTOM1),
            shortcuts={
                CUSTOM0: {"binding": "<Super>F1"},
                CUSTOM1: {"binding": "<Super>Return"},
            },
            fail_read=(child_target(CUSTOM0, "binding"),),
        )
        found = discover_custom_shortcuts(adapter)
        self.assertEqual(len(found.shortcuts), 2)
        broken = found.by_path(CUSTOM0)
        self.assertFalse(broken.readable)
        self.assertEqual(broken.binding_state, conflicts.BINDING_UNKNOWN)
        # The healthy one was still read.
        self.assertEqual(found.by_path(CUSTOM1).binding, "<Super>Return")

    def test_an_unreadable_list_is_a_problem_not_an_empty_system(self):
        adapter = InMemoryAdapter(fail_read={PARENT_TARGET})
        found = discover_custom_shortcuts(adapter)
        self.assertFalse(found.ok)
        self.assertEqual(found.shortcuts, ())

    def test_a_duplicate_entry_is_reported_and_left_alone(self):
        found = discover_custom_shortcuts(
            fake(
                registered=(CUSTOM0, CUSTOM1, CUSTOM0),
                shortcuts={CUSTOM0: {"binding": "<Super>F1"}, CUSTOM1: {}},
            )
        )
        self.assertEqual(found.registered_paths, (CUSTOM0, CUSTOM1, CUSTOM0))
        self.assertEqual(found.duplicate_paths, (CUSTOM0,))
        # One shortcut object per distinct path: a duplicate is one shortcut
        # named twice, not two shortcuts.
        self.assertEqual(len(found.shortcuts), 2)

    def test_discovery_performs_no_writes(self):
        adapter = fake(
            registered=(CUSTOM0, CUSTOM1),
            shortcuts={CUSTOM0: {"binding": "<Super>F1"}, CUSTOM1: {"binding": "<Super>Return"}},
        )
        discover_custom_shortcuts(adapter)
        self.assertEqual(adapter.writes, [])

    def test_discovery_reads_only_the_media_keys_tree(self):
        """No dconf dump, and nothing outside the one list Kedra takes part in."""
        adapter = fake(
            registered=(CUSTOM1,), shortcuts={CUSTOM1: {"binding": "<Super>Return"}}
        )
        discover_custom_shortcuts(adapter)
        for _, target in adapter.operations:
            with self.subTest(target=target):
                self.assertTrue(
                    target == PARENT_TARGET
                    or target.startswith(
                        "gsettings:org.gnome.settings-daemon.plugins.media-keys"
                    ),
                    target,
                )

    def test_only_three_keys_per_shortcut_are_read(self):
        adapter = fake(registered=(CUSTOM1,), shortcuts={CUSTOM1: {}})
        discover_custom_shortcuts(adapter)
        keys = {t.rsplit(":", 1)[1] for _, t in adapter.operations if t != PARENT_TARGET}
        self.assertEqual(keys, {"name", "binding", "command"})


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@requires_gtk
class ClassificationTests(unittest.TestCase):
    def report(self, registered=(), shortcuts=None, *, binding=SHORTCUT_BINDING, **kw):
        found = discover_custom_shortcuts(fake(registered, shortcuts, **kw))
        return classify(
            desired_binding=binding,
            owned_path=KEDRA,
            discovery=found,
            resource=SHORTCUT_ID,
        )

    def test_a_free_accelerator_is_clear(self):
        report = self.report((CUSTOM0,), {CUSTOM0: {"binding": "<Super>F1"}})
        self.assertTrue(report.checked)
        self.assertFalse(report.blocked)
        self.assertEqual(report.findings, ())

    def test_an_empty_system_is_clear(self):
        report = self.report()
        self.assertFalse(report.blocked)

    def test_an_external_conflict_blocks(self):
        report = self.report(
            (CUSTOM1,),
            {CUSTOM1: {"name": "blackbox", "binding": "<Super>Return",
                       "command": "flatpak run com.raggesilver.BlackBox"}},
        )
        self.assertTrue(report.blocked)
        self.assertEqual(len(report.blocking), 1)
        finding = report.blocking[0]
        self.assertEqual(finding.type, CONFLICT_EXTERNAL)
        self.assertEqual(finding.path, CUSTOM1)
        self.assertEqual(finding.name, "blackbox")
        self.assertEqual(finding.binding, "<Super>Return")
        self.assertIn("blackbox", finding.reason)

    def test_a_conflict_by_a_different_spelling_still_blocks(self):
        report = self.report(
            (CUSTOM1,),
            {CUSTOM1: {"name": "theirs", "binding": "<Super><Shift>1"}},
            binding="<Shift><Super>1",
        )
        self.assertTrue(report.blocked)
        self.assertEqual(report.blocking[0].type, CONFLICT_EXTERNAL)

    def test_the_canonical_kedra_path_is_never_an_external_conflict(self):
        """Finding Kedra's accelerator at Kedra's path is ownership, not a clash."""
        report = self.report(
            (KEDRA,),
            {KEDRA: {"name": "Kedra Terminal", "binding": "<Super>Return"}},
        )
        self.assertFalse(report.blocked)
        self.assertEqual([f.type for f in report.findings], [CONFLICT_SELF])
        self.assertFalse(report.findings[0].blocking)

    def test_kedra_plus_an_unrelated_conflict_reports_both(self):
        report = self.report(
            (KEDRA, CUSTOM1),
            {
                KEDRA: {"name": "Kedra Terminal", "binding": "<Super>Return"},
                CUSTOM1: {"name": "blackbox", "binding": "<Super>Return"},
            },
        )
        self.assertTrue(report.blocked)
        self.assertEqual(
            [(f.type, f.path) for f in report.findings],
            [(CONFLICT_EXTERNAL, CUSTOM1), (CONFLICT_SELF, KEDRA)],
        )

    def test_an_unbound_shortcut_never_conflicts(self):
        report = self.report((CUSTOM0,), {CUSTOM0: {"name": "half made"}})
        self.assertFalse(report.blocked)
        self.assertEqual(report.findings, ())

    def test_an_unparseable_binding_is_an_advisory_not_a_conflict(self):
        """GNOME cannot grab what GTK cannot parse, so it is not in the way.

        Said out loud anyway: the user probably meant it to be a shortcut.
        """
        report = self.report((CUSTOM0,), {CUSTOM0: {"name": "typo", "binding": "<Supr>Return"}})
        self.assertFalse(report.blocked)
        self.assertEqual([f.type for f in report.findings], [CONFLICT_MALFORMED])
        self.assertFalse(report.findings[0].blocking)

    def test_an_unreadable_shortcut_is_undetermined_and_blocks(self):
        """Cannot establish its binding, so cannot establish that it is free."""
        report = self.report(
            (CUSTOM0,),
            {CUSTOM0: {"binding": "<Super>F1"}},
            fail_read=(child_target(CUSTOM0, "binding"),),
        )
        self.assertTrue(report.blocked)
        self.assertEqual([f.type for f in report.findings], [CONFLICT_UNDETERMINED])
        self.assertTrue(report.findings[0].blocking)

    def test_an_unreadable_shortcut_does_not_claim_to_conflict(self):
        report = self.report(
            (CUSTOM0,),
            {CUSTOM0: {"binding": "<Super>F1"}},
            fail_read=(child_target(CUSTOM0, "binding"),),
        )
        self.assertNotIn(CONFLICT_EXTERNAL, [f.type for f in report.findings])
        self.assertIn("cannot be established", report.findings[0].reason)

    def test_a_near_miss_accelerator_does_not_conflict(self):
        # <Shift><Super>Return is a different combination from <Super>Return,
        # however similar it looks.
        report = self.report(
            (CUSTOM2, CUSTOM0, CUSTOM1),
            {
                CUSTOM0: {"name": "a", "binding": "<Super>Return"},
                CUSTOM1: {"name": "b", "binding": "<Shift><Super>Return"},
                CUSTOM2: {"name": "c", "binding": "<Super><Shift>Return"},
            },
        )
        self.assertEqual([f.path for f in report.blocking], [CUSTOM0])

    def test_multiple_conflicts_are_ordered_deterministically(self):
        """Sorted by path, whatever order the user's list holds them in."""
        entries = {
            CUSTOM0: {"name": "a", "binding": "<Super>Return"},
            CUSTOM1: {"name": "b", "binding": "<Super>F2"},
            CUSTOM2: {"name": "c", "binding": "<Super><Shift>Return"},
        }
        entries[CUSTOM2] = {"name": "c", "binding": "<Super>Return"}
        for order in ((CUSTOM0, CUSTOM1, CUSTOM2), (CUSTOM2, CUSTOM1, CUSTOM0)):
            with self.subTest(order=order):
                report = self.report(order, entries)
                self.assertEqual([f.path for f in report.blocking], [CUSTOM0, CUSTOM2])

    def test_an_unreadable_list_makes_the_answer_unknown(self):
        found = discover_custom_shortcuts(InMemoryAdapter(fail_read={PARENT_TARGET}))
        report = classify(
            desired_binding=SHORTCUT_BINDING, owned_path=KEDRA, discovery=found
        )
        self.assertFalse(report.checked)
        self.assertTrue(report.blocked)

    def test_kedras_own_binding_being_invalid_blocks(self):
        report = self.report((CUSTOM0,), {CUSTOM0: {}}, binding="<Supr>Return")
        self.assertTrue(report.blocked)
        self.assertIn("not a valid accelerator", report.block_reason)

    def test_no_finding_carries_anything_beyond_shortcut_metadata(self):
        report = self.report(
            (CUSTOM1,),
            {CUSTOM1: {"name": "blackbox", "binding": "<Super>Return",
                       "command": "flatpak run com.raggesilver.BlackBox"}},
        )
        self.assertEqual(
            set(report.findings[0].to_dict()),
            {"type", "path", "reason", "blocking", "name", "binding", "command"},
        )


class ReportSemanticsTests(unittest.TestCase):
    """"Could not check" is not "no conflict", and there is no way to say it is."""

    def test_unchecked_blocks(self):
        self.assertTrue(ConflictReport.unchecked().blocked)

    def test_unavailable_blocks_and_says_why(self):
        report = ConflictReport.unavailable("GTK is missing")
        self.assertTrue(report.blocked)
        self.assertIn("GTK is missing", report.block_reason)

    def test_clear_is_the_only_non_blocking_constructor(self):
        self.assertFalse(ConflictReport.clear().blocked)

    def test_there_is_no_assume_free_constructor(self):
        """A named constructor meaning "assume it is free" must not exist."""
        for forbidden in ("assume_free", "unchecked_ok", "skip", "ignore"):
            with self.subTest(name=forbidden):
                self.assertFalse(hasattr(ConflictReport, forbidden))

    def test_a_default_report_blocks(self):
        # Even the zero-argument dataclass default is blocking, so a report that
        # got constructed by accident cannot wave a registration through.
        self.assertTrue(ConflictReport().blocked)


class ReportForShortcutSeamTests(unittest.TestCase):
    """The live seam cannot return "clear" without having looked."""

    def setUp(self):
        report = Report()
        self.tree = load_tree(DATA_DIR, report)
        assert report.ok, report.errors
        self.shortcut = module_shortcuts(
            "terminal", self.tree.modules["terminal"], self.tree.modules
        )[0]

    @requires_gtk
    def test_a_clean_system_reports_clear(self):
        result = report_for_shortcut(fake(registered=()), self.shortcut)
        self.assertTrue(result.checked)
        self.assertFalse(result.blocked)

    @requires_gtk
    def test_the_black_box_case_blocks(self):
        adapter = fake(
            (CUSTOM1,),
            {CUSTOM1: {"name": "blackbox", "binding": "<Super>Return",
                       "command": "flatpak run com.raggesilver.BlackBox"}},
        )
        result = report_for_shortcut(adapter, self.shortcut)
        self.assertTrue(result.blocked)
        self.assertEqual(result.blocking[0].path, CUSTOM1)

    def test_a_backend_failure_blocks_rather_than_clearing(self):
        adapter = InMemoryAdapter(fail_read={PARENT_TARGET})
        result = report_for_shortcut(adapter, self.shortcut)
        self.assertFalse(result.checked)
        self.assertTrue(result.blocked)

    def test_the_seam_writes_nothing(self):
        adapter = fake((CUSTOM1,), {CUSTOM1: {"binding": "<Super>Return"}})
        report_for_shortcut(adapter, self.shortcut)
        self.assertEqual(adapter.writes, [])


class NormalizationUnavailableTests(unittest.TestCase):
    """With no authoritative parser, a fresh registration is refused.

    Simulated by making `accelerators.available()` report False, which is what a
    machine without GTK produces. Manifest loading and planning must still work;
    only the semantic answer becomes unknown.
    """

    def setUp(self):
        report = Report()
        self.tree = load_tree(DATA_DIR, report)
        assert report.ok, report.errors
        self.shortcut = module_shortcuts(
            "terminal", self.tree.modules["terminal"], self.tree.modules
        )[0]

        self._saved = (accelerators._probed, accelerators._gtk, accelerators._gtk_error)
        accelerators._probed = True
        accelerators._gtk = None
        accelerators._gtk_error = "simulated: no Gtk typelib"
        self.addCleanup(self._restore)

    def _restore(self):
        accelerators._probed, accelerators._gtk, accelerators._gtk_error = self._saved

    def test_the_capability_reports_itself_unavailable(self):
        self.assertFalse(accelerators.available())
        self.assertIn("simulated", accelerators.unavailable_reason())

    def test_parsing_raises_unavailable_rather_than_guessing(self):
        with self.assertRaises(accelerators.AcceleratorUnavailable):
            accelerators.parse("<Super>Return")

    def test_manifest_loading_still_works(self):
        report = Report()
        tree = load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(len(module_shortcuts(
            "terminal", tree.modules["terminal"], tree.modules)), 1)

    def test_the_conflict_answer_is_unavailable_not_clear(self):
        result = report_for_shortcut(fake(registered=()), self.shortcut)
        self.assertFalse(result.checked)
        self.assertTrue(result.blocked)
        self.assertIn("no authoritative accelerator parser", result.block_reason)

    def test_discovery_still_works_it_just_cannot_compare(self):
        found = discover_custom_shortcuts(
            fake((CUSTOM1,), {CUSTOM1: {"name": "blackbox", "binding": "<Super>Return"}})
        )
        self.assertEqual(found.by_path(CUSTOM1).name, "blackbox")
        # The binding text is there; its *meaning* is what cannot be established.
        self.assertEqual(found.by_path(CUSTOM1).binding, "<Super>Return")
        self.assertEqual(found.by_path(CUSTOM1).binding_state, conflicts.BINDING_UNKNOWN)

    def test_a_fresh_registration_is_conservatively_blocked(self):
        user_state = state.UserState(base_preset="kedra")
        resolution = resolve_state(self.tree, user_state)
        plan = build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed={},
            conflicts={
                SHORTCUT_ID: report_for_shortcut(fake(registered=()), self.shortcut)
            },
        )
        self.assertEqual(plan.writes, ())
        self.assertEqual(len(plan.blocked), 4)


class ShortcutStateHelperTests(unittest.TestCase):
    """The little classifier that decides what is known about a binding."""

    def test_none_is_unknown(self):
        self.assertEqual(
            CustomShortcut(path=CUSTOM0).binding_state, conflicts.BINDING_UNKNOWN
        )

    def test_empty_is_unbound(self):
        self.assertEqual(
            CustomShortcut(path=CUSTOM0, binding="").binding_state,
            conflicts.BINDING_UNBOUND,
        )

    @requires_gtk
    def test_valid_text_is_bound(self):
        self.assertEqual(
            CustomShortcut(path=CUSTOM0, binding="<Super>Return").binding_state,
            conflicts.BINDING_BOUND,
        )

    @requires_gtk
    def test_invalid_text_is_unparseable(self):
        self.assertEqual(
            CustomShortcut(path=CUSTOM0, binding="<Supr>x").binding_state,
            conflicts.BINDING_UNPARSEABLE,
        )

    def test_configured_distinguishes_a_stale_path(self):
        self.assertFalse(
            CustomShortcut(path=CUSTOM0, name="", binding="", command="").configured
        )
        self.assertTrue(CustomShortcut(path=CUSTOM0, name="x", binding="").configured)



# ---------------------------------------------------------------------------
# Planning and execution
# ---------------------------------------------------------------------------


class ConflictPlanningTestCase(unittest.TestCase):
    """The shipped tree, a real state, and an in-memory desktop."""

    def setUp(self):
        report = Report()
        self.tree = load_tree(DATA_DIR, report)
        assert report.ok, report.errors
        self.shortcut = module_shortcuts(
            "terminal", self.tree.modules["terminal"], self.tree.modules
        )[0]

    def state_for(self, *, managed=(), terminal=None):
        overrides = ()
        if terminal is not None:
            overrides = (state.ModuleOverride(id="terminal", enabled=terminal),)
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=1,
                preset_id="kedra",
                preset_version="1.0.0",
                experience_schema_version=SCHEMA_VERSION,
                applied_at="2026-01-01T00:00:00Z",
                modules=(state.AppliedModule(
                    id="terminal", version="0.5.0", active=True, generation=1),),
                managed_values=tuple(managed),
            )
        return state.UserState(base_preset="kedra", module_overrides=overrides, applied=applied)

    def run_with(self, *, desktop, user_state=None, operation=ownership.OPERATION_APPLY):
        """Plan and execute against one described desktop.

        The *same* adapter provides the conflict report, the observed values and
        the execution backend, so nothing can pass because the fixtures disagreed
        with each other.
        """
        user_state = user_state or self.state_for()
        conflict = report_for_shortcut(desktop, self.shortcut)

        observed = {
            PARENT_TARGET: desktop.read_effective(PARENT_TARGET),
            T_NAME: desktop.read_effective(T_NAME),
            T_BINDING: desktop.read_effective(T_BINDING),
            T_COMMAND: desktop.read_effective(T_COMMAND),
        }
        resolution = resolve_state(self.tree, user_state)
        plan = build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=operation,
            conflicts={SHORTCUT_ID: conflict},
        )
        before = list(desktop.operations)
        result = execute_plan(
            plan=plan,
            adapters={"gsettings": desktop},
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA_VERSION,
            applied_at="2026-01-02T00:00:00Z",
        )
        writes = [op for op in desktop.operations[len(before):] if op[0] in ("apply", "reset")]
        return plan, result, writes

    def records(self, result):
        applied = result.state.applied
        return sorted(r.setting_id for r in (applied.managed_values if applied else ()))


BLACKBOX = {
    CUSTOM1: {
        "name": "blackbox",
        "binding": "<Super>Return",
        "command": "flatpak run com.raggesilver.BlackBox",
    }
}

KEDRA_OWNED = {
    KEDRA: {
        "name": "Kedra Terminal",
        "binding": "<Super>Return",
        "command": "gapplication launch org.gnome.Console",
    }
}


def kedra_records(binding="<Super>Return"):
    """Records for a shortcut Kedra has already applied cleanly."""
    def record(setting_id, target, value, member=None):
        return state.ManagedValue(
            setting_id=setting_id, backend="gsettings", target=target,
            module_id="terminal", module_version="0.5.0", policy="managed",
            generation=1, last_applied=value, resource=SHORTCUT_ID, member=member,
        )

    return (
        record(f"{SHORTCUT_ID}-name", T_NAME, TypedValue("s", "Kedra Terminal")),
        record(f"{SHORTCUT_ID}-binding", T_BINDING, TypedValue("s", binding)),
        record(f"{SHORTCUT_ID}-command", T_COMMAND,
               TypedValue("s", "gapplication launch org.gnome.Console")),
        record(f"{SHORTCUT_ID}-registration", PARENT_TARGET,
               TypedValue("as", (KEDRA,)), member=KEDRA),
    )


@requires_gtk
class FreshApplyWithoutConflict(ConflictPlanningTestCase):
    """The unchanged happy path: a checked, free accelerator behaves as before."""

    def test_the_shortcut_is_created(self):
        desktop = fake((CUSTOM0,), {CUSTOM0: {"name": "notes", "binding": "<Super>n"}})
        plan, result, writes = self.run_with(desktop=desktop)

        self.assertEqual(plan.blocked, ())
        self.assertEqual(len(plan.writes), 4)
        self.assertTrue(result.ok)
        self.assertEqual(len(self.records(result)), 4)
        self.assertIn(KEDRA, desktop.snapshot()[PARENT_TARGET].data)

    def test_the_unrelated_shortcut_is_untouched(self):
        desktop = fake((CUSTOM0,), {CUSTOM0: {"name": "notes", "binding": "<Super>n"}})
        self.run_with(desktop=desktop)
        for key in ("name", "binding", "command"):
            self.assertNotIn(child_target(CUSTOM0, key), desktop.snapshot())


@requires_gtk
class FreshApplyBlockedByConflict(ConflictPlanningTestCase):
    """The reference machine: Black Box already owns <Super>Return."""

    def setUp(self):
        super().setUp()
        self.desktop = fake((CUSTOM1,), BLACKBOX)
        self.plan, self.result, self.writes = self.run_with(desktop=self.desktop)

    def test_every_setting_of_the_resource_is_blocked(self):
        self.assertEqual(len(self.plan.blocked), 4)
        self.assertEqual(
            {e.decision for e in self.plan.by_resource(SHORTCUT_ID)},
            {ownership.BLOCKED_BY_CONFLICT},
        )

    def test_zero_writes(self):
        self.assertEqual(self.plan.writes, ())
        self.assertEqual(self.writes, [])
        self.assertEqual(self.result.writes, ())

    def test_zero_ownership_records(self):
        self.assertFalse(self.result.committed)
        self.assertEqual(self.records(self.result), [])

    def test_no_orphan_child_values(self):
        """Nothing is configured and then left dangling at the Kedra path."""
        for key in ("name", "binding", "command"):
            self.assertNotIn(child_target(KEDRA, key), self.desktop.snapshot())

    def test_the_kedra_path_is_not_registered(self):
        self.assertNotIn(KEDRA, self.desktop.snapshot().get(PARENT_TARGET, TypedValue("as", ())).data
                         if PARENT_TARGET in self.desktop.snapshot() else ())

    def test_the_users_shortcut_is_completely_untouched(self):
        # Not "not deleted" -- not written at all, in any of its three keys.
        self.assertEqual(self.desktop.snapshot(), {})

    def test_the_parent_list_is_not_written(self):
        self.assertNotIn(PARENT_TARGET, self.desktop.snapshot())

    def test_the_run_is_reported_as_successful_not_failed(self):
        """Refusing is a correct outcome, not an error.

        A failed *write* means retry; a blocked resource means a human has to
        decide something. Conflating them would make a configurator retry
        forever against a shortcut it is never going to be allowed to take.
        """
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.failures, ())

    def test_the_conflict_is_available_structurally(self):
        report = self.plan.conflicts[SHORTCUT_ID]
        self.assertTrue(report.blocked)
        finding = report.blocking[0]
        self.assertEqual(finding.type, CONFLICT_EXTERNAL)
        self.assertEqual(finding.path, CUSTOM1)
        self.assertEqual(finding.name, "blackbox")

    def test_the_plan_json_carries_the_conflict(self):
        import json

        payload = json.loads(self.plan.to_json())
        self.assertEqual(payload["summary"]["blocked_by_conflict"], 4)
        self.assertTrue(payload["conflicts"][SHORTCUT_ID]["blocked"])
        self.assertEqual(
            payload["conflicts"][SHORTCUT_ID]["findings"][0]["name"], "blackbox"
        )

    def test_blocked_entries_do_not_ask_for_confirmation(self):
        """There is nothing here a user can approve.

        Freeing the accelerator means editing a shortcut Kedra does not own,
        which is a separate action -- so this must not surface as a yes/no.
        """
        for entry in self.plan.blocked:
            self.assertFalse(entry.requires_confirmation)


@requires_gtk
class ConflictResolvedThenRetried(ConflictPlanningTestCase):
    """The user rebinds their shortcut; the next run creates Kedra's normally."""

    def test_retry_after_the_conflict_is_gone(self):
        desktop = fake((CUSTOM1,), BLACKBOX)
        _, first, writes = self.run_with(desktop=desktop)
        self.assertEqual(writes, [])
        self.assertFalse(first.committed)

        # The user moves Black Box to Super+B. Simulated by rewriting the
        # fixture's *defaults*, i.e. the desktop, not by Kedra touching it.
        desktop._defaults[child_target(CUSTOM1, "binding")] = TypedValue("s", "<Super>b")

        plan, second, writes = self.run_with(desktop=desktop, user_state=first.state)

        self.assertEqual(plan.blocked, ())
        self.assertEqual(len(plan.writes), 4)
        self.assertTrue(second.ok)
        self.assertEqual(len(self.records(second)), 4)
        self.assertEqual(
            desktop.snapshot()[PARENT_TARGET].data, (CUSTOM1, KEDRA)
        )

    def test_the_users_rebound_shortcut_keeps_its_own_values(self):
        desktop = fake((CUSTOM1,), BLACKBOX)
        desktop._defaults[child_target(CUSTOM1, "binding")] = TypedValue("s", "<Super>b")
        self.run_with(desktop=desktop)
        for key in ("name", "binding", "command"):
            self.assertNotIn(child_target(CUSTOM1, key), desktop.snapshot())


@requires_gtk
class ExternalConflictAppearsAfterKedraOwnsIt(ConflictPlanningTestCase):
    """Kedra registered first; the user later creates a clashing shortcut."""

    def setUp(self):
        super().setUp()
        # Both registered, both bound to <Super>Return. GNOME resolves this by
        # grab order, which is the ambiguity Kedra refuses to *create* -- but
        # here the user created it, and Kedra's job is to not make it worse.
        self.desktop = fake(
            (KEDRA, CUSTOM1), {**KEDRA_OWNED, **BLACKBOX}
        )
        self.plan, self.result, self.writes = self.run_with(
            desktop=self.desktop, user_state=self.state_for(managed=kedra_records())
        )

    def test_the_conflict_is_detected(self):
        report = self.plan.conflicts[SHORTCUT_ID]
        self.assertTrue(report.blocked)
        self.assertEqual([f.path for f in report.blocking], [CUSTOM1])

    def test_kedras_own_path_is_reported_as_self_not_as_a_conflict(self):
        report = self.plan.conflicts[SHORTCUT_ID]
        self_findings = [f for f in report.findings if f.type == CONFLICT_SELF]
        self.assertEqual([f.path for f in self_findings], [KEDRA])
        self.assertFalse(self_findings[0].blocking)

    def test_nothing_is_written_at_all(self):
        self.assertEqual(self.writes, [])
        self.assertEqual(self.desktop.snapshot(), {})

    def test_kedra_does_not_deregister_itself(self):
        """A conflict appearing later must not make Kedra abandon its shortcut.

        Deregistering would silently hand the accelerator to the newcomer, which
        is an ownership change nobody asked for -- and it would oscillate if the
        user removed theirs again.
        """
        self.assertEqual(
            [e.decision for e in self.plan.by_resource(SHORTCUT_ID)],
            [ownership.NOOP] * 4,
        )
        self.assertNotIn(PARENT_TARGET, self.desktop.snapshot())

    def test_kedras_records_survive_untouched(self):
        self.assertFalse(self.result.committed)
        self.assertEqual(
            sorted(r.setting_id for r in self.result.state.applied.managed_values),
            sorted(r.setting_id for r in kedra_records()),
        )

    def test_the_users_new_shortcut_survives_untouched(self):
        for key in ("name", "binding", "command"):
            self.assertNotIn(child_target(CUSTOM1, key), self.desktop.snapshot())


@requires_gtk
class UserModifiedKedraResourceIsDivergenceNotConflict(ConflictPlanningTestCase):
    """Precedence: ownership decides first, and the gate only refuses writes."""

    def test_a_rebound_kedra_shortcut_is_preserved_as_divergence(self):
        # The user moved Kedra's own shortcut to Super+T. Nothing else holds
        # Super+Return, so there is no external conflict at all -- and their
        # change must be preserved by the *divergence* rule, not by the gate.
        desktop = fake(
            (KEDRA,),
            {KEDRA: {**KEDRA_OWNED[KEDRA], "binding": "<Super>t"}},
        )
        plan, result, writes = self.run_with(
            desktop=desktop, user_state=self.state_for(managed=kedra_records())
        )

        binding = next(e for e in plan.entries if e.setting_id == f"{SHORTCUT_ID}-binding")
        self.assertEqual(binding.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertFalse(binding.blocked_by_conflict)
        self.assertFalse(plan.conflicts[SHORTCUT_ID].blocked)
        self.assertEqual(writes, [])

    def test_the_user_does_not_conflict_with_themselves(self):
        """Kedra's own path holding Kedra's accelerator is never external."""
        desktop = fake((KEDRA,), KEDRA_OWNED)
        plan, _, _ = self.run_with(
            desktop=desktop, user_state=self.state_for(managed=kedra_records())
        )
        report = plan.conflicts[SHORTCUT_ID]
        self.assertFalse(report.blocked)
        self.assertEqual([f.type for f in report.findings], [CONFLICT_SELF])

    def test_divergence_is_not_reopened_by_an_external_conflict(self):
        """A conflict must never rewrite what Kedra last applied.

        `last_applied` is the evidence that makes the user's change detectable
        forever. If a blocked run reset it, the next run would adopt their value
        as Kedra's own.
        """
        desktop = fake(
            (KEDRA, CUSTOM1),
            {KEDRA: {**KEDRA_OWNED[KEDRA], "binding": "<Super>t"}, **BLACKBOX},
        )
        user_state = self.state_for(managed=kedra_records())
        _, result, writes = self.run_with(desktop=desktop, user_state=user_state)

        self.assertEqual(writes, [])
        record = next(
            r for r in result.state.applied.managed_values
            if r.setting_id == f"{SHORTCUT_ID}-binding"
        )
        self.assertEqual(record.last_applied, TypedValue("s", "<Super>Return"))


@requires_gtk
class ExplicitRestoreWithExternalConflict(ConflictPlanningTestCase):
    """Restore may reclaim Kedra's own resource. It may not free the accelerator."""

    def setUp(self):
        super().setUp()
        self.desktop = fake((CUSTOM1,), BLACKBOX)
        self.plan, self.result, self.writes = self.run_with(
            desktop=self.desktop,
            user_state=self.state_for(managed=kedra_records(binding="<Super>t")),
            operation=ownership.OPERATION_RESTORE,
        )

    def test_restore_does_not_delete_the_unrelated_shortcut(self):
        self.assertEqual(self.desktop.snapshot(), {})
        self.assertEqual(self.writes, [])

    def test_restore_does_not_rebind_the_unrelated_shortcut(self):
        self.assertNotIn(child_target(CUSTOM1, "binding"), self.desktop.snapshot())

    def test_restore_is_blocked_rather_than_stealing_the_accelerator(self):
        self.assertEqual(len(self.plan.blocked), 4)
        self.assertEqual(self.plan.writes, ())

    def test_the_reason_says_the_shortcut_will_not_be_altered(self):
        entry = self.plan.blocked[0]
        self.assertIn("will not alter that shortcut", entry.reason)


@requires_gtk
class DisableIsNeverBlocked(ConflictPlanningTestCase):
    """A conflict must not trap Kedra's shortcut.

    Blocking a revert would mean a conflict appearing after installation made
    the Terminal module impossible to switch off cleanly.
    """

    def test_terminal_disable_still_removes_the_kedra_shortcut(self):
        desktop = fake((KEDRA, CUSTOM1), {**KEDRA_OWNED, **BLACKBOX})
        plan, result, _ = self.run_with(
            desktop=desktop,
            user_state=self.state_for(managed=kedra_records(), terminal=False),
        )

        self.assertEqual(plan.blocked, ())
        self.assertTrue(result.ok)
        self.assertEqual(
            desktop.snapshot()[PARENT_TARGET].data, (CUSTOM1,)
        )
        self.assertEqual(self.records(result), [])

    def test_the_unrelated_shortcut_survives_the_disable(self):
        desktop = fake((KEDRA, CUSTOM1), {**KEDRA_OWNED, **BLACKBOX})
        self.run_with(
            desktop=desktop,
            user_state=self.state_for(managed=kedra_records(), terminal=False),
        )
        self.assertIn(CUSTOM1, desktop.snapshot()[PARENT_TARGET].data)
        for key in ("name", "binding", "command"):
            self.assertNotIn(child_target(CUSTOM1, key), desktop.snapshot())


@requires_gtk
class ParentListIntegrityTests(ConflictPlanningTestCase):
    """Scanning must never tidy the user's list."""

    def test_a_blocked_run_leaves_ordering_and_duplicates_alone(self):
        desktop = fake(
            (CUSTOM2, CUSTOM1, CUSTOM2),
            {**BLACKBOX, CUSTOM2: {"name": "dup", "binding": "<Super>d"}},
        )
        found = discover_custom_shortcuts(desktop)
        self.assertEqual(found.registered_paths, (CUSTOM2, CUSTOM1, CUSTOM2))
        self.assertEqual(found.duplicate_paths, (CUSTOM2,))

        _, _, writes = self.run_with(desktop=desktop)
        self.assertEqual(writes, [])
        self.assertNotIn(PARENT_TARGET, desktop.snapshot())

    def test_a_successful_run_appends_and_preserves_duplicates(self):
        """Kedra owns its own membership, not the list's tidiness."""
        desktop = fake(
            (CUSTOM2, CUSTOM0, CUSTOM2),
            {
                CUSTOM0: {"name": "notes", "binding": "<Super>n"},
                CUSTOM2: {"name": "dup", "binding": "<Super>d"},
            },
        )
        _, result, _ = self.run_with(desktop=desktop)
        self.assertTrue(result.ok)
        # The duplicate the user had is still there, in the same places.
        self.assertEqual(
            desktop.snapshot()[PARENT_TARGET].data, (CUSTOM2, CUSTOM0, CUSTOM2, KEDRA)
        )

    def test_a_stale_path_is_not_removed(self):
        desktop = fake((CUSTOM0, CUSTOM1), {CUSTOM0: {}, **BLACKBOX})
        _, _, writes = self.run_with(desktop=desktop)
        self.assertEqual(writes, [])
        self.assertIn(CUSTOM0, discover_custom_shortcuts(desktop).registered_paths)

    def test_a_stale_path_does_not_block_a_free_accelerator(self):
        desktop = fake((CUSTOM0,), {CUSTOM0: {}})
        plan, result, _ = self.run_with(desktop=desktop)
        self.assertEqual(plan.blocked, ())
        self.assertTrue(result.ok)
        self.assertEqual(desktop.snapshot()[PARENT_TARGET].data, (CUSTOM0, KEDRA))

    def test_a_malformed_shortcut_does_not_crash_the_planner(self):
        desktop = fake(
            (CUSTOM0, CUSTOM1),
            {CUSTOM0: {"name": "typo", "binding": "<Supr>Return"}, **BLACKBOX},
        )
        plan, result, _ = self.run_with(desktop=desktop)
        # It blocks -- because of Black Box, not because of the typo.
        report = plan.conflicts[SHORTCUT_ID]
        self.assertEqual([f.path for f in report.blocking], [CUSTOM1])
        self.assertEqual([f.path for f in report.advisories], [CUSTOM0])
        self.assertTrue(result.ok)


class UncheckedConflictsBlockTests(ConflictPlanningTestCase):
    """The planner default: nobody looked, so nothing is registered."""

    def test_a_plan_with_no_conflict_information_blocks_the_shortcut(self):
        user_state = self.state_for()
        resolution = resolve_state(self.tree, user_state)
        plan = build_plan(
            tree=self.tree,
            resolution=resolution,
            user_state=user_state,
            observed={},
        )
        self.assertEqual(len(plan.blocked), 4)
        self.assertEqual(plan.writes, ())
        self.assertIn("no conflict check", plan.conflicts[SHORTCUT_ID].block_reason)

    def test_ordinary_settings_are_unaffected_by_a_missing_report(self):
        """The gate touches shortcut resources only.

        Workflow's and Desktop's vendor defaults have no accelerator to reserve
        and must plan exactly as they did before conflicts existed.
        """
        user_state = self.state_for()
        resolution = resolve_state(self.tree, user_state)
        plan = build_plan(
            tree=self.tree, resolution=resolution, user_state=user_state, observed={}
        )
        others = [e for e in plan.entries if e.resource is None]
        self.assertTrue(others)
        for entry in others:
            with self.subTest(setting=entry.setting_id):
                self.assertFalse(entry.blocked_by_conflict)
                self.assertEqual(entry.decision, ownership.NOOP)


class DocumentationTests(unittest.TestCase):
    """The policy is only durable if it is written down."""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.terminal = (root / "docs" / "kedra-terminal.md").read_text(encoding="utf-8")
        self.experience = (root / "docs" / "kedra-experience.md").read_text(encoding="utf-8")
        self.readme = (root / "README.md").read_text(encoding="utf-8")

    def test_the_policy_is_stated(self):
        for expected in (
            "never takes an accelerator",
            "gtk_accelerator_parse",
            "no fallback parser",
            "blocked-by-conflict",
            "not deduplicated",
        ):
            with self.subTest(text=expected):
                self.assertIn(expected, self.terminal)

    def test_the_could_not_check_rule_is_stated(self):
        self.assertIn('"Could not check" is never "no conflict."', self.terminal)

    def test_restore_limitation_is_stated(self):
        self.assertIn("it may not delete or rebind\nsomebody else", self.terminal)
        self.assertIn("restore\nis blocked exactly as a normal apply is", self.terminal)

    def test_the_precedence_is_documented(self):
        self.assertIn("divergence first, conflicts second", self.terminal.lower())

    def test_the_later_conflict_behaviour_is_documented(self):
        self.assertIn("does **not** deregister itself", self.terminal)
        self.assertIn("grab order", self.terminal)

    def test_the_future_terminal_selector_requirement_is_documented(self):
        self.assertIn("launchability_problems", self.terminal)
        self.assertIn("DBusActivatable", self.terminal)

    def test_the_new_modules_are_listed(self):
        for module in ("accelerators", "conflicts"):
            with self.subTest(module=module):
                self.assertIn(f"| `{module}` |", self.experience)

    def test_the_readme_mentions_the_conflict_policy(self):
        self.assertIn("accelerator", self.readme.lower())


if __name__ == "__main__":
    unittest.main()
