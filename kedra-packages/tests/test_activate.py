"""First-login activation: the production apply path, end to end.

Every test drives the real `activate()` against a real GSettings adapter over
real GNOME schemas -- compiled into a temporary directory, backed by a keyfile
store in a temporary directory. The values, the types, the difference between a
stored value and an inherited one, the relocatable-schema handling: all genuine.
Only where the bytes land differs.

`LiveStateProof` fingerprints the developer's own dconf database, GSettings
keyfile store and XDG state directory before and after every single test, and
the check runs as a cleanup so it fires even when a test fails. Nothing here can
touch the machine it runs on, and that is asserted rather than promised.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from support import (
    DATA_DIR,
    IsolatedGSettings,
    LiveStateProof,
    TreeTestCase,
    gsettings_available,
)

from kedra_experience import activate as activate_mod
from kedra_experience.activate import (
    OUTCOME_APPLIED,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
    OUTCOME_NO_LAYER,
    OUTCOME_NO_STATE,
    OUTCOME_UNCHANGED,
    ActivationError,
    activate,
)
from kedra_experience.backends import BackendError
from kedra_experience.conflicts import PARENT_TARGET, child_target
from kedra_experience.ownership import OPERATION_APPLY, OPERATION_UNMANAGE
from kedra_experience.state import UserState
from kedra_experience.values import TypedValue

KEDRA_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/"
SUPER_RETURN = "<Super>Return"
LAUNCH = "gapplication launch org.gnome.Console"

SELECTION_KEDRA = 'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n'
SELECTION_VANILLA = 'state_schema_version = 3\n\n[selection]\nbase_preset = "vanilla"\n'


def selection_custom(module_id: str, enabled: bool = False) -> str:
    return (
        "state_schema_version = 3\n\n[selection]\nbase_preset = \"kedra\"\n\n"
        f"[[selection.module_overrides]]\nid = \"{module_id}\"\n"
        f"enabled = {'true' if enabled else 'false'}\n"
    )


@unittest.skipUnless(gsettings_available(), "needs PyGObject, glib2 and GNOME schemas")
class ActivationTestCase(unittest.TestCase):
    """A real adapter over isolated schemas, and a state file in a temp dir."""

    def setUp(self):
        import tempfile

        self.live = LiveStateProof()
        tmp = tempfile.TemporaryDirectory(prefix="kedra-activate-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.iso = IsolatedGSettings(self.root / "gsettings")
        self.addCleanup(self.iso.release)
        self.addCleanup(self._assert_live_state_untouched)

        self.state_path = self.root / "state" / "experience-state.toml"
        self.state_path.parent.mkdir(parents=True)

    def _assert_live_state_untouched(self):
        self.assertEqual(
            LiveStateProof.fingerprint(),
            self.live.before,
            "a test modified the developer's real GNOME configuration",
        )

    # -- helpers ---------------------------------------------------------

    def write_state(self, text: str) -> None:
        self.state_path.write_text(text, encoding="utf-8")

    def change_selection(self, module_id: str, enabled: bool) -> None:
        """Change the profile the way a profile change actually happens.

        Only the `[selection]` half moves; `[applied]` is what Kedra has done
        and is not the user's to rewrite. Overwriting the whole file instead
        would delete the ownership records, and the next run would then see a
        system it has never touched -- which is a different scenario, and not
        the one these tests are about.
        """
        from dataclasses import replace

        from kedra_experience.state import ModuleOverride

        current = self.state()
        overrides = tuple(
            o for o in current.module_overrides if o.id != module_id
        ) + (ModuleOverride(id=module_id, enabled=enabled),)
        replace(current, module_overrides=overrides).save(self.state_path)

    def run_activation(self, *, operation: str = OPERATION_APPLY, adapter=None):
        factory = (lambda: adapter) if adapter is not None else self.iso.adapter
        return activate(
            data_dir=DATA_DIR,
            state_path=self.state_path,
            adapter_factory=factory,
            operation=operation,
        )

    def state(self) -> UserState:
        return UserState.load(self.state_path)

    def shortcut(self) -> dict:
        return self.iso.shortcut_snapshot(KEDRA_PATH)

    def registered(self) -> tuple:
        return self.iso.registered_paths()

    def read(self, target: str):
        return self.iso.adapter().read(target)


# ---------------------------------------------------------------------------
# A. No state file -> no action
# ---------------------------------------------------------------------------


class NoStateTests(ActivationTestCase):
    """The Vanilla guarantee, keyed on the state file and nothing else."""

    def test_apply_is_a_successful_no_op(self):
        result = self.run_activation()
        self.assertEqual(result.outcome, OUTCOME_NO_STATE)
        self.assertTrue(result.ok)

    def test_nothing_is_written_to_the_desktop(self):
        self.run_activation()
        self.assertEqual(self.registered(), ())
        self.assertEqual(self.iso.store_contents(), "")

    def test_no_state_file_is_created(self):
        self.run_activation()
        self.assertFalse(self.state_path.exists())

    def test_no_lock_file_is_left_behind(self):
        self.run_activation()
        self.assertEqual(list(self.state_path.parent.iterdir()), [])

    def test_the_adapter_is_never_even_constructed(self):
        # The earliest possible exit: before the manifest tree is read, before
        # an adapter exists, before anything could reach a session.
        def explode():
            raise AssertionError("an adapter must not be created without state")

        result = activate(
            data_dir=DATA_DIR, state_path=self.state_path, adapter_factory=explode
        )
        self.assertEqual(result.outcome, OUTCOME_NO_STATE)

    def test_installed_packages_do_not_imply_a_profile(self):
        # Stated as its own test because it is the rule: presence of the runtime
        # is not consent to reconfigure somebody's desktop.
        self.assertFalse(self.state_path.exists())
        self.assertEqual(self.run_activation().outcome, OUTCOME_NO_STATE)


# ---------------------------------------------------------------------------
# B. Vanilla
# ---------------------------------------------------------------------------


class VanillaTests(ActivationTestCase):
    def test_a_vanilla_selection_applies_nothing(self):
        self.write_state(SELECTION_VANILLA)
        result = self.run_activation()
        self.assertEqual(result.outcome, OUTCOME_NO_LAYER)
        self.assertTrue(result.ok)

    def test_no_shortcut_is_created(self):
        self.write_state(SELECTION_VANILLA)
        self.run_activation()
        self.assertEqual(self.registered(), ())

    def test_no_applied_record_is_written(self):
        self.write_state(SELECTION_VANILLA)
        self.run_activation()
        self.assertIsNone(self.state().applied)

    def test_the_state_file_is_left_exactly_as_it_was(self):
        self.write_state(SELECTION_VANILLA)
        before = self.state_path.read_text(encoding="utf-8")
        self.run_activation()
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)


# ---------------------------------------------------------------------------
# C. First Kedra apply
# ---------------------------------------------------------------------------


class FirstApplyTests(ActivationTestCase):
    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.result = self.run_activation()

    def test_it_succeeds(self):
        self.assertEqual(self.result.outcome, OUTCOME_APPLIED, self.result.failed)
        self.assertTrue(self.result.ok)

    def test_the_shortcut_resource_is_created(self):
        snapshot = self.shortcut()
        self.assertEqual(snapshot["binding"], SUPER_RETURN)
        self.assertEqual(snapshot["name"], "Kedra Terminal")

    def test_the_launcher_comes_from_the_application_contract(self):
        # Never a binary path. The contract names a desktop ID and the command
        # is derived from it, so replacing the terminal replaces this answer.
        self.assertEqual(self.shortcut()["command"], LAUNCH)
        self.assertNotIn("/usr/bin", self.shortcut()["command"])

    def test_the_parent_list_gains_only_kedras_path(self):
        self.assertEqual(self.registered(), (KEDRA_PATH,))

    def test_the_resource_is_created_exactly_once(self):
        self.assertEqual(list(self.registered()).count(KEDRA_PATH), 1)

    def test_the_applied_record_becomes_truthful(self):
        applied = self.state().applied
        self.assertIsNotNone(applied)
        self.assertEqual(applied.preset_id, "kedra")
        self.assertEqual(applied.generation, 1)

    def test_ownership_records_carry_original_and_last_applied(self):
        records = {r.setting_id: r for r in self.state().applied.managed_values}
        self.assertTrue(records, "apply recorded no ownership at all")
        for setting_id, record in records.items():
            with self.subTest(setting=setting_id):
                self.assertIsNotNone(record.last_applied)
                # On a fresh system there was nothing before Kedra, and the
                # model says so with original_present=False rather than with a
                # value. That absence is a real state: it reverts by unsetting,
                # not by writing something back.
                self.assertFalse(record.original_present)
                self.assertIsNone(record.original)

    def test_the_registration_record_names_the_one_entry_kedra_owns(self):
        # Load-bearing on revert: without `member`, giving back "what Kedra
        # found" would restore a whole list snapshot and delete every shortcut
        # the user created after Kedra's.
        record = next(
            r
            for r in self.state().applied.managed_values
            if r.setting_id == "terminal-launch-shortcut-registration"
        )
        self.assertEqual(record.member, KEDRA_PATH)
        self.assertTrue(record.is_membership)

    def test_every_record_is_for_a_managed_setting(self):
        for record in self.state().applied.managed_values:
            with self.subTest(setting=record.setting_id):
                self.assertEqual(record.policy, "managed")


# ---------------------------------------------------------------------------
# D. Idempotence
# ---------------------------------------------------------------------------


class SecondApplyTests(ActivationTestCase):
    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.first = self.run_activation()
        self.after_first = self.iso.store_contents()
        self.second = self.run_activation()

    def test_the_second_run_writes_nothing(self):
        self.assertEqual(self.second.outcome, OUTCOME_UNCHANGED)
        self.assertEqual(self.second.written, ())

    def test_the_desktop_is_byte_identical(self):
        self.assertEqual(self.iso.store_contents(), self.after_first)

    def test_no_duplicate_resource_appears(self):
        self.assertEqual(self.registered(), (KEDRA_PATH,))

    def test_the_generation_does_not_increment(self):
        self.assertEqual(self.state().applied.generation, 1)
        self.assertIsNone(self.second.generation)

    def test_a_third_run_is_also_inert(self):
        third = self.run_activation()
        self.assertEqual(third.outcome, OUTCOME_UNCHANGED)
        self.assertEqual(self.state().applied.generation, 1)


# ---------------------------------------------------------------------------
# E, F. User divergence
# ---------------------------------------------------------------------------


class DivergenceTests(ActivationTestCase):
    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.run_activation()

    def test_a_rebound_shortcut_is_preserved(self):
        # The scenario from the brief: Kedra owns Super+Enter, the user moves it
        # to Super+T, and the next login must leave it alone.
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))

        result = self.run_activation()
        self.assertEqual(self.shortcut()["binding"], "<Super>t")
        self.assertIn(
            "terminal-launch-shortcut-binding", result.preserved
        )

    def test_a_rebound_shortcut_survives_repeated_logins(self):
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        for _ in range(3):
            self.run_activation()
        self.assertEqual(self.shortcut()["binding"], "<Super>t")

    def test_a_changed_command_is_preserved(self):
        adapter = self.iso.adapter()
        adapter.apply(
            child_target(KEDRA_PATH, "command"), TypedValue("s", "alacritty")
        )
        result = self.run_activation()
        self.assertEqual(self.shortcut()["command"], "alacritty")
        self.assertIn("terminal-launch-shortcut-command", result.preserved)

    def test_a_changed_name_is_preserved(self):
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "name"), TypedValue("s", "My Terminal"))
        self.run_activation()
        self.assertEqual(self.shortcut()["name"], "My Terminal")

    def test_divergence_does_not_increment_the_generation(self):
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        before = self.state().applied.generation
        self.run_activation()
        self.assertEqual(self.state().applied.generation, before)

    def test_the_record_still_names_what_kedra_last_applied(self):
        # Preserving a user's value must not rewrite history: `last_applied`
        # stays what Kedra wrote, which is how the next run knows they diverged.
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        self.run_activation()
        record = next(
            r
            for r in self.state().applied.managed_values
            if r.setting_id == "terminal-launch-shortcut-binding"
        )
        self.assertEqual(record.last_applied.data, SUPER_RETURN)


# ---------------------------------------------------------------------------
# G. Conflict with somebody else's shortcut
# ---------------------------------------------------------------------------


class ConflictTests(ActivationTestCase):
    OTHER = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"

    def setUp(self):
        super().setUp()
        self.iso.seed_custom_shortcut(
            self.OTHER, name="Black Box", binding=SUPER_RETURN, command="blackbox"
        )
        self.before = self.iso.shortcut_snapshot(self.OTHER)
        self.write_state(SELECTION_KEDRA)
        self.result = self.run_activation()

    def test_activation_reports_blocked_rather_than_failing(self):
        self.assertEqual(self.result.outcome, OUTCOME_BLOCKED)
        # Not a failure: Kedra asked, was told no, and behaved correctly.
        self.assertTrue(self.result.ok)

    def test_the_reason_is_reported_structurally(self):
        self.assertTrue(self.result.blocked)
        self.assertIn("terminal-launch-shortcut", " ".join(self.result.blocked))

    def test_kedra_does_not_steal_the_accelerator(self):
        self.assertNotIn(KEDRA_PATH, self.registered())

    def test_the_other_shortcut_is_untouched(self):
        self.assertEqual(self.iso.shortcut_snapshot(self.OTHER), self.before)
        self.assertEqual(self.before["binding"], SUPER_RETURN)

    def test_no_orphan_kedra_resource_is_left_exposed(self):
        # Neither a registered path nor half-written child values.
        self.assertEqual(self.registered(), (self.OTHER,))
        for key in ("name", "binding", "command"):
            self.assertIsNone(self.read(child_target(KEDRA_PATH, key)))

    def test_state_does_not_claim_ownership(self):
        applied = self.state().applied
        owned = [r.setting_id for r in applied.managed_values] if applied else []
        self.assertEqual(owned, [])

    def test_a_later_login_after_the_conflict_clears_succeeds(self):
        # The user frees Super+Enter; the next login may then take it.
        adapter = self.iso.adapter()
        adapter.apply(child_target(self.OTHER, "binding"), TypedValue("s", "<Super>b"))
        result = self.run_activation()
        self.assertEqual(result.outcome, OUTCOME_APPLIED)
        self.assertIn(KEDRA_PATH, self.registered())

    def test_repeated_blocked_logins_change_nothing(self):
        for _ in range(3):
            self.run_activation()
        self.assertEqual(self.registered(), (self.OTHER,))
        self.assertEqual(self.iso.shortcut_snapshot(self.OTHER), self.before)


# ---------------------------------------------------------------------------
# H. Custom: terminal off
# ---------------------------------------------------------------------------


class TerminalDisabledTests(ActivationTestCase):
    def test_no_shortcut_is_created(self):
        self.write_state(selection_custom("terminal"))
        result = self.run_activation()
        self.assertEqual(self.registered(), ())
        self.assertIn(result.outcome, (OUTCOME_UNCHANGED, OUTCOME_NO_LAYER))

    def test_no_ownership_is_claimed(self):
        self.write_state(selection_custom("terminal"))
        self.run_activation()
        applied = self.state().applied
        self.assertEqual([] if applied is None else list(applied.managed_values), [])


# ---------------------------------------------------------------------------
# I, J. Disabling a module Kedra already manages
# ---------------------------------------------------------------------------


class DisableAfterManagingTests(ActivationTestCase):
    """The supported state transition: Terminal was on, now it is off."""

    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.run_activation()
        self.assertEqual(self.registered(), (KEDRA_PATH,))

    def disable_terminal(self):
        self.change_selection("terminal", False)

    def test_the_resource_is_given_back(self):
        self.disable_terminal()
        result = self.run_activation()
        self.assertEqual(result.outcome, OUTCOME_APPLIED)
        self.assertEqual(self.registered(), ())

    def test_the_child_values_are_returned_to_unset(self):
        self.disable_terminal()
        self.run_activation()
        for key in ("name", "binding", "command"):
            with self.subTest(key=key):
                self.assertIsNone(self.read(child_target(KEDRA_PATH, key)))

    def test_ownership_records_are_dropped(self):
        self.disable_terminal()
        self.run_activation()
        applied = self.state().applied
        self.assertEqual([] if applied is None else list(applied.managed_values), [])

    def test_a_diverged_resource_is_NOT_destroyed(self):
        # The rule that matters most here: once a person has made the shortcut
        # theirs, disabling the module stops Kedra claiming it -- it does not
        # entitle Kedra to delete it.
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        self.disable_terminal()
        self.run_activation()
        self.assertEqual(
            self.iso.adapter().read(child_target(KEDRA_PATH, "binding")).data,
            "<Super>t",
        )

    def test_a_diverged_resource_stops_being_claimed(self):
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        self.disable_terminal()
        self.run_activation()
        applied = self.state().applied
        owned = [r.setting_id for r in applied.managed_values] if applied else []
        self.assertNotIn("terminal-launch-shortcut-binding", owned)

    def test_disabling_twice_is_idempotent(self):
        self.disable_terminal()
        self.run_activation()
        second = self.run_activation()
        self.assertEqual(second.outcome, OUTCOME_UNCHANGED)


class UnmanageTests(ActivationTestCase):
    """The explicit way out, for a person who wants Kedra to let go."""

    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.run_activation()

    def test_it_gives_the_resource_back(self):
        result = self.run_activation(operation=OPERATION_UNMANAGE)
        self.assertTrue(result.ok)
        self.assertEqual(self.registered(), ())

    def test_it_drops_every_record(self):
        self.run_activation(operation=OPERATION_UNMANAGE)
        applied = self.state().applied
        self.assertEqual([] if applied is None else list(applied.managed_values), [])

    def test_a_diverged_value_is_left_with_the_user(self):
        adapter = self.iso.adapter()
        adapter.apply(child_target(KEDRA_PATH, "binding"), TypedValue("s", "<Super>t"))
        self.run_activation(operation=OPERATION_UNMANAGE)
        self.assertEqual(
            self.iso.adapter().read(child_target(KEDRA_PATH, "binding")).data,
            "<Super>t",
        )

    def test_apply_after_unmanage_takes_it_again(self):
        self.run_activation(operation=OPERATION_UNMANAGE)
        result = self.run_activation()
        self.assertEqual(result.outcome, OUTCOME_APPLIED)
        self.assertEqual(self.registered(), (KEDRA_PATH,))


# ---------------------------------------------------------------------------
# K. policy = "default" is never written or owned
# ---------------------------------------------------------------------------


class DefaultPolicyIsolationTests(ActivationTestCase):
    """The vendor-default boundary, checked from the production path.

    The five-workspace model and the Super+1..5 keymap are delivered as
    package-owned GSettings overrides. `apply` writing them would create an
    ownership claim over values Kedra never wrote -- and a later revert would
    then "restore" something it never changed.
    """

    DEFAULT_TARGETS = (
        "gsettings:org.gnome.mutter:dynamic-workspaces",
        "gsettings:org.gnome.desktop.wm.preferences:num-workspaces",
        "gsettings:org.gnome.desktop.wm.keybindings:close",
        "gsettings:org.gnome.desktop.wm.keybindings:switch-to-workspace-1",
        "gsettings:org.gnome.shell.keybindings:switch-to-application-1",
    )

    def setUp(self):
        super().setUp()
        self.write_state(SELECTION_KEDRA)
        self.run_activation()

    def test_no_default_policy_target_is_written(self):
        for target in self.DEFAULT_TARGETS:
            with self.subTest(target=target):
                try:
                    stored = self.read(target)
                except BackendError:
                    continue  # schema absent from the fixture; nothing written
                self.assertIsNone(
                    stored, f"apply stored an explicit value at {target}"
                )

    def test_no_default_policy_setting_is_owned(self):
        owned = {r.setting_id for r in self.state().applied.managed_values}
        for setting_id in (
            "desktop-dynamic-workspaces",
            "desktop-workspace-count",
            "workflow-close-window",
            "workflow-switch-workspace-1",
            "workflow-unbind-app-switch-1",
        ):
            with self.subTest(setting=setting_id):
                self.assertNotIn(setting_id, owned)

    def test_every_owned_setting_is_managed_policy(self):
        # The general form of the two above, so a future managed setting is
        # covered without editing a list.
        for record in self.state().applied.managed_values:
            with self.subTest(setting=record.setting_id):
                self.assertEqual(record.policy, "managed")

    def test_the_store_mentions_no_workspace_or_keymap_key(self):
        contents = self.iso.store_contents()
        for key in (
            "dynamic-workspaces",
            "num-workspaces",
            "switch-to-workspace",
            "switch-to-application",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, contents)


# ---------------------------------------------------------------------------
# L, M. Generation and partial failure
# ---------------------------------------------------------------------------


class RefusingAdapter:
    """A real adapter that refuses writes to exactly one target.

    Exactly one, matched on equality rather than on a substring: the child
    targets all contain the parent key's name, so a substring match refuses
    everything and produces total failure, which is a different scenario.
    """

    def __init__(self, inner, refuse_target: str):
        self.inner = inner
        self.refuse = refuse_target
        self.written: list[str] = []

    def read(self, target):
        return self.inner.read(target)

    def read_effective(self, target):
        return self.inner.read_effective(target)

    def apply(self, target, value):
        if target == self.refuse:
            raise BackendError(f"refused {target}")
        self.written.append(target)
        return self.inner.apply(target, value)

    def reset(self, target):
        return self.inner.reset(target)


class GenerationAndFailureTests(ActivationTestCase):
    def test_the_first_apply_takes_generation_one(self):
        self.write_state(SELECTION_KEDRA)
        result = self.run_activation()
        self.assertEqual(result.generation, 1)
        self.assertEqual(self.state().applied.generation, 1)

    def test_one_generation_per_logical_transaction(self):
        # Several settings change together; they share one generation.
        self.write_state(SELECTION_KEDRA)
        self.run_activation()
        generations = {r.generation for r in self.state().applied.managed_values}
        self.assertEqual(generations, {1})

    def test_an_inert_run_consumes_no_generation(self):
        self.write_state(SELECTION_KEDRA)
        self.run_activation()
        self.run_activation()
        self.run_activation()
        self.assertEqual(self.state().applied.generation, 1)

    def test_a_real_change_increments_once(self):
        self.write_state(SELECTION_KEDRA)
        self.run_activation()
        self.change_selection("terminal", False)
        self.run_activation()
        self.assertEqual(self.state().applied.generation, 2)

    def test_partial_failure_records_only_what_succeeded(self):
        # The three child values are written; registering the path is refused.
        self.write_state(SELECTION_KEDRA)
        adapter = RefusingAdapter(self.iso.adapter(), PARENT_TARGET)
        result = self.run_activation(adapter=adapter)

        self.assertEqual(result.outcome, OUTCOME_FAILED)
        self.assertFalse(result.ok)
        self.assertTrue(result.failed)

        applied = self.state().applied
        owned = {r.setting_id for r in applied.managed_values} if applied else set()
        # Nothing that failed may be claimed.
        for setting_id in result.failed:
            with self.subTest(setting=setting_id):
                self.assertNotIn(setting_id, owned)

    def test_partial_failure_claims_the_writes_that_did_happen(self):
        self.write_state(SELECTION_KEDRA)
        adapter = RefusingAdapter(self.iso.adapter(), PARENT_TARGET)
        self.run_activation(adapter=adapter)
        applied = self.state().applied
        owned = {r.setting_id for r in applied.managed_values} if applied else set()
        self.assertTrue(owned, "successful writes must still be recorded")
        self.assertNotIn("terminal-launch-shortcut-registration", owned)

    def test_total_failure_claims_nothing_at_all(self):
        # Every write refused. `[applied]` must not appear at all -- a record
        # claiming a value Kedra never wrote is the one thing the model must
        # never contain.
        class RefuseEverything(RefusingAdapter):
            def apply(self, target, value):
                raise BackendError(f"refused {target}")

        self.write_state(SELECTION_KEDRA)
        result = self.run_activation(
            adapter=RefuseEverything(self.iso.adapter(), PARENT_TARGET)
        )
        self.assertEqual(result.outcome, OUTCOME_FAILED)
        self.assertIsNone(self.state().applied)
        self.assertEqual(self.registered(), ())

    def test_retrying_after_a_partial_failure_completes_it(self):
        self.write_state(SELECTION_KEDRA)
        self.run_activation(adapter=RefusingAdapter(self.iso.adapter(), PARENT_TARGET))
        result = self.run_activation()
        self.assertTrue(result.ok)
        self.assertEqual(self.registered(), (KEDRA_PATH,))


# ---------------------------------------------------------------------------
# Refusing to act on a state file that is not usable
# ---------------------------------------------------------------------------


class UnusableStateTests(ActivationTestCase):
    def test_an_invalid_profile_is_refused(self):
        self.write_state(
            'state_schema_version = 3\n\n[selection]\nbase_preset = "kedra"\n\n'
            '[[selection.module_overrides]]\nid = "integration"\nenabled = false\n'
        )
        with self.assertRaises(ActivationError) as caught:
            self.run_activation()
        self.assertIn("not a valid profile", str(caught.exception))
        self.assertEqual(self.registered(), ())

    def test_an_unknown_preset_is_refused(self):
        self.write_state('state_schema_version = 3\n\n[selection]\nbase_preset = "ghost"\n')
        with self.assertRaises(ActivationError):
            self.run_activation()
        self.assertEqual(self.registered(), ())

    def test_a_malformed_state_file_is_refused(self):
        self.write_state("this is not toml {")
        with self.assertRaises(ActivationError):
            self.run_activation()
        self.assertEqual(self.registered(), ())

    def test_a_refusal_writes_nothing(self):
        self.write_state("state_schema_version = 3\n")
        with self.assertRaises(ActivationError):
            self.run_activation()
        self.assertEqual(self.iso.store_contents(), "")


# ---------------------------------------------------------------------------
# What the module reads, and what it does not
# ---------------------------------------------------------------------------


class ObservationTests(ActivationTestCase):
    def test_only_relevant_targets_are_observed(self):
        # No dconf dump: Kedra reads the values it is about to reason about,
        # plus the custom-shortcut list, and nothing else.
        from kedra_experience.manifests import Report, load_tree
        from kedra_experience.resolve import resolve_state

        self.write_state(SELECTION_KEDRA)
        report = Report()
        tree = load_tree(DATA_DIR, report)
        state = UserState.load(self.state_path)
        resolution = resolve_state(tree, state)

        targets = activate_mod.observed_targets(tree, resolution, state)
        self.assertIn(PARENT_TARGET, targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue(
                    "custom-keybinding" in target or "media-keys" in target,
                    f"{target} is not a managed-resource target",
                )

    def test_default_policy_targets_are_not_observed(self):
        # They are not read either -- there is nothing to decide about a value
        # Kedra will never write.
        from kedra_experience.manifests import Report, load_tree
        from kedra_experience.resolve import resolve_state

        self.write_state(SELECTION_KEDRA)
        report = Report()
        tree = load_tree(DATA_DIR, report)
        state = UserState.load(self.state_path)
        targets = activate_mod.observed_targets(tree, resolve_state(tree, state), state)
        for forbidden in DefaultPolicyIsolationTests.DEFAULT_TARGETS:
            self.assertNotIn(forbidden, targets)


if __name__ == "__main__":
    unittest.main()
