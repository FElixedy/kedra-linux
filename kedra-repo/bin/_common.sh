#!/usr/bin/env bash
# Shared configuration for the Kedra repository tooling.
#
# Everything here is overridable from the environment so the same scripts work
# on a developer machine, in a container and later in CI.

set -euo pipefail

# Repository identity. There is exactly one Kedra binary repository.
# CPU-optimised variants stay with CachyOS; Kedra does not fork them.
KEDRA_REPO_NAME=${KEDRA_REPO_NAME:-kedra}
KEDRA_REPO_ARCH=${KEDRA_REPO_ARCH:-x86_64}

# Publishing docroot. This directory is the exact tree that will later be served
# over HTTPS, so the on-disk layout must not change when hosting moves:
#
#   $KEDRA_REPO_ROOT/$KEDRA_REPO_NAME/os/$KEDRA_REPO_ARCH/
#
# which is consumed as `Server = <base>/$repo/os/$arch`.
KEDRA_REPO_ROOT=${KEDRA_REPO_ROOT:-${HOME}/Build/kedra-repo}

# Where built packages are staged before publishing.
KEDRA_STAGING_DIR=${KEDRA_STAGING_DIR:-${HOME}/Build/kedra-staging}

# GnuPG home holding the signing secret key.
#
# DEVELOPMENT: a local throwaway keyring created by kedra-dev-keys-init.
# PRODUCTION:  must be replaced by a hardware token / HSM / CI secret store.
# This directory is deliberately outside every git working tree.
KEDRA_SIGNING_GNUPGHOME=${KEDRA_SIGNING_GNUPGHOME:-${XDG_DATA_HOME:-${HOME}/.local/share}/kedra-dev-signing/gnupg}

# Fingerprint of the key used to sign packages and the repository database.
# Falls back to the fingerprint recorded by kedra-dev-keys-init.
_kedra_signing_key_file="$(dirname "${KEDRA_SIGNING_GNUPGHOME}")/packager-fingerprint"
if [[ -z ${KEDRA_SIGNING_KEY:-} && -r ${_kedra_signing_key_file} ]]; then
	KEDRA_SIGNING_KEY=$(<"${_kedra_signing_key_file}")
fi
KEDRA_SIGNING_KEY=${KEDRA_SIGNING_KEY:-}

# Absolute path of the published repository directory.
KEDRA_REPO_DIR="${KEDRA_REPO_ROOT}/${KEDRA_REPO_NAME}/os/${KEDRA_REPO_ARCH}"

kedra_msg()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
kedra_warn() { printf '\033[1;33m==> WARNING:\033[0m %s\n' "$*" >&2; }
kedra_die()  { printf '\033[1;31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

kedra_gpg() {
	gpg --homedir "${KEDRA_SIGNING_GNUPGHOME}" --batch --no-tty "$@"
}

kedra_require_signing_key() {
	[[ -d ${KEDRA_SIGNING_GNUPGHOME} ]] ||
		kedra_die "signing GNUPGHOME ${KEDRA_SIGNING_GNUPGHOME} does not exist (run kedra-dev-keys-init)"
	[[ -n ${KEDRA_SIGNING_KEY} ]] ||
		kedra_die "KEDRA_SIGNING_KEY is unset and no packager-fingerprint file was found"
	kedra_gpg --list-secret-keys "${KEDRA_SIGNING_KEY}" >/dev/null 2>&1 ||
		kedra_die "no secret key ${KEDRA_SIGNING_KEY} in ${KEDRA_SIGNING_GNUPGHOME}"
}
