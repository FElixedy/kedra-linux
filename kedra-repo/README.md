# kedra-repo

Tooling and configuration for the Kedra Linux binary package repository and its
package-signing trust root.

Small on purpose: two scripts, one allowlist, two config templates, one test
suite. Everything else is stock Arch tooling (`makepkg`, `repo-add`, `gpg`,
`pacman-key`).

```
Kedra package source → makepkg → sign → [kedra] → repo metadata → pacman client
```

## Layout

| Path | Purpose |
|---|---|
| `bin/_common.sh` | shared, env-overridable configuration |
| `bin/kedra-dev-keys-init` | creates the DEVELOPMENT signing keys and exports public material into `kedra-keyring` |
| `bin/kedra-repo-publish` | allowlist check → sign → publish → `repo-add --sign` |
| `config/kedra-repo-packages.txt` | the only names allowed into `[kedra]` |
| `config/pacman.conf.kedra-section` | the `[kedra]` stanza and its intended ordering |
| `config/kedra-mirrorlist.template` | `/etc/pacman.d/kedra-mirrorlist` |
| `tests/run-tests.sh` | isolated end-to-end test suite |
| `docs/repository.md` | layout, pipeline, ordering, publishing policy |
| `docs/signing.md` | trust topology, key handling, production replacement |
| `docs/bootstrap.md` | how a fresh system comes to trust `[kedra]` |

## Usage

```sh
# once per machine: create development signing keys (see docs/signing.md)
bin/kedra-dev-keys-init

# build a package the ordinary Arch way
cd ../kedra-hooks && PKGDEST=~/Build/kedra-staging makepkg -f

# sign it and publish it
bin/kedra-repo-publish ~/Build/kedra-staging/kedra-hooks-*.pkg.tar.zst

# verify the whole chain in throwaway roots (never touches the host)
tests/run-tests.sh
```

Everything is env-overridable: `KEDRA_REPO_ROOT`, `KEDRA_STAGING_DIR`,
`KEDRA_SIGNING_GNUPGHOME`, `KEDRA_SIGNING_KEY`, `KEDRA_REPO_NAME`,
`KEDRA_REPO_ARCH`.

## Status

Milestone 0. The repository is published to a local docroot whose layout is
already the future HTTPS layout, so moving to hosting is an `rsync` plus a
`Server` line.

## Open decisions

Deliberately deferred, not overlooked:

- **Production signing identity** — who holds the master key, what real name and
  address it carries. The current keys are development-only (`docs/signing.md`).
- **Hosting and domain** — no Kedra domain has been chosen, so
  `kedra-mirrorlist.template` carries no production URL rather than a fake one.
- **CI secrets** — where a packager key lives when publishing is automated.
- **Mirror strategy** — single origin vs. mirror network; out of scope for M0.
