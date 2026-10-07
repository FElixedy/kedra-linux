"""The ownership decision layer.

Pure and backend-independent: given what Kedra wants, what Kedra last did, and
what the value currently is, decide what *should* happen. Nothing here performs
side effects, reads a session, or knows what GSettings is.

The rule the whole layer exists to enforce:

    A normal Kedra update must never overwrite a value merely because it
    differs from the current Kedra recommendation. If Kedra previously managed
    a value and the observed value differs from what Kedra last applied, that
    is a user modification unless proven otherwise.

"Proven otherwise" is narrow: it means the observed value happens to equal what
Kedra now wants, or the user explicitly asked for a reset. Everything else is
the user's.
"""

from __future__ import annotations

from dataclasses import dataclass

from .values import TypedValue, values_equal

# Operations a caller can plan for.
OPERATION_APPLY = "apply"            # normal apply/upgrade of enabled modules
OPERATION_UNMANAGE = "unmanage"      # module switched off, or setting withdrawn
OPERATION_RESTORE = "restore-defaults"  # explicit, user-confirmed reset

OPERATIONS = frozenset({OPERATION_APPLY, OPERATION_UNMANAGE, OPERATION_RESTORE})

# Decision outcomes.
APPLY = "apply"                           # first write; take ownership
UPDATE = "update"                         # we own it and it should change
NOOP = "noop"                             # nothing to do
PRESERVE_USER_CHANGE = "preserve-user-change"  # user diverged; leave it alone
REVERT_TO_ORIGINAL = "revert-to-original"      # give the value back
DROP_RECORD = "drop-record"               # stop managing, touch nothing
FORCE_APPLY = "force-apply"               # explicit reset over user divergence

# A write this layer authorized, refused afterwards because performing it would
# take a keyboard accelerator an unrelated user shortcut already holds -- or
# because whether it would could not be established.
#
# Deliberately *not* produced by `decide()`. Ownership answers "may Kedra write
# this value?", which is a question about Kedra and the user's history with this
# setting. Whether the resulting shortcut would collide with an unrelated one is
# a different question, about the rest of the system, and mixing the two would
# let a conflict re-open a value the user had diverged. See
# `plan.apply_conflict_gate` for the precedence.
BLOCKED_BY_CONFLICT = "blocked-by-conflict"

DECISIONS = frozenset(
    {
        APPLY,
        UPDATE,
        NOOP,
        PRESERVE_USER_CHANGE,
        REVERT_TO_ORIGINAL,
        DROP_RECORD,
        FORCE_APPLY,
        BLOCKED_BY_CONFLICT,
    }
)


@dataclass(frozen=True)
class Outcome:
    """What should happen to one setting, and why.

    `writes` is the flag an executor keys off: it is the difference between a
    plan that changes the system and one that only changes bookkeeping.
    """

    decision: str
    reason: str
    writes: bool = False
    # The value an executor should write. None together with reset_to_default
    # means "unset the key" rather than "write nothing".
    value_to_write: TypedValue | None = None
    reset_to_default: bool = False
    requires_confirmation: bool = False
    records_ownership: bool = False
    drops_record: bool = False


def decide(
    *,
    operation: str,
    desired: "SettingDeclaration | None",  # noqa: F821 - avoids a circular import
    record: "ManagedValue | None",  # noqa: F821
    observed: TypedValue | None,
) -> Outcome:
    """Decide what to do with one setting. No side effects.

    `observed` is None when the value is unset (at its vendor default), which
    is deliberately distinct from any concrete value.
    """
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation {operation!r}")

    if operation == OPERATION_APPLY:
        return _decide_apply(desired, record, observed)
    if operation == OPERATION_UNMANAGE:
        return _decide_unmanage(record, observed)
    return _decide_restore(desired, record, observed)


def _diverged(record, observed: TypedValue | None) -> bool:
    """Has the value moved since Kedra last wrote it?

    This is the single question the conservative rule turns on. Note it
    compares against `last_applied`, not against what Kedra wants now: a value
    that differs from the *current recommendation* is not evidence of anything.
    """
    return not values_equal(record.last_applied, observed)


def _decide_apply(desired, record, observed) -> Outcome:
    if desired is None:
        # Nothing wants this setting any more; that is an unmanage.
        return _decide_unmanage(record, observed)

    # A 'default' policy setting is a recommendation, not something Kedra
    # writes or owns. It belongs in a native vendor-default mechanism, so there
    # is nothing to apply and no record to keep.
    if not desired.creates_managed_record:
        return Outcome(
            decision=NOOP,
            reason=(
                "policy 'default' is delivered as a vendor default, not written "
                "by Kedra; no value is applied and no ownership is taken"
            ),
        )

    if record is None:
        # CASE 1: no managed record. Whatever is there now is the pre-Kedra
        # value, and becomes `original` so a later revert can restore it.
        if values_equal(observed, desired.value):
            return Outcome(
                decision=APPLY,
                reason="no managed record; value already matches, recording ownership",
                writes=False,
                value_to_write=desired.value,
                records_ownership=True,
            )
        return Outcome(
            decision=APPLY,
            reason="no managed record; taking ownership and applying Kedra's value",
            writes=True,
            value_to_write=desired.value,
            records_ownership=True,
        )

    if not _diverged(record, observed):
        # We still own it: the value is exactly what Kedra last wrote.
        if values_equal(record.last_applied, desired.value):
            return Outcome(
                decision=NOOP,
                reason="already at Kedra's value and unchanged by the user",
            )
        # CASE 2: safe automatic update.
        return Outcome(
            decision=UPDATE,
            reason="value is unchanged since Kedra applied it; updating to the new value",
            writes=True,
            value_to_write=desired.value,
            records_ownership=True,
        )

    # The value moved since Kedra wrote it.
    if values_equal(observed, desired.value):
        # The user independently arrived at what Kedra now wants. Nothing to
        # write; the record is refreshed so this stops reading as divergence.
        return Outcome(
            decision=NOOP,
            reason="user's current value already matches Kedra's new value",
            records_ownership=True,
        )

    # CASE 3: the conservative rule. Do not overwrite.
    return Outcome(
        decision=PRESERVE_USER_CHANGE,
        reason=(
            "value changed since Kedra applied it; treating as a user modification "
            "and leaving it alone"
        ),
        requires_confirmation=True,
    )


def _decide_unmanage(record, observed) -> Outcome:
    if record is None:
        return Outcome(decision=NOOP, reason="not managed by Kedra; nothing to undo")

    if _diverged(record, observed):
        # CASE 5: the user changed it before disabling the module. Their value
        # stands; Kedra simply stops claiming it.
        return Outcome(
            decision=DROP_RECORD,
            reason=(
                "value changed since Kedra applied it; dropping the management record "
                "without touching the user's value"
            ),
            drops_record=True,
        )

    # CASE 4: still exactly what Kedra wrote, so giving it back is safe.
    if record.original_present:
        return Outcome(
            decision=REVERT_TO_ORIGINAL,
            reason="unchanged since Kedra applied it; restoring the pre-Kedra value",
            writes=True,
            value_to_write=record.original,
            drops_record=True,
        )
    return Outcome(
        decision=REVERT_TO_ORIGINAL,
        reason="unchanged since Kedra applied it; no pre-Kedra value, resetting to default",
        writes=True,
        value_to_write=None,
        reset_to_default=True,
        drops_record=True,
    )


def _decide_restore(desired, record, observed) -> Outcome:
    """Explicit 'restore Kedra defaults'.

    Allowed to be destructive, but only inside the Kedra-owned scope the user
    selected, and always as a decision distinguishable from a normal update so
    a UI can label and confirm it.
    """
    if desired is None or not desired.creates_managed_record:
        if record is None:
            return Outcome(decision=NOOP, reason="not managed by Kedra; nothing to restore")
        if record.original_present:
            return Outcome(
                decision=REVERT_TO_ORIGINAL,
                reason="explicit reset: setting is no longer part of the profile",
                writes=True,
                value_to_write=record.original,
                requires_confirmation=True,
                drops_record=True,
            )
        return Outcome(
            decision=REVERT_TO_ORIGINAL,
            reason="explicit reset: setting is no longer part of the profile",
            writes=True,
            reset_to_default=True,
            requires_confirmation=True,
            drops_record=True,
        )

    if values_equal(observed, desired.value):
        return Outcome(decision=NOOP, reason="already at Kedra's value")

    if record is None:
        return Outcome(
            decision=APPLY,
            reason="explicit reset: taking ownership and applying Kedra's value",
            writes=True,
            value_to_write=desired.value,
            records_ownership=True,
        )

    if not _diverged(record, observed):
        return Outcome(
            decision=UPDATE,
            reason="explicit reset: value unchanged since Kedra applied it",
            writes=True,
            value_to_write=desired.value,
            records_ownership=True,
        )

    # CASE 6: this is the one path allowed to overwrite a user's change, and it
    # is a distinct decision precisely so it can never be reached by an upgrade.
    return Outcome(
        decision=FORCE_APPLY,
        reason=(
            "explicit reset: overwriting a user modification with Kedra's value "
            "at the user's request"
        ),
        writes=True,
        value_to_write=desired.value,
        requires_confirmation=True,
        records_ownership=True,
    )
