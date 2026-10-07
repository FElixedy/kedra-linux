# kedra-packages

Package sources for [Kedra Linux](https://github.com/KedraLinux), a CachyOS /
Arch Linux downstream with GNOME as its primary desktop.

One directory per package, each containing a `PKGBUILD` and its payload —
the layout Arch downstreams conventionally use.

```text
kedra-packages/
├── kedra-experience/       Experience profile & module metadata
│   ├── PKGBUILD
│   └── data/experience/    installed to /usr/share/kedra/experience/
├── kedra-experience-desktop/   Kedra fixed five-workspace model
│   └── PKGBUILD            override rendered from the manifests at build time
├── kedra-experience-workflow/  Kedra Workflow v1 GSettings vendor defaults
│   └── PKGBUILD            override rendered from the manifests at build time
├── kedra-experience-runtime/   the shipped resolver and CLI
│   ├── PKGBUILD            installs a subset of tools/kedra_experience
│   └── bin/kedra-experience    installed as /usr/bin/kedra-experience
├── docs/                   developer documentation
├── tools/
│   ├── kedra_experience/   manifest loading, resolution, user state
│   ├── kedra-experience        the SHIPPED CLI, runnable from this checkout
│   ├── kedra-experience-dev    internal developer CLI (not shipped)
│   ├── kedra-packages-build    build every package into a clean staging dir
│   └── validate-experience.py  manifest validator entry point
└── tests/                  tests for that tooling
```

`tools/kedra_experience/` serves both audiences. `kedra_experience.RUNTIME_MODULES`
names the subset `kedra-experience-runtime` installs — exactly the transitive
import closure of `prodcli`, the shipped CLI. Everything else is repository
infrastructure and is not packaged.

## Packages

| Package | Contents |
|---|---|
| `kedra-experience` | Declarative preset and module metadata for the Kedra desktop experience, including the terminal application contract. Metadata only — it ships no GNOME configuration itself. |
| `kedra-experience-workflow` | The Kedra Workflow v1 keymap, as a GSettings vendor override generated from those manifests. Separate and removable, so Vanilla GNOME can simply not have it. |
| `kedra-experience-desktop` | The Kedra fixed five-workspace model, likewise. One defaults package per module, so each module's behaviour can be switched off on its own. |
| `kedra-experience-runtime` | The profile resolver and `/usr/bin/kedra-experience`, the command that answers "which profile is this, and what does it need?" on an installed system, records the chosen profile at install time, and applies its managed part in the user's own session on first login. Since 0.4.0 the executor, ownership layer and GSettings adapter are shipped with it; what it still never writes is a vendor default — see [`docs/kedra-experience-runtime.md`](docs/kedra-experience-runtime.md). |

## Kedra Experience

Kedra ships three desktop products: **Vanilla GNOME** (upstream, untouched),
**Kedra Experience** (curated, opinionated, overridable) and **Custom Kedra
Experience** (Kedra plus user overrides, stored as deltas rather than as a
copy).

`kedra-experience` defines the data model those products are built from —
presets, modules, dependency metadata and versioning — the resolver that folds a
base preset and its overrides into an effective module set, the per-user state
model that records what was applied, and the ownership contract that decides
whether a value may be written at all.

Almost nothing here writes a desktop setting, and that is deliberate. Real GNOME
behaviour ships the other way round — as **vendor defaults**, a
`.gschema.override` generated from the module manifests and installed by a
separate removable package. It changes what a key reads as when the user has set
nothing, puts no value in anyone's dconf, and needs no revert. See
[`docs/kedra-workflow.md`](docs/kedra-workflow.md).

Exactly one value is written: **`Super+Enter` opens the Kedra terminal.** GNOME
can only represent that as a *custom keybinding* — a relocatable schema instance
at a per-shortcut dconf path, plus that path's membership of a list — which no
vendor default can express, so it is Kedra's only `policy = "managed"` setting
and the reason a real GSettings adapter exists. Kedra owns its own shortcut and
nothing around it: disabling the Terminal module removes Kedra's entry from
GNOME's shortcut list and leaves every shortcut the user created, before or
after, exactly where it is. If the user rebinds or repoints it, Kedra stops
claiming it rather than taking it back.

And if an unrelated shortcut already holds `<Super>Return`, Kedra does not
register at all. It will not delete, rebind or reorder somebody else's shortcut
to free an accelerator, and it will not create a second grab on the same
combination — even under an explicit "restore defaults". Accelerator equivalence
is decided by GTK, the parser GNOME itself uses, so `<Super><Shift>1` and
`<Shift><Super>1` are recognised as one combination; where GTK is unavailable
the answer is *unknown*, and unknown blocks rather than assuming the key is
free.

`terminal` also declares which terminal application the Kedra experience *is* —
GNOME Console, as `org.gnome.Console.desktop` — and the launch command is
*derived* from that contract rather than written anywhere, so selecting a
different terminal is a one-line edit. Beyond those two things it ships no
configuration: every key in `org.gnome.Console` was reviewed and upstream kept,
so there is no override to render and deliberately no `kedra-experience-terminal`
package. See [`docs/kedra-terminal.md`](docs/kedra-terminal.md).

The governing rule is in place first, by design:

> A normal Kedra update must never overwrite a value merely because it differs
> from the current Kedra recommendation. If Kedra previously managed a value and
> the observed value differs from Kedra's last applied value, treat it as a user
> modification unless proven otherwise.

Two kinds of data, never mixed:

| | Location | Owner |
|---|---|---|
| Immutable profile definitions | `/usr/share/kedra/experience/` | the package |
| Mutable per-user state | `$XDG_STATE_HOME/kedra/` | the user |

Read [`docs/kedra-experience.md`](docs/kedra-experience.md) before adding or
changing a manifest — in particular the sections on what must never be stored
in them and on what Kedra-managed state does not claim ownership of.

## Building

```console
$ tools/kedra-packages-build              # all four, into a clean staging dir
$ tools/kedra-packages-build kedra-experience-runtime   # or just one
```

It builds outside the source tree, forces `--check`, and verifies makedepends
and checkdepends with `pacman -T` rather than installing anything. Bare
`makepkg -f` in a package directory still works, except for
`kedra-experience-runtime`, whose `depends` name a sibling package that a build
host does not have installed.

`makepkg` runs the manifest validator and the test suite in `check()`, so a
malformed manifest fails the build. Each defaults package additionally compiles
its generated override with `glib-compile-schemas --strict`, alongside GNOME's
own override files so precedence is exercised rather than assumed — into a
scratch directory under `$srcdir`, never `/usr/share/glib-2.0/schemas`, and
neither reading nor writing the building user's own GSettings values.

## Validating and testing

```console
$ python3 tools/validate-experience.py     # validate shipped manifests
$ python3 -m unittest discover -s tests    # run the full test suite
```

Both are standard-library Python 3.11+ (TOML via `tomllib`). The repository
adds no runtime or build dependency for parsing.

`tools/kedra-experience-dev` is internal developer infrastructure — not
shipped, not user-facing, and deliberately not a profile manager:

```console
$ tools/kedra-experience-dev resolve --preset kedra
$ tools/kedra-experience-dev resolve --state ./state.toml --format json
$ tools/kedra-experience-dev validate-state ./state.toml
$ tools/kedra-experience-dev plan ./state.toml ./observed.toml
$ tools/kedra-experience-dev execute ./state.toml ./observed.toml
$ tools/kedra-experience-dev vendor-defaults --list
$ tools/kedra-experience-dev vendor-defaults --module desktop   # render, to stdout
$ tools/kedra-experience-dev packages --preset kedra            # queries no package DB
$ tools/kedra-experience-dev applications --preset kedra        # launches nothing
$ tools/kedra-experience-dev state-path
$ tools/kedra-experience-dev module-version --module desktop
```

`tools/kedra-experience` is the *shipped* command run straight from the
checkout. It differs from the installed `/usr/bin/kedra-experience` only in
where it imports the library from, so `--data-dir` is needed to point it at the
in-tree manifests instead of `/usr/share/kedra/experience`:

```console
$ tools/kedra-experience --data-dir kedra-experience/data/experience profiles
$ echo '{"base_preset":"kedra"}' |
      tools/kedra-experience --data-dir kedra-experience/data/experience \
          resolve --selection -
```

No command reads or writes a path it was not given, so the tests and the
package build never touch the invoking user's home directory.

## Conventions

- Manifests are TOML, one file per preset or module, filename equal to `id`.
- Everything under `data/` is read-only, package-owned system data. Mutable
  user state never goes there.
- Module IDs are stable: never renamed, never reused.
- Activation state lives in presets only; module manifests never declare it.
- Setting IDs and backend targets are unique across the whole tree.
- Prefer `policy = "default"`: a GSettings vendor default writes nothing into a
  user's dconf, so an explicit user value always wins and removal restores
  upstream GNOME. `managed` is only for values a vendor default cannot express.
- Vendor override files are generated from the manifests, never hand-edited —
  one file, and one package, per module, so a module's defaults are removable
  on their own.
- `requires` means a module cannot function without another; `works_best_with`
  means they are designed to be used together. Only the first can invalidate a
  profile, and neither ever force-enables a module or adds a package.
- An application is named by its desktop-file ID, never a binary path or a
  command line — `module.applications` carries no exec field, and a consumer
  that needs to launch one resolves the ID through GAppInfo. A declaration is
  not a requirement: only an *enabled* module contributes a contract, which is
  what keeps Vanilla free of a dependency it never applied.
- Do not overstate a dependency. Making an honest customisation look like
  misconfiguration is worse than not describing the relationship at all.
- Vanilla must be able to simply not install a package, rather than have Kedra
  defaults applied and then reverted.
- Planning is side-effect free; only the executor writes, and only what a plan
  authorized. The executor never re-decides ownership.
- State never claims a backend write that did not succeed.
- State mutation is serialized by an advisory lock and saved atomically.
- Resolver and planner output is deterministic — always sorted by ID.
- Tooling never touches `$HOME` implicitly; paths are always explicit.
- Every manifest change must keep `validate-experience.py` and the test suite
  passing.
