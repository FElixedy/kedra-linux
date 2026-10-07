# Deferred hardening debt

Findings that a review raised, that were understood, and that were
**deliberately not fixed** in the change that raised them. Each entry says what
the exposure is, why it was left, and what closing it would take — so the
decision is reviewable rather than forgotten.

An entry here is not a bug report. It is a debt with a named holder in the
milestone plan.

## 1. Predictable temporary filename in `install-state`

*Raised:* M1 freeze audit (independent review), severity LOW.
*Status:* deferred, still open.
*Site:* `tools/kedra_experience/prodcli.py`, `cmd_install_state`.

The state file is written whole and then moved into place — which is the right
shape, because a half-written state file is worse than none and the executor
that later reads it must never see one. The temporary it writes through is a
sibling with a fixed, predictable name:

    <state file>.new

created by `Path.write_text`, i.e. `open(..., "w")` — `O_CREAT|O_TRUNC` without
`O_EXCL` — with its mode and ownership set immediately afterwards rather than at
creation.

**Why it is LOW here, and not theatre.** The directory chain is created by this
same command with mode 0700, and the only caller is the installer, running as
root against a freshly created target home, before that home's owner has any way
to run code. There is no window in the shipped flow in which an attacker exists.

**What closing it looks like.** Create the temporary with
`os.open(..., O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)` and a random suffix, write
through that descriptor, `os.fchmod`/`os.fchown` it before the rename, and treat
an existing temporary as a hard failure rather than truncating it. Perhaps forty
lines with its tests.

**Why it was not done during the M1 freeze.** The freeze-hardening pass closed
two HIGH and two MEDIUM findings, all of which change installer or repository
behaviour. This one changes how a file is created on a path that is already
root-only and already refuses to overwrite an existing state file. Touching the
write path to fix a window that the shipped flow does not open would have added
risk to a freeze for no gain in the product.

**Where it should be picked up.** With the next change that touches
`cmd_install_state` for any other reason, or as an explicit hardening item after
M1 — whichever comes first. It is not a release blocker for M1 and is not
claimed to be fixed anywhere.
