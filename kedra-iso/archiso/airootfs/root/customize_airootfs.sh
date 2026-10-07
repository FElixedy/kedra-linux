#!/usr/bin/env bash
#
# Run once by mkarchiso (_make_customize_airootfs), chrooted into the image, and
# deleted from the image immediately afterwards. arch-chroot provides /proc,
# /sys and /dev here, so tools that need them work.
#
# Keep this file small. It exists for steps that genuinely cannot be expressed
# as a static file in airootfs/ -- not as a general customisation hook.

set -e -u

# dconf reads the COMPILED database /etc/dconf/db/local, not the .d directory
# that ships in airootfs. Without this the live session settings in
# /etc/dconf/db/local.d are silently inert.
if command -v dconf >/dev/null 2>&1; then
    dconf update
fi
