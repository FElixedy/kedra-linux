#!/usr/bin/env bash
#
# Regression tests for the live GNOME isolation GATE in kedra-live-checks.sh.
#
#   tests/kedra-live-gate-tests.sh
#
# WHY THIS EXISTS
# ---------------
# The live acceptance run used to PRINT the five distinguishing GSettings values
# and leave the comparison to whoever read the transcript. That is not a check:
# it passed just as quietly when the values were Kedra's as when they were
# GNOME's, and the ISO was accepted either way.
#
# Turning it into a gate is only half the job. A gate that nothing exercises can
# regress back into observational output in one careless edit and nobody would
# know until an ISO shipped with a contaminated live session. So the gate's
# functions are sourced here -- the real ones, extracted from the real script --
# and driven against fixtures on a development host.
#
# HOW IT DRIVES THEM
# ------------------
# `loginctl`, `id`, `runuser`, `gsettings` and `gdbus` are replaced by shims on
# PATH, so the discovery code, the runuser/env invocation and the desktop-scope
# assertion are the production ones and only the system underneath is a fixture.
# KEDRA_LIVE_RUNTIME_ROOT points the runtime-directory checks at a temporary
# tree, which is how the missing-session-bus cases are reachable at all.

set -uo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CHECKS=${CHECKS:-${HERE}/kedra-live-checks.sh}
[[ -r ${CHECKS} ]] || { printf 'not readable: %s\n' "${CHECKS}" >&2; exit 2; }

WORK=$(mktemp -d -t kedra-live-gate-XXXXXX)
trap 'rm -rf -- "${WORK}"' EXIT

pass=0; fail=0
ok()  { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; pass=$((pass + 1)); }
bad() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=$((fail + 1)); }
section() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

# The gate itself, lifted out of the script that ships. Extracted rather than
# copied, so a gate that stops existing -- or stops being function-only -- fails
# here immediately.
GATE="${WORK}/gate.sh"
sed -n '/^# >>> live GNOME isolation gate >>>$/,/^# <<< live GNOME isolation gate <<<$/p' \
    "${CHECKS}" >"${GATE}"

[[ -s ${GATE} ]] || { printf 'no gate block found in %s\n' "${CHECKS}" >&2; exit 2; }

# The five accepted Vanilla values, as the gate must see them.
VANILLA_VALUES=$(cat <<'VALUES'
org.gnome.mutter dynamic-workspaces=true
org.gnome.desktop.wm.preferences num-workspaces=4
org.gnome.desktop.wm.keybindings close=['<Alt>F4']
org.gnome.desktop.wm.keybindings switch-to-workspace-1=['<Super>Home']
org.gnome.shell.keybindings switch-to-application-1=['<Super>1']
VALUES
)

# What kedra-experience-desktop and kedra-experience-workflow make of them. This
# is the contamination the gate exists to catch.
KEDRA_VALUES=$(cat <<'VALUES'
org.gnome.mutter dynamic-workspaces=false
org.gnome.desktop.wm.preferences num-workspaces=5
org.gnome.desktop.wm.keybindings close=['<Super>q', '<Alt>F4']
org.gnome.desktop.wm.keybindings switch-to-workspace-1=['<Super>1', '<Super>Home']
org.gnome.shell.keybindings switch-to-application-1=@as []
VALUES
)

# fixture <name> -- a shim PATH and a runtime tree; sets SHIMS and RUNROOT.
fixture() {
	SHIMS="${WORK}/$1/bin"; RUNROOT="${WORK}/$1/run"
	rm -rf -- "${WORK}/$1"; mkdir -p "${SHIMS}" "${RUNROOT}"
	: >"${WORK}/$1/values"
	VALUES_FILE="${WORK}/$1/values"

	cat >"${SHIMS}/loginctl" <<-SHIM
	#!/bin/sh
	# `loginctl list-sessions --no-legend`: SESSION UID USER SEAT ...
	printf '%s\n' "\${KEDRA_TEST_SESSIONS}"
	SHIM

	cat >"${SHIMS}/id" <<-SHIM
	#!/bin/sh
	[ "\$1" = -u ] || exit 1
	[ -n "\${KEDRA_TEST_UID}" ] || exit 1
	printf '%s\n' "\${KEDRA_TEST_UID}"
	SHIM

	# runuser -u USER -- env A=1 B=2 CMD ARGS...  ->  env A=1 B=2 CMD ARGS...
	cat >"${SHIMS}/runuser" <<-'SHIM'
	#!/bin/sh
	[ "$1" = -u ] || exit 1
	[ "$3" = -- ] || exit 1
	shift 3
	exec "$@"
	SHIM

	cat >"${SHIMS}/gsettings" <<-SHIM
	#!/bin/sh
	# gsettings get SCHEMA KEY, answered from the fixture's value table.
	[ "\$1" = get ] || exit 2
	[ "\${KEDRA_TEST_GSETTINGS_BROKEN:-0}" = 1 ] && exit 1
	[ "\${XDG_CURRENT_DESKTOP:-}" = GNOME ] || exit 1
	while IFS= read -r line; do
	    [ -n "\${line}" ] || continue
	    case "\${line}" in
	        "\$2 \$3="*) printf '%s\n' "\${line#*=}"; exit 0 ;;
	    esac
	done <"${VALUES_FILE}"
	exit 1
	SHIM

	cat >"${SHIMS}/gdbus" <<-SHIM
	#!/bin/sh
	[ "\${KEDRA_TEST_BUS_DEAD:-0}" = 1 ] && exit 1
	printf "('fixture',)\n"
	SHIM

	chmod +x "${SHIMS}"/*
	KEDRA_TEST_SESSIONS="1 1000 liveuser seat0 tty2"
	KEDRA_TEST_UID=1000
	KEDRA_TEST_GSETTINGS_BROKEN=0
	KEDRA_TEST_BUS_DEAD=0
	mkdir -p "${RUNROOT}/1000"
	# A socket, because the gate requires one rather than any old file.
	python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
" "${RUNROOT}/1000/bus"
}

values() { printf '%s\n' "$1" >"${VALUES_FILE}"; }

# run_gate -- source the real gate and run it under the current fixture.
run_gate() {
	GATE_OUT=$(
		export PATH="${SHIMS}:${PATH}"
		export KEDRA_LIVE_RUNTIME_ROOT="${RUNROOT}"
		export KEDRA_TEST_SESSIONS KEDRA_TEST_UID \
			KEDRA_TEST_GSETTINGS_BROKEN KEDRA_TEST_BUS_DEAD
		# shellcheck disable=SC1090
		. "${GATE}"
		live_isolation_gate
		printf 'STATUS=%s\n' "$?"
	)
	GATE_STATUS=${GATE_OUT##*STATUS=}
}

expect() { # expect pass|fail DESCRIPTION
	local want=$1 desc=$2
	if [[ ${want} == pass && ${GATE_STATUS} == 0 ]]; then
		ok "${desc}"
	elif [[ ${want} == fail && ${GATE_STATUS} != 0 ]]; then
		ok "${desc}"
	else
		bad "${desc} (gate exited ${GATE_STATUS})"
		printf '%s\n' "${GATE_OUT}" | sed 's/^/        /'
	fi
}

###############################################################################
section "the gate is a gate"
###############################################################################

grep -q 'live_isolation_gate' "${CHECKS}"
[[ $? -eq 0 ]] && ok "kedra-live-checks.sh calls the gate" || bad "the gate is never run"

grep -qE '^exit "\$\{_live_gate:-1\}"' "${CHECKS}"
[[ $? -eq 0 ]] && ok "the script's exit status is the gate's verdict" ||
	bad "the script does not exit on the gate's verdict"

# Every distinguishing key the acceptance run is required to assert.
for key in dynamic-workspaces num-workspaces close switch-to-workspace-1 \
	switch-to-application-1; do
	grep -q "gate_gsettings .*${key}" "${CHECKS}"
	[[ $? -eq 0 ]] && ok "${key} is asserted, not merely printed" ||
		bad "${key} is not asserted by the gate"
done

# Desktop scope, which is what made the old check report vanilla GNOME as Kedra.
grep -q 'XDG_CURRENT_DESKTOP=GNOME' "${GATE}"
[[ $? -eq 0 ]] && ok "values are read in a GNOME desktop scope" ||
	bad "the gate reads outside the GNOME desktop scope"

###############################################################################
section "an accepted Vanilla live session"
###############################################################################

fixture vanilla; values "${VANILLA_VALUES}"
run_gate
expect pass "the five accepted Vanilla values pass the gate"
grep -q "GATE OK    org.gnome.mutter dynamic-workspaces = true" <<<"${GATE_OUT}"
[[ $? -eq 0 ]] && ok "each value is reported as checked" || bad "no per-key result"

###############################################################################
section "harmless formatting differences must not decide a gate"
###############################################################################

fixture spacing
values "$(printf '%s\n' \
	"org.gnome.mutter dynamic-workspaces=true" \
	"org.gnome.desktop.wm.preferences num-workspaces=4" \
	"org.gnome.desktop.wm.keybindings close=[ '<Alt>F4' ]" \
	"org.gnome.desktop.wm.keybindings switch-to-workspace-1=['<Super>Home']" \
	"org.gnome.shell.keybindings switch-to-application-1=['<Super>1']")"
run_gate
expect pass "extra spaces inside a list are tolerated"

fixture quoting
values "$(printf '%s\n' \
	"org.gnome.mutter dynamic-workspaces=true" \
	"org.gnome.desktop.wm.preferences num-workspaces=4" \
	'org.gnome.desktop.wm.keybindings close=["<Alt>F4"]' \
	"org.gnome.desktop.wm.keybindings switch-to-workspace-1=['<Super>Home']" \
	"org.gnome.shell.keybindings switch-to-application-1=['<Super>1']")"
run_gate
expect pass "double-quoted strings are tolerated"

###############################################################################
section "a contaminated live session must FAIL"
###############################################################################

fixture kedra; values "${KEDRA_VALUES}"
run_gate
expect fail "a live session carrying the Kedra Experience values is rejected"
for key in dynamic-workspaces num-workspaces close switch-to-workspace-1 \
	switch-to-application-1; do
	grep -q "GATE FAIL  .*${key}" <<<"${GATE_OUT}"
	[[ $? -eq 0 ]] && ok "${key} is reported as failing" ||
		bad "${key} was not reported as failing"
done

# One key at a time: the gate must not depend on all five differing at once.
while IFS= read -r contaminated; do
	[[ -n ${contaminated} ]] || continue
	key=${contaminated%%=*}
	fixture "one-${key// /-}"
	values "$(grep -vF "${key}=" <<<"${VANILLA_VALUES}"; printf '%s\n' "${contaminated}")"
	run_gate
	expect fail "a single contaminated key fails the gate: ${key}"
done <<<"${KEDRA_VALUES}"

###############################################################################
section "an unusable live session must FAIL, never pass quietly"
###############################################################################

fixture no-session; values "${VANILLA_VALUES}"
KEDRA_TEST_SESSIONS=""
run_gate
expect fail "no seat0 session at all"
grep -q 'cannot be determined' <<<"${GATE_OUT}"
[[ $? -eq 0 ]] && ok "it says the live GNOME user could not be determined" ||
	bad "the failure is not explained"

fixture no-uid; values "${VANILLA_VALUES}"
KEDRA_TEST_UID=""
run_gate
expect fail "the live user's uid cannot be resolved"

fixture no-runtime-dir; values "${VANILLA_VALUES}"
rm -rf -- "${RUNROOT}/1000"
run_gate
expect fail "the live user has no runtime directory"

fixture no-bus; values "${VANILLA_VALUES}"
rm -f -- "${RUNROOT}/1000/bus"
run_gate
expect fail "the session bus socket is missing"

fixture dead-bus; values "${VANILLA_VALUES}"
KEDRA_TEST_BUS_DEAD=1
run_gate
expect fail "the session bus socket exists but does not answer"

fixture broken-gsettings; values "${VANILLA_VALUES}"
KEDRA_TEST_GSETTINGS_BROKEN=1
run_gate
expect fail "gsettings itself fails"

fixture missing-key
values "$(grep -v 'switch-to-application-1' <<<"${VANILLA_VALUES}")"
run_gate
expect fail "a key the schema does not answer for"

###############################################################################
printf '\n%d passed, %d failed\n' "${pass}" "${fail}"
(( fail == 0 ))
