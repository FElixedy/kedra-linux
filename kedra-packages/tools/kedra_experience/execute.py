"""The execution layer: perform the writes a ChangePlan authorized.

Division of responsibility, and it is absolute:

    the planner decides, the executor executes.

Nothing here re-evaluates ownership. The executor does not compare observed
values against desired ones, does not ask whether a user diverged, and has no
opinion about policy. It reads a decision off each plan entry and carries it
out. If that seems to remove flexibility, that is the point: there is exactly
one place where "may Kedra overwrite this?" is answered, and it is not here.

Transaction model
-----------------
There is no atomic transaction across a desktop backend, and this module does
not pretend otherwise. What it guarantees instead:

1. Backend writes happen first; state is updated only for writes that actually
   succeeded.
2. A failed write never produces a record claiming the value was applied.
3. State is persisted once, atomically, at the end of the run.
4. Partial failure is reported explicitly rather than smoothed over.

**No rollback.** When a later entry fails, earlier successful writes stand.
Undoing them would mean issuing more writes -- which can themselves fail,
turning one partial success into two inconsistent halves -- to revert changes
the user's desktop already reflects. Instead, every successful write is
recorded truthfully and the next run reconciles from state plus a fresh
observation. Because each entry is independent, execution continues past a
failure rather than abandoning the remaining settings.

**Retry safety.** Re-running after a partial failure is safe and idempotent:
the planner re-derives decisions from the committed state and freshly observed
values, so entries that succeeded become `noop` and only the failed ones are
attempted again.

**Known crash window.** State is committed after the backend writes. If the
process dies in between, the writes happened but no record exists. State is
then *stale*, not false: it under-claims ownership rather than over-claiming
it. The consequence is bounded and specific -- a subsequent run sees no record
and captures `original` from the current value, which is Kedra's own value
rather than the true pre-Kedra one, so a later revert restores that instead.
Closing this window needs an intent journal, which is deliberately not built
here; see docs/kedra-experience.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .backends import (
    BackendAdapter,
    BackendError,
    list_with_member,
    list_without_member,
)
from .ownership import (
    APPLY,
    BLOCKED_BY_CONFLICT,
    DROP_RECORD,
    FORCE_APPLY,
    NOOP,
    PRESERVE_USER_CHANGE,
    REVERT_TO_ORIGINAL,
    UPDATE,
)
from .state import ManagedValue, UserState
from .values import TypedValue

EXECUTION_SCHEMA_VERSION = 1

# What happened to the ownership record for one setting.
RECORD_CREATED = "created"
RECORD_UPDATED = "updated"
RECORD_REFRESHED = "refreshed"
RECORD_REMOVED = "removed"
RECORD_UNCHANGED = "unchanged"
RECORD_NONE = "none"

RECORD_ACTIONS = frozenset(
    {
        RECORD_CREATED,
        RECORD_UPDATED,
        RECORD_REFRESHED,
        RECORD_REMOVED,
        RECORD_UNCHANGED,
        RECORD_NONE,
    }
)


class ExecutionError(Exception):
    """Raised before any write when a plan cannot be executed safely."""


class MigrationRequired(ExecutionError):
    """A managed record needs a migration that has not been performed.

    Executing anyway would either revert a value that merely moved, or create a
    second ownership record for the same underlying target. Both are worse than
    stopping, so the executor refuses and the caller must resolve the migration
    first. The migration executor itself is a later task.
    """


@dataclass(frozen=True)
class ExecutedChange:
    """What actually happened to one setting."""

    setting_id: str
    backend: str
    target: str
    decision: str
    module_id: str | None
    # Did the executor attempt a backend mutation?
    attempted: bool
    # Did a backend mutation actually complete?
    wrote: bool
    succeeded: bool
    previous_value: TypedValue | None
    # What the value is expected to be now. None with `reset` means unset.
    final_value: TypedValue | None
    reset: bool
    record_action: str
    reason: str
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "setting_id": self.setting_id,
            "backend": self.backend,
            "target": self.target,
            "decision": self.decision,
            "module_id": self.module_id,
            "attempted": self.attempted,
            "wrote": self.wrote,
            "succeeded": self.succeeded,
            "reset": self.reset,
            "record_action": self.record_action,
            "reason": self.reason,
            "error": self.error,
            "previous_value": self.previous_value.to_dict() if self.previous_value else None,
            "final_value": self.final_value.to_dict() if self.final_value else None,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of one execution run."""

    operation: str
    # The transaction generation, or None when nothing was committed.
    generation: int | None
    committed: bool
    changes: tuple[ExecutedChange, ...]
    # State after the run. Identical to the input state when nothing committed.
    state: UserState

    @property
    def failures(self) -> tuple[ExecutedChange, ...]:
        return tuple(c for c in self.changes if not c.succeeded)

    @property
    def writes(self) -> tuple[ExecutedChange, ...]:
        return tuple(c for c in self.changes if c.wrote)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def partial(self) -> bool:
        """Some writes succeeded and some failed."""
        return bool(self.failures) and bool(self.writes)

    def to_dict(self) -> dict:
        return {
            "execution_schema_version": EXECUTION_SCHEMA_VERSION,
            "operation": self.operation,
            "generation": self.generation,
            "committed": self.committed,
            "ok": self.ok,
            "partial": self.partial,
            "summary": {
                "total": len(self.changes),
                "wrote": len(self.writes),
                "failed": len(self.failures),
            },
            "changes": [change.to_dict() for change in self.changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def check_migrations(tree, user_state) -> None:
    """Refuse to execute when an ownership record needs migrating.

    Two cases, both of which would silently corrupt ownership:

    - the record's setting ID appears as the `from_id` of a `rename-setting`
      migration, so the setting still exists under a new ID. Executing would
      revert the old ID and separately take ownership under the new one.
    - the setting still exists under the same ID but its declared target has
      moved, so the record points at a target Kedra no longer manages.

    A `remove-setting` migration is deliberately *not* refused: the setting is
    genuinely gone, and reverting it is the correct behaviour.
    """
    if user_state.applied is None:
        return

    from .manifests import module_setting_entries

    declared: dict[str, dict] = {}
    renamed_from: dict[str, str] = {}
    for module_id, module in tree.modules.items():
        # The expanded view, so a shortcut whose path moved is caught by the
        # same retarget check as any other setting. Changing a shortcut's path
        # is a migration, and this is where executing without one is refused.
        for entry in module_setting_entries(module_id, module, tree.modules):
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                declared[entry["id"]] = entry
        for entry in module.get("migrations", []):
            if isinstance(entry, dict) and entry.get("type") == "rename-setting":
                if isinstance(entry.get("from_id"), str):
                    renamed_from[entry["from_id"]] = entry.get("to_id", "?")

    from .backends import spec_for

    for record in user_state.applied.managed_values:
        if record.setting_id in renamed_from:
            raise MigrationRequired(
                f"managed record '{record.setting_id}' was renamed to "
                f"'{renamed_from[record.setting_id]}'; resolve the migration before "
                f"executing, or ownership would be duplicated"
            )
        entry = declared.get(record.setting_id)
        if entry is None:
            continue
        spec = spec_for(entry.get("backend", ""))
        if spec is None or not isinstance(entry.get("target"), dict):
            continue
        if spec.target_problems(entry["target"]):
            continue
        current_target = spec.canonical_target(entry["target"])
        if current_target != record.target:
            raise MigrationRequired(
                f"managed record '{record.setting_id}' points at '{record.target}' but the "
                f"setting now targets '{current_target}'; resolve the retarget migration "
                f"before executing"
            )


def execute_plan(
    *,
    plan,
    adapters: dict[str, BackendAdapter],
    tree,
    resolution,
    user_state: UserState,
    experience_schema_version: int,
    applied_at: str | None = None,
) -> ExecutionResult:
    """Carry out a plan. Performs backend writes; does not persist state.

    Returns the state that *should* be persisted. Persisting it is the
    caller's job, under the state lock -- see `locking.state_lock`.
    """
    check_migrations(tree, user_state)

    # One generation for the whole transaction, computed before any write, so
    # every record this run touches carries the same value.
    generation = user_state.next_generation()
    stamp = applied_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )

    records: dict[str, ManagedValue] = {
        record.setting_id: record
        for record in (user_state.applied.managed_values if user_state.applied else ())
    }

    changes: list[ExecutedChange] = []
    mutated = False

    for entry in plan.entries:
        change, record, changed = _execute_entry(
            entry, adapters, records.get(entry.setting_id), generation
        )
        changes.append(change)
        if not changed:
            continue
        mutated = True
        if record is None:
            records.pop(entry.setting_id, None)
        else:
            records[entry.setting_id] = record

    if not mutated:
        # Nothing changed persistently: an all-noop or preserve-only run is a
        # read-only operation. It must not burn a generation or rewrite state.
        return ExecutionResult(
            operation=plan.operation,
            generation=None,
            committed=False,
            changes=tuple(changes),
            state=user_state,
        )

    new_state = user_state.with_applied(
        resolution,
        experience_schema_version=experience_schema_version,
        applied_at=stamp,
        generation=generation,
        managed_values=tuple(sorted(records.values(), key=lambda r: r.setting_id)),
    )

    return ExecutionResult(
        operation=plan.operation,
        generation=generation,
        committed=True,
        changes=tuple(changes),
        state=new_state,
    )


def _execute_entry(
    entry, adapters: dict[str, BackendAdapter], record: ManagedValue | None, generation: int
) -> tuple[ExecutedChange, ManagedValue | None, bool]:
    """Execute one plan entry.

    Returns (change, new record or None, whether the record set changed).
    """
    decision = entry.decision

    # Decisions that must never touch a backend. Checked first and by name, so
    # that a mistake in the write flags below cannot turn a preserved user
    # change -- or a refusal to take somebody else's accelerator -- into a write.
    if decision in (PRESERVE_USER_CHANGE, NOOP, BLOCKED_BY_CONFLICT) or not entry.writes:
        return _execute_without_writing(entry, record, generation)

    if decision in (APPLY, UPDATE, FORCE_APPLY):
        return _execute_write(entry, adapters, record, generation)
    if decision == REVERT_TO_ORIGINAL:
        return _execute_revert(entry, adapters, record)
    if decision == DROP_RECORD:
        return _execute_without_writing(entry, record, generation)

    raise ExecutionError(f"unknown decision {decision!r} for setting '{entry.setting_id}'")


def _execute_without_writing(
    entry, record: ManagedValue | None, generation: int
) -> tuple[ExecutedChange, ManagedValue | None, bool]:
    """Handle every decision that performs no backend mutation."""
    decision = entry.decision

    if decision == DROP_RECORD:
        # The user changed the value and the module is going away. Their value
        # stands untouched; Kedra simply stops claiming it.
        return (
            _change(entry, record, attempted=False, wrote=False, succeeded=True,
                    final=entry.current_value, action=RECORD_REMOVED),
            None,
            record is not None,
        )

    if decision == BLOCKED_BY_CONFLICT:
        # Nothing is written and no ownership is taken. Critically, an existing
        # record is left exactly as it was: a conflict appearing later must not
        # rewrite what Kedra last applied, or the divergence detection that
        # protects the user's own edits would be quietly reset.
        return (
            _change(entry, record, attempted=False, wrote=False, succeeded=True,
                    final=entry.current_value, action=RECORD_UNCHANGED),
            record,
            False,
        )

    if decision == PRESERVE_USER_CHANGE:
        # Crucially: the record is left exactly as it was. last_applied keeps
        # pointing at what Kedra actually wrote, which is the evidence that
        # makes the divergence detectable on every future run. Overwriting it
        # with the new desired value would erase that and silently re-adopt the
        # user's value as Kedra's own.
        return (
            _change(entry, record, attempted=False, wrote=False, succeeded=True,
                    final=entry.current_value, action=RECORD_UNCHANGED),
            record,
            False,
        )

    # NOOP, or APPLY whose value already matches.
    if entry.policy == "default":
        # Vendor-default settings are never owned by the executor. Guarded
        # explicitly rather than relying on the planner alone.
        return (
            _change(entry, record, attempted=False, wrote=False, succeeded=True,
                    final=entry.current_value, action=RECORD_NONE),
            record,
            False,
        )

    if decision == APPLY and record is None:
        # The value already equals what Kedra wants, but Kedra does not own it
        # yet. Take ownership without writing: `original` is the current value,
        # which is genuinely the pre-Kedra state.
        return (
            _change(entry, record, attempted=False, wrote=False, succeeded=True,
                    final=entry.desired_value, action=RECORD_CREATED),
            _new_record(entry, original=entry.current_value, generation=generation),
            True,
        )

    if decision == NOOP and record is not None and entry.desired_value is not None:
        if record.last_applied != entry.desired_value:
            # The user independently arrived at the value Kedra now wants.
            # Refreshing last_applied is bookkeeping only -- no write occurs --
            # and it exists so the value stops reading as divergence forever.
            # `original` is preserved: ownership began earlier, not now.
            return (
                _change(entry, record, attempted=False, wrote=False, succeeded=True,
                        final=entry.desired_value, action=RECORD_REFRESHED),
                replace(
                    record,
                    last_applied=entry.desired_value,
                    generation=generation,
                    module_version=entry.module_version or record.module_version,
                ),
                True,
            )

    return (
        _change(entry, record, attempted=False, wrote=False, succeeded=True,
                final=entry.current_value, action=RECORD_UNCHANGED),
        record,
        False,
    )


def _ensure_member(adapter, entry) -> None:
    """Put Kedra's entry into a list without disturbing anything else in it.

    Read-modify-write against the *effective* list, read fresh at write time
    rather than taken from the plan, so the window in which a concurrently added
    user entry could be lost is as small as it can be made without a transaction
    the backend does not offer.

    Idempotent: an entry already present is left where it is, so a retry after a
    partial failure appends nothing and reorders nothing.
    """
    current = _current_members(adapter, entry)
    updated = list_with_member(current, entry.member)
    if updated == current:
        return
    adapter.apply(entry.target, TypedValue(type="as", data=updated))


def _drop_member(adapter, entry, member: str) -> bool:
    """Take Kedra's entry out of a list and leave every other entry alone.

    This is the whole of the "disabling Terminal must not delete the shortcuts
    the user made" guarantee. Kedra never writes back a remembered list: it
    reads what is there now, removes one string, and writes the rest. Entries
    created before Kedra, after Kedra, or while Kedra was installed are all
    equally untouched, because none of them is ever named.

    Returns whether a write was performed.
    """
    current = _current_members(adapter, entry)
    updated = list_without_member(current, member)
    if updated == current:
        # Already gone. Not an error: the post-condition is what matters.
        return False
    if updated:
        adapter.apply(entry.target, TypedValue(type="as", data=updated))
    else:
        # Nothing left that Kedra did not put there. Unsetting rather than
        # writing an explicit empty list restores the key to exactly the state a
        # machine that never ran Kedra has -- an explicit [] and an unset key
        # read identically, so this is the tidier of two equivalent results.
        adapter.reset(entry.target)
    return True


def _current_members(adapter, entry) -> tuple[str, ...]:
    value = adapter.read_effective(entry.target)
    if value is None:
        return ()
    if value.type != "as":
        raise BackendError(
            f"{entry.target}: list membership needs an 'as' key, found {value.type!r}"
        )
    return tuple(value.data)


def _execute_write(
    entry, adapters, record: ManagedValue | None, generation: int
) -> tuple[ExecutedChange, ManagedValue | None, bool]:
    """apply / update / force-apply: write, then record only if it worked."""
    if entry.policy == "default":
        raise ExecutionError(
            f"setting '{entry.setting_id}' has policy 'default' but the plan asks for a "
            f"write; vendor defaults are never applied by the executor"
        )
    if entry.desired_value is None:
        raise ExecutionError(f"setting '{entry.setting_id}' has no desired value to write")

    adapter = _adapter_for(entry, adapters)

    try:
        if entry.is_membership:
            _ensure_member(adapter, entry)
        else:
            adapter.apply(entry.target, entry.desired_value)
    except BackendError as exc:
        # The write failed, so no record may claim the value was applied. An
        # existing record keeps its old last_applied, which stays true.
        return (
            _change(entry, record, attempted=True, wrote=False, succeeded=False,
                    final=entry.current_value, action=RECORD_UNCHANGED, error=str(exc)),
            record,
            False,
        )

    if record is None:
        # First ownership: `original` is the value observed before this write.
        return (
            _change(entry, record, attempted=True, wrote=True, succeeded=True,
                    final=entry.desired_value, action=RECORD_CREATED),
            _new_record(entry, original=entry.current_value, generation=generation),
            True,
        )

    # Update or forced re-assert. `original` is preserved untouched: it records
    # the pre-Kedra state, not the previous Kedra value. Restore-defaults
    # re-asserts Kedra's value within the existing ownership lifecycle; it does
    # not begin a new one, so a later unmanage still returns the true original.
    return (
        _change(entry, record, attempted=True, wrote=True, succeeded=True,
                final=entry.desired_value, action=RECORD_UPDATED),
        replace(
            record,
            last_applied=entry.desired_value,
            generation=generation,
            module_id=entry.module_id or record.module_id,
            module_version=entry.module_version or record.module_version,
            policy=entry.policy or record.policy,
            resource=entry.resource or record.resource,
            member=entry.member or record.member,
        ),
        True,
    )


def _execute_revert(
    entry, adapters, record: ManagedValue | None
) -> tuple[ExecutedChange, ManagedValue | None, bool]:
    """revert-to-original: give the value back, then stop claiming it."""
    if record is None:
        raise ExecutionError(
            f"setting '{entry.setting_id}': revert requires a managed record"
        )

    adapter = _adapter_for(entry, adapters)
    final: TypedValue | None
    wrote = True

    try:
        if entry.is_membership:
            # A membership revert is never a reset of the key. `original`
            # answers "was Kedra's entry in the list before Kedra?" -- if it
            # was, the entry stays where the user put it and there is nothing to
            # undo; if it was not, exactly that one entry comes out and every
            # other entry in the list is left untouched.
            if record.original_present:
                wrote = False
                final = record.original
            else:
                wrote = _drop_member(adapter, entry, record.member or entry.member)
                final = None
        elif entry.reset_to_default:
            # There was no value before Kedra, so the target is unset rather
            # than written back to a fabricated value.
            adapter.reset(entry.target)
            final = None
        else:
            adapter.apply(entry.target, record.original)
            final = record.original
    except BackendError as exc:
        # The revert failed, so Kedra still owns the value. Dropping the record
        # here would abandon a value Kedra wrote with no way to undo it.
        return (
            _change(entry, record, attempted=True, wrote=False, succeeded=False,
                    final=entry.current_value, action=RECORD_UNCHANGED, error=str(exc)),
            record,
            False,
        )

    # The record is dropped whether or not a write was needed: the
    # post-condition -- Kedra no longer claims this value -- is reached either
    # way, and reporting `wrote` truthfully is what keeps "nothing was written"
    # from being confused with "nothing was done".
    return (
        _change(entry, record, attempted=True, wrote=wrote, succeeded=True,
                final=final, action=RECORD_REMOVED),
        None,
        True,
    )


def _adapter_for(entry, adapters: dict[str, BackendAdapter]) -> BackendAdapter:
    adapter = adapters.get(entry.backend)
    if adapter is None:
        raise ExecutionError(
            f"setting '{entry.setting_id}': no adapter for backend '{entry.backend}'"
        )
    return adapter


def _new_record(entry, *, original: TypedValue | None, generation: int) -> ManagedValue:
    """Build a first-ownership record.

    `original` is captured exactly once, here, and never rewritten by a later
    update. `original_present` distinguishes "there was no value" from "there
    was a value", because the two revert differently.
    """
    return ManagedValue(
        setting_id=entry.setting_id,
        backend=entry.backend,
        target=entry.target,
        module_id=entry.module_id,
        module_version=entry.module_version,
        policy=entry.policy,
        generation=generation,
        last_applied=entry.desired_value,
        original=original,
        original_present=original is not None,
        resource=entry.resource,
        member=entry.member,
    )


def _change(
    entry,
    record,
    *,
    attempted: bool,
    wrote: bool,
    succeeded: bool,
    final: TypedValue | None,
    action: str,
    error: str | None = None,
) -> ExecutedChange:
    return ExecutedChange(
        setting_id=entry.setting_id,
        backend=entry.backend,
        target=entry.target,
        decision=entry.decision,
        module_id=entry.module_id,
        attempted=attempted,
        wrote=wrote,
        succeeded=succeeded,
        previous_value=entry.current_value,
        final_value=final,
        reset=entry.reset_to_default and wrote,
        record_action=action,
        reason=entry.reason,
        error=error,
    )
