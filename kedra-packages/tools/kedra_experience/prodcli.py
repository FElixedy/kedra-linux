"""The shipped, read-only Kedra Experience command.

Installed as /usr/bin/kedra-experience by `kedra-experience-runtime`. This is
the supported interface -- the one an installer, a future configurator or an
administrator may call -- and it is deliberately a different program from
`kedra-experience-dev`, which is repository infrastructure with development-only
assumptions (a source-relative data directory, an in-memory backend standing in
for a session, and a surface shaped for poking at the model by hand).

What it does
------------
Answers two questions about profiles, from data alone:

    profiles       which profiles exist, and what each one is made of
    resolve        what a given selection resolves to -- modules, packages,
                   application contracts and diagnostics
    install-state  write one user's chosen selection into a target root
    apply          enact the managed part of this user's profile, in their session
    unmanage       give back everything Kedra still owns for this user

What it will not do
-------------------
`apply` writes ONLY `policy = "managed"` resources. A `policy = "default"`
setting -- the five-workspace model, the Super+1..5 keymap -- is delivered as a
package-owned GSettings vendor override and is never written into anybody's
dconf. That is enforced twice below this command, in the planner and again in
the executor, and asserted by tests: turning a vendor default into a managed
setting by accident would create an ownership claim over a value Kedra never
wrote, and a later revert would then "restore" something it never changed.

There is deliberately no `--operation` switch and no force. `apply` and
`unmanage` are the two lifecycle operations a user's session needs;
restore-to-defaults reclaims a resource over a user's own divergence and is not
something a login should be able to do.

`install-state` writes Kedra's own bookkeeping -- which profile the user
picked -- into a target root it is given explicitly. It reads no GSettings and
records no `[applied]` state, because an installer applies nothing.

It also reads exactly one manifest tree: `manifests.installed_data_dir()`,
/usr/share/kedra/experience, owned by the `kedra-experience` package. There is
no environment variable and no fallback to a checkout. `--data-dir` exists for
development and testing and production callers must omit it; when they do, the
answer depends on nothing but the installed packages.

Output
------
JSON with sorted keys and stable, versioned envelopes, so a consumer can pin
`profiles_schema_version` / `resolve_schema_version` rather than pattern-match
prose. No Python repr, no exception text on stdout: a failure writes a message
to stderr and produces no document at all.

Exit status
-----------
    0   the requested document was produced and the profile is valid
    1   nothing could be produced -- bad input, unreadable tree, unknown preset;
        stdout is empty
    2   the document WAS produced on stdout, and it reports hard requirement
        violations or refused overrides; the caller should read
        `diagnostics.violations` / `diagnostics.refused_overrides`

2 rather than 1 because a profile that resolves and is wrong is a different
situation from one that cannot be resolved at all, and an installer needs to
tell them apart: the first can be shown to the user, the second cannot.

`install-state` uses the same three: 0 written (or deliberately not written, for
a profile that applies no layer), 1 could not, 2 the selection is invalid so
nothing was written. It never writes a file for a selection it would have
reported as invalid -- see `cmd_install_state`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import state as state_mod
from .activate import (
    ACTIVATION_SCHEMA_VERSION,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
    ActivationError,
    activate,
)
from .applications import application_contracts
from .manifests import Report, SCHEMA_VERSION, installed_data_dir, load_tree
from .ownership import OPERATION_APPLY, OPERATION_UNMANAGE
from .packages import package_requirements, packages_by_relation
from .resolve import (
    RESOLUTION_SCHEMA_VERSION,
    ResolutionError,
    diagnose,
    resolve_preset,
    resolve_state,
)
from .state import (
    STATE_FILENAME,
    STATE_SCHEMA_VERSION,
    ModuleOverride,
    StateError,
    UserState,
)

PROGRAM = "kedra-experience"

# Envelope versions. Independent of the manifest schema and of each other: a
# consumer pins the shape it parses, not the data model behind it.
PROFILES_SCHEMA_VERSION = 1

# 2  `diagnostics.refused_overrides`, and with it a second reason for
#    `diagnostics.ok` to be false and for `resolve` to exit 2. A consumer pinned
#    to 1 would treat a selection whose override was refused as acceptable.
RESOLVE_SCHEMA_VERSION = 2

# The install-state result envelope's version.
INSTALL_STATE_SCHEMA_VERSION = 1

# The selection document's own version. It is 1 because this is the first
# shipped spelling of it, not because it mirrors anything else.
SELECTION_SCHEMA_VERSION = 1

# The keys a selection document may carry.
#
# `base_preset` and `module_overrides` are exactly the keys of the `[selection]`
# table in a user state file (state.SELECTION_KNOWN_KEYS). That is deliberate
# and is the whole reason no second format was invented: what an installer hands
# to `resolve` is, byte for byte, what a later task will write into
# $XDG_STATE_HOME/kedra/experience-state.toml.
SELECTION_REQUIRED_KEYS = frozenset({"base_preset"})
SELECTION_OPTIONAL_KEYS = frozenset({"module_overrides", "selection_schema_version"})
SELECTION_KNOWN_KEYS = SELECTION_REQUIRED_KEYS | SELECTION_OPTIONAL_KEYS

OVERRIDE_KNOWN_KEYS = frozenset({"id", "enabled"})

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID_PROFILE = 2


class UsageError(Exception):
    """A caller-supplied document or argument cannot be used."""


# ---------------------------------------------------------------------------
# Tree loading
# ---------------------------------------------------------------------------


def _load_tree(data_dir: Path):
    """Load and fully validate a manifest tree, or fail loudly.

    A tree that does not exist is reported as what it actually means -- the
    package that owns it is not installed -- rather than as a stack trace about
    a missing file.
    """
    # Absolute, so `data_dir` in the output names one directory unambiguously
    # whatever the caller's working directory was. Production already passes an
    # absolute path; this only normalises a relative --data-dir given by hand.
    data_dir = Path(data_dir).resolve()
    if not data_dir.is_dir():
        raise UsageError(
            f"no Experience manifest tree at {data_dir}: "
            f"the kedra-experience package does not appear to be installed"
        )

    report = Report()
    tree = load_tree(data_dir, report)
    if tree is None or not report.ok:
        detail = report.rendered() if report.errors else "tree is structurally unusable"
        raise UsageError(f"the manifest tree at {data_dir} is not valid:\n{detail}")
    return tree


def _module_label(tree, module_id: str) -> dict:
    """The human-facing strings for a module, from its manifest."""
    manifest = tree.modules.get(module_id) or {}
    return {
        "name": manifest.get("name", module_id),
        "description": manifest.get("description", ""),
    }


# ---------------------------------------------------------------------------
# Selection documents
# ---------------------------------------------------------------------------


def parse_selection(document: object) -> UserState:
    """Turn a selection document into the existing user-state model.

    Strict on purpose. Every rejection here is a mistake an installer would
    otherwise carry silently into a user's state file: an unknown key is a
    typo or a version skew, and a non-boolean `enabled` is a UI bug.

    Returns a `UserState` with no `applied` record, because a selection
    describes what was chosen and says nothing about what was applied. This
    command never applies anything, so there is nothing it could truthfully put
    there.
    """
    if not isinstance(document, dict):
        raise UsageError("a selection must be a JSON object")

    unknown = sorted(set(document) - SELECTION_KNOWN_KEYS)
    if unknown:
        raise UsageError(
            f"selection has unknown key(s): {', '.join(unknown)} "
            f"(known: {', '.join(sorted(SELECTION_KNOWN_KEYS))})"
        )

    version = document.get("selection_schema_version", SELECTION_SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise UsageError("selection_schema_version must be an integer")
    if version != SELECTION_SCHEMA_VERSION:
        raise UsageError(
            f"selection_schema_version {version} is not understood "
            f"(this build reads version {SELECTION_SCHEMA_VERSION})"
        )

    base_preset = document.get("base_preset")
    if not isinstance(base_preset, str) or not base_preset:
        raise UsageError("selection.base_preset is required and must be a string")

    raw_overrides = document.get("module_overrides", [])
    if not isinstance(raw_overrides, list):
        raise UsageError("selection.module_overrides must be an array")

    overrides: list[ModuleOverride] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_overrides):
        where = f"selection.module_overrides[{index}]"
        if not isinstance(entry, dict):
            raise UsageError(f"{where} must be an object")
        unknown = sorted(set(entry) - OVERRIDE_KNOWN_KEYS)
        if unknown:
            raise UsageError(f"{where} has unknown key(s): {', '.join(unknown)}")
        module_id = entry.get("id")
        if not isinstance(module_id, str) or not module_id:
            raise UsageError(f"{where}.id is required and must be a string")
        enabled = entry.get("enabled")
        if not isinstance(enabled, bool):
            raise UsageError(f"{where}.enabled is required and must be true or false")
        if module_id in seen:
            raise UsageError(f"{where} overrides module '{module_id}' twice")
        seen.add(module_id)
        overrides.append(ModuleOverride(id=module_id, enabled=enabled))

    try:
        return UserState(
            base_preset=base_preset,
            module_overrides=tuple(overrides),
            applied=None,
            state_schema_version=STATE_SCHEMA_VERSION,
        )
    except StateError as exc:  # pragma: no cover - the model accepts what we built
        raise UsageError(str(exc)) from exc


def selection_to_dict(state: UserState) -> dict:
    """Echo the accepted selection back, normalised.

    Worth returning rather than assuming: the model sorts overrides, so what a
    caller sent and what will end up in a state file are not necessarily in the
    same order, and an installer that wants to persist exactly what was resolved
    should persist this.
    """
    return {
        "selection_schema_version": SELECTION_SCHEMA_VERSION,
        "base_preset": state.base_preset,
        "module_overrides": [
            {"id": o.id, "enabled": o.enabled} for o in state.module_overrides
        ],
    }


def read_selection(source: str) -> object:
    """Read a selection document from stdin ('-') or a file path."""
    if source == "-":
        text = sys.stdin.read()
        origin = "standard input"
    else:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read selection: {exc}") from exc
        origin = str(source)

    if not text.strip():
        raise UsageError(f"no selection document on {origin}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"selection on {origin} is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------


def build_profiles_document(tree) -> dict:
    """Every profile the tree defines, with its resolved module set.

    Resolved rather than declared: a derived preset's module list is the result
    of walking its base, and a consumer must never have to reimplement that to
    show a checklist. Vanilla therefore reports an empty module list here, which
    is the truthful answer -- it applies no layer, so it has no modules -- and
    not a list of modules that all happen to be off.
    """
    presets = []
    for preset_id in sorted(tree.presets):
        manifest = tree.presets[preset_id]
        try:
            resolution = resolve_preset(tree, preset_id)
        except ResolutionError as exc:
            raise UsageError(f"preset '{preset_id}' does not resolve: {exc}") from exc

        presets.append(
            {
                "id": preset_id,
                "name": manifest.get("name", preset_id),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", ""),
                "kind": manifest.get("kind", "base"),
                "base_preset": manifest.get("base_preset"),
                # Whether a UI may offer this profile as a choice. Kedra ships
                # only selectable presets today; a consumer must still honour
                # the flag rather than assume that stays true.
                "user_selectable": bool(manifest.get("user_selectable", False)),
                "applies_experience_layer": resolution.applies_experience_layer,
                "modules": [
                    {
                        "id": module.id,
                        "enabled": module.enabled,
                        "tier": module.tier,
                        # The only modules an installer may offer as choices.
                        # `desktop` and `integration` are false and must not be
                        # presented, however they are rendered.
                        "user_selectable": module.user_selectable,
                        "version": module.version,
                        "source": module.source,
                        **_module_label(tree, module.id),
                    }
                    for module in resolution.modules
                ],
            }
        )

    return {
        "profiles_schema_version": PROFILES_SCHEMA_VERSION,
        "experience_schema_version": SCHEMA_VERSION,
        "resolution_schema_version": RESOLUTION_SCHEMA_VERSION,
        "selection_schema_version": SELECTION_SCHEMA_VERSION,
        "data_dir": str(tree.root),
        "default_preset": tree.default_preset,
        "presets": presets,
    }


def build_resolve_document(tree, state: UserState) -> dict:
    """What one selection resolves to. The installer's whole input.

    Everything a caller needs to act is here and nothing it must not act on is:
    module activation, the package set split by relation strength, the
    application contracts the enabled modules contribute, and both severities of
    diagnostic. No setting values, because choosing a profile does not entitle a
    caller to write one.
    """
    try:
        resolution = resolve_state(tree, state)
    except ResolutionError as exc:
        raise UsageError(str(exc)) from exc

    diagnostics = diagnose(tree, resolution)
    by_relation = packages_by_relation(tree, resolution)
    requirements = package_requirements(tree, resolution)

    modules = [
        {
            "id": module.id,
            "enabled": module.enabled,
            "tier": module.tier,
            "user_selectable": module.user_selectable,
            "version": module.version,
            "source": module.source,
            **_module_label(tree, module.id),
        }
        for module in resolution.modules
    ]

    return {
        "resolve_schema_version": RESOLVE_SCHEMA_VERSION,
        "experience_schema_version": SCHEMA_VERSION,
        "resolution_schema_version": RESOLUTION_SCHEMA_VERSION,
        "data_dir": str(tree.root),
        # What was asked for, normalised. See `selection_to_dict`.
        "selection": selection_to_dict(state),
        "preset": {
            "id": resolution.preset_id,
            "version": resolution.preset_version,
            # Leaf-to-base, so a caller can show "Custom, based on Kedra"
            # without re-walking the tree.
            "chain": list(resolution.chain),
        },
        "applies_experience_layer": resolution.applies_experience_layer,
        "modules": modules,
        "enabled_modules": list(resolution.enabled_module_ids),
        "packages": {
            # Resolved names only, per relation. `required` is the set an
            # installer installs; the rest are decisions it may surface but must
            # not act on silently.
            "required": list(by_relation["required"]),
            "recommended": list(by_relation["recommended"]),
            "optional": list(by_relation["optional"]),
            "conditional": list(by_relation["conditional"]),
            # Relations this layer cannot settle without a package database --
            # today only `package-installed`. Reported with the condition
            # attached, never guessed at, and never folded into `required`.
            "unresolved": [
                {
                    "name": r.name,
                    "relation": r.relation,
                    "module_id": r.module_id,
                    "reason": r.reason,
                    "condition": r.unresolved_condition,
                }
                for r in requirements
                if not r.resolved
            ],
            # Every relation, resolved or not, with the module that asked and
            # why. This is what a UI shows when a user asks "why is this being
            # installed?"; nothing else in the document answers that.
            "requirements": [
                {
                    "name": r.name,
                    "relation": r.relation,
                    "module_id": r.module_id,
                    "reason": r.reason,
                    "resolved": r.resolved,
                    "condition": r.unresolved_condition,
                }
                for r in requirements
            ],
        },
        "applications": [
            {
                "role": contract.role,
                "desktop_id": contract.desktop_id,
                "application_id": contract.application_id,
                "package": contract.package,
                "module_id": contract.module_id,
                "reason": contract.reason,
                # Derived from the desktop ID by pure string manipulation --
                # this reads no desktop-file database and launches nothing.
                "launch_command": contract.launch_command,
            }
            for contract in application_contracts(tree, resolution)
        ],
        "diagnostics": diagnostics.to_dict(),
    }


# ---------------------------------------------------------------------------
# Writing one user's selection into a target root
# ---------------------------------------------------------------------------

# Relative to the target user's home. The same location `state.default_state_dir`
# computes from $XDG_STATE_HOME, spelled out because there is no environment to
# read: this runs as root, outside any session, against a filesystem that is not
# the running system.
STATE_RELATIVE_DIR = Path(".local/state/kedra")


def _target_user_record(root: Path, user: str) -> tuple[int, int, str] | None:
    """(uid, gid, home) for `user`, read from the TARGET's /etc/passwd.

    The target is the authority on where its own users live. Deriving the home
    directory instead of assuming /home/<user> is what keeps this correct for an
    installer that places homes elsewhere, and reading uid/gid from the same
    place means a caller cannot pass a pair that disagrees with the account that
    actually exists.
    """
    passwd = root / "etc/passwd"
    try:
        text = passwd.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) >= 6 and fields[0] == user:
            try:
                return int(fields[2]), int(fields[3]), fields[5]
            except ValueError:
                return None
    return None


def _resolve_target_paths(args) -> tuple[Path, int, int]:
    """Where to write, and who should own it. Validates before deciding."""
    root = Path(args.root)
    if not root.is_dir():
        raise UsageError(f"target root {root} does not exist")
    root = root.resolve()

    user = args.user
    # A username is a path component here, so anything that could climb out of
    # the target is refused rather than sanitised. An installer never sends
    # these; a bug or an injected value would.
    if not user or "/" in user or user in (".", "..") or user.startswith("-"):
        raise UsageError(f"invalid target user name {user!r}")

    record = _target_user_record(root, user)
    if record is None and (args.uid is None or args.gid is None or args.home is None):
        raise UsageError(
            f"user '{user}' is not in {root}/etc/passwd, and --uid/--gid/--home "
            f"were not all given. The account must exist in the target before its "
            f"state can be written -- run this after the installer's user step."
        )

    uid = args.uid if args.uid is not None else record[0]
    gid = args.gid if args.gid is not None else record[1]
    home = args.home if args.home is not None else record[2]

    if not home.startswith("/"):
        raise UsageError(f"target home {home!r} for '{user}' is not an absolute path")

    # Join under the root and refuse anything that escapes it. `..` in a passwd
    # entry is not something to be clever about.
    target_home = (root / home.lstrip("/")).resolve()
    if target_home != root and root not in target_home.parents:
        raise UsageError(f"target home {target_home} lies outside the root {root}")
    if not target_home.is_dir():
        raise UsageError(
            f"{target_home} does not exist. The user's home must have been created "
            f"already -- run this after the installer's user step, and after any "
            f"/etc/skel copy that would overwrite it."
        )

    return target_home / STATE_RELATIVE_DIR / STATE_FILENAME, uid, gid


def cmd_install_state(args: argparse.Namespace) -> int:
    """Write a target user's Experience selection. The only command that writes.

    Order matters and is deliberate: the selection is parsed, then RESOLVED, and
    only a resolution the resolver calls valid is written. A refused override or
    a requirement violation means nothing reaches the disk -- an installer must
    not be able to persist a selection that `resolve` would have rejected, or the
    resolver's authority would end at the UI.

    A profile that applies no Experience layer -- Vanilla -- writes no file at
    all. There is no runtime on that system to read one, and a Kedra state
    directory on a Vanilla install is a confusing artefact, not a record.
    """
    tree = _load_tree(args.data_dir)
    state = parse_selection(read_selection(args.selection))

    # Resolve first. This is the gate.
    document = build_resolve_document(tree, state)
    if not document["diagnostics"]["ok"]:
        problems = [r["message"] for r in document["diagnostics"]["refused_overrides"]]
        problems += [v["message"] for v in document["diagnostics"]["violations"]]
        print(
            f"{PROGRAM}: error: refusing to persist an invalid selection:\n  "
            + "\n  ".join(problems),
            file=sys.stderr,
        )
        return EXIT_INVALID_PROFILE

    path, uid, gid = _resolve_target_paths(args)

    if not document["applies_experience_layer"]:
        # Vanilla. Say so rather than writing an empty record.
        print(
            json.dumps(
                {
                    "install_state_schema_version": INSTALL_STATE_SCHEMA_VERSION,
                    "written": False,
                    "reason": "the selected profile applies no Experience layer",
                    "preset_id": document["preset"]["id"],
                    "path": None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_OK

    if path.exists():
        raise UsageError(
            f"{path} already exists. Installing writes this file once; refusing to "
            f"overwrite state that is already there."
        )

    # Create the directory chain under the home, owning each level this creates.
    # Directories that already exist are left exactly as they are -- their owner
    # and mode are the user's business, not the installer's.
    #
    # 0700 matches what a session creates for ~/.local and ~/.local/state, and is
    # set explicitly rather than left to mkdir's mode argument, which the process
    # umask would otherwise mask down.
    def own(target: Path) -> None:
        # A state file left owned by root in somebody's home is broken in a way
        # that only shows up much later, so a chown that cannot happen is fatal
        # here rather than a warning.
        try:
            os.chown(target, uid, gid)
        except OSError as exc:
            raise UsageError(
                f"cannot give {target} to {uid}:{gid}: {exc}. "
                f"Writing another user's state requires root."
            ) from exc

    home = path.parent.parents[len(STATE_RELATIVE_DIR.parts) - 1]
    for part in STATE_RELATIVE_DIR.parts:
        home = home / part
        if home.exists():
            continue
        home.mkdir()
        os.chmod(home, 0o700)
        own(home)

    # Written whole, then moved into place: a half-written state file is worse
    # than none, and the executor that later reads it must never see one.
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(state.to_toml(), encoding="utf-8")
    os.chmod(temporary, 0o644)
    own(temporary)
    temporary.replace(path)

    print(
        json.dumps(
            {
                "install_state_schema_version": INSTALL_STATE_SCHEMA_VERSION,
                "written": True,
                "path": str(path),
                "uid": uid,
                "gid": gid,
                "preset_id": document["preset"]["id"],
                "selection": document["selection"],
                # Stated in the output so a caller can assert it rather than
                # trust it: an installer applies nothing, so it records nothing
                # as applied.
                "applied_recorded": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Enacting the managed part of a profile, in this user's session
# ---------------------------------------------------------------------------

# Exit statuses `apply` and `unmanage` add to the three above.
#
# 3 is its own status because a blocked resource is NOT a failed run. Kedra
# asked whether an accelerator was free, was told no, and correctly left it
# alone -- the desktop is in exactly the state the rules say it should be in.
# A systemd unit that treated that as failure would put a permanent red mark on
# a session whose only fault is that the user already uses Super+Enter for
# something else.
EXIT_BLOCKED = 3


def _gsettings_adapter():
    """The production backend. Imported here so nothing else pulls in GLib.

    Every other command in this program runs with no desktop at all, and must
    keep working on a machine with no PyGObject. Only these two need it.
    """
    from .gsettings import GSettingsAdapter

    return GSettingsAdapter()


def _run_activation(args, operation: str) -> int:
    state_path = (
        Path(args.state) if args.state is not None else state_mod.default_state_path()
    )

    try:
        result = activate(
            data_dir=Path(args.data_dir),
            state_path=state_path,
            adapter_factory=_gsettings_adapter,
            operation=operation,
            lock_timeout=args.lock_timeout,
        )
    except ActivationError as exc:
        raise UsageError(str(exc)) from exc

    if args.format == "json":
        _emit_json(result.to_dict())
    else:
        print(f"outcome:    {result.outcome}")
        if result.detail:
            print(f"            {result.detail}")
        if result.generation is not None:
            print(f"generation: {result.generation}")
        for label, values in (
            ("wrote", result.written),
            ("reverted", result.reverted),
            ("preserved", result.preserved),
        ):
            for value in values:
                print(f"{label + ':':<12}{value}")
        for resource, reason in sorted(result.blocked.items()):
            print(f"blocked:    {resource}: {reason}", file=sys.stderr)
        for setting_id, reason in sorted(result.failed.items()):
            print(f"FAILED:     {setting_id}: {reason}", file=sys.stderr)

    if result.outcome == OUTCOME_FAILED:
        return EXIT_ERROR
    if result.outcome == OUTCOME_BLOCKED:
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    """Enact this user's profile. Idempotent; safe to run at every login.

    Writes only managed resources, only where ownership allows, and only after
    the conflict gate says the accelerator is free. A run that finds everything
    already correct writes nothing and consumes no generation.
    """
    return _run_activation(args, OPERATION_APPLY)


def cmd_unmanage(args: argparse.Namespace) -> int:
    """Give back everything Kedra still owns, and stop claiming it.

    The supported way out. Each managed value returns to what Kedra recorded as
    its `original` -- unset if it was unset -- unless the user has since diverged
    from what Kedra last wrote, in which case their value stands and only the
    record is dropped. Kedra never removes something a person has made their
    own, even on the way out.
    """
    return _run_activation(args, OPERATION_UNMANAGE)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _emit_json(document: dict) -> None:
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _emit_profiles_text(document: dict) -> None:
    print(f"data:    {document['data_dir']}")
    print(f"default: {document['default_preset']}")
    for preset in document["presets"]:
        offer = "selectable" if preset["user_selectable"] else "not selectable"
        layer = "applies" if preset["applies_experience_layer"] else "no layer"
        print(f"\n{preset['id']}  ({preset['name']})  [{offer}, {layer}]")
        if not preset["modules"]:
            print("  modules: (none)")
            continue
        width = max(len(m["id"]) for m in preset["modules"])
        for module in preset["modules"]:
            mark = "on " if module["enabled"] else "off"
            pick = "user" if module["user_selectable"] else "  --"
            print(f"  {module['id']:<{width}}  {mark}  {pick}  {module['version']}")


def _emit_resolve_text(document: dict) -> None:
    preset = document["preset"]
    layer = "applied" if document["applies_experience_layer"] else "NOT APPLIED"
    print(f"preset:   {preset['id']} ({preset['version']})")
    print(f"chain:    {' -> '.join(preset['chain'])}")
    print(f"layer:    {layer}")
    packages = document["packages"]
    print(f"required: {', '.join(packages['required']) or '(none)'}")
    for relation in ("recommended", "optional", "conditional"):
        if packages[relation]:
            print(f"{relation + ':':<10}{', '.join(packages[relation])}")
    for entry in packages["unresolved"]:
        print(f"unresolved: {entry['name']} ({entry['condition']})")
    for application in document["applications"]:
        print(f"app:      {application['role']} -> {application['desktop_id']}")
    diagnostics = document["diagnostics"]
    for advisory in diagnostics["advisories"]:
        print(f"advisory: {advisory['message']}")
    # On stderr with the violations, not with the advisories: a refused override
    # means the caller asked for something it did not get, and the exit status
    # is non-zero because of it.
    for refusal in diagnostics["refused_overrides"]:
        print(f"REFUSED:  {refusal['message']}", file=sys.stderr)
    for violation in diagnostics["violations"]:
        print(f"ERROR:    {violation['message']}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_profiles(args: argparse.Namespace) -> int:
    document = build_profiles_document(_load_tree(args.data_dir))
    if args.format == "json":
        _emit_json(document)
    else:
        _emit_profiles_text(document)
    return EXIT_OK


def cmd_resolve(args: argparse.Namespace) -> int:
    tree = _load_tree(args.data_dir)
    state = parse_selection(read_selection(args.selection))
    document = build_resolve_document(tree, state)

    if args.format == "json":
        _emit_json(document)
    else:
        _emit_resolve_text(document)

    # The document is on stdout either way; the status distinguishes a profile
    # that is merely unusual from one that is wrong.
    return EXIT_OK if document["diagnostics"]["ok"] else EXIT_INVALID_PROFILE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Read-only Kedra Experience profile queries. Resolves profiles from "
            "the installed manifest tree and prints JSON. Changes nothing."
        ),
        epilog=(
            "Exit status: 0 resolved and valid; 1 nothing could be produced; "
            "2 document produced but the profile has requirement violations."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=installed_data_dir(),
        help=(
            "manifest tree to read (default: %(default)s). For development and "
            "testing only -- production callers must omit it, so that the answer "
            "depends on the installed packages and nothing else."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser(
        "profiles",
        help="list the profiles the installed tree defines and what each contains",
    )
    p.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the stable contract; text is for humans and may change",
    )
    p.set_defaults(func=cmd_profiles)

    p = subparsers.add_parser(
        "resolve",
        help="resolve a selection to modules, packages, applications and diagnostics",
    )
    p.add_argument(
        "--selection",
        required=True,
        metavar="PATH",
        help="JSON selection document; '-' reads standard input",
    )
    p.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the stable contract; text is for humans and may change",
    )
    p.set_defaults(func=cmd_resolve)

    p = subparsers.add_parser(
        "install-state",
        help="write a target user's selection into an installed system; creates "
        "exactly one file and touches no desktop configuration",
        description=(
            "Persist an Experience selection for a user in a target root. Run it "
            "after the account exists and after any /etc/skel copy that would "
            "overwrite the home directory. A selection the resolver would reject "
            "is not written."
        ),
    )
    p.add_argument(
        "--selection",
        required=True,
        metavar="PATH",
        help="JSON selection document; '-' reads standard input",
    )
    p.add_argument(
        "--root",
        required=True,
        metavar="DIR",
        help="the installed system's root, e.g. Calamares' ${ROOT}",
    )
    p.add_argument(
        "--user",
        required=True,
        metavar="NAME",
        help="the target account whose selection this is",
    )
    p.add_argument(
        "--uid",
        type=int,
        help="owner uid (default: read from the target's /etc/passwd)",
    )
    p.add_argument(
        "--gid",
        type=int,
        help="owner gid (default: read from the target's /etc/passwd)",
    )
    p.add_argument(
        "--home",
        metavar="PATH",
        help="the account's home, absolute and inside the root "
        "(default: read from the target's /etc/passwd)",
    )
    p.set_defaults(func=cmd_install_state)

    for verb, handler, summary in (
        (
            "apply",
            cmd_apply,
            "enact the managed part of this user's profile; writes only managed "
            "resources and never a vendor default",
        ),
        (
            "unmanage",
            cmd_unmanage,
            "give back everything Kedra still owns for this user, preserving "
            "anything they have since changed",
        ),
    ):
        p = subparsers.add_parser(verb, help=summary, description=summary)
        p.add_argument(
            "--state",
            type=Path,
            help="state file to act on (default: this user's, under $XDG_STATE_HOME)",
        )
        p.add_argument(
            "--lock-timeout",
            type=float,
            default=10.0,
            help="seconds to wait for the Experience state lock (default: %(default)s)",
        )
        p.add_argument("--format", choices=("json", "text"), default="text")
        p.set_defaults(func=handler)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        # Nothing partial reaches stdout: a caller parsing JSON sees an empty
        # stream and a non-zero status, never a truncated document.
        print(f"{PROGRAM}: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
