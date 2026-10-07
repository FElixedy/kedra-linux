"""Mutable per-user Kedra Experience state.

This is the *other half* of the data model and must not be confused with the
manifests in `manifests`:

    /usr/share/kedra/experience/   distro-owned, immutable, identical on every
                                   machine, shipped by a package
    $XDG_STATE_HOME/kedra/         user-owned, mutable, machine-local, written
                                   by Kedra tooling

What this file records is *Kedra's own bookkeeping*: which preset the user
picked, which modules they overrode, and what Kedra last applied. It is not a
store for GNOME preferences. GNOME settings live where GNOME puts them; Kedra
records only that it applied a module and at which version.

That boundary is deliberate and load-bearing. See the "ownership" section of
docs/kedra-experience.md: Kedra-managed state must never imply ownership of
unrelated user configuration.

Nothing in this module reads or writes a path it was not given. There is no
implicit fallback to the real home directory, so tests and builds cannot touch
the invoking user's files by accident.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .backends import _is_gsettings_path, spec_for
from .manifests import Report, is_identifier, is_semver
from .settings import SETTING_POLICIES, _is_setting_id
from .values import TypedValue, ValueError_

# 1  selection + applied module records; managed_values reserved and empty.
# 2  managed_values defined: per-setting ownership records.
# 3  managed_values gained `resource` and `member`, which composite resources
#    and list-membership ownership need. Both are optional in the model but the
#    version is bumped anyway: a reader that does not understand `member` would
#    treat a membership record as an ordinary scalar and, on revert, reset the
#    whole `custom-keybindings` key -- deleting every shortcut the user has.
#    Silently misreading a record is exactly what a schema version exists to
#    prevent, so an old reader must refuse rather than guess.
STATE_SCHEMA_VERSION = 3

# Basename inside the state directory. Documented, but every entry point takes
# an explicit path.
STATE_FILENAME = "experience-state.toml"

STATE_KNOWN_TOP_LEVEL = frozenset({"state_schema_version", "selection", "applied"})
SELECTION_KNOWN_KEYS = frozenset({"base_preset", "module_overrides"})
APPLIED_KNOWN_KEYS = frozenset(
    {
        "generation",
        "preset_id",
        "preset_version",
        "experience_schema_version",
        "applied_at",
        "modules",
        "managed_values",
    }
)
APPLIED_MODULE_KNOWN_KEYS = frozenset({"id", "version", "active", "generation"})

MANAGED_VALUE_REQUIRED_KEYS = frozenset(
    {
        "setting_id",
        "backend",
        "target",
        "module_id",
        "module_version",
        "policy",
        "generation",
        "last_applied",
        "original",
    }
)
# Optional, because a record for an ordinary scalar setting has neither.
MANAGED_VALUE_OPTIONAL_KEYS = frozenset({"resource", "member"})
MANAGED_VALUE_KNOWN_KEYS = MANAGED_VALUE_REQUIRED_KEYS | MANAGED_VALUE_OPTIONAL_KEYS
ORIGINAL_KNOWN_KEYS = frozenset({"present", "type", "data"})


class StateError(Exception):
    """Raised when state cannot be parsed into the model at all."""


def default_state_dir(environ: dict | None = None) -> Path:
    """The intended XDG location: $XDG_STATE_HOME/kedra/.

    Falls back to ~/.local/state/kedra per the XDG Base Directory spec. This
    function only *computes* a path -- it never creates or reads it. Callers
    that act on the filesystem must pass a path explicitly, which is what keeps
    the test suite and `makepkg` out of the invoking user's home directory.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME")
    if not base:
        base = str(Path(env.get("HOME", "~")).expanduser() / ".local" / "state")
    return Path(base) / "kedra"


def default_state_path(environ: dict | None = None) -> Path:
    return default_state_dir(environ) / STATE_FILENAME


@dataclass(frozen=True)
class ModuleOverride:
    """One user decision that differs from the base preset."""

    id: str
    enabled: bool


@dataclass(frozen=True)
class AppliedModule:
    """What Kedra last did for one module.

    `version` is the module version that was applied, so a later run can tell
    "this module changed since the user's last apply" from "nothing to do"
    without inspecting any desktop setting.
    """

    id: str
    version: str
    active: bool
    generation: int


@dataclass(frozen=True)
class ManagedValue:
    """One value Kedra has taken ownership of.

    The three-value model this supports:

      original          what was there before Kedra first took ownership
      last_applied      what Kedra wrote most recently
      current_observed  what is there now -- deliberately NOT stored here

    `current_observed` is read live by a backend adapter rather than persisted.
    Persisting it would create a second source of truth that goes stale the
    moment the user changes anything outside Kedra, and a stale copy is exactly
    what would cause a user's edit to be mistaken for Kedra's own value. Reading
    it live is authoritative and costs nothing.

    `original` is deliberately not a snapshot of anything broader. Only targets
    a Kedra module explicitly declares are ever recorded here -- there is no
    dconf dump, no scan of unrelated keys, and no path outside the declared
    target set.
    """

    setting_id: str
    backend: str
    target: str
    module_id: str
    module_version: str
    policy: str
    generation: int
    last_applied: TypedValue | None
    # Absence is a real state: "there was no value here before Kedra", which
    # reverts by unsetting rather than by writing something back.
    original: TypedValue | None = None
    original_present: bool = False
    # The composite resource this record belongs to, when it belongs to one.
    # Records sharing a resource are unmanaged together rather than key by key,
    # so a shortcut the user has edited is left whole instead of dismantled.
    resource: str | None = None
    # For a list-membership claim: the single entry Kedra owns the presence of.
    #
    # Load-bearing on revert, and the reason schema 3 exists. Without it a
    # record on `custom-keybindings` looks like an ordinary scalar, and giving
    # back "what Kedra found" would restore a list snapshot -- deleting every
    # shortcut the user created after Kedra's. With it, a revert removes one
    # entry and leaves the rest of the list exactly as it is.
    member: str | None = None

    @property
    def is_membership(self) -> bool:
        return self.member is not None

    def diverged_from(self, observed: TypedValue | None) -> bool:
        """Has the value moved since Kedra last wrote it?"""
        from .values import values_equal

        return not values_equal(self.last_applied, observed)


@dataclass(frozen=True)
class AppliedRecord:
    """The last successful application.

    `generation` is a plain monotonic counter, incremented once per successful
    apply. It is deliberately not a hash or a timestamp: it must be comparable,
    reproducible in tests, and meaningful when read by a human. Per-module
    generations record when each module was last touched, which may lag the
    top-level generation if a later apply changed nothing for that module.
    """

    generation: int
    preset_id: str
    preset_version: str
    experience_schema_version: int
    applied_at: str
    modules: tuple[AppliedModule, ...] = ()
    # Per-setting ownership records. Empty until a module declares a 'managed'
    # setting -- which none do yet, because no real GNOME values ship.
    managed_values: tuple[ManagedValue, ...] = ()

    def __post_init__(self) -> None:
        # Canonical ordering, so two records with the same content are equal
        # and serialize identically regardless of construction order.
        object.__setattr__(self, "modules", tuple(sorted(self.modules, key=lambda m: m.id)))
        object.__setattr__(
            self,
            "managed_values",
            tuple(sorted(self.managed_values, key=lambda v: v.setting_id)),
        )

    def managed(self, setting_id: str) -> ManagedValue | None:
        for record in self.managed_values:
            if record.setting_id == setting_id:
                return record
        return None


@dataclass(frozen=True)
class UserState:
    """One user's Kedra Experience selection and applied-state record."""

    base_preset: str
    module_overrides: tuple[ModuleOverride, ...] = ()
    applied: AppliedRecord | None = None
    state_schema_version: int = STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Overrides are a set of decisions, not an ordered list. Normalising
        # here means load(save(x)) == x regardless of the order the caller
        # supplied, and keeps on-disk state diff-friendly across rewrites.
        object.__setattr__(
            self, "module_overrides", tuple(sorted(self.module_overrides, key=lambda o: o.id))
        )

    # -- construction -----------------------------------------------------

    @classmethod
    def from_dict(cls, doc: dict) -> "UserState":
        """Build from a parsed TOML document. Shape errors raise StateError.

        Use `validate_state_document` first for a full, collected report;
        this only refuses what it cannot represent.
        """
        if not isinstance(doc, dict):
            raise StateError("state document must be a table")
        selection = doc.get("selection")
        if not isinstance(selection, dict):
            raise StateError("missing required [selection] table")
        base_preset = selection.get("base_preset")
        if not is_identifier(base_preset):
            raise StateError("selection.base_preset is missing or not a valid identifier")

        overrides: list[ModuleOverride] = []
        raw_overrides = selection.get("module_overrides", [])
        if not isinstance(raw_overrides, list):
            raise StateError("selection.module_overrides must be an array of tables")
        for entry in raw_overrides:
            if not isinstance(entry, dict):
                raise StateError("selection.module_overrides entries must be tables")
            if not is_identifier(entry.get("id")) or not isinstance(
                entry.get("enabled"), bool
            ):
                raise StateError(f"malformed module override {entry!r}")
            overrides.append(ModuleOverride(id=entry["id"], enabled=entry["enabled"]))

        applied = None
        raw_applied = doc.get("applied")
        if raw_applied is not None:
            if not isinstance(raw_applied, dict):
                raise StateError("[applied] must be a table")
            modules: list[AppliedModule] = []
            for entry in raw_applied.get("modules", []) or []:
                if not isinstance(entry, dict):
                    raise StateError("applied.modules entries must be tables")
                try:
                    modules.append(
                        AppliedModule(
                            id=entry["id"],
                            version=entry["version"],
                            active=entry["active"],
                            generation=entry["generation"],
                        )
                    )
                except KeyError as exc:
                    raise StateError(f"applied module missing key {exc}") from exc
            raw_managed = raw_applied.get("managed_values") or []
            if not isinstance(raw_managed, list):
                raise StateError("applied.managed_values must be an array of tables")
            managed = [_managed_from_dict(entry) for entry in raw_managed]
            try:
                applied = AppliedRecord(
                    generation=raw_applied["generation"],
                    preset_id=raw_applied["preset_id"],
                    preset_version=raw_applied["preset_version"],
                    experience_schema_version=raw_applied["experience_schema_version"],
                    applied_at=raw_applied["applied_at"],
                    modules=tuple(modules),
                    managed_values=tuple(managed),
                )
            except KeyError as exc:
                raise StateError(f"[applied] missing key {exc}") from exc

        version = doc.get("state_schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise StateError("state_schema_version is missing or not an integer")

        return cls(
            base_preset=base_preset,
            module_overrides=tuple(sorted(overrides, key=lambda o: o.id)),
            applied=applied,
            state_schema_version=version,
        )

    @classmethod
    def load(cls, path: Path) -> "UserState":
        """Read state from an explicit path. Never guesses a location."""
        try:
            with Path(path).open("rb") as fh:
                doc = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise StateError(f"malformed TOML: {exc}") from exc
        except OSError as exc:
            raise StateError(f"unreadable state file: {exc}") from exc
        return cls.from_dict(doc)

    # -- serialization ----------------------------------------------------

    def to_toml(self) -> str:
        """Serialize deterministically.

        Hand-rolled rather than pulling in a TOML writer: the shape is small,
        fully controlled by this module, and adding a dependency to the base
        system to write four tables is not a trade worth making. Only the value
        types this model actually uses are supported.
        """
        lines = [
            "# Kedra Experience — per-user state. Managed by Kedra tooling.",
            "#",
            "# Mutable, user-owned, machine-local. This is NOT distributable profile",
            "# metadata, and NOT a store for GNOME preferences.",
            "",
            f"state_schema_version = {self.state_schema_version}",
            "",
            "[selection]",
            f'base_preset = "{_escape(self.base_preset)}"',
        ]

        for override in sorted(self.module_overrides, key=lambda o: o.id):
            lines += [
                "",
                "[[selection.module_overrides]]",
                f'id = "{_escape(override.id)}"',
                f"enabled = {_bool(override.enabled)}",
            ]

        if self.applied is not None:
            applied = self.applied
            lines += [
                "",
                "[applied]",
                f"generation = {applied.generation}",
                f'preset_id = "{_escape(applied.preset_id)}"',
                f'preset_version = "{_escape(applied.preset_version)}"',
                f"experience_schema_version = {applied.experience_schema_version}",
                f'applied_at = "{_escape(applied.applied_at)}"',
            ]
            for module in applied.modules:
                lines += [
                    "",
                    "[[applied.modules]]",
                    f'id = "{_escape(module.id)}"',
                    f'version = "{_escape(module.version)}"',
                    f"active = {_bool(module.active)}",
                    f"generation = {module.generation}",
                ]
            if not applied.managed_values:
                lines += [
                    "",
                    "# No managed values: no enabled module declares a 'managed' setting.",
                    "# Written as [[applied.managed_values]] entries once one does.",
                ]
            else:
                for record in applied.managed_values:
                    lines += _managed_lines(record)

        return "\n".join(lines) + "\n"

    def save(self, path: Path) -> None:
        """Write to an explicit path, creating parent directories.

        Atomic replacement, in the order that matters:

        1. serialize fully in memory -- a serialization error must not have
           touched the filesystem at all;
        2. write a temporary file in the *same directory*, so the final step is
           a rename within one filesystem and cannot degrade to a copy;
        3. flush and fsync the temporary file, so its contents are durable
           before anything points at them;
        4. `os.replace`, which is atomic: a concurrent reader sees either the
           whole old file or the whole new one, never a truncated mix;
        5. fsync the directory, so the rename itself survives a power loss.

        On any failure the temporary file is removed and the previous state is
        left exactly as it was.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content = self.to_toml()

        # Preserve the mode of an existing state file; otherwise 0o644. This
        # file holds no secrets by construction, so it needs no special mode --
        # but it must not silently become more permissive than it was.
        mode = 0o644
        if path.exists():
            mode = path.stat().st_mode & 0o777

        temporary = path.with_name(path.name + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                # fdopen took ownership of fd; if it failed before that, close.
                raise
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        except OSError:
            # Some filesystems refuse to fsync a directory. The rename is still
            # atomic; only its durability across power loss is weakened.
            pass
        finally:
            os.close(directory)

    # -- transitions ------------------------------------------------------

    def next_generation(self) -> int:
        return 1 if self.applied is None else self.applied.generation + 1

    def with_applied(
        self,
        resolution,
        *,
        experience_schema_version: int,
        applied_at: str | None = None,
        generation: int | None = None,
        managed_values: tuple[ManagedValue, ...] | None = None,
    ) -> "UserState":
        """Return a copy recording `resolution` as newly applied.

        Pure: returns a new state rather than mutating, so a failed apply
        simply never produces one. `applied_at` is injectable to keep tests
        deterministic.

        `generation` is injectable so an executor can compute one transaction
        generation up front and stamp every record it touches with the same
        value. `managed_values` replaces the ownership records wholesale, which
        is what the executor hands over once it knows which writes succeeded.
        """
        generation = self.next_generation() if generation is None else generation
        stamp = applied_at or datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")

        previous = {m.id: m for m in (self.applied.modules if self.applied else ())}
        modules: list[AppliedModule] = []
        for module in resolution.modules:
            was = previous.get(module.id)
            # Only bump a module's generation when something about it actually
            # changed; otherwise it keeps the generation it was last applied at.
            unchanged = (
                was is not None
                and was.version == module.version
                and was.active == module.enabled
            )
            modules.append(
                AppliedModule(
                    id=module.id,
                    version=module.version,
                    active=module.enabled,
                    generation=was.generation if unchanged else generation,
                )
            )

        return replace(
            self,
            applied=AppliedRecord(
                generation=generation,
                preset_id=resolution.preset_id,
                preset_version=resolution.preset_version,
                experience_schema_version=experience_schema_version,
                applied_at=stamp,
                modules=tuple(sorted(modules, key=lambda m: m.id)),
                managed_values=(
                    self.applied.managed_values
                    if (managed_values is None and self.applied is not None)
                    else tuple(managed_values or ())
                ),
            ),
        )


def _managed_from_dict(entry: object) -> ManagedValue:
    """Build a ManagedValue from a parsed TOML table."""
    if not isinstance(entry, dict):
        raise StateError("applied.managed_values entries must be tables")

    missing = sorted(MANAGED_VALUE_REQUIRED_KEYS - entry.keys())
    if missing:
        raise StateError(f"managed value missing key(s): {', '.join(missing)}")

    try:
        last_applied = TypedValue.from_dict(entry["last_applied"])
    except ValueError_ as exc:
        raise StateError(f"managed value '{entry['setting_id']}': last_applied {exc}") from exc

    original_raw = entry["original"]
    if not isinstance(original_raw, dict):
        raise StateError("managed value 'original' must be a table")
    present = original_raw.get("present")
    if not isinstance(present, bool):
        raise StateError("managed value 'original' requires a boolean 'present'")

    original = None
    if present:
        try:
            original = TypedValue.from_dict(
                {"type": original_raw.get("type"), "data": original_raw.get("data")}
            )
        except ValueError_ as exc:
            raise StateError(
                f"managed value '{entry['setting_id']}': original {exc}"
            ) from exc

    return ManagedValue(
        setting_id=entry["setting_id"],
        backend=entry["backend"],
        target=entry["target"],
        module_id=entry["module_id"],
        module_version=entry["module_version"],
        policy=entry["policy"],
        generation=entry["generation"],
        last_applied=last_applied,
        original=original,
        original_present=present,
        resource=entry.get("resource"),
        member=entry.get("member"),
    )


def _managed_lines(record: ManagedValue) -> list[str]:
    """Serialize one managed value deterministically."""
    lines = [
        "",
        "[[applied.managed_values]]",
        f'setting_id = "{_escape(record.setting_id)}"',
        f'backend = "{_escape(record.backend)}"',
        f'target = "{_escape(record.target)}"',
        f'module_id = "{_escape(record.module_id)}"',
        f'module_version = "{_escape(record.module_version)}"',
        f'policy = "{_escape(record.policy)}"',
        f"generation = {record.generation}",
        f"last_applied = {_value_inline(record.last_applied)}",
    ]
    if record.resource is not None:
        lines.append(f'resource = "{_escape(record.resource)}"')
    if record.member is not None:
        lines.append(f'member = "{_escape(record.member)}"')
    if record.original_present and record.original is not None:
        lines.append(
            "original = { present = true, "
            f'type = "{_escape(record.original.type)}", '
            f"data = {_toml_data(record.original.data)} }}"
        )
    else:
        lines.append("original = { present = false }")
    return lines


def _value_inline(value: TypedValue) -> str:
    return f'{{ type = "{_escape(value.type)}", data = {_toml_data(value.data)} }}'


def _toml_data(data) -> str:
    """Render a typed value's data as TOML. Only the types in VALUE_TYPES."""
    if isinstance(data, bool):
        return _bool(data)
    if isinstance(data, (int, float)):
        return repr(data)
    if isinstance(data, str):
        return f'"{_escape(data)}"'
    if isinstance(data, (list, tuple)):
        return "[" + ", ".join(_toml_data(item) for item in data) + "]"
    raise StateError(f"cannot serialize value data {data!r}")


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_state_document(doc: object, path: Path | str, report: Report, tree=None) -> None:
    """Validate a parsed state document, collecting every problem found.

    When `tree` is supplied, preset and module references are checked against
    it as well. Without a tree the document is checked for shape only, so state
    remains validatable on a machine where the manifests are not installed.
    """
    if not isinstance(doc, dict):
        report.error(path, "state document must be a table")
        return

    for key in sorted(doc.keys() - STATE_KNOWN_TOP_LEVEL):
        report.error(
            path,
            f"unknown top-level key '{key}'; this file records Kedra's own selection "
            f"and applied state only",
        )

    version = doc.get("state_schema_version")
    if version is None:
        report.error(path, "missing required key 'state_schema_version'")
    elif not isinstance(version, int) or isinstance(version, bool):
        report.error(path, f"state_schema_version must be an integer, got {version!r}")
    elif version != STATE_SCHEMA_VERSION:
        report.error(
            path,
            f"unsupported state_schema_version {version} "
            f"(this tool speaks {STATE_SCHEMA_VERSION})",
        )

    selection = doc.get("selection")
    if not isinstance(selection, dict):
        report.error(path, "missing required [selection] table")
        return

    for key in sorted(selection.keys() - SELECTION_KNOWN_KEYS):
        report.error(path, f"selection: unknown key '{key}'")

    base_preset = selection.get("base_preset")
    if base_preset is None:
        report.error(path, "selection: missing required key 'base_preset'")
    elif not is_identifier(base_preset):
        report.error(path, f"selection.base_preset {base_preset!r} is not a valid identifier")
    elif tree is not None and base_preset not in tree.presets:
        report.error(path, f"selection.base_preset references nonexistent preset '{base_preset}'")

    overrides = selection.get("module_overrides", [])
    if not isinstance(overrides, list):
        report.error(path, "selection.module_overrides must be an array of tables")
    else:
        seen: set[str] = set()
        for index, entry in enumerate(overrides):
            where = f"selection.module_overrides[{index}]"
            if not isinstance(entry, dict):
                report.error(path, f"{where}: must be a table")
                continue
            for key in sorted(entry.keys() - {"id", "enabled"}):
                report.error(path, f"{where}: unknown key '{key}'")
            module_id = entry.get("id")
            if not is_identifier(module_id):
                report.error(path, f"{where}: missing or invalid module 'id'")
                continue
            if module_id in seen:
                report.error(path, f"{where}: duplicate override for module '{module_id}'")
            seen.add(module_id)
            if not isinstance(entry.get("enabled"), bool):
                report.error(path, f"{where}: 'enabled' must be present and a boolean")
            if tree is not None and module_id not in tree.modules:
                report.error(path, f"{where}: references nonexistent module '{module_id}'")
            elif tree is not None and not tree.modules[module_id]["user_selectable"]:
                # The same rule the resolver enforces, applied to state at rest.
                # A file carrying this override is not merely ineffective -- the
                # resolver refuses it -- it is a file whose author believed they
                # had switched a core module off, and saying so here is how they
                # find out before wondering why nothing changed.
                report.error(
                    path,
                    f"{where}: module '{module_id}' is not user-selectable and "
                    f"cannot be overridden by a selection",
                )

    applied = doc.get("applied")
    if applied is None:
        return
    if not isinstance(applied, dict):
        report.error(path, "[applied] must be a table")
        return

    for key in sorted(applied.keys() - APPLIED_KNOWN_KEYS):
        report.error(path, f"applied: unknown key '{key}'")

    for key in ("generation", "preset_id", "preset_version", "experience_schema_version",
                "applied_at"):
        if key not in applied:
            report.error(path, f"applied: missing required key '{key}'")

    generation = applied.get("generation")
    if generation is not None and (
        not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
    ):
        report.error(path, f"applied.generation must be a positive integer, got {generation!r}")

    if "preset_id" in applied and not is_identifier(applied["preset_id"]):
        report.error(path, f"applied.preset_id {applied['preset_id']!r} is not a valid identifier")
    if "preset_version" in applied and not is_semver(applied["preset_version"]):
        report.error(
            path, f"applied.preset_version {applied['preset_version']!r} is not MAJOR.MINOR.PATCH"
        )
    schema = applied.get("experience_schema_version")
    if schema is not None and (not isinstance(schema, int) or isinstance(schema, bool)):
        report.error(path, "applied.experience_schema_version must be an integer")
    if "applied_at" in applied and not _is_timestamp(applied["applied_at"]):
        report.error(
            path, f"applied.applied_at {applied['applied_at']!r} is not an ISO 8601 timestamp"
        )

    validate_managed_values(applied.get("managed_values"), generation, path, report, tree=tree)

    modules = applied.get("modules", [])
    if not isinstance(modules, list):
        report.error(path, "applied.modules must be an array of tables")
        return

    seen_modules: set[str] = set()
    for index, entry in enumerate(modules):
        where = f"applied.modules[{index}]"
        if not isinstance(entry, dict):
            report.error(path, f"{where}: must be a table")
            continue
        for key in sorted(entry.keys() - APPLIED_MODULE_KNOWN_KEYS):
            report.error(path, f"{where}: unknown key '{key}'")
        for key in sorted(APPLIED_MODULE_KNOWN_KEYS - entry.keys()):
            report.error(path, f"{where}: missing required key '{key}'")

        module_id = entry.get("id")
        if module_id is not None and not is_identifier(module_id):
            report.error(path, f"{where}: module 'id' {module_id!r} is not a valid identifier")
        elif module_id is not None:
            if module_id in seen_modules:
                report.error(path, f"{where}: duplicate entry for module '{module_id}'")
            seen_modules.add(module_id)
            if tree is not None and module_id not in tree.modules:
                report.error(path, f"{where}: references nonexistent module '{module_id}'")

        if "version" in entry and not is_semver(entry["version"]):
            report.error(path, f"{where}: version {entry['version']!r} is not MAJOR.MINOR.PATCH")
        if "active" in entry and not isinstance(entry["active"], bool):
            report.error(path, f"{where}: 'active' must be a boolean")

        module_generation = entry.get("generation")
        if module_generation is not None:
            if (
                not isinstance(module_generation, int)
                or isinstance(module_generation, bool)
                or module_generation < 1
            ):
                report.error(path, f"{where}: 'generation' must be a positive integer")
            elif isinstance(generation, int) and module_generation > generation:
                report.error(
                    path,
                    f"{where}: generation {module_generation} is ahead of "
                    f"applied.generation {generation}",
                )


def validate_managed_values(
    entries: object, generation: object, path: Path | str, report: Report, tree=None
) -> None:
    """Validate the per-setting ownership records.

    With a tree, setting IDs are checked against the settings that actually
    exist. Without one, only the shape is checked -- a user's state must stay
    validatable on a machine where the manifests are not installed.
    """
    if entries is None:
        return
    if not isinstance(entries, list):
        report.error(path, "applied.managed_values must be an array of tables")
        return

    known_settings = _tree_settings(tree)
    seen_ids: set[str] = set()
    seen_targets: dict[str, str] = {}

    for index, entry in enumerate(entries):
        where = f"applied.managed_values[{index}]"
        if not isinstance(entry, dict):
            report.error(path, f"{where}: must be a table")
            continue

        for key in sorted(entry.keys() - MANAGED_VALUE_KNOWN_KEYS):
            report.error(path, f"{where}: unknown key '{key}'")
        for key in sorted(MANAGED_VALUE_REQUIRED_KEYS - entry.keys()):
            report.error(path, f"{where}: missing required key '{key}'")

        setting_id = entry.get("setting_id")
        if setting_id is not None and not _is_setting_id(setting_id):
            report.error(path, f"{where}: setting_id {setting_id!r} is not a valid setting ID")
        elif isinstance(setting_id, str):
            if setting_id in seen_ids:
                report.error(path, f"{where}: duplicate record for setting '{setting_id}'")
            seen_ids.add(setting_id)
            if known_settings is not None and setting_id not in known_settings:
                report.error(
                    path, f"{where}: references nonexistent setting '{setting_id}'"
                )

        backend = entry.get("backend")
        if backend is not None and spec_for(backend) is None:
            report.error(path, f"{where}: unknown backend {backend!r}")

        target = entry.get("target")
        if target is not None and (not isinstance(target, str) or not target):
            report.error(path, f"{where}: target must be a non-empty canonical target string")
        elif isinstance(target, str):
            if target in seen_targets:
                report.error(
                    path,
                    f"{where}: target '{target}' is already claimed by setting "
                    f"'{seen_targets[target]}'",
                )
            else:
                seen_targets[target] = setting_id if isinstance(setting_id, str) else "?"

        module_id = entry.get("module_id")
        if module_id is not None and not is_identifier(module_id):
            report.error(path, f"{where}: module_id {module_id!r} is not a valid identifier")
        elif isinstance(module_id, str) and tree is not None and module_id not in tree.modules:
            report.error(path, f"{where}: references nonexistent module '{module_id}'")

        if "module_version" in entry and not is_semver(entry["module_version"]):
            report.error(
                path,
                f"{where}: module_version {entry['module_version']!r} is not MAJOR.MINOR.PATCH",
            )

        policy = entry.get("policy")
        if policy is not None and policy not in SETTING_POLICIES:
            report.error(path, f"{where}: invalid policy {policy!r}")

        record_generation = entry.get("generation")
        if record_generation is not None:
            if (
                not isinstance(record_generation, int)
                or isinstance(record_generation, bool)
                or record_generation < 1
            ):
                report.error(path, f"{where}: 'generation' must be a positive integer")
            elif isinstance(generation, int) and record_generation > generation:
                report.error(
                    path,
                    f"{where}: generation {record_generation} is ahead of "
                    f"applied.generation {generation}",
                )

        resource = entry.get("resource")
        if resource is not None and not _is_setting_id(resource):
            report.error(path, f"{where}: resource {resource!r} is not a valid resource ID")

        member = entry.get("member")
        if member is not None:
            if not isinstance(member, str) or not member:
                report.error(path, f"{where}: member must be a non-empty string")
            elif not _is_gsettings_path(member):
                report.error(
                    path,
                    f"{where}: member {member!r} is not an absolute GSettings/dconf "
                    f"path; the only list Kedra takes part in holds shortcut paths",
                )

        if "last_applied" in entry:
            try:
                last = TypedValue.from_dict(entry["last_applied"])
            except ValueError_ as exc:
                report.error(path, f"{where}: last_applied {exc}")
            else:
                if isinstance(member, str) and member:
                    # A membership record's last_applied is the membership
                    # claim, not a list snapshot. Anything else means the record
                    # is claiming entries it does not own.
                    if last.type != "as" or tuple(last.data) != (member,):
                        report.error(
                            path,
                            f"{where}: a membership record's last_applied must be "
                            f"exactly ['{member}']; recording the surrounding list "
                            f"would claim ownership of entries Kedra did not write",
                        )

        _validate_original(entry.get("original"), where, path, report)


def _validate_original(original: object, where: str, path, report: Report) -> None:
    if original is None:
        return
    if not isinstance(original, dict):
        report.error(path, f"{where}: 'original' must be a table")
        return
    for key in sorted(original.keys() - ORIGINAL_KNOWN_KEYS):
        report.error(path, f"{where}.original: unknown key '{key}'")

    present = original.get("present")
    if not isinstance(present, bool):
        report.error(path, f"{where}.original: 'present' must be a boolean")
        return

    if not present:
        # "There was nothing here before Kedra": a revert unsets the key, so
        # carrying a value alongside would be contradictory.
        for key in ("type", "data"):
            if key in original:
                report.error(
                    path,
                    f"{where}.original: '{key}' is meaningless when present = false",
                )
        return

    try:
        TypedValue.from_dict({"type": original.get("type"), "data": original.get("data")})
    except ValueError_ as exc:
        report.error(path, f"{where}.original: {exc}")


def _tree_settings(tree) -> set[str] | None:
    if tree is None:
        return None
    from .manifests import module_setting_entries

    ids: set[str] = set()
    for module_id, module in tree.modules.items():
        for entry in module_setting_entries(module_id, module, tree.modules):
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.add(entry["id"])
    return ids


def validate_state_file(path: Path, report: Report, tree=None) -> dict | None:
    """Parse and validate a state file at an explicit path."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        report.error(path, f"malformed TOML: {exc}")
        return None
    except OSError as exc:
        report.error(path, f"unreadable state file: {exc}")
        return None
    validate_state_document(doc, path, report, tree=tree)
    return doc
