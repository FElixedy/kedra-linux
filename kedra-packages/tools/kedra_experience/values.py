"""Typed values for declarative settings.

Every value Kedra declares or records carries an explicit type. Nothing is
inferred from TOML's own types, because the eventual backend (GSettings) is
strictly typed and a value that round-trips through TOML as an integer may need
to be written as a double.

The type vocabulary is a deliberately small subset of GVariant type strings.
Extending it means adding an entry to VALUE_TYPES -- there is no parser for
arbitrary GVariant signatures, and no code path that evaluates anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# GVariant type string -> human description. Kept minimal on purpose: every
# type here must be representable in TOML unambiguously and comparable exactly.
VALUE_TYPES: dict[str, str] = {
    "b": "boolean",
    "i": "32-bit integer",
    "d": "double",
    "s": "string",
    "as": "array of strings",
    "ai": "array of 32-bit integers",
}

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1

VALUE_KNOWN_KEYS = frozenset({"type", "data"})


class ValueError_(Exception):
    """Raised when a typed value cannot be represented."""


@dataclass(frozen=True)
class TypedValue:
    """A value plus its GVariant-compatible type string.

    Arrays are stored as tuples so the value is hashable and two declarations
    of the same list compare equal regardless of how they were built.
    """

    type: str
    data: Any

    def __post_init__(self) -> None:
        if isinstance(self.data, list):
            object.__setattr__(self, "data", tuple(self.data))

    @classmethod
    def from_dict(cls, doc: object) -> "TypedValue":
        """Build from a parsed TOML table. Raises on anything malformed."""
        if not isinstance(doc, dict):
            raise ValueError_("value must be a table with 'type' and 'data'")
        unknown = sorted(doc.keys() - VALUE_KNOWN_KEYS)
        if unknown:
            raise ValueError_(f"value has unknown key(s): {', '.join(unknown)}")
        if "type" not in doc:
            raise ValueError_("value is missing 'type'")
        if "data" not in doc:
            raise ValueError_("value is missing 'data'")
        value = cls(type=doc["type"], data=doc["data"])
        problems = value.problems()
        if problems:
            raise ValueError_(problems[0])
        return value

    def to_dict(self) -> dict:
        data = list(self.data) if isinstance(self.data, tuple) else self.data
        return {"type": self.type, "data": data}

    def problems(self) -> list[str]:
        """Every reason this value is invalid. Empty when it is fine."""
        if self.type not in VALUE_TYPES:
            return [
                f"unknown value type {self.type!r} "
                f"(expected one of {sorted(VALUE_TYPES)})"
            ]
        return _CHECKS[self.type](self.data)

    def render(self) -> str:
        """Short human-readable form for plan output and error messages."""
        if isinstance(self.data, tuple):
            return f"{self.type}:[{', '.join(repr(item) for item in self.data)}]"
        return f"{self.type}:{self.data!r}"


def _check_bool(data: Any) -> list[str]:
    return [] if isinstance(data, bool) else [f"type 'b' requires a boolean, got {data!r}"]


def _check_int(data: Any) -> list[str]:
    if isinstance(data, bool) or not isinstance(data, int):
        return [f"type 'i' requires an integer, got {data!r}"]
    if not INT32_MIN <= data <= INT32_MAX:
        return [f"type 'i' value {data} is out of 32-bit range"]
    return []


def _check_double(data: Any) -> list[str]:
    # TOML integers are accepted for doubles; TOML has no way to write 1.0 as
    # anything but a float, but a hand-written 1 is a reasonable double.
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        return [f"type 'd' requires a number, got {data!r}"]
    return []


def _check_string(data: Any) -> list[str]:
    return [] if isinstance(data, str) else [f"type 's' requires a string, got {data!r}"]


def _check_string_array(data: Any) -> list[str]:
    if not isinstance(data, (list, tuple)):
        return [f"type 'as' requires an array, got {data!r}"]
    return [
        f"type 'as' requires string elements, got {item!r}"
        for item in data
        if not isinstance(item, str)
    ]


def _check_int_array(data: Any) -> list[str]:
    if not isinstance(data, (list, tuple)):
        return [f"type 'ai' requires an array, got {data!r}"]
    problems: list[str] = []
    for item in data:
        if isinstance(item, bool) or not isinstance(item, int):
            problems.append(f"type 'ai' requires integer elements, got {item!r}")
        elif not INT32_MIN <= item <= INT32_MAX:
            problems.append(f"type 'ai' element {item} is out of 32-bit range")
    return problems


_CHECKS = {
    "b": _check_bool,
    "i": _check_int,
    "d": _check_double,
    "s": _check_string,
    "as": _check_string_array,
    "ai": _check_int_array,
}


def values_equal(left: TypedValue | None, right: TypedValue | None) -> bool:
    """Compare two values, treating absence as a distinct state.

    Absence (None) means "no value is set" -- for GSettings, the key is at its
    vendor default. It is deliberately not equal to any concrete value, because
    "unset" and "set to the default" behave differently on revert.
    """
    if left is None or right is None:
        return left is None and right is None
    return left.type == right.type and left.data == right.data
