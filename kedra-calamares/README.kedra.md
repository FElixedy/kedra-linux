# kedra-calamares

The Kedra Linux system installer: a **downstream of
[cachyos-calamares](https://github.com/CachyOS/cachyos-calamares)**, kept as a
small, readable delta so upstream changes can keep being merged.

Upstream's own `README.md` is Calamares' and is left in place. This file
documents only what Kedra changes and why.

## Upstream baseline

| | |
|---|---|
| Upstream | `https://github.com/CachyOS/cachyos-calamares` (git remote `upstream`) |
| Branch | `cachyos-dev` |
| Baseline commit | `ce54421cf0a08008ccda59a4e5f2f4747ae762be` |
| Baseline subject | `netinstall: fix correct mangowm path` |
| Baseline date | 2026-08-13 |
| Tag | `upstream-baseline` |
| Packaging baseline | `CachyOS/CachyOS-PKGBUILDS` @ `8630282`, `cachyos-calamares-next 3.4.2-13` |

`cachyos-dev` is the branch the shipping installer is actually built from —
verified against the `source=` line of the PKGBUILD that produces
`cachyos-calamares-next`, not assumed. Full upstream history is present, so
syncing is an ordinary `git fetch upstream && git merge upstream/cachyos-dev`.

Kedra work lives on the `kedra` branch.

## Building

```sh
# build in a throwaway build root, then sign + publish into [kedra]
packaging/kedra-calamares-buildroot --publish
```

The PKGBUILD packages a git clone of this working tree's `kedra` branch, so
**commit before building** — uncommitted changes are not in the package, and the
script warns when the tree is dirty.

### No root, no host changes

`makepkg --syncdeps` installs build dependencies into the host, which needs root
and permanently changes the machine. This workstream has refused that trade
before (`kedra-iso` deleted upstream's `sudo sed -i /usr/bin/mkarchiso` for the
same reason), and archiso already builds the ISO rootlessly, so the package is
built the same way: a complete pacman root under `~/Build/kedra-buildroot`,
populated and entered inside `unshare -r --map-auto`, with the source tree
bind-mounted in read-only and `makepkg` running as a mapped unprivileged uid.

Beyond avoiding root, this buys two things worth having:

- the build sees **only its declared dependencies**, so a missing entry in
  `depends`/`makedepends` fails here instead of working by accident on one
  developer's machine;
- the build root is populated from the **baseline** Arch/CachyOS repositories, not
  this host's `cachyos-*-znver4` ones.

Signature verification is fully enabled in the build root, against the same
build-local keyring `kedra-iso` uses. The host's pacman database, keyring and
`/etc/makepkg.conf` are never touched; its package cache is only ever read.

### Build flags are not the host's

`packaging/makepkg.kedra.conf` pins the **x86-64 baseline**. This is not
housekeeping: the development host is a CachyOS machine whose `/etc/makepkg.conf`
sets `-march=native`, and a znver4 build of this package would `SIGILL` on every
older CPU — including the QEMU guests it is tested in. The failure would appear
as "the installer crashes" on hardware the builder does not own.

Because that failure mode is invisible to the builder, the **artifact** is checked
rather than the configuration: every ELF object in the finished package is
disassembled and rejected if it contains AVX/AVX2/AVX-512/BMI instructions or
`ymm`/`zmm` registers, none of which exist in the baseline. Trusting that
`MAKEPKG_CONF` was honoured is precisely the assumption that would hide the bug.

CachyOS's CPU-optimised builds stay upstream, selected per machine at runtime by
`cachyos-rate-mirrors`. Kedra does not fork or compete with them.

> Note: upstream Calamares' `.gitignore` contains `*-build`, which silently
> ignores plausible script names in `packaging/`. `git check-ignore -v` before
> assuming a new file was committed.

## Package

| | |
|---|---|
| Name | `kedra-calamares` |
| Replaces on the ISO | `cachyos-calamares-next` |
| `provides` | `calamares` |
| `conflicts` | `calamares` |
| `replaces` | *none, deliberately* |

`provides`/`conflicts` are upstream's own values and they are load-bearing: every
CachyOS Calamares variant declares the same pair, so `kedra-calamares` and
`cachyos-calamares-next` conflict with each other through the virtual `calamares`
name. That is correct — they own the same `/usr/bin/calamares` and the same
`/etc/calamares` tree.

There is **no** `replaces=('cachyos-calamares-next')`. Nothing depends on the
CachyOS installer by name, so there is no dependency for it to satisfy; declaring
one would mean any CachyOS machine with `[kedra]` configured had its installer
swapped by a routine `pacman -Syu`. Kedra replaces `cachyos-hooks` that way
because Kedra must own the system identity. It has no such claim here.

## What Kedra changes

### Product identity

The branding component is `src/branding/kedra`, replacing `src/branding/cachyos`.
Product strings, the window title, the sidebar and `bootloaderEntryName` are
Kedra's. `bootloaderEntryName` is doing real work: the grubcfg module writes it
verbatim as `GRUB_DISTRIBUTOR`, so the installed boot menu says *Kedra Linux*
with no code change.

**The CachyOS artwork was deleted rather than reconfigured.** Three sets of images
said "CachyOS" in the pixels, where no configuration can reach:

- the six branding slides (`slide1.jxl` … — the first reads *"WELCOME TO
  CACHYOS!"*),
- `src/images/*.webp|jxl`, the desktop chooser screenshots — the GNOME one opens
  with a *"Welcome to CachyOS!"* window filling the frame,
- `src/images/bootloaders/*`, photographs of CachyOS boot menus.

In their place: a **text-only** slideshow and generated wordmark images
(`packaging/kedra-branding-images`). This is explicitly placeholder, not final
visual branding — and it is honest, which the alternative was not. The
`screenshot:` keys were removed with the images; the choosers degrade to name +
description. Re-adding Kedra artwork later is restoring those keys and swapping
three PNGs.

`uploadServer` (upstream: `paste.cachyos.org`) is removed. Kedra installation logs
are not CachyOS's to host and there is no Kedra endpoint yet, so the affordance is
off rather than pointing somewhere wrong.

No Kedra URL appears anywhere. No Kedra domain has been chosen.

### Repositories in the installed system

This is the substantive change. Upstream's installer copies the **ISO's**
`/etc/pacman-more.conf` into the target as `/etc/pacman.conf`. That file has no
`[kedra]` entry, so a Kedra target could not resolve `kedra-hooks` at all —
neither during `pacstrap` nor after reboot.

Which repositories an installed Kedra system has is an installer decision, so the
file now lives here: `src/kedra/pacman.conf` → `/etc/calamares/kedra/pacman.conf`,
copied to the target by `shellprocess-before-online`. Ordering is the accepted
`[kedra] → [cachyos] → [core] → [extra] → [multilib]`, and `detect-architecture`
still inserts the CachyOS v3/v4/znver4 repositories above `[cachyos]` unchanged.

Because no Kedra hosting exists, `src/scripts/kedra-target-repository` copies the
signed `[kedra]` snapshot off the live medium onto the **installed disk** at
`/var/lib/kedra/dev-repo`. A `file://` path that existed only in the live
environment would be a dead repository the moment the machine rebooted.

- Signatures are verified normally. `SigLevel = Never` appears nowhere.
- A positive allowlist refuses to copy anything that is not repository content, so
  key material cannot ride along.
- `/var/lib`, and "dev-repo" in the name: this is development infrastructure and
  does not pretend to be package-managed.

> **Moving to real hosting**: replace the `Server =` line in
> `src/kedra/pacman.conf` with `https://<kedra-host>/$repo/os/$arch`. The script
> already handles this — if the live medium's `[kedra]` Server is not `file://`,
> it copies nothing, writes that URL into the target, and says so. It can then be
> deleted at leisure. The snapshot layout is already the HTTPS layout.

### Trust in the target

Trust reaches the target two ways, both preserved and both checked:

1. `shellprocess_initialize_pacman` copies the live `/etc/pacman.d/gnupg` wholesale.
2. `kedra-keyring`'s install scriptlet runs `pacman-key --populate kedra` inside
   the target — which needs `/proc` mounted there. Calamares' `mount` module
   provides it.

`src/scripts/create-pacman-keyring` was reordered to **populate before the first
`pacman -Sy`**, and `shellprocess_initialize_pacman` now runs it *before*
`update-mirrorlist`. Upstream does both the other way round, which assumes a sync
can happen without trust. That stops being true the moment a signed repository
database is in the configuration: `DatabaseOptional` tolerates a *missing*
signature, but still verifies one that is present, so an unpopulated keyring
aborts the sync outright. This is the same class of bug the ISO workstream fixed
in `calamares-online.sh`; it is fixed here as well because this script is the
installer's own trust bootstrap and has to be correct on its own.

### It fails loudly

`src/scripts/kedra-verify-target` runs chrooted, last in the sequence, and aborts
the installation if the target is not a valid Kedra system. Every condition it
checks for is otherwise **silent** — `kedra-keyring`'s scriptlet guards itself and
does nothing when `pacman-key` cannot run; a dangling `Include =` quietly drops a
repository; `pacman -Q cachyos-hooks` succeeds on a *correct* Kedra system via
`provides`, so only the local database distinguishes it. A loud installer failure
is recoverable; an installed system that cannot verify its own packages is not.

### The Kedra Experience enters here

Three modules, and between them they contain no Experience knowledge at all.

| Module | Type | Where in the sequence | What it does |
|---|---|---|---|
| `kedraexperience` | C++ view | `show`, after `packagechooser@desktop` | runs `kedra-experience profiles`, shows what comes back, publishes the chosen selection to GlobalStorage |
| `kedraexperiencepackages` | Python job | `exec`, before `packages@online` | runs `kedra-experience resolve`, appends `packages.required` to `packageOperations` |
| `kedraexperiencestate` | Python job | `exec`, after `shellprocess` | runs `kedra-experience install-state` for the created user |

The rule they exist to keep: **Calamares knows profile identifiers; the resolver
knows everything else.** No module list, no package list, no GNOME setting and
no application contract appears anywhere in this repository. Grep for
`kedra-experience-workflow` — the only Kedra package name in the installer is
the resolver's own path, which is configuration.

That matters because the alternative is a copy. A package list here would be a
second answer to "what is the Kedra Experience", and the two would diverge
silently, on installed systems, where nobody is looking. The manifests that
`kedra-experience` installs are the only answer.

`kedraexperience` reads `profiles` because it must draw a checklist, and it
honours `user_selectable` from that data rather than inferring it from anything:
`integration` is never offered, and `desktop` is, because the metadata says so.
A Custom profile is stored as deltas — a module left at the preset's own answer
contributes no override — so it stays a pointer plus changes rather than a
snapshot that goes stale when Kedra adds a module.

Every failure fails closed. A missing resolver, malformed JSON, a document
version this installer was not written against, a non-zero exit, a profile the
resolver calls invalid, a malformed package list: each one either leaves the
page unable to be left or aborts the installation with a message. There is no
path from a failed Kedra resolution to a Vanilla install or to a partial package
set — a user who asked for Kedra either gets it or is told why not.

The live ISO carries `kedra-experience` and `kedra-experience-runtime` so the
resolver can run. It deliberately does **not** carry
`kedra-experience-desktop` or `kedra-experience-workflow`: those are the GSettings
vendor overrides, and installing them to make the installer work would give the
live session Kedra's workspace model and keymap before the user has chosen
anything. The live session stays upstream GNOME.

### GNOME-first

`packagechooser_desktop.conf` preselects GNOME. The other desktops are untouched —
GNOME-first is a default, not a restriction.

The GNOME group is the **minimum working set**, matching the accepted live ISO so
live and installed GNOME agree. Notably `cachyos-gnome-settings` is **not**
inherited: despite the name it ships one dconf drop-in and depends on
`adw-gtk-theme`, while everything GNOME needs to actually run (`gnome-session`,
`gnome-settings-daemon`) is already a hard dependency of `gnome-shell`. What it
carries is appearance — the deferred Kedra Experience. No `kedra-gnome-settings`
was created, because nothing about installation needs one.

Upstream's list is also not self-sufficient: it ships no file manager. `nautilus`,
`gvfs`, `xdg-desktop-portal-gnome` and `xorg-xwayland` were added.

### The CachyOS layer is kept

`linux-cachyos` + `-lts`, the CachyOS repositories and mirror infrastructure,
`cachyos-rate-mirrors` and its v3/v4/znver4 runtime selection, `cachyos-settings`,
`cachyos-keyring`, `chwd` hardware detection, microcode, firmware, the Btrfs
subvolume layout and snapshot integration are all upstream's, unchanged.
`kedra-verify-target` asserts several of them, so losing one to a future branding
change fails the build rather than shipping quietly.

Only **artwork** packages were dropped: `cachyos-grub-theme` (draws the CachyOS
logo over the installed system's boot menu), `cachyos-plymouth-theme` and
`cachyos-plymouth-bootanimation` (the boot splash — and the bootloader module adds
`splash` to the kernel command line whenever plymouth is installed, so this was
displayed on every boot), `cachyos-wallpapers`, and `cachyos-hello`, a "Welcome to
CachyOS" application. Plymouth itself is kept, on the stock `bgrt` theme.

### Package selection is no longer fetched from CachyOS

`netinstall.conf` listed CachyOS's `netinstall.yaml` on
`raw.githubusercontent.com` **first**, with the locally installed copy only as a
fallback if the fetch failed. Inherited unchanged, every Kedra installation with
working network would have installed CachyOS's package selection, and the file
shipped in this package would only be used when GitHub was unreachable — the one
case nobody tests. The remote source is removed.

### Bootloader

GRUB is the canonical Milestone 0 bootloader (upstream's default is limine). All
five options are kept and no bootloader code was touched. GRUB names itself from
`GRUB_DISTRIBUTOR`, so it presents as Kedra with no patch; limine's menu group is
a hardcoded `/+CachyOS` string in `bootloader/main.py` and its splash comes from
`cachyos-wallpapers`. `efiBootloaderId` is `kedra`.

### An upstream bug, fixed

`src/scripts/dmcheck` used `pacman -Qs <pkg>` — a **substring search over names
and descriptions**. For `ly` that always matches on a normal desktop install
(`automake`, `baobab`, …), so upstream's script took the `ly` branch on *every*
installation: `getty@tty2` removed, and a dangling symlink to a non-existent
`ly@.service` left in `multi-user.target.wants`, which systemd then reports as a
not-found unit on every boot of a system that never had `ly`. Replaced with
`pacman -Qq -- <pkg>`, which matches the name exactly and still resolves
`provides`.

## Intentionally unchanged

Technical names that contain "cachyos" and are **correct as written**:

- `linux-cachyos`, `linux-cachyos-lts` and `/boot/vmlinuz-linux-cachyos*` —
  CachyOS kernel package and file names
- `[cachyos]`, `cachyos-mirrorlist`, `cachyos-v3/v4-mirrorlist`,
  `cachyos-rate-mirrors` — the repositories Kedra depends on
- `cachyos-settings`, `cachyos-micro-settings`, `cachyos-keyring`, `chwd`
- `cachyos-fish-config` / `cachyos-zsh-config` — their shell greeting is
  `fastfetch`, which reads `/etc/os-release` and therefore says Kedra
- `zfs.conf`'s `poolName: zpcachyos` — ZFS is not the Kedra default path and
  renaming a pool convention is not this workstream's call
- `welcome_online.conf`'s `internetCheckUrl` — never shown to the user, and what a
  Kedra online install genuinely needs to reach *is* CachyOS infrastructure
- `netinstall.yaml`'s hidden "CachyOS kernels" group names, which describe
  precisely what they install

## Branding debt found on the installed system

Traced during Milestone 0 acceptance. Neither is an installer defect and neither
indicates a wrong package was installed — both need Kedra *assets* plus a Kedra
system-defaults package, which is the deferred branding workstream.

**GDM login screen shows the CachyOS logo.**
`cachyos-settings` ships
`/usr/share/glib-2.0/schemas/zz_cachyos.org.gnome.login-screen.gschema.override`,
which sets `[org.gnome.login-screen] logo='/usr/share/icons/cachyos.svg'` — and
ships that SVG too. The `zz_` prefix makes it sort after Arch's own
`30_org.archlinux.gdm.gschema.override`, so it wins. This is *inherited settings
behaviour from a package Kedra deliberately retains*: `cachyos-settings` is the
performance layer (zram, ananicy-cpp rules, sysctl), and it happens to carry one
branding override too. It is **not** a `LOGO=kedra` problem — `LOGO=` in
os-release does not feed GDM's logo, and no Kedra icon exists yet.
*Fix:* a Kedra override sorting after `zz_cachyos` plus a Kedra logo asset.

**Boot splash shows the Arch Linux watermark.**
`plymouth-set-default-theme` correctly reports `bgrt` (the theme this package
configures). The `bgrt` theme falls back to the spinner watermark
`/usr/share/plymouth/themes/spinner/watermark.png`, owned by the stock
**`plymouth`** package — upstream Arch artwork. This is *stock upstream package
artwork plus a missing Kedra asset*, not a configuration bug: the previous
CachyOS boot animation was removed on purpose and there is no Kedra one yet.
*Fix:* ship a Kedra watermark.

## Known consequences

- `PackageModel.cpp` guards against selecting two desktop *settings* packages at
  once by counting names from a hardcoded `cachyos-*` list. With
  `cachyos-gnome-settings` no longer in the GNOME group, that guard no longer
  counts GNOME. Generic C++ internals were left alone; the canonical path is a
  single desktop.
- The ISO's `/etc/pacman-more.conf` is now unused by the online path and can be
  dropped from `kedra-iso` in a later cleanup.
- `usr/local/bin/calamares-online.sh` still logs to
  `/home/liveuser/cachy-install.log` (ISO-owned).
