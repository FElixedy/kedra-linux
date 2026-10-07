#!/usr/bin/env bash
# Shared Kedra configuration for the ISO build.
#
# Naming and defaults deliberately mirror kedra-repo/bin/_common.sh so the two
# workstreams can be driven from one set of environment variables.

# Where the signed [kedra] docroot lives. Same default and same meaning as
# kedra-repo: the directory whose layout is already the future HTTPS layout,
# consumed as `Server = <base>/$repo/os/$arch`.
KEDRA_REPO_NAME=${KEDRA_REPO_NAME:-kedra}
KEDRA_REPO_ARCH=${KEDRA_REPO_ARCH:-x86_64}
KEDRA_REPO_ROOT=${KEDRA_REPO_ROOT:-${HOME}/Build/kedra-repo}
KEDRA_REPO_DIR="${KEDRA_REPO_ROOT}/${KEDRA_REPO_NAME}/os/${KEDRA_REPO_ARCH}"

# Absolute path of this kedra-iso checkout, resolved once. Computed from this
# file's own location so the scripts work from any working directory, and
# normalised with `pwd -P` so no '..' segments leak into pacman.conf, where an
# unresolvable path is a confusing parse error rather than a clear one.
_KEDRA_ISO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

# Checked-out kedra-keyring working tree, used to bootstrap the build keyring
# from reviewed public material rather than from a keyserver.
KEDRA_KEYRING_SRC=${KEDRA_KEYRING_SRC:-$(dirname -- "${_KEDRA_ISO_ROOT}")/kedra-keyring}

# Build-local pacman keyring.
#
# WHY THIS EXISTS: mkarchiso expands the profile pacman.conf with `pacman-conf`
# and strips RootDir/DBPath/CacheDir/HookDir/LogFile, but NOT GPGDir. Left
# alone, pacstrap would verify every package against the build host's
# /etc/pacman.d/gnupg, so building a Kedra ISO would require teaching the host
# keyring to trust Kedra. Pointing GPGDir at this directory keeps signature
# verification fully enabled while leaving the host's pacman trust untouched.
KEDRA_ISO_GPGDIR=${KEDRA_ISO_GPGDIR:-${_KEDRA_ISO_ROOT}/keyring}

# Build-local pacman package cache.
#
# WHY THIS EXISTS: mkarchiso defaults to the SYSTEM cache (/var/cache/pacman/pkg),
# which is root-owned and therefore unwritable during a rootless build -- inside
# the user namespace we appear as uid 0, but the kernel still checks the real uid
# against the on-disk owner. mkarchiso honours a profile CacheDir as long as it
# differs from the system one, so point it somewhere the build user owns.
# Kept outside work_dir so it survives `clean_first` and speeds up rebuilds.
KEDRA_ISO_CACHEDIR=${KEDRA_ISO_CACHEDIR:-${HOME}/Build/kedra-iso-cache}

# Keyrings the build keyring is populated with. Kedra trusts Arch and CachyOS
# packages exactly as before and adds its own root; nothing is replaced.
KEDRA_ISO_KEYRINGS=${KEDRA_ISO_KEYRINGS:-"archlinux cachyos kedra"}
