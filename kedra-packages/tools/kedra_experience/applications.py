"""The application a resolved profile nominates for a named role.

Deterministic and side-effect free, exactly like `packages`: it folds module
manifests over a `Resolution` and returns declarations. It does not read the
desktop-file database, does not consult GAppInfo, does not launch anything and
cannot install anything.

Why this exists
---------------
"Which terminal is the Kedra terminal?" is a question several parts of Kedra
need one answer to -- the Super+Enter shortcut first, a future terminal
selector after it -- and it is not a GSettings preference. Nothing writes it
into dconf, nothing reads it back out, and there is no user-visible value to
revert. Forcing it into `module.settings` merely because that table exists
would make a contract look like a managed value and give it revert semantics it
must not have.

An application is named by its *desktop ID*, never a binary path or a command
line. A desktop ID is what GAppInfo and GNOME Settings speak; it
survives a native/Flatpak switch and a rename of the binary underneath it; and
as data it cannot carry arguments or a shell fragment. A consumer that
eventually needs to *launch* the application resolves the ID through
GAppInfo/GDesktopAppInfo at that point -- the launch mechanism is the consumer's
business, and keeping it out of the manifest is what lets a future selector
replace the terminal without any caller learning a path.

Not a registry. There are no priorities, no fallback chains and no lookup
service, because one role with one owner is what the product actually has.
"""

from __future__ import annotations

from dataclasses import dataclass

from .manifests import APPLICATION_DESKTOP_SUFFIX, APPLICATION_ROLES
from .shortcuts import launch_command_for_desktop_id

# The one role defined today. Named here so a consumer refers to the role by
# constant rather than by a string literal it might misspell.
ROLE_TERMINAL = "terminal"


@dataclass(frozen=True)
class ApplicationContract:
    """One application a module nominates for a role, and why."""

    role: str
    desktop_id: str
    package: str
    module_id: str
    reason: str = ""

    @property
    def application_id(self) -> str:
        """The desktop ID without its '.desktop' suffix.

        The form D-Bus activation and `app-id` matching use. Derived rather
        than declared, so the two spellings cannot disagree.
        """
        return self.desktop_id[: -len(APPLICATION_DESKTOP_SUFFIX)]

    @property
    def launch_command(self) -> str:
        """The command line that launches this application.

        Derived, never declared. It is the one place a command string is
        produced from an application contract, and it exists because GNOME's
        custom-keybinding schema types `command` as a string -- see
        `shortcuts.LAUNCH_PROGRAM` for why `gapplication launch` and not
        anything else.

        A *property of the contract*, not of a manifest: `module.applications`
        holds a desktop ID and nothing that resembles a command, so selecting a
        different terminal changes this answer without any manifest learning
        what a command line is.
        """
        return launch_command_for_desktop_id(self.desktop_id)


def _contracts(module_id: str, module: dict) -> list[ApplicationContract]:
    found: list[ApplicationContract] = []
    for entry in module.get("applications", []):
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        desktop_id = entry.get("desktop_id")
        package = entry.get("package")
        if role not in APPLICATION_ROLES:
            continue
        if not isinstance(desktop_id, str) or not isinstance(package, str):
            continue
        found.append(
            ApplicationContract(
                role=role,
                desktop_id=desktop_id,
                package=package,
                module_id=module_id,
                reason=entry.get("reason", ""),
            )
        )
    return found


def declared_applications(tree) -> tuple[ApplicationContract, ...]:
    """Every application contract in the tree, regardless of activation.

    The manifest-level view: what the distribution declares. Use
    `application_contracts` for what a given profile actually gets.
    """
    found: list[ApplicationContract] = []
    for module_id in sorted(tree.modules):
        found += _contracts(module_id, tree.modules[module_id])
    return tuple(sorted(found, key=lambda c: (c.role, c.module_id)))


def application_contracts(tree, resolution) -> tuple[ApplicationContract, ...]:
    """The application contracts the modules a resolution enables contribute.

    Disabled modules contribute nothing, and a preset that does not apply the
    Experience layer contributes nothing at all -- its module set is empty by
    construction, so Vanilla falls out of the same code path rather than needing
    a special case. This is what keeps a *declared* contract from becoming a
    Vanilla package requirement: the contract exists in the tree, and Vanilla
    still resolves to none.

    Sorted by (role, module ID) so the result is stable across runs.
    """
    enabled = {m.id for m in resolution.modules if m.enabled}

    found: list[ApplicationContract] = []
    for module_id in sorted(enabled):
        module = tree.modules.get(module_id)
        if module is None:
            continue
        found += _contracts(module_id, module)
    return tuple(sorted(found, key=lambda c: (c.role, c.module_id)))


def application_for_role(tree, resolution, role: str) -> ApplicationContract | None:
    """The application this profile uses for `role`, or None.

    None is a real answer, not an error: a profile with the owning module
    switched off -- and every Vanilla profile -- genuinely has no Kedra
    nomination for the role, and a caller must handle that rather than fall back
    to a hardcoded guess.

    At most one contract can match: role ownership is unique tree-wide, enforced
    by the manifest validator.
    """
    for contract in application_contracts(tree, resolution):
        if contract.role == role:
            return contract
    return None


def terminal_application(tree, resolution) -> ApplicationContract | None:
    """The Kedra terminal for this profile, or None. See `application_for_role`."""
    return application_for_role(tree, resolution, ROLE_TERMINAL)


def terminal_launch_command(tree, resolution) -> str | None:
    """The command that opens this profile's terminal, or None.

    None for Vanilla, and that is the answer rather than a fallback: a profile
    that applies no Experience module has no Kedra terminal, so there is no
    command to derive and nothing may invent one. A caller with no command must
    not launch a guess.
    """
    contract = terminal_application(tree, resolution)
    return contract.launch_command if contract else None


__all__ = [
    "ROLE_TERMINAL",
    "ApplicationContract",
    "application_contracts",
    "application_for_role",
    "declared_applications",
    "terminal_application",
    "terminal_launch_command",
]
