#!/bin/sh
# Verification run INSIDE the booted Kedra live system.
#
# Delivered to the VM on a small FAT disk and executed there; its output is
# written back to the same disk so the host can read it without needing a
# network service, SSH or an interactive transcript.
#
# Read-only with respect to the live system, with two deliberate exceptions:
# `pacman -Sy` (proves the signed [kedra] database actually syncs in the live
# environment) and `pacman -Sup` (a PRINT-ONLY full-system upgrade resolution).
# No upgrade is applied and no partial upgrade is ever performed.

exec 2>&1
sep() { printf '\n===== %s =====\n' "$*"; }

# pacman-init.service is what establishes Kedra trust in the live keyring
# (`pacman-key --init` + `--populate` over every keyring in
# /usr/share/pacman/keyrings). It takes a couple of minutes -- importing the
# Arch keyring is slow -- and the guest agent is reachable long before it
# finishes. Checking signatures before it completes would measure a race, not
# the system, so wait for it explicitly.
sep "waiting for pacman-init.service"
i=0
while [ "$(systemctl is-active pacman-init.service)" = activating ] && [ "$i" -lt 300 ]; do
    sleep 5; i=$((i + 5))
done
echo "pacman-init.service: $(systemctl is-active pacman-init.service) after ${i}s"
systemctl is-system-running --wait >/dev/null 2>&1
echo "system state: $(systemctl is-system-running)"

sep "uname"
uname -a

sep "/etc/os-release"
cat /etc/os-release

sep "/etc/lsb-release"
cat /etc/lsb-release 2>/dev/null || echo "(absent)"

sep "hostname"
hostname; cat /etc/hostname

sep "kedra-hooks installed"
pacman -Q kedra-hooks

sep "kedra-keyring installed"
pacman -Q kedra-keyring

sep "cachyos-hooks MUST NOT be separately installed"
pacman -Q cachyos-hooks; echo "pacman -Q cachyos-hooks exit=$?"

sep "kedra-hooks provides/replaces"
pacman -Qi kedra-hooks | grep -iE 'provides|replaces|conflicts'

sep "who owns /etc/os-release"
pacman -Qo /etc/os-release 2>&1

sep "configured repositories, in order"
pacman-conf --repo-list

sep "effective SigLevel per repository"
# The ACTIVE configuration, as pacman itself resolves it -- not the profile
# source, and not a grep. pacman-conf normalises every spelling of a policy
# (Never / Optional / TrustAll, each optionally Package- or Database-prefixed,
# in any order) into the same tokens, and reports it for repositories that
# inherit from [options] as well as ones that override it.
_global=$(pacman-conf SigLevel | tr '\n' ' ')
printf '%-12s %s\n' "(global)" "${_global}"
for r in $(pacman-conf --repo-list); do
    _lvl=$(pacman-conf --repo="$r" SigLevel | tr '\n' ' ')
    printf '%-12s %s\n' "$r" "${_lvl:-(inherits global)}"
done
printf '%-12s %s\n' "(localfile)" "$(pacman-conf LocalFileSigLevel | tr '\n' ' ')"

sep "no repository may weaken package validation"
# M0 shipped [cachyos] with `SigLevel = Optional TrustAll`, which the previous
# check -- a grep for `SigLevel = Never` -- passed. Assert the effective policy
# instead: a package must be signed (PackageRequired) by a key the keyring
# trusts (PackageTrustedOnly), for every repository including inheriting ones.
#
# LocalFileSigLevel is intentionally not asserted: it governs `pacman -U` on an
# operator-supplied file, not repository packages, and Optional is the Arch
# default.
_weak=0
for r in $(pacman-conf --repo-list); do
    _lvl=$(pacman-conf --repo="$r" SigLevel | tr '\n' ' ')
    [ -n "$_lvl" ] || _lvl="${_global}"
    case "$_lvl" in
        *PackageRequired*) ;;
        *) echo "WEAK: [$r] does not require a package signature: $_lvl"; _weak=1 ;;
    esac
    case "$_lvl" in
        *PackageTrustedOnly*) ;;
        *) echo "WEAK: [$r] accepts untrusted signing keys: $_lvl"; _weak=1 ;;
    esac
done
[ "$_weak" = 0 ] && echo "OK: every repository requires a trusted package signature" \
                 || echo "FAILED: at least one repository weakens package validation"

sep "no Optional/TrustAll/Never override in the active config files"
# Belt and braces alongside the effective check above: show any surviving
# weakening directive with the file and line it is on.
grep -rniE '^[[:space:]]*SigLevel[[:space:]]*=.*(Never|Optional|TrustAll)' \
    /etc/pacman.conf /etc/pacman.d/ 2>/dev/null |
    grep -viE 'SigLevel[[:space:]]*=[[:space:]]*Required[[:space:]]+DatabaseOptional[[:space:]]*$' ||
    echo "clean (no weakening SigLevel directive)"

sep "Kedra trust in the live keyring"
pacman-key --list-keys 2>/dev/null | grep -iB2 -A2 'Kedra' | head -20

sep "in-image [kedra] docroot"
ls -l /usr/share/kedra/repo/kedra/os/x86_64/

sep "pacman -Sy (signed kedra.db must verify)"
pacman -Sy --noconfirm

sep "pacman -Si kedra-hooks (must come from [kedra])"
pacman -Si kedra-hooks | head -6

sep "pacman -Si kedra-keyring (must come from [kedra])"
pacman -Si kedra-keyring | head -4

sep "representative CachyOS package still from [cachyos]"
pacman -Si linux-cachyos | head -4
pacman -Si cachyos-settings | head -4

sep "representative Arch packages still from core/extra"
pacman -Si gnome-shell | head -4
pacman -Si bash | head -4

sep "full-system upgrade resolution (PRINT ONLY, never applied)"
pacman -Sup --print-format '%r/%n' 2>&1 | head -30
echo "--- count: $(pacman -Sup --print-format '%n' 2>/dev/null | wc -l) package(s) would upgrade ---"

sep "GNOME session"
echo "display-manager -> $(readlink -f /etc/systemd/system/display-manager.service)"
echo "display-manager active: $(systemctl is-active display-manager)"
echo "gdm active:             $(systemctl is-active gdm)"
loginctl list-sessions --no-legend
for s in $(loginctl list-sessions --no-legend | awk '{print $1}'); do
    echo "-- session $s"
    loginctl show-session "$s" -p Type -p Class -p Active -p State -p Desktop -p Name
done
echo "-- processes --"
pgrep -a gnome-shell | head -3
pgrep -a gdm | head -3

sep "graphics / renderer"
[ -e /dev/dri/card0 ] && echo "/dev/dri/card0 present" || echo "no /dev/dri/card0"
lsmod | grep -E '^virtio_gpu|^drm' | head

sep "NetworkManager"
systemctl is-active NetworkManager
nmcli -t -f STATE,CONNECTIVITY general 2>&1
nmcli -t -f DEVICE,TYPE,STATE device 2>&1

sep "network connectivity"
ping -c2 -W4 1.1.1.1 2>&1 | tail -3
curl -sS -m 20 -o /dev/null -w 'archlinux.org http=%{http_code}\n' https://archlinux.org 2>&1

sep "installer entry point present"
command -v calamares && calamares --version 2>&1 | head -2
ls /usr/share/applications/ | grep -iE 'calamares|install' || echo "(no installer .desktop)"
ls -l /usr/local/bin/calamares-online.sh 2>&1

sep "Kedra Experience resolver (live)"
# The installer needs this to list and resolve profiles. If it is missing the
# Experience page refuses to be left, so an installation cannot silently
# continue as Vanilla -- but it is better to find out here.
command -v kedra-experience >/dev/null && echo "kedra-experience: present" \
    || echo "kedra-experience: MISSING"
kedra-experience profiles --format json 2>&1 |
    head -c 400 || echo "(profiles failed)"
echo
echo "-- resolve: vanilla --"
echo '{"base_preset":"vanilla"}' | kedra-experience resolve --selection - 2>&1 |
    grep -E '"applies_experience_layer"|"required"' | head -4
echo "-- resolve: kedra --"
echo '{"base_preset":"kedra"}' | kedra-experience resolve --selection - 2>&1 |
    sed -n '/"required"/,/]/p' | head -10

sep "Vanilla live-session isolation"
# The live session must behave like upstream GNOME. The two vendor-override
# packages are deliberately NOT on the image: installing them so the installer
# could resolve profiles would give the live session Kedra's workspace model and
# keymap before the user has chosen anything.
for p in kedra-experience-desktop kedra-experience-workflow; do
    if pacman -Qq "$p" >/dev/null 2>&1; then
        echo "$p: INSTALLED -- the live session is not vanilla"
    else
        echo "$p: absent (correct)"
    fi
done
ls /usr/share/glib-2.0/schemas/95_kedra-experience-* 2>/dev/null \
    && echo "Kedra overrides present -- WRONG" \
    || echo "no Kedra GSettings override on the live image (correct)"

# READ THE VALUES AS THE LIVE USER, IN A GNOME DESKTOP SCOPE -- AND GATE ON THEM.
#
# This used to run bare `gsettings` as root, and it reported
# dynamic-workspaces=false on a perfectly vanilla live session -- which is
# indistinguishable from Kedra's fixed five-workspace model, the exact leak
# this section exists to detect.
#
# Two reasons it was wrong, and both matter:
#
#   1. Desktop scope. mutter's OWN schema default for dynamic-workspaces is
#      false; what makes a GNOME session dynamic is gnome-shell's
#      00_org.gnome.shell.gschema.override, whose section is
#      [org.gnome.mutter:GNOME] -- a DESKTOP-SCOPED default that applies only
#      when XDG_CURRENT_DESKTOP names GNOME. root's shell has no such variable,
#      so the scoped default was skipped and mutter's bare default came back.
#
#   2. Wrong user. root is not the person whose session is being judged. A
#      value the live user had explicitly set would never be seen.
#
# Kedra's own overrides ship each section twice, plain and :GNOME, precisely so
# they do not depend on this variable -- so a check that drops the variable sees
# Kedra's value but not GNOME's, and reports upstream GNOME as if it were Kedra.
#
# Reading the right values was only half the fix. Printing them for a human to
# compare by eye is not a check: an acceptance run that nobody reads passes just
# as quietly as one that is correct. Everything below is therefore a GATE. It
# decides this script's exit status, and kedra-iso-testvm turns that into the
# acceptance result.
#
# It fails -- rather than skipping, warning or guessing -- when the live GNOME
# user cannot be determined, when its uid or runtime directory cannot be, when
# the session bus cannot be queried, when gsettings itself fails, and of course
# when any value differs from the accepted Vanilla one.

# >>> live GNOME isolation gate >>>
# Everything between these markers is functions only, so that
# tests/kedra-live-gate-tests.sh can source this block on a development host and
# drive it against fixtures. Keep it free of top-level statements.

_gate_failures=0
_live_user=
_live_uid=

gate_fail() { printf 'GATE FAIL  %s\n' "$*"; _gate_failures=$((_gate_failures + 1)); }
gate_ok()   { printf 'GATE OK    %s\n' "$*"; }

# The live user's runtime directory. /run/user/<uid> in every real session --
# KEDRA_LIVE_RUNTIME_ROOT exists only so the gate tests can present a fixture
# with no session bus, and nothing on a live image ever sets it.
live_runtime_dir() { printf '%s/%s' "${KEDRA_LIVE_RUNTIME_ROOT:-/run/user}" "${_live_uid}"; }

# Normalise a GVariant as gsettings prints it, so that harmless formatting
# never decides a gate: collapse whitespace, write strings in single quotes,
# and drop the spaces GVariant puts inside and after list punctuation.
#
# Deliberately NOT a parser. It equalises presentation; it does not reorder,
# deduplicate or otherwise reinterpret a value, because a keybinding list that
# holds the right entries in the wrong order is not the accepted value.
gs_normalise() {
    sed -e 's/"/'"'"'/g' \
        -e 's/[[:space:]][[:space:]]*/ /g' \
        -e 's/^ *//' -e 's/ *$//' \
        -e 's/\[ */[/g' -e 's/ *\]/]/g' \
        -e 's/ *, */,/g'
}

# Who is logged in on the seat, and as which uid. Both are gates: an acceptance
# run that cannot tell whose session it is has not checked anything.
live_gnome_context() {
    _live_user=$(loginctl list-sessions --no-legend 2>/dev/null |
                 awk '$4 == "seat0" { print $3; exit }')
    if [ -z "${_live_user}" ]; then
        gate_fail "no seat0 session: the live GNOME user cannot be determined"
        return 1
    fi
    _live_uid=$(id -u "${_live_user}" 2>/dev/null)
    if [ -z "${_live_uid}" ]; then
        gate_fail "the uid of live user '${_live_user}' cannot be determined"
        return 1
    fi
    if [ ! -d "$(live_runtime_dir)" ]; then
        gate_fail "no XDG_RUNTIME_DIR at $(live_runtime_dir) for '${_live_user}'"
        return 1
    fi
    gate_ok "live GNOME user '${_live_user}' (uid ${_live_uid})"
    return 0
}

# Every read happens as that user, on that user's bus, in a GNOME desktop
# scope. XDG_CURRENT_DESKTOP is what makes gnome-shell's [schema:GNOME]
# override sections apply; without it mutter's bare schema defaults come back
# and a vanilla session is misreported as Kedra.
as_live_user() {
    runuser -u "${_live_user}" -- env \
        XDG_RUNTIME_DIR="$(live_runtime_dir)" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$(live_runtime_dir)/bus" \
        XDG_CURRENT_DESKTOP=GNOME "$@" 2>/dev/null
}

# The scope this reads in is itself asserted: a runner that lost
# XDG_CURRENT_DESKTOP would silently answer with the wrong defaults.
gate_desktop_scope() {
    _scope=$(as_live_user sh -c 'printf %s "${XDG_CURRENT_DESKTOP}"')
    if [ "${_scope}" != GNOME ]; then
        gate_fail "the GSettings reader is not in a GNOME desktop scope (XDG_CURRENT_DESKTOP='${_scope}')"
        return 1
    fi
    gate_ok "reading in XDG_CURRENT_DESKTOP=GNOME scope, as '${_live_user}'"
    return 0
}

# The session bus has to be answerable, not merely present: a socket that is
# there but dead would make every gsettings read fall back to schema defaults.
gate_session_bus() {
    if [ ! -S "$(live_runtime_dir)/bus" ]; then
        gate_fail "no session bus socket at $(live_runtime_dir)/bus"
        return 1
    fi
    if ! as_live_user gdbus call --session --dest org.freedesktop.DBus \
        --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.GetId >/dev/null; then
        gate_fail "the session bus of '${_live_user}' could not be queried"
        return 1
    fi
    gate_ok "session bus of '${_live_user}' answers"
    return 0
}

# gate_gsettings SCHEMA KEY EXPECTED
gate_gsettings() {
    _schema=$1 _key=$2 _want=$3
    if ! _raw=$(as_live_user gsettings get "${_schema}" "${_key}"); then
        gate_fail "${_schema} ${_key}: gsettings failed in the live session"
        return 1
    fi
    _got=$(printf '%s' "${_raw}" | gs_normalise)
    _want=$(printf '%s' "${_want}" | gs_normalise)
    if [ "${_got}" != "${_want}" ]; then
        gate_fail "${_schema} ${_key} = ${_got} (accepted Vanilla value: ${_want})"
        return 1
    fi
    gate_ok "${_schema} ${_key} = ${_got}"
    return 0
}

# The distinguishing keys. Each one has a different value under the Kedra
# Experience, which is what makes this set able to tell the two apart:
#
#   Vanilla GNOME                       Kedra Experience
#   dynamic-workspaces      true        false
#   num-workspaces          4           5
#   close                   ['<Alt>F4'] ['<Super>q','<Alt>F4']
#   switch-to-workspace-1   ['<Super>Home']
#                                       ['<Super>1','<Super>Home']
#   switch-to-application-1 ['<Super>1'] []
live_isolation_gate() {
    _gate_failures=0
    live_gnome_context || return 1
    gate_desktop_scope
    gate_session_bus
    gate_gsettings org.gnome.mutter                 dynamic-workspaces      "true"
    gate_gsettings org.gnome.desktop.wm.preferences num-workspaces          "4"
    gate_gsettings org.gnome.desktop.wm.keybindings close                   "['<Alt>F4']"
    gate_gsettings org.gnome.desktop.wm.keybindings switch-to-workspace-1   "['<Super>Home']"
    gate_gsettings org.gnome.shell.keybindings      switch-to-application-1 "['<Super>1']"
    [ "${_gate_failures}" -eq 0 ]
}
# <<< live GNOME isolation gate <<<

if live_isolation_gate; then
    _live_gate=0
    echo "LIVE GNOME ISOLATION GATE: PASSED"
else
    _live_gate=1
    echo "LIVE GNOME ISOLATION GATE: FAILED (${_gate_failures} check(s) failed)"
fi

sep "boot-critical failures"
systemctl --failed --no-legend || true
echo "--- system state: $(systemctl is-system-running) ---"

sep "DONE"

# The acceptance verdict. kedra-iso-testvm records this script's exit status and
# fails the run on anything but zero, so the live isolation gate above decides
# whether the ISO is accepted rather than sitting unread in a transcript.
if [ "${_live_gate:-1}" -eq 0 ]; then
    echo "LIVE CHECKS RESULT: PASS"
else
    echo "LIVE CHECKS RESULT: FAIL (Vanilla live-session isolation)"
fi
exit "${_live_gate:-1}"
