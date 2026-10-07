"""Loading and validation of the distro-owned Experience manifest tree.

This module owns the *immutable* side of the data model: the manifests shipped
in /usr/share/kedra/experience/. Mutable per-user state lives in `state`, and
the two are never mixed.

Schema history
--------------
1  Initial architecture.
2  `condition` on a conditional package relation became a structured table
   instead of an unparsed expression string. See CONDITION_TYPES.
3  `module.settings` and `module.migrations` became arrays of typed records
   with a defined schema, replacing the reserved empty tables. See `settings`.
4  `module.works_best_with` added: an *advisory* relationship between modules,
   split out from `requires`, which had been carrying both meanings. See
   WORKS_BEST_WITH_KNOWN_KEYS and `resolve.diagnose`.
5  `module.applications` added: which application a module nominates for a
   named role, as a desktop-file ID. See APPLICATION_ROLES and `applications`.
6  `module.shortcuts` added: a GNOME custom keyboard shortcut, declared as one
   composite resource rather than as the four coordinated GSettings values it
   is made of. See `shortcuts`.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .backends import spec_for
from .settings import migration_problems, setting_problems
from .shortcuts import expanded_settings, shortcut_problems

SCHEMA_VERSION = 6

MODULE_TIERS = frozenset({"core", "recommended", "optional"})
PACKAGE_RELATIONS = frozenset({"required", "recommended", "optional", "conditional"})
PRESET_KINDS = frozenset({"base", "derived"})

# Conditional dependency grammar (schema 2).
#
# Deliberately a closed set of typed records, not an expression language: each
# condition names its type and exactly one operand. There is no negation, no
# boolean composition and no evaluator, so a condition can be inspected by
# tooling without anything resembling eval. If a future need genuinely cannot
# be expressed, the right move is to add a type here -- not to add syntax.
CONDITION_TYPES: dict[str, str] = {
    # True when the named pacman package is installed on the target system.
    "package-installed": "package",
    # True when the named Experience module resolves to enabled.
    "module-enabled": "module",
}

# Activation state belongs to presets. A module manifest that declares its own
# activation has confused dependency strength with activation, which is exactly
# the conflation the architecture forbids -- so these keys are rejected outright
# rather than ignored.
FORBIDDEN_MODULE_KEYS = frozenset(
    {"enabled", "default_enabled", "active", "enabled_by_default"}
)

MODULE_REQUIRED_KEYS = frozenset(
    {"id", "name", "description", "version", "tier", "user_selectable"}
)
MODULE_KNOWN_KEYS = MODULE_REQUIRED_KEYS | {
    "introduced_in",
    "requires",
    "works_best_with",
    "conflicts",
    "packages",
    "applications",
    "settings",
    "shortcuts",
    "migrations",
}

# Application roles (schema 5).
#
# A module may nominate the application that fills a named role -- currently
# only the terminal. This is *not* a GSettings preference and deliberately does
# not live in `module.settings`: nothing writes it, nothing reads it out of
# dconf, and it has no user-visible value to revert. It is a contract, stated
# once, that other parts of Kedra can build on -- the Super+Enter shortcut being
# the case it exists for.
#
# A closed vocabulary, like CONDITION_TYPES, and for the same reason: a role is
# something Kedra has decided it has, so adding one is a deliberate schema edit
# with a consumer in hand. This is not a generic application registry -- there
# are no priorities, no fallbacks and no lookup service, because nothing needs
# them yet.
APPLICATION_ROLES: dict[str, str] = {
    # The terminal emulator the Kedra experience treats as its own.
    "terminal": "terminal emulator",
}

# An application is named by its desktop-file ID, never by a binary path or a
# command line. A desktop ID survives a Flatpak/native switch, is what
# GAppInfo and GNOME Settings both speak, and cannot smuggle in
# arguments or a shell fragment.
APPLICATION_DESKTOP_SUFFIX = ".desktop"

APPLICATION_REQUIRED_KEYS = frozenset({"role", "desktop_id", "package"})
APPLICATION_KNOWN_KEYS = APPLICATION_REQUIRED_KEYS | {"reason"}

# The two module-to-module relationships, and the whole reason schema 4 exists.
#
#   requires          A cannot function correctly without B. A enabled with B
#                     disabled is an invalid profile, and tooling must say so.
#
#   works_best_with   A and B are designed to be used together, and the standard
#                     Kedra preset enables both -- but A on its own still works.
#                     A enabled with B disabled is a *valid* profile that earns
#                     an advisory, nothing more.
#
# Before schema 4 there was only `requires`, and it was carrying both meanings.
# The result was that a Custom Kedra which switched off `desktop` -- an
# explicitly supported customisation -- reported four "unsatisfied requirements"
# and looked broken. Overstating a dependency is not the safe direction to err
# in: it makes honest customisation look like misconfiguration.
#
# Deliberately two, not a taxonomy. "Hard" and "advisory" is the distinction the
# product actually has; anything finer would be invented.
#
# `works_best_with` is spelled out rather than called `recommends` because this
# same file format already uses `recommended` for a *package* relation, and that
# one does mean "install it by default". A module relationship must never
# install or enable anything, so reusing the word would invite exactly the
# conflation the rest of this architecture works to prevent.
WORKS_BEST_WITH_KNOWN_KEYS = frozenset({"id", "reason"})

PRESET_REQUIRED_KEYS = frozenset(
    {"id", "name", "description", "version", "kind", "applies_experience_layer"}
)
PRESET_KNOWN_KEYS = PRESET_REQUIRED_KEYS | {
    "user_selectable",
    "modules",
    "module_overrides",
    "base_preset",
}

PACKAGE_KNOWN_KEYS = frozenset({"name", "relation", "reason", "condition", "provides"})


@dataclass
class Report:
    """Collected errors, keyed by the file they were found in."""

    errors: list[tuple[str, str]] = field(default_factory=list)

    def error(self, where: Path | str, message: str) -> None:
        self.errors.append((str(where), message))

    @property
    def ok(self) -> bool:
        return not self.errors

    def rendered(self) -> str:
        return "\n".join(f"{where}: error: {message}" for where, message in self.errors)


@dataclass
class Tree:
    """A parsed, validated manifest tree.

    Produced by `load_tree` so callers -- notably the resolver -- work from
    already-parsed tables instead of re-reading files.
    """

    root: Path
    descriptor: dict
    modules: dict[str, dict]
    presets: dict[str, dict]

    @property
    def default_preset(self) -> str | None:
        return self.descriptor.get("experience", {}).get("default_preset")


class ManifestError(Exception):
    """Raised when a caller needs a hard failure instead of a Report."""


def is_semver(value: object) -> bool:
    """MAJOR.MINOR.PATCH with numeric, non-padded components."""
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    if len(parts) != 3:
        return False
    return all(p.isdigit() and (p == "0" or not p.startswith("0")) for p in parts)


def is_identifier(value: object) -> bool:
    """Stable IDs are lowercase, dash-separated, and start with a letter."""
    if not isinstance(value, str) or not value:
        return False
    if not value[0].isalpha():
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in value)


def load_toml(path: Path, report: Report) -> dict | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        report.error(path, f"malformed TOML: {exc}")
    except OSError as exc:
        report.error(path, f"unreadable: {exc}")
    return None


def check_schema_version(doc: dict, path: Path, report: Report) -> None:
    if "schema_version" not in doc:
        report.error(path, "missing required top-level key 'schema_version'")
        return
    version = doc["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        report.error(path, f"schema_version must be an integer, got {version!r}")
    elif version != SCHEMA_VERSION:
        report.error(
            path, f"unsupported schema_version {version} (this tool speaks {SCHEMA_VERSION})"
        )


def check_keys(
    table: dict, required: frozenset, known: frozenset, path: Path, label: str, report: Report
) -> None:
    for key in sorted(required - table.keys()):
        report.error(path, f"{label}: missing required key '{key}'")
    for key in sorted(table.keys() - known):
        report.error(path, f"{label}: unknown key '{key}'")


def validate_condition(condition: object, path: Path, label: str, report: Report) -> None:
    """Validate one structured conditional-dependency record (schema 2)."""
    if isinstance(condition, str):
        report.error(
            path,
            f"{label}: 'condition' must be a table; expression strings such as "
            f"{condition!r} were removed in schema_version 2 "
            f"(use condition.type and its operand)",
        )
        return
    if not isinstance(condition, dict):
        report.error(path, f"{label}: 'condition' must be a table")
        return

    kind = condition.get("type")
    if kind is None:
        report.error(path, f"{label}.condition: missing required key 'type'")
        return
    if kind not in CONDITION_TYPES:
        report.error(
            path,
            f"{label}.condition: invalid type {kind!r} "
            f"(expected one of {sorted(CONDITION_TYPES)})",
        )
        return

    operand = CONDITION_TYPES[kind]
    allowed = {"type", operand}
    for key in sorted(condition.keys() - allowed):
        report.error(
            path,
            f"{label}.condition: key '{key}' is not valid for type {kind!r} "
            f"(expected '{operand}')",
        )

    value = condition.get(operand)
    if not isinstance(value, str) or not value:
        report.error(
            path, f"{label}.condition: type {kind!r} requires a non-empty '{operand}'"
        )
        return

    # module-enabled names an Experience module, so it must look like a module
    # ID here and is cross-checked against the tree in load_tree().
    if kind == "module-enabled" and not is_identifier(value):
        report.error(
            path, f"{label}.condition: module {value!r} is not a valid module ID"
        )


def validate_packages(entries: object, path: Path, report: Report) -> None:
    if not isinstance(entries, list):
        report.error(path, "module.packages must be an array of tables")
        return
    for index, entry in enumerate(entries):
        label = f"module.packages[{index}]"
        if not isinstance(entry, dict):
            report.error(path, f"{label}: must be a table")
            continue
        for key in sorted(entry.keys() - PACKAGE_KNOWN_KEYS):
            report.error(path, f"{label}: unknown key '{key}'")

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            report.error(path, f"{label}: missing or empty 'name'")

        relation = entry.get("relation")
        if relation is None:
            report.error(path, f"{label}: missing required key 'relation'")
        elif relation not in PACKAGE_RELATIONS:
            report.error(
                path,
                f"{label}: invalid relation {relation!r} "
                f"(expected one of {sorted(PACKAGE_RELATIONS)})",
            )
        elif relation == "conditional":
            if "condition" not in entry:
                report.error(path, f"{label}: relation 'conditional' requires a 'condition'")
            else:
                validate_condition(entry["condition"], path, label, report)
        elif "condition" in entry:
            report.error(
                path, f"{label}: 'condition' is only meaningful for relation 'conditional'"
            )


def validate_module(path: Path, report: Report) -> dict | None:
    doc = load_toml(path, report)
    if doc is None:
        return None
    check_schema_version(doc, path, report)

    module = doc.get("module")
    if not isinstance(module, dict):
        report.error(path, "missing required [module] table")
        return None

    for key in sorted(FORBIDDEN_MODULE_KEYS & module.keys()):
        report.error(
            path,
            f"module: '{key}' is activation state and must not appear in a module "
            f"manifest; activation is declared by presets",
        )

    check_keys(module, MODULE_REQUIRED_KEYS, MODULE_KNOWN_KEYS, path, "module", report)

    if "id" in module and not is_identifier(module["id"]):
        report.error(path, f"module.id {module['id']!r} is not a valid identifier")
    if "version" in module and not is_semver(module["version"]):
        report.error(
            path, f"module.version {module['version']!r} is not MAJOR.MINOR.PATCH"
        )
    if "introduced_in" in module and not is_semver(module["introduced_in"]):
        report.error(
            path, f"module.introduced_in {module['introduced_in']!r} is not MAJOR.MINOR.PATCH"
        )
    if "tier" in module and module["tier"] not in MODULE_TIERS:
        report.error(
            path,
            f"invalid module.tier {module['tier']!r} (expected one of {sorted(MODULE_TIERS)})",
        )
    if "user_selectable" in module and not isinstance(module["user_selectable"], bool):
        report.error(path, "module.user_selectable must be a boolean")
    if "description" in module and not str(module["description"]).strip():
        report.error(path, "module.description must not be empty")

    for key in ("requires", "conflicts"):
        value = module.get(key, [])
        if not isinstance(value, list) or not all(is_identifier(v) for v in value):
            report.error(path, f"module.{key} must be an array of module IDs")
        elif "id" in module and module["id"] in value:
            report.error(path, f"module.{key} must not reference the module itself")

    validate_works_best_with(module, path, report)

    if "packages" in module:
        validate_packages(module["packages"], path, report)

    validate_applications(module, path, report)
    validate_settings(module.get("settings"), path, report)
    # Shortcuts are validated twice: for shape here, where no tree exists yet,
    # and again in load_tree() once the role a command may reference can be
    # resolved. Doing the shape half early means a malformed shortcut is
    # reported from the file it is in, with the rest of that file's problems.
    validate_shortcuts(module.get("shortcuts"), path, report, modules=None)
    validate_migrations(module.get("migrations"), path, report)

    return module if "id" in module else None


def validate_works_best_with(module: dict, path: Path, report: Report) -> None:
    """Validate a module's advisory relationships (schema 4).

    An array of tables rather than an array of IDs, because unlike a hard
    requirement an advisory relationship has a *consequence* worth stating: what
    the user loses by separating the two. That text belongs with the module that
    knows it, not in the resolver and not in a UI string table.
    """
    entries = module.get("works_best_with")
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(path, "module.works_best_with must be an array of tables")
        return

    module_id = module.get("id")
    requires = module.get("requires", [])
    conflicts = module.get("conflicts", [])
    seen: set[str] = set()

    for index, entry in enumerate(entries):
        label = f"module.works_best_with[{index}]"
        if not isinstance(entry, dict):
            report.error(
                path,
                f"{label}: must be a table with 'id' (an array of bare module IDs "
                f"was the schema 3 shape of 'requires', not this)",
            )
            continue

        for key in sorted(entry.keys() - WORKS_BEST_WITH_KNOWN_KEYS):
            report.error(path, f"{label}: unknown key '{key}'")

        related = entry.get("id")
        if not is_identifier(related):
            report.error(path, f"{label}: missing or invalid module 'id'")
            continue
        if related == module_id:
            report.error(path, f"{label}: must not reference the module itself")
            continue
        if related in seen:
            report.error(path, f"{label}: duplicate reference to module '{related}'")
        seen.add(related)

        # A relationship is one strength or the other. Declaring both says the
        # module cannot work without B *and* works fine without it.
        if related in requires:
            report.error(
                path,
                f"{label}: module '{related}' is already a hard requirement; a "
                f"relationship is either hard or advisory, never both",
            )
        if related in conflicts:
            report.error(
                path,
                f"{label}: module '{related}' is declared as a conflict; it cannot "
                f"also be recommended alongside",
            )

        reason = entry.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            report.error(path, f"{label}: 'reason' must be a non-empty string")


def validate_applications(module: dict, path: Path, report: Report) -> None:
    """Validate a module's application contracts (schema 5).

    Two rules carry the weight here.

    *An application is a desktop ID.* `kgx`, `/usr/bin/kgx` and
    `flatpak run org.gnome.Console` are all rejected: a command string is a
    packaging detail that changes underneath the contract, and it is the shape
    that would let a manifest smuggle in arguments. `org.gnome.Console.desktop`
    is stable, launchable through GAppInfo, and inert as data.

    *The package must already be a required one.* A module that nominates an
    application the profile does not install has declared a contract nothing
    guarantees -- so the two statements are cross-checked here rather than left
    to agree by hand.
    """
    entries = module.get("applications")
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(path, "module.applications must be an array of tables")
        return

    required_packages = {
        entry.get("name")
        for entry in module.get("packages", [])
        if isinstance(entry, dict) and entry.get("relation") == "required"
    }
    seen_roles: set[str] = set()

    for index, entry in enumerate(entries):
        label = f"module.applications[{index}]"
        if not isinstance(entry, dict):
            report.error(path, f"{label}: must be a table")
            continue

        for key in sorted(entry.keys() - APPLICATION_KNOWN_KEYS):
            report.error(path, f"{label}: unknown key '{key}'")
        for key in sorted(APPLICATION_REQUIRED_KEYS - entry.keys()):
            report.error(path, f"{label}: missing required key '{key}'")

        role = entry.get("role")
        if role is not None:
            if role not in APPLICATION_ROLES:
                report.error(
                    path,
                    f"{label}: unknown application role {role!r} "
                    f"(expected one of {sorted(APPLICATION_ROLES)})",
                )
            elif role in seen_roles:
                report.error(
                    path,
                    f"{label}: role {role!r} is already filled by this module; a role "
                    f"names one application, not a list of candidates",
                )
            else:
                seen_roles.add(role)

        desktop_id = entry.get("desktop_id")
        if desktop_id is not None and not is_desktop_id(desktop_id):
            report.error(
                path,
                f"{label}: desktop_id {desktop_id!r} is not a desktop-file ID such as "
                f"'org.gnome.Console.desktop'; an application contract names a desktop "
                f"file, never a binary path or a command line",
            )

        package = entry.get("package")
        if package is not None:
            if not isinstance(package, str) or not package:
                report.error(path, f"{label}: 'package' must be a non-empty string")
            elif package not in required_packages:
                report.error(
                    path,
                    f"{label}: package {package!r} is not declared by this module as a "
                    f"'required' package relation; a module cannot nominate an "
                    f"application its own profile would not install",
                )

        reason = entry.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            report.error(path, f"{label}: 'reason' must be a non-empty string")


def is_desktop_id(value: object) -> bool:
    """A desktop-file ID: dotted segments plus a '.desktop' suffix.

    Deliberately strict. Anything containing a path separator, whitespace or an
    argument is not an ID, and neither is a bare binary name -- rejecting them
    is the entire reason this check exists rather than a non-empty-string test.
    """
    if not isinstance(value, str) or not value.endswith(APPLICATION_DESKTOP_SUFFIX):
        return False
    stem = value[: -len(APPLICATION_DESKTOP_SUFFIX)]
    if not stem:
        return False
    segments = stem.split(".")
    return all(_is_desktop_id_segment(segment) for segment in segments)


def _is_desktop_id_segment(segment: str) -> bool:
    if not segment or not segment[0].isalpha():
        return False
    return all(c.isalnum() or c in "-_" for c in segment)


def check_application_role_uniqueness(
    modules: dict[str, dict], module_paths: dict[str, Path], report: Report
) -> None:
    """One module owns a role, tree-wide.

    Same rule, and the same reason, as a setting's backend target: two modules
    nominating a terminal means there is no single answer to "which terminal
    does this profile use", and the first consumer of that question would have
    to invent a tie-break. Selecting a *different* terminal later is an edit to
    the one declaration, not a second one alongside it.
    """
    owners: dict[str, tuple[str, str]] = {}
    for module_id in sorted(modules):
        for index, entry in enumerate(modules[module_id].get("applications", [])):
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            if role not in APPLICATION_ROLES:
                continue
            if role in owners:
                other_module, other_desktop_id = owners[role]
                report.error(
                    module_paths[module_id],
                    f"module.applications[{index}]: role '{role}' is already filled by "
                    f"module '{other_module}' with '{other_desktop_id}'; a role has one "
                    f"owner across the whole Experience tree",
                )
            else:
                owners[role] = (module_id, entry.get("desktop_id"))


def validate_settings(entries: object, path: Path, report: Report) -> None:
    """Validate a module's setting declarations (schema 3)."""
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(path, "module.settings must be an array of tables")
        return
    for index, entry in enumerate(entries):
        for problem in setting_problems(entry, f"module.settings[{index}]"):
            report.error(path, problem)


def validate_shortcuts(
    entries: object, path: Path, report: Report, modules: dict | None = None
) -> None:
    """Validate a module's custom-shortcut declarations (schema 6).

    With `modules`, a `command` that names an application role is resolved
    against the tree as well, so a shortcut cannot claim to launch a role
    nothing fills.
    """
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(path, "module.shortcuts must be an array of tables")
        return
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"module.shortcuts[{index}]"
        for problem in shortcut_problems(entry, label, modules):
            report.error(path, problem)
        if not isinstance(entry, dict):
            continue
        shortcut_id = entry.get("id")
        if isinstance(shortcut_id, str):
            if shortcut_id in seen:
                report.error(path, f"{label}: duplicate shortcut ID '{shortcut_id}'")
            seen.add(shortcut_id)


def module_setting_entries(module_id: str, module: dict, modules: dict) -> list[dict]:
    """Every setting table a module contributes: declared, then generated.

    The single view of "what settings does this module have". Everything that
    reasons over settings -- duplicate detection, migration cross-checks,
    planning, state validation -- goes through this, so a shortcut's four
    generated settings are subject to exactly the rules a hand-written one is
    and cannot quietly collide with one.
    """
    declared = [e for e in module.get("settings", []) or [] if isinstance(e, dict)]
    return declared + expanded_settings(module_id, module, modules)


def validate_migrations(entries: object, path: Path, report: Report) -> None:
    """Validate a module's migration records (schema 3)."""
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(
            path,
            "module.migrations must be an array of tables; the reserved "
            "[module.migrations] table with 'entries' was replaced in schema_version 3",
        )
        return
    for index, entry in enumerate(entries):
        label = f"module.migrations[{index}]"
        for problem in migration_problems(entry, label):
            report.error(path, problem)
        if isinstance(entry, dict) and "module_version" in entry:
            if not is_semver(entry["module_version"]):
                report.error(
                    path,
                    f"{label}: module_version {entry['module_version']!r} is not "
                    f"MAJOR.MINOR.PATCH",
                )


def validate_preset(path: Path, report: Report) -> dict | None:
    doc = load_toml(path, report)
    if doc is None:
        return None
    check_schema_version(doc, path, report)

    preset = doc.get("preset")
    if not isinstance(preset, dict):
        report.error(path, "missing required [preset] table")
        return None

    check_keys(preset, PRESET_REQUIRED_KEYS, PRESET_KNOWN_KEYS, path, "preset", report)

    if "id" in preset and not is_identifier(preset["id"]):
        report.error(path, f"preset.id {preset['id']!r} is not a valid identifier")
    if "version" in preset and not is_semver(preset["version"]):
        report.error(
            path, f"preset.version {preset['version']!r} is not MAJOR.MINOR.PATCH"
        )

    kind = preset.get("kind")
    if kind is not None and kind not in PRESET_KINDS:
        report.error(
            path, f"invalid preset.kind {kind!r} (expected one of {sorted(PRESET_KINDS)})"
        )

    applies = preset.get("applies_experience_layer")
    if applies is not None and not isinstance(applies, bool):
        report.error(path, "preset.applies_experience_layer must be a boolean")

    modules = preset.get("modules", [])
    overrides = preset.get("module_overrides", [])

    if kind == "derived":
        if not preset.get("base_preset"):
            report.error(path, "preset.kind 'derived' requires 'base_preset'")
        elif preset["base_preset"] == preset.get("id"):
            report.error(path, "preset.base_preset must not reference the preset itself")
        if modules:
            report.error(
                path,
                "a derived preset must record only 'module_overrides'; a full 'modules' "
                "list would clone its base instead of inheriting from it",
            )
    else:
        if "base_preset" in preset:
            report.error(path, "'base_preset' is only valid when preset.kind is 'derived'")
        if overrides:
            report.error(path, "'module_overrides' is only valid for a derived preset")

    # Vanilla semantics: a preset that does not apply the Experience layer must
    # not carry module entries at all. Otherwise it becomes "Kedra with
    # everything toggled off", and tooling could apply defaults then revert them.
    if applies is False and (modules or overrides):
        report.error(
            path,
            "preset declares applies_experience_layer = false but still lists modules; "
            "the absence of the Experience layer must be expressed as an empty module set",
        )

    for label, entries in (("modules", modules), ("module_overrides", overrides)):
        if not isinstance(entries, list):
            report.error(path, f"preset.{label} must be an array of tables")
            continue
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            where = f"preset.{label}[{index}]"
            if not isinstance(entry, dict):
                report.error(path, f"{where}: must be a table")
                continue
            for key in sorted(entry.keys() - {"id", "enabled", "presentation"}):
                report.error(path, f"{where}: unknown key '{key}'")
            module_id = entry.get("id")
            if not is_identifier(module_id):
                report.error(path, f"{where}: missing or invalid module 'id'")
                continue
            if module_id in seen:
                report.error(path, f"{where}: duplicate reference to module '{module_id}'")
            seen.add(module_id)
            if not isinstance(entry.get("enabled"), bool):
                report.error(path, f"{where}: 'enabled' must be present and a boolean")

    return preset if "id" in preset else None


def load_tree(root: Path, report: Report) -> Tree | None:
    """Validate a data directory and return the parsed tree.

    Returns None if the tree is structurally unusable. A non-None result may
    still have errors in `report`; callers that need a clean tree must check
    `report.ok` themselves.
    """
    descriptor_path = root / "experience.toml"
    if not descriptor_path.is_file():
        report.error(root, "missing experience.toml root descriptor")
        return None

    descriptor = load_toml(descriptor_path, report)
    if descriptor is None:
        return None
    check_schema_version(descriptor, descriptor_path, report)

    experience = descriptor.get("experience")
    if not isinstance(experience, dict):
        report.error(descriptor_path, "missing required [experience] table")
        return None
    for key in ("layout_version", "data_format", "presets_dir", "modules_dir", "default_preset"):
        if key not in experience:
            report.error(descriptor_path, f"experience: missing required key '{key}'")
    if experience.get("data_format") not in (None, "toml"):
        report.error(
            descriptor_path, f"unsupported data_format {experience['data_format']!r}"
        )

    modules_dir = root / experience.get("modules_dir", "modules")
    presets_dir = root / experience.get("presets_dir", "presets")

    modules: dict[str, dict] = {}
    module_paths: dict[str, Path] = {}
    for path in sorted(modules_dir.glob("*.toml")):
        module = validate_module(path, report)
        if module is None:
            continue
        module_id = module["id"]
        if module_id in modules:
            report.error(
                path,
                f"duplicate module ID '{module_id}' (already defined in {module_paths[module_id]})",
            )
            continue
        if path.stem != module_id:
            report.error(path, f"filename does not match module ID '{module_id}'")
        modules[module_id] = module
        module_paths[module_id] = path

    presets: dict[str, dict] = {}
    preset_paths: dict[str, Path] = {}
    for path in sorted(presets_dir.glob("*.toml")):
        preset = validate_preset(path, report)
        if preset is None:
            continue
        preset_id = preset["id"]
        if preset_id in presets:
            report.error(path, f"duplicate preset ID '{preset_id}'")
            continue
        if path.stem != preset_id:
            report.error(path, f"filename does not match preset ID '{preset_id}'")
        presets[preset_id] = preset
        preset_paths[preset_id] = path

        for label in ("modules", "module_overrides"):
            for entry in preset.get(label, []):
                if not isinstance(entry, dict):
                    continue
                module_id = entry.get("id")
                if is_identifier(module_id) and module_id not in modules:
                    report.error(
                        path, f"preset.{label} references nonexistent module '{module_id}'"
                    )

    # Module-to-module dependency graph, and module references inside
    # conditional package metadata.
    for module_id, module in modules.items():
        path = module_paths[module_id]
        for key in ("requires", "conflicts"):
            for dependency in module.get(key, []):
                if dependency not in modules:
                    report.error(
                        path, f"module.{key} references nonexistent module '{dependency}'"
                    )
        for index, entry in enumerate(module.get("works_best_with", [])):
            if not isinstance(entry, dict):
                continue
            related = entry.get("id")
            if is_identifier(related) and related not in modules:
                report.error(
                    path,
                    f"module.works_best_with[{index}] references nonexistent "
                    f"module '{related}'",
                )
        for index, entry in enumerate(module.get("packages", [])):
            if not isinstance(entry, dict):
                continue
            condition = entry.get("condition")
            if not isinstance(condition, dict):
                continue
            if condition.get("type") == "module-enabled":
                target = condition.get("module")
                if is_identifier(target) and target not in modules:
                    report.error(
                        path,
                        f"module.packages[{index}].condition references nonexistent "
                        f"module '{target}'",
                    )

    # Re-run shortcut validation now that the whole tree is known, so a
    # `command` referencing an application role can be resolved.
    for module_id, module in modules.items():
        validate_shortcuts(
            module.get("shortcuts"), module_paths[module_id], report, modules=modules
        )

    detect_cycles(modules, module_paths, report)
    check_application_role_uniqueness(modules, module_paths, report)
    check_setting_uniqueness(modules, module_paths, report)
    check_migration_references(modules, module_paths, report)

    default_preset = experience.get("default_preset")
    if default_preset is not None and default_preset not in presets:
        report.error(
            descriptor_path, f"default_preset references nonexistent preset '{default_preset}'"
        )

    for preset_id, preset in presets.items():
        base = preset.get("base_preset")
        if base is not None and base not in presets:
            report.error(
                preset_paths[preset_id],
                f"base_preset references nonexistent preset '{base}'",
            )

    return Tree(root=root, descriptor=descriptor, modules=modules, presets=presets)


def setting_index(modules: dict[str, dict]) -> dict[str, str]:
    """Map every declared setting ID to the module that declares it."""
    index: dict[str, str] = {}
    for module_id, module in modules.items():
        for entry in module_setting_entries(module_id, module, modules):
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                index.setdefault(entry["id"], module_id)
    return index


def check_setting_uniqueness(
    modules: dict[str, dict], module_paths: dict[str, Path], report: Report
) -> None:
    """Setting IDs and backend targets must be unique across the whole tree.

    IDs are global because a user's managed-value record refers to a setting by
    ID alone; scoping them per module would make moving a setting between
    modules a breaking change to every user's state.

    Targets are global because two settings claiming the same key means
    ambiguous ownership -- there would be no single answer to "what did Kedra
    last write here?". If a genuine need appears for two modules to offer the
    same key, it should be one setting with a condition, not two owners.
    """
    seen_ids: dict[str, str] = {}
    # canonical target -> (module ID, setting ID, raw declared value)
    seen_targets: dict[str, tuple[str, str, object]] = {}

    for module_id in sorted(modules):
        module = modules[module_id]
        path = module_paths[module_id]
        for index, entry in enumerate(module_setting_entries(module_id, module, modules)):
            if not isinstance(entry, dict):
                continue
            declared_count = len(module.get("settings", []) or [])
            label = (
                f"module.settings[{index}]"
                if index < declared_count
                else f"module.shortcuts -> setting '{entry.get('id')}'"
            )

            setting_id = entry.get("id")
            if isinstance(setting_id, str):
                if setting_id in seen_ids:
                    report.error(
                        path,
                        f"{label}: duplicate setting ID '{setting_id}' (already declared "
                        f"by module '{seen_ids[setting_id]}'); setting IDs are unique "
                        f"across the whole Experience tree",
                    )
                else:
                    seen_ids[setting_id] = module_id

            spec = spec_for(entry.get("backend", ""))
            target = entry.get("target")
            if spec is None or not isinstance(target, dict) or spec.target_problems(target):
                continue
            canonical = spec.canonical_target(target)

            if canonical in seen_targets:
                other_module, other_id, other_value = seen_targets[canonical]
                conflicting = other_value != entry.get("value")
                detail = (
                    "with a different value" if conflicting else "with the same value"
                )
                report.error(
                    path,
                    f"{label}: target '{canonical}' is already claimed by setting "
                    f"'{other_id}' in module '{other_module}' {detail}; a target may "
                    f"have only one owner",
                )
            else:
                seen_targets[canonical] = (module_id, setting_id, entry.get("value"))


def check_migration_references(
    modules: dict[str, dict], module_paths: dict[str, Path], report: Report
) -> None:
    """Cross-check migration records against the settings that exist now."""
    index = setting_index(modules)

    for module_id in sorted(modules):
        module = modules[module_id]
        path = module_paths[module_id]
        for position, entry in enumerate(module.get("migrations", [])):
            if not isinstance(entry, dict):
                continue
            label = f"module.migrations[{position}]"
            kind = entry.get("type")

            # A rename must land on a setting that exists; otherwise the
            # migration points nowhere.
            if kind == "rename-setting":
                target_id = entry.get("to_id")
                if isinstance(target_id, str) and target_id not in index:
                    report.error(
                        path, f"{label}: to_id '{target_id}' does not name a declared setting"
                    )
                source_id = entry.get("from_id")
                if isinstance(source_id, str) and source_id in index:
                    report.error(
                        path,
                        f"{label}: from_id '{source_id}' is still declared; a rename must "
                        f"describe an ID that no longer exists",
                    )

            # A removal must describe a setting that is genuinely gone.
            elif kind == "remove-setting":
                setting_id = entry.get("setting_id")
                if isinstance(setting_id, str) and setting_id in index:
                    report.error(
                        path,
                        f"{label}: setting_id '{setting_id}' is still declared by module "
                        f"'{index[setting_id]}'; a removal must describe a setting that "
                        f"no longer exists",
                    )

            # Retarget and default changes act on a setting that still exists.
            elif kind in ("retarget-setting", "change-default"):
                setting_id = entry.get("setting_id")
                if isinstance(setting_id, str) and setting_id not in index:
                    report.error(
                        path,
                        f"{label}: setting_id '{setting_id}' does not name a declared setting",
                    )


def validate_tree(root: Path, report: Report) -> None:
    """Backwards-compatible wrapper around `load_tree`."""
    load_tree(root, report)


def detect_cycles(
    modules: dict[str, dict], module_paths: dict[str, Path], report: Report
) -> None:
    """Report any cycle in the module `requires` graph."""
    graph: dict[str, list[str]] = {
        module_id: [
            r for r in module.get("requires", []) if isinstance(r, str) and r in modules
        ]
        for module_id, module in modules.items()
    }

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, trail: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            cycle = " -> ".join(trail[trail.index(node):] + [node])
            report.error(module_paths[node], f"dependency cycle: {cycle}")
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            walk(dependency, trail + [node])
        visiting.discard(node)
        done.add(node)

    for module_id in sorted(graph):
        walk(module_id, [])


# Where the `kedra-experience` package installs the manifest tree.
#
# A literal absolute path, deliberately: this is the ONLY location a production
# invocation reads, and it must not vary with the environment, the invoking
# user, the working directory or how the interpreter was started. A fallback to
# a source checkout would mean a shipped command that behaves differently on a
# developer's machine than on a user's -- the exact class of bug that makes an
# installer contract untestable.
#
# Kept in step with the `package()` function of kedra-experience/PKGBUILD by
# tests/test_installed_layout.py, which reads the built package's file list.
INSTALLED_DATA_DIR = Path("/usr/share/kedra/experience")


def installed_data_dir() -> Path:
    """The package-owned manifest tree. The only tree production code reads.

    Returns the path whether or not it exists: a missing tree means
    `kedra-experience` is not installed, which is a condition the caller must
    report rather than paper over by looking somewhere else.
    """
    return INSTALLED_DATA_DIR


def source_data_dir() -> Path:
    """The in-tree package data, for developer tooling and tests only.

    Resolved relative to this file, so it follows the checkout it lives in and
    never names a particular machine or user. Never consulted by the production
    CLI -- see `prodcli`.
    """
    return (
        Path(__file__).resolve().parent.parent.parent
        / "kedra-experience"
        / "data"
        / "experience"
    )


def default_data_dir() -> Path:
    """The tree developer tooling works against when none is given.

    Unchanged behaviour: `kedra-experience-dev`, `validate-experience.py` and
    the test suite all operate on the working tree, because their whole purpose
    is to check the manifests *before* they are packaged. Production code calls
    `installed_data_dir()` instead.
    """
    return source_data_dir()


def main(argv: list[str]) -> int:
    """Standalone manifest validation. See tools/validate-experience.py."""
    targets = [Path(a) for a in argv[1:]] or [default_data_dir()]
    report = Report()

    for target in targets:
        if target.is_dir():
            load_tree(target, report)
        elif target.is_file():
            doc = load_toml(target, report)
            if doc is None:
                continue
            if "preset" in doc:
                validate_preset(target, report)
            elif "module" in doc:
                validate_module(target, report)
            elif "experience" in doc:
                check_schema_version(doc, target, report)
            else:
                report.error(target, "not a recognised manifest ([preset]/[module]/[experience])")
        else:
            report.error(target, "no such file or directory")

    if report.ok:
        checked = ", ".join(str(t) for t in targets)
        print(f"kedra-experience: all manifests valid ({checked})")
        return 0

    print(report.rendered(), file=sys.stderr)
    print(f"\n{len(report.errors)} error(s)", file=sys.stderr)
    return 1
