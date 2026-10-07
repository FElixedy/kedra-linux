# How a Kedra system comes to trust `[kedra]`

This is the question the ISO and Calamares workstreams need answered concretely,
so it is answered concretely, with the pacman behaviour that was actually
observed rather than assumed.

## The problem, stated exactly

`[kedra]` is configured with `SigLevel = Required`. Packages from it are only
installable if the signing key is present **and fully valid** in the target's
pacman keyring. That trust arrives in the `kedra-keyring` package. But
`kedra-keyring` lives in `[kedra]`.

It is worse than a package-level cycle. `kedra.db` is signed, and pacman
verifies a database signature that is *present* even under
`DatabaseOptional` — `Optional` only tolerates a **missing** signature. So a
system without Kedra trust cannot even run `pacman -Sy`:

```
error: kedra: key "…" is unknown
:: Import PGP key …? [Y/n] error: key "…" could not be looked up remotely
error: failed to synchronize all databases (unexpected error)
```

(`tests/run-tests.sh` asserts this, so the constraint cannot silently regress.)

## Mechanisms that were considered and rejected

| Mechanism | Why not |
|---|---|
| `SigLevel = Never` on `[kedra]` | Explicitly out of scope. It removes the property the repository exists to have. |
| `pacman-key --recv-keys` + `--lsign-key` in the installer | Trust-on-first-use over the network. A keyserver is not an authenticated channel; anyone able to intercept the fetch chooses the key. |
| `pacman -U` of a downloaded `kedra-keyring` package | `LocalFileSigLevel` must then be relaxed, and the download is unauthenticated anyway. Same cycle, moved. |
| Shipping the key inside `kedra-hooks` | Makes an identity package a trust root, and still needs a trusted first delivery. |

None of them solve anything, because none of them add an *authenticated
channel*. The cycle can only be broken outside pacman.

## The mechanism Kedra uses

**Trust originates from the ISO image, whose authenticity is established
out-of-band** (published checksum + detached signature of the ISO). This is
exactly how Arch and CachyOS bootstrap their own keyrings, so it needs no new
machinery.

```
Kedra ISO image  (authenticity established out-of-band: signed checksum)
        │
        │  kedra-keyring is in the ISO's package list, so the live system's
        │  /etc/pacman.d/gnupg already trusts Kedra at image build time
        ▼
Live installer environment  ──────────────┐
        │                                 │ verifies packages against the
        │ pacstrap / Calamares            │ LIVE keyring
        ▼                                 │
Target root  ◄────────────────────────────┘
        │
        │  kedra-keyring's install scriptlet runs chrooted in the target:
        │      pacman-key --populate kedra
        ▼
Target trusts Kedra independently; [kedra] is usable from first boot
```

At no point is a signature check disabled, and no key is trusted that was not
carried by the signed ISO.

### The exact sequence for the ISO workstream

1. **ISO build host** — bootstrap once per builder/CI runner. Trust comes from
   a human reviewing the public keyring committed to `kedra-keyring`:
   ```
   pacman-key --populate-from /path/to/kedra-keyring --populate kedra
   ```
   After that the builder can install `kedra-keyring` from `[kedra]` normally
   and the bootstrap is never repeated.

2. **ISO image** — add `kedra-keyring` to the archiso `packages.x86_64` list.
   Its scriptlet populates the live system's keyring during image build. Add
   `[kedra]` to the ISO's `pacman.conf` at the top (see
   `config/pacman.conf.kedra-section`).

3. **Installation** — `kedra-keyring` must be in the package set pacstrap /
   Calamares installs into the target, alongside `archlinux-keyring` and
   `cachyos-keyring`. Package verification uses the live keyring; the scriptlet
   then populates the target keyring.

4. **Installed system** — `[kedra]` in the target's `/etc/pacman.conf`. Nothing
   further is required; `pacman -Syu` works from first boot.

5. **Key rotation later** — a new packager key is distributed as an ordinary
   `kedra-keyring` update through `[kedra]`, signed by a key the system already
   trusts. Only a compromise of the master key would require a new ISO.

## Two traps found while testing this

Both were found by making the test suite install into a *real* pacman root
rather than a stub, and both will bite the installer workstream if ignored.

1. **The target root needs `/proc` mounted when the scriptlet runs.**
   `kedra-keyring.install` guards on `pacman-key -l`, and `pacman-key` uses
   process substitution internally. Without `/proc` in the chroot the guard
   fails, the scriptlet does nothing, and — because the guard is
   `>/dev/null 2>&1`, following `archlinux-keyring`'s own convention — **it
   fails silently**. Trust is simply absent, and the failure only surfaces
   later as "key is unknown". pacstrap and Calamares do mount `/proc`, `/sys`
   and `/dev`; anything custom must too.

2. **The target's `/etc/pacman.conf` `Include` files must exist inside the
   target.** `pacman-key` parses `/etc/pacman.conf` from within the chroot. A
   dangling `Include = /etc/pacman.d/kedra-mirrorlist` makes it exit non-zero,
   which trips the same silent guard. `kedra-mirrorlist` therefore has to be in
   place in the target before or with `kedra-keyring`.

A cheap post-install assertion for Calamares, worth adding for exactly this
reason:

```sh
pacman-key --gpgdir "$TARGET/etc/pacman.d/gnupg" --list-keys --with-colons \
  | grep -q 'Kedra Linux' || fail "Kedra trust was not established in the target"
```
