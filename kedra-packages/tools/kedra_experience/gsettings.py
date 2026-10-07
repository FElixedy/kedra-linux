"""The production GSettings backend adapter.

The only module in this package that talks to a real desktop configuration
system, and the only one that imports GLib. Everything above it -- the planner,
the ownership rules, the executor, the state model -- stays backend-agnostic and
is exercised end to end against `InMemoryAdapter` with no session at all.

GLib, not a subprocess
----------------------
`Gio.Settings` is used directly through PyGObject rather than by driving the
`gsettings` command line, and the difference is not stylistic:

* **Types survive.** `gsettings get` prints GVariant *text* which has to be
  parsed back, and the round trip is lossy in exactly the places that matter --
  `10` is an integer or a double depending on the key it came from, and an
  empty array prints without its element type. `Gio.Settings.get_value()`
  returns a typed GVariant.
* **Values are never re-parsed from a command line.** There is no string
  concatenation and no shell, so a value cannot be reinterpreted as syntax.
* **"Is a value explicitly stored?" is answerable.** `gsettings get` prints the
  effective value and offers no way to ask whether it came from a user write or
  from a default. `g_settings_get_user_value()` answers exactly that. Without
  it, `original` capture and revert are both wrong -- see EXPLICIT_VALUE_NOTE.
* **Failures are structured.** A write that fails because a key is locked down
  is a return value, not a message on stderr to pattern-match.

Aborts, and why every access is guarded
---------------------------------------
GLib treats a schema or key that does not exist as a *programmer* error and
calls `g_error()`, which aborts the process -- there is no exception to catch.
Four calls in this file would abort if made blindly:

    Gio.Settings.new(schema)                schema not installed
    settings.get_value(key)                 key not in the schema
    Gio.Settings.new_full(schema, ..., None)  schema is relocatable
    Gio.Settings.new_full(schema, ..., path)  schema is *not* relocatable

So nothing is constructed before the schema is looked up through
`Gio.SettingsSchemaSource`, the key is checked with `SettingsSchema.has_key()`,
and the schema's own path is compared against the target's. Each of those turns
an abort into a `BackendError`, which is what the executor is built to handle.
"""

from __future__ import annotations

from .backends import (
    BackendAdapter,
    BackendError,
    GSettingsSpec,
    GSettingsTarget,
    parse_gsettings_target,
)
from .values import TypedValue

# The distinction this adapter exists to preserve, stated once so it can be
# quoted where it matters:
#
#     effective value == default   does NOT imply   no explicit value is stored
#
# A user may set a key to precisely the value it would have shown anyway. If
# Kedra reads the effective value and calls it "unset", it records
# `original.present = false` and a later revert *unsets a key the user had set*
# -- silently discarding an explicit choice that happened to agree with the
# default at the time. It also gets the mirror case wrong: after a vendor
# default changes underneath, a key the user never touched would start looking
# like an explicit choice.
EXPLICIT_VALUE_NOTE = (
    "an effective value equal to the default does not mean the key is unset; "
    "explicit storage is read with g_settings_get_user_value()"
)

# GVariant type strings this adapter can convert, mapped to the reader used on
# a GVariant. Exactly the vocabulary in `values` -- a key of any other type is
# refused rather than approximated, because a lossy read would be recorded as
# `original` and later written back as the user's own value.
_READERS = {
    "b": lambda variant: variant.get_boolean(),
    "i": lambda variant: variant.get_int32(),
    "d": lambda variant: variant.get_double(),
    "s": lambda variant: variant.get_string(),
    "as": lambda variant: tuple(variant.get_strv()),
    "ai": lambda variant: tuple(variant),
}


def _gio():
    """Import GLib lazily, and turn its absence into a BackendError.

    Deferred so that importing this module -- which the CLI does unconditionally
    -- never requires PyGObject. Every offline test, every manifest validation
    and every `makepkg check()` on a machine without GNOME keeps working; only
    actually constructing an adapter needs GLib.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import GLib, Gio
    except (ImportError, ValueError) as exc:  # pragma: no cover - environment
        raise BackendError(
            f"the GSettings backend needs PyGObject with Gio 2.0 available: {exc}"
        ) from exc
    return Gio, GLib


class GSettingsAdapter(BackendAdapter):
    """Reads and writes real GSettings keys through GLib.

    `schema_dir` selects an alternative compiled-schema directory, and is how
    the integration tests run against schemas compiled into a temporary
    directory instead of the system ones. `backend` accepts a `Gio.SettingsBackend`
    so a test can bind to an isolated store; passing None uses whatever GLib
    resolves, which on a GNOME session is dconf.

    Neither parameter has a default that reaches into a live session by
    accident: with no arguments the adapter is the system one, which is the
    honest thing for a production adapter to be, and no code path in this
    repository constructs it that way.
    """

    def __init__(self, *, schema_dir: str | None = None, backend=None, sync: bool = True):
        self.spec = GSettingsSpec()
        self._schema_dir = schema_dir
        self._backend = backend
        self._sync = sync
        self._source = None
        self._cache: dict[str, object] = {}

    # -- schema resolution -------------------------------------------------

    def _schema_source(self):
        if self._source is not None:
            return self._source
        Gio, _ = _gio()
        if self._schema_dir is None:
            source = Gio.SettingsSchemaSource.get_default()
            if source is None:
                raise BackendError(
                    "no GSettings schemas are installed on this system"
                )
        else:
            try:
                source = Gio.SettingsSchemaSource.new_from_directory(
                    self._schema_dir, None, True
                )
            except Exception as exc:  # GLib.Error
                raise BackendError(
                    f"cannot read compiled GSettings schemas from "
                    f"{self._schema_dir!r}: {exc}"
                ) from exc
        self._source = source
        return source

    def _settings_for(self, parsed: GSettingsTarget):
        """A `Gio.Settings` for one target, or a BackendError explaining why not.

        Every abort GLib would perform is pre-empted here.
        """
        cache_key = f"{parsed.schema}\0{parsed.path or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        Gio, _ = _gio()
        # recursive=True so a schema defined in an included source is found.
        schema = self._schema_source().lookup(parsed.schema, True)
        if schema is None:
            raise BackendError(
                f"GSettings schema {parsed.schema!r} is not installed"
            )

        declared_path = schema.get_path()
        if declared_path is None and parsed.path is None:
            raise BackendError(
                f"GSettings schema {parsed.schema!r} is relocatable and has no path "
                f"of its own; a target for it must supply one"
            )
        if declared_path is not None and parsed.path is not None:
            raise BackendError(
                f"GSettings schema {parsed.schema!r} declares its own path "
                f"{declared_path!r}; a target for it must not supply {parsed.path!r}"
            )

        try:
            settings = Gio.Settings.new_full(schema, self._backend, parsed.path)
        except Exception as exc:  # GLib.Error
            raise BackendError(
                f"cannot open GSettings for schema {parsed.schema!r} at "
                f"path {parsed.path!r}: {exc}"
            ) from exc

        self._cache[cache_key] = settings
        return settings

    def _resolve(self, target: str):
        """(Gio.Settings, key, expected GVariant type string) for a target."""
        parsed = parse_gsettings_target(target)
        settings = self._settings_for(parsed)

        schema = settings.get_property("settings-schema")
        if not schema.has_key(parsed.key):
            raise BackendError(
                f"GSettings schema {parsed.schema!r} has no key {parsed.key!r}"
            )

        type_string = schema.get_key(parsed.key).get_value_type().dup_string()
        if type_string not in _READERS:
            raise BackendError(
                f"{target}: key type {type_string!r} is outside the value "
                f"vocabulary Kedra can represent exactly "
                f"(supported: {', '.join(sorted(_READERS))})"
            )
        return settings, parsed.key, type_string

    # -- BackendAdapter ----------------------------------------------------

    def read(self, target: str) -> TypedValue | None:
        """The explicitly stored value, or None when the key is at a default.

        `g_settings_get_user_value()` is the whole mechanism: it consults the
        writable layer only, returning NULL when nothing has been written there,
        and it is unaffected by what the schema default or a vendor override
        happens to be. See EXPLICIT_VALUE_NOTE.
        """
        settings, key, type_string = self._resolve(target)
        try:
            variant = settings.get_user_value(key)
        except Exception as exc:  # GLib.Error
            raise BackendError(f"{target}: cannot read stored value: {exc}") from exc
        if variant is None:
            return None
        return _to_typed(variant, type_string, target)

    def read_effective(self, target: str) -> TypedValue | None:
        """The value in force -- explicit if there is one, otherwise inherited."""
        settings, key, type_string = self._resolve(target)
        try:
            variant = settings.get_value(key)
        except Exception as exc:  # GLib.Error
            raise BackendError(f"{target}: cannot read value: {exc}") from exc
        return _to_typed(variant, type_string, target)

    def has_user_value(self, target: str) -> bool:
        """Whether anything is explicitly stored at this target.

        Equivalent to `read(target) is not None`, named because the question is
        asked on its own -- and because a caller that spells it as a comparison
        against the effective value has already got it wrong.
        """
        return self.read(target) is not None

    def apply(self, target: str, value: TypedValue) -> None:
        settings, key, type_string = self._resolve(target)

        if value.type != type_string:
            raise BackendError(
                f"{target}: key expects type {type_string!r} but the value to "
                f"write is {value.type!r}"
            )

        # Asked before writing so a locked-down key produces an explanation
        # rather than a bare False from set_value().
        if not settings.is_writable(key):
            raise BackendError(
                f"{target}: key is not writable (locked by a system policy or a "
                f"read-only backend)"
            )

        variant = _to_variant(value, target)
        try:
            written = settings.set_value(key, variant)
        except Exception as exc:  # GLib.Error
            raise BackendError(f"{target}: write failed: {exc}") from exc
        if not written:
            raise BackendError(f"{target}: backend refused the write")
        self._flush()

    def reset(self, target: str) -> None:
        """Unset the key, so it reads as its default again.

        Resetting an already-unset key is not an error: the post-condition is
        what matters, and that is what makes retrying a failed revert safe.
        """
        settings, key, _ = self._resolve(target)
        if not settings.is_writable(key):
            raise BackendError(
                f"{target}: key is not writable, so it cannot be reset"
            )
        try:
            settings.reset(key)
        except Exception as exc:  # GLib.Error
            raise BackendError(f"{target}: reset failed: {exc}") from exc
        self._flush()

    def _flush(self) -> None:
        """Push queued writes out to the backend before returning.

        GSettings batches writes and delivers them asynchronously. Without this
        the executor could record a value as applied, have the process exit, and
        leave the write undelivered -- state claiming something the desktop
        never received, which is precisely the failure mode the execution layer
        is built to avoid.
        """
        if not self._sync:
            return
        Gio, _ = _gio()
        Gio.Settings.sync()


def _to_typed(variant, type_string: str, target: str) -> TypedValue:
    reader = _READERS.get(type_string)
    if reader is None:  # pragma: no cover - _resolve rejects these first
        raise BackendError(f"{target}: unsupported GVariant type {type_string!r}")

    actual = variant.get_type_string()
    if actual != type_string:
        raise BackendError(
            f"{target}: stored value has type {actual!r} but the schema declares "
            f"{type_string!r}"
        )
    return TypedValue(type=type_string, data=reader(variant))


def _to_variant(value: TypedValue, target: str):
    problems = value.problems()
    if problems:
        raise BackendError(f"{target}: {problems[0]}")

    _, GLib = _gio()
    data = value.data
    if value.type == "b":
        return GLib.Variant.new_boolean(bool(data))
    if value.type == "i":
        return GLib.Variant.new_int32(int(data))
    if value.type == "d":
        return GLib.Variant.new_double(float(data))
    if value.type == "s":
        return GLib.Variant.new_string(str(data))
    if value.type == "as":
        return GLib.Variant("as", [str(item) for item in data])
    if value.type == "ai":
        return GLib.Variant("ai", [int(item) for item in data])
    raise BackendError(  # pragma: no cover - _resolve rejects these first
        f"{target}: value type {value.type!r} has no GVariant form"
    )


__all__ = [
    "EXPLICIT_VALUE_NOTE",
    "GSettingsAdapter",
]
