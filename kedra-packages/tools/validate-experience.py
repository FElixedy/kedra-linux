#!/usr/bin/env python3
"""Validate Kedra Experience preset and module manifests.

Thin entry point. The implementation moved to kedra_experience.manifests when
the resolver and user-state model were added and needed to share it; the names
this module re-exports are kept stable so existing callers and tests do not
have to care.

Stdlib only: TOML is parsed with `tomllib` (Python 3.11+), so validation adds
no runtime dependency to the package or to the build environment.

Usage:
    validate-experience.py [DATA_DIR ...]

DATA_DIR is a directory containing experience.toml alongside presets/ and
modules/. Defaults to the in-tree package data. Additional standalone manifests
may be passed as file paths; they are validated in isolation against the schema
but not cross-checked.

Exit status is 0 when every manifest is valid, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kedra_experience.manifests import (  # noqa: E402
    CONDITION_TYPES,
    FORBIDDEN_MODULE_KEYS,
    MODULE_KNOWN_KEYS,
    MODULE_REQUIRED_KEYS,
    MODULE_TIERS,
    PACKAGE_KNOWN_KEYS,
    PACKAGE_RELATIONS,
    PRESET_KINDS,
    PRESET_KNOWN_KEYS,
    PRESET_REQUIRED_KEYS,
    SCHEMA_VERSION,
    Report,
    Tree,
    check_keys,
    check_schema_version,
    default_data_dir,
    detect_cycles,
    is_identifier,
    is_semver,
    load_toml,
    load_tree,
    main,
    setting_index,
    validate_condition,
    validate_migrations,
    validate_settings,
    validate_module,
    validate_packages,
    validate_preset,
    validate_tree,
)

__all__ = [
    "CONDITION_TYPES",
    "FORBIDDEN_MODULE_KEYS",
    "MODULE_KNOWN_KEYS",
    "MODULE_REQUIRED_KEYS",
    "MODULE_TIERS",
    "PACKAGE_KNOWN_KEYS",
    "PACKAGE_RELATIONS",
    "PRESET_KINDS",
    "PRESET_KNOWN_KEYS",
    "PRESET_REQUIRED_KEYS",
    "SCHEMA_VERSION",
    "Report",
    "Tree",
    "check_keys",
    "check_schema_version",
    "default_data_dir",
    "detect_cycles",
    "is_identifier",
    "is_semver",
    "load_toml",
    "load_tree",
    "main",
    "setting_index",
    "validate_condition",
    "validate_migrations",
    "validate_settings",
    "validate_module",
    "validate_packages",
    "validate_preset",
    "validate_tree",
]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
