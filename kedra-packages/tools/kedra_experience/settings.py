"""Declarative setting records and migration records inside a module manifest.

A setting declaration says *what Kedra wants a value to be*. It never says
whether that value is currently set, who set it, or what to do about a user who
changed it -- that is ownership state (`state`) and the decision layer
(`ownership`). Keeping the three apart is the whole point: a declaration is
distributable, immutable and identical on every machine; ownership is not.

Nothing here executes anything. A setting is data: an ID, a backend, a target,
a typed value and a policy. There is no command backend and no expression
field, so a manifest cannot ask for arbitrary code to run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import spec_for
from .values import TypedValue, ValueError_

SETTING_REQUIRED_KEYS = frozenset({"id", "backend", "policy", "target", "value"})
SETTING_KNOWN_KEYS = SETTING_REQUIRED_KEYS | {"description", "introduced_in"}

# Fields a *generated* setting carries and a hand-written one may not. Both are
# produced only by `shortcuts.expand`, and neither is in SETTING_KNOWN_KEYS, so
# writing either into a `[[module.settings]]` table is rejected as an unknown
# key. The distinction is deliberate: a composite resource is declared as a
# resource, never assembled by hand out of settings that claim to belong to one.
#
#   resource  the logical resource this setting is part of. Settings sharing one
#             are planned and unmanaged together -- see `plan.resource_cohesion`.
#   member    marks a *list-membership* claim: Kedra owns the presence of this
#             one entry in an array-typed key, and nothing else about the array.
GENERATED_SETTING_KEYS = frozenset({"resource", "member"})

# How Kedra intends a value to behave. Two policies, because two is what the
# distinction actually requires; more can be added when a real case appears.
#
# "default"  Kedra's recommended default. The user may freely change it, and a
#            later Kedra release that changes its recommendation must not
#            overwrite an explicit user choice. Where the backend supports a
#            native vendor-default mechanism (a GSettings override file), that
#            is the preferred delivery route -- it leaves the user's dconf
#            untouched, so "the user changed it" is trivially detectable and
#            nothing needs reverting.
#
# "managed"  Kedra must actively write the value, because no vendor-default
#            mechanism can express it. This is the only policy that creates a
#            managed-value record, and therefore the only one that can ever
#            require a revert.
# The two policies, named so callers branch on a constant rather than a literal.
#
#   default  delivered as a package-owned GSettings vendor override. Never
#            written into any user's dconf, never owned, never reverted.
#   managed  written by Kedra into the user's own configuration, with an
#            ownership record and the full three-value model behind it.
POLICY_DEFAULT = "default"
POLICY_MANAGED = "managed"

SETTING_POLICIES = frozenset({POLICY_DEFAULT, POLICY_MANAGED})

MIGRATION_REQUIRED_KEYS = frozenset({"type", "module_version"})

# Migration record shapes. Each type declares the keys it requires beyond the
# common ones. There is deliberately no "script" or "command" type: a migration
# is a description of what changed, never a program.
MIGRATION_TYPES: dict[str, frozenset] = {
    # A setting's stable ID changed. Managed records under `from_id` should be
    # re-keyed to `to_id`; nothing is written to the backend.
    "rename-setting": frozenset({"from_id", "to_id"}),
    # A setting no longer exists. `on_managed` says what to do with records.
    "remove-setting": frozenset({"setting_id", "on_managed"}),
    # The backend target moved. The new target comes from the declaration; the
    # old one is recorded here so existing records can be matched.
    "retarget-setting": frozenset({"setting_id", "from_target"}),
    # Only the recommended value changed. Informational: normal upgrade rules
    # apply, so a user who diverged keeps their value.
    "change-default": frozenset({"setting_id"}),
}

MIGRATION_ON_MANAGED = frozenset({"revert", "drop"})


@dataclass(frozen=True)
class SettingDeclaration:
    """One setting a module wants applied."""

    id: str
    backend: str
    policy: str
    target: dict
    value: TypedValue
    module_id: str
    module_version: str
    description: str = ""
    introduced_in: str | None = None
    resource: str | None = None
    member: str | None = None

    @property
    def is_membership(self) -> bool:
        """Does this declaration claim one entry in a list rather than a value?

        A membership declaration's `value` is the single-element array holding
        `member`; it is spelled that way so the ownership layer can compare it
        against a projected observation with no special cases at all.
        """
        return self.member is not None

    @property
    def canonical_target(self) -> str:
        spec = spec_for(self.backend)
        if spec is None:
            raise ValueError_(f"no spec for backend {self.backend!r}")
        return spec.canonical_target(self.target)

    @property
    def creates_managed_record(self) -> bool:
        """Only 'managed' settings take ownership of a value."""
        return self.policy == "managed"


def setting_problems(entry: object, label: str) -> list[str]:
    """Every reason a raw setting table is invalid."""
    if not isinstance(entry, dict):
        return [f"{label}: must be a table"]

    problems: list[str] = []
    for key in sorted(entry.keys() - SETTING_KNOWN_KEYS):
        problems.append(f"{label}: unknown key '{key}'")
    for key in sorted(SETTING_REQUIRED_KEYS - entry.keys()):
        problems.append(f"{label}: missing required key '{key}'")

    setting_id = entry.get("id")
    if setting_id is not None and not _is_setting_id(setting_id):
        problems.append(f"{label}: id {setting_id!r} is not a valid setting ID")

    policy = entry.get("policy")
    if policy is not None and policy not in SETTING_POLICIES:
        problems.append(
            f"{label}: invalid policy {policy!r} (expected one of {sorted(SETTING_POLICIES)})"
        )

    backend = entry.get("backend")
    if backend is None:
        return problems
    spec = spec_for(backend)
    if spec is None:
        problems.append(
            f"{label}: unknown backend {backend!r} (expected one of {sorted(_backend_names())})"
        )
        return problems

    if "target" in entry:
        problems += [f"{label}: {p}" for p in spec.target_problems(entry["target"])]

    if "value" in entry:
        try:
            value = TypedValue.from_dict(entry["value"])
        except ValueError_ as exc:
            problems.append(f"{label}: {exc}")
        else:
            problems += [f"{label}: {p}" for p in spec.value_problems(value)]

    return problems


def build_declaration(
    entry: dict, module_id: str, module_version: str
) -> SettingDeclaration:
    """Build a declaration from an already-validated table."""
    return SettingDeclaration(
        id=entry["id"],
        backend=entry["backend"],
        policy=entry["policy"],
        target=dict(entry["target"]),
        value=TypedValue.from_dict(entry["value"]),
        module_id=module_id,
        module_version=module_version,
        description=entry.get("description", ""),
        introduced_in=entry.get("introduced_in"),
        resource=entry.get("resource"),
        member=entry.get("member"),
    )


def migration_problems(entry: object, label: str) -> list[str]:
    """Every reason a raw migration table is invalid."""
    if not isinstance(entry, dict):
        return [f"{label}: must be a table"]

    problems: list[str] = []
    kind = entry.get("type")
    if kind is None:
        problems.append(f"{label}: missing required key 'type'")
        return problems
    if kind not in MIGRATION_TYPES:
        problems.append(
            f"{label}: invalid migration type {kind!r} "
            f"(expected one of {sorted(MIGRATION_TYPES)})"
        )
        return problems

    required = MIGRATION_REQUIRED_KEYS | MIGRATION_TYPES[kind]
    known = required | {"reason"}
    for key in sorted(entry.keys() - known):
        problems.append(f"{label}: key '{key}' is not valid for migration type {kind!r}")
    for key in sorted(required - entry.keys()):
        problems.append(f"{label}: migration type {kind!r} requires '{key}'")

    for key in ("setting_id", "from_id", "to_id"):
        value = entry.get(key)
        if value is not None and not _is_setting_id(value):
            problems.append(f"{label}: {key} {value!r} is not a valid setting ID")

    on_managed = entry.get("on_managed")
    if on_managed is not None and on_managed not in MIGRATION_ON_MANAGED:
        problems.append(
            f"{label}: invalid on_managed {on_managed!r} "
            f"(expected one of {sorted(MIGRATION_ON_MANAGED)})"
        )

    from_target = entry.get("from_target")
    if from_target is not None and (not isinstance(from_target, str) or not from_target):
        problems.append(f"{label}: from_target must be a non-empty canonical target string")

    return problems


def _backend_names() -> list[str]:
    from .backends import SPECS

    return sorted(SPECS)


def _is_setting_id(value: object) -> bool:
    """Lowercase, dash-separated, starting with a letter.

    Setting IDs are unique across the whole Experience tree, not merely within
    a module: a managed-value record in a user's state refers to a setting by
    ID alone, and a setting may legitimately move between modules. Scoping IDs
    per module would make that move a breaking change to every user's state.
    """
    if not isinstance(value, str) or not value:
        return False
    if not value[0].isalpha():
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in value)
