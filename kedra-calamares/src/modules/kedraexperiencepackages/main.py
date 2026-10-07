#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# === This file is part of Calamares - <https://calamares.io> ===
#
#   SPDX-FileCopyrightText: 2026 Kedra Linux
#   SPDX-License-Identifier: GPL-3.0-or-later
#
"""Turn the chosen Kedra Experience profile into target package additions.

This module is the whole of Calamares' knowledge about the Kedra Experience,
and it is deliberately almost nothing: it hands a selection to the shipped
`kedra-experience` resolver and installs whatever `packages.required` comes
back.

WHAT IS NOT HERE, AND MUST NEVER BE
-----------------------------------
No module names. No package names. No GNOME settings. No application contracts.
No idea which modules belong to which profile, or which of them a user may
switch. Every one of those questions is answered by the manifest tree that
`kedra-experience` installs, and answering any of them a second time here would
create a copy that drifts -- silently, and only on installed systems.

Grep this file for `kedra-experience-workflow` and the like: the only Kedra
package name that appears is the resolver's own path, which is configuration.

WHY AT EXEC TIME RATHER THAN IN THE PAGE
----------------------------------------
The view module collects a selection and nothing else. Resolving here means the
authoritative answer is computed from the resolver that is actually installed,
at the moment the packages are about to be chosen, and that a failure aborts the
installation loudly instead of leaving a page in a bad state. It also keeps the
C++ side to a single QProcess call.

This job must run BEFORE `packages@online`, which is what consumes
`packageOperations`.

FAIL CLOSED
-----------
Every failure mode ends the installation with an error. There is no path from
"the resolver could not be run", "the JSON was malformed", "the profile is
invalid", or "a module vanished between listing and resolving" to a Vanilla
install or to a partial package set. A user who asked for Kedra either gets
Kedra or gets told why not.

Failing closed also means refusing to GUESS a type. `bool(x)` is not validation:
it reads a missing field as False and the string "false" as True, so a resolver
that answered `"applies_experience_layer": "false"` would produce a Kedra
install that recorded no profile. Every field this module acts on is therefore
required to have exactly the JSON type it is documented to have -- see the
validators below -- and anything else stops the installation before one byte of
GlobalStorage is touched.

ONE MODULE, ONE OPERATION, ALWAYS RECONCILED
--------------------------------------------
The Experience page can be revisited: a user may resolve Kedra, go back and pick
Vanilla. So this job may run more than once in one installer process, and what
it leaves behind must depend only on the LAST answer.

Every successful resolve therefore reconciles first and adds second: any package
operation this module owns is removed, and only then is the newly resolved one
(if any) installed. Removing only when the new set is non-empty would leave a
Kedra operation standing behind a Vanilla choice, and `packages@online` would
install the Experience into a system the user asked to keep vanilla. Operations
belonging to other modules are never touched.
"""

import json
import os
import subprocess

import libcalamares

import gettext

_ = gettext.translation(
    "calamares-python",
    localedir=libcalamares.utils.gettext_path(),
    languages=libcalamares.utils.gettext_languages(),
    fallback=True,
).gettext


# GlobalStorage key the view module writes the chosen selection into, as a JSON
# string. Absent means the Experience page never ran -- an installer
# configuration without it -- which is not an error: there is simply nothing to
# resolve.
GS_SELECTION = "kedraExperienceSelection"

# Written back for the later kedraexperiencestate job, so the selection that was
# actually resolved is the one that gets persisted.
GS_RESOLVED_SELECTION = "kedraExperienceResolvedSelection"
GS_APPLIES_LAYER = "kedraExperienceAppliesLayer"

# The `source` this module stamps on the package operation it owns. It is what
# makes a re-run able to find and remove its own earlier answer without touching
# anybody else's.
MODULE_ID = "kedraexperiencepackages"

# The resolver's exit statuses. Part of its documented contract.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID_PROFILE = 2

RESOLVE_TIMEOUT_SECONDS = 60


def pretty_name():
    return _("Resolving the Kedra Experience profile.")


class ResolverError(Exception):
    """Anything that makes the resolver's answer unusable."""

    def __init__(self, title, message):
        super().__init__(message)
        self.title = title
        self.message = message


def _config(key, default=None):
    return libcalamares.job.configuration.get(key, default)


# ---------------------------------------------------------------------------
# Exact JSON types
#
# `isinstance(True, int)` is True in Python, so an integer check has to exclude
# bool explicitly or `"resolve_schema_version": true` would be read as 1.
# ---------------------------------------------------------------------------


def _is_bool(value):
    return isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _malformed(what):
    """The one failure the shape checks below raise."""
    return ResolverError(
        _("Kedra Experience resolver failed"),
        _("The resolver's answer is malformed: {!s}.").format(what),
    )


def _run_resolver(resolver, selection_json):
    """Invoke `kedra-experience resolve` and return the parsed document.

    Raises ResolverError with a message fit for a user on every failure path.
    """
    if not os.path.exists(resolver):
        raise ResolverError(
            _("Kedra Experience resolver missing"),
            _(
                "The Kedra Experience resolver was not found at <pre>{!s}</pre>. "
                "The installation medium is incomplete: this program is installed "
                "by the kedra-experience-runtime package."
            ).format(resolver),
        )

    try:
        completed = subprocess.run(
            [resolver, "resolve", "--selection", "-"],
            input=selection_json,
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver <pre>{!s}</pre> could not be run: {!s}").format(
                resolver, exc
            ),
        )
    except subprocess.TimeoutExpired:
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver <pre>{!s}</pre> did not finish in time.").format(resolver),
        )

    libcalamares.utils.debug(f"resolver exit {completed.returncode}")
    if completed.stderr:
        libcalamares.utils.debug(f"resolver stderr: {completed.stderr.strip()}")

    # Exit 1: nothing was produced. Exit 2: a document was produced and it says
    # the profile is invalid. Both stop the installation, with different text,
    # because the user can act on them differently.
    if completed.returncode == EXIT_ERROR:
        raise ResolverError(
            _("Kedra Experience profile could not be resolved"),
            _(
                "The selected profile could not be resolved and the installation "
                "cannot continue.<br/><br/><pre>{!s}</pre>"
            ).format(completed.stderr.strip() or "no output"),
        )
    if completed.returncode not in (EXIT_OK, EXIT_INVALID_PROFILE):
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver exited with an unexpected status {!s}.").format(
                completed.returncode
            ),
        )

    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver produced output that is not valid JSON: {!s}").format(exc),
        )
    if not isinstance(document, dict):
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver produced output of an unexpected shape."),
        )

    return completed.returncode, document


def _check_schema(document):
    """Refuse a document shape this installer was not written against.

    A resolver newer than the installer may have changed what `packages.required`
    or `diagnostics.ok` mean. Guessing is exactly the failure the version field
    exists to prevent.
    """
    supported = _config("supportedResolveSchemas", [2])
    version = document.get("resolve_schema_version")
    # An exact integer, or the version was not stated at all: `true` and `"2"`
    # are not version 2, and must not be read as one.
    if not _is_int(version) or version not in supported:
        raise ResolverError(
            _("Kedra Experience version mismatch"),
            _(
                "The Kedra Experience resolver answered with document version "
                "{!s}, which this installer does not understand (it supports "
                "{!s}). The installer and kedra-experience-runtime are out of step."
            ).format(version, ", ".join(str(s) for s in supported)),
        )


def _check_valid(returncode, document):
    """The resolver decides whether a selection is legal. Not this module."""
    diagnostics = document.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver's answer carries no diagnostics."),
        )

    # Types before meaning. `diagnostics.ok` decides whether an installation
    # continues, so "missing" and "the string 'false'" must not both become a
    # verdict -- they are malformed answers and stop the installation as such.
    if not _is_bool(diagnostics.get("ok")):
        raise _malformed(_("diagnostics.ok is not a boolean"))
    for key in ("refused_overrides", "violations"):
        if not isinstance(diagnostics.get(key), list):
            raise _malformed(_("diagnostics.{!s} is not a list").format(key))
    # Not consumed here, but shown to a user wherever it is: still checked when
    # present, because a malformed advisory means a malformed document.
    if "advisories" in diagnostics and not isinstance(diagnostics["advisories"], list):
        raise _malformed(_("diagnostics.advisories is not a list"))

    refused = diagnostics["refused_overrides"]
    violations = diagnostics["violations"]
    for entry in list(refused) + list(violations):
        if not isinstance(entry, dict):
            raise _malformed(_("a diagnostics entry is not an object"))
        if "message" in entry and not isinstance(entry["message"], str):
            raise _malformed(_("a diagnostics message is not a string"))

    if returncode == EXIT_INVALID_PROFILE or not diagnostics["ok"]:
        problems = [
            entry.get("message", "")
            for entry in list(refused) + list(violations)
        ]
        raise ResolverError(
            _("Kedra Experience profile is not valid"),
            _(
                "The selected combination of Kedra Experience modules was "
                "rejected and the installation cannot continue.<br/><br/>{!s}"
            ).format("<br/>".join(problems) or _("No further detail was given.")),
        )


def _required_packages(document):
    """`packages.required`, checked for shape. The authoritative list."""
    packages = document.get("packages")
    if not isinstance(packages, dict):
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver's answer carries no package set."),
        )
    required = packages.get("required")
    if not isinstance(required, list) or not all(
        isinstance(name, str) and name for name in required
    ):
        raise ResolverError(
            _("Kedra Experience resolver failed"),
            _("The resolver's package list is malformed."),
        )
    return required


def _applies_experience_layer(document):
    """Whether this profile applies the Experience layer. Exactly a boolean.

    This is the field the later state job checks its own result against, so a
    coerced value here becomes a system whose recorded profile and whose
    installed packages disagree.
    """
    applies = document.get("applies_experience_layer")
    if not _is_bool(applies):
        raise _malformed(_("applies_experience_layer is not a boolean"))
    return applies


def _resolved_selection(document):
    """The selection the resolver actually resolved, handed to the state job."""
    selection = document.get("selection")
    if not isinstance(selection, dict):
        raise _malformed(_("the resolved selection is not an object"))
    return selection


def _preset_id(document):
    """`preset.id`, which names the profile in the log and in the state file."""
    preset = document.get("preset")
    if not isinstance(preset, dict):
        raise _malformed(_("the resolved preset is not an object"))
    if not isinstance(preset.get("id"), str) or not preset["id"]:
        raise _malformed(_("the resolved preset has no name"))
    return preset["id"]


def _owned_by_this_module(operation):
    return isinstance(operation, dict) and operation.get("source") == MODULE_ID


def _reconcile_package_operations(names):
    """Make GlobalStorage carry exactly the operation this resolve calls for.

    Removal comes first and is unconditional. That is the whole point: a re-run
    that resolves to nothing must leave nothing behind, and a re-run that
    resolves to a smaller set must not leave the larger one standing beside it.
    Only operations stamped with this module's own `source` are removed;
    netinstall's and the desktop chooser's are never touched.

    The operation added is the same shape Calamares' own C++ helper writes, so
    the `packages` module needs no special case: a list of operations, each a
    dict of action -> names plus the module that asked. Appended rather than
    assigned, because the netinstall page and the desktop chooser have already
    put theirs there.

    Duplicates against the GNOME group are left alone deliberately. pacman
    resolves a package that is requested twice by installing it once, and
    filtering here would mean this module holding an opinion about what the
    other lists contain -- which is the coupling the whole design avoids.
    """
    present = libcalamares.globalstorage.contains("packageOperations")
    operations = []
    if present:
        operations = list(libcalamares.globalstorage.value("packageOperations") or [])

    kept = [op for op in operations if not _owned_by_this_module(op)]
    dropped = len(operations) - len(kept)
    if dropped:
        libcalamares.utils.debug(
            f"removed {dropped} earlier {MODULE_ID} package operation(s)"
        )

    if names:
        kept.append({"install": list(names), "source": MODULE_ID})

    # Nothing to say and nothing was there: leave GlobalStorage as it was rather
    # than inventing an empty list of operations.
    if not present and not kept:
        return

    libcalamares.globalstorage.insert("packageOperations", kept)


def run():
    resolver = _config("resolver", "/usr/bin/kedra-experience")

    if not libcalamares.globalstorage.contains(GS_SELECTION):
        # No Experience page in this installer's sequence. Nothing to do, and
        # nothing is added -- which is precisely a Vanilla target.
        libcalamares.utils.debug("no Kedra Experience selection; nothing to resolve")
        return None

    selection_json = libcalamares.globalstorage.value(GS_SELECTION)
    if not isinstance(selection_json, str) or not selection_json.strip():
        return (
            _("Kedra Experience selection missing"),
            _("The installer recorded no Kedra Experience selection to resolve."),
        )

    # Everything is validated before anything is written. A failure here --
    # including on a re-run -- leaves GlobalStorage exactly as the previous run
    # left it, and returns an error, which ends the installation: a stale
    # operation can never reach `packages@online` behind a failed resolve.
    # There is deliberately no rollback, because there is nothing to roll back.
    try:
        returncode, document = _run_resolver(resolver, selection_json)
        _check_schema(document)
        _check_valid(returncode, document)
        required = _required_packages(document)
        applies = _applies_experience_layer(document)
        selection = _resolved_selection(document)
        preset = _preset_id(document)
    except ResolverError as exc:
        libcalamares.utils.warning(exc.message)
        return (exc.title, exc.message)

    libcalamares.utils.debug(
        f"profile {preset!r}: layer={applies}, {len(required)} required package(s)"
    )

    # Recorded for the later state job, so what gets persisted is the selection
    # that was actually resolved rather than a second reading of the UI.
    libcalamares.globalstorage.insert(GS_RESOLVED_SELECTION, json.dumps(selection))
    libcalamares.globalstorage.insert(GS_APPLIES_LAYER, applies)

    # Unconditional, on every successful resolve. An empty set leaves no
    # operation of this module's behind -- which is what makes a Kedra profile
    # re-resolved as Vanilla actually install a Vanilla system.
    _reconcile_package_operations(required)
    if not required:
        libcalamares.utils.debug("profile requires no packages; adding none")

    return None
