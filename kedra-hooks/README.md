# kedra-hooks

Pacman hooks for setting up system identification files and other features of Kedra Linux.

File name | Description
:--- | :---
kedra-branding | Fixes files like `os-release`, `lsb-release`, `issues` for Kedra Linux.
kedra-branding.hook | Runs `kedra-branding` script after the `kedra-hooks` package is updated.
kedra-plymouth-initramfs.hook | Replaces plymouth script to update initrd with ``update-initramfs``
kedra-reboot-required | Notifies user to reboot after essential system files have been updated.
kedra-reboot-required.hook | Runs `kedra-reboot-required` after any listed essential system files have been updated.
lsb-release.hook | Runs `kedra-branding` script after package `lsb-release` has been updated.
os-release.hook | Runs `kedra-branding` script after package `filesystem` has been updated.
update-initramfs | Helper script for autodetecting and using installed initramfs generator
PKGBUILD | Package recipe (upstream keeps it in a separate PKGBUILDS repository).

## Relationship to upstream

This is a downstream fork of [CachyOS/cachyos-hooks](https://github.com/CachyOS/cachyos-hooks)
(imported at tag `2026.02.05`, commit `9996afc`). Layout and script structure are kept as
close to upstream as possible so future CachyOS changes stay easy to diff and merge.

The Kedra delta is deliberately limited to distro identity:

* `cachyos-*` files are renamed to `kedra-*`; the hook/script split is unchanged.
* `kedra-branding` writes the Kedra identity instead of the CachyOS one, and additionally
  normalizes already CachyOS-branded files (Kedra systems are converted CachyOS systems).
* Identity values live in variables at the top of `kedra-branding` so the pending branding
  and domain decisions are one-line changes.
* Everything non-identity is preserved verbatim, including the CachyOS kernel package
  targets in `kedra-reboot-required.hook` — Kedra keeps the CachyOS kernels and repositories.

`kedra-hooks` conflicts with and replaces `cachyos-hooks`: only one identity owner may be
installed, otherwise both hook sets would fight over `/etc/os-release` on every update.
