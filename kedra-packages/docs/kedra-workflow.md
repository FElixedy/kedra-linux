# Kedra Workflow v1

The first Kedra module that produces user-visible GNOME behaviour: a
keyboard-driven workflow layer built from **GSettings vendor defaults** and
nothing else.

Everything here was verified against the GNOME actually installed on the
development system — GNOME Shell **50.4**, mutter **50.4**,
gsettings-desktop-schemas **50.1**, GLib **2.88.3** — by reading the installed
schema definitions. No schema or key name in this document was assumed.

- [The keymap](#the-keymap)
- [Why vendor defaults](#why-vendor-defaults-and-not-managed-settings)
- [Why user overrides always win](#why-a-user-override-always-beats-a-kedra-default)
- [Why Vanilla does not have this package](#why-vanilla-does-not-have-this-package)
- [Displaced GNOME defaults](#displaced-gnome-defaults)
- [Super+Enter — implemented, and not here](#superenter--implemented-and-not-here)
- [The workspace model this keymap is built for](#the-workspace-model-this-keymap-is-built-for)
- [Why no live settings were touched](#why-no-live-settings-were-touched)

## The keymap

| Shortcut | Action | Schema | Key |
|---|---|---|---|
| `Super+Q` | Close the active window | `org.gnome.desktop.wm.keybindings` | `close` |
| `Super+1`…`Super+5` | Switch to workspace 1…5 | `org.gnome.desktop.wm.keybindings` | `switch-to-workspace-1`…`-5` |
| `Super+Shift+1`…`Super+Shift+5` | Move the active window to workspace 1…5 | `org.gnome.desktop.wm.keybindings` | `move-to-workspace-1`…`-5` |
| `Super+Enter` | Open the terminal | — | **not Workflow's** — shipped by the `terminal` module; see [below](#superenter--implemented-and-not-here) |

Three upstream bindings that would otherwise be lost are **kept alongside** the
new ones, because nothing forced their removal:

| Retained | Action |
|---|---|
| `Alt+F4` | Close the active window |
| `Super+Home` | Switch to workspace 1 |
| `Super+Shift+Home` | Move the active window to workspace 1 |

The Kedra shortcut is listed first in each key's value, so it is the one GNOME
Settings displays as *the* shortcut.

Declarations live in
[`kedra-experience/data/experience/modules/workflow.toml`](../kedra-experience/data/experience/modules/workflow.toml)
as sixteen `policy = "default"` settings — eleven bindings and five
neutralisations.

## Why vendor defaults, and not managed settings

A GNOME keybinding maps cleanly onto a GSettings key with an `as` type in a
schema installed at a fixed path. That is exactly what a **vendor default** — a
`.gschema.override` file in `/usr/share/glib-2.0/schemas/` — is designed to
express, so Kedra uses the native mechanism instead of writing values:

| | Vendor default (`policy = "default"`) | Managed write (`policy = "managed"`) |
|---|---|---|
| User's dconf | untouched | a key is written |
| "Did the user change this?" | trivially answerable — any dconf value at all is theirs | needs a stored record of what Kedra last wrote |
| Uninstall | defaults vanish; nothing to revert | must be reverted, and the revert can fail |
| Ownership record | none | one per setting, per user, forever |

Managed writes exist for values a vendor default genuinely cannot express. Not
one of the eleven Workflow bindings is such a value, so none of them is managed
— including the five neutralisations, which are ordinary `as` keys in a fixed
schema like any other.

The override file is **generated at package time** from the module manifest
(`tools/kedra_experience/vendordefaults.py`), so there is one source of truth
and no second copy to drift. One file per module, because the file is the
delivery unit — a module's defaults have to be removable on their own.

Its content today, with each section repeated as `[schema:GNOME]` (elided here;
see [why](kedra-workspaces.md#beating-gnomes-own-vendor-default)):

```ini
[org.gnome.desktop.wm.keybindings]
close=['<Super>q', '<Alt>F4']
move-to-workspace-1=['<Shift><Super>1', '<Shift><Super>Home']
move-to-workspace-2=['<Shift><Super>2']
move-to-workspace-3=['<Shift><Super>3']
move-to-workspace-4=['<Shift><Super>4']
move-to-workspace-5=['<Shift><Super>5']
switch-to-workspace-1=['<Super>1', '<Super>Home']
switch-to-workspace-2=['<Super>2']
switch-to-workspace-3=['<Super>3']
switch-to-workspace-4=['<Super>4']
switch-to-workspace-5=['<Super>5']

[org.gnome.shell.keybindings]
switch-to-application-1=@as []
switch-to-application-2=@as []
switch-to-application-3=@as []
switch-to-application-4=@as []
switch-to-application-5=@as []
```

The file is installed as
`/usr/share/glib-2.0/schemas/95_kedra-experience-workflow.gschema.override`. The
numeric prefix is load order, not decoration: `glib-compile-schemas` sorts
override files by name and lets later ones win, and GNOME ships its own vendor
defaults as `00_org.gnome.shell.gschema.override`.

Filename order is necessary but **not sufficient**, which is why every section
is also emitted desktop-scoped. Nothing GNOME scopes competes with these
keybindings today, so the scoped copy changes no value here — it exists so that
a future GNOME release which starts scoping one cannot quietly take it back. The
mechanism, and the case where it does bite, are in
[Beating GNOME's own vendor default](kedra-workspaces.md#beating-gnomes-own-vendor-default).

No compiled `gschemas.compiled` is packaged. Arch's `glib2` ships an alpm hook
triggering on `usr/share/glib-2.0/schemas/*.override` for install, upgrade
**and remove**, so compilation is the system's job in all three directions.

## Why a user override always beats a Kedra default

GSettings resolves a key in this order:

```text
user's dconf value   →   vendor default (.gschema.override)   →   schema default
```

Kedra only ever supplies the middle layer. A user who changes a shortcut — in
GNOME Settings, with `gsettings set`, or through any other tool — writes the
top layer, which wins unconditionally and is never consulted, compared or
overwritten by a Kedra update. Kedra literally does not know it is there.

This is the ownership rule from
[`kedra-experience.md`](kedra-experience.md#ownership-what-kedra-state-does-not-claim)
obtained for free rather than enforced: with nothing written, there is nothing
to conflict over.

The corollary is worth stating plainly. A user who *has* overridden a shortcut
will not receive a later Kedra change to that shortcut. That is the intended
trade, and it is the same trade GNOME itself makes.

## Why Vanilla does not have this package

Vanilla GNOME must be upstream GNOME. Not "Kedra with the Workflow module
switched off", and above all not "Kedra applied and then reverted" — a revert
leaves residue in dconf, and a failed revert leaves a desktop nobody chose.

So the Workflow defaults are a **separate, removable package**,
`kedra-experience-workflow`, and the whole question reduces to whether that
package is installed:

| Profile | Workflow module | `kedra-experience-workflow` | Effective bindings |
|---|---|---|---|
| Vanilla | not consulted at all | **not required** | upstream GNOME |
| Kedra | enabled | **required** | Kedra Workflow v1 |
| Custom Kedra, `workflow = false` | disabled | **not required** | upstream GNOME |
| Custom Kedra, other module disabled | enabled | **required** | Kedra Workflow v1 |

Vanilla's preset sets `applies_experience_layer = false`, so no module is read
and the resolved module set is empty by construction — the empty package set
falls out of that, rather than being a special case someone remembered to write.

`tools/kedra-experience-dev packages` computes this from the manifests alone. It
consults no package database and installs nothing:

```console
$ tools/kedra-experience-dev packages --preset vanilla
preset:   vanilla
required: (none)

$ tools/kedra-experience-dev packages --preset kedra
preset:   kedra
required: gnome-console, gnome-shell, kedra-experience,
          kedra-experience-desktop, kedra-experience-runtime,
          kedra-experience-workflow

$ tools/kedra-experience-dev packages --state ./workflow-off.toml
preset:   kedra
required: gnome-console, gnome-shell, kedra-experience,
          kedra-experience-desktop, kedra-experience-runtime
```

`kedra-experience-workflow` is the only entry that differs. The rest are the
Experience layer's own delivery (`integration`) and the terminal application
(`terminal`), neither of which the keymap being on or off can change.

`kedra-experience-desktop` is the same idea applied to the
[workspace model](kedra-workspaces.md) — a second, independently removable
defaults package, so each module's behaviour can be switched off without
disturbing the other.

Package *transactions* are deliberately not implemented yet. This layer only has
to be able to say what the correct package set is.

## Displaced GNOME defaults

One clash, and it is unavoidable.

| Combination | Upstream owner | Upstream action |
|---|---|---|
| `Super+1`…`Super+5` | `org.gnome.shell.keybindings switch-to-application-1`…`-5` | Activate the 1st–5th pinned app in the dash |

Two GNOME actions bound to one combination is not a configuration; it is a race
between whichever grab registers first. So Kedra explicitly clears those five
keys, **through the same vendor-default layer that caused the clash**. Removing
`kedra-experience-workflow` restores both halves together.

Two consequences worth being explicit about:

- **`Super+6`…`Super+9` still activate pinned applications 6–9.** Workflow v1
  claims five workspaces, so it releases five keys and no more. The resulting
  asymmetry — `Super+1..5` for workspaces, `Super+6..9` for applications — is a
  known rough edge, and the alternative (clearing four bindings the keymap has
  no use for) is worse.
- **Activating a pinned app by keyboard is not gone**, it moved:
  `Super+Ctrl+1`…`5` (`open-new-window-application-N`) is untouched, and the
  overview search reaches any application.

Nothing else clashes. `Super+Q` and `Super+Shift+1..5` carry no
default binding in any schema installed on the reference system; `Alt+F4`,
`Super+Home`, `Super+Shift+Home`, `Super+End` and `Super+Shift+End` keep their
upstream meanings.

The clash check is a test, not a one-time audit:
`tests/test_workflow.py::InstalledSchemaTests` rebuilds the effective binding map
from the installed schema XML on every run and fails on any combination claimed
by both Kedra and a GNOME default that is not on the declared displacement list.

## Super+Enter — implemented, and not here

**Super+Enter is not a Workflow shortcut and never will be. It belongs to the
`terminal` module**, which owns the terminal application contract it launches.
It is implemented; the full account is in
[docs/kedra-terminal.md](kedra-terminal.md#superenter). This section records the
two findings that decided its shape, because both were discovered while building
Workflow's vendor-default layer and both are about *this* layer's limits.

### 1. GNOME 50 has no native terminal-launch key

There is no `terminal` key in `org.gnome.settings-daemon.plugins.media-keys` —
it was removed years ago. The one remaining candidate,
`org.gnome.desktop.default-applications.terminal`, still exists but its own
schema says:

> DEPRECATED: This key is deprecated and ignored. The default terminal is
> handled in GIO.

It is also not a keybinding; it names a program. So a vendor default cannot
express Super+Enter, and a **custom keybinding** is the only route.

### 2. A custom keybinding cannot be a vendor default

GNOME represents a custom shortcut as an instance of the *relocatable* schema
`org.gnome.settings-daemon.plugins.media-keys.custom-keybinding`, at a
per-shortcut dconf path such as
`…/media-keys/custom-keybindings/custom0/`, with three keys (`name`, `binding`,
`command`) and that path listed in `custom-keybindings`.

An override file addresses a schema **by ID and cannot name a path**. A section
for a relocatable schema *is* accepted by `glib-compile-schemas` — which is the
trap — but the defaults it sets then apply to **every instance of that schema at
every path**. Defaulting `binding` there would change the fallback binding of
every custom shortcut on the system, including ones the user created, for any
that has not had `binding` explicitly written.

This was verified against GLib 2.88 rather than assumed; the probe is kept as a
test (`tests/test_vendor_defaults.py::RelocatableSchemaLimitTests`) so the
finding is re-checked on every run.

**Therefore** Super+Enter is `policy = "managed"` — the only such setting in
Kedra — and it is the reason the real GSettings adapter exists.

### Why it is Terminal's and not Workflow's

Workflow owns the window and workspace keyboard model. Super+Enter is a key
combination, but what makes it a Terminal shortcut is that it launches the
application the Terminal contract names:

```toml
# modules/terminal.toml
[[module.applications]]
role = "terminal"
desktop_id = "org.gnome.Console.desktop"
package = "gnome-console"

[[module.shortcuts]]
id = "terminal-launch-shortcut"
binding = "<Super>Return"
command = { application_role = "terminal" }
```

A shortcut that opened a terminal the profile does not install would be exactly
the layering mistake this split avoids: a Custom Kedra with `terminal = false`
requires no terminal and gets no such shortcut. The dependency runs the right
way round too — Super+Enter needs no Workflow package and works with the Workflow
module switched off entirely, which a test asserts.

The tests encode the split so it cannot drift: no Workflow setting may target a
custom-keybinding schema, no Workflow declaration may bind `Return`, and no
terminal binary may appear in any declared value anywhere in the tree.
## The workspace model this keymap is built for

`Super+1..5` and `Super+Shift+1..5` are designed around the **Kedra Desktop
fixed five-workspace model**: workspaces 1 to 5 always exist, from login. See
[The Kedra workspace model](kedra-workspaces.md).

The two halves are owned by different modules on purpose:

| Module | Owns | Delivered by |
|---|---|---|
| `desktop` | **workspace topology** — how many workspaces exist, and whether that is fixed | `kedra-experience-desktop` |
| `workflow` | **workspace navigation** — the numeric keymap on this page | `kedra-experience-workflow` |

Under the full Kedra preset both are enabled, and together they are the intended
experience:

```text
Desktop fixed-five policy  +  Workflow numeric keymap
        = five workspaces that are always there, and always one keystroke away
```

The workspace count is deliberately **not** duplicated into this module. One
product decision, one owner — and the manifest validator forbids two settings
claiming the same target, so the duplication is not expressible even by
accident. The coupling is declared instead, as an **advisory** relationship:

```toml
[[module.works_best_with]]
id     = "desktop"
reason = "without the Kedra fixed five-workspace model, GNOME manages workspaces dynamically and Super+1..5 do nothing for workspaces that do not currently exist"
```

Advisory, not `requires`, and that is a deliberate correction: none of these
keybindings reads, writes or depends on anything the `desktop` module declares.
They are ordinary GSettings defaults in schemas mutter and GNOME Shell install,
and every one of them is registered and functional with Kedra Desktop switched
off. Calling that a hard requirement would make a supported customisation look
like a broken profile. See
[Module-to-module relationships](kedra-experience.md#module-to-module-relationships).

### If the Desktop module is switched off

The profile stays **valid**. The resolver will not switch `desktop` back on
behind a user who disabled it, and the advisory neither enables the module nor
adds its package — it is said, never acted on:

```console
$ tools/kedra-experience-dev resolve --state ./desktop-off.toml
advisories:
  module 'workflow' works best with 'desktop', which is disabled; without the
  Kedra fixed five-workspace model, GNOME manages workspaces dynamically and
  Super+1..5 do nothing for workspaces that do not currently exist
$ echo $?
0
```

In that configuration GNOME manages workspaces dynamically again — the count is
however many are occupied, plus one — and the consequence for this keymap is:

> `Super+N` and `Super+Shift+N` do nothing while workspace *N* does not exist.
> On a freshly logged-in session with one occupied workspace, `Super+3` is a
> no-op. The shortcuts stay correct and safe; they are just not always
> *reachable*.

That is the trade an advanced user is making deliberately. Under the full Kedra
preset it does not arise.

## Why no live settings were touched

Not one `gsettings set`, `gsettings reset`, `dconf write` or `dconf reset` was
run against a live desktop during development, packaging or testing — and the
design makes that structural rather than a matter of care:

- **The product writes nothing.** Every Workflow setting is `policy = "default"`,
  and the planner returns `noop` for those (`ownership._decide_apply`). There is
  no code path from a Workflow declaration to a write.
- **The real backend adapter is never handed a live session by anything here.**
  A production `GSettingsAdapter` now exists — Super+Enter needed one — but no
  Workflow code path constructs it, no CLI subcommand constructs it, and its
  integration tests bind it to a keyfile store in a temporary directory. A test
  asserts the planner and the CLI do not so much as import the module.
- **Inspection was read-only.** GNOME's schemas were read as XML from
  `/usr/share/glib-2.0/schemas/` and via `gsettings list-recursively`, which
  reads.
- **Compilation is sandboxed.** Tests and `check()` copy the system schemas into
  a temporary directory, write the override there, and run
  `glib-compile-schemas` against *that* directory.
  `/usr/share/glib-2.0/schemas` is never an argument.
- **Read-back uses the memory backend.** Where a test confirms a compiled
  default, it sets `GSETTINGS_BACKEND=memory`, so no dconf database is opened at
  all.
- **Packaging writes only into `$pkgdir`.** The override is rendered to an
  explicit path passed by the PKGBUILD; `vendor-defaults` has no default output
  location precisely so it cannot guess one.
