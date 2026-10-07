"""Observed-value fixtures.

The planner needs to know what a value currently *is*, but must never find that
out itself -- reading a live GNOME session from the planner is precisely the
coupling this architecture avoids. So observed values arrive as data: a file a
developer writes, or (later) a mapping a backend adapter produced.

This keeps the whole planning path testable with no session, no dconf and no
GSettings, which is why every ownership test in this repository is a pure
function call.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .values import TypedValue, ValueError_

OBSERVED_SCHEMA_VERSION = 1

OBSERVED_KNOWN_KEYS = frozenset({"target", "type", "data", "absent"})


class ObservedError(Exception):
    """Raised when an observed-value fixture cannot be parsed."""


def parse_observed(doc: object, where: str = "<observed>") -> dict[str, TypedValue | None]:
    """Parse a fixture document into {canonical target: value or None}.

    A target mapped to None is *unset* -- at its vendor default. That is a
    distinct state from any concrete value, so it is spelled explicitly with
    `absent = true` rather than by omission.
    """
    if not isinstance(doc, dict):
        raise ObservedError(f"{where}: document must be a table")

    version = doc.get("observed_schema_version")
    if version is None:
        raise ObservedError(f"{where}: missing 'observed_schema_version'")
    if version != OBSERVED_SCHEMA_VERSION:
        raise ObservedError(
            f"{where}: unsupported observed_schema_version {version!r} "
            f"(this tool speaks {OBSERVED_SCHEMA_VERSION})"
        )

    entries = doc.get("observed", [])
    if not isinstance(entries, list):
        raise ObservedError(f"{where}: 'observed' must be an array of tables")

    result: dict[str, TypedValue | None] = {}
    for index, entry in enumerate(entries):
        label = f"{where}: observed[{index}]"
        if not isinstance(entry, dict):
            raise ObservedError(f"{label} must be a table")
        unknown = sorted(entry.keys() - OBSERVED_KNOWN_KEYS)
        if unknown:
            raise ObservedError(f"{label} has unknown key(s): {', '.join(unknown)}")

        target = entry.get("target")
        if not isinstance(target, str) or not target:
            raise ObservedError(f"{label} needs a non-empty 'target'")
        if target in result:
            raise ObservedError(f"{label} repeats target '{target}'")

        if entry.get("absent"):
            if "type" in entry or "data" in entry:
                raise ObservedError(
                    f"{label} is marked absent but also carries a value"
                )
            result[target] = None
            continue

        try:
            result[target] = TypedValue.from_dict(
                {"type": entry.get("type"), "data": entry.get("data")}
            )
        except ValueError_ as exc:
            raise ObservedError(f"{label}: {exc}") from exc

    return result


def observe(adapter, declarations) -> dict[str, TypedValue | None]:
    """Read the current value of every declared target through an adapter.

    The one place the *stored versus effective* rule is written down, because
    getting it wrong is silent and expensive:

    **Scalar targets are read with `read()`** -- the explicitly stored value, or
    None. None is what makes `original.present = false`, and that is what makes
    a later revert *unset* the key rather than write something back. Reading the
    effective value here would capture a schema default such as `''` as though
    the user had chosen it, and the revert would then leave an explicit empty
    string behind where there had been nothing at all.

    **Membership targets are read with `read_effective()`** -- the list in
    force. Kedra owns one entry in that list and must preserve every other
    entry exactly, including ones nobody ever explicitly wrote. Reading only the
    stored value would make an inherited list look empty and Kedra would replace
    it with a list of one.

    Accepts setting *declarations* and ownership *records* alike -- both know
    their target and whether it is a membership claim. Both are needed: a module
    that has been switched off contributes no declarations, and observing only
    declarations would leave the planner unable to see the values it still owns
    and must give back.

    Fixture-driven callers pass an observed file instead; this is for a caller
    that has a live adapter. Reads only the targets it is given -- there is no
    enumeration of anything, and nothing is written.
    """
    observed: dict[str, TypedValue | None] = {}
    for item in declarations:
        target = getattr(item, "canonical_target", None) or item.target
        if target in observed:
            continue
        if getattr(item, "is_membership", False):
            observed[target] = adapter.read_effective(target)
        else:
            observed[target] = adapter.read(target)
    return observed


def load_observed(path: Path) -> dict[str, TypedValue | None]:
    """Read a fixture from an explicit path. Never reads a live session."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ObservedError(f"{path}: malformed TOML: {exc}") from exc
    except OSError as exc:
        raise ObservedError(f"{path}: unreadable: {exc}") from exc
    return parse_observed(doc, str(path))
