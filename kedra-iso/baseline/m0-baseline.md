# Kedra Linux -- Milestone 0 baseline

Tag: `m0`  ·  Generated: 2026-08-25T23:47:53+03:00

The exact sources, artifacts and environment that were built and accepted
together as Milestone 0, after the M0.1 provenance and trust hardening.
Machine-readable form: `m0-baseline.json`.

Contains no private key material.

## Source repositories

| Repository | Commit | Branch | Date | Head subject |
|---|---|---|---|---|
| `kedra-hooks` | `e882882017d0a919160bae2cab24346c25d9055e` | `master` | 2026-08-14T19:27:54+03:00 | kedra: drop inherited privacy policy URL, provide cachyos-hooks |
| `kedra-keyring` | `e6e59828b46b85865332944cb1e863e453e1e14b` | `main` | 2026-08-14T20:08:51+03:00 | kedra-keyring: add the Kedra PGP trust root package |
| `kedra-repo` | `41379f2a86c66a46735c8515b6f0b157a53555c4` | `main` | 2026-08-15T13:17:59+03:00 | kedra-repo: allow kedra-calamares into [kedra] |
| `kedra-calamares` | `1381d5fc3a9e04f923f8ff1b91bb47bbeb7fe1c7` | `kedra` | 2026-08-25T18:44:13+03:00 | kedra-calamares: always populate the build root from empty |
| `kedra-iso` | `bd3d715aee465e3e50565cbf329c87d61f9eca88` | `kedra` | 2026-08-25T23:47:39+03:00 | kedra-iso: do not let the manifest's own output block the dirty check |

## Upstream baselines

| Upstream | Commit | Source |
|---|---|---|
| CachyOS Live ISO | `4937780541814b4b0ec7c212c00fe06299f799b6` | https://github.com/CachyOS/CachyOS-Live-ISO.git |
| cachyos-calamares | `ce54421cf0a08008ccda59a4e5f2f4747ae762be` | https://github.com/CachyOS/cachyos-calamares.git |

## Packages in the baseline

As named by `kedra.db` -- superseded releases remain in the docroot but are not part of the baseline.

| Package | Version | File | Size | SHA-256 |
|---|---|---|---|---|
| `kedra-calamares` | `3.4.2-2` | `kedra-calamares-3.4.2-2-x86_64.pkg.tar.zst` | 4092280 | `7743029ac50bddd8a5faa21941caaa13e28f39e75aae11a0814534f1650e636d` |
| `kedra-hooks` | `2026.08.14-1` | `kedra-hooks-2026.08.14-1-any.pkg.tar.zst` | 17521 | `652e7ea7faea8f86829277ddd56a69d42d6e21bd94f59352af331e5231231a03` |
| `kedra-keyring` | `2026.08.14-1` | `kedra-keyring-2026.08.14-1-any.pkg.tar.zst` | 17890 | `628445a240815ac651142dc77232b9ade7fd32a21873b8e3c1ccd8a98f60c0a9` |

Every package above has a detached `.sig` alongside it:

| Signature | SHA-256 |
|---|---|
| `kedra-calamares-3.4.2-2-x86_64.pkg.tar.zst.sig` | `864082147acc21a1db4a19d80ada7d864ba903335450c610f98b9636c17e46e3` |
| `kedra-hooks-2026.08.14-1-any.pkg.tar.zst.sig` | `4c86df70a34001b9826e2605270147f24e1e04b104b72444a97f8ee993db1ca7` |
| `kedra-keyring-2026.08.14-1-any.pkg.tar.zst.sig` | `71752e42b5bb6d65b6bf33a214b1302517ec98e87cea7a3c18c4f82288771373` |

## Repository

| Object | SHA-256 |
|---|---|
| `kedra.db.tar.zst` | `cff3348e3f29c9bdb57938a416eca492a8994aa593bb46936532bd6862a7b97a` |
| `kedra.db.tar.zst.sig` | `b8b70a4bf666279f85389c609803f36532b4bf23d597155db2e2465d55e3a06c` |
| `kedra.files.tar.zst` | `a9b1f38e3a6bccd1998e11b1fb4a4c26683656cc9320ea155c49b28feac41dbe` |
| `kedra.files.tar.zst.sig` | `a8bbb085d3e0caf5c495833c283eeb2586d7ad604f175611dc7e7f74564dc9f7` |

Signing key fingerprint: `F4C6894B5CA35D7E58ADF182885AD0B1CE1A0EB5`

**DEVELOPMENT key.** Not a production Kedra identity; replacing it is Alpha work.

## ISO

| Field | Value |
|---|---|
| Filename | `kedra-desktop-linux-260825.iso` |
| SHA-256 | `fd71210a520fd7bbe7519b354f7079471081b5bc1912541c63ae84a566b22596` |
| Size | 2979217408 bytes (2,8G) |
| Built | 2026-08-25T19:01:26+03:00 |
| archiso | `89-1` |
| Built from | `e6cfd7148719614eecf67b4bc6f716b3fa038314` |
| Provenance | built from kedra-iso e6cfd7148719614eecf67b4bc6f716b3fa038314; HEAD differs only in files that never enter the image (6 later commit(s)) |

## Acceptance environment

| Field | Value |
|---|---|
| Hypervisor | QEMU 11.1.0 with KVM |
| Firmware | OVMF / UEFI (edk2-ovmf 202605-1) |
| VM architecture | x86_64, q35, `-cpu host` |
| Disk | single blank raw virtio-blk image |
| Filesystem | Btrfs |
| Desktop | GNOME (GDM) |
| Bootloader | GRUB |
| pacman | `7.1.0.r9.g54d9411-4` |

## Acceptance results

| Criterion | Result |
|---|---|
| ISO build | **PASS** |
| UEFI live boot | **PASS** |
| GNOME live session | **PASS** |
| installer launch (as Kedra) | **PASS** |
| real installation | **PASS** |
| Btrfs | **PASS** |
| ISO-detached cold boot | **PASS** |
| Kedra identity (ID=kedra) | **PASS** |
| GRUB label correct (no doubled name) | **PASS** |
| EFI entry named kedra | **PASS** |
| signed repository operation | **PASS** |
| strict signature policy (no Optional TrustAll) | **PASS** |
| full pacman -Syu (exit 0) | **PASS** |
| post-update reboot | **PASS** |
| zero failed units | **PASS** |
| no real cachyos-hooks package | **PASS** |
