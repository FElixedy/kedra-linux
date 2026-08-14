# Kedra Linux

A native Linux maintenance and troubleshooting control center, built around one
idea:

> **System management and troubleshooting should share the same history.**

Kedra Linux (internally *Linux System Manager*) is a GTK4/libadwaita application
for inspecting, updating and repairing an Arch-based system. Its centrepiece is
**What Changed?** — a troubleshooting view that answers the question every Linux
user eventually asks:

> *"My system worked yesterday. What changed since then?"*

Every meaningful action the application performs — a kernel upgrade, a service
being disabled, a DKMS build failing — is recorded as structured evidence in a
single normalized timeline, so it can be correlated later instead of being
reconstructed from raw logs.

> **Status: early development.** The core domain layer, storage and execution
> abstractions are implemented and tested. The GTK front end is not yet built.
> See [`docs/CHECKPOINT.md`](docs/CHECKPOINT.md) for exactly what works today.

## Design principles

- **One history.** Packages, services, kernel, DKMS, configuration and boot all
  emit the same `ChangeEvent` type into one store. There is no per-feature
  history to reconcile.
- **No shell, ever.** External programs run through a `CommandRunner` with an
  argument vector. A command cannot be constructed from a string, so argument
  injection is not expressible in the API.
- **Never root as a whole.** The GUI runs unprivileged. Privileged work goes to
  a helper that accepts only a typed, validated action enum — there is
  deliberately no `run_command_as_root(String)`.
- **SQLite caches history, not state.** The running kernel, installed packages
  and unit states are always read from the system itself.
- **Metadata, not contents.** Configuration tracking stores hashes and
  permissions. File contents are read only when a user explicitly opens a diff,
  and paths that hold credentials are never hashed at all.
- **Relevance, not danger scores.** Changes are ranked by troubleshooting
  relevance (`Info` → `Failure`). There are no invented "system health" numbers.
- **Correlation, not causation.** The engine says *possible contributor* and
  *occurred shortly before*. It does not claim one change caused another.

## Architecture

```text
  lsm-gui      GTK4 / libadwaita. Renders models. No system parsing.
     │
  lsm-core     Domain models, backends, change tracking.
     │
  lsm-helper   Privileged actions only, behind a typed enum.
```

Distribution support is a backend concern, not a UI concern:

```text
PackageBackend
└── ArchPackageBackend        (implemented)
    FedoraPackageBackend      (future)
    AptPackageBackend         (future)
```

## Target platform

Arch Linux, CachyOS, EndeavourOS and other Arch-based distributions. The
architecture accommodates Fedora and Debian backends without changes to the UI
or domain models. Linux only.

## Building

Requires Rust 1.85+, GTK 4 and libadwaita 1.

```sh
# Arch-based systems
sudo pacman -S --needed rust gtk4 libadwaita pkgconf

cargo build
cargo test
```

The test suite is hermetic: it never installs or removes packages, never
modifies system configuration, and never touches systemd. System interaction is
abstracted behind `CommandRunner`, and tests inject a fixture-driven fake.

## Licence

GPL-3.0-or-later.
