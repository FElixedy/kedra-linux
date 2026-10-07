"""GNOME custom keyboard shortcuts as one Kedra-managed resource.

A GNOME custom shortcut is not one setting. It is four values that only mean
anything together:

    /org/gnome/settings-daemon/plugins/media-keys/  custom-keybindings   as
        the list of shortcut paths GNOME will honour
    <kedra path>                                    name                 s
    <kedra path>                                    binding              s
    <kedra path>                                    command              s

Modelling that as four unrelated settings would be wrong in a specific,
damaging way: nothing would order the writes, so a run could register a path
whose `command` write had failed and leave a live shortcut that does nothing;
and an unmanage would treat `custom-keybindings` as an ordinary scalar Kedra
owns, so restoring "the value Kedra found" would delete every shortcut the user
created afterwards.

So one declaration describes the resource, and this module expands it into the
four settings the rest of the machinery already understands -- three ordinary
managed scalars at a relocatable path, plus one *list-membership* claim on the
parent list. Everything else about them is ordinary: the same ownership rules,
the same divergence detection, the same records. See docs/kedra-terminal.md.

Nothing here performs I/O. Expansion is a pure function of the manifest tree.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# GNOME's representation, read from the installed schemas rather than assumed.
# Verified against gnome-settings-daemon 50.1 / GLib 2.88; see
# tests/test_shortcuts.py and tests/test_gsettings_adapter.py.
# ---------------------------------------------------------------------------

# The parent list. A fixed-path schema; the key is an array of dconf paths.
PARENT_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
PARENT_KEY = "custom-keybindings"

# One instance per shortcut. Relocatable: the schema declares no path, so every
# instance lives wherever its creator put it.
CHILD_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

# The three child keys Kedra sets, in the order they are written. All type 's'.
# `enable-in-lockscreen` exists too and is deliberately left alone: its
# upstream default is false, which is the safe answer for a shortcut that opens
# a terminal, and writing a value equal to the default would take ownership of a
# key for no reason.
CHILD_KEYS = ("name", "command", "binding")

# ---------------------------------------------------------------------------
# The canonical Kedra shortcut path
# ---------------------------------------------------------------------------
#
# This is persistent identity. Every managed record Kedra ever writes for the
# terminal shortcut refers to it, so changing it later is a migration, not an
# edit -- the old path would keep its values and its list membership while a
# second shortcut appeared at the new one.
#
# Three properties it has to have, and does:
#
# *Deterministic.* Fixed text. It carries no username, no machine ID, no
# hostname, no counter and nothing derived from when or where it was created,
# so it is byte-identical on every Kedra installation and the same after a
# reinstall as before it.
#
# *Kedra-namespaced.* The final segment is `kedra-<role>`, which says who owns
# it to anyone reading dconf.
#
# *Collision-proof against GNOME.* GNOME Settings generates its own shortcut
# paths with the format string `%s/custom%d/` -- `custom0/`, `custom1/`, and so
# on -- verified in the installed gnome-control-center binary. A segment
# beginning `kedra-` is not of that shape and can never be produced by it, so
# Kedra can never occupy a path GNOME was about to hand a user, and a user's own
# shortcut can never land on Kedra's.
#
# The trailing slash is required, not stylistic: GSettings paths must begin and
# end with '/', and the value stored in `custom-keybindings` is compared by
# GNOME as a plain string, so a path written without it would be a different
# entry that nothing honours.
SHORTCUT_PATH_ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def kedra_shortcut_path(name: str) -> str:
    """The canonical dconf path for a Kedra-owned shortcut called `name`."""
    return f"{SHORTCUT_PATH_ROOT}kedra-{name}/"


TERMINAL_SHORTCUT_PATH = kedra_shortcut_path("terminal")

# ---------------------------------------------------------------------------
# Launch command
# ---------------------------------------------------------------------------
#
# GNOME's schema types `command` as a string, so a shortcut necessarily ends in
# a command line. That is the single allowed exception to "a manifest never
# contains a command", and it is confined to this function: the manifest names
# an application *role*, the role resolves to a desktop ID, and the command is
# derived. No manifest, no state file and no override contains it.
#
# `gapplication launch <APPID>` rather than the alternatives:
#
#   gio launch <FILE>      rejected. The installed `gio` resolves its argument
#                          with g_desktop_app_info_new_from_filename() only --
#                          confirmed in the binary's symbol table -- so it needs
#                          a filesystem path to the desktop file. That would
#                          hardcode /usr/share/applications/..., which breaks
#                          for a user-local override in ~/.local/share and for a
#                          Flatpak-exported desktop file, and reintroduces
#                          exactly the absolute path the contract exists to
#                          avoid.
#
#   kgx, /usr/bin/kgx      rejected outright. A binary name is a packaging
#                          detail that changes underneath the contract, and it
#                          is what makes a terminal selector impossible.
#
#   gapplication launch    chosen. It takes an application ID, resolves it
#                          through the ordinary XDG desktop-file search path --
#                          so a user's own override wins, and a Flatpak export
#                          works -- and activates it over D-Bus with a proper
#                          activation token, which is what StartupNotify needs.
#                          The ID is `ApplicationContract.application_id`, i.e.
#                          the declared desktop ID with `.desktop` removed, so
#                          nothing is spelled twice.
#
# The one requirement it adds is that the application be `DBusActivatable`.
# GNOME Console is (verified: `gapplication list-apps` lists org.gnome.Console,
# and the desktop file sets DBusActivatable=true), and a future terminal
# selector must check it -- see `launch_requirement`.
#
# `gapplication` is part of glib2, which every GNOME system has by construction,
# so this adds no dependency the desktop did not already have.
LAUNCH_PROGRAM = "gapplication"
LAUNCH_VERB = "launch"

LAUNCH_REQUIREMENT = (
    "gapplication launch activates an application over D-Bus, so the desktop "
    "file must set DBusActivatable=true"
)

DESKTOP_SUFFIX = ".desktop"

# Characters that make a word in a desktop-entry Exec line stop being one word.
# gnome-settings-daemon runs a custom shortcut's command by handing it to
# g_app_info_create_from_commandline() -- confirmed in the gsd-media-keys symbol
# table -- so it is parsed by the Desktop Entry specification's rules and never
# by a shell. There is no shell metacharacter to worry about and no expansion;
# what matters is that a word containing whitespace or a quote stays one word.
_EXEC_RESERVED = ' \t\n"\'\\><~|&;$*?#()`'


def application_id_from_desktop_id(desktop_id: str) -> str:
    """`org.gnome.Console.desktop` -> `org.gnome.Console`."""
    if not isinstance(desktop_id, str) or not desktop_id.endswith(DESKTOP_SUFFIX):
        raise ShortcutError(
            f"{desktop_id!r} is not a desktop-file ID, so no application ID can "
            f"be derived from it"
        )
    return desktop_id[: -len(DESKTOP_SUFFIX)]


def quote_exec_word(word: str) -> str:
    """One word of a desktop-entry Exec line, quoted if it needs to be.

    A no-op for every application ID that passes the manifest validator, which
    is the point of validating them strictly. It is implemented rather than
    asserted because a rendering step that is only correct for well-behaved
    input is a rendering step that will one day emit a broken command.
    """
    if not isinstance(word, str) or not word:
        raise ShortcutError("cannot render an empty word in a command")
    if "\n" in word or "\r" in word:
        raise ShortcutError(f"{word!r} contains a newline; a command is one line")
    if not any(c in _EXEC_RESERVED for c in word):
        return word
    escaped = word.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def launch_command_for_application_id(application_id: str) -> str:
    """The command string that launches an application by its ID."""
    return f"{LAUNCH_PROGRAM} {LAUNCH_VERB} {quote_exec_word(application_id)}"


def launch_command_for_desktop_id(desktop_id: str) -> str:
    """The command string that launches the application a desktop ID names."""
    return launch_command_for_application_id(application_id_from_desktop_id(desktop_id))


class ShortcutError(Exception):
    """A shortcut declaration cannot be expanded into settings."""


def launchability_problems(desktop_id: str) -> list[str]:
    """Whether an application can actually be launched the way Kedra launches it.

    The validation seam a future terminal selector must go through, and the
    reason it exists now rather than then: `gapplication launch` activates an
    application over D-Bus, so a desktop file without `DBusActivatable=true`
    produces a shortcut that is registered, looks correct in GNOME Settings and
    silently does nothing. That failure is invisible until somebody presses the
    key.

    Verified for the shipped contract: `org.gnome.Console.desktop` sets
    `DBusActivatable=true`, ships
    `/usr/share/dbus-1/services/org.gnome.Console.service`, and is listed by
    `gapplication list-apps`.

    Reads the installed desktop-file database through GAppInfo and launches
    nothing. Returns a *problems* list rather than a boolean so a caller can say
    why; an empty list means launchable. A machine where the application is not
    installed reports exactly that, which is a different answer from "not
    launchable" and is why this is not part of manifest validation -- a manifest
    must validate identically on a build machine with no desktop.

    Deliberately narrow. It answers one question about one launch mechanism. It
    is not a general application-capability probe, and adding a binary path or a
    command field to the contract to work around a negative answer would defeat
    the point: the right response to "this terminal is not D-Bus activatable" is
    to pick a different terminal or a different launch mechanism, declared once.
    """
    if not isinstance(desktop_id, str) or not desktop_id.endswith(DESKTOP_SUFFIX):
        return [f"{desktop_id!r} is not a desktop-file ID"]

    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError) as exc:  # pragma: no cover - environment
        return [f"cannot check launchability without PyGObject/Gio: {exc}"]

    try:
        info = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        # PyGObject raises rather than returning None when the constructor gets
        # a NULL back, which is what an uninstalled application produces.
        info = None
    if info is None:
        return [f"no desktop file {desktop_id!r} is installed on this system"]

    if not info.get_boolean("DBusActivatable"):
        return [
            f"{desktop_id!r} does not set DBusActivatable=true, so "
            f"'{LAUNCH_PROGRAM} {LAUNCH_VERB}' cannot activate it -- "
            f"{LAUNCH_REQUIREMENT}"
        ]
    return []


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

SHORTCUT_REQUIRED_KEYS = frozenset({"id", "policy", "path", "name", "binding", "command"})
SHORTCUT_KNOWN_KEYS = SHORTCUT_REQUIRED_KEYS | {"description", "introduced_in"}

# `command` is either a literal string or a typed reference to an application
# role. A typed reference rather than string interpolation: there is no
# expression language here and there will not be one, so the only thing a
# manifest can say is "the application that fills this role", which tooling can
# resolve, validate and display without evaluating anything.
COMMAND_SOURCE_KEYS = frozenset({"application_role"})

# Suffixes appended to a shortcut's ID to name the settings it expands into.
# Part of the persistent contract: these IDs appear in every user's state file.
CHILD_SUFFIXES = {key: f"-{key}" for key in CHILD_KEYS}
REGISTRATION_SUFFIX = "-registration"


@dataclass(frozen=True)
class ShortcutDeclaration:
    """One custom shortcut a module wants to exist."""

    id: str
    policy: str
    path: str
    name: str
    binding: str
    command: str
    module_id: str
    module_version: str
    description: str = ""
    introduced_in: str | None = None
    # The role `command` was derived from, when it was derived. Kept so tooling
    # can say *why* the command is what it is without re-resolving it.
    command_role: str | None = None

    @property
    def registration_setting_id(self) -> str:
        return f"{self.id}{REGISTRATION_SUFFIX}"

    def child_setting_id(self, key: str) -> str:
        return f"{self.id}{CHILD_SUFFIXES[key]}"

    def setting_ids(self) -> tuple[str, ...]:
        return tuple(self.child_setting_id(k) for k in CHILD_KEYS) + (
            self.registration_setting_id,
        )


def shortcut_problems(entry: object, label: str, modules: dict | None = None) -> list[str]:
    """Every reason a raw `[[module.shortcuts]]` table is invalid."""
    from .backends import _is_gsettings_path
    from .settings import SETTING_POLICIES, _is_setting_id

    if not isinstance(entry, dict):
        return [f"{label}: must be a table"]

    problems: list[str] = []
    for key in sorted(entry.keys() - SHORTCUT_KNOWN_KEYS):
        problems.append(f"{label}: unknown key '{key}'")
    for key in sorted(SHORTCUT_REQUIRED_KEYS - entry.keys()):
        problems.append(f"{label}: missing required key '{key}'")

    shortcut_id = entry.get("id")
    if shortcut_id is not None and not _is_setting_id(shortcut_id):
        problems.append(f"{label}: id {shortcut_id!r} is not a valid setting ID")

    policy = entry.get("policy")
    if policy is not None and policy != "managed":
        if policy in SETTING_POLICIES:
            # Worth an explanation rather than a bare "invalid": the reason is
            # a real GSettings limitation, not a Kedra rule.
            problems.append(
                f"{label}: policy {policy!r} is not available for a custom shortcut; "
                f"a relocatable schema's vendor override applies to every instance "
                f"at every path, so a per-shortcut value can only be 'managed'"
            )
        else:
            problems.append(f"{label}: invalid policy {policy!r} (expected 'managed')")

    path = entry.get("path")
    if path is not None:
        if not _is_gsettings_path(path):
            problems.append(
                f"{label}: path {path!r} is not an absolute GSettings/dconf path"
            )
        elif not path.startswith(SHORTCUT_PATH_ROOT):
            problems.append(
                f"{label}: path {path!r} is not under {SHORTCUT_PATH_ROOT!r}; a "
                f"custom shortcut GNOME will honour must live there"
            )
        elif path == SHORTCUT_PATH_ROOT:
            problems.append(f"{label}: path must name a shortcut below {SHORTCUT_PATH_ROOT!r}")
        elif not path[len(SHORTCUT_PATH_ROOT):].startswith("kedra-"):
            problems.append(
                f"{label}: path {path!r} does not use the Kedra namespace; the "
                f"segment below {SHORTCUT_PATH_ROOT!r} must begin 'kedra-' so it "
                f"cannot collide with a shortcut GNOME Settings creates"
            )

    for key in ("name", "binding"):
        value = entry.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(f"{label}: '{key}' must be a non-empty string")

    problems += _command_problems(entry.get("command"), label, modules)

    introduced = entry.get("introduced_in")
    if introduced is not None and not isinstance(introduced, str):
        problems.append(f"{label}: 'introduced_in' must be a string")

    description = entry.get("description")
    if description is not None and not isinstance(description, str):
        problems.append(f"{label}: 'description' must be a string")

    return problems


def _command_problems(command: object, label: str, modules: dict | None) -> list[str]:
    if command is None:
        return []

    if isinstance(command, str):
        if not command.strip():
            return [f"{label}: 'command' must not be empty"]
        return []

    if not isinstance(command, dict):
        return [
            f"{label}: 'command' must be a string or a typed source table such as "
            f"{{ application_role = \"terminal\" }}"
        ]

    problems: list[str] = []
    for key in sorted(command.keys() - COMMAND_SOURCE_KEYS):
        problems.append(
            f"{label}.command: unknown source key '{key}' "
            f"(expected one of {sorted(COMMAND_SOURCE_KEYS)})"
        )
    if not command.keys() & COMMAND_SOURCE_KEYS:
        problems.append(
            f"{label}.command: a source table needs exactly one of "
            f"{sorted(COMMAND_SOURCE_KEYS)}"
        )
        return problems

    role = command.get("application_role")
    if not isinstance(role, str) or not role:
        problems.append(f"{label}.command: 'application_role' must be a non-empty string")
        return problems

    if modules is not None and _desktop_id_for_role(modules, role) is None:
        problems.append(
            f"{label}.command: no module in this tree nominates an application for "
            f"role {role!r}, so no launch command can be derived"
        )
    return problems


def _desktop_id_for_role(modules: dict, role: str) -> str | None:
    """The desktop ID declared for a role, across the whole tree.

    Deliberately the *declared* contract rather than a resolved one. A shortcut
    is expanded while validating manifests, where no profile exists; and the
    only module that can declare a shortcut naming a role is one enabled
    alongside the module that fills it -- role ownership is unique tree-wide, so
    there is nothing to disambiguate.
    """
    for module_id in sorted(modules):
        for entry in modules[module_id].get("applications", []) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("role") == role and isinstance(entry.get("desktop_id"), str):
                return entry["desktop_id"]
    return None


def build_shortcut(
    entry: dict, module_id: str, module_version: str, modules: dict
) -> ShortcutDeclaration:
    """Build a shortcut declaration from an already-validated table."""
    raw = entry["command"]
    role = None
    if isinstance(raw, dict):
        role = raw.get("application_role")
        desktop_id = _desktop_id_for_role(modules, role)
        if desktop_id is None:
            raise ShortcutError(
                f"shortcut '{entry['id']}': no application is declared for role "
                f"{role!r}, so no launch command can be derived"
            )
        command = launch_command_for_desktop_id(desktop_id)
    else:
        command = raw

    return ShortcutDeclaration(
        id=entry["id"],
        policy=entry["policy"],
        path=entry["path"],
        name=entry["name"],
        binding=entry["binding"],
        command=command,
        module_id=module_id,
        module_version=module_version,
        description=entry.get("description", ""),
        introduced_in=entry.get("introduced_in"),
        command_role=role,
    )


def expand(shortcut: ShortcutDeclaration) -> list[dict]:
    """The four setting tables this shortcut is made of.

    Plain setting tables, in the same shape a manifest would write by hand, so
    every existing code path -- duplicate-target detection, migration
    cross-checks, planning, execution -- handles them without knowing they were
    generated. Two fields the hand-written form does not have:

    `resource`   ties the four together, so an unmanage can treat a shortcut the
                 user has edited as one thing rather than dismantling it
                 key by key.
    `member`     marks the registration as a *list-membership* claim: Kedra owns
                 the presence of one entry in `custom-keybindings`, never the
                 list itself.
    """
    values = {
        "name": shortcut.name,
        "binding": shortcut.binding,
        "command": shortcut.command,
    }

    entries: list[dict] = []
    for key in CHILD_KEYS:
        entries.append(
            {
                "id": shortcut.child_setting_id(key),
                "backend": "gsettings",
                "policy": shortcut.policy,
                "description": f"{shortcut.description} ({key})".strip(),
                "introduced_in": shortcut.introduced_in,
                "target": {"schema": CHILD_SCHEMA, "path": shortcut.path, "key": key},
                "value": {"type": "s", "data": values[key]},
                "resource": shortcut.id,
            }
        )

    entries.append(
        {
            "id": shortcut.registration_setting_id,
            "backend": "gsettings",
            "policy": shortcut.policy,
            "description": (
                f"Register {shortcut.path} in GNOME's custom-keybindings list."
            ),
            "introduced_in": shortcut.introduced_in,
            "target": {"schema": PARENT_SCHEMA, "key": PARENT_KEY},
            # The value is the membership claim, not the list. `member` is what
            # makes the executor add and remove one entry instead of writing
            # this as if Kedra owned every shortcut the user has.
            "value": {"type": "as", "data": [shortcut.path]},
            "resource": shortcut.id,
            "member": shortcut.path,
        }
    )
    return entries


def module_shortcuts(module_id: str, module: dict, modules: dict) -> list[ShortcutDeclaration]:
    """Every valid shortcut declaration in one module."""
    found: list[ShortcutDeclaration] = []
    for entry in module.get("shortcuts", []) or []:
        if not isinstance(entry, dict):
            continue
        if shortcut_problems(entry, "shortcut", modules):
            continue
        try:
            found.append(
                build_shortcut(entry, module_id, module.get("version", "0.0.0"), modules)
            )
        except ShortcutError:
            continue
    return found


def expanded_settings(module_id: str, module: dict, modules: dict) -> list[dict]:
    """The setting tables a module's shortcuts expand into."""
    entries: list[dict] = []
    for shortcut in module_shortcuts(module_id, module, modules):
        entries += expand(shortcut)
    return entries


def declared_shortcuts(tree) -> list[ShortcutDeclaration]:
    """Every shortcut in the tree, activation ignored."""
    found: list[ShortcutDeclaration] = []
    for module_id in sorted(tree.modules):
        found += module_shortcuts(module_id, tree.modules[module_id], tree.modules)
    return found


__all__ = [
    "CHILD_KEYS",
    "CHILD_SCHEMA",
    "LAUNCH_PROGRAM",
    "LAUNCH_REQUIREMENT",
    "PARENT_KEY",
    "PARENT_SCHEMA",
    "REGISTRATION_SUFFIX",
    "SHORTCUT_PATH_ROOT",
    "TERMINAL_SHORTCUT_PATH",
    "ShortcutDeclaration",
    "ShortcutError",
    "application_id_from_desktop_id",
    "build_shortcut",
    "declared_shortcuts",
    "expand",
    "expanded_settings",
    "kedra_shortcut_path",
    "launch_command_for_application_id",
    "launchability_problems",
    "launch_command_for_desktop_id",
    "module_shortcuts",
    "quote_exec_word",
    "shortcut_problems",
]
