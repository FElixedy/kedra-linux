"""Internal developer CLI for the Kedra Experience data model.

NOT a user-facing profile manager, and not shipped in the package. It exists to
make resolution and state validation exercisable from tests and by hand. The
entry point is deliberately named `kedra-experience-dev` so it cannot be
mistaken for a supported command.

No subcommand reads or writes a path it was not given, with one exception:
`state-path` *prints* the default XDG location without touching it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import state as state_mod
from .applications import (
    application_contracts,
    declared_applications,
    terminal_launch_command,
)
from .backends import InMemoryAdapter
from .conflicts import (
    child_target,
    discover_custom_shortcuts,
    report_for_shortcut,
)
from .execute import ExecutionError, execute_plan
from .locking import StateLocked, state_lock
from .manifests import Report, SCHEMA_VERSION, default_data_dir, load_tree
from .manifests import main as validate_main
from .observed import ObservedError, load_observed
from .ownership import OPERATION_APPLY, OPERATION_RESTORE, OPERATION_UNMANAGE
from .packages import package_requirements, required_packages
from .plan import build_plan
from .resolve import ResolutionError, diagnose, resolve_preset, resolve_state
from .shortcuts import CHILD_KEYS, declared_shortcuts, expand, module_shortcuts
from .values import TypedValue
from .vendordefaults import (
    OverrideError,
    modules_with_vendor_defaults,
    override_filename,
    render_module,
)


def _load_tree_or_exit(data_dir: Path) -> object:
    report = Report()
    tree = load_tree(Path(data_dir), report)
    if not report.ok:
        print(report.rendered(), file=sys.stderr)
        print(f"\n{len(report.errors)} error(s)", file=sys.stderr)
        raise SystemExit(1)
    return tree


def cmd_validate_manifests(args: argparse.Namespace) -> int:
    return validate_main(["validate-manifests", str(args.data_dir)])


def cmd_resolve(args: argparse.Namespace) -> int:
    tree = _load_tree_or_exit(args.data_dir)

    try:
        if args.state is not None:
            user_state = state_mod.UserState.load(Path(args.state))
            resolution = resolve_state(tree, user_state)
        else:
            preset_id = args.preset or tree.default_preset
            if preset_id is None:
                print("no preset given and no default_preset in the tree", file=sys.stderr)
                return 1
            resolution = resolve_preset(tree, preset_id)
    except (ResolutionError, state_mod.StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Both severities, reported and never repaired: the resolver does not
    # force-enable a module the user switched off. Saying so beats silently
    # undoing their choice.
    diagnostics = diagnose(tree, resolution)

    if args.format == "json":
        payload = resolution.to_dict()
        payload["diagnostics"] = diagnostics.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        layer = "applied" if resolution.applies_experience_layer else "NOT APPLIED"
        print(f"preset:  {resolution.preset_id} ({resolution.preset_version})")
        print(f"chain:   {' -> '.join(resolution.chain)}")
        print(f"layer:   {layer}")
        if not resolution.modules:
            print("modules: (none)")
        else:
            print("modules:")
            width = max(len(m.id) for m in resolution.modules)
            for module in resolution.modules:
                mark = "enabled " if module.enabled else "disabled"
                print(f"  {module.id:<{width}}  {mark}  {module.version:<8} [{module.source}]")

        # Advisories are printed on stdout with the rest of the report;
        # violations go to stderr, because only they mean the profile is wrong.
        if diagnostics.advisories:
            print("advisories:")
            for advisory in diagnostics.advisories:
                print(f"  {advisory.describe()}")
        if diagnostics.violations:
            print("errors:", file=sys.stderr)
            for violation in diagnostics.violations:
                print(f"  {violation.describe()}", file=sys.stderr)

    # Advisories never affect exit status; a hard requirement violation is a
    # genuinely invalid profile and does.
    return 0 if diagnostics.ok else 1


def cmd_validate_state(args: argparse.Namespace) -> int:
    tree = None if args.no_tree else _load_tree_or_exit(args.data_dir)

    report = Report()
    state_mod.validate_state_file(Path(args.state), report, tree=tree)
    if not report.ok:
        print(report.rendered(), file=sys.stderr)
        print(f"\n{len(report.errors)} error(s)", file=sys.stderr)
        return 1
    print(f"kedra-experience: state valid ({args.state})")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Produce a change plan. Reads fixtures only; changes nothing."""
    tree = _load_tree_or_exit(args.data_dir)

    try:
        user_state = state_mod.UserState.load(Path(args.state))
        observed = load_observed(args.observed) if args.observed else {}
        resolution = resolve_state(tree, user_state)
        plan = build_plan(
            tree=tree,
            resolution=resolution,
            user_state=user_state,
            observed=observed,
            operation=args.operation,
            conflicts=_conflict_reports(tree, resolution, observed),
        )
    except (ResolutionError, state_mod.StateError, ObservedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(plan.to_json())
        return 0

    print(f"operation:  {plan.operation}")
    print(f"generation: {plan.generation}")
    if not plan.entries:
        print("no settings in scope")
        return 0
    print(f"entries:    {len(plan.entries)} "
          f"({len(plan.writes)} write, {len(plan.requires_confirmation)} need confirmation, "
          f"{len(plan.blocked)} blocked by conflict)")
    for resource, report in sorted(plan.conflicts.items()):
        if report.blocked:
            print(f"conflict:   {resource}: {report.block_reason}")
    width = max(len(e.setting_id) for e in plan.entries)
    for entry in plan.entries:
        flag = "!" if entry.requires_confirmation else " "
        print(f" {flag} {entry.setting_id:<{width}}  {entry.decision:<20}  {entry.reason}")
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    """Execute a plan against a simulated backend.

    Offline by construction: the backend is an in-memory fake seeded from the
    observed fixture, so this can never reach a live session. It exists to
    demonstrate and test the execution path end to end.
    """
    tree = _load_tree_or_exit(args.data_dir)
    state_path = Path(args.state)

    try:
        # The lock spans load -> plan -> execute -> save, which is the whole
        # read-decide-write cycle. Locking only the save would leave two
        # processes free to plan from the same stale state.
        with state_lock(state_path, timeout=args.lock_timeout):
            user_state = state_mod.UserState.load(state_path)
            observed = load_observed(args.observed) if args.observed else {}
            resolution = resolve_state(tree, user_state)
            plan = build_plan(
                tree=tree,
                resolution=resolution,
                user_state=user_state,
                observed=observed,
                operation=args.operation,
                conflicts=_conflict_reports(tree, resolution, observed),
            )

            adapter = InMemoryAdapter(values={t: v for t, v in observed.items() if v})
            result = execute_plan(
                plan=plan,
                adapters={"gsettings": adapter},
                tree=tree,
                resolution=resolution,
                user_state=user_state,
                experience_schema_version=SCHEMA_VERSION,
                applied_at=args.applied_at,
            )

            if result.committed and not args.dry_run:
                result.state.save(state_path)
    except (
        ResolutionError,
        state_mod.StateError,
        ObservedError,
        ExecutionError,
        StateLocked,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        payload = result.to_dict()
        payload["simulated_backend"] = {
            target: value.to_dict() for target, value in sorted(adapter.snapshot().items())
        }
        payload["backend_operations"] = [list(op) for op in adapter.operations]
        payload["state_written"] = bool(result.committed and not args.dry_run)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"operation:  {result.operation}")
        print(f"generation: {result.generation if result.committed else '(not committed)'}")
        print(
            f"changes:    {len(result.changes)} "
            f"({len(result.writes)} wrote, {len(result.failures)} failed)"
        )
        for change in result.changes:
            mark = "ok " if change.succeeded else "ERR"
            print(
                f"  {mark} {change.setting_id}  {change.decision} "
                f"-> record {change.record_action}"
                + (f"  [{change.error}]" if change.error else "")
            )
        print("simulated backend:")
        snapshot = adapter.snapshot()
        if not snapshot:
            print("  (empty)")
        for target, value in sorted(snapshot.items()):
            print(f"  {target} = {value.render()}")
        print(f"state written: {bool(result.committed and not args.dry_run)}")

    return 0 if result.ok else 1


def _fixture_adapter(observed: dict) -> InMemoryAdapter:
    """An offline stand-in for a live session, built from an observed fixture.

    Seeded as *defaults* rather than explicit values, because that is what an
    observed fixture describes: what the system reads as. Child keys of every
    registered path are additionally defaulted to the empty string, which is
    what a real relocatable schema instance returns for a key nobody has
    written -- without that, the fake would report a perfectly ordinary stale
    shortcut as unreadable and the tool would disagree with the desktop.
    """
    defaults = {target: value for target, value in observed.items() if value is not None}

    registered = defaults.get(_conflicts_parent_target())
    paths = tuple(registered.data) if registered is not None else ()
    for path in paths:
        for key in CHILD_KEYS:
            defaults.setdefault(child_target(path, key), TypedValue("s", ""))

    return InMemoryAdapter(defaults=defaults)


def _conflicts_parent_target() -> str:
    from .conflicts import PARENT_TARGET

    return PARENT_TARGET


def _conflict_reports(tree, resolution, observed: dict) -> dict:
    """A conflict report per shortcut the profile wants, from the fixture.

    Genuinely *checked*: discovery runs against the fixture exactly as it would
    against a session. A caller that supplies no fixture describes a system with
    no custom shortcuts at all, which is a real system and not an unchecked one.
    """
    adapter = _fixture_adapter(observed)
    enabled = {m.id for m in resolution.modules if m.enabled}
    reports = {}
    for module_id in sorted(enabled):
        manifest = tree.modules.get(module_id)
        if manifest is None:
            continue
        for shortcut in module_shortcuts(module_id, manifest, tree.modules):
            reports[shortcut.id] = report_for_shortcut(adapter, shortcut)
    return reports


def cmd_shortcut_conflicts(args: argparse.Namespace) -> int:
    """Report accelerator conflicts for a profile's shortcuts, from a fixture.

    Read-only twice over: discovery performs no writes by construction, and the
    backend here is an in-memory fake seeded from an observed-value fixture, so
    there is no path from this command to a live session at all.
    """
    tree = _load_tree_or_exit(args.data_dir)

    try:
        user_state = state_mod.UserState.load(Path(args.state))
        observed = load_observed(args.observed) if args.observed else {}
        resolution = resolve_state(tree, user_state)
    except (ResolutionError, state_mod.StateError, ObservedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    adapter = _fixture_adapter(observed)
    discovery = discover_custom_shortcuts(adapter)
    reports = _conflict_reports(tree, resolution, observed)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "preset_id": resolution.preset_id,
                    "discovery": discovery.to_dict(),
                    "conflicts": {
                        resource: report.to_dict()
                        for resource, report in sorted(reports.items())
                    },
                    # Proof, in the output itself, that looking cost no writes.
                    "backend_writes": [list(op) for op in adapter.writes],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if not any(r.blocked for r in reports.values()) else 1

    print(f"preset:     {resolution.preset_id}")
    print(f"registered: {len(discovery.registered_paths)} custom shortcut path(s)")
    for shortcut in discovery.shortcuts:
        state_label = shortcut.binding_state
        print(f"  {shortcut.path}")
        print(f"    name={shortcut.name!r} binding={shortcut.binding!r} [{state_label}]")
        if shortcut.problems:
            print(f"    problems: {'; '.join(shortcut.problems)}")
    if discovery.duplicate_paths:
        print(f"  duplicated in the list (left as-is): {', '.join(discovery.duplicate_paths)}")
    if not discovery.registered_paths:
        print("  (none)")

    if not reports:
        print("shortcuts:  (this profile declares none)")
        return 0

    print("conflicts:")
    for resource, report in sorted(reports.items()):
        verdict = "BLOCKED" if report.blocked else "clear"
        print(f"  {resource}  {report.accelerator or '(unbound)'}  -> {verdict}")
        if report.blocked:
            print(f"    {report.block_reason}")
        for finding in report.findings:
            mark = "!" if finding.blocking else "-"
            print(f"    {mark} [{finding.type}] {finding.reason}")

    print(f"backend writes during discovery: {len(adapter.writes)}")
    return 0 if not any(r.blocked for r in reports.values()) else 1


def cmd_vendor_defaults(args: argparse.Namespace) -> int:
    """Render the GSettings vendor override for one module, or for all of them.

    One file per module, because that is the delivery unit -- each module's
    defaults package installs its own. Used by those PKGBUILDs, which is why an
    explicit output path is required for a file: a packaging step that can guess
    its own output location is a packaging step that can write outside $pkgdir.
    """
    tree = _load_tree_or_exit(args.data_dir)
    available = modules_with_vendor_defaults(tree)

    if args.list:
        for module_id in available:
            print(f"{module_id}\t{override_filename(module_id)}")
        return 0

    if args.module is not None:
        if args.module not in available:
            print(
                f"error: module '{args.module}' declares no vendor defaults "
                f"(modules that do: {', '.join(available) or 'none'})",
                file=sys.stderr,
            )
            return 1
        selected = [args.module]
    else:
        selected = available

    if not selected:
        print("error: no module in this tree declares vendor defaults", file=sys.stderr)
        return 1

    try:
        rendered = {module_id: render_module(tree, module_id) for module_id in selected}
    except OverrideError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output is None:
        for index, module_id in enumerate(selected):
            if len(selected) > 1:
                print(f"{'' if index == 0 else chr(10)}# ==> {override_filename(module_id)}")
            sys.stdout.write(rendered[module_id])
        return 0

    output = Path(args.output)
    if output.is_dir():
        for module_id in selected:
            path = output / override_filename(module_id)
            path.write_text(rendered[module_id], encoding="utf-8")
            print(f"kedra-experience: wrote {path}", file=sys.stderr)
        return 0

    if len(selected) != 1:
        print(
            "error: --output names a single file, so exactly one --module must be "
            "selected (or give a directory)",
            file=sys.stderr,
        )
        return 1
    output.write_text(rendered[selected[0]], encoding="utf-8")
    print(f"kedra-experience: wrote {output}", file=sys.stderr)
    return 0


def cmd_packages(args: argparse.Namespace) -> int:
    """Show the package set a resolved profile refers to. Queries no database."""
    tree = _load_tree_or_exit(args.data_dir)

    try:
        if args.state is not None:
            resolution = resolve_state(tree, state_mod.UserState.load(Path(args.state)))
        else:
            preset_id = args.preset or tree.default_preset
            if preset_id is None:
                print("no preset given and no default_preset in the tree", file=sys.stderr)
                return 1
            resolution = resolve_preset(tree, preset_id)
    except (ResolutionError, state_mod.StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    requirements = package_requirements(tree, resolution)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "preset_id": resolution.preset_id,
                    "applies_experience_layer": resolution.applies_experience_layer,
                    "required": list(required_packages(tree, resolution)),
                    "requirements": [
                        {
                            "name": r.name,
                            "relation": r.relation,
                            "module_id": r.module_id,
                            "reason": r.reason,
                            "unresolved_condition": r.unresolved_condition,
                        }
                        for r in requirements
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"preset:   {resolution.preset_id}")
    print(f"required: {', '.join(required_packages(tree, resolution)) or '(none)'}")
    if not requirements:
        print("relations: (none)")
        return 0
    print("relations:")
    width = max(len(r.name) for r in requirements)
    for requirement in requirements:
        mark = "" if requirement.resolved else "  [condition not evaluated here]"
        print(
            f"  {requirement.name:<{width}}  {requirement.relation:<12} "
            f"[{requirement.module_id}]{mark}"
        )
    return 0


def cmd_applications(args: argparse.Namespace) -> int:
    """Show the application contracts a profile resolves to. Launches nothing.

    Two lists, deliberately distinguished rather than merged:

    `declared`  every contract in the manifest tree, regardless of activation.
                What the distribution offers.
    `effective` the contracts the *enabled* modules contribute. What this
                profile actually gets -- empty for Vanilla, which applies no
                module, even though the declaration exists in the tree.

    Conflating the two is precisely how a Vanilla system would acquire a Kedra
    dependency from a declaration it never enabled.
    """
    tree = _load_tree_or_exit(args.data_dir)

    try:
        if args.state is not None:
            resolution = resolve_state(tree, state_mod.UserState.load(Path(args.state)))
        else:
            preset_id = args.preset or tree.default_preset
            if preset_id is None:
                print("no preset given and no default_preset in the tree", file=sys.stderr)
                return 1
            resolution = resolve_preset(tree, preset_id)
    except (ResolutionError, state_mod.StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    declared = declared_applications(tree)
    effective = application_contracts(tree, resolution)

    def payload(contract):
        return {
            "role": contract.role,
            "desktop_id": contract.desktop_id,
            "application_id": contract.application_id,
            "package": contract.package,
            "module_id": contract.module_id,
            "reason": contract.reason,
            # Derived, never declared, and shown so the derivation is
            # inspectable rather than something to take on trust.
            "launch_command": contract.launch_command,
        }

    if args.format == "json":
        print(
            json.dumps(
                {
                    "preset_id": resolution.preset_id,
                    "applies_experience_layer": resolution.applies_experience_layer,
                    "declared": [payload(c) for c in declared],
                    "effective": [payload(c) for c in effective],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"preset:    {resolution.preset_id}")
    print("declared:  (every contract in the tree, activation ignored)")
    for contract in declared or ():
        print(
            f"  {contract.role:<10} {contract.desktop_id}  "
            f"[{contract.module_id}] -> {contract.package}"
        )
    if not declared:
        print("  (none)")
    print("effective: (contracts this profile's enabled modules contribute)")
    for contract in effective or ():
        print(
            f"  {contract.role:<10} {contract.desktop_id}  "
            f"[{contract.module_id}] -> {contract.package}"
        )
        print(f"             launch: {contract.launch_command}")
    if not effective:
        print("  (none)")

    launch = terminal_launch_command(tree, resolution)
    print(f"terminal launch command: {launch if launch else '(none -- no Kedra terminal)'}")
    return 0


def cmd_shortcuts(args: argparse.Namespace) -> int:
    """Show the custom-shortcut resources a profile declares, and what they expand to.

    Read-only and offline in the strongest sense: it resolves a profile, expands
    the declarations, and prints them. It reads no dconf, opens no GSettings and
    launches nothing -- there is no code path from here that could.

    Deliberately not a terminal selector and deliberately not an apply command.
    """
    tree = _load_tree_or_exit(args.data_dir)

    try:
        if args.state is not None:
            resolution = resolve_state(tree, state_mod.UserState.load(Path(args.state)))
        else:
            preset_id = args.preset or tree.default_preset
            if preset_id is None:
                print("no preset given and no default_preset in the tree", file=sys.stderr)
                return 1
            resolution = resolve_preset(tree, preset_id)
    except (ResolutionError, state_mod.StateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    enabled = {m.id for m in resolution.modules if m.enabled}
    declared = declared_shortcuts(tree)
    effective = [s for s in declared if s.module_id in enabled]

    def payload(shortcut):
        return {
            "id": shortcut.id,
            "module_id": shortcut.module_id,
            "policy": shortcut.policy,
            "path": shortcut.path,
            "name": shortcut.name,
            "binding": shortcut.binding,
            "command": shortcut.command,
            "command_role": shortcut.command_role,
            "settings": [
                {
                    "id": entry["id"],
                    "target": entry["target"],
                    "value": entry["value"],
                    "member": entry.get("member"),
                }
                for entry in expand(shortcut)
            ],
        }

    if args.format == "json":
        print(
            json.dumps(
                {
                    "preset_id": resolution.preset_id,
                    "applies_experience_layer": resolution.applies_experience_layer,
                    "declared": [payload(s) for s in declared],
                    "effective": [payload(s) for s in effective],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"preset:    {resolution.preset_id}")
    print("declared:  (every shortcut in the tree, activation ignored)")
    for shortcut in declared or ():
        print(f"  {shortcut.id}  [{shortcut.module_id}]  policy={shortcut.policy}")
    if not declared:
        print("  (none)")

    print("effective: (shortcuts this profile's enabled modules contribute)")
    if not effective:
        print("  (none)")
    for shortcut in effective:
        source = (
            f"derived from application role '{shortcut.command_role}'"
            if shortcut.command_role
            else "literal"
        )
        print(f"  {shortcut.id}  [{shortcut.module_id}]")
        print(f"    path:    {shortcut.path}")
        print(f"    name:    {shortcut.name}")
        print(f"    binding: {shortcut.binding}")
        print(f"    command: {shortcut.command}   ({source})")
        print("    expands to:")
        for entry in expand(shortcut):
            kind = "membership" if entry.get("member") else "value"
            print(f"      {entry['id']:<38} {kind:<10} {entry['value']}")
    return 0


def cmd_module_version(args: argparse.Namespace) -> int:
    """Print one module's declared version. Reads the manifests, nothing else.

    Exists so a generated package's PKGBUILD can assert that its `pkgver` still
    matches the module it renders. Both vendor-default packages install a file
    produced from a manifest, so a manifest edit changes the shipped bytes --
    and publishing those bytes at an unchanged version would mean overwriting an
    immutable artifact. Making the version answerable from data is what turns
    "remember to bump it" into a build failure.
    """
    tree = _load_tree_or_exit(args.data_dir)
    manifest = tree.modules.get(args.module)
    if manifest is None:
        print(
            f"error: no module '{args.module}' in {args.data_dir} "
            f"(modules: {', '.join(sorted(tree.modules)) or 'none'})",
            file=sys.stderr,
        )
        return 1
    print(manifest["version"])
    return 0


def cmd_state_path(args: argparse.Namespace) -> int:
    # Computes and prints only; nothing is created or read.
    print(state_mod.default_state_path())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kedra-experience-dev",
        description="Internal developer tooling for Kedra Experience metadata. "
        "Not a user-facing profile manager.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="manifest tree to work against (default: in-tree package data)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("validate-manifests", help="validate the manifest tree")
    p.set_defaults(func=cmd_validate_manifests)

    p = subparsers.add_parser(
        "resolve", help="resolve a preset or a user state file to its effective module set"
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument("--preset", help="preset ID from the manifest tree")
    source.add_argument("--state", type=Path, help="path to a user state file")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_resolve)

    p = subparsers.add_parser("validate-state", help="validate a user state file")
    p.add_argument("state", type=Path, help="path to the state file (never implicit)")
    p.add_argument(
        "--no-tree",
        action="store_true",
        help="check shape only, without resolving references against the manifests",
    )
    p.set_defaults(func=cmd_validate_state)

    p = subparsers.add_parser(
        "plan",
        help="produce a side-effect-free change plan from a state file and an "
        "observed-value fixture",
    )
    p.add_argument("state", type=Path, help="path to a user state file")
    p.add_argument(
        "observed",
        type=Path,
        nargs="?",
        help="path to an observed-value fixture; omitted means every target is unset. "
        "Never read from a live GNOME session.",
    )
    p.add_argument(
        "--operation",
        choices=(OPERATION_APPLY, OPERATION_UNMANAGE, OPERATION_RESTORE),
        default=OPERATION_APPLY,
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_plan)

    p = subparsers.add_parser(
        "execute",
        help="execute a plan against a simulated in-memory backend and update the "
        "given state file; never touches a live desktop",
    )
    p.add_argument("state", type=Path, help="path to a user state file (rewritten in place)")
    p.add_argument(
        "observed",
        type=Path,
        nargs="?",
        help="observed-value fixture; also seeds the simulated backend",
    )
    p.add_argument(
        "--operation",
        choices=(OPERATION_APPLY, OPERATION_UNMANAGE, OPERATION_RESTORE),
        default=OPERATION_APPLY,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="run the executor but do not write the state file",
    )
    p.add_argument("--applied-at", help="fixed ISO 8601 timestamp, for reproducible output")
    p.add_argument(
        "--lock-timeout",
        type=float,
        default=0.0,
        help="seconds to wait for the state lock (default: fail immediately)",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_execute)

    p = subparsers.add_parser(
        "vendor-defaults",
        help="render a module's GSettings vendor override from its policy "
        "'default' settings; writes no system path",
    )
    p.add_argument(
        "--module",
        help="module to render (default: every module that has vendor defaults)",
    )
    p.add_argument(
        "--output",
        type=Path,
        help="file to write, or a directory to write each module's canonical "
        "filename into (default: standard output)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="list the modules that have vendor defaults, and their filenames",
    )
    p.set_defaults(func=cmd_vendor_defaults)

    p = subparsers.add_parser(
        "packages",
        help="show the package set a resolved profile refers to; consults no "
        "package database and installs nothing",
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument("--preset", help="preset ID from the manifest tree")
    source.add_argument("--state", type=Path, help="path to a user state file")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_packages)

    p = subparsers.add_parser(
        "applications",
        help="show the application contracts a resolved profile refers to; "
        "reads no desktop-file database and launches nothing",
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument("--preset", help="preset ID from the manifest tree")
    source.add_argument("--state", type=Path, help="path to a user state file")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_applications)

    p = subparsers.add_parser(
        "shortcuts",
        help="show the custom-shortcut resources a resolved profile declares and "
        "the settings they expand into; reads no dconf and applies nothing",
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument("--preset", help="preset ID from the manifest tree")
    source.add_argument("--state", type=Path, help="path to a user state file")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_shortcuts)

    p = subparsers.add_parser(
        "shortcut-conflicts",
        help="report accelerator conflicts between a profile's shortcuts and the "
        "custom shortcuts an observed fixture describes; performs no writes and "
        "never reads a live session",
    )
    p.add_argument("state", type=Path, help="path to a user state file")
    p.add_argument(
        "observed",
        type=Path,
        nargs="?",
        help="observed-value fixture describing the registered custom shortcuts; "
        "omitted means a system with none",
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=cmd_shortcut_conflicts)

    p = subparsers.add_parser(
        "module-version",
        help="print a module's declared version, for a generated package to "
        "check its own pkgver against",
    )
    p.add_argument("--module", required=True, help="module ID from the manifest tree")
    p.set_defaults(func=cmd_module_version)

    p = subparsers.add_parser(
        "state-path", help="print the default XDG state path (does not create or read it)"
    )
    p.set_defaults(func=cmd_state_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
