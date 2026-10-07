# Kedra Experience — profile and module architecture

This document describes the declarative data model behind Kedra's desktop
product, the resolver that turns it into an effective configuration, and the
per-user state that records what was applied.

It also covers the ownership contract that decides whether a value may be
written, and the executor that carries those decisions out.

**Almost nothing here writes GNOME configuration, and that is by design.** Most
real GNOME behaviour ships the other way round: the `workflow` and `desktop`
modules declare `policy = "default"` settings that are rendered into **GSettings
vendor overrides**, each delivered by its own separate, removable package. A
vendor default changes what a key reads as when the user has set nothing; it puts
no value in anyone's dconf and needs no revert. See [Kedra
Workflow v1](kedra-workflow.md), [the workspace model](kedra-workspaces.md) and
[Setting policies](#setting-policies).

Exactly one value is `policy = "managed"` — actively written into a user's dconf,
and therefore owned and revertable: the **Super+Enter** terminal shortcut, which
GNOME can only represent as a custom keybinding and which no vendor-default
mechanism can express. It is written through the real
[`GSettingsAdapter`](#the-gsettings-adapter), under the [ownership
contract](#ownership-and-the-managed-value-record), and the whole of its
behaviour is documented in [docs/kedra-terminal.md](kedra-terminal.md#superenter).

Extensions land in a later task, in the places marked below.

## The three products

| Product | Selection | Shape |
|---|---|---|
| Vanilla GNOME | preset `vanilla` | `kind = "base"`, `applies_experience_layer = false`, no modules |
| Kedra Experience | preset `kedra` | `kind = "base"`, `applies_experience_layer = true`, declares the module set |
| Custom Kedra Experience | user state, or a derived preset | `base_preset = "kedra"` plus overrides only — never a copy |

## Two kinds of data

The single most important distinction in this design. Mixing these up is how
desktop customisation systems end up shipping someone's home directory.

| | Immutable profile definitions | Mutable user state |
|---|---|---|
| Location | `/usr/share/kedra/experience/` | `$XDG_STATE_HOME/kedra/` |
| Owner | the `kedra-experience` package | the user |
| Contents | presets, modules, dependency metadata | selected preset, overrides, applied-state record |
| Identical across machines? | yes, byte for byte | no, never |
| Distributable? | yes, that is the point | **no, never** |
| Schema key | `schema_version` | `state_schema_version` |
| Written by | `makepkg` | Kedra tooling |

Neither ever contains GNOME preference values today, and the user-state file
never will: see [Ownership](#ownership-what-kedra-state-does-not-claim).

## Layout

Everything below is package-owned, read-only system data installed by
`kedra-experience`:

```text
/usr/share/kedra/experience/
├── experience.toml          root descriptor: schema, layout, discovery, default preset
├── presets/
│   ├── vanilla.toml
│   └── kedra.toml
└── modules/
    ├── desktop.toml
    ├── workflow.toml
    ├── appearance.toml
    ├── terminal.toml
    ├── extensions.toml
    ├── integration.toml
    └── extras.toml
```

Discovery is a directory scan: every `*.toml` under `presets/` and `modules/`
is a manifest, and a manifest's filename must equal its `id`. There is no index
file, so adding a module cannot desynchronise from a list someone forgot to
update.

**Nothing mutable lives here.** A user's chosen preset, their overrides and any
applied-state bookkeeping are *not* in this tree. Where that state lives is a
later task's decision; it will not be under `/usr/share`.

## Data format

TOML, for four reasons:

- `tomllib` is in the Python standard library (3.11+), so the validator and any
  future packaging tooling parse it with **no new runtime dependency**.
- The Kedra codebase is Rust, where `serde` + `toml` is first-class.
- It supports comments, which matters a great deal for a file whose main job
  right now is to explain reserved-but-empty structure.
- One key per line diffs cleanly, so a module gaining a package relationship is
  a one-line review.

JSON was rejected for having no comments; YAML for requiring `python-yaml` and
for its significant-whitespace and implicit-typing footguns in a file format
that distro tooling must parse unattended.

## Preset semantics

A preset answers one question: **which modules are active, if any.** It never
contains configuration values.

```toml
[preset]
id                       = "kedra"
kind                     = "base"      # or "derived"
applies_experience_layer = true
version                  = "0.1.0"

[[preset.modules]]
id      = "desktop"
enabled = true
```

### Vanilla is not "Kedra with everything off"

`applies_experience_layer = false` means the layer is never entered. Tooling
must check this flag **before** it looks at any module, so a Vanilla system
never has Kedra defaults written and then reverted — the sequence that produces
leftover keys, `pacnew` churn and half-reverted state.

Vanilla's module list is empty, and the validator enforces that it stays empty:
a preset with `applies_experience_layer = false` that lists modules is a hard
error. The "toggled off" shape is therefore not expressible.

### Custom Kedra inherits, it does not clone

A derived preset stores a pointer plus deltas:

```toml
[preset]
kind        = "derived"
base_preset = "kedra"

[[preset.module_overrides]]
id      = "terminal"
enabled = false
```

Resolution is: read `base_preset`, then apply `module_overrides` on top, keyed
by module ID. Anything the user did not touch resolves to whatever `kedra` says
**at resolution time**, not at the time the custom preset was written.

This is the point. When a future Kedra release adds a module, systems on a
custom preset pick it up, because there is no stale copy of the module list to
go out of date. A derived preset carrying a full `modules` list would be a
snapshot, and the validator rejects it for that reason.

A derived preset may not contradict its base on `applies_experience_layer`.
Deriving from Vanilla to switch the layer on is not a custom Kedra — it is
Kedra. The resolver rejects the flip in both directions.

See [`examples/custom-kedra.toml`](examples/custom-kedra.toml) for the full
shape. That file is an example, not shipped data.

A derived *preset* in the manifest tree is how the distro would ship a variant.
A single user's Custom Kedra is not a preset file at all — it is the
`[selection]` table of their [state file](#mutable-per-user-state), which the
resolver treats as one more derived layer on top of the base. Both forms use
the same base-plus-deltas rule and the same resolver.

## Resolution

`kedra_experience.resolve` turns a preset — or a user's state — into an
effective module set. It knows nothing about GNOME; it resolves identifiers and
booleans, and what an enabled module eventually *does* is a separate layer's
problem.

The algorithm, in order:

1. **Build the chain.** Walk `base_preset` from the leaf upward until reaching
   a preset whose `kind` is `base`. A repeated ID is an inheritance cycle and
   is rejected; so is a `base_preset` naming a preset that does not exist.
2. **Check layer consistency.** Every preset in the chain must agree on
   `applies_experience_layer`. A derived preset that flips it is rejected.
3. **Short-circuit on Vanilla.** If the layer does not apply, return an empty
   module set immediately — step 4 never runs, so no module is ever consulted.
   Any override present at this point is an error, not something to ignore.
4. **Seed from the base.** The base preset's `[[preset.modules]]` entries
   become the initial `id → enabled` map, each attributed to `preset:<id>`.
5. **Fold the derived layers, base-most first.** Each layer's
   `module_overrides` are applied in turn, so a leaf override always wins over
   an intermediate one. An override for a module the base never declared is an
   error, as is one for a module that does not exist.
6. **Apply user-state overrides last**, attributed to `user-state`.
7. **Emit sorted by module ID**, always.

Step 7 matters more than it looks: the resolved document is what a future
installer and configurator will diff against to decide whether anything needs
doing, so identical inputs must produce byte-identical output. Manifest
declaration order must never leak into it.

Output is a `Resolution`, serialized as stable JSON with its own
`resolution_schema_version` (independent of `schema_version` — consumers pin
the output format, not the manifest grammar):

```json
{
  "resolution_schema_version": 1,
  "preset_id": "kedra",
  "preset_version": "0.1.0",
  "applies_experience_layer": true,
  "chain": ["kedra"],
  "modules": [
    {
      "id": "appearance",
      "enabled": true,
      "version": "0.1.0",
      "tier": "recommended",
      "user_selectable": true,
      "source": "preset:kedra"
    }
  ]
}
```

`source` records which layer decided the final value, so a future UI can show
"you changed this" without re-deriving the inheritance.

## Mutable per-user state

One user's choices and Kedra's own bookkeeping, at:

```text
$XDG_STATE_HOME/kedra/experience-state.toml
```

defaulting to `~/.local/state/kedra/experience-state.toml` per the XDG Base
Directory specification. State, not config: it is data Kedra maintains about
what it has done, not hand-edited configuration — and it must survive without
being backed up or synced.

**No tool reads or writes this path implicitly.** Every entry point takes an
explicit path; `default_state_path()` only *computes* a string and never
touches the filesystem. That is what keeps the test suite and `makepkg` out of
the invoking user's home directory, and it is enforced by tests.

```toml
state_schema_version = 1

[selection]
base_preset = "kedra"

[[selection.module_overrides]]
id      = "terminal"
enabled = false

[applied]
generation                = 2
preset_id                 = "kedra"
preset_version            = "0.1.0"
experience_schema_version = 2
applied_at                = "2026-08-14T12:00:00Z"

[[applied.modules]]
id         = "desktop"
version    = "0.1.0"
active     = true
generation = 1
```

`[selection]` is what the user chose. `[applied]` is what Kedra last did, and
is absent until the first successful apply. See
[`examples/user-state.toml`](examples/user-state.toml), which is validated in
CI and by `makepkg` so it cannot drift.

Overrides are stored sorted and normalised on construction, so writing the same
choices twice produces the same bytes and the file stays diff-friendly.

### Applied-state tracking

Enough to answer "is this system up to date?" without inspecting a single
desktop setting:

| Field | Purpose |
|---|---|
| `generation` | Monotonic counter, +1 per successful apply. |
| `preset_id` / `preset_version` | Which preset was applied, at what version. |
| `experience_schema_version` | Which manifest grammar it was applied under. |
| `applied_at` | ISO 8601 timestamp. |
| `applied.modules[].version` | The module version that was applied. |
| `applied.modules[].active` | Whether the module was on. |
| `applied.modules[].generation` | When that module last actually changed. |

`generation` is deliberately a plain integer, not a hash or timestamp: it must
be comparable, reproducible in tests, and legible to a human reading the file.
A module's own generation only advances when its version or active state
actually changed, so "nothing to do" is distinguishable from "re-applied
identically" — the top-level generation moves, the module's does not.

Comparing `applied.modules[].version` against the installed manifest is how a
later run will know a module changed underneath the user. That comparison is
the foundation the first real GNOME adapter will build on.

`applied.managed_values` is reserved and must stay empty. A future GNOME
adapter will record individually managed values there, so it can tell "Kedra
set this and the user has not touched it" from "the user has since changed it".
Nothing writes it today, and the validator rejects a non-empty value.

### Ownership: what Kedra state does *not* claim

**Kedra-managed state must never imply ownership of unrelated user
configuration.**

Recording that Kedra applied the `workflow` module at version `0.1.0` is a
claim about *that module's values only*. It is not a claim over the user's
GNOME settings, their `~/.config`, their keybindings as a whole, or anything a
module did not explicitly set. Specifically:

- Kedra may only revert values it recorded setting, at the version it recorded.
- A value the user changed after Kedra set it belongs to the user. Kedra may
  report the divergence; it must not silently re-assert its own value.
- Disabling a module means Kedra stops managing its values. It does not mean
  Kedra resets unrelated settings that happen to be nearby.
- Uninstalling Kedra tooling must never take user configuration with it.
- Nothing outside a module's declared value set may ever be written, reverted
  or "cleaned up".

This is why the state file records module IDs and versions rather than a
snapshot of desktop settings: a snapshot cannot distinguish what Kedra set from
what the user did, so it invites exactly the overreach above. The validator
enforces the boundary structurally by rejecting unknown tables and keys in the
state file — general GNOME preferences have nowhere to go.

## Conditional dependency representation

A `conditional` package relation carries a structured `condition` table. As of
`schema_version = 2` the earlier unparsed expression string
(`condition = "installed(gnome-terminal)"`) is rejected outright.

```toml
[[module.packages]]
name      = "gnome-terminal"
relation  = "conditional"
condition = { type = "package-installed", package = "gnome-terminal" }
```

Exactly two types exist, each with exactly one operand:

| `type` | Operand | True when |
|---|---|---|
| `package-installed` | `package` | the named pacman package is installed |
| `module-enabled` | `module` | the named Experience module resolves to enabled |

This is a closed set of typed records, **not** an expression language. There is
no negation, no boolean composition, no precedence and no evaluator — a
condition can be inspected by tooling without anything resembling `eval`, and
the validator can cross-check a `module-enabled` operand against the modules
that actually exist. If a future need genuinely cannot be expressed, the right
move is to add a type to `CONDITION_TYPES` — not to add syntax.

Conditions describe *dependency applicability*. They never affect module
activation, which remains a preset's business alone.

## Module semantics

A module is an independently versioned, independently identifiable unit of the
experience.

| Field | Meaning |
|---|---|
| `id` | Stable identifier. Lowercase, dash-separated. Never reused, never renamed. |
| `name` | Human-readable, shown in UI. |
| `description` | One line, shown in UI. |
| `version` | The module's own semver. See [Versioning](#versioning). |
| `tier` | `core` \| `recommended` \| `optional`. |
| `user_selectable` | Whether a UI may offer this module as a toggle. |
| `introduced_in` | Module version that first shipped it. |
| `requires` / `conflicts` | Hard module-to-module relationships, by ID. See [Module-to-module relationships](#module-to-module-relationships). |
| `module.works_best_with` | Advisory relationships, with a `reason`. Never invalidates a profile. |
| `module.packages` | Package relationships, with strength. |
| `module.settings` | Setting declarations. Filled in by `workflow` and `desktop`. |
| `module.migrations` | Per-module migration history. Empty today. |

### Tier is not activation

`tier` describes a module's place in the product — how central it is, and how a
UI should present it. It does **not** decide whether the module runs. A `core`
module that a preset leaves `enabled = false` is legal and meaningful.

Activation is declared by presets, and only by presets. A module manifest may
not contain `enabled`, `default_enabled`, `active` or `enabled_by_default`; the
validator rejects those keys outright rather than ignoring them, because
silently accepting them would let the two concepts drift together over time.

## Dependency metadata

Two independent relationship graphs, deliberately not merged:

**Module → module** (`requires`, `conflicts`) — coherence. `workflow` requires
`desktop` because a shortcut scheme is meaningless without the shell behaviour
it assumes. The validator rejects references to modules that do not exist,
self-references, and cycles.

**Module → package** (`module.packages`) — what needs to be installed, and how
badly:

| Relation | Meaning |
|---|---|
| `required` | The module is broken without it. |
| `recommended` | Installed by default; the module degrades without it. |
| `optional` | Enhances the module; never installed automatically. |
| `conditional` | Applied only when `condition` holds. Requires a `condition`. |

Relation strength and activation state are orthogonal in both directions:
enabling a module does not promote its `optional` packages to `required`, and a
`required` package does not enable anything.

## Module-to-module relationships

Two, and the distinction between them is the whole point:

| Relation | Meaning | A enabled, B disabled |
|---|---|---|
| `requires` | A **cannot function correctly** without B. | **invalid profile** — a `RequirementViolation` |
| `works_best_with` | A and B are **designed to be used together**, and the standard Kedra preset enables both. A alone still works. | **valid profile** — an `Advisory` |

```toml
requires = []            # array of module IDs

[[module.works_best_with]]
id     = "desktop"
reason = "without the fixed five-workspace model, Super+1..5 do nothing for workspaces that do not exist"
```

`works_best_with` is an array of tables rather than of bare IDs because, unlike
a hard requirement, an advisory relationship has a *consequence* worth stating —
what the user gives up by separating the two. That belongs with the module that
knows it.

### Why the split exists

Until schema 4 there was only `requires`, and it was carrying both meanings.
Four modules declared `requires = ["desktop"]`; on inspection none of them read
a Desktop setting, shared a package with it, or stopped working without it. The
result was that a Custom Kedra which switched off `desktop` — an explicitly
supported customisation — reported four "unsatisfied requirements" and read as a
broken profile.

Overstating a dependency is not the safe direction to err in. It makes honest
customisation look like misconfiguration, and it trains people to ignore the
diagnostic.

Deliberately two levels, not a taxonomy: "hard" and "advisory" is the
distinction the product actually has.

The name is `works_best_with` rather than `recommends` because this same file
format already uses `recommended` for a **package** relation, and that one
*does* mean "install it by default". A module relationship must never install or
enable anything, so reusing the word would invite exactly the conflation the
rest of this architecture works to prevent.

### Neither relation ever acts

The resolver does not force-enable anything for either kind. Silently switching
a module back on behind a user who disabled it is the same failure as
overwriting a setting they changed — their explicit choice fails to stick. So a
violated requirement is *reported*, not repaired; the profile still resolves,
it is simply not valid, and tooling says which part.

A soft relationship in particular never:

- enables a module,
- adds a package to the resolved set, or
- overrides a user's choice.

Package resolution follows **active** modules only. `workflow works_best_with
desktop` with `desktop` disabled yields no `kedra-experience-desktop` — asserted
from both the activation side and the package side, because a recommendation
that quietly acts is worse than no recommendation at all.

### The diagnostics API

```python
hard_requirement_violations(tree, resolution) -> tuple[RequirementViolation, ...]
advisories(tree, resolution)                  -> tuple[Advisory, ...]
diagnose(tree, resolution)                    -> Diagnostics   # both, plus .ok
```

All pure, no I/O, sorted and deterministic. `Diagnostics.ok` is false only for
hard violations; advisories never make a profile invalid.

An `Advisory` carries structure, not prose: a stable `code`
(`works-best-with-disabled`) that consumers branch on, the two module IDs, and
the module author's `reason`. `describe()` renders a default sentence as a
convenience — a UI is free to ignore it and use the fields.

`kedra-experience-dev resolve` prints advisories on stdout and violations on
stderr under `errors:`, and exits non-zero only for the latter.

### What the shipped tree declares

After the schema 4 audit, **no shipped module declares a hard requirement.** Two
advisory relationships remain, both with evidence:

| Module | Relation | Related | Why |
|---|---|---|---|
| `workflow` | `works_best_with` | `desktop` | `Super+1..5` is designed around the [fixed five-workspace model](kedra-workspaces.md) |
| `terminal` | `works_best_with` | `appearance` | terminal presentation conventions are chosen to match the Kedra appearance layer |

`appearance`, `extensions` and `integration` now declare no module relationship
at all — their `requires = ["desktop"]` described nothing that was ever true.
The hard-requirement machinery is retained and enforced; it simply has no case
in the shipped data yet, and is exercised against synthetic trees in
`tests/test_dependencies.py`.

## Versioning

Five version axes, each moving on its own clock:

| Axis | Where | Covers |
|---|---|---|
| `schema_version` | every manifest | the manifest grammar |
| `experience.layout_version` | root descriptor | the directory tree |
| `module.version` | each module | that module's own semver |
| `state_schema_version` | user state file | the state grammar |
| `resolution_schema_version` | resolver output | the resolved-document format |
| `plan_schema_version` | plan output | the change-plan format |
| `observed_schema_version` | observed fixtures | the observed-value format |
| `execution_schema_version` | execution result | the execution-result format |

A reader that does not know a version must refuse, not guess. The separation is
load-bearing: the tree can be rearranged without rewriting every manifest, the
state format can change without touching the manifests, and a consumer of
resolver output pins that output's shape rather than the grammar behind it.

Per-module versions are the important one. Kedra is rolling-release: the
terminal module will need a migration on a schedule that has nothing to do with
the appearance module. A single global "Experience v1" integer would force
every module to move together and would make it impossible to say which
migration a given system still owes. Each [migration record](#migration-records)
carries the `module_version` that introduced it, which is what makes "which
migrations does this system still owe?" answerable.

The schema bumps so far show the machinery working:

| Bump | What changed | Modules affected |
|---|---|---|
| 1 → 2 | conditional-dependency grammar | `terminal`, `extras` only |
| 2 → 3 | `settings` / `migrations` became typed arrays | all seven |
| 3 → 4 | `works_best_with` split out of `requires` | five modules' relationships |

The 3 → 4 bump is required rather than cosmetic: the reader rejects unknown
keys, so a schema 3 reader handed a manifest with `works_best_with` would fail
outright. That is exactly the job `schema_version` exists to do.

## Where future configuration plugs in

Now defined rather than reserved:

- `module.settings` — [setting declarations](#setting-declarations), one record
  per value. Filled in by `workflow` and `desktop`; present and empty in every
  other module, so adding one is an edit rather than a new key.
- `module.shortcuts` — GNOME custom keyboard shortcuts, declared as one
  composite resource each. Used by `terminal`; see
  [docs/kedra-terminal.md](kedra-terminal.md#superenter).
- `module.migrations` — [migration records](#migration-records).
- `applied.managed_values` — [ownership records](#ownership-and-the-managed-value-record).

Still reserved:

- `presentation` on a `preset.module_overrides` entry — for sub-module choices
  once modules expose any.

Kedra Workflow v1 was the first test of this claim, and it held: shipping a real
keymap meant filling in `module.settings` and adding a package that installs the
rendered vendor override. No new file format, no new location, no new decision
rule.

Super+Enter was the second and harder test, because it is the first
`policy = "managed"` value — the first thing Kedra writes into a user's dconf.
It held too, with three additions and no redesign: a `path` on a gsettings
target, a `[[module.shortcuts]]` resource that expands into ordinary settings,
and `resource` / `member` on an ownership record. The planner, the ownership
rules, the generation semantics, the locking and the atomic save were all
unchanged. See [docs/kedra-terminal.md](kedra-terminal.md#superenter).

## Setting declarations

A module declares settings as an array of typed records. A declaration says
*what Kedra wants a value to be* — never whether it is currently set, who set
it, or what to do about a user who changed it. That is ownership state, and it
lives in the user's state file.

```toml
[[module.settings]]
id          = "example-close-window"
backend     = "gsettings"
policy      = "managed"
description = "Synthetic example — not a real Kedra binding."
target      = { schema = "org.example.wm", key = "close" }
value       = { type = "as", data = ["<Super>q"] }
```

| Field | Meaning |
|---|---|
| `id` | Stable, **globally unique** across the whole tree. Never renamed in place. |
| `backend` | Which backend owns the target. Only `gsettings` exists. |
| `policy` | `default` or `managed`. See [policies](#setting-policies). |
| `target` | Backend-specific. For `gsettings`: `schema` + `key`, plus `path` for a [relocatable schema](#relocatable-schemas-and-target-identity). |
| `value` | `{ type, data }` — an explicit GVariant type string plus its data. |
| `description` | Optional, human-readable. |
| `introduced_in` | Optional module version. |

Setting IDs are unique tree-wide rather than per module, because a user's
managed-value record refers to a setting by ID alone. Scoping IDs per module
would make moving a setting between modules a breaking change to every user's
state file. Targets are likewise unique tree-wide: two settings claiming one key
means there is no single answer to "what did Kedra last write here?", so the
validator rejects it even when both declare the same value.

Values are explicitly typed because the eventual backend is strictly typed and
TOML is not — a `1` that must be written as a double cannot be distinguished by
inspection. The vocabulary is a small GVariant subset (`b`, `i`, `d`, `s`, `as`,
`ai`); extending it means adding an entry to `VALUE_TYPES`.

There is no `command` or `shell` backend and no expression field anywhere in a
declaration. A manifest cannot ask for code to run.

### Setting policies

Two, because two is what the distinction requires:

| Policy | Meaning |
|---|---|
| `default` | Kedra's recommended default. The user may freely change it. Kedra writes nothing and takes no ownership, so there is nothing to revert. |
| `managed` | Kedra must actively write the value. Creates a managed-value record, and is the only policy that can ever require a revert. |

**Prefer `default` wherever the backend can express it.** For GSettings that
means a vendor override file (a `gschema.override` compiled into the schema),
which changes what the *default* is without writing anything into the user's
dconf. Three things follow, and they are the reason this is the preferred
route:

- The user's dconf stays empty for that key, so "the user changed it" is
  exactly "the key is set" — divergence detection becomes trivial and exact.
- Nothing needs reverting when a module is switched off; the override simply
  stops applying.
- A later Kedra release can change its recommendation freely, because a user
  who never touched the key follows along and a user who did is untouched.

`managed` exists for the cases a vendor default genuinely cannot express —
values that must be *created* rather than defaulted, such as a list whose
entries reference objects that do not exist until Kedra makes them. Those need
real ownership tracking, which is the rest of this section.

Vendor defaults are rendered from the manifests by
`tools/kedra_experience/vendordefaults.py`, one file per module, installed by
that module's own package (today: `kedra-experience-workflow`,
`kedra-experience-desktop`). One file per module because the file is the
delivery unit — a module's defaults must be removable on their own, which is
what makes "Kedra without Workflow" a real configuration rather than something
applied and then undone. The manifest is the single source of truth; the
override is generated, never hand-maintained, so the two cannot drift.

Two limits worth knowing before writing a `default` setting, both found by
measurement rather than assumed:

- **A vendor default cannot be scoped to a dconf path.** An override addresses a
  schema by ID, and a section naming a *relocatable* schema sets defaults for
  every instance of it at every path — which for GNOME's custom keybindings
  would alter shortcuts the user created themselves. Per-path state is the
  concrete case that forces `managed`. See
  [Super+Enter](kedra-workflow.md#superenter--blocked).
- **Filename order is not the whole precedence story.** A desktop-scoped
  `[schema:GNOME]` group beats a generic one regardless of which file each came
  from, and GNOME ships such groups. Every section is therefore emitted twice,
  generically and scoped. See
  [Beating GNOME's own vendor default](kedra-workspaces.md#beating-gnomes-own-vendor-default).

## Ownership and the managed-value record

Every `managed` setting Kedra writes produces one record in the user's state:

```toml
[[applied.managed_values]]
setting_id     = "example-close-window"
backend        = "gsettings"
target         = "gsettings:org.example.wm:close"
module_id      = "workflow"
module_version = "0.2.0"
policy         = "managed"
generation     = 2
last_applied   = { type = "as", data = ["<Super>x"] }
original       = { present = true, type = "as", data = ["<Super>o"] }
```

It answers every question the decision layer needs: what logical setting this
is, what backend owns it, what concrete target was written, what Kedra wrote
last time, under which module version and generation, and what was there before
Kedra first took ownership.

`original = { present = false }` is a distinct and important state: there was no
value here before Kedra, so a revert *unsets* the key rather than writing
something back. The validator rejects a value carried alongside `present =
false`, because the two together are contradictory.

### The three-value model, and what is not stored

```text
original          what was there before Kedra first took ownership   (stored)
last_applied      what Kedra wrote most recently                     (stored)
current_observed  what is there right now                            (NOT stored)
```

**`current_observed` is read live, never persisted.** Storing it would create a
second source of truth that goes stale the instant the user changes anything
outside Kedra — and a stale copy is precisely what would cause a user's edit to
be mistaken for Kedra's own value, which is the one failure this whole design
exists to prevent. Reading it live is authoritative and costs nothing.

Only targets a module explicitly declares are ever recorded. There is no dconf
dump, no schema-wide scan, no glob, and no path outside the declared target set.
The record shape is closed — the validator rejects unknown keys — so a broad
snapshot has nowhere to go even if someone tried.

### User-divergence detection

```text
diverged  ⟺  current_observed ≠ last_applied
```

Note what it is *not* compared against: the value Kedra currently wants. A value
differing from the current recommendation is not evidence of anything — the
recommendation may simply have moved.

> **Architectural rule.** A normal Kedra update must never overwrite a value
> merely because it differs from the current Kedra recommendation. If Kedra
> previously managed a value and the observed value differs from Kedra's last
> applied value, treat it as a user modification unless proven otherwise.

"Proven otherwise" is narrow: either the observed value happens to equal what
Kedra now wants, or the user explicitly asked for a reset. Everything else is
the user's.

### Decision table

Three operations, seven outcomes. `decide()` is a pure function — no I/O, no
session, no knowledge of GSettings.

**`apply`** — normal apply or upgrade of an enabled module:

| Record | Observed vs `last_applied` | Observed vs desired | Decision |
|---|---|---|---|
| none | — | equal | `apply` (records ownership, no write) |
| none | — | differs | `apply` ← **case 1** |
| exists | same | equal | `noop` |
| exists | same | differs | `update` ← **case 2** |
| exists | **differs** | equal | `noop` (refresh record only) |
| exists | **differs** | differs | `preserve-user-change` ← **case 3** |

**`unmanage`** — module switched off, or setting withdrawn:

| Record | Observed vs `last_applied` | Decision |
|---|---|---|
| none | — | `noop` |
| exists | same | `revert-to-original` ← **case 4** (unsets if no original) |
| exists | **differs** | `drop-record` ← **case 5** (value untouched) |

**`restore-defaults`** — explicit, user-confirmed:

| Record | Observed vs `last_applied` | Observed vs desired | Decision |
|---|---|---|---|
| any | — | equal | `noop` |
| none | — | differs | `apply` |
| exists | same | differs | `update` |
| exists | **differs** | differs | `force-apply` ← **case 6** |
| exists (withdrawn) | any | — | `revert-to-original` (forced) |

A `default`-policy setting always decides `noop` under `apply`: it is delivered
as a vendor default, so Kedra neither writes it nor owns it.

### Normal upgrade vs explicit reset

`force-apply` is a **separate decision** from `update`, not a flag on it. That
is deliberate: it makes "this overwrote something you changed" impossible to
reach from an upgrade path, and a test asserts that no combination of inputs
produces `force-apply` under the `apply` operation. Explicit reset may be
destructive, but only inside the Kedra-owned scope the user selected, and it
always sets `requires_confirmation`.

### Change plans

`build_plan()` produces a side-effect-free description of what *would* happen —
the substrate for dry runs, configurator previews, reset previews and tests.
Observed values arrive as a mapping the caller supplies, so there is no code
path from the planner to a live session even by accident.

Each entry carries the setting ID, backend, target, module and version, policy,
decision, reason, whether it writes, whether it needs confirmation, and the
current, desired and original values. Entries are always sorted by setting ID,
so two runs over the same inputs produce byte-identical JSON and a preview can
be diffed.

```console
$ kedra-experience-dev plan state.toml observed.toml
operation:  apply
generation: 2
entries:    1 (0 write, 1 need confirmation)
 ! example-close-window  preserve-user-change  value changed since Kedra applied it; ...
```

Observed-value fixtures are TOML, and `absent = true` spells "unset" explicitly
rather than leaving it to omission:

```toml
observed_schema_version = 1

[[observed]]
target = "gsettings:org.example.wm:close"
type   = "as"
data   = ["<Super>z"]
```

## Execution

### Planner decides, executor executes

The division is absolute. The executor does not compare observed values against
desired ones, does not ask whether a user diverged, and has no opinion about
policy — it reads a decision off each plan entry and carries it out. That is
what keeps "may Kedra overwrite this?" answerable in exactly one place.

| | Planner | Executor |
|---|---|---|
| Decides ownership | yes | **never** |
| Touches a backend | never | yes |
| Writes state | never | yes |
| Pure | yes | no |

### First ownership and original capture

On the first successful `managed` apply for a setting, in this order:

1. the current value is observed (supplied by the caller, not read by the planner);
2. it is captured as `original`;
3. the desired value is written to the backend;
4. **only after that write succeeds** is the ownership record created.

If the write fails, no record exists — so state never claims an apply that did
not happen, and the next run simply tries again.

`original` is captured **once** and is never rewritten by a later update:

```text
before Kedra          O
Kedra v1 writes       X    → original = O, last_applied = X
Kedra v2 updates      Y    → original = O, last_applied = Y     ← O, not X
```

`original = { present = false }` records that there was no value before Kedra.
That reverts by *unsetting* the target rather than writing a fabricated value
back, which is why the distinction is preserved end to end.

### Managed record lifecycle

| Event | `original` | `last_applied` | Record |
|---|---|---|---|
| First apply (write succeeds) | captured from observed | desired | created |
| First apply, value already correct | captured from observed | desired | created, no write |
| Safe update | **preserved** | desired | updated |
| No-op | preserved | unchanged | unchanged |
| User converged on desired | preserved | refreshed to desired | refreshed, no write |
| Preserve-user-change | preserved | **unchanged** | unchanged |
| Unmanage, unchanged | — | — | removed after successful revert |
| Unmanage, diverged | — | — | removed, no write |
| Explicit restore (force) | **preserved** | desired | updated |
| Any failed write | preserved | **unchanged** | unchanged |

Two entries deserve emphasis.

**Preserve-user-change must not touch `last_applied`.** Updating it to the new
desired value would erase the evidence of what Kedra actually wrote, and the
divergence would become undetectable — Kedra would silently adopt the user's
value as its own and overwrite it on the following release.

**Explicit restore preserves `original`.** Restore-defaults re-asserts Kedra's
value *within the existing ownership lifecycle*; it does not begin a new one. A
later unmanage therefore still returns the true pre-Kedra value, not whatever
Kedra happened to have written before the restore.

### Generation semantics

A generation is **one successful apply transaction**, not one setting write.

- The next generation is computed once, before any write.
- Every record the run changes is stamped with that same generation.
- It is committed only if at least one record actually changed.

| Plan | Generation |
|---|---|
| At least one record created, updated, refreshed or removed | incremented once |
| All no-op | **not incremented**; state is not written at all |
| Preserve-user-change only | **not incremented**; nothing changed |
| Partial failure with ≥1 success | incremented once, shared by the successes |

An all-noop or preserve-only run is a read-only operation. Burning a generation
on it would make the counter measure invocations rather than changes.

### Partial failure

There is no atomic transaction across a desktop backend, and this layer does not
pretend otherwise. What it guarantees instead:

1. Backend writes happen before state claims success.
2. A failed write never produces a record claiming the value was applied.
3. State is persisted once, atomically.
4. Partial failure is reported explicitly (`ok`, `partial`, per-entry `error`).

**Execution continues past a failure.** Entries are independent, so abandoning
the rest would forfeit progress for no benefit.

**There is no rollback**, and that is a deliberate choice rather than a missing
feature. Undoing an earlier successful write would mean issuing *more* writes —
which can themselves fail, turning one partial success into two inconsistent
halves — in order to revert a change the user's desktop already reflects.
Instead every successful write is recorded truthfully, and the next run
reconciles from state plus a fresh observation.

### Retry and idempotency

Re-running after a partial failure is safe. The planner re-derives every
decision from the committed state and freshly observed values, so entries that
succeeded resolve to `noop` and only the failed ones are attempted again.
Resetting an already-unset target is likewise not an error — post-conditions,
not operations, are what the model is defined on.

**Known crash window.** State is committed after the backend writes. If the
process dies in between, the writes happened but no record exists. State is then
*stale, not false*: it under-claims ownership rather than over-claiming it. The
bounded consequence is that a subsequent run captures `original` from the
current value — Kedra's own value rather than the true pre-Kedra one — so a
later revert restores that instead. Closing this window requires an intent
journal, which is deliberately not built here; it is listed as a known
limitation rather than papered over.

### State locking

```text
$XDG_STATE_HOME/kedra/experience-state.lock
```

An advisory `flock(2)` held across the whole read → plan → execute → save cycle,
because locking only the save would leave two processes free to plan from the
same stale state.

- The kernel releases it when the descriptor closes, **including on crash or
  `kill -9`**, so a dead process cannot leave a permanent stale lock. No PID
  file, no staleness heuristic, no daemon.
- It is a sibling `.lock` file, not the state file itself: the state file is
  replaced by `rename(2)` on every save, and a lock held on the old inode would
  say nothing about the new one.
- It locks **only Kedra's own state**. Nothing global, nothing belonging to
  dconf or GNOME.

Acquisition fails immediately by default; a timeout may be given. Blocking
forever is not offered — a tool that hangs on a lock is worse than one that says
who holds it.

### Atomic persistence

`UserState.save()` performs, in this order: serialize fully in memory (so a
serialization error never touches the filesystem), write a temporary file **in
the same directory**, `flush` + `fsync` it, `os.replace`, then `fsync` the
directory. A concurrent reader sees either the whole old file or the whole new
one. On any failure the temporary is removed and the previous state is left
exactly as it was. An existing file's permissions are preserved; new files are
`0o644`.

### Default policy is excluded from the executor

A `default`-policy setting is never written by the executor and never produces
an ownership record — it is delivered as a vendor default, which is a separate
mechanism. This is enforced twice: the planner decides `noop`, and the executor
independently refuses a write entry whose policy is `default`. Turning a vendor
default into a managed setting by accident would create an ownership claim over
a value Kedra never wrote.

## Migration records

A module declares migrations as an array. Each record describes *what changed*;
none of them is a program. There is no script or command type, no eval, and no
shell hook — a migration that could run arbitrary code would be a far larger
security surface than the thing it migrates.

```toml
[[module.migrations]]
type           = "rename-setting"
module_version = "0.2.0"
from_id        = "example-old-name"
to_id          = "example-new-name"
```

| `type` | Required fields | Intended semantics |
|---|---|---|
| `rename-setting` | `from_id`, `to_id` | Re-key managed records. No backend write. |
| `remove-setting` | `setting_id`, `on_managed` | Setting is gone. `on_managed` is `revert` or `drop`. |
| `retarget-setting` | `setting_id`, `from_target` | Target moved; new target comes from the declaration. |
| `change-default` | `setting_id` | Only the recommendation changed — informational, normal upgrade rules apply, so a diverged user keeps their value. |

Every record carries `module_version`: the version that introduced it, which is
how a system knows which migrations it still owes. That is what per-module
versioning was built for.

The validator cross-checks references against the settings that actually exist:
a `rename-setting` must land on a declared ID and come *from* one that no longer
exists; a `remove-setting` must name a setting that is genuinely gone; a
`retarget-setting` or `change-default` must name one that still exists.

Only the record format and its validation exist today. There is no migration
executor, and building one is a later task.

### The executor's migration boundary

Because no migration executor exists, the executor **refuses to run** rather
than corrupt ownership, in two cases:

- a managed record's setting ID appears as the `from_id` of a `rename-setting`
  migration — the setting still exists under a new ID, so executing would revert
  the old ID *and* separately take ownership under the new one;
- a record's target no longer matches the setting's declared target — the record
  points at something Kedra no longer manages.

Both raise `MigrationRequired`, before any write. A `remove-setting` migration is
deliberately *not* refused: the setting is genuinely gone, and reverting it is
correct.

## Backends

Two deliberately separate halves:

| | Purpose | Implementations |
|---|---|---|
| `BackendSpec` | Declarative: validate targets and values, canonicalise a target. **No I/O, ever.** | `GSettingsSpec` |
| `BackendAdapter` | Side effects: `read`, `read_effective`, `apply`, `reset`. | `GSettingsAdapter` (production), `InMemoryAdapter` (test double) |

The planner depends only on `BackendSpec`, which has no method that could reach
a session — that is a structural guarantee, not a convention, and a test asserts
the spec exposes no `read`/`apply`/`reset` at all.

Only one backend is defined because only one is needed. The abstraction exists
to keep GNOME concepts out of the generic layer, not to speculate about backends
nobody has asked for.

### Relocatable schemas and target identity

A GSettings schema is normally installed at a fixed path, so naming the schema
names the storage location. A **relocatable** schema declares no path: it is a
shape, instantiated once per thing, each instance at a path its creator chooses.
GNOME's custom keybindings are the case Kedra has.

So a target is `schema` + `key` for a fixed schema, and `schema` + `path` + `key`
for a relocatable one, and the canonical identity string carries the path:

```
gsettings:org.gnome.mutter:dynamic-workspaces
gsettings:org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kedra-terminal/:binding
```

The path is part of the *identity*, not decoration: two instances of one schema
are two different values, and an ownership record that could not tell them apart
would revert the wrong shortcut. Fixed-schema targets keep their original
three-part spelling, so records written before relocatable support existed still
resolve.

Which schemas are relocatable is **declared data** (`backends.RELOCATABLE_SCHEMAS`),
not discovered by reading the installed schemas. The declarative half performs no
I/O, and a manifest must validate identically on a build machine with no GNOME
installed. An integration test checks the declared set against what GLib actually
says, which is where the two are kept honest.

Validation is strict in both directions:

- a relocatable schema **without** a path is rejected;
- a fixed schema **with** a path is rejected — it would either duplicate the
  schema's own path or contradict it, and contradicting it makes GLib abort;
- a path that is relative, has no trailing slash, contains `//`, `..`, `.`,
  whitespace or a colon, or is `/` itself, is rejected;
- a schema ID that is really a command line (`gapplication launch …`), a binary
  path (`/usr/bin/kgx`) or a dconf path is rejected as a schema.

### The GSettings adapter

`kedra_experience.gsettings.GSettingsAdapter` is the only module in the package
that talks to a real configuration system, and the only one that imports GLib.
It uses `Gio.Settings` through PyGObject rather than driving the `gsettings`
command line, and the difference is not stylistic:

- **Types survive.** `gsettings get` prints GVariant *text* that has to be parsed
  back, and the round trip is lossy exactly where it matters — `10` is an integer
  or a double depending on the key it came from, and an empty array prints
  without its element type. `get_value()` returns a typed GVariant.
- **Values are never re-parsed from a command line.** No string concatenation, no
  shell, so a value cannot be reinterpreted as syntax.
- **"Is a value explicitly stored?" is answerable** — see below. Through the
  command line it is not, and that alone rules the approach out.
- **Failures are structured.** A write refused because a key is locked down is a
  return value, not a message on stderr to pattern-match.

GLib treats a missing schema or key as a *programmer* error and calls `g_error()`,
which aborts the process — there is no exception to catch. Four calls would abort
if made blindly, so none of them is: the schema is looked up through
`Gio.SettingsSchemaSource` first, the key is checked with `SettingsSchema.has_key()`,
and the schema's own path is compared against the target's. Each check turns an
abort into a `BackendError`, which is what the executor is built to handle. A key
whose type is outside the value vocabulary (`org.gnome.Console scrollback-lines`
is `x`, int64) is refused rather than approximated, because a lossy read would be
recorded as `original` and written back later as if it were the user's own value.

Writes are flushed with `Gio.Settings.sync()` before the call returns. Without
that, the executor could record a value as applied, the process could exit, and
the write could never be delivered — state claiming something the desktop never
received.

### Explicit value versus effective value

The single most important thing the adapter gets right:

> **effective value == default does NOT imply no explicit value is stored.**

A user may set a key to precisely the value it would have shown anyway. If Kedra
reads the *effective* value and calls that "unset", it records
`original.present = false`, and a later revert **unsets a key the user had set** —
silently discarding an explicit choice that happened to agree with the default at
the time. The mirror case is just as wrong: after a vendor default changes
underneath, a key the user never touched would start looking like a choice.

So the adapter answers two different questions with two different methods:

| Method | GLib call | Question |
|---|---|---|
| `read()` | `g_settings_get_user_value()` | What is *stored* here? `None` when nothing is. |
| `read_effective()` | `g_settings_get_value()` | What is *in force* here, stored or inherited? |

`read()` is what ownership uses, and `None` from it is a real state — "no value
here before Kedra" — that reverts by unsetting rather than by writing something
back. `read_effective()` is used only where Kedra must reason about a value it
does not own the whole of: the list-membership case, where the entries around
Kedra's own have to be preserved exactly whether or not anyone ever set them.

There is no `dconf dump` and no broad read of anybody's database. `get_user_value`
answers the question at one key; a test forbids the string `dconf dump` from
appearing in the package at all.

### Composite resources and list membership

Two additions on top of an ordinary setting, both driven by GNOME custom
shortcuts and neither generalised beyond them:

`resource`
: The logical resource a setting belongs to. Settings sharing one are unmanaged
  *together* — if the user has materially changed any value of the resource, a
  normal unmanage stops claiming all of it and deletes none of it, rather than
  dismantling a shortcut the user has made their own.

`member`
: Marks a **list-membership** claim: Kedra owns the presence of one entry in an
  array-typed key, and nothing else about the array. The planner projects the
  observed list down to `['<member>']` or `None` before any decision is made, so
  `decide()` runs unchanged and `original` records "was my entry there before
  me?" rather than what the list used to be. On revert, one string is removed
  from whatever the list is *now* — a remembered list is never written back, so
  entries the user added afterwards cannot be deleted.

Both fields are **generated**, never declared: writing either into a
`[[module.settings]]` table is rejected as an unknown key. A composite resource
is declared as a resource, or not at all.

Neither is a collection framework. `list_with_member` and `list_without_member`
are two pure functions, and the one list Kedra takes part in is
`custom-keybindings`. See [docs/kedra-terminal.md](kedra-terminal.md#superenter).

### What still cannot happen

The package now writes desktop settings, so the old blanket claim is gone and a
narrower one is enforced in its place. A test parses every module's AST — parsing
rather than searching text, so prose explaining *why* there is no subprocess does
not trip it, and an aliased import does — and asserts:

- no module imports `subprocess`, `pty` or `commands`;
- no module calls `system`, `popen`, `eval`, `exec` or any `spawn*`;
- no string literal begins a `gsettings` or `dconf` command line, and the phrase
  `dconf dump` appears nowhere;
- the one command string Kedra produces, `gapplication launch <APPID>`, is built
  in exactly one module and one function.

`accelerators` is additionally checked for the *absence* of a hand-written
parser: a modifier-mask constant or a keysym table appearing there would mean
GTK's answer was being second-guessed, and the disagreements would surface as
"no conflict". See [docs/kedra-terminal.md](kedra-terminal.md#accelerator-conflicts).

Branding attaches through the `appearance` and `integration` modules, which
name `kedra-branding` as an `optional` package providing
`kedra-branding-provider`. That is the entire integration point: no colours, no
logo, no mascot, no assumption that the package exists. The Experience layer
must remain fully functional without it.

## What must never enter these manifests

These files are distributed to every Kedra user and are world-readable in
`/usr/share`. They describe **what a Kedra desktop is**, never **what one
machine or one person has**.

Never commit any of the following into preset or module metadata:

- **Secrets** — passwords, API tokens, SSH or GPG keys, certificates, Wi-Fi
  PSKs, keyring contents.
- **Account information** — usernames, real names, email addresses, avatars,
  online-account or cloud-sync configuration.
- **Machine-specific state** — hostnames, MAC addresses, UUIDs, serial numbers,
  hardware inventories, monitor layouts, IP addresses, timezone or locale
  captured from a machine rather than chosen as a default.
- **Personal paths** — anything under `/home/<user>`, `$HOME` expanded to a
  literal, bookmarks, recent-file lists, mount points naming a person.
- **Cache and history** — shell history, browser history, thumbnails, search
  indexes, `recently-used.xbel`, application usage counters.
- **Browser and application profiles** — profile directories, cookies,
  sessions, extension state, saved logins.
- **Anything captured from a real system.** In particular: do not generate
  these manifests from a `dconf dump`, and do not copy a developer's
  `~/.config` into the tree. A working desktop is not a specification of one.
  Every value that eventually lands in `module.settings` must be a deliberate
  product decision, written by hand, with a reason.

The rule of thumb: if two Kedra installations on two different machines should
not have byte-identical copies of it, it does not belong in this tree.

Note the asymmetry with the user-state file: the same list applies there for
*different reasons*. The manifests must not carry personal data because they
are distributed; the state file must not carry it because Kedra does not own
it. Neither is a place to put a user's GNOME preferences.

## Developer tooling

`tools/kedra-experience-dev` is internal infrastructure — not shipped, not
user-facing, and deliberately not a profile manager. It exists so resolution
and state validation are exercisable by hand and from tests.

```console
$ tools/kedra-experience-dev resolve --preset kedra
$ tools/kedra-experience-dev resolve --preset kedra --format json
$ tools/kedra-experience-dev resolve --state ./some-state.toml
$ tools/kedra-experience-dev validate-state ./some-state.toml
$ tools/kedra-experience-dev validate-manifests
$ tools/kedra-experience-dev state-path      # prints only; touches nothing
$ tools/kedra-experience-dev plan ./state.toml ./observed.toml
$ tools/kedra-experience-dev plan ./state.toml ./observed.toml \
      --operation restore-defaults --format json
$ tools/kedra-experience-dev execute ./state.toml ./observed.toml
$ tools/kedra-experience-dev execute ./state.toml ./observed.toml --dry-run --format json
$ tools/kedra-experience-dev applications --preset kedra    # shows the derived launch command
$ tools/kedra-experience-dev shortcuts --preset kedra       # shows each shortcut and its four settings
$ tools/kedra-experience-dev shortcut-conflicts ./state.toml ./observed.toml
```

`shortcuts` resolves a profile, expands its shortcut declarations and prints
them. `shortcut-conflicts` additionally reports which registered custom shortcut
holds an accelerator Kedra wants — against an in-memory fake seeded from an
observed fixture, so it too reaches no live session. Neither reads dconf, opens
GSettings or launches anything. It is
deliberately **not** a terminal selector and deliberately **not** an apply
command: there is no subcommand in this tool that writes to a live desktop, and
`execute` runs against the in-memory fake as it always has.

`execute` runs the real executor against an **in-memory fake backend** seeded
from the observed fixture, and rewrites the given state file in place under the
state lock. It cannot reach a live session; there is no adapter that could.

`--data-dir` points at any manifest tree and defaults to the in-tree package
data. No subcommand reads or writes a path it was not given.

The Python API behind it is `tools/kedra_experience/`:

| Module | Responsibility |
|---|---|
| `manifests` | Load and validate the immutable tree; produces a `Tree`. |
| `resolve` | `resolve_preset(tree, id)` / `resolve_state(tree, state)` → `Resolution`. |
| `state` | `UserState` load/save/validate, applied-state and ownership records. |
| `values` | `TypedValue`, the GVariant type subset, exact comparison. |
| `settings` | `SettingDeclaration`, setting and migration record validation. |
| `backends` | `BackendSpec` (declarative), `BackendAdapter`, `InMemoryAdapter`, list-membership algebra. |
| `gsettings` | `GSettingsAdapter` — the production backend. The only module that imports GLib. |
| `shortcuts` | GNOME custom shortcuts as one composite resource; the canonical Kedra path; launch-command derivation; the launchability seam. |
| `accelerators` | Canonical accelerator identity, via GTK. No fallback parser: absent GTK means *unknown*, never *no conflict*. |
| `conflicts` | Read-only discovery of registered custom shortcuts, and structured conflict classification. |
| `ownership` | `decide()` — the pure decision layer. |
| `plan` | `build_plan()` → `ChangePlan`. |
| `execute` | `execute_plan()` → `ExecutionResult`; performs the authorized writes. |
| `locking` | `state_lock()` — advisory flock on Kedra's own state. |
| `observed` | Observed-value fixtures. |
| `cli` | The developer CLI above. |

## Validation

```console
$ python3 tools/validate-experience.py
$ python3 -m unittest discover -s tests
```

Setting validation detects unknown and missing keys, invalid setting IDs,
unknown backends (there is no `shell` or `command` backend to reach for),
invalid policies, malformed GSettings schema IDs and key names, unknown value
types, values whose data does not match their declared type, out-of-range
integers, setting IDs duplicated anywhere in the tree, and two settings claiming
one target. Migration validation detects unknown types (including `script`),
missing `module_version`, keys that do not belong to the declared type, invalid
`on_managed`, and references to settings that do or do not exist as the type
requires.

Managed-value validation detects unknown and missing keys, invalid setting IDs,
unknown backends and policies, malformed `last_applied` and `original` values,
an `original` marked absent while carrying a value, duplicate records, duplicate
targets, and a record generation ahead of `applied.generation`.

Everything is stdlib-only. Manifest validation detects malformed TOML, missing
or unsupported schema/version fields, non-semver versions, duplicate module
IDs, filename/ID mismatches, preset references to nonexistent modules, invalid
package relation types, `conditional` without a condition, malformed or
unknown condition types, `module-enabled` conditions naming a nonexistent
module, string conditions left over from schema 1, invalid `tier` and `kind`
values, unknown keys, module dependency cycles, activation keys in module
manifests, derived presets that clone their base, and presets that claim not to
apply the Experience layer while still listing modules.

Resolution additionally rejects unknown and cyclic base presets, overrides for
modules that do not exist or that the base never declared, malformed override
entries, and any attempt to change `applies_experience_layer` down an
inheritance chain.

State validation rejects malformed TOML, missing or unsupported
`state_schema_version`, a missing or invalid `base_preset`, duplicate or
malformed overrides, non-semver versions, invalid timestamps, non-positive or
inconsistent generations, and any unknown table or key — which is what
structurally keeps general GNOME preferences out. It additionally rejects a
`member` that is not an absolute GSettings path, and a membership record whose
`last_applied` is anything other than exactly `['<member>']`: a record holding
the surrounding list would claim ownership of entries Kedra never wrote, so the
destructive shape cannot exist on disk at all rather than merely not being
produced.

Shortcut validation detects unknown and missing keys, a policy other than
`managed` (with the reason — a relocatable schema's override applies to every
instance at every path), a path outside GNOME's `custom-keybindings` root, a path
outside the Kedra namespace, malformed paths, empty names and bindings, a command
source table with an unknown or missing key, an `application_role` no module
fills, and duplicate shortcut IDs.

All of it runs as `check()` during `makepkg`, so a broken manifest, a broken
resolution or a stale documented example fails the package build rather than
shipping.
