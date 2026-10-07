"""The package set a resolved profile requires.

Deterministic and side-effect free: it folds module manifests over a
`Resolution` and returns names. It does not consult pacman, does not consult
the filesystem, and deliberately cannot install anything -- transactions are a
later task, and building them on top of a pure function is much easier than
untangling them from an impure one later.

Why this exists at all: whether a Kedra default reaches a desktop is decided by
*package presence*, not by a configuration write. `kedra-experience-workflow`
installs a GSettings vendor override; if it is not installed, upstream GNOME
defaults stand, with nothing to revert. So "which packages does this profile
need" is the question that makes Vanilla genuinely vanilla, and it has to be
answerable from data alone.
"""

from __future__ import annotations

from dataclasses import dataclass

# Relations, strongest first. Strength is dependency strength only; it says
# nothing about whether the module that declares it is enabled.
RELATION_REQUIRED = "required"
RELATION_RECOMMENDED = "recommended"
RELATION_OPTIONAL = "optional"
RELATION_CONDITIONAL = "conditional"

RELATION_ORDER = (
    RELATION_REQUIRED,
    RELATION_RECOMMENDED,
    RELATION_OPTIONAL,
    RELATION_CONDITIONAL,
)


@dataclass(frozen=True)
class PackageRequirement:
    """One package a resolved profile refers to, and why."""

    name: str
    relation: str
    module_id: str
    reason: str
    # Set only for a relation this layer cannot settle on its own -- currently
    # just `package-installed`, which needs a query against the target system.
    # An unresolved condition is reported, never guessed at.
    unresolved_condition: dict | None = None

    @property
    def resolved(self) -> bool:
        return self.unresolved_condition is None


def package_requirements(tree, resolution) -> tuple[PackageRequirement, ...]:
    """Every package relation contributed by the modules a resolution enables.

    Disabled modules contribute nothing, and a preset that does not apply the
    Experience layer contributes nothing at all -- its module set is empty by
    construction, so Vanilla falls out of the same code path as everything else
    rather than needing a special case here.

    Conditional relations are evaluated only where that is possible without
    touching the system:

    * `module-enabled` is answered from this very resolution.
    * `package-installed` cannot be, so it is returned with the condition
      attached for a caller that has a package database to ask.

    Sorted by (name, module ID) so the result is stable across runs.
    """
    enabled = {m.id for m in resolution.modules if m.enabled}

    found: list[PackageRequirement] = []
    for module_id in sorted(enabled):
        manifest = tree.modules.get(module_id)
        if manifest is None:
            continue
        for entry in manifest.get("packages", []):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            relation = entry.get("relation")
            if not isinstance(name, str) or relation not in RELATION_ORDER:
                continue

            unresolved: dict | None = None
            if relation == RELATION_CONDITIONAL:
                condition = entry.get("condition")
                if not isinstance(condition, dict):
                    continue
                kind = condition.get("type")
                if kind == "module-enabled":
                    if condition.get("module") not in enabled:
                        continue
                elif kind == "package-installed":
                    unresolved = dict(condition)
                else:
                    continue

            found.append(
                PackageRequirement(
                    name=name,
                    relation=relation,
                    module_id=module_id,
                    reason=entry.get("reason", ""),
                    unresolved_condition=unresolved,
                )
            )

    return tuple(sorted(found, key=lambda r: (r.name, r.module_id)))


def required_packages(tree, resolution) -> tuple[str, ...]:
    """Package names this profile must have installed. Sorted, deduplicated.

    Only `required` relations, and only those already settled: a recommendation
    is not a requirement, and a condition nobody has evaluated is not a
    requirement either. This is the set an installer would diff against what is
    installed -- so an unevaluated condition must not be able to leak into it.
    """
    return tuple(
        sorted(
            {
                requirement.name
                for requirement in package_requirements(tree, resolution)
                if requirement.relation == RELATION_REQUIRED and requirement.resolved
            }
        )
    )


def packages_by_relation(tree, resolution) -> dict[str, tuple[str, ...]]:
    """All resolved package names, grouped by relation. Every relation present."""
    requirements = package_requirements(tree, resolution)
    return {
        relation: tuple(
            sorted(
                {r.name for r in requirements if r.relation == relation and r.resolved}
            )
        )
        for relation in RELATION_ORDER
    }


__all__ = [
    "RELATION_CONDITIONAL",
    "RELATION_OPTIONAL",
    "RELATION_ORDER",
    "RELATION_RECOMMENDED",
    "RELATION_REQUIRED",
    "PackageRequirement",
    "package_requirements",
    "packages_by_relation",
    "required_packages",
]
