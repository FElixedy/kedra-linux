#!/usr/bin/env bash
#
# Regression tests for bin/kedra-check-sigpolicy.
#
# A validation check that has never been seen to fail is not a check. These
# tests feed the checker synthetic configurations -- one of them the EXACT
# stanza that shipped in the M0 live ISO -- and assert which are accepted and
# which are rejected.
#
# Runs in a temporary directory, needs no root and touches no pacman state:
# pacman-conf only reads the file it is given.

set -euo pipefail

_here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CHECK=${CHECK:-${_here}/../bin/kedra-check-sigpolicy}
[[ -x ${CHECK} ]] || { printf 'not executable: %s\n' "${CHECK}" >&2; exit 2; }

work=$(mktemp -d -t kedra-sigpolicy-tests-XXXXXX)
trap 'rm -rf -- "${work}"' EXIT

pass=0 fail=0
ok()  { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; pass=$((pass + 1)); }
bad() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=$((fail + 1)); }

# fixture NAME <<'EOF' ... EOF   -- writes ${work}/NAME.conf
fixture() { cat >"${work}/$1.conf"; }

# expect accept|reject NAME DESCRIPTION
expect() {
	local want=$1 name=$2 desc=$3 out rc
	out=$("${CHECK}" --quiet --label "${name}" "${work}/${name}.conf" 2>&1) && rc=0 || rc=$?
	case ${want} in
		accept) (( rc == 0 )) && ok "accepts: ${desc}" ||
			{ bad "should have accepted: ${desc}"; printf '%s\n' "${out}" | sed 's/^/        /'; } ;;
		reject)
			if (( rc == 0 )); then
				bad "should have REJECTED: ${desc}"
			elif [[ ${out} == *'could not parse it'* ]]; then
				bad "rejected for the WRONG REASON (unparseable fixture, not policy): ${desc}"
				printf '%s\n' "${out}" | sed 's/^/        /'
			else
				ok "rejects: ${desc}"
			fi ;;
	esac
}

# A repository needs a Server for pacman-conf to list it. file:///dev/null is
# never contacted -- nothing here syncs anything.
_repo() { printf '\n[%s]\n%sServer = file:///dev/null\n' "$1" "${2:+SigLevel = $2$'\n'}"; }

printf '\n\033[1;34m==>\033[0m The case this check was written for\n'

# ---------------------------------------------------------------------------
# THE DISCOVERED CASE. Reproduced verbatim from kedra-iso commit 29061d3,
# archiso/airootfs/etc/pacman.conf lines 94-100 -- the configuration that
# shipped inside kedra-desktop-linux-260815.iso and that the previous
# `grep -E '^\s*SigLevel\s*=\s*Never'` check passed.
# ---------------------------------------------------------------------------
fixture cachyos-trustall <<'EOF'
[options]
Architecture = x86_64
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[kedra]
SigLevel = Required DatabaseOptional
Server = file:///usr/share/kedra/repo/$repo/os/$arch

[cachyos]
SigLevel = Optional TrustAll
Server = file:///dev/null

[core]
Server = file:///dev/null
EOF
expect reject cachyos-trustall '[cachyos] SigLevel = Optional TrustAll (the M0 live ISO)'

# The same file with only that one line removed must pass, which is precisely
# the fix applied in M0.1.
fixture cachyos-fixed <<'EOF'
[options]
Architecture = x86_64
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[kedra]
SigLevel = Required DatabaseOptional
Server = file:///usr/share/kedra/repo/$repo/os/$arch

[cachyos]
Server = file:///dev/null

[core]
Server = file:///dev/null
EOF
expect accept cachyos-fixed '[cachyos] inheriting the global policy (the M0.1 fix)'

printf '\n\033[1;34m==>\033[0m Every spelling of a weakened repository policy\n'

_base() { printf '[options]\nSigLevel = Required DatabaseOptional\nLocalFileSigLevel = Optional\n'; }

while read -r level desc; do
	[[ -z ${level} ]] && continue
	name="weak-$(tr -c 'A-Za-z0-9' '-' <<<"${level}" | tr -s '-' | sed 's/-$//')"
	{ _base; _repo cachyos "${level//_/ }"; _repo core; } >"${work}/${name}.conf"
	expect reject "${name}" "SigLevel = ${level//_/ } (${desc})"
done <<'CASES'
Never unsigned_and_unverified
Optional unsigned_packages_accepted
TrustAll untrusted_keys_accepted
Optional_TrustAll the_discovered_form
PackageOptional package_half_only
PackageTrustAll package_half_only
PackageNever package_half_only
TrustAll_Required word_order_reversed
PackageTrustAll_DatabaseRequired mixed_prefixes
CASES

# A weakened GLOBAL policy is just as bad, and inheriting repositories hide it.
fixture weak-global <<'EOF'
[options]
SigLevel = Optional TrustAll

[cachyos]
Server = file:///dev/null
EOF
expect reject weak-global '[options] itself weakened, repositories inheriting it'

printf '\n\033[1;34m==>\033[0m Legitimate configurations must not be rejected\n'

# The explicit false positive this check must not produce. LocalFileSigLevel
# governs `pacman -U` on an operator-supplied file, not repository packages;
# `Optional` is the Arch default and every makepkg workflow depends on it.
fixture localfile-optional <<'EOF'
[options]
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional

[cachyos]
Server = file:///dev/null
EOF
expect accept localfile-optional 'LocalFileSigLevel = Optional (Arch default, not a repository policy)'

fixture localfile-trustall <<'EOF'
[options]
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional TrustAll

[cachyos]
Server = file:///dev/null
EOF
expect accept localfile-trustall 'LocalFileSigLevel = Optional TrustAll (still not a repository policy)'

# DatabaseOptional is what Arch, CachyOS and [kedra] all ship. Requiring a
# database signature is a separate decision and must not be forced here.
fixture db-optional <<'EOF'
[options]
SigLevel = Required DatabaseOptional

[kedra]
SigLevel = Required DatabaseOptional
Server = file:///dev/null

[cachyos]
Server = file:///dev/null
EOF
expect accept db-optional 'DatabaseOptional on every repository'

fixture db-required <<'EOF'
[options]
SigLevel = Required DatabaseRequired

[kedra]
Server = file:///dev/null
EOF
expect accept db-required 'DatabaseRequired (stricter than needed)'

# A repository may restate the global policy explicitly rather than inherit it.
fixture explicit-restate <<'EOF'
[options]
SigLevel = Required DatabaseOptional

[cachyos]
SigLevel = Required DatabaseOptional
Server = file:///dev/null
EOF
expect accept explicit-restate 'a repository restating the global policy verbatim'

printf '\n\033[1;34m==>\033[0m The real Kedra configurations\n'

# The shipped files themselves, so this suite fails if any of them ever regresses.
# The build-time pacman.conf is a template and is substituted first, exactly as
# mkarchiso's _make_pacman_conf does.
_iso=$(cd -- "${_here}/.." && pwd -P)
sed -e "s|@KEDRA_ISO_GPGDIR@|${work}|g" \
    -e "s|@KEDRA_ISO_CACHEDIR@|${work}|g" \
    -e "s|@KEDRA_REPO_ROOT@|${work}|g" \
    "${_iso}/archiso/pacman.conf" >"${work}/profile-buildtime.conf"
expect accept profile-buildtime 'archiso/pacman.conf (build-time, placeholders substituted)'

cp "${_iso}/archiso/airootfs/etc/pacman.conf"      "${work}/profile-live.conf"
expect accept profile-live 'archiso/airootfs/etc/pacman.conf (live system)'

cp "${_iso}/archiso/airootfs/etc/pacman-more.conf" "${work}/profile-more.conf"
expect accept profile-more 'archiso/airootfs/etc/pacman-more.conf'

# The installed system's configuration, owned by kedra-calamares.
_target=${_iso}/../kedra-calamares/src/kedra/pacman.conf
if [[ -r ${_target} ]]; then
	cp "${_target}" "${work}/profile-target.conf"
	expect accept profile-target 'kedra-calamares src/kedra/pacman.conf (installed system)'
else
	printf '  \033[1;33mSKIP\033[0m kedra-calamares checkout not found at %s\n' "${_target}"
fi

printf '\n'
printf '\033[1;34m==>\033[0m %d passed, %d failed\n' "${pass}" "${fail}"
(( fail == 0 )) || exit 1
