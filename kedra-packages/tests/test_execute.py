#!/usr/bin/env python3
"""Tests for the execution layer.

Every test runs against InMemoryAdapter in a temporary directory. Nothing here
reaches GSettings, dconf, ~/.config or the invoking user's real state.

The invariants under test, restated because they are what this layer is for:

- state must never claim a write succeeded when it did not;
- `original` is captured once and never silently rewritten;
- a normal update never overwrites a detected user modification;
- removing a module never overwrites a detected user modification;
- explicit restore is the only normal operation allowed to overwrite one.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    KEDRA_MODULES,
    TreeTestCase,
    backends,
    base_preset_toml,
    gsettings_toml,
    module_toml,
    ownership,
    plan as plan_mod,
    resolve,
    state,
    typed,
)
from kedra_experience import execute as execute_mod  # noqa: E402

X = typed("as", ["<Super>x"])   # what Kedra v1 wrote
Y = typed("as", ["<Super>y"])   # what Kedra v2 wants
Z = typed("as", ["<Super>z"])   # what the user chose
O = typed("as", ["<Super>o"])   # the pre-Kedra original

TARGET_A = "gsettings:org.example.test:key-a"
TARGET_B = "gsettings:org.example.test:key-b"
SCHEMA = 3


class ExecutorTestCase(TreeTestCase):
    def tree_with(self, settings: str):
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml("workflow", version="0.2.0", settings=settings)
        return self.load(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )

    def record(self, setting_id="example-a", target=TARGET_A, last_applied=X,
               original=O, present=True, generation=1):
        return state.ManagedValue(
            setting_id=setting_id,
            backend="gsettings",
            target=target,
            module_id="workflow",
            module_version="0.1.0",
            policy="managed",
            generation=generation,
            last_applied=last_applied,
            original=original,
            original_present=present,
        )

    def make_state(self, *, overrides=(), managed=(), generation=1):
        applied = None
        if managed:
            applied = state.AppliedRecord(
                generation=generation,
                preset_id="kedra",
                preset_version="0.1.0",
                experience_schema_version=SCHEMA,
                applied_at="2026-08-14T12:00:00Z",
                managed_values=tuple(managed),
            )
        return state.UserState(
            base_preset="kedra", module_overrides=tuple(overrides), applied=applied
        )

    def run_execution(self, tree, user_state, observed, *, operation="apply", adapter=None):
        adapter = adapter or backends.InMemoryAdapter(
            values={t: v for t, v in observed.items() if v}
        )
        resolution = resolve.resolve_state(tree, user_state)
        plan = plan_mod.build_plan(
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=operation,
        )
        result = execute_mod.execute_plan(
            plan=plan,
            adapters={"gsettings": adapter},
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            experience_schema_version=SCHEMA,
            applied_at="2026-08-14T13:00:00Z",
        )
        return result, adapter

    def managed(self, result, setting_id="example-a"):
        if result.state.applied is None:
            return None
        return result.state.applied.managed(setting_id)


class TestFirstApply(ExecutorTestCase):
    """Part B: the first-ownership rule."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>x"])
        )

    def test_first_apply_writes_the_desired_value(self):
        result, adapter = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})

        self.assertTrue(result.ok)
        self.assertEqual(adapter.snapshot()[TARGET_A], X)
        self.assertEqual([op[0] for op in adapter.writes], ["apply"])

    def test_first_apply_captures_the_pre_kedra_value_as_original(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})
        record = self.managed(result)

        self.assertIsNotNone(record)
        self.assertEqual(record.original, O)
        self.assertTrue(record.original_present)
        self.assertEqual(record.last_applied, X)

    def test_first_apply_creates_exactly_one_record(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})

        self.assertEqual(len(result.state.applied.managed_values), 1)
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_CREATED)

    def test_first_apply_uses_one_generation(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})

        self.assertTrue(result.committed)
        self.assertEqual(result.generation, 1)
        self.assertEqual(self.managed(result).generation, 1)

    def test_original_absent_is_recorded_when_the_target_was_unset(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {})
        record = self.managed(result)

        self.assertFalse(record.original_present)
        self.assertIsNone(record.original)

    def test_record_carries_module_and_policy_metadata(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})
        record = self.managed(result)

        self.assertEqual(record.module_id, "workflow")
        self.assertEqual(record.module_version, "0.2.0")
        self.assertEqual(record.policy, "managed")
        self.assertEqual(record.target, TARGET_A)

    def test_value_already_correct_takes_ownership_without_writing(self):
        result, adapter = self.run_execution(self.tree, self.make_state(), {TARGET_A: X})
        record = self.managed(result)

        self.assertEqual(adapter.writes, [])
        self.assertIsNotNone(record)
        self.assertEqual(record.original, X)
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_CREATED)


class TestUpdatePreservesOriginal(ExecutorTestCase):
    """Part B: original survives every later update."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )

    def test_update_writes_the_new_value(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: X})

        self.assertTrue(result.ok)
        self.assertEqual(adapter.snapshot()[TARGET_A], Y)

    def test_update_preserves_the_original(self):
        """v1 wrote X over O; v2 writes Y. The original must still be O."""
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X})
        record = self.managed(result)

        self.assertEqual(record.original, O)
        self.assertNotEqual(record.original, X)
        self.assertEqual(record.last_applied, Y)

    def test_original_survives_repeated_updates(self):
        current = self.make_state(managed=[self.record(last_applied=X, original=O)])
        for desired in (Y, X, Y):
            tree = self.tree_with(
                gsettings_toml("example-a", key="key-a", data=list(desired.data))
            )
            observed = {TARGET_A: current.applied.managed("example-a").last_applied}
            result, _ = self.run_execution(tree, current, observed)
            current = result.state
            self.assertEqual(current.applied.managed("example-a").original, O)

    def test_update_bumps_generation_and_module_version(self):
        user_state = self.make_state(
            managed=[self.record(last_applied=X, original=O, generation=1)], generation=1
        )
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X})
        record = self.managed(result)

        self.assertEqual(result.generation, 2)
        self.assertEqual(record.generation, 2)
        self.assertEqual(record.module_version, "0.2.0")
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_UPDATED)

    def test_original_absent_stays_absent_across_updates(self):
        user_state = self.make_state(
            managed=[self.record(last_applied=X, original=None, present=False)]
        )
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X})
        record = self.managed(result)

        self.assertFalse(record.original_present)
        self.assertIsNone(record.original)


class TestUserDivergence(ExecutorTestCase):
    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )

    def test_preserve_performs_zero_writes(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: Z})

        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot()[TARGET_A], Z)
        self.assertFalse(result.changes[0].attempted)
        self.assertFalse(result.changes[0].wrote)

    def test_preserve_does_not_falsify_last_applied(self):
        """last_applied must keep pointing at what Kedra actually wrote."""
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: Z})
        record = result.state.applied.managed("example-a")

        self.assertEqual(record.last_applied, X)
        self.assertNotEqual(record.last_applied, Y)
        self.assertNotEqual(record.last_applied, Z)

    def test_divergence_remains_detectable_afterwards(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: Z})

        # Re-planning from the resulting state must reach the same conclusion.
        second, adapter = self.run_execution(self.tree, result.state, {TARGET_A: Z})
        self.assertEqual(second.changes[0].decision, ownership.PRESERVE_USER_CHANGE)
        self.assertEqual(adapter.writes, [])

    def test_preserve_only_plan_does_not_commit(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: Z})

        self.assertFalse(result.committed)
        self.assertIsNone(result.generation)
        self.assertIs(result.state, user_state)

    def test_user_converging_on_the_desired_value_refreshes_without_writing(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: Y})
        record = self.managed(result)

        self.assertEqual(adapter.writes, [])
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_REFRESHED)
        self.assertEqual(record.last_applied, Y)
        self.assertEqual(record.original, O)


class TestUnmanage(ExecutorTestCase):
    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>x"])
        )
        self.disabled = [state.ModuleOverride(id="workflow", enabled=False)]

    def test_unchanged_value_reverts_to_original_and_drops_the_record(self):
        user_state = self.make_state(
            overrides=self.disabled, managed=[self.record(last_applied=X, original=O)]
        )
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: X})

        self.assertEqual(adapter.snapshot()[TARGET_A], O)
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_REMOVED)
        self.assertEqual(result.state.applied.managed_values, ())

    def test_absent_original_causes_a_reset_not_a_fabricated_write(self):
        user_state = self.make_state(
            overrides=self.disabled,
            managed=[self.record(last_applied=X, original=None, present=False)],
        )
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: X})

        self.assertEqual([op[0] for op in adapter.writes], ["reset"])
        self.assertTrue(adapter.is_unset(TARGET_A))
        self.assertTrue(result.changes[0].reset)
        self.assertIsNone(result.changes[0].final_value)
        self.assertEqual(result.state.applied.managed_values, ())

    def test_diverged_value_is_not_written_and_the_record_is_dropped(self):
        user_state = self.make_state(
            overrides=self.disabled, managed=[self.record(last_applied=X, original=O)]
        )
        result, adapter = self.run_execution(self.tree, user_state, {TARGET_A: Z})

        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot()[TARGET_A], Z)   # user's value intact
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_REMOVED)
        self.assertEqual(result.state.applied.managed_values, ())

    def test_dropping_a_record_commits(self):
        user_state = self.make_state(
            overrides=self.disabled, managed=[self.record(last_applied=X, original=O)]
        )
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: Z})

        self.assertTrue(result.committed)
        self.assertEqual(result.generation, 2)


class TestExplicitRestore(ExecutorTestCase):
    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )

    def test_force_apply_overwrites_user_divergence(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, adapter = self.run_execution(
            self.tree, user_state, {TARGET_A: Z}, operation=ownership.OPERATION_RESTORE
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.changes[0].decision, ownership.FORCE_APPLY)
        self.assertEqual(adapter.snapshot()[TARGET_A], Y)

    def test_force_apply_updates_last_applied(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(
            self.tree, user_state, {TARGET_A: Z}, operation=ownership.OPERATION_RESTORE
        )
        self.assertEqual(self.managed(result).last_applied, Y)

    def test_force_apply_preserves_the_original(self):
        """Restore re-asserts within the lifecycle; it does not start a new one."""
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(
            self.tree, user_state, {TARGET_A: Z}, operation=ownership.OPERATION_RESTORE
        )
        self.assertEqual(self.managed(result).original, O)

    def test_revert_after_restore_still_returns_the_true_original(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        restored, _ = self.run_execution(
            self.tree, user_state, {TARGET_A: Z}, operation=ownership.OPERATION_RESTORE
        )

        disabled = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="workflow", enabled=False),),
            applied=restored.state.applied,
        )
        result, adapter = self.run_execution(self.tree, disabled, {TARGET_A: Y})
        self.assertEqual(adapter.snapshot()[TARGET_A], O)


class TestDefaultPolicy(ExecutorTestCase):
    """Part I: vendor defaults never become managed settings."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>x"], policy="default")
        )

    def test_default_policy_is_never_written(self):
        result, adapter = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})

        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot()[TARGET_A], O)

    def test_default_policy_creates_no_managed_record(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})

        self.assertFalse(result.committed)
        self.assertIsNone(result.state.applied)
        self.assertEqual(result.changes[0].record_action, execute_mod.RECORD_NONE)

    def test_default_policy_with_a_diverged_value_still_writes_nothing(self):
        result, adapter = self.run_execution(self.tree, self.make_state(), {TARGET_A: Z})
        self.assertEqual(adapter.writes, [])
        self.assertFalse(result.committed)

    def test_executor_refuses_a_default_policy_write_entry(self):
        """Defence in depth: even a malformed plan cannot force the write."""
        entry = plan_mod.PlannedChange(
            setting_id="example-a", backend="gsettings", target=TARGET_A,
            module_id="workflow", module_version="0.2.0", policy="default",
            decision=ownership.APPLY, reason="forced", requires_confirmation=False,
            writes=True, current_value=O, desired_value=X, original_value=None,
            reset_to_default=False,
        )
        adapter = backends.InMemoryAdapter()
        with self.assertRaises(execute_mod.ExecutionError) as ctx:
            execute_mod._execute_write(entry, {"gsettings": adapter}, None, 1)
        self.assertIn("vendor defaults are never applied", str(ctx.exception))
        self.assertEqual(adapter.writes, [])


class TestFailures(ExecutorTestCase):
    """Part E: state must never claim a write that did not happen."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )

    def test_first_write_failure_creates_no_ownership_record(self):
        adapter = backends.InMemoryAdapter(values={TARGET_A: O}, fail_apply={TARGET_A})
        result, _ = self.run_execution(
            self.tree, self.make_state(), {TARGET_A: O}, adapter=adapter
        )

        self.assertFalse(result.ok)
        self.assertFalse(result.committed)
        self.assertIsNone(result.state.applied)
        change = result.changes[0]
        self.assertTrue(change.attempted)
        self.assertFalse(change.wrote)
        self.assertIn("injected write failure", change.error)

    def test_update_failure_does_not_claim_the_new_value(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        adapter = backends.InMemoryAdapter(values={TARGET_A: X}, fail_apply={TARGET_A})
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X}, adapter=adapter)

        self.assertFalse(result.ok)
        self.assertFalse(result.committed)
        # The record still says X, which is the truth.
        self.assertEqual(result.state.applied.managed("example-a").last_applied, X)

    def test_reset_failure_does_not_drop_the_record(self):
        user_state = self.make_state(
            overrides=[state.ModuleOverride(id="workflow", enabled=False)],
            managed=[self.record(last_applied=X, original=None, present=False)],
        )
        adapter = backends.InMemoryAdapter(values={TARGET_A: X}, fail_reset={TARGET_A})
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X}, adapter=adapter)

        self.assertFalse(result.ok)
        self.assertFalse(result.committed)
        self.assertIsNotNone(result.state.applied.managed("example-a"))

    def test_revert_failure_keeps_ownership(self):
        user_state = self.make_state(
            overrides=[state.ModuleOverride(id="workflow", enabled=False)],
            managed=[self.record(last_applied=X, original=O)],
        )
        adapter = backends.InMemoryAdapter(values={TARGET_A: X}, fail_apply={TARGET_A})
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X}, adapter=adapter)

        self.assertFalse(result.committed)
        self.assertIsNotNone(result.state.applied.managed("example-a"))

    def test_unknown_backend_is_refused_before_writing(self):
        entry = plan_mod.PlannedChange(
            setting_id="example-a", backend="nonexistent", target=TARGET_A,
            module_id="workflow", module_version="0.2.0", policy="managed",
            decision=ownership.APPLY, reason="", requires_confirmation=False,
            writes=True, current_value=O, desired_value=X, original_value=None,
            reset_to_default=False,
        )
        with self.assertRaises(execute_mod.ExecutionError):
            execute_mod._execute_write(entry, {}, None, 1)


class TestPartialFailure(ExecutorTestCase):
    """Two settings, one failing: exactly what the documented model says."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
            + gsettings_toml("example-b", key="key-b", data=["<Super>y"])
        )

    def observed(self):
        return {TARGET_A: O, TARGET_B: O}

    def test_successful_write_is_committed_despite_a_later_failure(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        result, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.partial)
        self.assertTrue(result.committed)
        self.assertEqual(adapter.snapshot()[TARGET_A], Y)
        self.assertEqual(adapter.snapshot()[TARGET_B], O)

    def test_only_the_successful_setting_gets_a_record(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        result, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )
        ids = [r.setting_id for r in result.state.applied.managed_values]

        self.assertEqual(ids, ["example-a"])

    def test_execution_continues_past_a_failure(self):
        """Entries are independent; a failure must not abandon the rest."""
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_A}
        )
        result, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )

        self.assertEqual(len(result.changes), 2)
        self.assertEqual(adapter.snapshot()[TARGET_B], Y)
        self.assertEqual([r.setting_id for r in result.state.applied.managed_values],
                         ["example-b"])

    def test_no_rollback_of_the_successful_write(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        self.run_execution(self.tree, self.make_state(), self.observed(), adapter=adapter)

        # Exactly one apply for A, one failed attempt for B, and nothing undone.
        self.assertEqual([op for op in adapter.writes],
                         [("apply", TARGET_A), ("apply", TARGET_B)])
        self.assertEqual(adapter.snapshot()[TARGET_A], Y)

    def test_retry_after_partial_failure_is_safe_and_completes(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        first, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )
        self.assertFalse(first.ok)

        # The failure clears; retry from the committed state and fresh values.
        adapter.fail_apply.clear()
        observed = {TARGET_A: adapter.snapshot()[TARGET_A], TARGET_B: adapter.snapshot()[TARGET_B]}
        second, _ = self.run_execution(
            self.tree, first.state, observed, adapter=adapter
        )

        self.assertTrue(second.ok)
        self.assertEqual(adapter.snapshot()[TARGET_B], Y)
        self.assertEqual(
            sorted(r.setting_id for r in second.state.applied.managed_values),
            ["example-a", "example-b"],
        )

    def test_retry_does_not_rewrite_the_already_successful_setting(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        first, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )
        adapter.fail_apply.clear()
        adapter.operations.clear()

        observed = {TARGET_A: adapter.snapshot()[TARGET_A], TARGET_B: adapter.snapshot()[TARGET_B]}
        second, _ = self.run_execution(self.tree, first.state, observed, adapter=adapter)

        self.assertEqual(adapter.writes, [("apply", TARGET_B)])
        by_id = {c.setting_id: c for c in second.changes}
        self.assertEqual(by_id["example-a"].decision, ownership.NOOP)

    def test_retry_preserves_the_original_of_the_first_success(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        first, _ = self.run_execution(
            self.tree, self.make_state(), self.observed(), adapter=adapter
        )
        adapter.fail_apply.clear()
        observed = {TARGET_A: adapter.snapshot()[TARGET_A], TARGET_B: adapter.snapshot()[TARGET_B]}
        second, _ = self.run_execution(self.tree, first.state, observed, adapter=adapter)

        self.assertEqual(second.state.applied.managed("example-a").original, O)
        self.assertEqual(second.state.applied.managed("example-b").original, O)


class TestGenerationSemantics(ExecutorTestCase):
    """Part D."""

    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
            + gsettings_toml("example-b", key="key-b", data=["<Super>y"])
        )

    def test_multiple_successful_entries_share_one_generation(self):
        result, _ = self.run_execution(
            self.tree, self.make_state(), {TARGET_A: O, TARGET_B: O}
        )
        generations = {r.generation for r in result.state.applied.managed_values}

        self.assertEqual(len(result.state.applied.managed_values), 2)
        self.assertEqual(generations, {1})
        self.assertEqual(result.generation, 1)

    def test_generation_increments_exactly_once_per_transaction(self):
        first, adapter = self.run_execution(
            self.tree, self.make_state(), {TARGET_A: O, TARGET_B: O}
        )
        self.assertEqual(first.generation, 1)

        tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>x"])
            + gsettings_toml("example-b", key="key-b", data=["<Super>x"])
        )
        second, _ = self.run_execution(
            tree, first.state, {TARGET_A: Y, TARGET_B: Y}, adapter=adapter
        )

        self.assertEqual(second.generation, 2)
        self.assertEqual({r.generation for r in second.state.applied.managed_values}, {2})

    def test_all_noop_plan_does_not_increment(self):
        user_state = self.make_state(
            managed=[
                self.record("example-a", TARGET_A, last_applied=Y),
                self.record("example-b", TARGET_B, last_applied=Y),
            ]
        )
        result, adapter = self.run_execution(
            self.tree, user_state, {TARGET_A: Y, TARGET_B: Y}
        )

        self.assertFalse(result.committed)
        self.assertIsNone(result.generation)
        self.assertEqual(adapter.writes, [])
        self.assertEqual(result.state.applied.generation, 1)

    def test_preserve_only_plan_does_not_increment(self):
        user_state = self.make_state(
            managed=[
                self.record("example-a", TARGET_A, last_applied=X),
                self.record("example-b", TARGET_B, last_applied=X),
            ]
        )
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: Z, TARGET_B: Z})

        self.assertFalse(result.committed)
        self.assertIsNone(result.generation)

    def test_partial_failure_still_uses_the_precomputed_generation(self):
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, TARGET_B: O}, fail_apply={TARGET_B}
        )
        result, _ = self.run_execution(
            self.tree, self.make_state(), {TARGET_A: O, TARGET_B: O}, adapter=adapter
        )

        self.assertEqual(result.generation, 1)
        self.assertEqual(result.state.applied.managed("example-a").generation, 1)

    def test_empty_plan_does_not_commit(self):
        tree = self.tree_with("")
        result, _ = self.run_execution(tree, self.make_state(), {})

        self.assertEqual(result.changes, ())
        self.assertFalse(result.committed)


class TestExecutionResultShape(ExecutorTestCase):
    def setUp(self):
        self.tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )

    def test_result_fields_for_a_successful_write(self):
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, _ = self.run_execution(self.tree, user_state, {TARGET_A: X})
        change = result.changes[0]

        self.assertEqual(change.setting_id, "example-a")
        self.assertEqual(change.backend, "gsettings")
        self.assertEqual(change.target, TARGET_A)
        self.assertTrue(change.attempted)
        self.assertTrue(change.wrote)
        self.assertTrue(change.succeeded)
        self.assertEqual(change.previous_value, X)
        self.assertEqual(change.final_value, Y)
        self.assertEqual(change.record_action, execute_mod.RECORD_UPDATED)
        self.assertTrue(change.reason)
        self.assertIsNone(change.error)

    def test_json_is_deterministic_and_versioned(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})
        payload = result.to_json()

        self.assertEqual(payload, result.to_json())
        import json

        parsed = json.loads(payload)
        self.assertEqual(
            parsed["execution_schema_version"], execute_mod.EXECUTION_SCHEMA_VERSION
        )
        self.assertEqual(parsed["summary"]["wrote"], 1)
        self.assertTrue(parsed["ok"])

    def test_record_action_vocabulary_is_closed(self):
        self.assertEqual(
            execute_mod.RECORD_ACTIONS,
            {"created", "updated", "refreshed", "removed", "unchanged", "none"},
        )

    def test_result_is_immutable(self):
        result, _ = self.run_execution(self.tree, self.make_state(), {TARGET_A: O})
        with self.assertRaises(Exception):
            result.changes[0].wrote = False


class TestMigrationBoundary(ExecutorTestCase):
    """Part J: refuse rather than corrupt ownership."""

    def test_renamed_setting_requires_migration(self):
        tree = self.tree_with(
            gsettings_toml("example-new", key="key-a", data=["<Super>y"])
        )
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow",
            version="0.2.0",
            settings=gsettings_toml("example-new", key="key-a", data=["<Super>y"]),
            migrations="""
[[module.migrations]]
type = "rename-setting"
module_version = "0.2.0"
from_id = "example-old"
to_id = "example-new"
""",
        )
        tree = self.load(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )
        user_state = self.make_state(
            managed=[self.record(setting_id="example-old", last_applied=X)]
        )
        with self.assertRaises(execute_mod.MigrationRequired) as ctx:
            self.run_execution(tree, user_state, {TARGET_A: X})
        self.assertIn("was renamed to 'example-new'", str(ctx.exception))

    def test_retargeted_setting_requires_migration(self):
        tree = self.tree_with(
            gsettings_toml("example-a", key="key-moved", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record(target=TARGET_A, last_applied=X)])

        with self.assertRaises(execute_mod.MigrationRequired) as ctx:
            self.run_execution(tree, user_state, {TARGET_A: X})
        self.assertIn("resolve the retarget migration", str(ctx.exception))

    def test_migration_check_runs_before_any_write(self):
        tree = self.tree_with(
            gsettings_toml("example-a", key="key-moved", data=["<Super>y"])
        )
        user_state = self.make_state(managed=[self.record(target=TARGET_A, last_applied=X)])
        adapter = backends.InMemoryAdapter(values={TARGET_A: X})

        with self.assertRaises(execute_mod.MigrationRequired):
            self.run_execution(tree, user_state, {TARGET_A: X}, adapter=adapter)
        self.assertEqual(adapter.writes, [])

    def test_removed_setting_is_not_refused(self):
        """A genuine removal reverts normally; only renames need resolving."""
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["workflow"] = module_toml(
            "workflow",
            version="0.2.0",
            migrations="""
[[module.migrations]]
type = "remove-setting"
module_version = "0.2.0"
setting_id = "example-a"
on_managed = "revert"
""",
        )
        tree = self.load(
            self.make_tree(
                modules=modules,
                presets={"kedra": base_preset_toml("kedra", KEDRA_MODULES)},
            )
        )
        user_state = self.make_state(managed=[self.record(last_applied=X, original=O)])
        result, adapter = self.run_execution(tree, user_state, {TARGET_A: X})

        self.assertTrue(result.ok)
        self.assertEqual(adapter.snapshot()[TARGET_A], O)
        self.assertEqual(result.state.applied.managed_values, ())


class TestNoUnrelatedWrites(ExecutorTestCase):
    """The executor touches only targets it was given."""

    def test_only_declared_targets_are_touched(self):
        tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, "gsettings:org.other.app:unrelated": Z}
        )
        self.run_execution(tree, self.make_state(), {TARGET_A: O}, adapter=adapter)

        touched = {op[1] for op in adapter.operations}
        self.assertEqual(touched, {TARGET_A})
        self.assertEqual(adapter.snapshot()["gsettings:org.other.app:unrelated"], Z)

    def test_no_snapshot_of_unrelated_state_is_created(self):
        tree = self.tree_with(
            gsettings_toml("example-a", key="key-a", data=["<Super>y"])
        )
        adapter = backends.InMemoryAdapter(
            values={TARGET_A: O, "gsettings:org.other.app:unrelated": Z}
        )
        result, _ = self.run_execution(
            tree, self.make_state(), {TARGET_A: O}, adapter=adapter
        )
        recorded = {r.target for r in result.state.applied.managed_values}

        self.assertEqual(recorded, {TARGET_A})


if __name__ == "__main__":
    unittest.main()
