#!/bin/bash
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

error_function() {
    if [[ -p $logpipe ]]; then
        rm "$logpipe"
    fi
    # first exit all subshells, then print the error
    if (( ! BASH_SUBSHELL )); then
        error "A failure occurred in %s()." "$1"
        plain "Aborting..."
    fi
    umount_fs
    umount_img
    exit 2
}

run_safe() {
    local restoretrap func="$1"
    set -e
    set -E
    restoretrap=$(trap -p ERR)
    trap 'error_function $func' ERR

    if ${verbose}; then
        run_log "$func"
    else
        "$func"
    fi

    eval $restoretrap
    set +E
    set +e
}

check_umount() {
    if mountpoint -q "$1"; then
        umount -l "$1"
    fi
}

trap_exit() {
    local sig=$1; shift
    error "$@"
    umount_fs
    trap -- "$sig"
    kill "-$sig" "$$"
}

generate_motd() {
    # Kedra Linux is a downstream of CachyOS and says so. The upstream projects
    # below are credited deliberately -- Kedra does not rename or hide what it is
    # built on.
    #
    # No Kedra URL appears here: no Kedra domain has been chosen yet, and an
    # address that looks real but is not is worse than no address at all. The
    # ANSI colour matches kedra-hooks' placeholder and is not a final value.
    local _k=$'\033[38;2;23;147;209m' _r=$'\033[0m'
    cat << EOF > ${src_dir}/archiso/airootfs/etc/motd
Welcome to the ${_k}Kedra Linux${_r} live environment.

${_k}Kedra Linux${_r} is a downstream of CachyOS and Arch Linux. The live
environment will start now and let you install ${_k}Kedra Linux${_r} to disk.

Kedra ISO sources are derived from the CachyOS Live ISO:
https://github.com/CachyOS/CachyOS-Live-ISO

CachyOS:
https://cachyos.org

ArchLinux ISO Source:
https://gitlab.archlinux.org/archlinux/archiso

Calamares is used as GUI installer:
https://github.com/calamares/calamares
EOF
}

fetch_cachyos_mirrorlist() {
    mkdir -p ${src_dir}/archiso/airootfs/etc/pacman.d
    local _mirrorlist_url="https://github.com/CachyOS/CachyOS-PKGBUILDS/raw/master/cachyos-mirrorlist/cachyos-mirrorlist"

    curl -sSL "${_mirrorlist_url}" > ${src_dir}/archiso/airootfs/etc/pacman.d/cachyos-mirrorlist
}

change_grub_version() {
    local _version="$1"
    sed -i "s/KEDRA_VERSION=\".*\"/KEDRA_VERSION=\"${_version}\"/" ${src_dir}/archiso/grub/grub.cfg
}

generate_environment() {
    local _profile="$1"
    if [ "$_profile" == "desktop" ]; then
        cat << 'EOF' > ${src_dir}/archiso/airootfs/etc/environment
ZPOOL_VDEV_NAME_PATH=1
EOF
    fi
}

generate_version_tag() {
    local _profile="$1"
    local _version="$2"
    if [ "$_profile" == "desktop" ]; then
        echo "${_version}" > ${src_dir}/archiso/airootfs/etc/version-tag
    fi
}

generate_edition_tag() {
    local _edition="$1"
    echo "${_edition}" > ${src_dir}/archiso/airootfs/etc/edition-tag
}

##
## KEDRA: modify_mkarchiso() was REMOVED.
##
## Upstream disabled archlinux-keyring-wkd-sync.timer in the image by running
##     sudo sed -i /usr/bin/mkarchiso
## which permanently rewrites a file owned by the host's `archiso` package.
## That is a persistent, invisible change to host tooling: it survives the
## build, silently affects every other archiso profile on the machine, and is
## reverted at the next `pacman -Syu` without warning.
##
## Kedra achieves the same result from inside the profile by masking the timer
## (a /dev/null symlink in the image's /etc/systemd/system), which is the same
## mechanism upstream already uses for cachyos-rate-mirrors.timer and which
## outranks whatever `systemctl preset` enables during pacstrap. See
## prepare_profile(). The build host is never modified.
##

prepare_profile(){
    profile=$1

    info "Profile: [%s]" "${profile}"

    # KEDRA: ONE instant decides every name and stamp in this build.
    #
    # This function stamps the profile with `date +%y%m%d`; profiledef.sh
    # computes iso_version from SOURCE_DATE_EPOCH (falling back to `date`);
    # gen_iso_fn dated the output file; and the mv below dated the name
    # mkarchiso produced. Four independent readings of the clock, and a build
    # that crosses midnight -- this one did -- disagrees with itself: mkarchiso
    # wrote kedra-2026.08.30-x86_64.iso and the rename looked for
    # kedra-2026.08.31-x86_64.iso, which failed the build after two hours of
    # work with the finished image sitting in the output directory.
    #
    # SOURCE_DATE_EPOCH is the mechanism archiso already has for exactly this,
    # so it is pinned here, once, and everything else derives from it.
    export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(date +%s)}
    local _iso_version="$(date --date="@${SOURCE_DATE_EPOCH}" +%y%m%d)"
    change_grub_version "${_iso_version}"

    # Fetch up-to-date version of CachyOS repo mirrorlist
    fetch_cachyos_mirrorlist

    # mask cachyos-rate-mirrors timer
    ln -sf /dev/null ${src_dir}/archiso/airootfs/etc/systemd/system/cachyos-rate-mirrors.timer

    # KEDRA: mask archlinux-keyring-wkd-sync.timer in the IMAGE instead of
    # patching the host's /usr/bin/mkarchiso (see the note above). A masking
    # symlink beats the enablement `systemctl preset` performs during pacstrap,
    # so the timer never runs in the live session.
    ln -sf /dev/null ${src_dir}/archiso/airootfs/etc/systemd/system/archlinux-keyring-wkd-sync.timer

    generate_motd

    rm -f ${src_dir}/archiso/airootfs/etc/systemd/system/display-manager.service
    if [ "$profile" == "desktop" ]; then
        cp ${src_dir}/archiso/packages_desktop.x86_64 ${src_dir}/archiso/packages.x86_64
        # KEDRA: GNOME-first. GDM replaces plasma-login-manager as the live
        # display manager; /usr/local/bin/dmcheck already knows how to point
        # display-manager.service at gdm on the running system.
        ln -sf /usr/lib/systemd/system/gdm.service ${src_dir}/archiso/airootfs/etc/systemd/system/display-manager.service
    else
        die "Unknown profile: [%s]" "${profile}"
    fi

    generate_environment "${profile}"

    # Write out version to be able to check ISO version
    generate_version_tag "${profile}" "${_iso_version}"

    # Write out edition to be able to check ISO edition
    generate_edition_tag "${profile}"

    iso_file=$(gen_iso_fn).iso
}

##
## KEDRA: resolve build-host-specific paths in the COPY of the profile under
## ${work_dir}, never in the committed tree.
##
## Two things cannot be expressed as static text in a committed profile:
##
##   1. GPGDir -- must be an absolute path to the build-local keyring, so that
##      pacstrap verifies signatures against Kedra trust WITHOUT the host's
##      /etc/pacman.d/gnupg being modified. mkarchiso strips RootDir, DBPath,
##      CacheDir, HookDir and LogFile from the profile pacman.conf but leaves
##      GPGDir, which is what makes this override possible at all.
##
##   2. The [kedra] Server -- an absolute file:// path to the signed docroot,
##      because no Kedra hosting exists yet. A `Server =` line is used rather
##      than `Include = /etc/pacman.d/kedra-mirrorlist` on purpose: `pacman-conf`
##      resolves Include= against the BUILD HOST, so an include would have to
##      exist on every builder, and a dangling one silently breaks keyring
##      bootstrap (kedra-repo/docs/bootstrap.md, trap 2).
##
## MOVING TO REAL HOSTING: replace the two `Server =` lines with
## `https://<kedra-host>/$repo/os/$arch` in archiso/pacman.conf and
## archiso/airootfs/etc/pacman.conf, then delete this function and its call.
## Nothing else in the profile depends on the local docroot.
##
prepare_kedra_build_tree() {
    local _work_profile="${work_dir}/archiso"
    local _build_pacman_conf="${_work_profile}/pacman.conf"
    local _live_repo_dir="${_work_profile}/airootfs/usr/share/kedra/repo"

    [[ -d ${KEDRA_ISO_GPGDIR} ]] ||
        die "build keyring %s is missing -- run: fakeroot ./bin/kedra-iso-keyring-init" "${KEDRA_ISO_GPGDIR}"
    # Presence is not enough; SigLevel = Required needs a FULLY VALID key.
    gpg --homedir "${KEDRA_ISO_GPGDIR}" --no-permission-warning --list-keys \
        --with-colons 2>/dev/null | grep -q '^uid:[fu]:.*Kedra' ||
        die "build keyring %s does not fully trust Kedra -- re-run bin/kedra-iso-keyring-init" "${KEDRA_ISO_GPGDIR}"
    [[ -f ${KEDRA_REPO_DIR}/${KEDRA_REPO_NAME}.db ]] ||
        die "[kedra] docroot %s has no %s.db -- publish with kedra-repo/bin/kedra-repo-publish" "${KEDRA_REPO_DIR}" "${KEDRA_REPO_NAME}"

    msg2 "Pointing the build pacman.conf at the Kedra build keyring, cache and docroot"
    install -d -m 0755 "${KEDRA_ISO_CACHEDIR}"
    sed -i -e "s|@KEDRA_ISO_GPGDIR@|${KEDRA_ISO_GPGDIR}|g" \
           -e "s|@KEDRA_ISO_CACHEDIR@|${KEDRA_ISO_CACHEDIR}|g" \
           -e "s|@KEDRA_REPO_ROOT@|${KEDRA_REPO_ROOT}|g" "${_build_pacman_conf}"
    if grep -q '@KEDRA_' "${_build_pacman_conf}"; then
        die "unsubstituted Kedra placeholder left in %s" "${_build_pacman_conf}"
    fi

    # Ship the signed [kedra] docroot inside the image so the LIVE system can
    # resolve [kedra] with signatures verified and no network. Packages and
    # database only -- kedra-repo keeps all secret key material outside every
    # git tree and outside the docroot, so nothing signing-related is copied.
    msg2 "Staging the signed [kedra] docroot into the image"
    local _dest="${_live_repo_dir}/${KEDRA_REPO_NAME}/os/${KEDRA_REPO_ARCH}"
    install -d -m 0755 "${_dest}"

    # Copy exactly what the database names -- not `cp -a` of the whole docroot.
    #
    # The docroot is an append-only working directory: it keeps superseded
    # releases (kedra-calamares 3.4.2-1 is still beside 3.4.2-2) and repo-add
    # leaves kedra.db.tar.zst.old backups behind. Copying all of it puts a
    # SECOND, older signed database and a package release known to be defective
    # inside an image that is supposed to be one immutable baseline. pacman
    # would never select them -- kedra.db names only the current release -- but
    # an image that ships the artifact whose staleness this milestone exists to
    # correct is not a baseline anyone can reason about.
    #
    # %FILENAME% in the database is the authority on what belongs in the image.
    local _f
    while IFS= read -r _f; do
        [[ -n ${_f} ]] || continue
        [[ -f ${KEDRA_REPO_DIR}/${_f} ]] ||
            die "%s names %s, which is not in the docroot" "${KEDRA_REPO_NAME}.db" "${_f}"
        [[ -f ${KEDRA_REPO_DIR}/${_f}.sig ]] ||
            die "%s has no detached signature in the docroot" "${_f}"
        install -m 0644 "${KEDRA_REPO_DIR}/${_f}"     "${_dest}/${_f}"
        install -m 0644 "${KEDRA_REPO_DIR}/${_f}.sig" "${_dest}/${_f}.sig"
    done < <(bsdtar -xOf "${KEDRA_REPO_DIR}/${KEDRA_REPO_NAME}.db" --include '*/desc' |
             awk '/^%FILENAME%/{getline f; print f}')

    # The database and file list themselves, dereferenced: in the docroot these
    # are symlinks to the .tar.zst, and a symlink whose target is not copied
    # would be a dangling [kedra] on the live system.
    local _m
    for _m in "${KEDRA_REPO_NAME}.db" "${KEDRA_REPO_NAME}.db.sig" \
              "${KEDRA_REPO_NAME}.files" "${KEDRA_REPO_NAME}.files.sig"; do
        [[ -e ${KEDRA_REPO_DIR}/${_m} ]] ||
            die "[kedra] docroot is missing %s" "${_m}"
        cp -L "${KEDRA_REPO_DIR}/${_m}" "${_dest}/${_m}"
        chmod 0644 "${_dest}/${_m}"
    done

    (( $(find "${_dest}" -name '*.pkg.tar.*' -not -name '*.sig' -printf '.' | wc -c) > 0 )) ||
        die "no packages were staged into the image's [kedra] docroot"
    if find "${_live_repo_dir}" \( -name '*.gpg' -o -name '*.key' -o -name 'secring*' \) -print | grep -q .; then
        die "secret-looking key material found in the staged [kedra] docroot"
    fi
    msg2 "Staged $(find "${_dest}" -name '*.pkg.tar.*' -not -name '*.sig' -printf '.' | wc -c) package(s) named by ${KEDRA_REPO_NAME}.db"
}

##
## KEDRA: privilege model for the build.
##
## archiso >= 88 can build an ISO entirely without root: when mkarchiso runs as
## an unprivileged user it wraps every privileged step in
## `unshare --map-auto --map-root-user` and passes `-N` to pacstrap. That needs
## unprivileged user namespaces plus a subuid/subgid range for the build user.
##
## Kedra prefers that path, because the alternative -- `sudo mkarchiso` -- runs
## a large build as real root against a tree full of downloaded content. Falling
## back to sudo keeps the profile usable on hosts without user namespaces (some
## hardened kernels, older CI images).
##
## Set KEDRA_ROOTLESS_BUILD=0 to force the sudo path, or 1 to demand rootless.
kedra_detect_rootless() {
    if [[ -n ${KEDRA_ROOTLESS_BUILD:-} ]]; then
        [[ ${KEDRA_ROOTLESS_BUILD} == 1 ]] && rootless_build=true || rootless_build=false
        return
    fi
    rootless_build=false
    (( EUID == 0 )) && return
    grep -q "^${USER}:" /etc/subuid 2>/dev/null || return
    grep -q "^${USER}:" /etc/subgid 2>/dev/null || return
    unshare --map-auto --map-root-user -- true >/dev/null 2>&1 && rootless_build=true
}

# Delete a build tree. In rootless mode its files belong to mapped subuids that
# the invoking user cannot unlink directly, so removal has to happen inside the
# same kind of namespace that created them.
kedra_rm_build_tree() {
    local _dir="$1"
    [[ -d ${_dir} ]] || return 0
    if ${rootless_build}; then
        unshare --map-auto --map-root-user -- rm -rf -- "${_dir}"
    else
        sudo rm -rf -- "${_dir}"
    fi
}

run_build() {
    kedra_detect_rootless
    if ${rootless_build}; then
        msg "Build privilege model: rootless (user namespaces); sudo is not used"
    else
        warning "Build privilege model: sudo (user namespaces unavailable)"
    fi

    prepare_profile "$1"
    local _profile="$1"

    msg "Prepare [work: ${work_dir}, out: ${outFolder}]"

    # KEDRA: upstream made mkarchiso verbose with `sudo sed -i /usr/bin/mkarchiso`,
    # another persistent edit to a host-owned file. `mkarchiso -v` is already
    # passed unconditionally below, so nothing is lost by dropping it.

    if $clean_first; then
        msg2 "Deleting the build folder if one exists - takes some time"
        umount_fs
        kedra_rm_build_tree "${work_dir}"
    fi

    msg2 "Copying the Archiso folder to build work"
    mkdir -p ${work_dir}

    # KEDRA: the work directory must not inherit POSIX ACLs from the host.
    #
    # A parent directory with a DEFAULT ACL -- ~/Projects shared with a second
    # local account, say -- gives every file created underneath it an access ACL
    # naming uids that do not exist inside the build's user namespace. `unshare
    # --map-auto` maps this user to 0 and the /etc/subuid range to 1..65535;
    # a second account's uid maps to nothing at all.
    #
    # mkarchiso then copies /etc/skel into the live user's home with
    # `cp --preserve=mode`, which tries to reproduce that ACL, fails with EINVAL
    # ("cp: preserving permissions ...: Invalid argument") and -- running under
    # set -e -- aborts the whole build, after an hour of pacstrap, at the very
    # last customisation step.
    #
    # Stripping the work directory's own ACLs is enough and is done here rather
    # than by patching the host's mkarchiso: ACL inheritance happens at creation
    # time, so with no default ACL on ${work_dir} nothing created below it
    # carries one. The committed profile and the output directory are untouched.
    if command -v setfacl >/dev/null 2>&1; then
        setfacl -b -k "${work_dir}" 2>/dev/null || true
    fi

    cp -r archiso ${work_dir}/archiso

    # KEDRA: resolve build-host-specific paths and stage [kedra] into the copy,
    # so the committed profile stays free of absolute developer paths.
    prepare_kedra_build_tree

    msg "Start [Build ISO]"

    [ -d "$outFolder/$_profile" ] || mkdir -p "$outFolder/$_profile"
    cd ${work_dir}/archiso/
    if ${rootless_build}; then
        # mkarchiso must see EUID != 0 here: that is what makes it wrap each
        # privileged step in its own namespace and pass `pacstrap -N`. Running
        # the whole of mkarchiso inside one namespace instead would take the
        # root code path and fail when pacstrap tries to bind-mount /proc.
        # xorriso runs outside those namespaces, so the finished ISO is already
        # owned by the invoking user and needs no chown.
        mkarchiso -v -w ${work_dir} -o "$outFolder/$_profile" ${work_dir}/archiso/
    else
        sudo mkarchiso -v -w ${work_dir} -o "$outFolder/$_profile" ${work_dir}/archiso/
        sudo chown $USER $outFolder
    fi

    cp ${work_dir}/iso/arch/pkglist.x86_64.txt "$outFolder/$_profile/$(gen_iso_fn).pkgs.txt"
    # mkarchiso names the image "${iso_name}-${iso_version}-${arch}.iso".
    mv "$outFolder/$_profile/kedra-$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)-x86_64.iso" "$outFolder/$_profile/${iso_file}"

    msg "Done [Build ISO] ${iso_file}"
    msg "Finished building [%s]" "${_profile}"

    cd "$outFolder/$_profile"
    for f in $(find . -maxdepth 1 -name '*.iso' | cut -d'/' -f2); do
        if [[ ! -e $f.sha256 ]]; then
            create_chksums $f
        elif [[ $f -nt $f.sha256 ]]; then
            create_chksums $f
        else
            info "checksums for [$f] already created"
        fi
        if [[ ! -e $f.sig ]]; then
            sign_with_key $f
        elif [[ $f -nt $f.sig ]]; then
            rm $f.sig
            sign_with_key $f
        else
            info "signature file for [$f] already created"
        fi
    done
    show_elapsed_time "${FUNCNAME}" "${timer_start}"
    if [[ "$build_in_ram" == "true" && "$remove_build_dir" == "false" ]]; then
        msg "!!! Remember to remove $work_dir !!!"
        msg2 "./buildiso.sh cleans it, or: unshare --map-auto --map-root-user -- rm -rf $work_dir"
    fi
    if [[ "$remove_build_dir" == "true" ]]; then
        msg "Automatically removing build directory ($work_dir)..."
        umount_fs
        kedra_rm_build_tree "${work_dir}"
        msg2 "Removed"
    fi
}

gen_iso_fn(){
    local vars=() name
    vars+=("kedra")
    [[ -n ${profile} ]] && vars+=("${profile}")

    vars+=("linux")
    # The same instant prepare_profile stamped the image with; see the note
    # there. Never a fresh reading of the clock.
    vars+=("$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%y%m%d)")

    for n in ${vars[@]}; do
        name=${name:-}${name:+-}${n}
    done

    echo $name
}
