#!/usr/bin/env bash
#
# kedra-experience-iso-checks -- what the live image may and may not carry.
#
#   tests/kedra-experience-iso-checks.sh
#
# Static: it reads the ISO profile and the published [kedra] database. No ISO is
# built, no VM is booted, nothing is installed. That is the point -- these are
# the properties that must hold *before* an acceptance run is worth doing, and
# they are cheap enough to check on every change.
#
# THE PROPERTY THIS EXISTS FOR
# ----------------------------
# The live installer needs the Experience resolver, so the image carries
# kedra-experience and kedra-experience-runtime. It must NOT carry
# kedra-experience-desktop or kedra-experience-workflow: those are the GSettings
# vendor overrides, and installing them so the installer can resolve profiles
# would give the LIVE session Kedra's fixed five-workspace model and Super+1..5
# keymap before the user has chosen anything at all.
#
# A live session that already behaves like Kedra cannot honestly offer "Vanilla
# GNOME" as a choice, and the difference is invisible unless something checks.

set -uo pipefail

HERE=$(dirname "$(readlink -f "$0")")
PROFILE=$(readlink -f "${HERE}/../archiso")
source "$(readlink -f "${HERE}/../bin/_kedra.sh")" 2>/dev/null || true

KEDRA_REPO_NAME=${KEDRA_REPO_NAME:-kedra}
KEDRA_REPO_ARCH=${KEDRA_REPO_ARCH:-x86_64}
KEDRA_REPO_ROOT=${KEDRA_REPO_ROOT:-${HOME}/Build/kedra-repo}
KEDRA_REPO_DIR="${KEDRA_REPO_ROOT}/${KEDRA_REPO_NAME}/os/${KEDRA_REPO_ARCH}"

RESOLVER_PACKAGES=(kedra-experience kedra-experience-runtime)
BEHAVIOUR_PACKAGES=(kedra-experience-desktop kedra-experience-workflow)
PACKAGE_LISTS=(packages.x86_64 packages_desktop.x86_64)

pass=0; fail=0
check() { # check <name> <status> [detail]
	if (( $2 == 0 )); then
		printf '\033[1;32mPASS\033[0m  %s\n' "$1"; (( pass++ ))
	else
		printf '\033[1;31mFAIL\033[0m  %s\n' "$1"; (( fail++ ))
		[[ -n ${3:-} ]] && printf '      %s\n' "$3"
	fi
}
section() { printf '\n\033[1;36m### %s\033[0m\n' "$*"; }

listed() { grep -qxF "$2" "${PROFILE}/$1"; }

###############################################################################
section "the resolver is available to the installer"
###############################################################################

for list in "${PACKAGE_LISTS[@]}"; do
	for pkg in "${RESOLVER_PACKAGES[@]}"; do
		listed "$list" "$pkg"
		check "${list}: ${pkg} is on the live image" $?
	done
done

###############################################################################
section "Vanilla live-session isolation"
###############################################################################

for list in "${PACKAGE_LISTS[@]}"; do
	for pkg in "${BEHAVIOUR_PACKAGES[@]}"; do
		! listed "$list" "$pkg"
		check "${list}: ${pkg} is NOT on the live image" $? \
			"a vendor-override package on the live medium changes the live session"
	done
done

# Belt and braces: whatever the lists say, no rendered override may be baked
# into the profile's own filesystem either.
overrides=$(find "${PROFILE}/airootfs" -name '95_kedra-experience-*.gschema.override' 2>/dev/null)
[[ -z ${overrides} ]]
check "no Kedra GSettings override is baked into airootfs" $? "${overrides}"

# The live session's own dconf drop-in is live-medium behaviour -- no lock
# screen, no idle suspend -- and must not have grown Experience keys.
live_dconf="${PROFILE}/airootfs/etc/dconf/db/local.d/00-kedra-live-session"
if [[ -f ${live_dconf} ]]; then
	! grep -qE 'org/gnome/(mutter|desktop/wm|shell/keybindings)|num-workspaces|dynamic-workspaces' \
		"${live_dconf}"
	check "the live dconf drop-in sets no Experience key" $? \
		"$(grep -nE 'mutter|keybindings|workspaces' "${live_dconf}" | head -3)"
else
	check "the live dconf drop-in is present" 1 "missing ${live_dconf}"
fi

###############################################################################
section "the target snapshot is database-driven"
###############################################################################

# prepare_kedra_build_tree() stages exactly the files kedra.db names. That is
# what makes new Experience releases reach the installed system's [kedra]
# snapshot without anyone copying artifacts by hand -- so it is asserted rather
# than assumed, and so is the absence of any hand-rolled copy beside it.
grep -q '%FILENAME%' "$(readlink -f "${HERE}/../util-iso.sh")"
check "the ISO stages what kedra.db names, not a hand-written list" $?

if [[ -f ${KEDRA_REPO_DIR}/${KEDRA_REPO_NAME}.db ]]; then
	mapfile -t db_names < <(
		bsdtar -xOf "${KEDRA_REPO_DIR}/${KEDRA_REPO_NAME}.db" --include '*/desc' 2>/dev/null |
			awk '/^%NAME%/{getline n; print n}'
	)
	for pkg in "${RESOLVER_PACKAGES[@]}" "${BEHAVIOUR_PACKAGES[@]}"; do
		printf '%s\n' "${db_names[@]}" | grep -qxF "$pkg"
		check "[${KEDRA_REPO_NAME}] publishes ${pkg}" $? \
			"the target snapshot can only contain what the database names"
	done
else
	printf '\033[1;33mSKIP\033[0m  [%s] is not published at %s\n' \
		"${KEDRA_REPO_NAME}" "${KEDRA_REPO_DIR}"
fi

###############################################################################
printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
