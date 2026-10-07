"""Generation of GSettings vendor-default (`.gschema.override`) files.

The *only* delivery route for a `policy = "default"` setting. A vendor default
is not written into anybody's dconf: it changes what the key reads as when the
user has set nothing, and it disappears the moment the package that supplied it
is removed. That is precisely the behaviour the "default" policy promises, so
the two are implemented as one thing.

Single source of truth
----------------------
The module manifests are the source; this file renders them. The override that
ships is *generated* at package time from the same declarations the planner
reads, so there is no second copy to keep in step -- a shortcut cannot be
changed in one place and not the other, because there is only one place.

Rendering only. Nothing here reads a schema, runs `glib-compile-schemas`, or
touches a session; it turns declarations into text and stops. Whether that text
compiles against real GNOME schemas is checked by the tests and by makepkg.

Format
------
A `.gschema.override` is a GKeyFile whose group names are schema IDs and whose
values are GVariant text. `glib-compile-schemas` reads each value with
`g_key_file_get_value()` -- the *raw* line, with no GKeyFile unescaping -- and
hands it to `g_variant_parse()` with the key's declared type. So the value must
be valid GVariant text and must not span lines; it must not be GKeyFile-escaped.

Relocatable schemas are deliberately unsupported here, and that is a semantic
limit rather than a missing feature -- see `RELOCATABLE_SCHEMA_NOTE`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .settings import SettingDeclaration, build_declaration
from .values import TypedValue

# Bumped when the *rendered layout* changes in a way that would make a
# byte-comparison test fail for a reason other than a manifest edit.
#
# 2  Every section is emitted twice, generically and desktop-scoped. See
#    DESKTOP_SCOPE.
OVERRIDE_FORMAT_VERSION = 2

# One override file per module, installed as
# /usr/share/glib-2.0/schemas/95_kedra-experience-<module>.gschema.override by
# that module's own package.
#
# The numeric prefix is load order, not decoration: glib-compile-schemas sorts
# override files by name and lets later ones win. GNOME ships its own vendor
# defaults as `00_org.gnome.shell.gschema.override`, so anything Kedra means to
# override must sort after it. 95 leaves room below for other Kedra layers and
# above for a site-local override that must beat the distro.
#
# One file per module rather than one for the whole tree, because the file is
# the delivery unit: a module's defaults must be installable and *removable* on
# their own, which is what makes "Kedra without Workflow" a real configuration
# rather than a setting that gets applied and then undone.
OVERRIDE_FILENAME_PREFIX = "95_kedra-experience-"
OVERRIDE_FILENAME_SUFFIX = ".gschema.override"

SCHEMAS_DIR = "/usr/share/glib-2.0/schemas"

# The desktop every section is *additionally* emitted for.
#
# Filename order alone is not enough, and assuming it is would ship a file that
# silently does nothing. GLib resolves a key's default in two steps: a
# desktop-scoped override (a `[schema:DESKTOP]` group, matched against any
# component of XDG_CURRENT_DESKTOP) beats a generic one *regardless of which
# file each came from*. GNOME ships
#
#     [org.gnome.mutter:GNOME]
#     dynamic-workspaces=true
#
# in 00_org.gnome.shell.gschema.override, so a plain `[org.gnome.mutter]` group
# in a 95_ file loses in a GNOME session -- while appearing to win everywhere a
# developer is likely to check.
#
# So every section is emitted twice: generic, and scoped to GNOME. The scoped
# copy wins the desktop-scoped comparison on filename order; the generic copy
# means the value does not depend on XDG_CURRENT_DESKTOP being set at all. Doing
# this uniformly rather than only where a clash exists today means a future
# GNOME release that starts scoping some other key cannot quietly take it back.
#
# Verified against GLib 2.88 rather than assumed; see
# tests/test_vendor_defaults.py::DesktopScopePrecedenceTests.
DESKTOP_SCOPE = "GNOME"

# Why a GNOME custom keybinding cannot be a vendor default.
#
# GNOME represents a custom shortcut as an instance of the *relocatable* schema
# org.gnome.settings-daemon.plugins.media-keys.custom-keybinding, living at a
# per-shortcut dconf path, plus that path's presence in the `custom-keybindings`
# list. An override file addresses a schema by ID and cannot name a path: a
# section for a relocatable schema is accepted by glib-compile-schemas, but the
# defaults it sets apply to *every* instance of that schema at every path.
#
# For a custom keybinding that is actively harmful. Defaulting `binding` would
# change the fallback binding of every custom shortcut the user has -- including
# ones they created themselves -- for any of them that has not had `binding`
# explicitly written. A per-path value is therefore not expressible as a vendor
# default, and a shortcut that needs one must be `policy = "managed"`.
#
# Verified empirically against GLib 2.88 rather than assumed; see
# tests/test_vendor_defaults.py::RelocatableSchemaLimitTests.
RELOCATABLE_SCHEMA_NOTE = (
    "a relocatable schema's override applies to every instance at every path, "
    "so per-path state cannot be expressed as a vendor default"
)

HEADER_TEMPLATE = """\
# Kedra Experience -- GSettings vendor defaults for the '{module_id}' module
#
# GENERATED FILE. Do not edit.
#
# Rendered from the `policy = "default"` setting declarations in
# /usr/share/kedra/experience/modules/{module_id}.toml. Edit the manifest and
# rebuild; editing this file only makes the two disagree.
#
# These are *defaults*, not settings. Nothing here is written to any user's
# dconf, so an explicit user value always wins and always survives a Kedra
# update. Removing the package that installs this file restores upstream GNOME
# defaults for every key below that the user has not explicitly set.
#
# Each section appears twice, once plain and once as [schema:{scope}]. That is
# required, not redundant: a desktop-scoped override beats a generic one no
# matter which file it came from, and GNOME ships desktop-scoped defaults of its
# own. The scoped copy wins on filename order; the generic copy means the value
# does not depend on XDG_CURRENT_DESKTOP being set.
#
# override_format_version: {version}
"""


class OverrideError(Exception):
    """Raised when a declaration cannot be rendered as a vendor default."""


@dataclass(frozen=True)
class OverrideEntry:
    """One `key=value` line, with the declaration it came from."""

    schema: str
    key: str
    value: TypedValue
    setting_id: str
    module_id: str

    def rendered(self) -> str:
        return f"{self.key}={gvariant_text(self.value)}"


def override_filename(module_id: str) -> str:
    """The file one module's vendor defaults are installed as."""
    return f"{OVERRIDE_FILENAME_PREFIX}{module_id}{OVERRIDE_FILENAME_SUFFIX}"


def vendor_default_declarations(tree, module_id: str | None = None) -> list[SettingDeclaration]:
    """Every `policy = "default"` gsettings declaration, optionally for one module.

    Module *activation* is irrelevant here and must stay irrelevant: a vendor
    default is delivered by a package, and which packages a profile installs is
    resolved separately (see `packages`). Rendering the file from a resolution
    instead would make the shipped override depend on whoever built it.
    """
    declarations: list[SettingDeclaration] = []
    for candidate in sorted(tree.modules):
        if module_id is not None and candidate != module_id:
            continue
        module = tree.modules[candidate]
        for entry in module.get("settings", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("policy") != "default" or entry.get("backend") != "gsettings":
                continue
            declarations.append(
                build_declaration(entry, candidate, module.get("version", "0.0.0"))
            )
    return declarations


def modules_with_vendor_defaults(tree) -> list[str]:
    """Module IDs that render a non-empty override file, sorted.

    This is the list of modules that need a defaults package. A module with no
    `default` settings deliberately produces nothing rather than an empty file:
    an empty override is a package that exists for no reason and a removal that
    changes nothing.
    """
    return sorted({d.module_id for d in vendor_default_declarations(tree)})


def entries_for(declarations: list[SettingDeclaration]) -> list[OverrideEntry]:
    """Turn declarations into override entries, rejecting what cannot ship.

    Two declarations for the same schema+key would silently produce one line;
    the manifest validator already forbids duplicate targets tree-wide, and this
    re-checks rather than trusting it, because the failure mode is a shortcut
    quietly vanishing from a built package.
    """
    entries: list[OverrideEntry] = []
    seen: dict[tuple[str, str], str] = {}

    for declaration in declarations:
        schema = declaration.target.get("schema")
        key = declaration.target.get("key")
        if not isinstance(schema, str) or not isinstance(key, str):
            raise OverrideError(
                f"setting '{declaration.id}': gsettings target needs 'schema' and 'key'"
            )
        if "path" in declaration.target:
            raise OverrideError(
                f"setting '{declaration.id}': targets a relocatable schema path, "
                f"which cannot be a vendor default -- {RELOCATABLE_SCHEMA_NOTE}; "
                f"such a setting must use policy 'managed'"
            )

        owner = seen.get((schema, key))
        if owner is not None:
            raise OverrideError(
                f"setting '{declaration.id}': {schema}:{key} is already rendered by "
                f"setting '{owner}'"
            )
        seen[(schema, key)] = declaration.id

        entries.append(
            OverrideEntry(
                schema=schema,
                key=key,
                value=declaration.value,
                setting_id=declaration.id,
                module_id=declaration.module_id,
            )
        )
    return entries


def render(entries: list[OverrideEntry], module_id: str) -> str:
    """Render one module's entries as a complete override file.

    Deterministic: schemas sorted, keys sorted within a schema, generic section
    before its desktop-scoped twin. Two builds of the same manifests therefore
    produce byte-identical output, which is what makes the drift test a plain
    string comparison.
    """
    by_schema: dict[str, list[OverrideEntry]] = {}
    for entry in entries:
        by_schema.setdefault(entry.schema, []).append(entry)

    chunks = [
        HEADER_TEMPLATE.format(
            module_id=module_id,
            scope=DESKTOP_SCOPE,
            version=OVERRIDE_FORMAT_VERSION,
        )
    ]
    for schema in sorted(by_schema):
        body = "\n".join(
            entry.rendered() for entry in sorted(by_schema[schema], key=lambda e: e.key)
        )
        # Generic first, then the desktop-scoped copy that has to beat GNOME's
        # own scoped defaults. Same keys, same values -- see DESKTOP_SCOPE.
        for section in (schema, f"{schema}:{DESKTOP_SCOPE}"):
            chunks.append(f"\n[{section}]\n{body}\n")

    return "".join(chunks)


def render_module(tree, module_id: str) -> str:
    """The override file shipped for one module, as text."""
    if module_id not in tree.modules:
        raise OverrideError(f"no module '{module_id}' in this manifest tree")
    declarations = vendor_default_declarations(tree, module_id)
    if not declarations:
        raise OverrideError(
            f"module '{module_id}' declares no policy 'default' gsettings settings, "
            f"so it has no vendor defaults to render"
        )
    return render(entries_for(declarations), module_id)


def gvariant_text(value: TypedValue) -> str:
    """Serialize a typed value as GVariant text format.

    Only the types the value vocabulary defines. There is no generic GVariant
    writer and no tuple or dictionary support, because no setting needs one; a
    type that cannot be written raises rather than being approximated.
    """
    problems = value.problems()
    if problems:
        raise OverrideError(f"cannot render invalid value: {problems[0]}")

    writer = _WRITERS.get(value.type)
    if writer is None:
        raise OverrideError(
            f"value type {value.type!r} has no GVariant text form; "
            f"a setting using it cannot ship as a vendor default"
        )
    return writer(value.data)


def _write_bool(data) -> str:
    return "true" if data else "false"


def _write_int(data) -> str:
    return str(int(data))


def _write_double(data) -> str:
    # GVariant text needs a decimal point, or `1` parses as an integer and
    # g_variant_parse rejects it against a 'd' key.
    return repr(float(data))


def _write_string(data: str) -> str:
    return _quote(data)


def _write_string_array(data) -> str:
    if not data:
        # `[]` alone is untyped. glib-compile-schemas does supply the key's type
        # to g_variant_parse, so bare `[]` happens to work -- but the annotated
        # form is unambiguous on its own and reads correctly in a diff.
        return "@as []"
    return "[" + ", ".join(_quote(item) for item in data) + "]"


def _write_int_array(data) -> str:
    if not data:
        return "@ai []"
    return "[" + ", ".join(str(int(item)) for item in data) + "]"


def _quote(text: str) -> str:
    """A GVariant single-quoted string.

    Escaping is done rather than assumed safe: the values here are keyboard
    shortcuts today, but a rendering step that only works for well-behaved input
    is a rendering step that will eventually emit a broken file.
    """
    if "\n" in text or "\r" in text:
        raise OverrideError(
            f"value {text!r} contains a newline; a GKeyFile value is one line"
        )
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


_WRITERS = {
    "b": _write_bool,
    "i": _write_int,
    "d": _write_double,
    "s": _write_string,
    "as": _write_string_array,
    "ai": _write_int_array,
}


__all__ = [
    "DESKTOP_SCOPE",
    "OVERRIDE_FILENAME_PREFIX",
    "OVERRIDE_FILENAME_SUFFIX",
    "OVERRIDE_FORMAT_VERSION",
    "RELOCATABLE_SCHEMA_NOTE",
    "SCHEMAS_DIR",
    "OverrideEntry",
    "OverrideError",
    "entries_for",
    "gvariant_text",
    "modules_with_vendor_defaults",
    "override_filename",
    "render",
    "render_module",
    "vendor_default_declarations",
]
