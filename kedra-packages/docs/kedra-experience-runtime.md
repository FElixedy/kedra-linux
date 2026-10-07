# Kedra Experience runtime — the shipped interface

`kedra-experience-runtime` puts three things on an installed Kedra system: the
Experience resolver, the command that exposes it, and the user-scoped systemd
unit that runs the command once, on first login.

    /usr/lib/kedra/experience/kedra_experience/    the library, private path
    /usr/bin/kedra-experience                      the command
    /usr/lib/systemd/user/kedra-experience-activate.service
                                                   first-login activation

It reads exactly one manifest tree — `/usr/share/kedra/experience`, owned by
`kedra-experience`.

Since 0.4.0 it also **writes**, in two narrowly bounded places: `install-state`
records one user's chosen profile into a target root, and `apply` enacts the
`policy = "managed"` part of that profile inside the user's own GNOME session.
Nothing else on the system is written, and a vendor default is never written at
all — see [the vendor-default boundary](#the-vendor-default-boundary), which is
the guarantee that replaced "this package writes nothing".

## Why it exists

Before it, the resolver lived only in this repository's `tools/` directory: no
package installed it, and `manifests.default_data_dir()` resolved relative to a
source checkout. An installed system could carry Kedra's GSettings defaults and
still have no way to answer *which profile is this, and what does it need?* —
the one question an installer has to ask before it installs anything.

That gap is why this package is the first Milestone 1 deliverable, and why it
began read-only: the profile question has to be answerable long before anything
is allowed to act on the answer. Acting on it came later, in 0.4.0, and arrived
as a deliberate addition to what is packaged — the executor, the ownership model
and the session adapter — rather than as a subcommand switched on over code that
had been quietly shipped in advance.

## What is shipped, and what is not

The package installs the transitive import closure of `prodcli` and nothing
else — currently nineteen modules, listed in `kedra_experience.RUNTIME_MODULES`.

Until 0.4.0 that closure was read-only, and the safety argument was that the
executor was not on the target at all. It is now, because `apply` has to run
somewhere and the user's own session is the only correct place. So the boundary
moved rather than disappeared, and the guarantee that replaced it is narrower
and more useful — see [the vendor-default boundary](#the-vendor-default-boundary).

Still absent from a target, by construction:

| Module | What it does | Why it is not shipped |
|---|---|---|
| `cli` | `kedra-experience-dev` | repository infrastructure: a source-relative data directory and an in-memory backend standing in for a session |
| `vendordefaults` | renders `.gschema.override` files | build-time only; a target receives the rendered files as packages |
| `observed` | reads a fixture of desktop values from a file | the production path observes the live session; shipping a way to feed it made-up values would be a way to make it write things nobody's desktop said |

`tests/test_runtime_package.py` recomputes the closure from the AST and fails if
the shipped set and the declared set ever disagree; `tests/test_installed_layout.py`
checks the same against the *built package*.

## The vendor-default boundary

**`apply` writes only `policy = "managed"` resources.** A `policy = "default"`
setting — the fixed five-workspace model, the Super+1..5 keymap — is delivered
as a package-owned GSettings vendor override by `kedra-experience-desktop` and
`kedra-experience-workflow`, and is never written into anybody's dconf.

This is the guarantee that replaced "nothing writes", and it is enforced three
times over rather than promised:

1. **The planner** decides `noop` for a default-policy setting and gives the
   reason in the plan entry.
2. **The executor** independently refuses a write entry whose policy is
   `default`, so a planner bug cannot get past it.
3. **Observation** does not even read those targets. `activate.observed_targets`
   filters to managed declarations plus existing records plus the shortcut list,
   which is what makes "Kedra reads only what it reasons about" literally true.

Why it matters: writing a vendor default into a user's dconf would create an
ownership claim over a value Kedra never wrote. A later revert would then
"restore" something Kedra never changed — and would do it to a value the user
may have chosen deliberately.

`tests/test_activate.py::DefaultPolicyIsolationTests` asserts all of it against
a real adapter: no default-policy target carries a stored value after `apply`,
no default-policy setting is owned, every ownership record is `policy = managed`,
and the backing store mentions no workspace or keymap key at all.

## The data path

`manifests.INSTALLED_DATA_DIR` is the literal `/usr/share/kedra/experience`, and
it is what `/usr/bin/kedra-experience` reads when `--data-dir` is not given.

There is no environment variable, no search path, and no fallback to a checkout.
A production caller omits `--data-dir`, and the answer then depends on the
installed packages and nothing else — not on the invoking user, the working
directory, or how the interpreter was started.

`--data-dir` exists for development and testing. `manifests.source_data_dir()`
still resolves the in-tree manifests for `kedra-experience-dev`,
`validate-experience.py` and the test suite, whose whole purpose is to check the
manifests *before* they are packaged.

## Commands

### `kedra-experience profiles`

Which profiles the installed tree defines, and what each one is made of.

```console
$ kedra-experience profiles --format json
```

```json
{
  "profiles_schema_version": 1,
  "experience_schema_version": 6,
  "resolution_schema_version": 2,
  "selection_schema_version": 1,
  "data_dir": "/usr/share/kedra/experience",
  "default_preset": "vanilla",
  "presets": [
    {
      "id": "kedra",
      "name": "Kedra Experience",
      "description": "Kedra's curated GNOME desktop experience. …",
      "version": "0.1.0",
      "kind": "base",
      "base_preset": null,
      "user_selectable": true,
      "applies_experience_layer": true,
      "modules": [
        {
          "id": "workflow",
          "name": "Kedra Workflow",
          "description": "Kedra's keyboard-driven workflow layer: …",
          "enabled": true,
          "tier": "recommended",
          "user_selectable": true,
          "version": "0.3.0",
          "source": "preset:kedra"
        }
      ]
    }
  ]
}
```

Module lists are **resolved**, not declared: a derived preset shows the result of
walking its base, so a consumer never reimplements inheritance to draw a
checklist. Vanilla therefore reports `"modules": []` — the truthful answer, since
it applies no layer — rather than every module switched off.

`user_selectable` appears on both presets and modules and must be honoured. On
modules it is what keeps a structural module — today only `integration` — out of
a UI. It is independent of `tier`: `desktop` is `tier = "core"` *and*
selectable, so a UI must read the flag rather than infer it from the tier.

### `kedra-experience resolve --selection -`

What one selection resolves to.

**Input** — a JSON object read from stdin (`-`) or a file. It is exactly the
`[selection]` table of a user state file, which is why no second format was
invented: what an installer sends here is what a later task writes to
`$XDG_STATE_HOME/kedra/experience-state.toml` verbatim.

```json
{
  "base_preset": "kedra",
  "module_overrides": [
    { "id": "workflow", "enabled": false }
  ]
}
```

`module_overrides` may be omitted. `selection_schema_version` may be stated
explicitly; a version this build does not understand is refused rather than
guessed at. Unknown keys, duplicate overrides and a non-boolean `enabled` are all
refused — each is a mistake that would otherwise be carried silently into a
user's state file.

**Only `user_selectable` modules may be overridden.** A selection naming a module
whose manifest says `user_selectable = false` — today only `integration` — is
not applied. The module keeps whatever its preset decided, the
refusal is reported in `diagnostics.refused_overrides`, and `resolve` exits `2`.

That rule lives in the resolver, not in the caller. `integration` carries
`kedra-experience` and `kedra-experience-runtime`, so an accepted override of it
would remove the Experience layer's own delivery from `packages.required` — and
the only thing preventing that used to be an installer honouring a flag. The
refusal is deliberately not a hard error: the document is still produced and
still correct, so even a consumer that ignores both the exit status and the
diagnostics gets a complete package set. Protection must not depend on the caller
noticing.

Preset machinery is unrestricted, and that is the distinction being drawn: a
preset is distributed Kedra data defining what a product *is*, so a derived
preset may still disable anything its base declares. A selection is untrusted
input from whatever built it.

**Output**:

```json
{
  "resolve_schema_version": 2,
  "experience_schema_version": 6,
  "resolution_schema_version": 2,
  "data_dir": "/usr/share/kedra/experience",
  "selection": {
    "selection_schema_version": 1,
    "base_preset": "kedra",
    "module_overrides": [{ "id": "workflow", "enabled": false }]
  },
  "preset": { "id": "kedra", "version": "0.1.0", "chain": ["kedra"] },
  "applies_experience_layer": true,
  "modules": [
    {
      "id": "workflow", "name": "Kedra Workflow", "description": "…",
      "enabled": false, "tier": "recommended", "user_selectable": true,
      "version": "0.3.0", "source": "user-state"
    }
  ],
  "enabled_modules": ["appearance", "desktop", "extensions", "integration", "terminal"],
  "packages": {
    "required": ["gnome-console", "gnome-shell", "kedra-experience",
                 "kedra-experience-desktop", "kedra-experience-runtime"],
    "recommended": ["gnome-control-center", "gnome-themes-extra"],
    "optional": ["gnome-shell-extension-manager", "kedra-branding"],
    "conditional": [],
    "unresolved": [],
    "requirements": [
      {
        "name": "kedra-experience", "relation": "required",
        "module_id": "integration", "resolved": true, "condition": null,
        "reason": "Owns /usr/share/kedra/experience, the manifest tree …"
      }
    ]
  },
  "applications": [
    {
      "role": "terminal",
      "desktop_id": "org.gnome.Console.desktop",
      "application_id": "org.gnome.Console",
      "package": "gnome-console",
      "module_id": "terminal",
      "reason": "GNOME Console is the Kedra terminal: …",
      "launch_command": "gapplication launch org.gnome.Console"
    }
  ],
  "diagnostics": { "ok": true, "violations": [], "advisories": [], "refused_overrides": [] }
}
```

A refused override appears as:

```json
{
  "code": "module-not-user-selectable",
  "module": "integration",
  "requested_enabled": false,
  "effective_enabled": true,
  "message": "module 'integration' is not user-selectable, so the request to have it disabled was refused; it remains enabled"
}
```

`refused_overrides` is a separate array from `violations` rather than an entry in
it, because the two describe different things and share no fields: a violation is
about the resolved profile, a refusal is about the input that produced it. Both
set `ok` to `false`. Branch on `code`, never on `message`.

`selection` is echoed back **normalised** — overrides are sorted — so a caller
that wants to persist exactly what it resolved persists this rather than what it
sent. It echoes what was *asked for*, including an override that was refused;
what happened to each one is in the diagnostics. Persist it only when
`diagnostics.ok` is true — a selection that resolved with refusals is one the
caller should fix, not store.

`packages.required` is the set an installer installs. `recommended` and
`optional` are decisions it may surface but must not act on silently.
`unresolved` carries any relation this layer cannot settle without a package
database (today only `package-installed`), with its condition attached; those
never leak into `required`. `requirements` lists every relation with the module
that asked and why — the only thing in the document that answers *why is this
being installed?*

`launch_command` is derived from the desktop ID by string manipulation. It reads
no desktop-file database and launches nothing.

Note what is **not** in the document: no setting values, no shortcut
declarations, no plan and no operations. Choosing a profile does not entitle a
caller to write one.

### `kedra-experience install-state`

Persist one user's chosen selection into a target root. The only command that
writes, and it writes exactly one file:

```console
# kedra-experience install-state --selection - \
      --root /tmp/target --user alice < selection.json
```

```json
{
  "install_state_schema_version": 1,
  "written": true,
  "path": "/tmp/target/home/alice/.local/state/kedra/experience-state.toml",
  "uid": 1000, "gid": 1000,
  "preset_id": "kedra",
  "selection": { "selection_schema_version": 1, "base_preset": "kedra", "module_overrides": [] },
  "applied_recorded": false
}
```

`--uid`, `--gid` and `--home` default to what the **target's** `/etc/passwd`
says. Reading them from there rather than assuming `/home/<user>` means a caller
cannot pass a pair that disagrees with the account that actually exists, and an
installer that places homes elsewhere still works. The account and its home must
already exist — this command refuses rather than creating a home of its own,
which is what makes "run it after the user step" an enforceable rule rather than
advice.

Three properties are worth stating because they are what make this safe to run
from an installer:

- **It resolves before it writes.** A selection the resolver would reject —
  a refused override, a requirement violation — is not persisted, and the command
  exits 2. The resolver's authority does not end at the UI.
- **Vanilla writes nothing.** A profile that applies no Experience layer gets no
  state file and no state directory. There is no runtime on that system to read
  one, and a Kedra state directory on a Vanilla install is a confusing artefact
  rather than a record. The result says `"written": false` and why.
- **No `[applied]` record.** An installer applies nothing, so it claims nothing.
  A fabricated `[applied]` block would later read as "Kedra already set these
  values", and the ownership layer would preserve user changes that never
  happened.

Nothing about GNOME is touched: no dconf, no GSettings, no session, no chroot
pretending to have one. The managed Super+Enter shortcut cannot be written by an
installer — it is a per-path relocatable-schema value needing the user's own
session bus — so installation records the choice and a later activation phase
enacts it.

### Exit status

| Status | Meaning | stdout |
|---|---|---|
| `0` | resolved, and the profile is valid | the document |
| `1` | nothing could be produced — bad input, unreadable tree, unknown preset | empty |
| `2` | resolved, but the profile has violations or refused overrides | the document |

`install-state` uses the same three: `0` written (or deliberately not written,
for a profile that applies no layer), `1` could not, `2` the selection is invalid
so nothing was written.

`2` rather than `1` because a profile that resolves and is wrong is a different
situation from one that cannot be resolved at all, and an installer needs to
tell them apart: the first can be shown to the user, the second cannot. Nothing
partial ever reaches stdout, so a consumer parsing JSON sees either a complete
document or an empty stream.

Advisories do not affect exit status. The one advisory the shipped tree declares
— `workflow` works best with `desktop` — is reachable from an ordinary Custom
Kedra: disabling `desktop` while keeping `workflow` is valid, reports
`works-best-with-disabled`, and exits `0`.

### `kedra-experience apply`

Enact the managed part of this user's profile, in this user's session.

```console
$ kedra-experience apply
outcome:    applied
generation: 1
wrote:      terminal-launch-shortcut-binding
wrote:      terminal-launch-shortcut-command
wrote:      terminal-launch-shortcut-name
wrote:      terminal-launch-shortcut-registration
```

Idempotent, and cheap enough to run at every login: a run that finds everything
already correct writes nothing and **consumes no generation**.

It resolves, observes the live desktop, builds a change plan, runs it, and
records the result — holding the Experience state lock across the whole
read → plan → execute → save cycle, because locking only the save would leave two
logins free to plan from the same stale state.

**No state file, no action.** A user who never chose a Kedra profile gets zero
writes and a successful exit — before the manifest tree is read, before an
adapter is constructed, before anything could reach a session. That is keyed on
the state file and never on installed packages: a system can carry this runtime
for any number of reasons, and none of them is consent to reconfigure somebody's
desktop.

| `outcome` | Meaning | Exit |
|---|---|---|
| `no-state` | no profile chosen; nothing done | 0 |
| `no-layer` | Vanilla; nothing to apply | 0 |
| `applied` | writes happened | 0 |
| `unchanged` | already correct | 0 |
| `blocked` | a managed resource is held by somebody else | **3** |
| `failed` | an authorized write failed | 1 |

`blocked` is its own status because it is **not a failed run**. Kedra asked
whether an accelerator was free, was told no, and correctly left it alone — the
desktop is in exactly the state the rules say it should be in. The systemd unit
declares `SuccessExitStatus=3` for that reason: a session whose only fault is
that its owner already uses Super+Enter must not carry a permanent red mark.

### `kedra-experience unmanage`

Give back everything Kedra still owns, and stop claiming it. The supported way
out. Each managed value returns to what Kedra recorded as its `original` — unset
if it was unset — **unless the user has since changed it**, in which case their
value stands and only the record is dropped. Kedra never removes something a
person has made their own, even on the way out.

There is deliberately no `--operation` switch, no `--force`, and no
restore-to-defaults verb. Restoring defaults reclaims a resource over a user's
own divergence, which is not something a login should be able to do.

## First-login activation

`/usr/lib/systemd/user/kedra-experience-activate.service`, a **user-scoped**
oneshot pulled in by `graphical-session.target`.

User-scoped because the work is only possible there. The one managed resource
Kedra has is a per-path instance of a relocatable GSettings schema plus
membership in a list, so writing it means writing this user's dconf through
dconf-service over this user's session bus — with GTK available to answer
whether the accelerator is free. A root service writing somebody's dconf, a
chroot-time apply, an XDG autostart entry and a shell-profile hook are all wrong,
and none is used.

```ini
ConditionPathExists=%S/kedra/experience-state.toml
ExecStart=/usr/bin/kedra-experience apply --format json
SuccessExitStatus=3
```

`%S` is `$XDG_STATE_HOME`. A user with no profile never runs the unit at all —
systemd records it as skipped. That is the Vanilla guarantee expressed at the
earliest possible point, and it is why enabling the unit for every user is safe.

It is enabled by a package-owned wants symlink rather than an install scriptlet:
a scriptlet cannot enable a *user* unit for users who do not exist yet, and
`systemctl --global enable` would leave a dangling symlink in `/etc` on removal.

The unit is close to unsandboxed, and that is a decision. It runs as the user,
doing what the user could do by hand in GNOME Settings, so a sandbox buys little
— while every filesystem restriction strong enough to be worth adding is strong
enough to break the session bus and dconf, *quietly*. A shortcut that silently
never appears is the worst failure this feature has.

## Dependencies, and why

| Dependency | Why |
|---|---|
| `python` | the package is Python; `tomllib` is stdlib from 3.11 |
| `kedra-experience` | owns the only manifest tree this package reads |
| `python-gobject` | `gsettings` uses `Gio.Settings` directly — types survive, and "is a value explicitly stored?" is answerable, which is the question the whole ownership model turns on |
| `glib2` | supplies the GObject-Introspection typelibs PyGObject loads for Gio and GLib. Inherited via python-gobject, but this package's correctness depends on it directly |
| `gtk4` | **not optional** — see below |

**Why `gtk4` is a hard dependency.** `accelerators` asks GTK whether two
accelerator strings mean the same combination. `gtk_accelerator_parse` is the
function GNOME itself parses them with, and there is deliberately no fallback
parser: a second grammar's disagreements would surface as *"no conflict"*, the
one answer that must never be produced by accident. Without GTK the conflict
checker reports itself unavailable and every shortcut is **blocked** rather than
registered — so Super+Enter would never appear, silently, on every login. A
dependency that decides whether a feature works at all is a hard dependency.

Nothing in the shipped set imports `gi` at module scope, so `profiles`,
`resolve` and `install-state` keep working on a machine with no desktop. A
top-level `import gi` in a shipped module fails the build.

## What this package is not

It is not a stable third-party Python API. The library is at a private path
rather than in site-packages, `import kedra_experience` from an unrelated program
still fails, and nothing outside Kedra should depend on it.

It is not a general profile manager, and `apply` is not a settings daemon. It
enacts the managed part of one recorded profile, once, in the session that owns
those settings, and it defers to the user: a value somebody changed themselves is
divergence to be left alone, not drift to be corrected. Everything outside that
— vendor defaults, other users' sessions, anything with no ownership record — is
not this package's to write, which is enforced in the planner and again in the
executor rather than promised here.

## `tier` and `user_selectable` are different questions

Worth stating because the two shipped `tier = "core"` modules answer them
differently, and conflating them was a real bug:

| Module | `tier` | `user_selectable` | Why |
|---|---|---|---|
| `desktop` | `core` | **true** | Kedra's curated experience is built around the fixed five-workspace model, *and* a Custom Kedra may switch it off, keep `workflow`, and be told what that costs. |
| `integration` | `core` | **false** | It carries `kedra-experience` and `kedra-experience-runtime` — the Experience layer's own delivery. Switching it off would remove the resolver from the system that is meant to be running it. |

`tier` says how central a module is to the product. `user_selectable` says
whether the decision is the user's. A UI must branch on the second and must not
infer it from the first.
