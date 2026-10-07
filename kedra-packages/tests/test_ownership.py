#!/usr/bin/env python3
"""Tests for the ownership decision layer.

Pure function calls: no tree, no filesystem, no session. Every case from the
architecture is pinned here, because these decisions are the difference between
a distro that respects a user's edits and one that stamps over them.

The rule under test throughout:

    A normal Kedra update must never overwrite a value merely because it
    differs from the current Kedra recommendation.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import ownership, settings, state, typed  # noqa: E402

X = typed("as", ["<Super>x"])   # what Kedra applied first
Y = typed("as", ["<Super>y"])   # what a newer Kedra wants
Z = typed("as", ["<Super>z"])   # what the user chose
O = typed("as", ["<Super>o"])   # the pre-Kedra original


def declaration(value=X, policy="managed", setting_id="example-setting"):
    return settings.SettingDeclaration(
        id=setting_id,
        backend="gsettings",
        policy=policy,
        target={"schema": "org.example.test", "key": "example-key"},
        value=value,
        module_id="workflow",
        module_version="0.2.0",
    )


def record(last_applied=X, original=O, original_present=True, generation=1):
    return state.ManagedValue(
        setting_id="example-setting",
        backend="gsettings",
        target="gsettings:org.example.test:example-key",
        module_id="workflow",
        module_version="0.2.0",
        policy="managed",
        generation=generation,
        last_applied=last_applied,
        original=original,
        original_present=original_present,
    )


def decide(operation, desired, rec, observed):
    return ownership.decide(
        operation=operation, desired=desired, record=rec, observed=observed
    )


class TestCase1FirstApplication(unittest.TestCase):
    """No managed record; the current value is whatever the vendor shipped."""

    def test_first_apply_writes_and_takes_ownership(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(X), None, None)

        self.assertEqual(outcome.decision, ownership.APPLY)
        self.assertTrue(outcome.writes)
        self.assertTrue(outcome.records_ownership)
        self.assertEqual(outcome.value_to_write, X)
        self.assertFalse(outcome.requires_confirmation)

    def test_first_apply_over_an_existing_vendor_value(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(X), None, O)

        self.assertEqual(outcome.decision, ownership.APPLY)
        self.assertTrue(outcome.writes)

    def test_first_apply_when_value_already_matches_records_without_writing(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(X), None, X)

        self.assertEqual(outcome.decision, ownership.APPLY)
        self.assertFalse(outcome.writes)
        self.assertTrue(outcome.records_ownership)


class TestCase2SafeUpdate(unittest.TestCase):
    """Kedra wrote X, the value is still X, Kedra now wants Y."""

    def test_safe_update_when_user_has_not_touched_it(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), X)

        self.assertEqual(outcome.decision, ownership.UPDATE)
        self.assertTrue(outcome.writes)
        self.assertEqual(outcome.value_to_write, Y)
        self.assertFalse(outcome.requires_confirmation)

    def test_noop_when_already_at_the_desired_value(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(X), record(X), X)

        self.assertEqual(outcome.decision, ownership.NOOP)
        self.assertFalse(outcome.writes)


class TestCase3PreserveUserChange(unittest.TestCase):
    """Kedra wrote X, the user changed it to Z, Kedra now wants Y."""

    def test_user_change_is_never_overwritten_by_an_update(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), Z)

        self.assertEqual(outcome.decision, ownership.PRESERVE_USER_CHANGE)
        self.assertFalse(outcome.writes)
        self.assertIsNone(outcome.value_to_write)
        self.assertTrue(outcome.requires_confirmation)

    def test_reason_names_it_as_a_user_modification(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), Z)
        self.assertIn("user modification", outcome.reason)

    def test_divergence_is_measured_against_last_applied_not_desired(self):
        """A value differing from the new recommendation proves nothing."""
        # Observed equals last_applied, differs from desired -> safe update.
        self.assertEqual(
            decide(ownership.OPERATION_APPLY, declaration(Y), record(X), X).decision,
            ownership.UPDATE,
        )
        # Observed differs from last_applied -> user's, even though it also
        # differs from desired.
        self.assertEqual(
            decide(ownership.OPERATION_APPLY, declaration(Y), record(X), Z).decision,
            ownership.PRESERVE_USER_CHANGE,
        )

    def test_user_arriving_at_the_desired_value_independently_is_a_noop(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), Y)

        self.assertEqual(outcome.decision, ownership.NOOP)
        self.assertFalse(outcome.writes)
        self.assertTrue(outcome.records_ownership)

    def test_user_unsetting_the_value_counts_as_divergence(self):
        outcome = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), None)
        self.assertEqual(outcome.decision, ownership.PRESERVE_USER_CHANGE)


class TestCase4SafeRevert(unittest.TestCase):
    """Module switched off while the value is still exactly what Kedra wrote."""

    def test_revert_to_the_original_value(self):
        outcome = decide(ownership.OPERATION_UNMANAGE, None, record(X, original=O), X)

        self.assertEqual(outcome.decision, ownership.REVERT_TO_ORIGINAL)
        self.assertTrue(outcome.writes)
        self.assertEqual(outcome.value_to_write, O)
        self.assertTrue(outcome.drops_record)
        self.assertFalse(outcome.reset_to_default)

    def test_revert_unsets_when_there_was_no_original(self):
        outcome = decide(
            ownership.OPERATION_UNMANAGE,
            None,
            record(X, original=None, original_present=False),
            X,
        )

        self.assertEqual(outcome.decision, ownership.REVERT_TO_ORIGINAL)
        self.assertTrue(outcome.writes)
        self.assertTrue(outcome.reset_to_default)
        self.assertIsNone(outcome.value_to_write)
        self.assertTrue(outcome.drops_record)

    def test_unmanage_without_a_record_is_a_noop(self):
        outcome = decide(ownership.OPERATION_UNMANAGE, None, None, X)
        self.assertEqual(outcome.decision, ownership.NOOP)
        self.assertFalse(outcome.writes)


class TestCase5PreserveOnDisable(unittest.TestCase):
    """User changed the value, then disabled the module."""

    def test_user_value_is_not_replaced_by_the_original(self):
        outcome = decide(ownership.OPERATION_UNMANAGE, None, record(X, original=O), Z)

        self.assertEqual(outcome.decision, ownership.DROP_RECORD)
        self.assertFalse(outcome.writes)
        self.assertIsNone(outcome.value_to_write)
        self.assertTrue(outcome.drops_record)

    def test_kedra_stops_claiming_the_value(self):
        outcome = decide(ownership.OPERATION_UNMANAGE, None, record(X), Z)
        self.assertTrue(outcome.drops_record)
        self.assertIn("without touching the user's value", outcome.reason)


class TestCase6ExplicitReset(unittest.TestCase):
    """The one operation allowed to overwrite a user's change."""

    def test_explicit_reset_overwrites_user_divergence(self):
        outcome = decide(ownership.OPERATION_RESTORE, declaration(Y), record(X), Z)

        self.assertEqual(outcome.decision, ownership.FORCE_APPLY)
        self.assertTrue(outcome.writes)
        self.assertEqual(outcome.value_to_write, Y)
        self.assertTrue(outcome.requires_confirmation)

    def test_force_apply_is_distinguishable_from_a_normal_update(self):
        forced = decide(ownership.OPERATION_RESTORE, declaration(Y), record(X), Z)
        normal = decide(ownership.OPERATION_APPLY, declaration(Y), record(X), X)

        self.assertNotEqual(forced.decision, normal.decision)
        self.assertEqual(normal.decision, ownership.UPDATE)

    def test_force_apply_is_unreachable_from_a_normal_apply(self):
        """No combination of inputs may produce FORCE_APPLY under 'apply'."""
        values = [None, X, Y, Z, O]
        records = [None] + [record(v) for v in (X, Y, Z, O)]
        for observed in values:
            for rec in records:
                outcome = decide(
                    ownership.OPERATION_APPLY, declaration(Y), rec, observed
                )
                self.assertNotEqual(outcome.decision, ownership.FORCE_APPLY)

    def test_reset_without_divergence_is_a_normal_update(self):
        outcome = decide(ownership.OPERATION_RESTORE, declaration(Y), record(X), X)
        self.assertEqual(outcome.decision, ownership.UPDATE)

    def test_reset_when_already_correct_is_a_noop(self):
        outcome = decide(ownership.OPERATION_RESTORE, declaration(Y), record(X), Y)
        self.assertEqual(outcome.decision, ownership.NOOP)

    def test_reset_of_a_withdrawn_setting_reverts_even_when_diverged(self):
        outcome = decide(ownership.OPERATION_RESTORE, None, record(X, original=O), Z)

        self.assertEqual(outcome.decision, ownership.REVERT_TO_ORIGINAL)
        self.assertTrue(outcome.writes)
        self.assertEqual(outcome.value_to_write, O)
        self.assertTrue(outcome.requires_confirmation)


class TestDefaultPolicy(unittest.TestCase):
    """'default' settings are vendor defaults; Kedra neither writes nor owns them."""

    def test_default_policy_applies_nothing(self):
        outcome = decide(
            ownership.OPERATION_APPLY, declaration(X, policy="default"), None, None
        )

        self.assertEqual(outcome.decision, ownership.NOOP)
        self.assertFalse(outcome.writes)
        self.assertFalse(outcome.records_ownership)

    def test_default_policy_reason_explains_the_delivery_route(self):
        outcome = decide(
            ownership.OPERATION_APPLY, declaration(X, policy="default"), None, None
        )
        self.assertIn("vendor default", outcome.reason)

    def test_default_policy_never_diverges_because_it_is_never_owned(self):
        outcome = decide(
            ownership.OPERATION_APPLY, declaration(X, policy="default"), None, Z
        )
        self.assertEqual(outcome.decision, ownership.NOOP)


class TestWithdrawnSetting(unittest.TestCase):
    def test_apply_with_no_declaration_behaves_as_unmanage(self):
        outcome = decide(ownership.OPERATION_APPLY, None, record(X), X)
        self.assertEqual(outcome.decision, ownership.REVERT_TO_ORIGINAL)

    def test_apply_with_no_declaration_and_divergence_drops_the_record(self):
        outcome = decide(ownership.OPERATION_APPLY, None, record(X), Z)
        self.assertEqual(outcome.decision, ownership.DROP_RECORD)


class TestSurface(unittest.TestCase):
    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            decide("frobnicate", declaration(X), None, None)

    def test_decision_set_is_closed(self):
        self.assertEqual(
            ownership.DECISIONS,
            {
                "apply",
                "update",
                "noop",
                "preserve-user-change",
                "revert-to-original",
                "drop-record",
                "force-apply",
                # Not produced by decide(): the conflict gate in `plan` applies
                # it on top of an ownership decision. Listed here because the
                # decision vocabulary is closed and shared.
                "blocked-by-conflict",
            },
        )

    def test_decide_never_produces_a_conflict_decision(self):
        """Ownership answers a question about Kedra and the user, not the system.

        Whether a write would collide with an unrelated shortcut is decided
        after `decide()` has run and can only refuse a write -- never create
        one, and never re-open a value the user diverged.
        """
        import itertools

        from kedra_experience.settings import SettingDeclaration
        from kedra_experience.state import ManagedValue
        from kedra_experience.values import TypedValue

        declaration = SettingDeclaration(
            id="example-one",
            backend="gsettings",
            policy="managed",
            target={"schema": "org.example.test", "key": "example-key"},
            value=TypedValue("s", "wanted"),
            module_id="terminal",
            module_version="1.0.0",
        )
        record = ManagedValue(
            setting_id="example-one",
            backend="gsettings",
            target="gsettings:org.example.test:example-key",
            module_id="terminal",
            module_version="1.0.0",
            policy="managed",
            generation=1,
            last_applied=TypedValue("s", "written"),
        )
        observations = (None, TypedValue("s", "wanted"), TypedValue("s", "written"),
                        TypedValue("s", "theirs"))
        for operation, desired, held, observed in itertools.product(
            sorted(ownership.OPERATIONS), (declaration, None), (record, None), observations
        ):
            with self.subTest(operation=operation, desired=bool(desired), record=bool(held)):
                outcome = ownership.decide(
                    operation=operation, desired=desired, record=held, observed=observed
                )
                self.assertNotEqual(outcome.decision, ownership.BLOCKED_BY_CONFLICT)

    def test_operation_set_is_closed(self):
        self.assertEqual(
            ownership.OPERATIONS, {"apply", "unmanage", "restore-defaults"}
        )

    def test_every_outcome_carries_a_reason(self):
        cases = [
            (ownership.OPERATION_APPLY, declaration(X), None, None),
            (ownership.OPERATION_APPLY, declaration(Y), record(X), X),
            (ownership.OPERATION_APPLY, declaration(Y), record(X), Z),
            (ownership.OPERATION_UNMANAGE, None, record(X), X),
            (ownership.OPERATION_UNMANAGE, None, record(X), Z),
            (ownership.OPERATION_RESTORE, declaration(Y), record(X), Z),
        ]
        for operation, desired, rec, observed in cases:
            outcome = decide(operation, desired, rec, observed)
            self.assertTrue(outcome.reason.strip(), outcome.decision)

    def test_only_writing_decisions_carry_a_value_or_reset(self):
        cases = [
            (ownership.OPERATION_APPLY, declaration(Y), record(X), Z),
            (ownership.OPERATION_UNMANAGE, None, record(X), Z),
            (ownership.OPERATION_APPLY, declaration(X), record(X), X),
        ]
        for operation, desired, rec, observed in cases:
            outcome = decide(operation, desired, rec, observed)
            self.assertFalse(outcome.writes)
            self.assertIsNone(outcome.value_to_write)
            self.assertFalse(outcome.reset_to_default)

    def test_decide_is_pure(self):
        """Calling twice gives the same answer and mutates no input."""
        desired, rec = declaration(Y), record(X)
        first = decide(ownership.OPERATION_APPLY, desired, rec, Z)
        second = decide(ownership.OPERATION_APPLY, desired, rec, Z)

        self.assertEqual(first, second)
        self.assertEqual(rec.last_applied, X)
        self.assertEqual(desired.value, Y)


class TestDivergenceHelper(unittest.TestCase):
    def test_record_reports_divergence(self):
        self.assertFalse(record(X).diverged_from(X))
        self.assertTrue(record(X).diverged_from(Z))
        self.assertTrue(record(X).diverged_from(None))

    def test_unset_is_not_equal_to_any_value(self):
        from support import values

        self.assertFalse(values.values_equal(None, X))
        self.assertTrue(values.values_equal(None, None))

    def test_type_is_part_of_equality(self):
        from support import values

        self.assertFalse(
            values.values_equal(typed("s", "a"), typed("as", ["a"]))
        )


if __name__ == "__main__":
    unittest.main()
