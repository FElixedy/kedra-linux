#!/bin/sh
# Installer smoke test plus the identity checks that need an exact answer.
#
# SCOPE: this proves an installer entry point exists and opens its first screen.
# It deliberately does NOT install anything -- the Calamares workstream owns
# installation, installer branding and installed-system identity.

exec 2>&1
sep() { printf '\n===== %s =====\n' "$*"; }

sep "waiting for the session to settle"
systemctl is-system-running --wait >/dev/null 2>&1
echo "system state: $(systemctl is-system-running)"

sep "cachyos-hooks is NOT a separately installed package"
# `pacman -Q cachyos-hooks` resolves PROVIDES, so it answers "kedra-hooks" and
# exits 0 -- which looks alarming but is correct. The unambiguous question is
# whether a package literally named cachyos-hooks is in the local database.
echo "-- exact match in the local package database:"
if pacman -Qq | grep -qx cachyos-hooks; then
    echo "FAIL: cachyos-hooks IS separately installed"
else
    echo "PASS: no package named cachyos-hooks is installed"
fi
echo "-- local database entries matching 'hooks':"
pacman -Qq | grep -i hooks
echo "-- what satisfies the cachyos-hooks name:"
pacman -Q cachyos-hooks 2>&1
echo "-- owner of the identity hooks on disk:"
pacman -Qo /usr/share/libalpm/hooks/os-release.hook 2>&1
ls /usr/share/libalpm/hooks/ | grep -iE 'kedra|cachyos'

sep "GNOME utilities available in the live session"
for b in nautilus kgx gnome-control-center gnome-text-editor gnome-disks gnome-system-monitor; do
    printf '%-24s %s\n' "$b" "$(command -v "$b" || echo MISSING)"
done

sep "installer package and files"
pacman -Qq | grep -i calamares
echo "-- binary:"; ls -l /usr/bin/calamares
echo "-- desktop entries:"; ls /usr/share/applications/ | grep -iE 'calamares|install' || echo "(none)"
echo "-- calamares configs:"; ls /etc/calamares/ 2>&1; ls /usr/share/calamares/settings*.conf 2>&1

sep "launching the installer"
# Calamares is a GUI application; run it inside liveuser's Wayland session.
# The live polkit rules already allow this without a password prompt.
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export QT_QPA_PLATFORM=wayland
export XDG_CURRENT_DESKTOP=GNOME
# The offline settings file is what the desktop launcher uses; make sure one is
# in place exactly as calamares-online.sh would.
if [ ! -f /etc/calamares/settings.conf ]; then
    mkdir -p /etc/calamares
    cp /usr/share/calamares/settings_offline.conf /etc/calamares/settings.conf 2>/dev/null ||
    cp /usr/share/calamares/settings.conf /etc/calamares/settings.conf 2>/dev/null ||
    echo "(no settings file to install)"
fi
calamares -D6 >/tmp/calamares.log 2>&1 &
CALPID=$!
echo "calamares pid=$CALPID"
i=0
while [ "$i" -lt 90 ]; do
    sleep 5; i=$((i + 5))
    kill -0 "$CALPID" 2>/dev/null || { echo "calamares exited after ${i}s"; break; }
done
echo "-- still running after ${i}s: $(kill -0 "$CALPID" 2>/dev/null && echo yes || echo no)"
echo "-- window list (wayland clients of the session):"
pgrep -a calamares
echo "-- last 40 lines of the calamares log:"
tail -40 /tmp/calamares.log

sep "DONE"
