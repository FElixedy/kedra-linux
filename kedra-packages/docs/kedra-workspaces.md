# The Kedra workspace model

Kedra Experience uses a **fixed set of five workspaces**. Workspaces 1, 2, 3, 4
and 5 always exist, from login, whether or not anything is on them.

This is a Kedra Desktop product decision taken on its own merits. It is not a
side effect of the keyboard layer, which is why it is declared by the `desktop`
module rather than by `workflow`.

Verified against the GNOME installed on the development system — GNOME Shell
**50.4**, mutter **50.4**, gsettings-desktop-schemas **50.1**, GLib **2.88.3** —
by reading the installed schema definitions. No schema or key name here was
assumed.

- [What each product gets](#what-each-product-gets)
- [The two settings](#the-two-settings)
- [Why both keys, and why neither alone](#why-both-keys-and-why-neither-alone)
- [Beating GNOME's own vendor default](#beating-gnomes-own-vendor-default)
- [Who owns what](#who-owns-what)
- [Disabling the Desktop module](#disabling-the-desktop-module)
- [Why no live settings were touched](#why-no-live-settings-were-touched)

## What each product gets

| Product | Workspace behaviour | `kedra-experience-desktop` |
|---|---|---|
| **Vanilla GNOME** | upstream GNOME: dynamic workspaces, count grows and shrinks | **not installed** |
| **Kedra Experience** | fixed, exactly five, numeric keyboard navigation | **required** |
| **Custom Kedra**, `desktop = false` | upstream GNOME behaviour returns | **not required** |

Vanilla's preset sets `applies_experience_layer = false`, so no module is read
at all and the empty package set follows by construction. Nothing is applied and
then undone.

## The two settings

Declared in
[`modules/desktop.toml`](../kedra-experience/data/experience/modules/desktop.toml),
both `policy = "default"`:

| Kedra setting ID | Schema | Key | Type | Kedra default | GNOME schema default |
|---|---|---|---|---|---|
| `desktop-dynamic-workspaces` | `org.gnome.mutter` | `dynamic-workspaces` | `b` | `false` | `false` (but see below) |
| `desktop-workspace-count` | `org.gnome.desktop.wm.preferences` | `num-workspaces` | `i` | `5` | `4`, range 1–36 |

Rendered into
`/usr/share/glib-2.0/schemas/95_kedra-experience-desktop.gschema.override`:

```ini
[org.gnome.desktop.wm.preferences]
num-workspaces=5

[org.gnome.desktop.wm.preferences:GNOME]
num-workspaces=5

[org.gnome.mutter]
dynamic-workspaces=false

[org.gnome.mutter:GNOME]
dynamic-workspaces=false
```

Each section appears twice on purpose. That is
[the whole subtlety of this package](#beating-gnomes-own-vendor-default).

As with every `policy = "default"` setting, nothing is written to anyone's
dconf: an explicit user value wins unconditionally, a Kedra update cannot
overwrite it, and `pacman -R kedra-experience-desktop` restores GNOME's
behaviour with no revert step. See
[Why a user override always beats a Kedra default](kedra-workflow.md#why-a-user-override-always-beats-a-kedra-default).

## Why both keys, and why neither alone

They are not two ways of saying the same thing, and shipping either on its own
produces something that does not work:

- **`num-workspaces` alone** does nothing. While workspaces are dynamic, mutter
  ignores the key entirely — GNOME's own schema description says so.
- **`dynamic-workspaces = false` alone** gives a fixed count of **four**, GNOME's
  schema default for `num-workspaces`. Fixed, but not the Kedra model, and
  `Super+5` would still be a no-op.

They also live in different schemas owned by different packages — `org.gnome.mutter`
by `mutter`, `org.gnome.desktop.wm.preferences` by `gsettings-desktop-schemas` —
which is why `kedra-experience-desktop` depends on both. Overriding a key in a
schema that is not installed is a hard error from `glib-compile-schemas`, and at
alpm-hook time that would break every *other* package's schemas on the system.

## Beating GNOME's own vendor default

**Filename order is not sufficient here, and assuming it is would ship a file
that silently does nothing.**

GNOME Shell ships its own vendor default, in
`/usr/share/glib-2.0/schemas/00_org.gnome.shell.gschema.override`:

```ini
[org.gnome.mutter:GNOME]
dynamic-workspaces=true
```

Note the `:GNOME` suffix. That is a *desktop-scoped* override group, matched
against any component of `XDG_CURRENT_DESKTOP`. GLib resolves a key's default in
two steps, and a desktop-scoped override beats a generic one **regardless of
which file each came from**. So a plain `[org.gnome.mutter]` group in a `95_`
file loses inside a GNOME session — while appearing to work everywhere a
developer is likely to check, because with `XDG_CURRENT_DESKTOP` unset it *does*
take effect.

Measured, not reasoned about:

| Kedra's section | `XDG_CURRENT_DESKTOP=GNOME` | unset |
|---|---|---|
| `[org.gnome.mutter]` only | `true` — **GNOME wins** | `false` |
| `[org.gnome.mutter:GNOME]` only | `false` — Kedra wins | `false` (schema default) |
| both, as shipped | `false` | `false` |

So the generator emits **every** section twice, generically and scoped to GNOME:

- the **scoped** copy wins the desktop-scoped comparison, and there filename
  order does decide — `95_kedra-experience-*` sorts after `00_org.gnome.shell`,
  and `glib-compile-schemas` lets the later file win;
- the **generic** copy means the value does not depend on `XDG_CURRENT_DESKTOP`
  being set at all.

This is applied uniformly to every module's override rather than only where a
clash exists today, so a future GNOME release that starts scoping some other key
cannot quietly take it back. The GNOME package itself is never modified.

Both branches are pinned by tests
(`test_vendor_defaults.py::DesktopScopePrecedenceTests`): the naive generic-only
file **must** lose in a GNOME session, and the shape Kedra ships **must** win in
all of them.

## Who owns what

A clean split, and the reason the workspace count is not declared in `workflow`:

| Module | Owns | Concretely |
|---|---|---|
| `desktop` | **workspace topology** | how many workspaces exist, and whether that is fixed |
| `workflow` | **workspace navigation** | `Super+1..5`, `Super+Shift+1..5` |

Under the full Kedra preset the two compose into the intended experience:

```text
Desktop fixed-five policy  +  Workflow numeric keymap
        = five workspaces that are always there, and always one keystroke away
```

The coupling is declared, not implied — as an **advisory** relationship, since
`workflow` functions perfectly well without this module:

```toml
# modules/workflow.toml
[[module.works_best_with]]
id     = "desktop"
reason = "without the Kedra fixed five-workspace model, GNOME manages workspaces dynamically and Super+1..5 do nothing for workspaces that do not currently exist"
```

The manifest validator also forbids two settings claiming one target, so the
count *cannot* be duplicated into `workflow` even by accident.

## Disabling the Desktop module

`desktop = false` is an explicitly supported Custom Kedra choice, and the
resulting profile is **valid**. Kedra says what it costs; it does not refuse it,
and it does not quietly undo it:

```console
$ tools/kedra-experience-dev resolve --state ./desktop-off.toml
preset:  kedra (0.1.0)
...
advisories:
  module 'workflow' works best with 'desktop', which is disabled; without the
  Kedra fixed five-workspace model, GNOME manages workspaces dynamically and
  Super+1..5 do nothing for workspaces that do not currently exist
$ echo $?
0
```

Exactly one advisory, and no errors. (Before the schema 4 dependency audit this
same choice reported *four* "unsatisfied requirements" — `appearance`,
`extensions` and `integration` all claimed to need `desktop`, and none of them
did. See
[Module-to-module relationships](kedra-experience.md#module-to-module-relationships).)

A soft relationship never enables a module, never adds a package and never
overrides a user's choice. It exists to be said.

**The consequence, stated plainly:** with the Desktop module off, GNOME manages
workspaces dynamically again, so `Super+3` does nothing while only two
workspaces exist. The bindings remain correct and safe — they are simply not
always reachable. That is the trade an advanced user is making, and Kedra's job
is to name it, not to prevent it.

The package set follows the same rule:

```console
$ tools/kedra-experience-dev packages --preset vanilla
required: (none)

$ tools/kedra-experience-dev packages --preset kedra
required: gnome-console, gnome-shell, kedra-experience,
          kedra-experience-desktop, kedra-experience-runtime,
          kedra-experience-workflow

$ tools/kedra-experience-dev packages --state ./desktop-off.toml
required: gnome-console, gnome-shell, kedra-experience,
          kedra-experience-runtime, kedra-experience-workflow
```

`kedra-experience-desktop` is the only entry that moves between those two, which
is the point. `kedra-experience` and `kedra-experience-runtime` come from the
`integration` module and stay for as long as the Experience layer applies;
`gnome-console` comes from `terminal`. None of them is affected by the workspace
model being on or off.

## Why no live settings were touched

Not one `gsettings set`, `gsettings reset`, `dconf write` or `dconf reset` was
run against a live desktop during development, packaging or testing, and the
design makes that structural rather than a matter of care:

- **The product writes nothing.** Both settings are `policy = "default"`, for
  which the planner returns `noop`. There is no code path from either
  declaration to a write, and no managed-value record is ever created — pinned
  by `test_desktop.py::NoManagedWriteTests`.
- **No Desktop code path reaches the real backend adapter.** A production
  `GSettingsAdapter` exists for the one `managed` setting Kedra has (the terminal
  shortcut); nothing in this module, and no CLI subcommand, constructs it.
- **Inspection was read-only.** GNOME's schemas were read as XML from
  `/usr/share/glib-2.0/schemas/`.
- **Compilation is sandboxed.** Tests and `check()` copy the system schemas —
  and GNOME's own override files, so precedence is exercised rather than assumed
  — into a temporary directory and compile *that*.
  `/usr/share/glib-2.0/schemas` is never an argument to `glib-compile-schemas`.
- **Read-back is isolated and out-of-process.** Values are read with
  `GSETTINGS_BACKEND=memory` in a subprocess, so no dconf database is opened and
  the varied `XDG_CURRENT_DESKTOP` never leaks into the test process.
- **Packaging writes only into `$pkgdir`.** The override is rendered to an
  explicit path passed by the PKGBUILD.
