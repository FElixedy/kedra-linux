# kedra-iso

The Kedra Linux live/install ISO profile: a **downstream of
[CachyOS-Live-ISO](https://github.com/CachyOS/CachyOS-Live-ISO)**, kept as a
small, readable delta so upstream changes can keep being merged.

Upstream's own `README.md` still describes the CachyOS build and is left in
place. This file documents only what Kedra changes and why.

## Upstream baseline

| | |
|---|---|
| Upstream | `https://github.com/CachyOS/CachyOS-Live-ISO` (git remote `upstream`) |
| Baseline commit | `4937780541814b4b0ec7c212c00fe06299f799b6` |
| Baseline subject | `dmcheck: targeting noctalia-greeter instead of greetd (#114)` |
| Baseline date | 2026-08-11 |
| Tag | `upstream-baseline` |

Full upstream history is present, so syncing is an ordinary
`git fetch upstream && git merge upstream/master`.

## Building

```sh
# once per builder: create the build-local pacman keyring (no root, no host changes)
fakeroot ./bin/kedra-iso-keyring-init

# fast static check: identity, repo ordering, package resolution, trust (~1 min)
./bin/kedra-iso-validate

# the real build (mkarchiso needs root)
./buildiso.sh -p desktop
```

Output lands in `out/desktop/kedra-desktop-linux-<YYMMDD>.iso`.

### Requirements

`archiso`, `mkinitcpio-archiso`, `squashfs-tools`, `fakeroot`, plus a published
`[kedra]` docroot (see `kedra-repo/bin/kedra-repo-publish`).

### Build-host state this depends on

Deliberately explicit, because upstream leaves some of it implicit:

| Path | Meaning | Overridable with |
|---|---|---|
| `~/Build/kedra-repo` | signed `[kedra]` docroot | `KEDRA_REPO_ROOT` |
| `../kedra-keyring` | reviewed Kedra public key material | `KEDRA_KEYRING_SRC` |
| `./keyring` | build-local pacman keyring (gitignored) | `KEDRA_ISO_GPGDIR` |

Everything else comes from the network at build time: the Arch, CachyOS and
`[kedra]` repositories, and `cachyos-mirrorlist`, which upstream's
`fetch_cachyos_mirrorlist()` downloads from GitHub on every build.

## What Kedra changes

### Identity

`kedra-hooks` is the **single** owner of the distro identity. Upstream shipped a
hardcoded `airootfs/etc/os-release` (and `/etc/cachyos-release`); both are
deleted here. `mkarchiso` copies `airootfs/` *before* running `pacstrap`, so the
`kedra-branding` libalpm hook gets the last word and writes
`NAME="Kedra Linux" / ID=kedra` into the image during package installation. The
profile never hardcodes the file.

ISO metadata (`profiledef.sh`), the GRUB/syslinux/systemd-boot menus, the MOTD
and the hostname are Kedra-facing. Kernel image paths (`vmlinuz-linux-cachyos*`)
are **not** renamed — they are CachyOS kernel package filenames.

No Kedra URL appears anywhere. No Kedra domain has been chosen, and a
plausible-looking address that does not exist is worse than none.

### GNOME-first live session

The Plasma live desktop is replaced by a minimal GNOME one: `gdm`,
`gnome-shell`, `gnome-control-center`, `gnome-console`, `nautilus`,
`gnome-text-editor`, `gnome-disk-utility`, `gnome-system-monitor`,
`gnome-keyring`, `gnome-backgrounds`, `gvfs`, `xdg-desktop-portal-gnome`,
`xorg-xwayland`, `qt6-wayland`.

This is deliberately **not** the `gnome` package group and deliberately not
Kedra Experience: no theming, no extensions, no wallpapers, no `gnome-software`.

`etc/gdm/custom.conf` replaces `etc/plasmalogin.conf` for live autologin, and
`etc/dconf/db/local.d/00-kedra-live-session` replaces upstream's
`kscreenlockerrc`/`powerdevilrc` — the live user has an **empty password**, so a
screen lock would be an unlockable dead end.

### Repository integration

Build-time and live ordering are both:

```
[kedra] → [cachyos] → [core] → [extra] → [multilib]
```

`[kedra]` sits first because that is the mechanism by which `kedra-hooks`
replaces `cachyos-hooks`. The hazard of top priority is contained *outside*
pacman by `kedra-repo`'s publishing allowlist, not by ordering tricks here.
CachyOS's CPU-optimised repository selection (`v3`/`v4`/`znver4`) is untouched:
it is a *runtime* mechanism owned by `cachyos-rate-mirrors` and the
`cachyos-*-mirrorlist` packages, which are all still installed.

`Server =`, not `Include =`, and **no `kedra-mirrorlist` package**. Kedra has
exactly one repository, and `pacman-conf` resolves `Include =` against the
*build host* — a dangling include silently breaks keyring bootstrap
(`kedra-repo/docs/bootstrap.md`, trap 2).

Because no Kedra hosting exists yet, the signed `[kedra]` docroot is copied into
the image at `/usr/share/kedra/repo`, so the live system resolves `[kedra]`
offline with signatures verified normally.

> **Moving to real hosting** is a two-line change: replace the `Server =` lines
> in `archiso/pacman.conf` and `archiso/airootfs/etc/pacman.conf` with
> `https://<kedra-host>/$repo/os/$arch`, then delete `prepare_kedra_build_tree()`
> and its call in `util-iso.sh`. Nothing else depends on the local docroot.

### Trust, without touching the build host

`mkarchiso` expands the profile `pacman.conf` with `pacman-conf` and strips
`RootDir`, `DBPath`, `CacheDir`, `HookDir` and `LogFile` — but **not** `GPGDir`.
Left at its default, `pacstrap` would verify every package against the build
host's `/etc/pacman.d/gnupg`, which would mean teaching the host keyring to
trust Kedra before an ISO could be built.

Kedra instead points `GPGDir` at a build-local keyring created by
`bin/kedra-iso-keyring-init` holding `archlinux` + `cachyos` + `kedra` trust.
Signature verification stays fully enabled; only the location of the trust store
moves. `SigLevel = Never` is never used.

The live keyring is populated twice over: at image build time by
`kedra-keyring`'s scriptlet, and again at boot by `pacman-init.service`, which
runs `pacman-key --populate` across every keyring in
`/usr/share/pacman/keyrings`.

### Host tooling is no longer modified

Upstream ran `sudo sed -i /usr/bin/mkarchiso` twice — once to strip
`archlinux-keyring-wkd-sync.timer` from the image, once to make the build
verbose. Both permanently rewrite a file owned by the host's `archiso` package:
the change survives the build, silently affects every other archiso profile on
the machine, and is reverted by the next `pacman -Syu` without warning.

Kedra removes `modify_mkarchiso()` entirely. The timer is masked from inside the
profile with a `/dev/null` symlink — the same mechanism upstream already uses for
`cachyos-rate-mirrors.timer`, and one that outranks whatever `systemctl preset`
enables during `pacstrap`. `mkarchiso -v` was already passed unconditionally, so
the verbosity patch bought nothing.

`buildiso.sh` still uses `sudo` for `mkarchiso`, `rm -rf` of the work directory
and `chown` of the output — that is inherent to archiso and is unchanged.

## Known deferred items

Owned by the **Calamares workstream**, not by this profile:

- `cachyos-calamares-next` is still the installer, so its window title, branding
  and installed-system identity still say CachyOS.
- `usr/local/bin/removeun-online` and `etc/pacman-more.conf` describe the
  *installed target*, not the live medium. They have no `[kedra]` entry, and
  adding a `file://` one would create a dangling repository in the target.
- `usr/local/bin/calamares-online.sh` logs to `/home/liveuser/cachy-install.log`.

Its keyring bootstrap **was** fixed here, because it is ISO-owned and was
actively destructive: it deleted `/etc/pacman.d/gnupg` and then ran `pacman -Sy`
*before* re-populating, which cannot work once a signed `kedra.db` is in the
configuration.
