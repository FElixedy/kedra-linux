#!/bin/sh
# Verification run INSIDE the INSTALLED Kedra system, after the ISO has been
# detached and the machine cold-booted from its own disk.
#
# Run via `kedra-vmctl sh "$(cat tests/kedra-installed-checks.sh)"`, or delivered
# any other way -- it needs nothing but a shell and root.
#
# Read-only. It never upgrades, never edits a repository and never installs
# anything; the full `pacman -Syu` is a separate, deliberate step in the
# acceptance run, not something a check script should do behind the operator.

exec 2>&1
sep() { printf '\n===== %s =====\n' "$*"; }
FAILED=0
verdict() { # verdict <condition-result> <label>
    if [ "$1" = 0 ]; then printf 'PASS  %s\n' "$2"
    else printf 'FAIL  %s\n' "$2"; FAILED=$((FAILED + 1)); fi
}

sep "settle"
systemctl is-system-running --wait >/dev/null 2>&1
echo "system state: $(systemctl is-system-running)"
echo "uptime: $(uptime -p 2>/dev/null)"
echo "booted from: $(findmnt -no SOURCE / 2>/dev/null)"

sep "identity: ID must be kedra"
cat /etc/os-release
. /etc/os-release
[ "$ID" = kedra ]; verdict $? "ID=kedra (got '$ID')"
echo "NAME=$NAME  PRETTY_NAME=$PRETTY_NAME"
echo "-- owner of /etc/os-release:"; pacman -Qo /etc/os-release 2>&1

sep "no real cachyos-hooks package"
# `pacman -Q cachyos-hooks` resolves PROVIDES and answers kedra-hooks, which is
# correct but ambiguous. The exact question is whether a package literally
# named cachyos-hooks is in the local database.
pacman -Qq | grep -qx cachyos-hooks; [ $? -ne 0 ]; verdict $? "no package literally named cachyos-hooks"
echo "-- what satisfies the name: $(pacman -Q cachyos-hooks 2>&1)"
pacman -Qq | grep -i hooks

sep "kernel: linux-cachyos"
uname -r
pacman -Qq | grep -E '^linux-cachyos' || echo "(no linux-cachyos package)"
uname -r | grep -q cachyos; verdict $? "running kernel is a CachyOS kernel ($(uname -r))"
ls -l /boot/vmlinuz* 2>&1

sep "firmware boot entry (EFI)"
efibootmgr -v 2>&1 | head -20
efibootmgr 2>/dev/null | grep -qi 'kedra'; verdict $? "an EFI boot entry named Kedra exists"
echo "-- ESP contents:"; ls -R /boot/EFI 2>/dev/null | head -20 || ls -R /efi/EFI 2>/dev/null | head -20

sep "GRUB menu entry name -- must NOT read 'Kedra Linux Linux'"
GRUBCFG=$(ls /boot/grub/grub.cfg 2>/dev/null || ls /boot/grub2/grub.cfg 2>/dev/null)
echo "grub.cfg: ${GRUBCFG:-(not found)}"
if [ -n "$GRUBCFG" ]; then
    echo "-- menuentry titles:"
    grep -E "^\s*(menuentry|submenu)" "$GRUBCFG" | sed -E "s/^\s*//" | cut -c1-100
    echo "-- GRUB_DISTRIBUTOR in /etc/default/grub:"
    grep -E '^GRUB_DISTRIBUTOR' /etc/default/grub 2>&1
    grep -qi 'Kedra Linux Linux' "$GRUBCFG"; [ $? -ne 0 ]
    verdict $? "grub.cfg contains no 'Kedra Linux Linux'"
    grep -qE "^\s*menuentry '?\"?Kedra" "$GRUBCFG"; verdict $? "grub.cfg has a menuentry starting with 'Kedra'"
else
    verdict 1 "grub.cfg found"
fi

sep "filesystem is Btrfs"
findmnt -no FSTYPE /; findmnt -no FSTYPE / | grep -qx btrfs; verdict $? "/ is Btrfs"
btrfs subvolume list / 2>&1 | head -10
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT 2>&1

sep "graphical stack: GDM / GNOME"
echo "display-manager -> $(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null)"
echo "default target:    $(systemctl get-default)"
systemctl is-active gdm; systemctl is-active gdm >/dev/null 2>&1; verdict $? "gdm is active"
systemctl is-enabled gdm 2>&1
pgrep -a gnome-shell | head -3
pgrep -a gdm | head -3
loginctl list-sessions --no-legend 2>&1

sep "NetworkManager"
systemctl is-active NetworkManager; systemctl is-active NetworkManager >/dev/null 2>&1
verdict $? "NetworkManager is active"
nmcli -t -f STATE,CONNECTIVITY general 2>&1
nmcli -t -f DEVICE,TYPE,STATE device 2>&1
ping -c2 -W4 1.1.1.1 2>&1 | tail -2
curl -sS -m 20 -o /dev/null -w 'archlinux.org http=%{http_code}\n' https://archlinux.org 2>&1

sep "repositories, in order"
pacman-conf --repo-list
for want in kedra cachyos core extra; do
    pacman-conf --repo-list | grep -qx "$want"; verdict $? "[$want] is configured"
done

sep "effective SigLevel per repository -- nothing may weaken validation"
_global=$(pacman-conf SigLevel | tr '\n' ' ')
printf '%-14s %s\n' "(global)" "$_global"
_weak=0
for r in $(pacman-conf --repo-list); do
    _lvl=$(pacman-conf --repo="$r" SigLevel | tr '\n' ' ')
    printf '%-14s %s\n' "$r" "${_lvl:-(inherits global)}"
    [ -n "$_lvl" ] || _lvl="$_global"
    case "$_lvl" in *PackageRequired*) ;; *) echo "  WEAK: no signature required"; _weak=1 ;; esac
    case "$_lvl" in *PackageTrustedOnly*) ;; *) echo "  WEAK: untrusted keys accepted"; _weak=1 ;; esac
done
printf '%-14s %s\n' "(localfile)" "$(pacman-conf LocalFileSigLevel | tr '\n' ' ')"
[ "$_weak" = 0 ]; verdict $? "every repository requires a trusted package signature"

sep "installed Kedra packages"
pacman -Q kedra-hooks kedra-keyring kedra-calamares 2>&1
echo "-- Kedra keys in the system keyring:"
pacman-key --list-keys 2>/dev/null | grep -i -A1 'Kedra' | head -10

sep "signed repository operation (sync only, no upgrade)"
pacman -Sy --noconfirm 2>&1 | tail -12
verdict $? "pacman -Sy succeeded with signatures enforced"
for p in kedra-hooks kedra-calamares; do
    echo "-- $p:"; pacman -Si "$p" 2>&1 | grep -E '^(Repository|Name|Version)'
done
echo "-- CachyOS package still from [cachyos]:"
pacman -Si linux-cachyos 2>&1 | grep -E '^(Repository|Name|Version)'
echo "-- Arch package still from core/extra:"
pacman -Si bash 2>&1 | grep -E '^(Repository|Name|Version)'

sep "boot-critical failed units"
systemctl --failed --no-legend 2>&1
N=$(systemctl --failed --no-legend 2>/dev/null | grep -c . || true)
echo "failed unit count: $N"
[ "${N:-0}" = 0 ]; verdict $? "zero failed units"

sep "SUMMARY"
echo "checks failed: $FAILED"
[ "$FAILED" = 0 ] && echo "RESULT: ALL PASS" || echo "RESULT: $FAILED CHECK(S) FAILED"
sep "DONE"
