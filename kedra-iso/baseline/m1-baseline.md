# Kedra Linux -- Milestone 1 baseline

Tag: `m1`  ·  Generated: 2026-09-04T14:43:32+03:00

Milestone 1 baseline: the Kedra Experience reaches an installed system. Records the exact sources, artifacts and environment built and accepted together after the M1 freeze hardening (batch-level repository immutability, Experience rerun reconciliation, strict resolver validation, and an assertive live GNOME isolation gate). Contains no secret key material.

Machine-readable form: `m1-baseline.json`.

Contains no private key material.

## Source repositories

| Repository | Commit | Branch | Date | Head subject |
|---|---|---|---|---|
| `kedra-hooks` | `e882882017d0a919160bae2cab24346c25d9055e` | `master` | 2026-08-14T19:27:54+03:00 | kedra: drop inherited privacy policy URL, provide cachyos-hooks |
| `kedra-keyring` | `e6e59828b46b85865332944cb1e863e453e1e14b` | `main` | 2026-08-14T20:08:51+03:00 | kedra-keyring: add the Kedra PGP trust root package |
| `kedra-repo` | `d4d2f6f9842686f5ee90f69865279126b18e12c6` | `main` | 2026-08-30T23:51:02+03:00 | kedra-repo: hold package identity immutable inside one batch too |
| `kedra-calamares` | `3957a075080f065dae9bf1105eae0ec2f2d39bb7` | `kedra` | 2026-08-30T23:51:12+03:00 | kedra-calamares: reconcile the Experience operation on every resolve |
| `kedra-packages` | `5667fbc61b6a30b5571c9ebcbd5e3925e0821cbb` | `master` | 2026-08-30T23:53:24+03:00 | Record the deferred temporary-file hardening debt |
| `kedra-iso` | `de8f6165d3763412d49f5ead65af3b5ab5838c21` | `kedra` | 2026-09-04T14:43:05+03:00 | kedra-iso: ignore the full-install acceptance run directories |

## Upstream baselines

| Upstream | Commit | Source |
|---|---|---|
| CachyOS Live ISO | `4937780541814b4b0ec7c212c00fe06299f799b6` | https://github.com/CachyOS/CachyOS-Live-ISO.git |
| cachyos-calamares | `ce54421cf0a08008ccda59a4e5f2f4747ae762be` | https://github.com/CachyOS/cachyos-calamares.git |

## Packages in the baseline

As named by `kedra.db` -- superseded releases remain in the docroot but are not part of the baseline.

| Package | Version | File | Size | SHA-256 |
|---|---|---|---|---|
| `kedra-calamares` | `3.4.2-5` | `kedra-calamares-3.4.2-5-x86_64.pkg.tar.zst` | 4123159 | `7ac6fb31dd50cfa3f6606bcaf1d7f2c87d27eecc4ab441f67d43b39215728cf2` |
| `kedra-experience` | `0.9.0-1` | `kedra-experience-0.9.0-1-any.pkg.tar.zst` | 59828 | `86dca66b455363bb1ad4336eb69f09aaeb8d13dbf3e368c5c90f5199220a377c` |
| `kedra-experience-desktop` | `0.4.0-1` | `kedra-experience-desktop-0.4.0-1-any.pkg.tar.zst` | 19165 | `24379faae54cc55f11095818f63a9e17632a5097d1f35b6821e1111f23a98cd4` |
| `kedra-experience-runtime` | `0.4.0-2` | `kedra-experience-runtime-0.4.0-2-any.pkg.tar.zst` | 107367 | `fc2146ebfd73204d8db2015cba2b0d090b50523f0ebcac28c0f9503c7f5fc6cf` |
| `kedra-experience-workflow` | `0.3.0-2` | `kedra-experience-workflow-0.3.0-2-any.pkg.tar.zst` | 21953 | `60275afc8a0fcca2859d0725aad290f86a485154d2c8bff0622e16cece7ba484` |
| `kedra-hooks` | `2026.08.14-1` | `kedra-hooks-2026.08.14-1-any.pkg.tar.zst` | 17521 | `652e7ea7faea8f86829277ddd56a69d42d6e21bd94f59352af331e5231231a03` |
| `kedra-keyring` | `2026.08.14-1` | `kedra-keyring-2026.08.14-1-any.pkg.tar.zst` | 17890 | `628445a240815ac651142dc77232b9ade7fd32a21873b8e3c1ccd8a98f60c0a9` |

Every package above has a detached `.sig` alongside it:

| Signature | SHA-256 |
|---|---|
| `kedra-calamares-3.4.2-5-x86_64.pkg.tar.zst.sig` | `861f3c5cce6fb2ec436a9eefb0b9d1f76a4713cc70c9ec4351df57db969adf1d` |
| `kedra-experience-0.9.0-1-any.pkg.tar.zst.sig` | `99280a422e3ccc6d6368e7d5c311942f0a3d3d35a3ced36769c21f87d1af2afa` |
| `kedra-experience-desktop-0.4.0-1-any.pkg.tar.zst.sig` | `d6b8184d7b5cc05c0f6690b69114231ad7dc46bcf1fdbb20cf3408d250b13d34` |
| `kedra-experience-runtime-0.4.0-2-any.pkg.tar.zst.sig` | `52f169d0cfce632d4d4f03e9dce51d9ebfc7e65789ecfc9a60cb757a9eb021d2` |
| `kedra-experience-workflow-0.3.0-2-any.pkg.tar.zst.sig` | `73b4191b0a08c7998a1618181effbb663a251457acb136366da395f4a07fe162` |
| `kedra-hooks-2026.08.14-1-any.pkg.tar.zst.sig` | `4c86df70a34001b9826e2605270147f24e1e04b104b72444a97f8ee993db1ca7` |
| `kedra-keyring-2026.08.14-1-any.pkg.tar.zst.sig` | `71752e42b5bb6d65b6bf33a214b1302517ec98e87cea7a3c18c4f82288771373` |

## Repository

| Object | SHA-256 |
|---|---|
| `kedra.db.tar.zst` | `a24115399896c6fb38d84b346b98cab7529aed81cce118a649d311ce30530427` |
| `kedra.db.tar.zst.sig` | `fe818683ef15ca284cf93c03d3bf7a8d3cd85f560a8a50ced74f63f221364fb4` |
| `kedra.files.tar.zst` | `a56dfabe0ff271552435f1b95dca409241b2b6d15b48b5b87055fec8396399c3` |
| `kedra.files.tar.zst.sig` | `77372a33acf3c19da43e78c7613f3769e8f1b396be25dd979952cb92ef362a33` |

Signing key fingerprint: `F4C6894B5CA35D7E58ADF182885AD0B1CE1A0EB5`

**DEVELOPMENT key.** Not a production Kedra identity; replacing it is Alpha work.

## ISO

| Field | Value |
|---|---|
| Filename | `kedra-desktop-linux-260904.iso` |
| SHA-256 | `c34a46d9f0c67dd012fd34e1355bd3d0d13c6eeab9d3b0c13d86abcef2d3d353` |
| Size | 2993717248 bytes (2,8G) |
| Built | 2026-09-04T13:42:17+03:00 |
| archiso | `89-1` |
| Built from | `5deba505665c45359268f75381ae0a3ddcc307d2` |
| Provenance | built from kedra-iso 5deba505665c45359268f75381ae0a3ddcc307d2; HEAD differs only in files that never enter the image (2 later commit(s)) |

## Acceptance environment

| Field | Value |
|---|---|
| Hypervisor | QEMU 11.1.1 with KVM |
| Firmware | OVMF / UEFI (edk2-ovmf 202608-1) |
| VM architecture | x86_64, q35, `-cpu host` |
| Disk | single blank raw virtio-blk image |
| Filesystem | Btrfs |
| Desktop | GNOME (GDM) |
| Bootloader | GRUB |
| pacman | `7.1.0.r9.g54d9411-4` |

## Acceptance results

| Criterion | Result |
|---|---|
| iso_static_validation | **PASS (65 checks)** |
| iso_finished_artifact_inspection | **PASS (40 checks)** |
| experience_iso_checks | **PASS (15 checks)** |
| signature_policy_tests | **PASS (21 checks)** |
| live_gate_host_tests | **PASS (31 checks)** |
| live_gnome_isolation_gate | **PASS (5 asserted values, real UEFI/KVM boot)** |
| installer_rerun_checks_in_live_iso | **PASS (22 checks)** |
| vanilla_full_gui_install | **PASS** |
| vanilla_target_invariants | **PASS (no Experience package, state, unit, override or user value)** |
| kedra_full_gui_install | **PASS** |
| kedra_first_login_activation | **PASS (generation 1, one managed shortcut)** |
| kedra_repo_suite | **PASS (94 checks)** |
| kedra_calamares_experience_suite | **PASS (129 tests)** |
| kedra_packages_suite | **PASS (1191 tests)** |
| former_high_repository_identity_reproduction | **CLOSED** |
| former_high_calamares_rerun_reproduction | **CLOSED** |
