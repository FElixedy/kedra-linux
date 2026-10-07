# Kedra Terminal

The `terminal` module owns two things, and deliberately nothing else:

1. **which terminal application the Kedra experience is**, and
2. **the shortcut that opens it**.

```toml
[[module.applications]]
role = "terminal"
desktop_id = "org.gnome.Console.desktop"
package = "gnome-console"

[[module.shortcuts]]
id = "terminal-launch-shortcut"
policy = "managed"
path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/"
name = "Kedra Terminal"
binding = "<Super>Return"
command = { application_role = "terminal" }
```

GNOME Console is the default and recommended Kedra terminal. It is GNOME's own
terminal for GNOME 50, it is already part of the desktop Kedra builds on, and it
is the application `Super+Enter` launches.

The second declaration is what the rest of this document is mostly about. It is
Kedra's **first and only `policy = "managed"` setting** — the first value Kedra
actively writes into a user's dconf, and therefore the first that can ever need
reverting. Everything about how it behaves under a user who edits it, a module
that gets switched off, and a write that fails half-way is spelled out below,
because a configurator that gets those wrong is a configurator that eats
people's settings.

Terminal ships **no `org.gnome.Console` configuration at all** — no GSettings
vendor override, and therefore still no defaults package. Every candidate key was
reviewed and upstream was kept. The review is below, because "we shipped nothing"
is only a decision if the reasoning survives with it.

## What Terminal does not own

Stated as flatly as possible, because each of these was a live option that was
turned down:

- **The login shell.** Kedra Terminal never runs `chsh`, never changes a user's
  shell, and never edits a fish, bash or zsh configuration. A terminal emulator
  and a shell are separate products.
- **The prompt.** No Starship, no prompt framework, no rc content. A prompt is
  not a terminal setting.
- **User dotfiles.** Nothing in this module writes into a user's home directory.
  There is no dotfile to install, back up, merge or revert.
- **Shell aliases.** CachyOS's fish configuration is the user's; `cachyos-fish-config`
  is neither copied, forked nor replaced.
- **The terminal font.** GNOME Console follows the system monospace font
  (`use-system-font = true`), which is the behaviour Kedra wants: a terminal
  that picks its own font fights the desktop's font settings. No Nerd Font is
  pulled in, and no personal FiraCode configuration is packaged.
- **Colours, palette and theme.** These belong to **Appearance**. Terminal must
  not pre-empt a palette decision that another module owns.
- **`x-scheme-handler/terminal`** and default-application registration. Not
  claimed, not modified.

**Black Box** — and foot, Ptyxis, Alacritty, kitty, or anything else — remains a
perfectly good choice for a user. None of them is a Kedra dependency, and
installing one does not make a Kedra system inconsistent. A user who prefers
another terminal changes nothing about the rest of the experience; the contract
above is simply what Kedra itself means by "the terminal".

## The org.gnome.Console review

Every key in the installed `org.gnome.Console` schema (gnome-console 50.0),
with the upstream default and the v1 decision. The rule applied throughout: ship
an override only where upstream is *wrong for a general user*, never merely to
demonstrate that the module has been configured.

| Key | Type | Upstream default | Decision |
|---|---|---|---|
| `scrollback-lines` | `x` | `10000` | **Keep upstream.** A useful capacity already, and the same figure GNOME Terminal has defaulted to for years. Re-declaring it would add a Kedra file whose removal changes nothing. |
| `ignore-scrollback-limit` | `b` | `false` | **Keep upstream.** An expert escape hatch; unbounded scrollback is not a sane distribution default. |
| `audible-bell` | `b` | `true` | **Keep upstream.** The bell is an alert channel with an accessibility dimension, already gated by GNOME's sound settings and toggleable in Console's own menu. Silencing it distribution-wide is a taste change, not a fix. |
| `visual-bell` | `b` | `true` | **Keep upstream.** The accessible counterpart of the above. |
| `use-system-font` | `b` | `true` | **Keep upstream.** Follow the desktop's monospace font — exactly what Kedra wants, so there is nothing to say. |
| `custom-font` | `s` | `""` | **Reject.** Personal presentation, and meaningless while `use-system-font` is true. |
| `font-scale` | `d` | `1.0` | **Reject.** Personal presentation. |
| `theme` | enum | `'night'` | **Reject — Appearance's.** A palette/theme decision Terminal must not pre-empt. |
| `livery` | `s` | a UUID | **Reject.** A profile identifier; never a distribution default. |
| `custom-liveries` | `a{sv}` | `{}` | **Reject.** User-created palettes. Also a dictionary type the value vocabulary deliberately does not support. |
| `transparency` | `b` | `false` | **Reject — Appearance's.** |
| `shell` | `as` | `[]` | **Reject.** The login shell is explicitly not Kedra Terminal's, and an empty default means "whatever the user's shell is", which is correct. |
| `last-window-size` | `(ii)` | `(-1, -1)` | **Reject.** Session state. |
| `last-window-maximised` | `b` | `false` | **Reject.** Session state. |
| `restore-window-size` | `b` | `true` | **Reject.** Window geometry behaviour, and upstream's choice is the friendly one. |
| `software-flow-control` | `b` | `false` | **Reject.** An expert toggle; enabling it distribution-wide would make Ctrl+S appear to hang a terminal. |

Three consequences follow, all intended:

1. **Still no `kedra-experience-terminal` package.** A defaults package with no
   defaults is a package whose removal changes nothing, and Super+Enter did not
   change that: a `managed` setting is written by tooling from the manifests in
   `kedra-experience`, not delivered as a file, so it creates nothing for a
   package to own. The Terminal module requires `gnome-console` directly and
   otherwise contains only declarative metadata. If a Console vendor default is
   ever justified, the package can be added then — it is not created for symmetry
   with `kedra-experience-desktop` and `kedra-experience-workflow`.
2. **No int64 (`x`) support was added** to the typed-value vocabulary.
   `scrollback-lines` is the only `x` key in sight, and Kedra does not ship it.
   The type will be added when a declaration needs it, with range and
   serialization tests, and not before.
3. **`terminal` declares `settings = []` explicitly**, so the empty list reads
   as a reviewed outcome rather than an oversight. A regression test asserts it
   stays empty for v1.

## The application contract

`module.applications` is a schema 5 addition. A module may nominate the
application that fills a named role:

```toml
[[module.applications]]
role = "terminal"                          # closed vocabulary
desktop_id = "org.gnome.Console.desktop"   # a desktop-file ID, never a command
package = "gnome-console"                  # must be a `required` relation here
reason = "..."                             # optional, non-empty if present
```

Rules the validator enforces:

- **`role` comes from a closed vocabulary.** Today that vocabulary is exactly
  `terminal`. A role is added when a consumer for it exists.
- **`desktop_id` must be a desktop-file ID.** `kgx`, `/usr/bin/kgx`,
  `flatpak run org.gnome.Console` and `org.gnome.Console` (no suffix) are all
  rejected. A desktop ID is what GAppInfo and GNOME Settings speak; it survives a
  native/Flatpak switch or a rename of the binary underneath it; and as data it
  cannot carry arguments or a shell fragment. Both the bare application ID
  (`ApplicationContract.application_id`) and the launch command
  (`ApplicationContract.launch_command`) are *derived* from it, never declared
  separately, so the terminal is named exactly once in the whole tree.
- **`package` must already be a `required` package relation of the same
  module.** "The Kedra terminal is X" and "X is merely recommended" cannot both
  be true, so the two statements are cross-checked rather than left to agree by
  hand.
- **A role has one owner tree-wide**, for the same reason a GSettings target
  does: two nominations would leave no single answer to "which terminal does
  this profile use", and the first consumer would have to invent a tie-break.

It is deliberately **not** a `module.settings` entry. Nothing writes it into
dconf, nothing reads it back out, and it has no user value to revert. Forcing a
contract into the settings table merely because that table exists would give it
ownership and revert semantics it must not have.

It is also deliberately **not** an application registry. There are no
priorities, no fallback chains and no lookup service, because one role with one
owner is what the product has.

### Declared versus effective

```console
$ tools/kedra-experience-dev applications --preset kedra
preset:    kedra
declared:  (every contract in the tree, activation ignored)
  terminal   org.gnome.Console.desktop  [terminal] -> gnome-console
effective: (contracts this profile's enabled modules contribute)
  terminal   org.gnome.Console.desktop  [terminal] -> gnome-console

$ tools/kedra-experience-dev applications --preset vanilla
preset:    vanilla
declared:  (every contract in the tree, activation ignored)
  terminal   org.gnome.Console.desktop  [terminal] -> gnome-console
effective: (contracts this profile's enabled modules contribute)
  (none)
```

The split is the invariant, not a display nicety: **a declaration is not a
requirement.** `application_contracts()` folds only *enabled* modules, so
Vanilla — which applies no module at all — has no Kedra terminal and no
`gnome-console` requirement, even though the declaration sits in package data it
ships. `application_for_role()` answers `None` in that case, and callers must
handle it rather than fall back to a hardcoded guess.

### Package resolution

| Profile | Terminal module | `gnome-console` required by Kedra Experience | Effective contract |
|---|---|---|---|
| Vanilla | not applied | no | none |
| Kedra | enabled | **yes** | `org.gnome.Console.desktop` |
| Custom, `terminal = false` | disabled | no | none |
| Custom, `terminal = true` | enabled | **yes** | `org.gnome.Console.desktop` |

`gnome-console` is `required`, not `recommended`: a module that names GNOME
Console as *the* Kedra terminal cannot also treat its absence as a mild
degradation. No other module relates to the package, so disabling Terminal
genuinely drops the requirement rather than having it arrive from elsewhere.

None of this consults a package database. "Required by this profile" is computed
from manifests alone and is a different question from "installed on this
machine" — a distinction the tests enforce structurally, by detonating every
process-spawning entry point while package resolution runs.

### Removed placeholders

Two relations that predated v1 were removed rather than kept for compatibility:

- **`gnome-terminal`, `conditional`** — "configured only if the user already has
  it", in a module that has never declared a setting. It configured nothing. A
  placeholder with no real purpose is worse than no relation: it is metadata a
  reader has to disprove.
- **`works_best_with = appearance`** — the advisory rule is that separating two
  modules must produce a *meaningful degraded experience*. Terminal declares
  which terminal application Kedra uses, and that is exactly as true and as
  complete with the appearance layer off; Appearance ships no terminal-facing
  value for its absence to degrade. The advisory pointed at nothing a user could
  observe, and an advisory with no concrete consequence is noise in a selection
  UI. If Appearance later ships a terminal palette Terminal is designed around,
  the relationship earns its place then — with the evidence in hand.

## Super+Enter

Implemented, and the only thing in Kedra that writes into a user's dconf.

### Why it has to be managed rather than a vendor default

Every other value Kedra ships is `policy = "default"`: it is rendered into a
GSettings vendor override, installed by a package, and never written to anybody's
dconf. That is by far the better mechanism — an explicit user value always wins,
`pacman -R` restores upstream, and there is no revert step because there was no
write. Super+Enter cannot use it, for two independent reasons.

**GNOME 50 has no terminal-launch keybinding key.**
`org.gnome.desktop.default-applications.terminal` still exists and is marked
*"DEPRECATED: This key is deprecated and ignored"*. There is no
`org.gnome.settings-daemon.plugins.media-keys` key for "open a terminal" either.
The only way to bind Super+Enter to a terminal is a GNOME **custom keybinding**.

**A custom keybinding cannot be expressed as a vendor default.** A `.gschema.override`
addresses a schema by ID and has no way to name a path. A section for a
relocatable schema is accepted by `glib-compile-schemas`, but the defaults it sets
apply to *every instance of that schema at every path* — so defaulting `binding`
would change the fallback binding of every custom shortcut the user has, including
ones they created themselves, for any that has not had `binding` explicitly
written. That is not a limitation to work around; it is a reason the mechanism is
the wrong one. Verified empirically against GLib 2.88 in
`tests/test_vendor_defaults.py::RelocatableSchemaLimitTests`.

So Super+Enter is written, and being written it is owned, and being owned it is
subject to the whole ownership contract in
[docs/kedra-experience.md](kedra-experience.md#ownership-and-the-managed-value-record).

It belongs to **Terminal**, not Workflow. Workflow owns the window and workspace
keyboard model; Super+Enter is a key combination, but what makes it a Terminal
shortcut is that it launches the terminal this module names. Selecting a different
terminal changes what it does, and that decision lives here.

### The GNOME resource

A custom shortcut is four values that only mean anything together. Read from the
installed schemas (gnome-settings-daemon 50.1), not assumed:

| Schema | Kind | Key | Type | Default |
|---|---|---|---|---|
| `org.gnome.settings-daemon.plugins.media-keys` | fixed, at `/org/gnome/settings-daemon/plugins/media-keys/` | `custom-keybindings` | `as` | `[]` |
| `org.gnome.settings-daemon.plugins.media-keys.custom-keybinding` | **relocatable** | `name` | `s` | `''` |
| … | … | `binding` | `s` | `''` |
| … | … | `command` | `s` | `''` |
| … | … | `enable-in-lockscreen` | `b` | `false` |

The child schema declares no `path` attribute, which is what makes it
relocatable: it is a shape, instantiated once per shortcut at a path its creator
chooses. A shortcut exists when its child values are set **and** its path appears
in the parent `custom-keybindings` list; either half alone does nothing.

`enable-in-lockscreen` is deliberately left alone. Its upstream default is
`false`, which is the right answer for a shortcut that opens a terminal, and
writing a value equal to the default would take ownership of a key for no reason.

### The canonical Kedra path

```
/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/
```

This is **persistent identity**. Every ownership record a user will ever have for
this shortcut refers to it, so changing it later is a migration, not an edit — the
old path would keep its values and its list membership while a second shortcut
appeared at the new one. Three properties it has to have:

- **Deterministic.** Fixed text. No username, hostname, machine ID, UUID, counter
  or creation timestamp, so it is byte-identical on every Kedra installation and
  the same after a reinstall as before it.
- **Kedra-namespaced.** The final segment is `kedra-terminal`, which says who owns
  it to anyone reading dconf or looking at GNOME Settings.
- **Collision-proof against GNOME.** GNOME Settings generates its own shortcut
  paths from the format string `%s/custom%d/` — `custom0/`, `custom1/`, … —
  verified in the installed `gnome-control-center` binary. A segment beginning
  `kedra-` is not of that shape and can never be produced by it. Kedra cannot
  occupy a path GNOME was about to hand a user, and a user's own shortcut cannot
  land on Kedra's.

The trailing slash is required rather than stylistic: a GSettings path must begin
and end with `/`, and GNOME compares the entry in `custom-keybindings` as a plain
string, so a path written without it would be a different entry that nothing
honours.

### One declaration, four settings

`[[module.shortcuts]]` is a schema 6 addition, and it is a *resource*, not four
settings written next to each other. Kedra expands it:

| Setting ID | Target | Value |
|---|---|---|
| `terminal-launch-shortcut-name` | child schema @ Kedra path, `name` | `s` `"Kedra Terminal"` |
| `terminal-launch-shortcut-command` | child schema @ Kedra path, `command` | `s` `"gapplication launch org.gnome.Console"` |
| `terminal-launch-shortcut-binding` | child schema @ Kedra path, `binding` | `s` `"<Super>Return"` |
| `terminal-launch-shortcut-registration` | parent schema, `custom-keybindings` | membership of the Kedra path |

```console
$ tools/kedra-experience-dev shortcuts --preset kedra
```

Modelling this as four *unrelated* settings would be wrong in two specific ways.
Nothing would order the writes, so a run could register a path whose `command`
write had failed and leave a live key combination that does nothing. And an
unmanage would treat `custom-keybindings` as an ordinary scalar Kedra owns —
which is the next section.

### List membership, and why unrelated shortcuts survive

Kedra does **not** own `custom-keybindings`. It owns *the presence of one entry in
it*. The registration setting carries a `member`, and the planner projects the
observed list down to the only question Kedra is entitled to ask before any
decision is made:

```
member present  ->  ['<kedra path>']
member absent   ->  None
```

`original` therefore records "was Kedra's entry in the list before Kedra?", never
what the list used to be. Worked through:

```
before Kedra      ['…/custom0/', '…/custom1/']
Kedra applies     ['…/custom0/', '…/custom1/', '…/kedra-terminal/']
user adds one     ['…/custom0/', '…/custom1/', '…/kedra-terminal/', '…/custom2/']
Terminal disabled ['…/custom0/', '…/custom1/', '…/custom2/']
```

The last line is the point. A configurator that snapshotted the list on first
apply and restored that snapshot would produce `['…/custom0/', '…/custom1/']` and
silently delete `custom2` — a shortcut the user made, that Kedra never saw, and
that had nothing to do with it. Kedra never writes back a remembered list: it
reads what is there now, removes one string, and writes the rest. Entries created
before Kedra, after Kedra, or while Kedra was installed are all equally untouched,
because none of them is ever named.

Two further consequences, both deliberate:

- **Adding is idempotent.** An entry already present is left where it is, so a
  repeat apply appends nothing and reorders nothing.
- **Removing the last entry unsets the key** rather than writing an explicit empty
  list. An explicit `[]` and an unset key read identically through GSettings, so
  unsetting is the tidier of two equivalent results and leaves no Kedra
  fingerprint behind.

The shape that would break this is refused at the state-validation layer, not just
avoided by the current code: a `member` record whose `last_applied` holds anything
other than exactly `['<member>']` is a validation error, so the destructive record
cannot exist on disk at all.

### The launch command

GNOME types `command` as a string, so a custom shortcut necessarily ends in a
command line. This is the **single allowed exception** to "a Kedra manifest never
contains a command", and it is confined to one function
(`shortcuts.launch_command_for_application_id`). The manifest names an application
*role*; the command is derived:

```
[[module.applications]] role = "terminal", desktop_id = "org.gnome.Console.desktop"
                                    |
                        application_id = "org.gnome.Console"
                                    |
                    command = "gapplication launch org.gnome.Console"
```

`command = { application_role = "terminal" }` is a **typed reference**, not string
interpolation. There is no expression language and there will not be one: the only
thing that line can say is "whichever application fills the terminal role", which
tooling can resolve, validate and display without evaluating anything.

Why `gapplication launch` and not the alternatives:

| Candidate | Verdict |
|---|---|
| `kgx`, `/usr/bin/kgx` | **Rejected.** A binary name is a packaging detail that changes underneath the contract, and hardcoding one is exactly what makes a terminal selector impossible. |
| `gio launch <file>` | **Rejected.** The installed `gio` resolves its argument with `g_desktop_app_info_new_from_filename()` only — confirmed in the binary's symbol table — so it needs a *filesystem path* to the desktop file. That would hardcode `/usr/share/applications/…`, which breaks for a user-local override in `~/.local/share/applications` and for a Flatpak-exported desktop file. |
| `flatpak run …` | **Rejected.** Assumes a packaging format the contract does not name. |
| `gapplication launch <APPID>` | **Chosen.** Takes an application ID, resolves it through the ordinary XDG desktop-file search path — so a user's own override wins and a Flatpak export works — and activates it over D-Bus with a proper activation token, which is what `StartupNotify` needs. |

`gapplication` ships in glib2, which every GNOME system has by construction, so
this adds no dependency the desktop did not already have. The one requirement it
introduces is that the application be **`DBusActivatable`**. GNOME Console is:
its desktop file sets `DBusActivatable=true`, it ships
`/usr/share/dbus-1/services/org.gnome.Console.service`, and `gapplication list-apps`
lists `org.gnome.Console`. `shortcuts.launchability_problems()` is the seam a
future terminal selector must run before nominating a replacement — see
[Future terminal selectors](#future-terminal-selectors).

Nothing is run through a shell. gnome-settings-daemon executes a custom shortcut's
command with `g_app_info_create_from_commandline()` — confirmed in the
`gsd-media-keys` symbol table — so it is parsed by the Desktop Entry
specification's rules, not by `/bin/sh`. There is no metacharacter to escape and
no expansion to worry about; words are quoted per those rules anyway, which for
every ID that passes the manifest validator is a no-op.

### Accelerator conflicts

GNOME lets any number of custom shortcuts be registered, each at its own dconf
path, and nothing stops two of them naming the same key combination. When that
happens the winner is decided by whichever grab gnome-settings-daemon registers
first — a coin toss that can come out differently after a reboot. The reference
machine that this feature was built on already had `<Super>Return` at
`custom1/`, launching Black Box, before Kedra had ever run.

**The policy, in one line: Kedra never takes an accelerator that an unrelated
shortcut already holds.**

Not "takes it and warns". Not "takes it if the other one looks unimportant".
The user's shortcut is preserved, unmodified, unremoved and unrebound, and
Kedra's own registration is blocked instead:

| | |
|---|---|
| Their shortcut | untouched — not deleted, not rebound, not reordered in the list |
| Kedra's shortcut | not registered; no child values written; no ownership record |
| The user | told which shortcut is in the way, by name and path |

The reasoning is the one this whole architecture keeps returning to. Kedra owns
what Kedra created. `<Super>Return` is Kedra's *recommendation*, not Kedra's
property, and a person who bound it to their own terminal before installing
Kedra has already answered the question Kedra was about to ask. Freeing that
accelerator is a decision for them to make in a UI, not something an unattended
`pacman -Syu` performs on their behalf.

This applies to **explicit restore too**. "Restore Kedra defaults" may reclaim
Kedra's own resource — that is what it is for — but it may not delete or rebind
somebody else's shortcut to make room. With the conflict still present, restore
is blocked exactly as a normal apply is.

#### Accelerator normalization

Two accelerators clash when they name the same key with the same modifiers, not
when their *text* matches. `<Super><Shift>1` and `<Shift><Super>1` are one
combination written two ways, and GNOME Settings, mutter and a hand-edited dconf
value do not agree on which spelling to produce. Raw string comparison would
miss a real conflict.

`gtk_accelerator_parse()` is the authority, because it is the function GNOME
itself parses these strings with: it owns the modifier vocabulary, the
`<Primary>` alias, the key names and the case rules. `accelerators.parse()`
reduces a spelling to GTK's `(keyval, modifiers)` pair and uses that as identity;
`gtk_accelerator_name()` supplies the canonical text for display.

There is deliberately **no fallback parser**. A hand-written one would be a
second, subtly different grammar, and its disagreements with GTK would surface
as "no conflict" — the one answer that must never be produced by accident. So
when GTK cannot be imported:

```python
accelerators.available()   # False
accelerators.parse(...)    # raises AcceleratorUnavailable
```

and the conflict report says **unavailable**, which blocks. Manifest loading and
validation are unaffected — they are stdlib-only by rule and never call into
this module — so a build machine with no desktop still works. Only the semantic
question becomes unanswerable, and an unanswerable question is not a yes.

Three states, never two:

```
checked + nothing in the way   ->  register
checked + something holds it   ->  blocked, with the conflicting shortcut named
not checked / cannot check     ->  blocked, with the reason
```

`ConflictReport` has no constructor meaning "assume it is free"; its zero-argument
default blocks; and the planner treats a *missing* report as unchecked rather
than as clear. **"Could not check" is never "no conflict."**

#### Discovery

Read-only, and narrow. From `custom-keybindings`, and for each registered path
three keys of the relocatable child schema — `name`, `binding`, `command`. There
is no `dconf dump` and no scan of anything else the user has.

Failures are contained per path, because one broken entry must not stop the
planner:

| Found | Treated as |
|---|---|
| binding `''` | **unbound** — not in the way |
| a path registered but never configured (*stale*) | unbound, reported, **left exactly where it is** |
| a binding GTK rejects | **advisory** — GNOME cannot grab it either, so it does not conflict, but the user probably meant it to |
| a path that cannot be read at all | **undetermined** — blocks, because its binding might be the one in question |
| the same path listed twice | reported, **not deduplicated** |

Stale and duplicate entries are reported and never repaired. A blank shortcut is
what a person part-way through creating one looks like, and "while I was here I
tidied your list" is precisely the behaviour the ownership model exists to
prevent. Kedra owns membership of one path and nothing else about that list.

#### Precedence: divergence first, conflicts second

The order is the design, and getting it backwards would break the ownership
model:

1. **Identify the canonical Kedra resource** — by `resource`, on the declaration
   and on the record, so it is still recognised after the module that declared
   it is gone.
2. **Decide ownership.** `decide()` runs first and untouched: divergence,
   reverts, first apply — all settled before an accelerator is looked at.
3. **Gate the writes.** Only then is the conflict report consulted, and it can
   only ever turn a write into a refusal. It cannot create a write, cannot
   re-open a value the user diverged, and cannot cancel a revert.

So a user who rebound Kedra's own shortcut has **diverged**; they have not
created an "external conflict" with themselves, and step 2 has already decided
to leave their value alone. Conversely an unrelated shortcut holding the
accelerator is **not** divergence, and must not be mistaken for it — the response
to divergence is to stop claiming a value Kedra wrote, and Kedra never wrote
that one.

The canonical Kedra path is excluded from external conflict **by identity**, not
by heuristic: a shortcut at that path *is* Kedra's, whether written a minute ago
or a release ago. Finding Kedra's accelerator there is reported as `self` and is
never blocking.

Only `apply`, `update` and `force-apply` are gated. `revert-to-original` and
`drop-record` are how Kedra *stops* holding an accelerator, so blocking them
would trap the shortcut — a conflict appearing after installation would make the
Terminal module impossible to switch off cleanly. It is not.

While a resource is blocked, **no** value of it is written — not the name, not
the command. The resource is in a state that needs a human decision, and
half-updating it meanwhile would leave a shortcut whose fields came from two
different Kedra versions.

#### An external conflict appearing after Kedra owns the accelerator

Kedra registers `<Super>Return`; later the user creates their own shortcut with
the same combination; a Kedra update runs. What happens:

- the conflict is **detected** and surfaced, naming their shortcut;
- their shortcut is **not** modified, rebound or removed;
- Kedra's resource is **not** deregistered and **not** rewritten;
- nothing is written at all, and `last_applied` is left intact.

Kedra does **not** deregister itself. Doing so would silently hand the
accelerator to the newcomer — an ownership change nobody asked for — and it
would oscillate if the user later removed theirs again. Preserving both and
saying so is the least destructive answer.

That does leave a genuinely ambiguous system: **two registered shortcuts share
one accelerator, and which one fires depends on grab order.** Kedra did not
create that ambiguity and cannot resolve it without editing a shortcut it does
not own. It reports it; resolving it is a user action.

#### Future configurator responsibility

The model produces structured data and stops there. A `ConflictReport` carries
the conflicting path, name, binding, command, type and reason — enough for a UI
to say

> Super+Enter is already used by **blackbox**

and offer the user a choice. Nothing beyond shortcut metadata is collected. The
product wording, and the decision itself, belong to that UI; the choices it will
need to offer are to rebind Kedra's shortcut, rebind theirs, or leave it. None of
them exists yet, and until one does the answer is to change nothing.

#### Future terminal selectors

Any application nominated for `role = "terminal"` must be verified as launchable
by the contract's launch mechanism. `gapplication launch` activates over D-Bus,
so a desktop file without `DBusActivatable=true` yields a shortcut that is
registered, looks correct in GNOME Settings, and silently does nothing — a
failure invisible until somebody presses the key.

`shortcuts.launchability_problems(desktop_id)` is that seam. It reads the
installed desktop-file database through GAppInfo and launches nothing. It is
deliberately *not* part of manifest validation: a build machine with no desktop
cannot answer it, and a manifest must validate identically everywhere.

The wrong response to a negative answer is to widen the contract into binary
paths or a free-form command field. The right one is a different terminal, or a
different launch mechanism — declared once, in the one place the terminal is
named.

#### Inspecting it

```console
$ tools/kedra-experience-dev shortcut-conflicts ./state.toml ./observed.toml
preset:     kedra
registered: 1 custom shortcut path(s)
  /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/
    name='blackbox' binding='<Super>Return' [bound]
conflicts:
  terminal-launch-shortcut  <Super>Return  -> BLOCKED
    <Super>Return is already used by the custom shortcut 'blackbox' at …/custom1/;
    Kedra will not take an accelerator another shortcut holds, and will not alter
    that shortcut to free it
backend writes during discovery: 0
```

Offline: the backend is an in-memory fake seeded from the fixture, so there is no
path from this command to a live session. `plan` and `execute` carry the same
report in their JSON, under `conflicts`.

### Execution ordering

A shortcut becomes live the instant its path enters `custom-keybindings`. Order
the writes wrongly and there is a window in which a registered key combination has
no command. So registration is always the last thing added and the first thing
removed:

```
create   name, command, binding   →   register path        (phase 1, then 2)
remove   deregister path          →   reset name/command/binding  (phase 0, then 1)
```

Ordinary settings sit in the middle phase, so a plan containing no shortcut is
still ordered by setting ID exactly as before.

### User divergence

Every value of the resource follows the same rule, `name` included. A rule with an
exception is a rule nobody can predict, and `name` being "only a label" is not a
reason to overwrite something a person typed.

| Case | What happened | What Kedra does |
|---|---|---|
| 0 | An unrelated shortcut already holds `<Super>Return` | **Nothing is written.** All four settings become `blocked-by-conflict`; no ownership record is created and their shortcut is untouched. See [Accelerator conflicts](#accelerator-conflicts). |
| 1 | Fresh user, no Kedra shortcut | Write the three child values, then register the path. Records created; `original` captures the pre-Kedra state of each. |
| 2 | Kedra's recommendation changed, user has not touched it | Update. Safe because the observed value is exactly what Kedra last wrote. |
| 3 | User rebound it to `Super+T` | **Preserved.** `preserve-user-change`; nothing is written; the record keeps pointing at `<Super>Return`, which is the evidence that keeps the divergence detectable forever. |
| 4 | User pointed `command` elsewhere | **Preserved**, identically. |
| 5 | Terminal disabled, shortcut untouched | Deregister the path (removing only Kedra's entry), reset the three child values, drop all four records. |
| 6 | Terminal disabled *after* the user edited it | **Nothing is written.** See below. |
| 7 | Explicit "restore Kedra defaults" | May reclaim binding, command, name and registration — the one path allowed to overwrite a user's change, and a distinct decision (`force-apply`) that requires confirmation, so it can never be reached by an upgrade. It still may **not** free the accelerator by altering somebody else's shortcut. |

**Case 6 is the one worth stating carefully.** Handling the four settings
independently would drop the record for `binding` (the user's change preserved —
correct) while reverting `name` and `command` and deregistering the path (their
shortcut destroyed — not correct). The user would be left with a shortcut they had
modified and that no longer exists.

So there is a **resource-cohesion rule**:

> Once the user has materially modified any *value* of a Kedra-created resource, a
> normal unmanage stops claiming the whole resource and deletes none of it.

Kedra drops all four records and writes nothing. The user keeps a working,
registered shortcut, bound the way they bound it. It is no longer Kedra's, which is
the correct outcome: they made it theirs.

A diverged *membership* does not trigger the rule. That means the user deleted the
shortcut themselves, in which case cleaning up the child values Kedra wrote at a
path nothing points at is tidying, not destruction.

### Failure and retry

There is no transaction across dconf and none is pretended. What is guaranteed is
that **state never claims more than happened**:

- *Child write fails, registration succeeds.* No record claims the failed value
  was applied. The next run re-attempts exactly that one write.
- *Child writes succeed, registration fails.* The three value records exist; there
  is no registration record and the list is untouched. The next run plans only the
  registration.
- *Deregistration succeeds, child cleanup fails.* The user's list is already
  correct; the one value that could not be reset is still claimed, honestly. The
  next run finishes the cleanup.

Retrying is safe and idempotent in every case, because the planner re-derives its
decisions from committed state plus a fresh observation rather than from a
remembered plan.

### Vanilla

A Vanilla profile applies no Experience module. `application_contracts()` and
`declarations_for()` both fold *enabled* modules only, so Vanilla has no terminal
contract, no launch command, no shortcut declaration and nothing to write —
`terminal_launch_command()` returns `None`, and callers must handle that rather
than fall back to a guess. The declaration still sits in the package data Vanilla
ships, and that is the distinction the whole architecture turns on: **a
declaration is not a requirement.** A test executes a full Vanilla plan against a
fake backend and asserts the adapter recorded *zero operations* — not "no writes",
none at all, reads included.

| Profile | Terminal module | `gnome-console` required | Super+Enter |
|---|---|---|---|
| Vanilla | not applied | no | untouched — GNOME/upstream/user state as it was |
| Kedra | enabled | **yes** | managed, `<Super>Return` → GNOME Console |
| Custom, `terminal = false` | disabled | no | safe unmanage (cases 5 and 6) |
| Custom, `terminal = true` | enabled | **yes** | managed |

No Workflow package is required for Super+Enter, and no Workflow module needs to
be enabled: the shortcut is declared by `terminal`, targets schemas
gnome-settings-daemon installs, and is planned and written with Workflow switched
off. A test asserts exactly that.

## Ownership going forward

| Owner | Scope |
|---|---|
| **Terminal** | The terminal application contract; the Super+Enter shortcut that launches it, including refusing to take the accelerator when somebody else has it; Console-specific defaults, if any are ever justified. |
| **Workflow** | The window and workspace keyboard model — Super+Q, Super+1..5, Super+Shift+1..5. Not terminal launching. |
| **Appearance** | Terminal colours, palette and aesthetic integration with the rest of the desktop. |

The boundary that matters: Workflow owns *keyboard model*, Terminal owns
*terminal*. Super+Enter is a key combination, but what makes it a Terminal
shortcut is that it launches the terminal this module names.
