"""Side-effect-free change plans.

A plan is what a future executor, a dry run, a configurator preview and a reset
preview all consume. Building one performs no I/O: observed values arrive as a
mapping the caller supplies, so there is no code path from here to a GNOME
session even by accident.

Deterministic by construction -- entries are ordered by execution phase and
then by setting ID -- so two runs over the same inputs produce byte-identical
JSON and a preview can be diffed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from .conflicts import ConflictReport
from .manifests import module_setting_entries
from .ownership import (
    APPLY,
    BLOCKED_BY_CONFLICT,
    DROP_RECORD,
    FORCE_APPLY,
    OPERATION_APPLY,
    OPERATION_RESTORE,
    OPERATION_UNMANAGE,
    OPERATIONS,
    REVERT_TO_ORIGINAL,
    UPDATE,
    Outcome,
    decide,
)
from .settings import SettingDeclaration, build_declaration
from .values import TypedValue

PLAN_SCHEMA_VERSION = 3

# Decisions that would put a value into the desktop. These are the only ones the
# accelerator-conflict gate can refuse -- everything else either changes nothing
# or takes Kedra's own value back out, and neither can create a clash.
_WRITING_DECISIONS = frozenset({APPLY, UPDATE, FORCE_APPLY})

# ---------------------------------------------------------------------------
# Execution phases
# ---------------------------------------------------------------------------
#
# A GNOME custom shortcut becomes live the instant its path enters
# `custom-keybindings`. Order the writes wrongly and there is a window in which
# a registered shortcut has no command -- a key combination the user presses
# that does nothing, or worse, launches a half-written command.
#
# So registration is always the *last* thing added and the *first* thing
# removed:
#
#   create  configure the child path, then register it
#   remove  deregister the path, then clean the child values
#
# Every ordinary setting sits in the middle phase, so nothing about the existing
# order changes: a plan with no membership entries is still sorted by setting ID
# alone.
PHASE_DEREGISTER = 0
PHASE_VALUE = 1
PHASE_REGISTER = 2

# Decisions that take a member *out* of a list. Everything else that touches a
# membership entry either puts it in or does nothing.
_REMOVING_DECISIONS = frozenset({REVERT_TO_ORIGINAL, DROP_RECORD})


@dataclass(frozen=True)
class PlannedChange:
    """One setting's entry in a plan."""

    setting_id: str
    backend: str
    target: str
    module_id: str | None
    module_version: str | None
    policy: str | None
    decision: str
    reason: str
    requires_confirmation: bool
    writes: bool
    current_value: TypedValue | None
    desired_value: TypedValue | None
    original_value: TypedValue | None
    reset_to_default: bool
    # The composite resource this entry belongs to, when it belongs to one.
    resource: str | None = None
    # For a list-membership entry: the single list element Kedra owns. When set,
    # `current_value` and `desired_value` are the *membership projection*
    # (`['member']` or None), never the surrounding list.
    member: str | None = None
    phase: int = PHASE_VALUE
    # Set when a write ownership authorized was refused by the conflict gate.
    # Distinct from `requires_confirmation`: a confirmable change is one the
    # user may approve, and this is not -- freeing the accelerator is a separate
    # action on a shortcut Kedra does not own.
    blocked_by_conflict: bool = False

    @property
    def is_membership(self) -> bool:
        return self.member is not None

    def to_dict(self) -> dict:
        return {
            "setting_id": self.setting_id,
            "backend": self.backend,
            "target": self.target,
            "module_id": self.module_id,
            "module_version": self.module_version,
            "policy": self.policy,
            "decision": self.decision,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "writes": self.writes,
            "reset_to_default": self.reset_to_default,
            "resource": self.resource,
            "member": self.member,
            "phase": self.phase,
            "blocked_by_conflict": self.blocked_by_conflict,
            "current_value": self.current_value.to_dict() if self.current_value else None,
            "desired_value": self.desired_value.to_dict() if self.desired_value else None,
            "original_value": self.original_value.to_dict() if self.original_value else None,
        }


@dataclass(frozen=True)
class ChangePlan:
    """A complete, ordered set of decisions for one operation."""

    operation: str
    generation: int
    entries: tuple[PlannedChange, ...]
    # One report per resource whose accelerator was examined, keyed by resource
    # ID. Carried on the plan rather than folded into the entries so a UI can
    # say *which shortcut* is in the way without reassembling it from four
    # settings that each mention it.
    conflicts: dict[str, ConflictReport] = field(default_factory=dict)

    @property
    def blocked(self) -> tuple[PlannedChange, ...]:
        return tuple(e for e in self.entries if e.blocked_by_conflict)

    @property
    def writes(self) -> tuple[PlannedChange, ...]:
        return tuple(e for e in self.entries if e.writes)

    @property
    def requires_confirmation(self) -> tuple[PlannedChange, ...]:
        return tuple(e for e in self.entries if e.requires_confirmation)

    def by_decision(self, decision: str) -> tuple[PlannedChange, ...]:
        return tuple(e for e in self.entries if e.decision == decision)

    def by_resource(self, resource: str) -> tuple[PlannedChange, ...]:
        return tuple(e for e in self.entries if e.resource == resource)

    def to_dict(self) -> dict:
        return {
            "plan_schema_version": PLAN_SCHEMA_VERSION,
            "operation": self.operation,
            "generation": self.generation,
            "summary": {
                "total": len(self.entries),
                "writes": len(self.writes),
                "requires_confirmation": len(self.requires_confirmation),
                "blocked_by_conflict": len(self.blocked),
            },
            "conflicts": {
                resource: report.to_dict()
                for resource, report in sorted(self.conflicts.items())
            },
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def declarations_for(tree, resolution) -> dict[str, SettingDeclaration]:
    """Every setting declared by the modules this resolution enables.

    Includes the settings a module's custom shortcuts expand into, which is why
    it goes through `module_setting_entries` rather than reading
    `module["settings"]` directly.

    Disabled modules contribute nothing: their settings are simply absent, and
    any managed record for them becomes an unmanage decision below.
    """
    declarations: dict[str, SettingDeclaration] = {}
    for module in resolution.modules:
        if not module.enabled:
            continue
        manifest = tree.modules[module.id]
        for entry in module_setting_entries(module.id, manifest, tree.modules):
            declaration = build_declaration(entry, module.id, module.version)
            declarations[declaration.id] = declaration
    return declarations


def project_membership(
    member: str, observed_list: TypedValue | None
) -> TypedValue | None:
    """Reduce an observed list to the one question Kedra is entitled to ask.

    Kedra does not own `custom-keybindings`; it owns the presence of one entry
    in it. So the whole list is projected down to "is my entry there?" before
    the ownership layer ever sees it:

        member present  ->  TypedValue('as', (member,))
        member absent   ->  None

    Everything downstream then works unchanged. `decide()` compares a value
    against a record with no idea a list was involved, divergence means "the
    user removed Kedra's entry" and nothing else, and `original` records whether
    the entry was there before Kedra rather than what the list used to be.

    The alternative -- storing the list itself as `last_applied` and `original`
    -- is what makes a configurator delete a user's shortcuts: reverting to a
    snapshot taken before Kedra ran removes every entry added since.
    """
    entries = tuple(observed_list.data) if observed_list is not None else ()
    return TypedValue(type="as", data=(member,)) if member in entries else None


def resource_cohesion(
    outcomes: dict[str, tuple], context: dict[str, dict]
) -> dict[str, tuple]:
    """Keep a composite resource whole when the user has edited part of it.

    The rule, and it is the least destructive answer to the awkward case:

        Once the user has materially modified any *value* of a Kedra-created
        resource, a normal unmanage stops claiming the whole resource and
        deletes none of it.

    Without this, disabling the Terminal module after a user had rebound
    Super+Enter to Super+T would drop the record for `binding` (their change is
    preserved -- correct) while reverting `name` and `command` and deregistering
    the path (their shortcut is destroyed -- not correct). The user would be
    left with a modified shortcut that no longer exists.

    A diverged *membership* entry does not trigger the rule. It means the user
    removed the shortcut themselves, in which case cleaning up the child values
    Kedra wrote is tidying, not destruction.

    `context` maps a setting ID to its `{"resource": ..., "member": ...}`, taken
    from the declaration where one exists and from the ownership record where the
    module has gone away -- so the rule still applies to a resource nothing
    declares any more, which is exactly when unmanage runs.
    """
    diverged_resources = {
        context[setting_id]["resource"]
        for setting_id, (outcome, _) in outcomes.items()
        if outcome.decision == DROP_RECORD
        and context[setting_id]["resource"] is not None
        and context[setting_id]["member"] is None
    }
    diverged_resources.discard(None)
    if not diverged_resources:
        return outcomes

    adjusted = dict(outcomes)
    for setting_id, (outcome, current) in outcomes.items():
        resource = context[setting_id]["resource"]
        if resource not in diverged_resources:
            continue
        if outcome.decision != REVERT_TO_ORIGINAL:
            continue
        adjusted[setting_id] = (
            replace(
                outcome,
                decision=DROP_RECORD,
                reason=(
                    f"another value of resource '{resource}' was changed by the user, "
                    f"so Kedra stops claiming the whole resource instead of taking "
                    f"apart a shortcut the user has made their own"
                ),
                writes=False,
                value_to_write=None,
                reset_to_default=False,
                drops_record=True,
            ),
            current,
        )
    return adjusted


def apply_conflict_gate(
    outcomes: dict[str, tuple],
    context: dict[str, dict],
    reports: dict[str, ConflictReport],
) -> dict[str, tuple]:
    """Refuse writes that would take an accelerator Kedra is not entitled to.

    Precedence, and the order is the whole design:

    1. **Identify the canonical Kedra resource.** Done by `resource` on the
       declaration and on the record, so a resource is recognised as Kedra's
       even after the module declaring it is gone.
    2. **Decide ownership.** `decide()` runs first and untouched. Whether the
       user has diverged, whether a revert is owed, whether this is a first
       apply -- all answered before an accelerator is looked at.
    3. **Gate the writes.** Only *then* is the conflict report consulted, and it
       can only ever turn a write into a refusal. It cannot create a write, it
       cannot re-open a value the user diverged, and it cannot cancel a revert.

    That ordering is what keeps the two ideas apart. A user who rebound Kedra's
    own shortcut has *diverged*; they have not created an "external conflict"
    with themselves, and step 2 has already decided to leave their value alone
    before step 3 gets a say. Conversely an unrelated shortcut holding the
    accelerator is not a divergence and must not be mistaken for one, because
    the response to divergence is to stop claiming a value Kedra wrote -- and
    Kedra never wrote that one.

    Only `apply`/`update`/`force-apply` are gated. `revert-to-original` and
    `drop-record` are how Kedra *stops* holding an accelerator, so blocking them
    would trap the shortcut: a conflict appearing after installation would make
    the Terminal module impossible to switch off cleanly.

    While a resource is blocked, **no** value of it is written -- not the name,
    not the command. The resource is in a state that needs a human decision, and
    a configurator half-updating it in the meantime would leave a shortcut whose
    fields came from two different Kedra versions.
    """
    if not reports:
        return outcomes

    adjusted = dict(outcomes)
    for setting_id, (outcome, current) in outcomes.items():
        resource = context[setting_id]["resource"]
        report = reports.get(resource) if resource else None
        if report is None or not report.blocked:
            continue
        if outcome.decision not in _WRITING_DECISIONS:
            continue
        adjusted[setting_id] = (
            replace(
                outcome,
                decision=BLOCKED_BY_CONFLICT,
                reason=f"not written -- {report.block_reason}",
                writes=False,
                value_to_write=None,
                reset_to_default=False,
                records_ownership=False,
                drops_record=False,
                requires_confirmation=False,
            ),
            current,
        )
    return adjusted


def shortcut_conflict_reports(
    tree, resolution, conflicts: dict[str, ConflictReport] | None
) -> dict[str, ConflictReport]:
    """One report per shortcut resource this profile wants, filling the gaps.

    A resource the caller supplied no report for gets `ConflictReport.unchecked`,
    which blocks. That default is the point: an observed-value fixture that omits
    a target means "unset", but a *missing conflict report* cannot mean "no
    conflict" -- nobody looked, and registering on that basis is the guess this
    whole module exists to refuse. A caller that has genuinely checked passes
    `ConflictReport.clear(...)`, and a live caller gets one from
    `conflicts.report_for_shortcut`, which has no way to return "clear" without
    having looked.
    """
    from .shortcuts import module_shortcuts

    supplied = dict(conflicts or {})
    reports: dict[str, ConflictReport] = {}
    enabled = {m.id for m in resolution.modules if m.enabled}
    for module_id in sorted(enabled):
        manifest = tree.modules.get(module_id)
        if manifest is None:
            continue
        for shortcut in module_shortcuts(module_id, manifest, tree.modules):
            reports[shortcut.id] = supplied.get(
                shortcut.id,
                ConflictReport.unchecked(
                    accelerator=shortcut.binding, resource=shortcut.id
                ),
            )
    # A report for a resource this profile no longer declares is still carried,
    # so an unmanage plan can explain a conflict the user is looking at.
    for resource, report in supplied.items():
        reports.setdefault(resource, report)
    return reports


def build_plan(
    *,
    tree,
    resolution,
    user_state,
    observed: dict[str, TypedValue | None],
    operation: str = OPERATION_APPLY,
    conflicts: dict[str, ConflictReport] | None = None,
) -> ChangePlan:
    """Decide what should happen, without doing any of it.

    `observed` maps a canonical target string to the value currently there, or
    to None when the target carries no explicit value. A target missing from the
    mapping is treated as unset -- callers supply a fixture or an adapter read,
    never a guess.

    For a list-membership target the mapping holds the *whole list* as it is in
    force; `project_membership` reduces it before anything decides anything.
    """
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation {operation!r}")

    declarations = declarations_for(tree, resolution)
    records = {
        record.setting_id: record
        for record in (user_state.applied.managed_values if user_state.applied else ())
    }

    # The union: everything Kedra wants, plus everything Kedra still claims.
    # The second half is what makes disabling a module produce revert decisions
    # rather than silently abandoning values.
    setting_ids = sorted(set(declarations) | set(records))

    outcomes: dict[str, tuple[Outcome, TypedValue | None]] = {}
    context: dict[str, dict] = {}

    for setting_id in setting_ids:
        declaration = declarations.get(setting_id)
        record = records.get(setting_id)
        target = declaration.canonical_target if declaration else record.target

        # A declaration is authoritative while it exists; a record carries the
        # facts forward once the module that declared it is gone.
        member = declaration.member if declaration else record.member
        resource = declaration.resource if declaration else record.resource
        context[setting_id] = {"member": member, "resource": resource}

        raw = observed.get(target)
        current = project_membership(member, raw) if member else raw

        effective = operation
        if operation == OPERATION_APPLY and declaration is None:
            # Still claimed, no longer wanted: withdraw it.
            effective = OPERATION_UNMANAGE

        outcomes[setting_id] = (
            decide(
                operation=effective,
                desired=declaration,
                record=record,
                observed=current,
            ),
            current,
        )

    outcomes = resource_cohesion(outcomes, context)

    # Ownership has finished deciding. Only now is the rest of the system
    # consulted, and only to refuse -- see `apply_conflict_gate`.
    reports = shortcut_conflict_reports(tree, resolution, conflicts)
    outcomes = apply_conflict_gate(outcomes, context, reports)

    entries = [
        _entry(
            setting_id,
            declarations.get(setting_id).canonical_target
            if declarations.get(setting_id)
            else records[setting_id].target,
            declarations.get(setting_id),
            records.get(setting_id),
            outcomes[setting_id][1],
            outcomes[setting_id][0],
            context[setting_id],
        )
        for setting_id in setting_ids
    ]

    return ChangePlan(
        operation=operation,
        generation=user_state.next_generation(),
        entries=tuple(sorted(entries, key=lambda e: (e.phase, e.setting_id))),
        conflicts=reports,
    )


def _phase(member: str | None, decision: str) -> int:
    if member is None:
        return PHASE_VALUE
    return PHASE_DEREGISTER if decision in _REMOVING_DECISIONS else PHASE_REGISTER


def _entry(
    setting_id: str,
    target: str,
    declaration: SettingDeclaration | None,
    record,
    current: TypedValue | None,
    outcome: Outcome,
    context: dict,
) -> PlannedChange:
    backend = declaration.backend if declaration else record.backend
    original = record.original if (record and record.original_present) else None
    member = context["member"]
    return PlannedChange(
        setting_id=setting_id,
        backend=backend,
        target=target,
        module_id=declaration.module_id if declaration else (record.module_id if record else None),
        module_version=(
            declaration.module_version
            if declaration
            else (record.module_version if record else None)
        ),
        policy=declaration.policy if declaration else (record.policy if record else None),
        decision=outcome.decision,
        reason=outcome.reason,
        requires_confirmation=outcome.requires_confirmation,
        writes=outcome.writes,
        current_value=current,
        desired_value=declaration.value if declaration else None,
        original_value=original,
        reset_to_default=outcome.reset_to_default,
        resource=context["resource"],
        member=member,
        phase=_phase(member, outcome.decision),
        blocked_by_conflict=outcome.decision == BLOCKED_BY_CONFLICT,
    )


__all__ = [
    "PLAN_SCHEMA_VERSION",
    "PHASE_DEREGISTER",
    "PHASE_REGISTER",
    "PHASE_VALUE",
    "ChangePlan",
    "PlannedChange",
    "OPERATION_APPLY",
    "OPERATION_RESTORE",
    "OPERATION_UNMANAGE",
    "apply_conflict_gate",
    "build_plan",
    "declarations_for",
    "project_membership",
    "resource_cohesion",
    "shortcut_conflict_reports",
]
