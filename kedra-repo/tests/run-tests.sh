#!/usr/bin/env bash
#
# Kedra repository / signing / bootstrap test suite.
#
# Everything runs inside unprivileged user namespaces (`unshare -r --map-auto`)
# against pacman roots under $KEDRA_TEST_DIR. The host's /etc/pacman.conf,
# /etc/pacman.d/gnupg and installed package set are never written to; host
# keyrings and mirrorlists are only ever READ.
#
#   ./run-tests.sh          run everything
#   ./run-tests.sh -k       keep the test roots afterwards
#
# The base root is a real pacman installation (~110 MiB, downloaded once and
# cached between runs) so that install scriptlets -- including the one in
# kedra-keyring that establishes trust -- genuinely execute.

set -uo pipefail

BIN_DIR=$(readlink -f "$(dirname "$(readlink -f "$0")")/../bin")
source "${BIN_DIR}/_common.sh"
set +e

KEYRING_SRC=$(readlink -f "${BIN_DIR}/../../kedra-keyring")

# GnuPG's agent socket lives inside the gpgdir and AF_UNIX paths are capped at
# ~108 bytes, so the test tree has to sit on a short path.
KEDRA_TEST_DIR=${KEDRA_TEST_DIR:-/tmp/kedra-repo-tests}
KEEP=0
[[ ${1:-} == -k ]] && KEEP=1

SELFTEST_SRC=$(dirname "$(readlink -f "$0")")/kedra-repo-selftest

pass=0; fail=0
declare -a RESULTS

check() { # check <name> <status> [detail]
	if (( $2 == 0 )); then
		RESULTS+=("PASS  $1"); (( pass++ ))
		printf '\033[1;32mPASS\033[0m  %s\n' "$1"
	else
		RESULTS+=("FAIL  $1${3:+  <<${3}>>}"); (( fail++ ))
		printf '\033[1;31mFAIL\033[0m  %s%s\n' "$1" "${3:+  <<${3}>>}"
	fi
}

section() { printf '\n\033[1;36m### %s\033[0m\n' "$*"; }
lastline() { printf '%s' "${1##*$'\n'}"; }

# Root inside a user namespace. Files created as non-zero uids land in our
# subuid range, so removal has to happen the same way.
ns()   { unshare -r --map-auto "$@"; }
nsrm() { ns rm -rf "$@" 2>/dev/null; }

# Root inside a user namespace + a PRIVATE mount namespace, with /dev, /proc
# and /sys bind-mounted into the target root.
#
# This is not test scaffolding for its own sake: libalpm runs install scriptlets
# chrooted into the target, and pacman-key needs /proc there. Without it,
# kedra-keyring's scriptlet fails its own `pacman-key -l` guard and SILENTLY
# does not establish trust. Real installers (pacstrap, Calamares) mount these;
# the test has to as well or it would not be modelling them.
nsmount() { # nsmount <root> <cmd...>
	unshare -r --map-auto -m --propagation private /bin/sh -c '
		root=$1; shift
		if [ -d "$root/usr/bin" ]; then
			mkdir -p "$root/dev" "$root/proc" "$root/sys"
			mount --rbind /dev  "$root/dev"  2>/dev/null
			mount --rbind /proc "$root/proc" 2>/dev/null
			mount --rbind /sys  "$root/sys"  2>/dev/null
		fi
		exec "$@"' _ "$@"
}

# pacman against an isolated root.
#   PAC_GPGDIR  overrides the keyring used for VERIFICATION (models pacstrap
#               verifying with the live ISO keyring while writing to a target).
#   PAC_CACHE   overrides the package cache (negative tests must not be served
#               a good package that an earlier test already downloaded).
pac() { # pac <root> <args...>
	local root=$1
	nsmount "$root" pacman \
		--root "${root}" \
		--dbpath "${root}/var/lib/pacman" \
		--cachedir "${PAC_CACHE:-${KEDRA_TEST_DIR}/cache}" \
		--gpgdir "${PAC_GPGDIR:-${root}/etc/pacman.d/gnupg}" \
		--config "${root}/etc/pacman.conf" \
		--logfile "${root}/pacman.log" \
		--noconfirm "${@:2}" 2>&1
}

pkey() { # pkey <gpgdir> <args...>
	local gpgdir=$1; shift
	ns pacman-key --gpgdir "${gpgdir}" "$@" 2>&1
}

gpgq() { # gpgq <gpgdir> <args...>   -- read a keyring
	local gpgdir=$1; shift
	ns gpg --homedir "${gpgdir}" --batch --no-tty "$@" 2>/dev/null
}

# write_conf <root> <kedra_server_url|-> <upstream:0|1>
write_conf() {
	local root=$1 kedra_url=$2 upstream=$3
	mkdir -p "${root}/etc"
	{
		cat <<-EOF
		[options]
		Architecture = x86_64
		HoldPkg = pacman glibc
		# Kedra never uses SigLevel = Never. Package signatures are mandatory.
		SigLevel = Required DatabaseOptional
		# Deliberately strict: even a bare \`pacman -U file.pkg.tar.zst\` must be
		# signed, so no test can accidentally pass through an unsigned path.
		LocalFileSigLevel = Required
		EOF
		if [[ ${kedra_url} != - ]]; then
			cat <<-EOF

			# [kedra] first: repository order is how Kedra takes ownership of a name.
			[kedra]
			SigLevel = Required DatabaseOptional
			Server = ${kedra_url}
			EOF
		fi
		if (( upstream )); then
			# Include paths are absolute. Outside the chroot they resolve to the
			# host's mirrorlists (read-only); inside the chroot -- which is where
			# install scriptlets run pacman-key -- they must resolve inside the
			# root, so the host lists are copied in below.
			cat <<-'EOF'

			[cachyos]
			Include = /etc/pacman.d/cachyos-mirrorlist
			[core]
			Include = /etc/pacman.d/mirrorlist
			[extra]
			Include = /etc/pacman.d/mirrorlist
			EOF
		fi
	} >"${root}/etc/pacman.conf"
	if (( upstream )); then
		mkdir -p "${root}/etc/pacman.d"
		cp /etc/pacman.d/mirrorlist /etc/pacman.d/cachyos-mirrorlist \
			"${root}/etc/pacman.d/"
	fi
}

KEDRA_URL="file://${KEDRA_REPO_ROOT}/\$repo/os/\$arch"

new_root() { # new_root <path> <kedra_url|-> <upstream:0|1> <trust_kedra:0|1>
	nsrm "$1"; mkdir -p "$1/var/lib/pacman" "$1/etc/pacman.d/gnupg"
	write_conf "$1" "$2" "$3"
	pkey "$1/etc/pacman.d/gnupg" --init >/dev/null
	(( $4 )) && pkey "$1/etc/pacman.d/gnupg" \
		--populate-from "${KEYRING_SRC}" --populate kedra >/dev/null
	return 0
}

mkdir -p "${KEDRA_TEST_DIR}/cache"
PAC_CACHE=""; PAC_GPGDIR=""

###############################################################################
section "0  preconditions"
###############################################################################

[[ -d ${KEDRA_REPO_DIR} ]]
check "published repository exists at ${KEDRA_REPO_DIR}" $?

###############################################################################
section "1  repository creation and metadata"
###############################################################################

for f in kedra.db kedra.db.sig kedra.files kedra.files.sig; do
	[[ -e ${KEDRA_REPO_DIR}/${f} ]]
	check "repository metadata present: ${f}" $?
done

R1="${KEDRA_TEST_DIR}/r1-metadata"
new_root "$R1" "${KEDRA_URL}" 0 1
check "isolated keyring initialised and populated with kedra" $?

out=$(pac "$R1" -Sy)
check "pacman syncs the [kedra] database (signed db verified)" $? "$(lastline "$out")"

out=$(pac "$R1" -Sl kedra)
grep -q '^kedra kedra-hooks '   <<<"$out"; check "kedra-hooks listed in [kedra]"   $?
grep -q '^kedra kedra-keyring ' <<<"$out"; check "kedra-keyring listed in [kedra]" $?

out=$(pac "$R1" -Si kedra-hooks)
repo=$(awk -F': *' '/^Repository/ {print $2; exit}' <<<"$out")
[[ ${repo} == kedra ]]
check "pacman -Si kedra-hooks reports Repository = kedra (got: ${repo:-none})" $?

###############################################################################
section "2  signature validation"
###############################################################################

# 2a  valid signature + trusted key -> accepted
out=$(pac "$R1" -Sdd kedra-hooks)
ns test -f "$R1/usr/share/libalpm/hooks/os-release.hook"
check "signed kedra-hooks installs from [kedra]" $? "$(lastline "$out")"

# 2b  identical repository, keyring without the Kedra trust root -> rejected
R2="${KEDRA_TEST_DIR}/r2-untrusted"
new_root "$R2" "${KEDRA_URL}" 0 0
PAC_CACHE="${R2}/cache"; mkdir -p "$PAC_CACHE"
out=$(pac "$R2" -Sy; pac "$R2" -Sdd kedra-hooks)
! ns test -f "$R2/usr/share/libalpm/hooks/os-release.hook"
check "without the Kedra trust root the same package is REJECTED" $? "$(lastline "$out")"
PAC_CACHE=""

# Helper: build a mutated copy of the repository. The database is rebuilt (and
# left unsigned) so that package checksums match and only the PACKAGE signature
# is at fault -- otherwise pacman would reject on the checksum instead and the
# test would prove nothing about signatures.
make_bad_repo() { # make_bad_repo <name> <mutator-fn>
	local name=$1 mutate=$2
	local dir="${KEDRA_TEST_DIR}/bad-${name}/kedra/os/x86_64"
	nsrm "${KEDRA_TEST_DIR}/bad-${name}"; mkdir -p "$dir"
	cp "${KEDRA_REPO_DIR}"/*.pkg.tar.zst "${KEDRA_REPO_DIR}"/*.pkg.tar.zst.sig "$dir"/
	"$mutate" "$dir"
	( cd "$dir" && repo-add --quiet kedra.db.tar.zst ./*.pkg.tar.zst >/dev/null 2>&1 )
	printf '%s' "$dir"
}

run_bad_repo() { # run_bad_repo <label> <dir>
	local label=$1 dir=$2
	local root="${KEDRA_TEST_DIR}/r-bad-${label}"
	new_root "$root" "file://${dir%/kedra/os/x86_64}/\$repo/os/\$arch" 0 1
	PAC_CACHE="${root}/cache"; mkdir -p "$PAC_CACHE"
	local out; out=$(pac "$root" -Sy; pac "$root" -Sdd kedra-hooks)
	PAC_CACHE=""
	! ns test -f "$root/usr/share/libalpm/hooks/os-release.hook"
	local rc=$?
	printf '%s\n' "$rc" "$(lastline "$out")"
}

# 2c  payload tampered after signing -> signature no longer matches
mutate_tamper() { printf 'TAMPERED' | dd of="$(ls "$1"/kedra-hooks-*.pkg.tar.zst)" \
	bs=1 seek=512 conv=notrunc status=none; }
mapfile -t r < <(run_bad_repo tampered "$(make_bad_repo tampered mutate_tamper)")
check "package tampered with after signing is REJECTED" "${r[0]}" "${r[1]}"

# 2d  signature removed entirely -> rejected by SigLevel = Required
mutate_unsign() { rm -f "$1"/kedra-hooks-*.pkg.tar.zst.sig; }
mapfile -t r < <(run_bad_repo unsigned "$(make_bad_repo unsigned mutate_unsign)")
check "unsigned package is REJECTED under SigLevel = Required" "${r[0]}" "${r[1]}"

# 2e  validly signed, but by a key outside the Kedra trust root
ROGUE="${KEDRA_TEST_DIR}/rogue-gnupg"
nsrm "$ROGUE"; mkdir -p "$ROGUE"; chmod 700 "$ROGUE"
gpg --homedir "$ROGUE" --batch --no-tty --pinentry-mode loopback --passphrase '' \
	--quick-generate-key 'Rogue Test Signer <rogue@kedra.invalid>' rsa2048 sign 1d \
	>/dev/null 2>&1
mutate_rogue() {
	local p
	for p in "$1"/*.pkg.tar.zst; do
		gpg --homedir "$ROGUE" --batch --no-tty --pinentry-mode loopback \
			--passphrase '' --yes --detach-sign --no-armor \
			--output "${p}.sig" "$p" >/dev/null 2>&1
	done
}
mapfile -t r < <(run_bad_repo rogue "$(make_bad_repo rogue mutate_rogue)")
check "package signed by an untrusted key is REJECTED" "${r[0]}" "${r[1]}"

###############################################################################
section "3  publishing policy (allowlist)"
###############################################################################

build_selftest() { # build_selftest <pkgrel> <pkgname> [payload] -> package path
	local pkgrel=$1 name=$2 payload=${3:-default}
	local wd="${KEDRA_TEST_DIR}/build/${name}-${pkgrel}-${payload}"
	nsrm "$wd"; mkdir -p "$wd"
	sed -e "s/@PKGNAME@/${name}/" -e "s/@PKGREL@/${pkgrel}/" \
		-e "s/@PAYLOAD@/${payload}/" \
		"${SELFTEST_SRC}/PKGBUILD.in" >"$wd/PKGBUILD"
	( cd "$wd" && PKGDEST="$wd" BUILDDIR="$wd/b" makepkg -f --nodeps --noconfirm \
		>/dev/null 2>&1 )
	ls "$wd"/*.pkg.tar.zst
}

notallowed=$(build_selftest 1 kedra-not-allowed)
out=$("${BIN_DIR}/kedra-repo-publish" "$notallowed" 2>&1); rc=$?
(( rc != 0 )) && grep -q 'kedra-repo-packages.txt' <<<"$out"
check "publishing a package outside the allowlist is refused" $? "$(lastline "$out")"

###############################################################################
section "3b  immutable package identity"
###############################################################################
#
# A published artifact never changes. Once (pkgname, pkgver-pkgrel, arch) is in
# the docroot the bytes behind it are fixed: a Kedra system that has synced
# kedra.db has cached that checksum, so replacing the bytes under an unchanged
# version does not produce an upgrade, it produces a repository that disagrees
# with itself -- and it invalidates every provenance record naming that version.

IMROOT="${KEDRA_TEST_DIR}/immutable-repo"
nsrm "$IMROOT"; mkdir -p "${IMROOT}/kedra/os/x86_64"
IMDIR="${IMROOT}/kedra/os/x86_64"

# Fingerprint of everything the repository serves. Any difference in any file --
# artifact, signature, kedra.db, kedra.files -- changes this.
im_snapshot() { find "$IMDIR" -type f -exec sha256sum {} + 2>/dev/null | sort; }
im_file_sha() { sha256sum "$IMDIR/$1" 2>/dev/null | cut -d' ' -f1; }

im_publish() { KEDRA_REPO_ROOT="${IMROOT}" "${BIN_DIR}/kedra-repo-publish" "$@" 2>&1; }

im_a=$(build_selftest 1 kedra-repo-selftest alpha)
im_base=$(basename "$im_a")

out=$(im_publish "$im_a"); rc=$?
(( rc == 0 )) && [[ -f "$IMDIR/$im_base" && -f "$IMDIR/$im_base.sig" ]]
check "first publication of an identity succeeds" $? "$(lastline "$out")"

after_first=$(im_snapshot)
art_sha=$(im_file_sha "$im_base"); sig_sha=$(im_file_sha "$im_base.sig")
db_sha=$(im_file_sha "kedra.db.tar.zst"); files_sha=$(im_file_sha "kedra.files.tar.zst")

# Re-publishing the very same bytes is an explicit no-op: nothing copied,
# nothing re-signed, and kedra.db not rewritten -- so it is also not re-signed.
out=$(im_publish "$im_a"); rc=$?
(( rc == 0 )) && grep -q 'already published identically' <<<"$out"
check "identical re-publication is an explicit no-op" $? "$(lastline "$out")"

[[ $(im_snapshot) == "$after_first" ]]
check "identical re-publication does not mutate the repository at all" $?

# Same identity, different bytes. This is the case the rule exists for.
im_b=$(build_selftest 1 kedra-repo-selftest beta)
[[ $(sha256sum "$im_b" | cut -d' ' -f1) != "$art_sha" ]]
check "the fixture really is the same identity with different bytes" $?

out=$(im_publish "$im_b"); rc=$?
(( rc != 0 )) && grep -qi 'IMMUTABLE PACKAGE IDENTITY' <<<"$out"
check "same version with a different payload is REFUSED" $? "$(lastline "$out")"

grep -qi 'bump pkgrel' <<<"$out"
check "the refusal says to bump pkgrel rather than overwrite" $?

# The whole point of failing in a validation pass: nothing may have been written
# before the refusal. Checked file by file as well as in aggregate, so a failure
# names what moved.
[[ $(im_file_sha "$im_base") == "$art_sha" ]]
check "refused publication left the package artifact untouched" $?
[[ $(im_file_sha "$im_base.sig") == "$sig_sha" ]]
check "refused publication left the signature untouched" $?
[[ $(im_file_sha "kedra.db.tar.zst") == "$db_sha" ]]
check "refused publication left kedra.db untouched" $?
[[ $(im_file_sha "kedra.files.tar.zst") == "$files_sha" ]]
check "refused publication left kedra.files untouched" $?
[[ $(im_snapshot) == "$after_first" ]]
check "refused publication left the served tree byte-for-byte unchanged" $?

# There must be no way to talk the publisher into it.
out=$(im_publish --force "$im_b" 2>&1); rc=$?
(( rc != 0 ))
check "there is no --force escape hatch for ordinary publication" $? "$(lastline "$out")"
[[ $(im_snapshot) == "$after_first" ]]
check "the --force attempt changed nothing either" $?

# A bumped pkgrel is a new identity and publishes normally.
im_c=$(build_selftest 2 kedra-repo-selftest beta)
out=$(im_publish "$im_c"); rc=$?
(( rc == 0 )) && [[ -f "$IMDIR/$(basename "$im_c")" ]]
check "a bumped pkgrel publishes normally" $? "$(lastline "$out")"

dbver=$(bsdtar -xOf "$IMDIR/kedra.db" --include '*/desc' 2>/dev/null |
	awk '/^%NAME%/{getline n} /^%VERSION%/{getline v; if (n == "kedra-repo-selftest") print v}')
[[ ${dbver} == "1.0-2" ]]
check "kedra.db names the new release (got: ${dbver:-none})" $?

# The superseded release stays exactly as it was published.
[[ $(im_file_sha "$im_base") == "$art_sha" && $(im_file_sha "$im_base.sig") == "$sig_sha" ]]
check "the superseded 1.0-1 artifact and signature are still byte-identical" $?

# A superseded artifact is present in the docroot but NOT named by kedra.db --
# the normal state of an append-only docroot. Re-offering it must not re-index
# it, which would silently downgrade the database.
out=$(im_publish "$im_a"); rc=$?
dbver=$(bsdtar -xOf "$IMDIR/kedra.db" --include '*/desc' 2>/dev/null |
	awk '/^%NAME%/{getline n} /^%VERSION%/{getline v; if (n == "kedra-repo-selftest") print v}')
(( rc == 0 )) && [[ ${dbver} == "1.0-2" ]]
check "re-offering a superseded release does not downgrade kedra.db (got: ${dbver:-none})" $?

###############################################################################
section "3c  immutable package identity WITHIN one batch"
###############################################################################
#
# Section 3b proves the rule holds between invocations. It cannot see the hole
# that opens inside one: two inputs of the same command line can carry the same
# identity and different bytes, and if each is only ever compared against what
# is ALREADY published, neither sees the other. Both then reach the mutation
# phase and one silently overwrites the other.
#
# These tests also pin the two things that make identity trustworthy at all:
# the docroot is indexed by reading each artifact's .PKGINFO rather than by
# globbing filenames, and the destination filename is computed from that
# metadata -- so renaming a file, on either side, changes nothing.

BA=$(build_selftest 1 kedra-repo-selftest alpha)   # identity X, bytes A
BB=$(build_selftest 1 kedra-repo-selftest beta)    # identity X, bytes B
BC=$(build_selftest 2 kedra-repo-selftest gamma)   # identity Y (pkgrel 2)
CANON=$(basename "$BA")

b_dir=""
b_new_root() { # b_new_root <name>  -- an empty docroot, and select it
	b_root="${KEDRA_TEST_DIR}/batch-$1"
	nsrm "$b_root"; mkdir -p "${b_root}/kedra/os/x86_64"
	b_dir="${b_root}/kedra/os/x86_64"
}
b_publish()  { KEDRA_REPO_ROOT="${b_root}" "${BIN_DIR}/kedra-repo-publish" "$@" 2>&1; }
b_snapshot() { find "$b_dir" -type f -exec sha256sum {} + 2>/dev/null | sort; }
b_files()    { find "$b_dir" -maxdepth 1 -name '*.pkg.tar.*' ! -name '*.sig' -printf '%f\n' | sort; }
b_dbver()    { bsdtar -xOf "$b_dir/kedra.db" --include '*/desc' 2>/dev/null |
	awk '/^%NAME%/{getline n} /^%VERSION%/{getline v; if (n == "kedra-repo-selftest") print v}'; }

# -- 1. same identity, different bytes, both in ONE batch --------------------
b_new_root conflict
empty=$(b_snapshot)
out=$(b_publish "$BA" "$BB"); rc=$?
(( rc != 0 ))
check "one batch carrying two different artifacts for one identity is REFUSED" $? "$(lastline "$out")"
grep -q "$BA" <<<"$out" && grep -q "$BB" <<<"$out"
check "the refusal names both offered paths" $?
grep -q "$(sha256sum "$BA" | cut -d' ' -f1)" <<<"$out" &&
	grep -q "$(sha256sum "$BB" | cut -d' ' -f1)" <<<"$out"
check "the refusal prints both checksums" $?
grep -qi 'pkgver or pkgrel must change' <<<"$out"
check "the refusal says pkgver/pkgrel must change" $?
[[ $(b_snapshot) == "$empty" ]] && [[ -z $(b_files) ]]
check "the refused batch published nothing at all -- no artifact, no signature, no database" $?

# -- 2. same identity, identical bytes, duplicated in one batch --------------
b_new_root duplicate
cp "$BA" "${b_root}/copy-under-another-name.pkg.tar.zst"
out=$(b_publish "$BA" "$BA" "${b_root}/copy-under-another-name.pkg.tar.zst"); rc=$?
(( rc == 0 ))
check "a batch that names one artifact three times succeeds" $? "$(lastline "$out")"
[[ $(b_files) == "$CANON" ]]
check "the duplicated input was published exactly once (got: $(b_files | tr '\n' ' '))" $?
(( $(find "$b_dir" -name '*.pkg.tar.*.sig' | wc -l) == 1 ))
check "it was signed exactly once" $?
[[ $(b_dbver) == "1.0-1" ]]
check "kedra.db names it exactly once, at 1.0-1 (got: $(b_dbver))" $?
grep -qi 'publishing it once' <<<"$out"
check "the duplicate was reported rather than published twice" $?
after_dup=$(b_snapshot)
out=$(b_publish "$BA" "$BA"); rc=$?
(( rc == 0 )) && [[ $(b_snapshot) == "$after_dup" ]]
check "re-offering the same duplicated batch is still an inert no-op" $? "$(lastline "$out")"

# -- 3. a renamed artifact cannot hide a published identity ------------------
#
# Discovery by filename glob would make this invisible: the docroot is read for
# what each artifact SAYS it is, not for what it is called.
b_new_root renamed-published
b_publish "$BA" >/dev/null
mv "$b_dir/$CANON"     "$b_dir/some-operator-renamed-this.pkg.tar.zst"
mv "$b_dir/$CANON.sig" "$b_dir/some-operator-renamed-this.pkg.tar.zst.sig"
before=$(b_snapshot)
out=$(b_publish "$BB"); rc=$?
(( rc != 0 )) && grep -qi 'IMMUTABLE PACKAGE IDENTITY' <<<"$out"
check "a renamed published artifact still blocks different bytes for its identity" $? "$(lastline "$out")"
[[ $(b_snapshot) == "$before" ]]
check "that refusal changed nothing in the served tree" $?
out=$(b_publish "$BA"); rc=$?
(( rc == 0 )) && grep -q 'already published identically' <<<"$out"
check "the same bytes under the renamed artifact are recognised as already published" $? "$(lastline "$out")"
[[ $(b_snapshot) == "$before" ]] && [[ ! -e "$b_dir/$CANON" ]]
check "and are NOT copied in again under the canonical name" $?

# -- 4. a renamed INPUT is normalised onto its canonical destination ---------
b_new_root renamed-input
cp "$BA" "${b_root}/totally-arbitrary-name.pkg.tar.zst"
out=$(b_publish "${b_root}/totally-arbitrary-name.pkg.tar.zst"); rc=$?
(( rc == 0 ))
check "an input whose filename disagrees with its metadata is accepted" $? "$(lastline "$out")"
[[ $(b_files) == "$CANON" ]]
check "it landed under the canonical metadata-derived name (got: $(b_files | tr '\n' ' '))" $?
[[ ! -e "$b_dir/totally-arbitrary-name.pkg.tar.zst" ]]
check "the arbitrary filename did not become a second name for this identity" $?
grep -qi 'not named as its metadata says' <<<"$out"
check "the normalisation was reported rather than done silently" $?
out=$(b_publish "$BB"); rc=$?
(( rc != 0 )) && grep -qi 'IMMUTABLE PACKAGE IDENTITY' <<<"$out"
check "a renamed input cannot smuggle different bytes past the identity check" $? "$(lastline "$out")"

# -- 5. a failing batch leaves EVERY served file byte-identical --------------
b_new_root failure-is-inert
b_publish "$BA" >/dev/null
art_sha=$(sha256sum "$b_dir/$CANON" | cut -d' ' -f1)
sig_sha=$(sha256sum "$b_dir/$CANON.sig" | cut -d' ' -f1)
db_sha=$(sha256sum "$b_dir/kedra.db.tar.zst" | cut -d' ' -f1)
files_sha=$(sha256sum "$b_dir/kedra.files.tar.zst" | cut -d' ' -f1)
before=$(b_snapshot)
out=$(b_publish "$BC" "$BA" "$BB"); rc=$?
(( rc != 0 ))
check "a batch whose members individually pass is refused when two of them collide" $? "$(lastline "$out")"
[[ $(sha256sum "$b_dir/$CANON" | cut -d' ' -f1) == "$art_sha" ]]
check "batch failure left the package artifact untouched" $?
[[ $(sha256sum "$b_dir/$CANON.sig" | cut -d' ' -f1) == "$sig_sha" ]]
check "batch failure left the signature untouched" $?
[[ $(sha256sum "$b_dir/kedra.db.tar.zst" | cut -d' ' -f1) == "$db_sha" ]]
check "batch failure left kedra.db untouched" $?
[[ $(sha256sum "$b_dir/kedra.files.tar.zst" | cut -d' ' -f1) == "$files_sha" ]]
check "batch failure left kedra.files untouched" $?
[[ $(b_snapshot) == "$before" ]]
check "batch failure left the served tree byte-for-byte unchanged" $?

# -- 6. one bad pair rejects the WHOLE batch, before any mutation ------------
b_new_root mixed
empty=$(b_snapshot)
out=$(b_publish "$BC" "$BA" "$BB"); rc=$?
(( rc != 0 ))
check "a mixed batch with one colliding pair is refused" $? "$(lastline "$out")"
[[ -z $(b_files) ]]
check "the innocent member of the refused batch was NOT published" $?
[[ $(b_snapshot) == "$empty" ]]
check "no signature and no database were written for it either" $?
out=$(b_publish "$BC" "$BA"); rc=$?
(( rc == 0 )) && [[ $(b_files | wc -l) == 2 ]]
check "the same batch without the collision publishes both members" $? "$(lastline "$out")"
# repo-add names whatever it is handed last, so a batch that offered the newer
# release first would otherwise leave kedra.db naming the older one.
[[ $(b_dbver) == "1.0-2" ]]
check "a batch offering 1.0-2 before 1.0-1 still leaves kedra.db naming 1.0-2 (got: $(b_dbver))" $?

# -- 7. a superseded release cannot be re-indexed from inside a batch --------
b_new_root superseded
b_publish "$BA" >/dev/null
b_publish "$BC" >/dev/null
[[ $(b_dbver) == "1.0-2" ]]
check "the batch fixture is at 1.0-2 before the downgrade attempt (got: $(b_dbver))" $?
before=$(b_snapshot)
out=$(b_publish "$BA" "$BC"); rc=$?
(( rc == 0 )) && [[ $(b_dbver) == "1.0-2" ]]
check "re-offering a superseded release inside a batch does not downgrade kedra.db (got: $(b_dbver))" $? "$(lastline "$out")"
[[ $(b_snapshot) == "$before" ]]
check "that batch was an inert no-op: the database was not even rewritten" $?

###############################################################################
section "4  signed upgrade lifecycle (N -> N+1)"
###############################################################################

# Published into a throwaway docroot so the real repository never carries test
# packages.
UPROOT="${KEDRA_TEST_DIR}/upgrade-repo"
nsrm "$UPROOT"; mkdir -p "${UPROOT}/kedra/os/x86_64"
UP_URL="file://${UPROOT}/\$repo/os/\$arch"

v1=$(build_selftest 1 kedra-repo-selftest)
out=$(KEDRA_REPO_ROOT="${UPROOT}" "${BIN_DIR}/kedra-repo-publish" "$v1" 2>&1)
check "repository revision N published (kedra-repo-selftest 1.0-1)" $? "$(lastline "$out")"

R6="${KEDRA_TEST_DIR}/r6-upgrade"
new_root "$R6" "${UP_URL}" 0 1
PAC_CACHE="${R6}/cache"; mkdir -p "$PAC_CACHE"
out=$(pac "$R6" -Sy; pac "$R6" -Sdd kedra-repo-selftest)
[[ $(pac "$R6" -Q kedra-repo-selftest) == "kedra-repo-selftest 1.0-1" ]]
check "revision N installs from [kedra]" $? "$(lastline "$out")"

v2=$(build_selftest 2 kedra-repo-selftest)
out=$(KEDRA_REPO_ROOT="${UPROOT}" "${BIN_DIR}/kedra-repo-publish" "$v2" 2>&1)
check "repository revision N+1 published (kedra-repo-selftest 1.0-2)" $? "$(lastline "$out")"

out=$(pac "$R6" -Syu)
[[ $(pac "$R6" -Q kedra-repo-selftest) == "kedra-repo-selftest 1.0-2" ]]
check "pacman -Syu performs a normal signed upgrade N -> N+1" $? "$(lastline "$out")"
grep -qi 'checking keyring' <<<"$out" && grep -qi 'checking package integrity' <<<"$out"
check "the upgrade passed through keyring + integrity verification" $?
PAC_CACHE=""

###############################################################################
section "5  base root: a real CachyOS-compatible system"
###############################################################################

# Deliberately built WITHOUT [kedra] and WITHOUT Kedra trust: this is the
# "clean Arch/CachyOS-compatible system" the bootstrap has to start from.
BASE="${KEDRA_TEST_DIR}/base"
if ! ns test -x "$BASE/usr/bin/pacman-key"; then
	printf '  (building base root; downloads ~110 MiB once)\n'
	nsrm "$BASE"; mkdir -p "$BASE/var/lib/pacman" "$BASE/etc/pacman.d/gnupg"
	write_conf "$BASE" - 1
	pkey "$BASE/etc/pacman.d/gnupg" --init >/dev/null
	# Host-side trust bootstrap for upstream, exactly as an installer does it.
	pkey "$BASE/etc/pacman.d/gnupg" --populate-from /usr/share/pacman/keyrings \
		--populate archlinux cachyos >/dev/null
	pac "$BASE" -Sy >/dev/null
	base_out=$(pac "$BASE" -S pacman cachyos-hooks)
fi
ns test -x "$BASE/usr/bin/pacman-key"
check "base root has a working pacman" $? "$(lastline "${base_out:-}")"
[[ $(pac "$BASE" -Q cachyos-hooks) == cachyos-hooks* ]]
check "base root has cachyos-hooks installed" $?
# --with-colons, because plain --list-keys prints the gpgdir path in its
# header and every test path contains the string "kedra".
! gpgq "$BASE/etc/pacman.d/gnupg" --list-keys --with-colons | grep -q 'Kedra Linux'
check "base root has NO Kedra trust" $?

snapshot() { nsrm "$1"; ns cp -a "$BASE" "$1"; }

# The "live ISO" keyring: upstream trust plus Kedra trust. On a real ISO this
# is /etc/pacman.d/gnupg of the live system, populated at image build time
# because kedra-keyring is installed into the ISO.
LIVE="${KEDRA_TEST_DIR}/live-gnupg"
if ! ns test -f "$LIVE/trustdb.gpg"; then
	nsrm "$LIVE"; mkdir -p "$LIVE"
	pkey "$LIVE" --init >/dev/null
	pkey "$LIVE" --populate-from /usr/share/pacman/keyrings \
		--populate archlinux cachyos >/dev/null
	pkey "$LIVE" --populate-from "${KEYRING_SRC}" --populate kedra >/dev/null
fi
gpgq "$LIVE" --list-keys --with-colons | grep -q 'Kedra Linux'
check "live/installer keyring trusts Kedra" $?

###############################################################################
section "6  keyring bootstrap into a target root"
###############################################################################

R7="${KEDRA_TEST_DIR}/r7-bootstrap"
snapshot "$R7"
write_conf "$R7" "${KEDRA_URL}" 1

# 6a  The bootstrap problem, demonstrated: [kedra] is configured but the system
#     has no Kedra trust, so it cannot even read the repository.
out=$(pac "$R7" -Sy)
grep -qiE 'is unknown|not valid' <<<"$out"
check "a system without Kedra trust CANNOT use [kedra] (this is the bootstrap problem)" $? "$(lastline "$out")"

# 6b  The solution: the installer verifies against the live keyring, which
#     already trusts Kedra, and installs kedra-keyring into the target.
PAC_GPGDIR="${LIVE}"
out=$(pac "$R7" -Sy; pac "$R7" -S kedra-keyring)
PAC_GPGDIR=""
grep -q 'Appending keys from kedra.gpg' <<<"$out"
check "kedra-keyring's scriptlet ran pacman-key --populate kedra inside the target" $? "$(lastline "$out")"

valid=$(gpgq "$R7/etc/pacman.d/gnupg" --list-keys --with-colons |
	awk -F: '/^pub:/ {v=$2} /^uid:/ && /Kedra Linux DEVELOPMENT Packager/ {print v; exit}')
[[ ${valid} == f ]]
check "the Kedra packager key is fully valid in the target keyring (validity: ${valid:-none})" $?

n_pub=$(gpgq "$R7/etc/pacman.d/gnupg" --list-keys --with-colons | grep -c '^pub')
(( n_pub > 100 ))
check "Kedra trust is additive: archlinux/cachyos keys still present (${n_pub} public keys)" $?

# 6c  The target is now self-sufficient: it can use [kedra] with its own keyring.
out=$(pac "$R7" -Sy)
! grep -qiE 'is unknown|not valid' <<<"$out"
check "after kedra-keyring the target uses [kedra] with its own keyring" $? "$(lastline "$out")"

###############################################################################
section "7  cachyos-hooks -> kedra-hooks replacement"
###############################################################################

R8="${KEDRA_TEST_DIR}/r8-replace"
snapshot "$R8"
write_conf "$R8" "${KEDRA_URL}" 1
PAC_GPGDIR="${LIVE}"
pac "$R8" -Sy >/dev/null; pac "$R8" -S kedra-keyring >/dev/null
PAC_GPGDIR=""

before=$(pac "$R8" -Q cachyos-hooks)
[[ ${before} == cachyos-hooks* ]]
check "before the transaction: ${before:-nothing} is installed" $?

out=$(pac "$R8" -Syu)

[[ $(pac "$R8" -Q kedra-hooks) == kedra-hooks* ]]
check "kedra-hooks is installed after a plain pacman -Syu" $? "$(lastline "$out")"
# `pacman -Q cachyos-hooks` would resolve through kedra-hooks' `provides`, so
# the local package list has to be matched by exact name instead.
! pac "$R8" -Qq | grep -qx cachyos-hooks
check "cachyos-hooks is gone -- no duplicate identity owner" $? "$(pac "$R8" -Qq | grep -c .) packages installed"
grep -qi 'replac' <<<"$out"
check "pacman treated it as a replacement, not a manual removal" $?
pac "$R8" -Qi kedra-hooks | grep -q 'cachyos-hooks='
check "kedra-hooks still provides cachyos-hooks (dependants stay satisfiable)" $?
ns grep -q '^ID=kedra' "$R8/etc/os-release"
check "/etc/os-release in the target now identifies Kedra" $? "$(ns cat "$R8/etc/os-release" 2>/dev/null | head -3 | tr '\n' ' ')"

###############################################################################
section "8  upstream repositories still resolve with [kedra] on top"
###############################################################################

for pkg_repo in "systemd core" "bash core" "linux-cachyos cachyos"; do
	set -- $pkg_repo
	got=$(pac "$R8" -Si "$1" | awk -F': *' '/^Repository/ {print $2; exit}')
	[[ ${got} == "$2" ]]
	check "$1 still resolves from [$2] (got: ${got:-none})" $?
done

out=$(pac "$R8" -Sup)
! grep -qi 'error' <<<"$out"
check "a full -Syu dry run resolves cleanly with [kedra] present" $? "$(lastline "$out")"

###############################################################################
section "9  host impact"
###############################################################################

! grep -q '^\[kedra\]' /etc/pacman.conf
check "host /etc/pacman.conf has no [kedra] section" $?

! gpg --homedir /etc/pacman.d/gnupg --batch --no-tty --list-keys 2>/dev/null |
	grep -qi 'Kedra Linux'
check "host pacman keyring contains no Kedra keys" $?

! ls /usr/share/pacman/keyrings/kedra* >/dev/null 2>&1
check "host has no kedra keyring files installed" $?

###############################################################################
printf '\n\033[1m===== summary =====\033[0m\n'
printf '%s\n' "${RESULTS[@]}"
printf '\n%d passed, %d failed\n' "$pass" "$fail"

if (( KEEP )); then
	printf 'test roots kept in %s\n' "${KEDRA_TEST_DIR}"
else
	nsrm "${KEDRA_TEST_DIR}"/r[0-9]* "${KEDRA_TEST_DIR}"/r-bad-* \
		"${KEDRA_TEST_DIR}"/bad-* "${KEDRA_TEST_DIR}/rogue-gnupg" \
		"${KEDRA_TEST_DIR}"/batch-* "${KEDRA_TEST_DIR}/immutable-repo" \
		"${KEDRA_TEST_DIR}/upgrade-repo" "${KEDRA_TEST_DIR}/build"
fi

exit $(( fail > 0 ))
