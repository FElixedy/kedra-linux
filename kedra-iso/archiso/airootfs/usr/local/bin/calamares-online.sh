#!/bin/bash

main() {
    # Remove current keyring first, to complete initiate it
    sudo rm -rf /etc/pacman.d/gnupg
    # KEDRA: the original order here was `pacman -Sy` first, then `pacman-key
    # --init`/`--populate`. With the keyring just deleted, that leaves the live
    # system with NO trust at all during the -Sy. [kedra] ships a signed
    # kedra.db, and DatabaseOptional only tolerates a MISSING signature -- it
    # still verifies one that is present -- so the sync aborts with
    # `key "..." is unknown` before any keyring package can be installed.
    # (kedra-repo/docs/bootstrap.md documents this exact failure.)
    #
    # Initialising and populating BEFORE the sync fixes it. `--populate` with no
    # arguments loads every keyring in /usr/share/pacman/keyrings, so Arch,
    # CachyOS and Kedra trust are all restored without naming them twice.
    sudo pacman-key --init
    sudo pacman-key --populate
    # We are using this, because archlinux is signing the keyring often with a newly created keyring
    # This results into a failed installation for the user.
    # Installing archlinux-keyring fails due not being correctly signed
    # Mitigate this by installing the latest archlinux-keyring on the ISO, before starting the installation
    # The issue could also happen, when the installation does rank the mirrors and then a "faulty" mirror gets used
    sudo pacman -Sy --noconfirm archlinux-keyring cachyos-keyring kedra-keyring
    # Re-populate so keys added by the refreshed keyring packages are trusted too.
    sudo pacman-key --populate
    # Also use timedatectl to sync the time with the hardware clock
    # There has been a bunch of reports, that the keyring was created in the future
    # Syncing appears to fix it
    timedatectl set-ntp true

    local progname="$(basename "$0")"
    # KEDRA: was /home/liveuser/cachy-install.log.
    local log="/home/liveuser/kedra-install.log"
    local mode="online"  # TODO: keep this line for now

    local SYSTEM=""

    if [ -d /sys/firmware/efi ]; then
        SYSTEM="UEFI SYSTEM"
    else
        SYSTEM="BIOS/MBR SYSTEM"
    fi

    local ISO_VERSION="$(cat /etc/version-tag)"
    echo "USING ISO VERSION: ${ISO_VERSION}"

    # KEDRA: was cachyos-calamares-next. The Kedra installer is a downstream fork
    # of it (see the kedra-calamares repository) and resolves from [kedra].
    #
    # Upstream refreshes the installer package here, on launch, rather than
    # relying on the copy baked into the image -- so a packaging fix can reach
    # users without a new ISO. That is worth keeping, and it is also why this line
    # has to change in step with archiso/packages*.x86_64: with the old name here,
    # pacman would install the CachyOS installer alongside the Kedra one, and the
    # two conflict through the virtual `calamares` name they both provide.
    sudo pacman -Sy --noconfirm kedra-calamares

    # Get Hardware Informations
    inxi -F > "$log"

    cat <<EOF >> "$log"
########## $log by $progname
########## Started (UTC): $(date -u "+%x %X")
########## ISO version: $ISO_VERSION
########## System: $SYSTEM
EOF

    sudo cp "/usr/share/calamares/settings_${mode}.conf" /etc/calamares/settings.conf
    exec pkexec-wrapper calamares -D6 >> $log
}

main "$@"
