"""The Kedra Experience data model.

Two audiences, and the split between them is enforced rather than described.

`RUNTIME_MODULES` below is the subset that `kedra-experience-runtime` installs
to /usr/lib/kedra/experience/. It is exactly the transitive import closure of
`prodcli`, the shipped CLI -- no more. Everything else in this directory is
repository infrastructure: it validates and renders the manifests during
`makepkg check()` and in tests, and is not packaged.

Until 0.4.0 that closure was read-only, and the safety argument was that the
executor was not on the target at all. It is now, because `apply` has to run
somewhere and the user's own session is the only correct place. The boundary
therefore moved rather than disappeared, and what enforces it moved with it:

* `vendordefaults` and `cli` remain unshipped -- build-time rendering and
  developer tooling have no business on a target.
* `observed` remains unshipped: reading a *fixture* of desktop values is a
  development affordance, and the production path observes the live session
  through the adapter instead.
* The read-only guarantee that still holds, and is still checked, is narrower
  and more useful: `apply` writes only `policy = "managed"` resources. A vendor
  default is never written into anybody's dconf, enforced independently by the
  planner and the executor.

Stdlib only, by rule: TOML is read with `tomllib` (3.11+) and written by a
narrow serializer in `state`, so the build gains no dependency. The three
`import gi` sites -- `accelerators`, `gsettings` and
`shortcuts.launchability_problems` -- are all lazy, inside function bodies, so
importing the package still costs nothing; but two of them are now reachable
from `apply`, which is why the runtime package declares PyGObject, GLib and GTK.

This is not a stable third-party Python API. It is installed to a private path,
not to site-packages, it is not importable by `import kedra_experience` on a
Kedra system, and nothing outside Kedra should depend on it.
"""

from __future__ import annotations

# The modules `kedra-experience-runtime` ships, sorted.
#
# Read by kedra-experience-runtime/PKGBUILD (via `kedra-experience-dev
# runtime-modules`) so the package cannot install a different set from the one
# the tests verify, and by tests/test_runtime_package.py, which proves the set
# is import-closed and free of every module in UNSHIPPED_MODULES.
RUNTIME_MODULES = (
    "__init__",
    "accelerators",
    "activate",
    "applications",
    "backends",
    "conflicts",
    "execute",
    "gsettings",
    "locking",
    "manifests",
    "ownership",
    "packages",
    "plan",
    "prodcli",
    "resolve",
    "settings",
    "shortcuts",
    "state",
    "values",
)

# Modules that must never be packaged, whatever the CLI grows.
#
#   cli             the developer CLI: source-relative data directory, an
#                   in-memory backend standing in for a session, and a surface
#                   shaped for poking at the model by hand.
#   vendordefaults  renders .gschema.override files at build time. A target has
#                   no use for it; the rendered files arrive as packages.
#   observed        reads a fixture of desktop values from a TOML file. The
#                   production path observes the live session through the
#                   adapter, and shipping a way to feed it made-up values would
#                   be a way to make it write things nobody's desktop said.
UNSHIPPED_MODULES = (
    "cli",
    "observed",
    "vendordefaults",
)

__all__ = [
    "RUNTIME_MODULES",
    "UNSHIPPED_MODULES",
    "activate",
    "manifests",
    "prodcli",
    "resolve",
    "state",
]
