"""Canonical identity for a GNOME keyboard accelerator.

Two accelerators clash when they name the same key with the same modifiers --
not when their *text* matches. `<Super><Shift>1` and `<Shift><Super>1` are one
combination written two ways, and GNOME Settings, mutter and a hand-edited dconf
value do not agree on which spelling to produce. Comparing raw strings would
therefore miss a real conflict, which is precisely the failure this module
exists to prevent.

GTK is the authority, and the only one
--------------------------------------
`gtk_accelerator_parse()` is the function GNOME itself parses these strings
with. It owns the grammar: the modifier vocabulary, the `<Primary>` alias, the
key names, the case rules. Re-implementing any part of that here would mean
maintaining a second, subtly different grammar and calling its disagreements
with GTK "no conflict" -- so there is deliberately **no fallback parser**.

When GTK cannot be imported, this module does not guess. `available()` returns
False and `parse()` raises `AcceleratorUnavailable`, and every caller must treat
that as *unknown*, never as *no conflict*. See `conflicts` for what unknown
costs: a managed shortcut registration is blocked rather than performed blind.

That split is intentional. Loading and validating manifests must stay
stdlib-only and work on a build machine with no desktop at all, so nothing in
the declarative path calls into here. Semantic conflict detection runs against a
live GNOME session, where GTK is part of the stack by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

# GTK versions whose accelerator parser is acceptable, most preferred first.
# Both are the real GTK implementation -- this is a typelib-availability
# fallback, not a semantic one.
GTK_VERSIONS = ("4.0", "3.0")

# Why there is no hand-written parser, quoted where the decision is tested.
NO_FALLBACK_NOTE = (
    "GTK defines the accelerator grammar being compared, so a hand-written "
    "parser would be a second grammar whose disagreements with GTK would be "
    "reported as 'no conflict'; when GTK is absent the answer is 'unknown'"
)


class AcceleratorError(ValueError):
    """An accelerator string cannot be parsed."""


class AcceleratorUnavailable(Exception):
    """No authoritative accelerator parser is installed.

    Deliberately not a subclass of `AcceleratorError`: "this text is wrong" and
    "this machine cannot tell you" are different answers, and a caller that
    handled them together would end up treating an unparseable environment as an
    unparseable value.
    """


@dataclass(frozen=True)
class Accelerator:
    """One key combination, in the identity GTK gives it.

    Equality and hashing come from `(keyval, modifiers)` -- the pair GTK reduces
    a spelling to -- so two spellings of one combination are one object's worth
    of meaning. `canonical` is GTK's own preferred text, kept for display and
    for explaining a conflict to a person.
    """

    keyval: int
    modifiers: int
    canonical: str
    # Exactly what was parsed, so a diagnostic can quote the user's own text
    # rather than a re-rendered version they never wrote.
    source: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Accelerator):
            return NotImplemented
        return (self.keyval, self.modifiers) == (other.keyval, other.modifiers)

    def __hash__(self) -> int:
        return hash((self.keyval, self.modifiers))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Accelerator({self.canonical!r})"


_gtk = None
_gtk_error: str | None = None
_probed = False


def _load_gtk():
    """Import GTK once, remembering failure as well as success.

    Import is deferred and cached because it is expensive, because it must not
    happen during manifest validation, and because a machine without it must
    fail the same way every time rather than depending on import order.
    """
    global _gtk, _gtk_error, _probed
    if _probed:
        return _gtk
    _probed = True

    try:
        import gi
    except ImportError as exc:
        _gtk_error = f"PyGObject is not installed ({exc})"
        return None

    problems = []
    for version in GTK_VERSIONS:
        try:
            gi.require_version("Gtk", version)
            from gi.repository import Gtk

            # Called rather than merely imported: a typelib can be present while
            # the library behind it is not, and the difference only shows up on
            # the first real call.
            Gtk.accelerator_parse("<Super>Return")
        except Exception as exc:  # ValueError from require_version, or GLib
            problems.append(f"Gtk {version}: {exc}")
            continue
        _gtk = Gtk
        return _gtk

    _gtk_error = "; ".join(problems) or "no usable Gtk typelib"
    return None


def available() -> bool:
    """Is an authoritative accelerator parser installed?

    The capability check callers use before promising a conflict answer. False
    means "cannot tell", and no caller may read it as "nothing conflicts".
    """
    return _load_gtk() is not None


def unavailable_reason() -> str | None:
    """Why no parser is available, or None when one is."""
    if _load_gtk() is not None:
        return None
    return _gtk_error or "no usable Gtk typelib"


def parse(text: object) -> Accelerator | None:
    """Canonical identity for an accelerator string.

    Returns None for the empty string, which GNOME uses to mean *not bound* --
    a real state, distinct from both a combination and a parse failure, and the
    one every unconfigured custom shortcut is in.

    Raises `AcceleratorError` when the text is not an accelerator, and
    `AcceleratorUnavailable` when no parser is installed.
    """
    if not isinstance(text, str):
        raise AcceleratorError(f"accelerator must be a string, got {text!r}")

    stripped = text.strip()
    if not stripped:
        return None

    gtk = _load_gtk()
    if gtk is None:
        raise AcceleratorUnavailable(
            f"cannot compare accelerators: {unavailable_reason()}. {NO_FALLBACK_NOTE}"
        )

    parsed = gtk.accelerator_parse(stripped)
    # GTK 4 returns (ok, keyval, mods); GTK 3 returns (keyval, mods).
    if len(parsed) == 3:
        ok, keyval, modifiers = parsed
    else:  # pragma: no cover - only on a GTK 3 machine
        keyval, modifiers = parsed
        ok = bool(keyval) or bool(modifiers)

    if not ok:
        raise AcceleratorError(f"{text!r} is not a valid accelerator")

    if not keyval:
        # GTK accepts a modifier-only string such as "<Super>", but it names no
        # key, so nothing can be grabbed with it. Reported as unbound rather
        # than as a combination: treating it as one would let two shortcuts that
        # both grab nothing be declared in conflict.
        return None

    canonical = gtk.accelerator_name(keyval, modifiers)
    if not canonical:  # pragma: no cover - GTK accepted it, so this is defensive
        raise AcceleratorError(f"{text!r} has no canonical accelerator form")

    return Accelerator(
        keyval=int(keyval),
        modifiers=int(modifiers),
        canonical=canonical,
        source=stripped,
    )


def equivalent(left: object, right: object) -> bool:
    """Do two accelerator strings name the same combination?

    Unbound is not equivalent to anything, including another unbound value:
    two shortcuts that are both unbound do not clash, and answering True would
    make every pair of blank shortcuts a conflict.
    """
    first = parse(left)
    second = parse(right)
    if first is None or second is None:
        return False
    return first == second


__all__ = [
    "GTK_VERSIONS",
    "NO_FALLBACK_NOTE",
    "Accelerator",
    "AcceleratorError",
    "AcceleratorUnavailable",
    "available",
    "equivalent",
    "parse",
    "unavailable_reason",
]
