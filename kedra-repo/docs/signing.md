# Kedra package signing model

## Trust topology

```
Kedra Master Key          certify + sign, 2-year expiry
   │                      the only fingerprint in kedra-trusted
   │                      kept offline; never touches a build machine
   │ certifies (exportable signature)
   ▼
Kedra Packager Key        signs packages and kedra.db
                          present in kedra.gpg, NOT in kedra-trusted
```

`pacman-key --populate kedra` imports `kedra.gpg`, locally signs every
fingerprint listed in `kedra-trusted` with the machine's own pacman master key,
and imports the ownertrust values. The packager key is then valid *through* the
master's certification, which is what makes rotation cheap: a new packager key
ships as a `kedra-keyring` update and the trust anchor never changes.

### Why ownertrust `5` and not `4`

GnuPG's ownertrust scale is `2` unknown, `3` never, `4` marginal, `5` full,
`6` ultimate.

`archlinux-trusted` uses `4`. That is correct *for Arch*, whose model requires
three master-key signatures before a packager key becomes valid
(`marginals needed: 3`). Kedra has one master key, so `4` would leave every
packager key merely marginal and pacman would refuse its signatures — this was
observed directly during implementation, not reasoned about.

`kedra-trusted` therefore uses `5`. Moving to Arch's 3-of-N scheme later is a
change to that one file plus additional master keys; nothing else in the design
assumes a single master.

## Where private keys live

Secret key material lives in `$KEDRA_SIGNING_GNUPGHOME`, default
`~/.local/share/kedra-dev-signing/gnupg`, mode `0700`. That path is deliberately
outside every git working tree. Nothing in this repository, in `kedra-keyring`,
or in any built package contains a private key: `kedra.gpg` is a plain
`gpg --export` of public keys only.

`--export-options export-minimal` must **not** be used when regenerating
`kedra.gpg`: it strips third-party certifications, including the master's
certification of the packager key, which silently breaks the trust chain.

## Current keys are DEVELOPMENT ONLY

`kedra-dev-keys-init` creates two keys whose user IDs say so, at
`@kedra.invalid` (a reserved TLD that can never be registered):

- `Kedra Linux DEVELOPMENT Master Key (DEVELOPMENT ONLY - NOT A PRODUCTION KEDRA IDENTITY)`
- `Kedra Linux DEVELOPMENT Packager (DEVELOPMENT ONLY - NOT A PRODUCTION KEDRA IDENTITY)`

They have **no passphrase**, because an unattended local test pipeline must not
prompt. They are a validation harness for the pipeline, not an identity.

No production Kedra identity has been invented here — no real name, no real
email domain, no claim of authority.

## Replacing them with production signing

1. Decide the production identity: real project name, a real address on the
   Kedra domain, and who holds the master key. **This is an open decision** —
   see `README.md`.
2. Generate the master key on an offline machine or hardware token, with a
   passphrase, and back up its revocation certificate separately.
3. Generate packager keys on their respective signing hosts (or in the CI
   secret store), and have the master certify each one.
4. Regenerate `kedra-keyring`'s `kedra.gpg` from the production public keys and
   put the production master fingerprint in `kedra-trusted`.
5. Add the development master fingerprint to `kedra-revoked` in the same
   `kedra-keyring` release, so any machine that ever trusted the development key
   disables it on update. Bump `pkgver`.
6. Point `KEDRA_SIGNING_KEY` / `KEDRA_SIGNING_GNUPGHOME` at the production
   material. Nothing in `bin/` needs to change.
7. Re-sign and re-publish every package in `[kedra]`, and rebuild any ISO that
   embedded the development keyring.

Until step 5 has shipped, any artifact signed by the development key must be
treated as untrusted for users.
