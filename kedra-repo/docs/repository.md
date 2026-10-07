# The `[kedra]` repository

## One repository, no CPU variants

Kedra publishes exactly one binary repository: `[kedra]`. There is no
`kedra-v3`, `kedra-v4` or `kedra-znver4`, and there should not be. CachyOS owns
optimised and CPU-specific packages, and Kedra reuses them unchanged. A Kedra
repository exists only for packages Kedra itself owns or deliberately overrides.

## On-disk layout

```
$KEDRA_REPO_ROOT/                        docroot -- becomes the HTTPS root
└── kedra/
    └── os/
        └── x86_64/
            ├── kedra.db -> kedra.db.tar.zst
            ├── kedra.db.tar.zst
            ├── kedra.db.tar.zst.sig
            ├── kedra.files -> kedra.files.tar.zst
            ├── kedra.files.tar.zst
            ├── kedra.files.tar.zst.sig
            ├── <pkg>-<ver>-<arch>.pkg.tar.zst
            └── <pkg>-<ver>-<arch>.pkg.tar.zst.sig
```

Consumed as `Server = <base>/$repo/os/$arch`, which is the Arch layout. `any`
packages live in the `x86_64` directory, as they do upstream.

Publishing over HTTPS later is an `rsync` of `$KEDRA_REPO_ROOT` to a docroot
and a `Server` line change in `kedra-mirrorlist`. No part of the repository
format, the signing pipeline or the client configuration changes. Repository
generation has no dependency on ISO generation and never should.

## Pipeline

```
package source (kedra-hooks, kedra-keyring, …)
      │  makepkg           -- ordinary Arch build, PKGDEST=$KEDRA_STAGING_DIR
      ▼
staged package file
      │  kedra-repo-publish
      │    1. allowlist check      (config/kedra-repo-packages.txt)
      │    2. gpg --detach-sign    (packager key)
      │    3. copy into docroot + verify the signature that was written
      │    4. repo-add --sign      (updates and signs kedra.db / kedra.files)
      ▼
[kedra] docroot
      ▼
pacman client
```

`repo-add` is stock pacman tooling; the only custom code is the ~60-line
`kedra-repo-publish` wrapper that adds the allowlist gate and pins the signing
key. Deliberately not built: any repository manager, web service, dashboard or
API.

## Repository ordering

Pacman resolves a package name from the **first** repository that contains it,
so repository order is the mechanism by which Kedra takes ownership of a name.

```
[kedra]                     Kedra-owned and Kedra-replacement packages
[cachyos-znver4]            CachyOS optimised repos, on capable hardware
[cachyos-core-znver4]
[cachyos-extra-znver4]
[cachyos]                   CachyOS packages, kernels, tooling
[core]                      Arch
[extra]
[multilib]
```

This ordering is what lets `kedra-hooks` replace `cachyos-hooks` while CachyOS
continues to provide kernels and optimised builds — verified end to end in
`tests/run-tests.sh` sections 7 and 8.

Note that `replaces=` is honoured by `pacman -Syu` regardless of order; order
matters for *name* resolution. Putting `[kedra]` first is the honest statement
of ownership, and it is also what keeps a future Kedra override of a CachyOS
package from being shadowed.

### The hazard, and the policy that contains it

Top priority means any name published to `[kedra]` silently overrides the
Arch/CachyOS build of that name — `glibc` and `linux` included. Pacman offers no
mechanism to scope a repository to a set of names, so the containment is a
publishing-side policy:

**`kedra-repo-publish` refuses to add a package whose `pkgname` is not listed in
`config/kedra-repo-packages.txt`.** Adding a name to that file is the explicit,
reviewable act of claiming it. A name qualifies only if it is a Kedra-original
package (`kedra-*`) or a deliberate, documented replacement of an upstream
package whose divergence Kedra actually requires. Kernels, CachyOS optimised
builds and CachyOS performance packages are out of scope by design.

## Client configuration

`config/pacman.conf.kedra-section` is the `[kedra]` stanza with its intended
placement; `config/kedra-mirrorlist.template` is the mirrorlist, using
`@KEDRA_REPO_ROOT@` because no production Kedra domain exists yet and inventing
one would be worse than leaving it unset.

`SigLevel = Required DatabaseOptional` matches Arch and CachyOS. `Required`
makes a valid, trusted package signature mandatory. `DatabaseOptional` tolerates
a *missing* database signature but still verifies one that is present — and
`kedra.db` is always signed, so in practice the database is verified. It can be
tightened to `DatabaseRequired` once every Kedra system is guaranteed to carry
`kedra-keyring` before its first `pacman -Sy`; see `bootstrap.md`.
