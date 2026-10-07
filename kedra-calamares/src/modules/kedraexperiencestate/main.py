#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# === This file is part of Calamares - <https://calamares.io> ===
#
#   SPDX-FileCopyrightText: 2026 Kedra Linux
#   SPDX-License-Identifier: GPL-3.0-or-later
#
"""Persist the chosen Kedra Experience profile for the installed user.

One call to `kedra-experience install-state`, which writes one file:

    <root>/<home>/.local/state/kedra/experience-state.toml

WHAT THIS MODULE DOES NOT KNOW
------------------------------
The state file's format, its location, its ownership rules, and whether the
chosen profile warrants one at all. All of that lives in the Experience runtime,
where the model it belongs to lives. This module supplies a root, a user name
and a selection, and reports what happened.

WHERE IT MUST RUN IN THE SEQUENCE
---------------------------------
After `users`, because the account and its home must exist -- install-state
refuses otherwise rather than creating a home of its own.

After the plain `shellprocess`, because that step does

    runuser ${USER} -c "cp -rf /etc/skel/. /home/${USER}/."

and anything written into the home before it can be overwritten by a package's
skel payload.

Before `shellprocess@kedra_verify`, so a failure aborts the installation while
the installer is still the thing reporting errors.

NOTHING ABOUT GNOME HAPPENS HERE
--------------------------------
No dconf, no GSettings, no session, no chroot pretending to have one. The
managed Super+Enter shortcut is not written by the installer and cannot be: it
is a per-path relocatable-schema value that needs the user's own session bus.
Installation records the choice; a later activation phase enacts it.

WHAT "IT WORKED" HAS TO MEAN
----------------------------
`written` decides whether this module reports success, and it is checked against
what the installer believed it was doing. So it is required to be exactly a JSON
boolean: `bool(result.get("written"))` would read a missing field as False and
the string "false" as True, which turns a runtime that wrote nothing -- or one
that answered nonsense -- into a successful installation whose recorded profile
does not exist. Every field consumed here is type-checked the same way, and the
whole result is checked before this module reports anything.
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


# Written by kedraexperiencepackages after a successful resolve. Using that
# rather than the raw UI value means the selection persisted is the one that was
# actually validated.
GS_RESOLVED_SELECTION = "kedraExperienceResolvedSelection"
GS_APPLIES_LAYER = "kedraExperienceAppliesLayer"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID_PROFILE = 2

INSTALL_TIMEOUT_SECONDS = 60


def pretty_name():
    return _("Recording the Kedra Experience profile.")


def _config(key, default=None):
    return libcalamares.job.configuration.get(key, default)


# `isinstance(True, int)` is True in Python, so an integer check must exclude
# bool explicitly or `"install_state_schema_version": true` would pass as 1.
def _is_bool(value):
    return isinstance(value, bool)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_result(result):
    """Validate the install-state envelope. Returns a message, or None.

    Everything the installer acts on has to have exactly the documented type.
    A result that does not is not a partial success to be interpreted, it is an
    answer this module cannot read -- and reading it optimistically is how an
    installation reports a profile it never recorded.

    The caller has already established that this is an object of a version this
    installer understands; what is checked here is everything else it consumes.
    """
    if not _is_bool(result.get("written")):
        return _("The Experience runtime did not say whether it wrote the profile.")

    if result["written"] and not (
        isinstance(result.get("path"), str) and result["path"]
    ):
        return _("The Experience runtime reported writing a profile but named no file.")

    # Stated by the runtime so a caller can assert it rather than trust it: an
    # installer applies nothing, so nothing may come back recorded as applied.
    if "applied_recorded" in result:
        if not _is_bool(result["applied_recorded"]):
            return _("The Experience runtime did not say whether it recorded an applied state.")
        if result["applied_recorded"]:
            return _(
                "The Experience runtime recorded settings as already applied. The "
                "installer applies none, so this result cannot be trusted."
            )

    if "preset_id" in result and not isinstance(result["preset_id"], str):
        return _("The Experience runtime named the profile in an unexpected way.")

    if "selection" in result and not isinstance(result["selection"], dict):
        return _("The Experience runtime returned a selection of an unexpected shape.")

    return None


def run():
    resolver = _config("resolver", "/usr/bin/kedra-experience")

    if not libcalamares.globalstorage.contains(GS_RESOLVED_SELECTION):
        libcalamares.utils.debug("no resolved Kedra Experience selection; nothing to record")
        return None

    selection_json = libcalamares.globalstorage.value(GS_RESOLVED_SELECTION)
    if not isinstance(selection_json, str) or not selection_json.strip():
        return (
            _("Kedra Experience selection missing"),
            _("The installer recorded no Kedra Experience selection to persist."),
        )

    root = libcalamares.globalstorage.value("rootMountPoint")
    user = libcalamares.globalstorage.value("username")

    if not root:
        return (
            _("Kedra Experience profile not recorded"),
            _("The installer did not report a target root."),
        )
    if not user:
        # An installation with no user account. Nothing to record it against,
        # and a system-wide default is not what a per-user selection is.
        libcalamares.utils.warning(
            "no target username; the Experience selection was not persisted"
        )
        return None

    if not os.path.exists(resolver):
        return (
            _("Kedra Experience resolver missing"),
            _(
                "The Kedra Experience resolver was not found at <pre>{!s}</pre>, so "
                "the chosen profile could not be recorded."
            ).format(resolver),
        )

    command = [
        resolver,
        "install-state",
        "--selection",
        "-",
        "--root",
        root,
        "--user",
        user,
    ]

    try:
        completed = subprocess.run(
            command,
            input=selection_json,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        return (
            _("Kedra Experience profile not recorded"),
            _("<pre>{!s}</pre> could not be run: {!s}").format(resolver, exc),
        )
    except subprocess.TimeoutExpired:
        return (
            _("Kedra Experience profile not recorded"),
            _("<pre>{!s}</pre> did not finish in time.").format(resolver),
        )

    if completed.stderr:
        libcalamares.utils.debug(f"install-state stderr: {completed.stderr.strip()}")

    if completed.returncode != EXIT_OK:
        # Includes exit 2, which would mean the selection stopped being valid
        # between resolving and persisting -- impossible if nothing changed, and
        # therefore worth failing loudly rather than shrugging at.
        return (
            _("Kedra Experience profile not recorded"),
            _(
                "Recording the chosen profile for <b>{!s}</b> failed and the "
                "installation cannot be reported as successful."
                "<br/><br/><pre>{!s}</pre>"
            ).format(user, completed.stderr.strip() or "no output"),
        )

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return (
            _("Kedra Experience profile not recorded"),
            _("The result of recording the profile was not valid JSON: {!s}").format(exc),
        )

    if not isinstance(result, dict):
        return (
            _("Kedra Experience profile not recorded"),
            _("The result of recording the profile was not an object."),
        )

    supported = _config("supportedInstallStateSchemas", [1])
    version = result.get("install_state_schema_version")
    if not _is_int(version) or version not in supported:
        return (
            _("Kedra Experience version mismatch"),
            _(
                "Recording the profile answered with result version {!s}, which "
                "this installer does not understand."
            ).format(version),
        )

    problem = _check_result(result)
    if problem:
        return (_("Kedra Experience profile not recorded"), problem)

    # Written by kedraexperiencepackages, which validated it as an exact boolean
    # before inserting it. Anything else means this job ran without that one, or
    # after something overwrote it, and there is nothing to compare against.
    applies = libcalamares.globalstorage.value(GS_APPLIES_LAYER)
    if not _is_bool(applies):
        return (
            _("Kedra Experience profile not recorded"),
            _(
                "The installer did not record whether the chosen profile applies "
                "an Experience layer, so what the runtime did cannot be checked."
            ),
        )
    written = result["written"]

    # The two must agree. A profile that applies the layer and wrote nothing --
    # or the reverse -- means the resolver and this module disagree about what
    # was chosen, which is exactly the kind of quiet mismatch that produces a
    # system nobody can explain later.
    if applies != written:
        return (
            _("Kedra Experience profile not recorded"),
            _(
                "The installer and the Experience runtime disagree about whether "
                "a profile should have been recorded (layer applies: {!s}, file "
                "written: {!s})."
            ).format(applies, written),
        )

    if written:
        libcalamares.utils.debug(f"recorded Experience selection at {result.get('path')}")
    else:
        libcalamares.utils.debug(
            "profile applies no Experience layer; no state file written"
        )

    return None
