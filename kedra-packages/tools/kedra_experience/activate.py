"""The production apply path: what runs in a user's own GNOME session.

This is the orchestration layer and nothing else. Every decision it appears to
make is made somewhere else and has been for several milestones:

    resolve      what profile is this            `resolve`
    plan         what should change, and may it  `plan` + `ownership`
    conflicts    is the accelerator free         `conflicts` + `accelerators`
    execute      carry out the authorized writes `execute`
    persist      record it truthfully            `state` + `locking`

What is genuinely new here is *observation*. Every other caller of `build_plan`
has supplied a fixture; a real session supplies a live desktop, and reading it
correctly is subtle enough to be worth its own function -- see `observe`.

WHERE THIS RUNS
---------------
Inside the user's graphical session, as the user, with a session bus. Not as
root, not in a chroot, not from a pacman hook. The managed shortcut is a
per-path instance of a relocatable schema plus membership in a list, and writing
it means writing that user's dconf through dconf-service over their session bus.
There is no correct way to do it from anywhere else, which is why installation
records the choice and this enacts it.

NO STATE, NO ACTION
-------------------
A user with no state file gets zero writes and a successful exit. That is the
Vanilla guarantee, and it is deliberately keyed on the *state file* rather than
on installed packages: a system can carry the runtime for any number of reasons,
and none of them is consent to reconfigure somebody's desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .backends import BackendError
from .conflicts import PARENT_TARGET, report_for_shortcut
from .execute import execute_plan
from .locking import StateLocked, state_lock
from .manifests import Report, SCHEMA_VERSION, load_tree
from .ownership import OPERATION_APPLY, OPERATION_UNMANAGE
from .settings import POLICY_MANAGED
from .plan import build_plan, declarations_for
from .resolve import ResolutionError, diagnose, resolve_state
from .shortcuts import module_shortcuts
from .state import StateError, UserState
from .values import TypedValue

# Outcomes an activation run can report. A closed set, so a caller -- notably a
# systemd unit's exit status -- branches on a name rather than on prose.
OUTCOME_NO_STATE = "no-state"          # nothing selected; nothing done
OUTCOME_NO_LAYER = "no-layer"          # Vanilla; nothing to apply
OUTCOME_APPLIED = "applied"            # writes happened
OUTCOME_UNCHANGED = "unchanged"        # already correct; nothing written
OUTCOME_BLOCKED = "blocked"            # a managed resource is held by someone else
OUTCOME_FAILED = "failed"              # at least one authorized write failed

ACTIVATION_SCHEMA_VERSION = 1


class ActivationError(Exception):
    """Activation could not be attempted at all."""


@dataclass
class ActivationResult:
    """What one activation run did, in terms a caller can act on."""

    outcome: str
    generation: int | None = None
    written: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    blocked: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)
    reverted: tuple[str, ...] = ()
    state_path: str | None = None
    preset_id: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Did everything Kedra was entitled to do actually happen?

        A blocked resource is NOT a failure of this run: Kedra asked whether an
        accelerator was free, was told no, and correctly did not take it. The
        desktop is in the state the rules say it should be in.
        """
        return self.outcome != OUTCOME_FAILED

    def to_dict(self) -> dict:
        return {
            "activation_schema_version": ACTIVATION_SCHEMA_VERSION,
            "outcome": self.outcome,
            "ok": self.ok,
            "generation": self.generation,
            "written": list(self.written),
            "preserved": list(self.preserved),
            "reverted": list(self.reverted),
            "blocked": self.blocked,
            "failed": self.failed,
            "state_path": self.state_path,
            "preset_id": self.preset_id,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def observed_targets(tree, resolution, user_state) -> tuple[str, ...]:
    """Every target this run needs to look at, and no others.

    The union of what Kedra wants and what Kedra still claims -- the same union
    the planner iterates -- plus the custom-shortcut parent list, which is
    needed whether or not a shortcut is declared: it is how a withdrawn
    membership is found, and how conflict discovery starts.

    Deliberately narrow. Kedra reads the values it is about to reason about and
    nothing else; there is no dconf dump here and never will be.
    """
    targets = {PARENT_TARGET}
    for declaration in declarations_for(tree, resolution).values():
        # `policy = "default"` is delivered as a package-owned vendor override
        # and is never written here, so there is nothing to decide about it and
        # no reason to look. Reading it would be harmless but dishonest: this
        # function is what "Kedra reads only what it reasons about" means.
        if declaration.policy != POLICY_MANAGED:
            continue
        targets.add(declaration.canonical_target)
    if user_state.applied:
        # Records are only ever created for managed settings, so every one of
        # these is in scope by construction.
        for record in user_state.applied.managed_values:
            targets.add(record.target)
    return tuple(sorted(targets))


def observe(adapter, tree, resolution, user_state) -> dict[str, TypedValue | None]:
    """Read the live values the planner will decide from.

    Two kinds of read, and using the wrong one is the whole difficulty:

    *Explicit* (`read`) for an ordinary setting. "Is a value stored here?" is a
    different question from "what does this read as", and the ownership layer
    depends on the difference -- a user who set a key to exactly its default
    has made a choice, and treating that as unset would silently discard it on a
    later revert.

    *Effective* (`read_effective`) for the custom-shortcut list, because Kedra
    owns one entry in it and must preserve the others exactly. What matters
    there is the list actually in force, not whether the user has stored one.

    A target the adapter cannot read at all is recorded as None -- unset -- and
    that is safe in the direction that matters: the planner then treats it as
    something Kedra has not written, which leads to `apply` for a fresh setting
    and to a conflict check for anything else.
    """
    membership_targets = {
        declaration.canonical_target
        for declaration in declarations_for(tree, resolution).values()
        if declaration.is_membership and declaration.policy == POLICY_MANAGED
    }
    if user_state.applied:
        membership_targets.update(
            record.target for record in user_state.applied.managed_values if record.member
        )

    observed: dict[str, TypedValue | None] = {}
    for target in observed_targets(tree, resolution, user_state):
        try:
            if target in membership_targets or target == PARENT_TARGET:
                observed[target] = adapter.read_effective(target)
            else:
                observed[target] = adapter.read(target)
        except BackendError:
            # A schema that is not installed, a key that does not exist. Not
            # this layer's to resolve: record it as unset and let the planner
            # and the conflict gate decide, which is where refusal belongs.
            observed[target] = None
    return observed


def conflict_reports(adapter, tree, resolution) -> dict:
    """A conflict report per shortcut the profile wants, from the live session.

    The same call the developer CLI makes against a fixture, made against a real
    adapter. `report_for_shortcut` performs no writes -- discovery is reads
    only -- and returns `unavailable` rather than "clear" when it cannot
    establish an answer, which the planner treats as blocking.
    """
    enabled = {module.id for module in resolution.modules if module.enabled}
    reports = {}
    for module_id in sorted(enabled):
        manifest = tree.modules.get(module_id)
        if manifest is None:
            continue
        for shortcut in module_shortcuts(module_id, manifest, tree.modules):
            reports[shortcut.id] = report_for_shortcut(adapter, shortcut)
    return reports


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _summarise(plan, result) -> tuple[tuple, tuple, dict, dict, tuple]:
    written, preserved, reverted = [], [], []
    failed: dict[str, str] = {}
    for change in result.changes:
        if not change.succeeded:
            failed[change.setting_id] = change.error or "write failed"
        elif change.decision in ("apply", "update", "force-apply"):
            written.append(change.setting_id)
        elif change.decision == "revert-to-original":
            reverted.append(change.setting_id)

    for entry in plan.entries:
        if entry.decision == "preserve-user-change":
            preserved.append(entry.setting_id)

    blocked = {
        resource: report.block_reason
        for resource, report in sorted(plan.conflicts.items())
        if report.blocked
    }
    return tuple(written), tuple(preserved), blocked, failed, tuple(reverted)


def activate(
    *,
    data_dir: Path,
    state_path: Path,
    adapter_factory,
    operation: str = OPERATION_APPLY,
    lock_timeout: float = 10.0,
    applied_at: str | None = None,
) -> ActivationResult:
    """Resolve, plan, apply and record -- the whole first-login transaction.

    `adapter_factory` is a callable returning a backend adapter, so a test can
    supply an isolated one and production can supply the GSettings adapter
    without this module importing GLib.

    The lock spans load -> plan -> execute -> save. Locking only the save would
    leave two logins free to plan from the same stale state and both write.
    """
    if operation not in (OPERATION_APPLY, OPERATION_UNMANAGE):
        raise ActivationError(f"unsupported operation {operation!r}")

    state_path = Path(state_path)
    if not state_path.is_file():
        # The Vanilla guarantee, and the earliest possible exit: before the
        # manifest tree is read, before an adapter exists, before anything at
        # all could touch a session.
        return ActivationResult(
            outcome=OUTCOME_NO_STATE,
            state_path=str(state_path),
            detail="no Kedra Experience state; nothing to apply",
        )

    report = Report()
    tree = load_tree(Path(data_dir), report)
    if tree is None or not report.ok:
        raise ActivationError(
            f"the manifest tree at {data_dir} is not valid:\n"
            + (report.rendered() if report.errors else "tree is structurally unusable")
        )

    try:
        with state_lock(state_path, timeout=lock_timeout):
            try:
                user_state = UserState.load(state_path)
            except StateError as exc:
                raise ActivationError(f"unreadable Experience state: {exc}") from exc

            try:
                resolution = resolve_state(tree, user_state)
            except ResolutionError as exc:
                raise ActivationError(f"the recorded selection does not resolve: {exc}") from exc

            diagnostics = diagnose(tree, resolution)
            if not diagnostics.ok:
                # A state file that resolves to an invalid profile. Refusing is
                # right: the alternative is enacting a profile the resolver says
                # is not allowed.
                problems = [r.describe() for r in diagnostics.refused_overrides]
                problems += [v.describe() for v in diagnostics.violations]
                raise ActivationError(
                    "the recorded selection is not a valid profile: " + "; ".join(problems)
                )

            if not resolution.applies_experience_layer and not (
                user_state.applied and user_state.applied.managed_values
            ):
                # Vanilla, and Kedra has never owned anything here. Nothing to
                # apply and nothing to give back.
                return ActivationResult(
                    outcome=OUTCOME_NO_LAYER,
                    state_path=str(state_path),
                    preset_id=resolution.preset_id,
                    detail="the selected profile applies no Experience layer",
                )

            adapter = adapter_factory()
            observed = observe(adapter, tree, resolution, user_state)
            reports = conflict_reports(adapter, tree, resolution)

            plan = build_plan(
                tree=tree,
                resolution=resolution,
                user_state=user_state,
                observed=observed,
                operation=operation,
                conflicts=reports,
            )

            result = execute_plan(
                plan=plan,
                adapters={"gsettings": adapter},
                tree=tree,
                resolution=resolution,
                user_state=user_state,
                experience_schema_version=SCHEMA_VERSION,
                applied_at=applied_at,
            )

            if result.committed:
                result.state.save(state_path)
    except StateLocked as exc:
        raise ActivationError(str(exc)) from exc

    written, preserved, blocked, failed, reverted = _summarise(plan, result)

    if failed:
        outcome = OUTCOME_FAILED
    elif blocked and not written:
        outcome = OUTCOME_BLOCKED
    elif written or reverted:
        outcome = OUTCOME_APPLIED
    else:
        outcome = OUTCOME_UNCHANGED

    return ActivationResult(
        outcome=outcome,
        # Reported only when something was actually committed. A run that wrote
        # nothing did not consume a generation, and saying otherwise would make
        # "has anything changed since?" unanswerable.
        generation=result.generation if result.committed else None,
        written=written,
        preserved=preserved,
        blocked=blocked,
        failed=failed,
        reverted=reverted,
        state_path=str(state_path),
        preset_id=resolution.preset_id,
    )


__all__ = [
    "ACTIVATION_SCHEMA_VERSION",
    "OUTCOME_APPLIED",
    "OUTCOME_BLOCKED",
    "OUTCOME_FAILED",
    "OUTCOME_NO_LAYER",
    "OUTCOME_NO_STATE",
    "OUTCOME_UNCHANGED",
    "ActivationError",
    "ActivationResult",
    "activate",
    "conflict_reports",
    "observe",
    "observed_targets",
]
