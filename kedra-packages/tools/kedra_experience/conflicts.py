"""Discovering GNOME custom shortcuts, and deciding whether Kedra's clashes.

The problem this exists for is concrete. GNOME lets any number of custom
shortcuts be registered, each at its own dconf path, and nothing stops two of
them naming the same key combination. When that happens the winner is decided by
whichever grab gets registered first -- which is not a behaviour to ship. So
before Kedra registers `<Super>Return`, it has to know whether somebody else
already owns it.

The policy, in one line:

    Kedra never takes an accelerator that an unrelated shortcut already holds.

Not "takes it and warns", not "takes it if the other one looks unimportant".
The user's shortcut is preserved, unmodified, unremoved and unrebound, and
Kedra's own registration is blocked instead. Freeing the accelerator is the
user's decision to make, through a UI that does not exist yet; it is not
something an unattended `pacman -Syu` gets to do on their behalf.

Read-only, absolutely
---------------------
Discovery reads the parent list and three keys per registered path. It writes
nothing, it does not tidy the list, and in particular:

* a **stale** path -- registered but never configured -- is reported and left
  alone. It is not evidence of a bug; a user may be mid-way through creating a
  shortcut, and removing it would destroy work in progress.
* a **duplicate** entry in the list is reported and left alone. Kedra owns the
  membership of exactly one path, and "while I was here I fixed your list" is
  the behaviour this whole architecture is built to avoid.
* an **unreadable** path is reported as *unknown*, never as harmless.

There is no `dconf dump` and no scan of anything outside the one list. Kedra
learns three strings about each shortcut the user has already told GNOME about,
and nothing else about the user's machine.
"""

from __future__ import annotations

from dataclasses import dataclass

from .accelerators import (
    Accelerator,
    AcceleratorError,
    AcceleratorUnavailable,
    parse,
    unavailable_reason,
)
from .accelerators import available as accelerators_available
from .backends import BackendError
from .shortcuts import CHILD_SCHEMA, PARENT_KEY, PARENT_SCHEMA

# What is known about one shortcut's binding. Four states, because collapsing
# any two of them loses something a decision depends on.
BINDING_BOUND = "bound"              # parsed; comparable
BINDING_UNBOUND = "unbound"          # empty string: GNOME's "no key assigned"
BINDING_UNPARSEABLE = "unparseable"  # text GTK rejects, so GNOME cannot grab it
BINDING_UNKNOWN = "unknown"          # could not be read at all

# Conflict classifications.
CONFLICT_SELF = "self"
CONFLICT_EXTERNAL = "external"
CONFLICT_UNDETERMINED = "undetermined"
CONFLICT_MALFORMED = "malformed"

CONFLICT_TYPES = frozenset(
    {CONFLICT_SELF, CONFLICT_EXTERNAL, CONFLICT_UNDETERMINED, CONFLICT_MALFORMED}
)

PARENT_TARGET = f"gsettings:{PARENT_SCHEMA}:{PARENT_KEY}"


def child_target(path: str, key: str) -> str:
    """The canonical target of one key of the shortcut instance at `path`."""
    return f"gsettings:{CHILD_SCHEMA}:{path}:{key}"


@dataclass(frozen=True)
class CustomShortcut:
    """One registered GNOME custom shortcut, as read.

    `problems` is non-empty when something could not be read. The object still
    exists in that case, because a shortcut Kedra could not fully read is a
    thing it must reason about -- not an exception that takes the planner down.
    """

    path: str
    name: str | None = None
    binding: str | None = None
    command: str | None = None
    problems: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        return not self.problems

    @property
    def binding_state(self) -> str:
        if self.binding is None:
            return BINDING_UNKNOWN
        if not self.binding.strip():
            return BINDING_UNBOUND
        try:
            return BINDING_BOUND if parse(self.binding) else BINDING_UNBOUND
        except AcceleratorError:
            return BINDING_UNPARSEABLE
        except AcceleratorUnavailable:
            return BINDING_UNKNOWN

    @property
    def configured(self) -> bool:
        """Has anything been set here at all?

        A registered path where name, binding and command are all empty is a
        *stale* entry: GNOME shows it as a blank row and it grabs nothing. It is
        reported and then left exactly where it is -- a user may be part-way
        through creating a shortcut, and tidying it away would destroy that.
        """
        return any(
            (value or "").strip() for value in (self.name, self.binding, self.command)
        )

    def accelerator(self) -> Accelerator | None:
        """The parsed binding, or None when unbound. Raises like `parse`."""
        return parse(self.binding or "")

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "binding": self.binding,
            "command": self.command,
            "binding_state": self.binding_state,
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class Discovery:
    """Every custom shortcut the parent list points at, exactly as found."""

    # The list verbatim: original order, duplicates included. Kept raw so a test
    # can prove nothing was normalised on the way through.
    registered_paths: tuple[str, ...] = ()
    shortcuts: tuple[CustomShortcut, ...] = ()
    # Reasons the *list itself* could not be read, if any.
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def by_path(self, path: str) -> CustomShortcut | None:
        for shortcut in self.shortcuts:
            if shortcut.path == path:
                return shortcut
        return None

    @property
    def duplicate_paths(self) -> tuple[str, ...]:
        """Paths the user's list names more than once. Reported, never fixed."""
        seen: set[str] = set()
        repeated: list[str] = []
        for path in self.registered_paths:
            if path in seen and path not in repeated:
                repeated.append(path)
            seen.add(path)
        return tuple(repeated)

    def to_dict(self) -> dict:
        return {
            "registered_paths": list(self.registered_paths),
            "duplicate_paths": list(self.duplicate_paths),
            "shortcuts": [s.to_dict() for s in self.shortcuts],
            "problems": list(self.problems),
        }


def discover_custom_shortcuts(adapter, *, keys=("name", "binding", "command")) -> Discovery:
    """Read every registered custom shortcut. Performs no writes.

    Failures are contained per path. A schema that will not open, a key that is
    missing, a value of the wrong type -- each becomes a `problems` entry on one
    `CustomShortcut`, and the other shortcuts are still read. One broken entry in
    a user's dconf must not be able to stop Kedra planning anything at all.
    """
    try:
        raw = adapter.read_effective(PARENT_TARGET)
    except BackendError as exc:
        return Discovery(problems=(f"cannot read {PARENT_KEY}: {exc}",))

    if raw is None:
        return Discovery()
    if raw.type != "as":
        return Discovery(
            problems=(f"{PARENT_KEY} has type {raw.type!r}, expected 'as'",)
        )

    paths = tuple(str(path) for path in raw.data)

    shortcuts: list[CustomShortcut] = []
    # One entry per *distinct* path: a duplicate in the list is the same
    # shortcut named twice, not two shortcuts. The duplication itself is
    # preserved in `registered_paths` and surfaced by `duplicate_paths`.
    for path in dict.fromkeys(paths):
        shortcuts.append(_read_one(adapter, path, keys))

    return Discovery(registered_paths=paths, shortcuts=tuple(shortcuts))


def _read_one(adapter, path: str, keys) -> CustomShortcut:
    values: dict[str, str | None] = {}
    problems: list[str] = []

    for key in keys:
        try:
            value = adapter.read_effective(child_target(path, key))
        except BackendError as exc:
            values[key] = None
            problems.append(f"{key}: {exc}")
            continue
        if value is None:
            # No value at all, which for a schema-backed key means the path
            # could not be resolved to a real instance.
            values[key] = None
            problems.append(f"{key}: no value")
        elif value.type != "s":
            values[key] = None
            problems.append(f"{key}: type {value.type!r}, expected 's'")
        else:
            values[key] = value.data

    return CustomShortcut(
        path=path,
        name=values.get("name"),
        binding=values.get("binding"),
        command=values.get("command"),
        problems=tuple(problems),
    )


@dataclass(frozen=True)
class Conflict:
    """One finding about a registered shortcut and Kedra's desired accelerator.

    `blocking` is the field a planner keys off. Everything else is for a human
    or a future UI: enough to say *which* shortcut is in the way and what it
    does, and nothing about the user's machine beyond that.
    """

    type: str
    path: str
    reason: str
    blocking: bool = False
    name: str = ""
    binding: str = ""
    command: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "path": self.path,
            "reason": self.reason,
            "blocking": self.blocking,
            "name": self.name,
            "binding": self.binding,
            "command": self.command,
        }


@dataclass(frozen=True)
class ConflictReport:
    """What is known about one accelerator Kedra wants.

    Three ways to build one, and the difference between them is the whole point:

    `checked`      the accelerator was compared against every registered
                   shortcut. `blocked` is then a real answer.
    `unavailable`  no authoritative parser, or the list could not be read. The
                   answer is *unknown*, and unknown blocks.
    `unchecked`    nobody looked. Also blocks.

    There is deliberately no constructor that means "assume it is free".
    """

    accelerator: str = ""
    resource: str = ""
    checked: bool = False
    findings: tuple[Conflict, ...] = ()
    unavailable_because: str = ""

    @classmethod
    def clear(cls, accelerator: str = "", resource: str = "") -> "ConflictReport":
        """Checked, and nothing else holds the accelerator."""
        return cls(accelerator=accelerator, resource=resource, checked=True)

    @classmethod
    def unchecked(cls, accelerator: str = "", resource: str = "") -> "ConflictReport":
        return cls(
            accelerator=accelerator,
            resource=resource,
            checked=False,
            unavailable_because="no conflict check was performed",
        )

    @classmethod
    def unavailable(
        cls, reason: str, accelerator: str = "", resource: str = ""
    ) -> "ConflictReport":
        return cls(
            accelerator=accelerator,
            resource=resource,
            checked=False,
            unavailable_because=reason,
        )

    @property
    def blocking(self) -> tuple[Conflict, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def advisories(self) -> tuple[Conflict, ...]:
        return tuple(f for f in self.findings if not f.blocking)

    @property
    def blocked(self) -> bool:
        """May Kedra register this accelerator?

        False only when the check actually ran and found nothing in the way.
        An unchecked or unavailable report blocks, because "could not check" is
        not "no conflict" -- it is the state in which registering would be a
        guess, and the cost of guessing wrong is a keyboard shortcut that does
        one of two things depending on grab order.
        """
        if not self.checked:
            return True
        return bool(self.blocking)

    @property
    def block_reason(self) -> str:
        if not self.blocked:
            return ""
        if not self.checked:
            return self.unavailable_because or "conflict status is unknown"
        first = self.blocking[0]
        return first.reason

    def to_dict(self) -> dict:
        return {
            "accelerator": self.accelerator,
            "resource": self.resource,
            "checked": self.checked,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "unavailable_because": self.unavailable_because,
            "findings": [f.to_dict() for f in self.findings],
        }


def classify(
    *,
    desired_binding: str,
    owned_path: str,
    discovery: Discovery,
    resource: str = "",
) -> ConflictReport:
    """Compare Kedra's desired accelerator against what is already registered.

    `owned_path` is the canonical Kedra path, and it is excluded from external
    conflict by identity rather than by heuristic: a shortcut at that path *is*
    Kedra's, whether Kedra wrote it a minute ago or a release ago, so finding
    Kedra's own accelerator there is confirmation and not a clash. Deciding
    whether that resource may be written is ownership's job, not this one's --
    see the precedence note in `plan.apply_conflict_gate`.

    Findings are sorted by path, so two runs over the same system produce the
    same report and a preview can be diffed.
    """
    if not discovery.ok:
        return ConflictReport.unavailable(
            "; ".join(discovery.problems),
            accelerator=desired_binding,
            resource=resource,
        )

    if not accelerators_available():
        return ConflictReport.unavailable(
            f"no authoritative accelerator parser: {unavailable_reason()}",
            accelerator=desired_binding,
            resource=resource,
        )

    try:
        wanted = parse(desired_binding)
    except AcceleratorError as exc:
        # Kedra's own declaration is wrong. Blocking rather than ignoring,
        # because a shortcut GNOME cannot parse is one that silently does
        # nothing once registered.
        return ConflictReport.unavailable(
            f"Kedra's declared binding is not a valid accelerator: {exc}",
            accelerator=desired_binding,
            resource=resource,
        )
    except AcceleratorUnavailable as exc:  # pragma: no cover - guarded above
        return ConflictReport.unavailable(
            str(exc), accelerator=desired_binding, resource=resource
        )

    if wanted is None:
        # Kedra wants no binding, so nothing can be in the way.
        return ConflictReport.clear(accelerator=desired_binding, resource=resource)

    findings: list[Conflict] = []
    for shortcut in sorted(discovery.shortcuts, key=lambda s: s.path):
        finding = _classify_one(shortcut, wanted, owned_path)
        if finding is not None:
            findings.append(finding)

    return ConflictReport(
        accelerator=wanted.canonical,
        resource=resource,
        checked=True,
        findings=tuple(findings),
    )


def _classify_one(
    shortcut: CustomShortcut, wanted: Accelerator, owned_path: str
) -> Conflict | None:
    is_self = shortcut.path == owned_path
    state = shortcut.binding_state

    if state == BINDING_UNKNOWN:
        if is_self:
            # Kedra's own path being unreadable is an ownership problem, not an
            # accelerator conflict; the planner will find out when it reads the
            # value it manages.
            return None
        # An unrelated shortcut whose binding cannot be read might be holding
        # the accelerator. Reported as undetermined and treated as blocking:
        # the alternative is registering on the assumption that it is not, and
        # that assumption is exactly what this module refuses to make.
        return Conflict(
            type=CONFLICT_UNDETERMINED,
            path=shortcut.path,
            blocking=True,
            reason=(
                f"the shortcut at {shortcut.path} could not be read "
                f"({'; '.join(shortcut.problems) or 'unknown reason'}), so whether it "
                f"already uses {wanted.canonical} cannot be established"
            ),
            name=shortcut.name or "",
            binding=shortcut.binding or "",
            command=shortcut.command or "",
        )

    if state == BINDING_UNBOUND:
        return None

    if state == BINDING_UNPARSEABLE:
        # GNOME cannot grab a combination it cannot parse either, so this is not
        # a conflict -- but it is worth saying out loud, because the user
        # probably meant it to be one.
        return Conflict(
            type=CONFLICT_MALFORMED,
            path=shortcut.path,
            blocking=False,
            reason=(
                f"the shortcut at {shortcut.path} has a binding GTK cannot parse "
                f"({shortcut.binding!r}); GNOME cannot bind it either, so it does not "
                f"conflict with {wanted.canonical}"
            ),
            name=shortcut.name or "",
            binding=shortcut.binding or "",
            command=shortcut.command or "",
        )

    try:
        held = shortcut.accelerator()
    except (AcceleratorError, AcceleratorUnavailable):  # pragma: no cover
        return None
    if held is None or held != wanted:
        return None

    if is_self:
        return Conflict(
            type=CONFLICT_SELF,
            path=shortcut.path,
            blocking=False,
            reason=(
                f"{wanted.canonical} is held by Kedra's own shortcut at "
                f"{shortcut.path}; that is ownership, not a conflict"
            ),
            name=shortcut.name or "",
            binding=shortcut.binding or "",
            command=shortcut.command or "",
        )

    return Conflict(
        type=CONFLICT_EXTERNAL,
        path=shortcut.path,
        blocking=True,
        reason=(
            f"{wanted.canonical} is already used by the custom shortcut "
            f"{shortcut.name or '(unnamed)'!r} at {shortcut.path}; Kedra will not take "
            f"an accelerator another shortcut holds, and will not alter that shortcut "
            f"to free it"
        ),
        name=shortcut.name or "",
        binding=shortcut.binding or "",
        command=shortcut.command or "",
    )


def report_for_shortcut(adapter, shortcut) -> ConflictReport:
    """Discover, then classify, for one declared Kedra shortcut.

    The seam a live caller uses, and the only one. It cannot return "no
    conflict" without having checked: every failure path -- an unreadable list,
    a missing parser, an invalid declared binding -- produces an `unavailable`
    report, which blocks.
    """
    discovery = discover_custom_shortcuts(adapter)
    return classify(
        desired_binding=shortcut.binding,
        owned_path=shortcut.path,
        discovery=discovery,
        resource=shortcut.id,
    )


__all__ = [
    "BINDING_BOUND",
    "BINDING_UNBOUND",
    "BINDING_UNKNOWN",
    "BINDING_UNPARSEABLE",
    "CONFLICT_EXTERNAL",
    "CONFLICT_MALFORMED",
    "CONFLICT_SELF",
    "CONFLICT_TYPES",
    "CONFLICT_UNDETERMINED",
    "PARENT_TARGET",
    "Conflict",
    "ConflictReport",
    "CustomShortcut",
    "Discovery",
    "child_target",
    "classify",
    "discover_custom_shortcuts",
    "report_for_shortcut",
]
