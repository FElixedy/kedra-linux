#!/usr/bin/env bash
#
# The Kedra Experience installer-integration suite.
#
#   tests/kedra-experience/run-tests.sh
#
# Exercises the two Python job modules against the REAL resolver from the
# kedra-packages checkout and the real shipped manifests. Nothing is installed,
# no Calamares runs, and every target root is a temporary directory.
#
# The resolver is not faked, deliberately: a fake one would let this suite agree
# with itself about package sets while the product disagreed, which is precisely
# the drift the installer's no-duplication rule exists to prevent. Fake
# resolvers appear only in the failure-mode tests, where the point IS the
# malformed answer.
#
# Set KEDRA_PACKAGES if the Experience checkout is not beside this repository.

set -uo pipefail

HERE=$(dirname "$(readlink -f "$0")")
export KEDRA_PACKAGES=${KEDRA_PACKAGES:-$(readlink -f "${HERE}/../../../../kedra-packages")}

if [[ ! -x ${KEDRA_PACKAGES}/tools/kedra-experience ]]; then
	printf '\033[1;31m==> ERROR:\033[0m no Experience checkout at %s\n' "${KEDRA_PACKAGES}" >&2
	printf '         set KEDRA_PACKAGES to the kedra-packages working tree\n' >&2
	exit 1
fi

printf '\033[1;36m### Kedra Experience installer integration\033[0m\n'
printf '    resolver: %s\n' "${KEDRA_PACKAGES}/tools/kedra-experience"
cd "${HERE}" || exit 1
exec python3 -m unittest discover -s . -p 'test_*.py' "$@"
