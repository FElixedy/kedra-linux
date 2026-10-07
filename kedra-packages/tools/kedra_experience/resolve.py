"""Preset inheritance and resolution.

Turns a preset (or a user's selection state) into an *effective* module set.

This module knows nothing about GNOME. It resolves identifiers and booleans;
what an enabled module eventually does to a desktop is a separate layer's
problem, and keeping that separation is the point of doing resolution here.

Determinism is a hard requirement: the same inputs must always produce
byte-identical output, because the resolved document is what a future installer
and configurator will diff against to decide whether anything needs doing.
Modules are therefore always emitted sorted by ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Iterable

from .manifests import Tree, is_identifier

# Version of the resolved-output document, independent of the manifest schema.
# Consumers pin this, not schema_version.
#
# 2  `refused_overrides` added, and with it a second way for a profile to be
#    invalid. The field is additive, but the meaning of `Diagnostics.ok` is not:
#    a selection that overrides a non-user-selectable module used to resolve
#    silently and now reports invalid, so a consumer pinned to 1 would call a
#    rejected selection acceptable.
RESOLUTION_SCHEMA_VERSION = 2


class ResolutionError(Exception):
    """Raised when a preset or selection cannot be resolved.

    Resolution failures are hard errors rather than collected reports: there is
    no partially-resolved preset worth handing to a caller.
    """


@dataclass(frozen=True)
class ResolvedModule:
    """One module's effective state, with the layer that decided it."""

    id: str
    enabled: bool
    version: str
    tier: str
    user_selectable: bool
    # Which layer set the final value, e.g. "preset:kedra" or "override:custom".
    # Kept so a future UI can show "you changed this" without re-deriving it.
    source: str


# Why a caller-supplied override was not applied. One code today; a closed set,
# like ADVISORY_CODES, so a consumer branches on the code and never on wording.
REFUSAL_NOT_USER_SELECTABLE = "module-not-user-selectable"

REFUSAL_CODES = frozenset({REFUSAL_NOT_USER_SELECTABLE})


@dataclass(frozen=True)
class RefusedOverride:
    """A caller-supplied module override the resolver declined to apply.

    Carries what was asked for *and* what the module actually resolved to, so a
    caller can see that the two differ without re-deriving anything. The
    override is not applied at all -- see `_build` for why refusing beats
    failing outright.
    """

    code: str
    module_id: str
    requested_enabled: bool
    effective_enabled: bool

    def describe(self) -> str:
        """Default rendering. A UI is free to ignore it and use the fields."""
        wanted = "enabled" if self.requested_enabled else "disabled"
        actual = "enabled" if self.effective_enabled else "disabled"
        return (
            f"module '{self.module_id}' is not user-selectable, so the request to "
            f"have it {wanted} was refused; it remains {actual}"
        )


@dataclass(frozen=True)
class Resolution:
    """The effective experience for one selection. Machine-readable output."""

    applies_experience_layer: bool
    preset_id: str
    preset_version: str
    # Leaf-to-base inheritance chain, e.g. ["custom", "kedra"].
    chain: tuple[str, ...]
    modules: tuple[ResolvedModule, ...]
    # Caller-supplied overrides that were declined. Empty for every preset
    # resolution, because a preset supplies no caller overrides.
    refused_overrides: tuple[RefusedOverride, ...] = ()

    @property
    def enabled_module_ids(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.modules if m.enabled)

    def to_dict(self) -> dict:
        return {
            "resolution_schema_version": RESOLUTION_SCHEMA_VERSION,
            "preset_id": self.preset_id,
            "preset_version": self.preset_version,
            "applies_experience_layer": self.applies_experience_layer,
            "chain": list(self.chain),
            "modules": [asdict(m) for m in self.modules],
            "refused_overrides": [asdict(r) for r in self.refused_overrides],
        }

    def to_json(self) -> str:
        """Stable JSON: sorted keys, modules already sorted by ID."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _chain(tree: Tree, leaf_id: str) -> list[str]:
    """Walk leaf -> base, rejecting dangling references and cycles."""
    if leaf_id not in tree.presets:
        raise ResolutionError(f"unknown preset '{leaf_id}'")

    chain: list[str] = []
    seen: set[str] = set()
    current = leaf_id

    while True:
        if current in seen:
            cycle = " -> ".join(chain[chain.index(current):] + [current])
            raise ResolutionError(f"preset inheritance cycle: {cycle}")
        seen.add(current)
        chain.append(current)

        preset = tree.presets[current]
        if preset.get("kind") != "derived":
            return chain

        base_id = preset.get("base_preset")
        if not base_id:
            raise ResolutionError(f"derived preset '{current}' has no base_preset")
        if base_id not in tree.presets:
            raise ResolutionError(
                f"preset '{current}' inherits from nonexistent preset '{base_id}'"
            )
        current = base_id


def _check_layer_consistency(tree: Tree, chain: Iterable[str]) -> bool:
    """Every preset in the chain must agree on applies_experience_layer.

    A derived preset cannot switch the layer on or off. Deriving from Vanilla
    and enabling the layer is not a custom Vanilla -- it is Kedra, and must be
    expressed by deriving from Kedra instead. Allowing the flip would mean
    inheriting an empty module set and then filling it in, which is the
    apply-then-revert shape the architecture exists to prevent.
    """
    chain = list(chain)
    base_id = chain[-1]
    base_applies = tree.presets[base_id].get("applies_experience_layer")
    if not isinstance(base_applies, bool):
        raise ResolutionError(
            f"preset '{base_id}' has no valid applies_experience_layer"
        )

    for preset_id in chain[:-1]:
        applies = tree.presets[preset_id].get("applies_experience_layer")
        if applies is not None and applies != base_applies:
            raise ResolutionError(
                f"preset '{preset_id}' declares applies_experience_layer={applies} but "
                f"inherits from '{base_id}' which declares {base_applies}; a derived "
                f"preset may not change whether the Experience layer applies"
            )
    return base_applies


def _validated_overrides(
    tree: Tree, entries: object, origin: str
) -> list[tuple[str, bool]]:
    """Normalise and check one layer's override entries."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ResolutionError(f"{origin}: module_overrides must be an array of tables")

    result: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"{origin}: module_overrides[{index}]"
        if not isinstance(entry, dict):
            raise ResolutionError(f"{where} must be a table")

        module_id = entry.get("id")
        if not is_identifier(module_id):
            raise ResolutionError(f"{where} has a missing or invalid module 'id'")
        if module_id not in tree.modules:
            raise ResolutionError(f"{where} overrides nonexistent module '{module_id}'")
        if module_id in seen:
            raise ResolutionError(f"{where} overrides module '{module_id}' twice")
        seen.add(module_id)

        enabled = entry.get("enabled")
        if not isinstance(enabled, bool):
            raise ResolutionError(f"{where} must set 'enabled' to a boolean")

        for key in entry.keys() - {"id", "enabled", "presentation"}:
            raise ResolutionError(f"{where} has unknown key '{key}'")

        result.append((module_id, enabled))
    return result


def _build(
    tree: Tree,
    chain: list[str],
    applies: bool,
    extra_overrides: list[tuple[str, bool]],
    extra_origin: str,
    leaf_id: str,
    leaf_version: str,
) -> Resolution:
    """Fold the chain base-first, then apply any caller-supplied overrides."""
    # Vanilla short-circuit. The layer is not applied, so no module is
    # consulted at all and the resolved set is empty by construction -- not by
    # disabling each module in turn.
    if not applies:
        if extra_overrides:
            raise ResolutionError(
                f"{extra_origin}: cannot override modules when the resolved preset does "
                f"not apply the Experience layer"
            )
        for preset_id in chain:
            if tree.presets[preset_id].get("module_overrides"):
                raise ResolutionError(
                    f"preset '{preset_id}' overrides modules but the Experience layer "
                    f"does not apply"
                )
        return Resolution(
            applies_experience_layer=False,
            preset_id=leaf_id,
            preset_version=leaf_version,
            chain=tuple(chain),
            modules=(),
        )

    base_id = chain[-1]
    state: dict[str, tuple[bool, str]] = {}

    for entry in tree.presets[base_id].get("modules", []):
        if not isinstance(entry, dict):
            raise ResolutionError(f"preset '{base_id}': modules entries must be tables")
        module_id = entry.get("id")
        enabled = entry.get("enabled")
        if not is_identifier(module_id) or not isinstance(enabled, bool):
            raise ResolutionError(
                f"preset '{base_id}': malformed module entry {entry!r}"
            )
        if module_id not in tree.modules:
            raise ResolutionError(
                f"preset '{base_id}' references nonexistent module '{module_id}'"
            )
        state[module_id] = (enabled, f"preset:{base_id}")

    # Base-most derived layer first, so a leaf override always wins.
    for preset_id in reversed(chain[:-1]):
        overrides = _validated_overrides(
            tree, tree.presets[preset_id].get("module_overrides"), f"preset '{preset_id}'"
        )
        for module_id, enabled in overrides:
            if module_id not in state:
                raise ResolutionError(
                    f"preset '{preset_id}' overrides module '{module_id}', which its base "
                    f"preset '{base_id}' does not declare"
                )
            state[module_id] = (enabled, f"override:{preset_id}")

    # Caller-supplied overrides -- a user's selection, never a shipped preset.
    #
    # This is the ONE layer where `user_selectable` is enforced. The preset loop
    # above deliberately does not check it: a preset is distributed Kedra data
    # that defines what a product *is*, and `kedra.toml` enabling `integration`
    # is exactly that machinery working. A selection is untrusted input from
    # whatever built it, and `user_selectable = false` is the manifest's
    # statement that the module is not the user's to switch.
    #
    # Refused rather than fatal, and the difference matters for safety. Raising
    # would leave a caller that ignores the exception -- or reads only
    # `packages.required` from a document it never got -- with nothing. Refusing
    # produces a complete, correct document in which the override simply did not
    # happen, so even a consumer that ignores the exit status and the
    # diagnostics gets a package set that still contains kedra-experience and
    # kedra-experience-runtime. Protection must not depend on the caller
    # noticing, which is the whole point of moving this out of the UI.
    refused: list[RefusedOverride] = []
    for module_id, enabled in extra_overrides:
        if module_id not in state:
            raise ResolutionError(
                f"{extra_origin}: overrides module '{module_id}', which preset "
                f"'{base_id}' does not declare"
            )
        if not tree.modules[module_id]["user_selectable"]:
            effective, _ = state[module_id]
            refused.append(
                RefusedOverride(
                    code=REFUSAL_NOT_USER_SELECTABLE,
                    module_id=module_id,
                    requested_enabled=enabled,
                    effective_enabled=effective,
                )
            )
            continue
        state[module_id] = (enabled, extra_origin)

    modules = tuple(
        ResolvedModule(
            id=module_id,
            enabled=enabled,
            version=tree.modules[module_id]["version"],
            tier=tree.modules[module_id]["tier"],
            user_selectable=tree.modules[module_id]["user_selectable"],
            source=source,
        )
        # Deterministic ordering, independent of manifest or preset order.
        for module_id, (enabled, source) in sorted(state.items())
    )

    return Resolution(
        applies_experience_layer=True,
        preset_id=leaf_id,
        preset_version=leaf_version,
        chain=tuple(chain),
        modules=modules,
        refused_overrides=tuple(
            sorted(refused, key=lambda r: (r.module_id, r.code))
        ),
    )


# Stable, machine-readable advisory codes. A consumer keys off these, never off
# the rendered sentence -- so wording can be changed, translated or replaced
# without breaking anything that reacts to the condition.
ADVISORY_WORKS_BEST_WITH_DISABLED = "works-best-with-disabled"

ADVISORY_CODES = frozenset({ADVISORY_WORKS_BEST_WITH_DISABLED})


@dataclass(frozen=True)
class RequirementViolation:
    """An enabled module whose hard `requires` is not satisfied.

    This is an error: the module declared it cannot function correctly without
    the other one. It is never emitted for an advisory relationship.
    """

    module_id: str
    requires: str

    def describe(self) -> str:
        """Default rendering. A UI is free to ignore it and use the fields."""
        return (
            f"module '{self.module_id}' requires '{self.requires}', "
            f"which is disabled"
        )


@dataclass(frozen=True)
class Advisory:
    """A valid profile that departs from how Kedra intends modules to be used.

    Carries structure, not prose: `code` is what a consumer branches on, the two
    module IDs say who and what, and `reason` is the module author's statement
    of the consequence. The rendered sentence is a convenience, not the contract.
    """

    code: str
    module_id: str
    related_module_id: str
    reason: str = ""

    def describe(self) -> str:
        """Default rendering. A UI is free to ignore it and use the fields."""
        sentence = (
            f"module '{self.module_id}' works best with '{self.related_module_id}', "
            f"which is disabled"
        )
        return f"{sentence}; {self.reason}" if self.reason else sentence


@dataclass(frozen=True)
class Diagnostics:
    """Everything worth saying about a resolved profile, split by severity.

    The split is the point. Before it existed, `requires` carried both meanings
    and a supported customisation -- switching off `desktop` -- reported four
    "unsatisfied requirements" and read as a broken profile.
    """

    violations: tuple[RequirementViolation, ...] = ()
    advisories: tuple[Advisory, ...] = ()
    # Overrides the caller asked for that the resolver declined. A separate
    # array rather than an entry in `violations`, because the two describe
    # different things and share no fields: a violation is about the resolved
    # profile, a refusal is about the input that produced it. Merging them would
    # make `violations` a list of irregular objects, which is exactly what a
    # machine-readable contract must not be.
    refused_overrides: tuple[RefusedOverride, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the profile is valid. Advisories do not make it invalid.

        A refused override does. The resolved profile itself is coherent -- the
        override was simply not applied -- but the caller asked for something it
        was not allowed to have, and reporting that as `ok` would be the silent
        acceptance this field exists to prevent.
        """
        return not self.violations and not self.refused_overrides

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": [
                {"module": v.module_id, "requires": v.requires, "message": v.describe()}
                for v in self.violations
            ],
            "refused_overrides": [
                {
                    "code": r.code,
                    "module": r.module_id,
                    "requested_enabled": r.requested_enabled,
                    "effective_enabled": r.effective_enabled,
                    "message": r.describe(),
                }
                for r in self.refused_overrides
            ],
            "advisories": [
                {
                    "code": a.code,
                    "module": a.module_id,
                    "related_module": a.related_module_id,
                    "reason": a.reason,
                    "message": a.describe(),
                }
                for a in self.advisories
            ],
        }


def _enabled_ids(resolution: Resolution) -> set[str]:
    return {m.id for m in resolution.modules if m.enabled}


def hard_requirement_violations(
    tree: Tree, resolution: Resolution
) -> tuple[RequirementViolation, ...]:
    """Enabled modules whose `requires` names a module that is not enabled.

    Reports; it does not repair. The resolver deliberately does not force-enable
    a module the user switched off -- silently switching one back on makes an
    explicit choice fail to stick, which is the same failure as overwriting a
    setting they changed. So the profile is still resolvable; it is simply not
    valid, and tooling says which part.

    Sorted and deterministic. Pure: no I/O.
    """
    enabled = _enabled_ids(resolution)
    return tuple(
        sorted(
            (
                RequirementViolation(module_id=module_id, requires=required)
                for module_id in enabled
                for required in tree.modules.get(module_id, {}).get("requires", [])
                if isinstance(required, str) and required not in enabled
            ),
            key=lambda v: (v.module_id, v.requires),
        )
    )


def advisories(tree: Tree, resolution: Resolution) -> tuple[Advisory, ...]:
    """Enabled modules whose `works_best_with` names a module that is not enabled.

    Advisory only. The profile is valid, nothing is enabled to satisfy it and no
    package is added because of it -- a soft relationship exists to be *said*,
    not acted on. The standard Kedra preset enables both sides of every such
    pair, so in practice these surface only for Custom Kedra.

    Sorted and deterministic. Pure: no I/O.
    """
    enabled = _enabled_ids(resolution)
    found: list[Advisory] = []
    for module_id in sorted(enabled):
        for entry in tree.modules.get(module_id, {}).get("works_best_with", []):
            if not isinstance(entry, dict):
                continue
            related = entry.get("id")
            if not isinstance(related, str) or related in enabled:
                continue
            found.append(
                Advisory(
                    code=ADVISORY_WORKS_BEST_WITH_DISABLED,
                    module_id=module_id,
                    related_module_id=related,
                    reason=entry.get("reason", "") or "",
                )
            )
    return tuple(sorted(found, key=lambda a: (a.module_id, a.related_module_id)))


def diagnose(tree: Tree, resolution: Resolution) -> Diagnostics:
    """Everything worth saying about a profile, for a caller that reports on it.

    Refusals are carried through from the resolution rather than recomputed:
    only `_build` saw the caller's overrides, and re-deriving "what did they ask
    for?" from the resolved output is not possible -- a refused override leaves
    no trace in `modules`, which is precisely the property that makes refusing
    safe.
    """
    return Diagnostics(
        violations=hard_requirement_violations(tree, resolution),
        advisories=advisories(tree, resolution),
        refused_overrides=resolution.refused_overrides,
    )


def resolve_preset(tree: Tree, preset_id: str) -> Resolution:
    """Resolve a preset that exists in the manifest tree."""
    chain = _chain(tree, preset_id)
    applies = _check_layer_consistency(tree, chain)
    return _build(
        tree,
        chain,
        applies,
        extra_overrides=[],
        extra_origin=f"preset:{preset_id}",
        leaf_id=preset_id,
        leaf_version=tree.presets[preset_id].get("version", "0.0.0"),
    )


def resolve_state(tree: Tree, user_state) -> Resolution:
    """Resolve a user's selection state against the manifest tree.

    The user's state is treated as an anonymous derived layer on top of its
    base preset. It is never written into the manifest tree, and the tree is
    never mutated -- the same distro data resolves differently per user purely
    by supplying different overrides here.
    """
    base_id = user_state.base_preset
    chain = _chain(tree, base_id)
    applies = _check_layer_consistency(tree, chain)

    entries = [{"id": o.id, "enabled": o.enabled} for o in user_state.module_overrides]
    overrides = _validated_overrides(tree, entries, "user state")

    return _build(
        tree,
        chain,
        applies,
        extra_overrides=overrides,
        extra_origin="user-state",
        leaf_id=base_id,
        leaf_version=tree.presets[base_id].get("version", "0.0.0"),
    )
