"""Backend abstraction for declarative settings.

Two deliberately separate halves:

`BackendSpec`
    Declarative only. Validates a setting's target and value, and reduces a
    target to a canonical identity string. No I/O, ever. This is what the
    manifest validator and the planner use, which is *why* the planner
    structurally cannot touch a GNOME session -- it has no method that could.

`BackendAdapter`
    The side-effecting half: read, read_effective, apply, reset. The production
    GSettings implementation lives in `gsettings` and is the only place in this
    package that talks to GLib; the double below is what every offline test
    runs against.

Only one backend is defined (`gsettings`) because only one is needed. The
abstraction exists to keep GNOME concepts -- schema IDs, dconf paths -- out of
the generic layer, not to speculate about backends nobody has asked for.

Relocatable schemas
-------------------
A GSettings schema is normally installed at a fixed path, and naming the schema
names the storage location. A *relocatable* schema declares no path: it is a
shape that may be instantiated many times, each instance at a path chosen by
whoever creates it. GNOME's custom keybindings are the case Kedra has --
`org.gnome.settings-daemon.plugins.media-keys.custom-keybinding` is one shape
with one instance per shortcut.

So a target is `schema` + `key` for a fixed schema and `schema` + `path` + `key`
for a relocatable one, and the canonical identity string must carry the path or
two shortcuts would be indistinguishable to the ownership records. Which schemas
are relocatable is *declared data* here (RELOCATABLE_SCHEMAS), not discovered by
reading the installed schemas: the declarative half performs no I/O, and a
manifest must validate identically on a machine with no GNOME installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .values import TypedValue, values_equal

TARGET_KNOWN_KEYS: dict[str, frozenset] = {
    "gsettings": frozenset({"schema", "key", "path"}),
}

# Relocatable GSettings schemas Kedra knows about, i.e. schemas whose instances
# live at a path the caller chooses. A target naming one of these *requires* a
# path; a target naming anything else must not carry one.
#
# A closed set rather than a lookup, for the same reason CONDITION_TYPES is a
# closed set: this half of the backend does no I/O, so it cannot ask GLib
# whether a schema declares a path, and a manifest must validate the same way on
# a build machine with no GNOME schemas installed. Adding an entry is a
# deliberate edit made with a real setting in hand.
RELOCATABLE_SCHEMAS = frozenset(
    {
        # One instance per GNOME custom keyboard shortcut. See `shortcuts`.
        "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
    }
)


@dataclass(frozen=True)
class GSettingsTarget:
    """A canonical target string taken back apart.

    The adapter needs the three components; everything above it passes the
    canonical string around as an opaque identity. Parsing happens in exactly
    one place so the two spellings cannot drift.
    """

    schema: str
    key: str
    path: str | None = None

    @property
    def relocatable(self) -> bool:
        return self.path is not None


class BackendSpec(ABC):
    """Declarative contract for one settings backend. No side effects."""

    name: str

    @abstractmethod
    def target_problems(self, target: object) -> list[str]:
        """Every reason this target is invalid. Empty when it is fine."""

    @abstractmethod
    def canonical_target(self, target: dict) -> str:
        """A stable identity string for this target.

        Used for duplicate detection across the tree, for the key of an
        observed-value fixture, and as the `target` field of a managed record.
        Must be stable across releases: managed records refer to it.
        """

    def value_problems(self, value: TypedValue) -> list[str]:
        """Backend-specific value constraints, on top of the type check."""
        return value.problems()

    def values_equal(self, left: TypedValue | None, right: TypedValue | None) -> bool:
        return values_equal(left, right)


class GSettingsSpec(BackendSpec):
    """GSettings/dconf. Declarative validation only -- writes nothing."""

    name = "gsettings"

    def target_problems(self, target: object) -> list[str]:
        if not isinstance(target, dict):
            return ["target must be a table with 'schema' and 'key'"]

        problems: list[str] = []
        for key in sorted(target.keys() - TARGET_KNOWN_KEYS["gsettings"]):
            problems.append(f"target has unknown key '{key}'")

        schema = target.get("schema")
        if schema is None:
            problems.append("target is missing 'schema'")
        elif not _is_schema_id(schema):
            # Spelled out because the shapes that get mistakenly written here
            # are a command line and a filesystem path, and both are rejected
            # by the same check that accepts a dotted schema ID.
            problems.append(
                f"target.schema {schema!r} is not a valid GSettings schema ID "
                f"(dotted reverse-DNS such as 'org.gnome.mutter'; a command "
                f"line, a dconf path or a binary path is not one)"
            )

        key_name = target.get("key")
        if key_name is None:
            problems.append("target is missing 'key'")
        elif not _is_key_name(key_name):
            problems.append(f"target.key {key_name!r} is not a valid GSettings key name")

        problems += self._path_problems(target, schema)
        return problems

    def _path_problems(self, target: dict, schema: object) -> list[str]:
        """Whether a path is required, forbidden, and well-formed."""
        path = target.get("path")
        relocatable = isinstance(schema, str) and schema in RELOCATABLE_SCHEMAS

        if path is None:
            if relocatable:
                return [
                    f"target.schema {schema!r} is a relocatable schema and has no path "
                    f"of its own, so the target must supply 'path'"
                ]
            return []

        problems: list[str] = []
        if not _is_gsettings_path(path):
            problems.append(
                f"target.path {path!r} is not an absolute GSettings/dconf path "
                f"(it must start and end with '/', contain no empty or '..' "
                f"segments, and use only letters, digits, '-' and '_')"
            )
        if isinstance(schema, str) and not relocatable:
            problems.append(
                f"target.path is only valid for a relocatable schema; {schema!r} "
                f"declares its own path, so supplying one would either duplicate "
                f"or contradict it"
            )
        return problems

    def canonical_target(self, target: dict) -> str:
        """`gsettings:SCHEMA:KEY`, or `gsettings:SCHEMA:PATH:KEY` when relocatable.

        The path is part of the identity, not decoration: two instances of one
        relocatable schema are two different values, and a managed record that
        could not tell them apart would revert the wrong shortcut. Fixed-schema
        targets keep their original three-part spelling so records written
        before relocatable support existed still resolve.
        """
        path = target.get("path")
        if path:
            return f"gsettings:{target['schema']}:{path}:{target['key']}"
        return f"gsettings:{target['schema']}:{target['key']}"


def parse_gsettings_target(canonical: str) -> GSettingsTarget:
    """Take a canonical gsettings target string back apart.

    The inverse of `GSettingsSpec.canonical_target`, and unambiguous because
    none of the three components may contain a colon: a schema ID and a key name
    are restricted alphabets, and a path segment is too.
    """
    if not isinstance(canonical, str):
        raise BackendError(f"target must be a string, got {canonical!r}")

    parts = canonical.split(":")
    if len(parts) not in (3, 4) or parts[0] != "gsettings":
        raise BackendError(
            f"malformed gsettings target {canonical!r} (expected "
            f"'gsettings:SCHEMA:KEY' or 'gsettings:SCHEMA:PATH:KEY')"
        )

    if len(parts) == 3:
        _, schema, key = parts
        path = None
    else:
        _, schema, path, key = parts

    if not _is_schema_id(schema):
        raise BackendError(f"target {canonical!r} has an invalid schema ID {schema!r}")
    if not _is_key_name(key):
        raise BackendError(f"target {canonical!r} has an invalid key name {key!r}")
    if path is not None and not _is_gsettings_path(path):
        raise BackendError(f"target {canonical!r} has an invalid path {path!r}")

    return GSettingsTarget(schema=schema, key=key, path=path)


def _is_schema_id(value: object) -> bool:
    """Dotted reverse-DNS form, e.g. org.gnome.desktop.wm.keybindings."""
    if not isinstance(value, str) or not value:
        return False
    segments = value.split(".")
    if len(segments) < 2:
        return False
    return all(_is_schema_segment(segment) for segment in segments)


def _is_schema_segment(segment: str) -> bool:
    if not segment or not segment[0].isalpha():
        return False
    return all(c.isalnum() or c in "-_" for c in segment)


def _is_key_name(value: object) -> bool:
    """Lowercase, dash-separated, starting with a letter."""
    if not isinstance(value, str) or not value:
        return False
    if not value[0].isalpha():
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in value)


def _is_gsettings_path(value: object) -> bool:
    """An absolute GSettings path: leading and trailing '/', no empty segments.

    GLib itself requires the leading and trailing slash and forbids `//`; the
    alphabet restriction on top of that is Kedra's, and deliberately narrow.
    A path is an identity Kedra stores forever, and the shapes that would
    otherwise slip through -- a relative path, a command line, a filesystem path
    with a `..` in it -- are all things a target must never contain.
    """
    if not isinstance(value, str) or not value:
        return False
    if not value.startswith("/") or not value.endswith("/"):
        return False
    if "//" in value:
        return False
    segments = value.strip("/").split("/")
    if not segments or any(not segment for segment in segments):
        return False
    return all(_is_path_segment(segment) for segment in segments)


def _is_path_segment(segment: str) -> bool:
    return bool(segment) and all(c.isalnum() or c in "-_" for c in segment)


SPECS: dict[str, BackendSpec] = {
    spec.name: spec for spec in (GSettingsSpec(),)
}


def spec_for(backend: str) -> BackendSpec | None:
    return SPECS.get(backend)


# ---------------------------------------------------------------------------
# List membership
# ---------------------------------------------------------------------------
#
# Two pure functions, and they are the whole of Kedra's collection support. A
# generic collection framework is deliberately not built: the one list Kedra
# takes part in is `custom-keybindings`, and what it needs is exactly "make sure
# my entry is in it" and "take my entry out of it, and nothing else".


def list_with_member(current: tuple[str, ...], member: str) -> tuple[str, ...]:
    """`current` with `member` present exactly once, appended if it was absent.

    Order is preserved and existing entries are never rewritten, so a user's
    own shortcuts keep the positions they had. Appending rather than inserting
    means a repeat apply is a no-op instead of shuffling the list.
    """
    if member in current:
        return tuple(current)
    return tuple(current) + (member,)


def list_without_member(current: tuple[str, ...], member: str) -> tuple[str, ...]:
    """`current` with every occurrence of `member` removed, order preserved.

    Every occurrence, not the first: a list that somehow acquired a duplicate
    must not be left half-cleaned, and removing all copies of the one entry
    Kedra owns still cannot touch an entry it does not.
    """
    return tuple(item for item in current if item != member)


class BackendError(Exception):
    """A backend mutation failed.

    Adapters raise this (and only this) for expected failures -- a key that
    cannot be written, a schema that is not installed. The executor treats it
    as a failed write and, critically, does not record the value as applied.
    """


class BackendAdapter(ABC):
    """The side-effecting half of a backend.

    The complete mutation contract the executor depends on: read a target's
    explicit value, read its effective value, write a typed value, unset a
    target. No GSettings vocabulary -- the executor never learns what a schema
    ID is.
    """

    spec: BackendSpec

    @abstractmethod
    def read(self, target: str) -> TypedValue | None:
        """The *explicit* value stored at a canonical target, or None.

        None means the target carries no explicitly stored value and is showing
        a default -- a schema default or a vendor override. That is distinct
        from any concrete value, and the distinction survives into `original`
        on a managed record.

        Deliberately not the effective value. An effective value equal to the
        default does **not** imply that nothing is stored: a user may have
        explicitly set a key to the value it would have had anyway, and a
        revert must unset that rather than leave it behind. Confusing the two
        is how a configurator quietly acquires ownership of a value it never
        wrote. See `read_effective` for the other question.
        """

    @abstractmethod
    def read_effective(self, target: str) -> TypedValue | None:
        """The value in force at a target, explicit or inherited.

        What the desktop actually behaves as. Needed where Kedra has to reason
        about a value it does not own the whole of -- the list-membership case,
        where the entries around Kedra's own must be preserved exactly whether
        or not anyone ever set them explicitly.

        None only when the target has no value at all, which for a schema-backed
        key cannot normally happen.
        """

    @abstractmethod
    def apply(self, target: str, value: TypedValue) -> None:
        """Write a value. Raises BackendError on failure."""

    @abstractmethod
    def reset(self, target: str) -> None:
        """Return the target to its default (unset).

        Raises BackendError on failure. Resetting an already-unset target is
        not an error -- the post-condition is what matters, which is what makes
        a retry after a partial failure safe.
        """


class InMemoryAdapter(BackendAdapter):
    """Test double. Holds values in a dict; touches nothing on the system.

    Supports everything the executor tests need: explicit values, the unset
    state, per-target defaults so `read` and `read_effective` can differ,
    injected write and reset failures, and a full operation log.
    """

    def __init__(
        self,
        spec: BackendSpec | None = None,
        values: dict | None = None,
        *,
        defaults: dict | None = None,
        fail_apply: set[str] | None = None,
        fail_reset: set[str] | None = None,
        fail_read: set[str] | None = None,
    ):
        self.spec = spec or GSettingsSpec()
        self._values: dict[str, TypedValue] = dict(values or {})
        # What a target reads as when nothing explicit is stored. The fake has
        # no schemas, so the defaults it would inherit are injected instead.
        self._defaults: dict[str, TypedValue] = dict(defaults or {})
        self.operations: list[tuple[str, str]] = []
        # Targets whose mutation raises. Mutable so a test can inject a failure,
        # observe it, clear it and retry -- which is how retry-safety is tested.
        self.fail_apply: set[str] = set(fail_apply or ())
        self.fail_reset: set[str] = set(fail_reset or ())
        self.fail_read: set[str] = set(fail_read or ())

    def read(self, target: str) -> TypedValue | None:
        self.operations.append(("read", target))
        if target in self.fail_read:
            raise BackendError(f"injected read failure for {target}")
        return self._values.get(target)

    def read_effective(self, target: str) -> TypedValue | None:
        self.operations.append(("read-effective", target))
        if target in self.fail_read:
            raise BackendError(f"injected read failure for {target}")
        if target in self._values:
            return self._values[target]
        return self._defaults.get(target)

    def apply(self, target: str, value: TypedValue) -> None:
        self.operations.append(("apply", target))
        if target in self.fail_apply:
            raise BackendError(f"injected write failure for {target}")
        self._values[target] = value

    def reset(self, target: str) -> None:
        self.operations.append(("reset", target))
        if target in self.fail_reset:
            raise BackendError(f"injected reset failure for {target}")
        self._values.pop(target, None)

    def snapshot(self) -> dict[str, TypedValue]:
        """Current contents, for assertions. Not an operation."""
        return dict(self._values)

    def is_unset(self, target: str) -> bool:
        return target not in self._values

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [op for op in self.operations if op[0] in ("apply", "reset")]
