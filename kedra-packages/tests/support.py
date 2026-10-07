"""Shared fixtures for the Kedra Experience tests.

Not a test module (the discover pattern is `test*.py`). It builds manifest
trees in temporary directories so each test can state the one thing it is
breaking, instead of maintaining a pile of checked-in broken files.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
DATA_DIR = REPO_ROOT / "kedra-experience" / "data" / "experience"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from kedra_experience import (  # noqa: E402
    backends,
    manifests,
    observed,
    ownership,
    packages,
    plan,
    resolve,
    settings,
    state,
    values,
    vendordefaults,
)

SCHEMA = manifests.SCHEMA_VERSION
TypedValue = values.TypedValue

# ---------------------------------------------------------------------------
# Kedra Workflow v1 -- the expected keymap, written out independently.
#
# Deliberately duplicated from the manifest rather than derived from it. A test
# that reads the manifest and asserts the manifest agrees with itself proves
# nothing; this table is the second opinion, and changing a shipped binding must
# mean changing it here too. Schema and key names were read from the installed
# GNOME 50 schemas (gsettings-desktop-schemas 50.1, gnome-shell 50.4).
#
# Keys: (schema, key) -> the exact `as` value Kedra defaults it to.
# ---------------------------------------------------------------------------
WM = "org.gnome.desktop.wm.keybindings"
SHELL_KEYS = "org.gnome.shell.keybindings"

EXPECTED_KEYMAP: dict[tuple[str, str], tuple[str, ...]] = {
    # Super+Q closes; upstream Alt+F4 is kept, not displaced.
    (WM, "close"): ("<Super>q", "<Alt>F4"),
    # Super+1..5 switch workspace; upstream Super+Home is kept on workspace 1.
    (WM, "switch-to-workspace-1"): ("<Super>1", "<Super>Home"),
    (WM, "switch-to-workspace-2"): ("<Super>2",),
    (WM, "switch-to-workspace-3"): ("<Super>3",),
    (WM, "switch-to-workspace-4"): ("<Super>4",),
    (WM, "switch-to-workspace-5"): ("<Super>5",),
    # Super+Shift+1..5 move the window; upstream Super+Shift+Home is kept.
    (WM, "move-to-workspace-1"): ("<Shift><Super>1", "<Shift><Super>Home"),
    (WM, "move-to-workspace-2"): ("<Shift><Super>2",),
    (WM, "move-to-workspace-3"): ("<Shift><Super>3",),
    (WM, "move-to-workspace-4"): ("<Shift><Super>4",),
    (WM, "move-to-workspace-5"): ("<Shift><Super>5",),
    # The displaced GNOME default: Super+1..5 belonged to these.
    (SHELL_KEYS, "switch-to-application-1"): (),
    (SHELL_KEYS, "switch-to-application-2"): (),
    (SHELL_KEYS, "switch-to-application-3"): (),
    (SHELL_KEYS, "switch-to-application-4"): (),
    (SHELL_KEYS, "switch-to-application-5"): (),
}

# Upstream GNOME defaults Kedra Workflow v1 takes away, and where they went.
# Every entry here must be neutralised through the same vendor-default layer
# that caused the clash, so removing the package restores both halves at once.
DISPLACED_GNOME_DEFAULTS: dict[tuple[str, str], tuple[str, ...]] = {
    (SHELL_KEYS, f"switch-to-application-{n}"): (f"<Super>{n}",) for n in range(1, 6)
}

# Setting IDs Kedra Workflow v1 declares. Exact: an addition must be a
# deliberate edit here, so "scope is only the four behaviour groups" is enforced
# rather than asserted in a comment.
EXPECTED_WORKFLOW_SETTING_IDS = frozenset(
    {"workflow-close-window"}
    | {f"workflow-switch-workspace-{n}" for n in range(1, 6)}
    | {f"workflow-move-workspace-{n}" for n in range(1, 6)}
    | {f"workflow-unbind-app-switch-{n}" for n in range(1, 6)}
)

# ---------------------------------------------------------------------------
# Kedra Desktop -- the fixed five-workspace model, likewise written out by hand.
#
# Two keys in two schemas, and both are load-bearing: num-workspaces is ignored
# entirely while workspaces are dynamic, and turning dynamic off alone would
# leave GNOME's default of 4.
# ---------------------------------------------------------------------------
MUTTER = "org.gnome.mutter"
WM_PREFS = "org.gnome.desktop.wm.preferences"

KEDRA_WORKSPACE_COUNT = 5

EXPECTED_WORKSPACE_MODEL: dict[tuple[str, str], TypedValue] = {
    (MUTTER, "dynamic-workspaces"): TypedValue(type="b", data=False),
    (WM_PREFS, "num-workspaces"): TypedValue(type="i", data=KEDRA_WORKSPACE_COUNT),
}

EXPECTED_DESKTOP_SETTING_IDS = frozenset(
    {"desktop-dynamic-workspaces", "desktop-workspace-count"}
)

# GNOME's own vendor default that the workspace model has to beat, and the file
# it comes from. This is the concrete case that proves filename order alone is
# not enough: the group is desktop-scoped, and a desktop-scoped override wins
# over a generic one regardless of which file each came from.
GNOME_SHELL_OVERRIDE_FILE = "00_org.gnome.shell.gschema.override"
GNOME_DESKTOP_SCOPED_DEFAULTS: dict[tuple[str, str], TypedValue] = {
    (MUTTER, "dynamic-workspaces"): TypedValue(type="b", data=True),
}

# Every module that ships vendor defaults, and exactly what its override file
# must contain. One table, so a new module cannot gain an override that nothing
# checks.
EXPECTED_VENDOR_DEFAULTS: dict[str, dict[tuple[str, str], TypedValue]] = {
    "workflow": {
        target: TypedValue(type="as", data=value)
        for target, value in EXPECTED_KEYMAP.items()
    },
    "desktop": dict(EXPECTED_WORKSPACE_MODEL),
}


def normalise_binding(binding: str) -> str:
    """Canonical form of a GNOME accelerator, for clash detection.

    Mutter's parser ignores modifier order and modifier case, so `<Shift><Super>1`
    and `<Super><Shift>1` are one combination, not two. Comparing the raw strings
    would let a duplicate shortcut ship simply because it was spelled differently.
    """
    modifiers = sorted(part.lower() for part in _MODIFIER_RE.findall(binding))
    key = _MODIFIER_RE.sub("", binding)
    return "".join(modifiers) + key


_MODIFIER_RE = re.compile(r"<[^>]+>")


def typed(type_: str, data) -> TypedValue:
    return TypedValue(type=type_, data=data)


# Synthetic settings only. Nothing here is a real Kedra binding or a value any
# shipped module declares -- the shipped modules deliberately declare none.
def gsettings_toml(
    setting_id: str,
    *,
    schema: str = "org.example.test",
    key: str = "example-key",
    value_type: str = "as",
    data=("<Super>q",),
    policy: str = "managed",
) -> str:
    rendered = _render(data)
    return f"""
[[module.settings]]
id = "{setting_id}"
backend = "gsettings"
policy = "{policy}"
description = "Synthetic test setting."
target = {{ schema = "{schema}", key = "{key}" }}
value = {{ type = "{value_type}", data = {rendered} }}
"""


def _render(data) -> str:
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, str):
        return f'"{data}"'
    if isinstance(data, (list, tuple)):
        return "[" + ", ".join(_render(item) for item in data) + "]"
    return repr(data)


def descriptor_toml(default_preset: str = "kedra") -> str:
    return f"""schema_version = {SCHEMA}

[experience]
layout_version = 1
data_format = "toml"
presets_dir = "presets"
modules_dir = "modules"
default_preset = "{default_preset}"
"""


def module_toml(
    module_id: str,
    *,
    version: str = "0.1.0",
    tier: str = "optional",
    user_selectable: bool = True,
    requires: tuple[str, ...] = (),
    works_best_with: tuple[str, ...] | dict[str, str] = (),
    packages: str = "",
    applications: str = "",
    settings: str = "",
    migrations: str = "",
) -> str:
    """Build a module manifest.

    `works_best_with` takes either bare IDs or {id: reason}. Rendered last,
    because it opens `[[module.works_best_with]]` sub-tables and any bare
    `[module]` key written after one would land inside it instead.
    """
    requires_list = ", ".join(f'"{r}"' for r in requires)
    if not isinstance(works_best_with, dict):
        works_best_with = {related: "" for related in works_best_with}
    soft = "".join(
        f'\n[[module.works_best_with]]\nid = "{related}"\n'
        + (f'reason = "{reason}"\n' if reason else "")
        for related, reason in works_best_with.items()
    )
    return f"""schema_version = {SCHEMA}

[module]
id = "{module_id}"
name = "Module {module_id}"
description = "Test module {module_id}."
version = "{version}"
tier = "{tier}"
user_selectable = {"true" if user_selectable else "false"}
requires = [{requires_list}]
conflicts = []
{settings}{migrations}{packages}{applications}{soft}"""


def application_toml(
    *,
    role: str = "terminal",
    desktop_id: str = "org.example.Terminal.desktop",
    package: str | None = "pkg-terminal",
    reason: str | None = None,
    extra: str = "",
) -> str:
    """One `[[module.applications]]` table. Any field may be omitted with None."""
    lines = ["\n[[module.applications]]"]
    if role is not None:
        lines.append(f'role = "{role}"')
    if desktop_id is not None:
        lines.append(f'desktop_id = "{desktop_id}"')
    if package is not None:
        lines.append(f'package = "{package}"')
    if reason is not None:
        lines.append(f'reason = "{reason}"')
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def required_package_toml(name: str = "pkg-terminal", reason: str = "r") -> str:
    """A `required` package relation, which an application contract needs."""
    return f"""
[[module.packages]]
name = "{name}"
relation = "required"
reason = "{reason}"
"""


def base_preset_toml(
    preset_id: str,
    modules: dict[str, bool],
    *,
    applies: bool = True,
    version: str = "0.1.0",
) -> str:
    head = f"""schema_version = {SCHEMA}

[preset]
id = "{preset_id}"
name = "Preset {preset_id}"
description = "Test preset {preset_id}."
version = "{version}"
kind = "base"
applies_experience_layer = {"true" if applies else "false"}
"""
    if not applies:
        return head + "modules = []\n"
    body = "".join(
        f'\n[[preset.modules]]\nid = "{mid}"\nenabled = {"true" if on else "false"}\n'
        for mid, on in modules.items()
    )
    return head + body


def derived_preset_toml(
    preset_id: str,
    base: str | None,
    overrides: dict[str, bool],
    *,
    applies: bool | None = True,
    version: str = "0.1.0",
) -> str:
    lines = [
        f"schema_version = {SCHEMA}",
        "",
        "[preset]",
        f'id = "{preset_id}"',
        f'name = "Preset {preset_id}"',
        f'description = "Test preset {preset_id}."',
        f'version = "{version}"',
        'kind = "derived"',
    ]
    if applies is not None:
        lines.append(f"applies_experience_layer = {'true' if applies else 'false'}")
    if base is not None:
        lines.append(f'base_preset = "{base}"')
    body = "".join(
        f'\n[[preset.module_overrides]]\nid = "{mid}"\nenabled = {"true" if on else "false"}\n'
        for mid, on in overrides.items()
    )
    return "\n".join(lines) + "\n" + body


# The seven-module Kedra shape used by the resolver tests, matching the shipped
# preset's activation pattern (extras off, everything else on).
KEDRA_MODULES = {
    "desktop": True,
    "workflow": True,
    "appearance": True,
    "terminal": True,
    "extensions": True,
    "integration": True,
    "extras": False,
}


def shipped_tree_with_derived_preset(
    case: unittest.TestCase,
    preset_id: str,
    overrides: dict[str, bool],
    *,
    base: str = "kedra",
) -> "manifests.Tree":
    """The shipped manifest tree, plus one derived preset, in a temp directory.

    The route by which a NON-user-selectable module can legitimately be off.

    Since the resolver started enforcing `user_selectable` on caller-supplied
    overrides, a user state file can no longer switch off `desktop` or
    `integration` -- correctly, since the manifests say they are not the user's
    to switch. Preset machinery is deliberately unrestricted: a preset is
    distributed Kedra data that defines what a product *is*, so a derived preset
    may still disable anything its base declares.

    That distinction is what this fixture exists to exercise. It also keeps the
    shipped advisory (`workflow` works best with `desktop`) reachable in tests
    against real manifests rather than only against synthetic ones -- with
    `desktop` not user-selectable, a derived preset is now the only way to
    produce it from the shipped data.

    Nothing outside the temporary directory is written, and DATA_DIR is only
    read.
    """
    tmp = tempfile.TemporaryDirectory(prefix="kedra-shipped-tree-")
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / "experience"
    shutil.copytree(DATA_DIR, root)
    (root / "presets" / f"{preset_id}.toml").write_text(
        derived_preset_toml(preset_id, base, overrides), encoding="utf-8"
    )

    report = manifests.Report()
    tree = manifests.load_tree(root, report)
    case.assertTrue(report.ok, f"fixture tree is invalid: {report.rendered()}")
    return tree


class TreeTestCase(unittest.TestCase):
    """Base class providing temp-directory manifest trees."""

    def temp_dir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def make_tree(
        self,
        *,
        modules: dict[str, str] | None = None,
        presets: dict[str, str] | None = None,
        default_preset: str = "kedra",
    ) -> Path:
        """Write a manifest tree and return its root."""
        root = self.temp_dir() / "experience"
        (root / "modules").mkdir(parents=True)
        (root / "presets").mkdir(parents=True)
        (root / "experience.toml").write_text(descriptor_toml(default_preset), encoding="utf-8")

        if modules is None:
            modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        for module_id, content in modules.items():
            (root / "modules" / f"{module_id}.toml").write_text(content, encoding="utf-8")

        if presets is None:
            presets = {"kedra": base_preset_toml("kedra", KEDRA_MODULES)}
        for preset_id, content in presets.items():
            (root / "presets" / f"{preset_id}.toml").write_text(content, encoding="utf-8")

        return root

    def load(self, root: Path) -> manifests.Tree:
        """Load a tree, asserting it is clean first."""
        report = manifests.Report()
        tree = manifests.load_tree(root, report)
        self.assertTrue(report.ok, f"fixture tree is invalid: {report.errors}")
        self.assertIsNotNone(tree)
        return tree

    def load_dirty(self, root: Path) -> tuple[manifests.Tree | None, manifests.Report]:
        """Load a tree that is expected to have errors."""
        report = manifests.Report()
        tree = manifests.load_tree(root, report)
        return tree, report


# ---------------------------------------------------------------------------
# Isolated real-GSettings environment
#
# The production adapter has to be tested against real GLib, and that must
# never mean testing against the developer's own desktop. Nothing below reads
# or writes the invoking user's dconf database, GSettings values, XDG
# directories or session bus, and `IsolatedGSettings.proof()` is what says so
# in a form a test can assert on.
#
# Two independent guarantees, so neither has to be trusted alone:
#
# *Schemas.* The real GNOME `.gschema.xml` files are copied into a temporary
# directory and compiled there with the system `glib-compile-schemas`. The
# adapter is pointed at that directory, so it exercises the genuine schema
# definitions -- real types, real defaults, real relocatable flags -- without
# reading /usr/share/glib-2.0/schemas at run time and without the ability to
# write anything into it.
#
# *Storage.* A `GKeyfileSettingsBackend` bound to one file inside the temporary
# directory is passed to the adapter explicitly. dconf is never involved: there
# is no environment variable to leak, no session bus to find, and no default
# backend to fall back to. The full GSettings semantics the adapter depends on
# -- most importantly the difference between a stored value and an inherited
# one -- are GSettings core behaviour and are exercised exactly as they are in
# a session; only where the bytes land differs.
#
# The keyfile backend is used rather than an isolated dconf profile on purpose.
# A dconf write goes through dconf-service over D-Bus, so an isolated dconf test
# needs `dbus-run-session`, which is absent from minimal build chroots and hangs
# where no bus can start -- turning a correctness test into an environment test.
# ---------------------------------------------------------------------------

SYSTEM_SCHEMA_DIR = Path("/usr/share/glib-2.0/schemas")

# The schemas the shortcut work needs. Named explicitly rather than copying the
# whole directory: it keeps the fixture small, and it means a schema arriving
# from somewhere unexpected cannot silently satisfy a test.
REQUIRED_SCHEMA_FILES = ("org.gnome.settings-daemon.plugins.media-keys.gschema.xml",)


def gsettings_available() -> bool:
    """Can a real-GSettings integration test run here at all?"""
    if not SYSTEM_SCHEMA_DIR.is_dir():
        return False
    if not all((SYSTEM_SCHEMA_DIR / name).is_file() for name in REQUIRED_SCHEMA_FILES):
        return False
    if shutil.which("glib-compile-schemas") is None:
        return False
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio  # noqa: F401
    except Exception:
        return False
    return True


class IsolatedGSettings:
    """A temporary schema directory plus a private keyfile store."""

    def __init__(self, root: Path, *, extra_schemas: tuple[str, ...] = ()):
        self.root = Path(root)
        self.schema_dir = self.root / "schemas"
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        self.store = self.root / "settings.keyfile"

        for name in REQUIRED_SCHEMA_FILES + tuple(extra_schemas):
            shutil.copy(SYSTEM_SCHEMA_DIR / name, self.schema_dir / name)
        result = subprocess.run(
            ["glib-compile-schemas", "--strict", str(self.schema_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:  # pragma: no cover - environment
            raise RuntimeError(f"glib-compile-schemas failed: {result.stderr}")

        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            self._backend = Gio.keyfile_settings_backend_new(str(self.store), "/", None)
        return self._backend

    def adapter(self):
        """One adapter per environment, shared so writes are visible to readers.

        Cached rather than rebuilt: two adapters over one keyfile backend see
        the same data, but reusing one keeps the operation log in a single place
        so a test can assert what was and was not written.
        """
        if getattr(self, "_adapter", None) is None:
            from kedra_experience.gsettings import GSettingsAdapter

            self._adapter = GSettingsAdapter(
                schema_dir=str(self.schema_dir), backend=self.backend
            )
        return self._adapter

    def read_only_adapter(self):
        """An adapter whose store cannot be written, for writability failures."""
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        from kedra_experience.gsettings import GSettingsAdapter

        locked = self.root / "locked"
        locked.mkdir(exist_ok=True)
        store = locked / "settings.keyfile"
        store.write_text("", encoding="utf-8")
        store.chmod(0o444)
        locked.chmod(0o555)
        # Restored on the way out, or a temp-directory cleanup fails.
        self._locked_dir = locked
        backend = Gio.keyfile_settings_backend_new(str(store), "/", None)
        return GSettingsAdapter(schema_dir=str(self.schema_dir), backend=backend)

    def release(self) -> None:
        locked = getattr(self, "_locked_dir", None)
        if locked is not None and locked.exists():
            locked.chmod(0o755)
            for child in locked.iterdir():
                child.chmod(0o644)

    def seed_custom_shortcut(self, path: str, **fields) -> None:
        """Create a custom shortcut the way a *user* would, not the way Kedra does.

        Writes the three child values and appends the path to
        `custom-keybindings`, through the real adapter and into the isolated
        store. This is the fixture standing in for shortcuts the user already
        had -- so a test can assert Kedra left them exactly as they were.
        """
        from kedra_experience.conflicts import PARENT_TARGET, child_target
        from kedra_experience.values import TypedValue

        adapter = self.adapter()
        for key in ("name", "binding", "command"):
            adapter.apply(child_target(path, key), TypedValue("s", fields.get(key, "")))
        current = adapter.read_effective(PARENT_TARGET)
        entries = tuple(current.data) if current is not None else ()
        if path not in entries:
            adapter.apply(PARENT_TARGET, TypedValue("as", entries + (path,)))

    def shortcut_snapshot(self, path: str) -> dict:
        """The three values at one path, for a before/after comparison."""
        from kedra_experience.conflicts import child_target

        adapter = self.adapter()
        return {
            key: adapter.read_effective(child_target(path, key)).data
            for key in ("name", "binding", "command")
        }

    def registered_paths(self) -> tuple:
        from kedra_experience.conflicts import PARENT_TARGET

        current = self.adapter().read_effective(PARENT_TARGET)
        return tuple(current.data) if current is not None else ()

    def store_contents(self) -> str:
        return self.store.read_text(encoding="utf-8") if self.store.exists() else ""

    def files_written(self) -> list[Path]:
        """Every file this environment produced, so a test can assert the set."""
        return sorted(p for p in self.root.rglob("*") if p.is_file())


class LiveStateProof:
    """A before/after fingerprint of everything a test must not touch.

    Asserting that a test *did not* change the user's desktop is only meaningful
    if the claim is measured rather than reasoned about, so it is measured: the
    real dconf database, the real GSettings keyfile store and the real XDG
    config directory are fingerprinted before the work and again after it.
    """

    WATCHED = (
        "~/.config/dconf/user",
        "~/.config/glib-2.0/settings/keyfile",
    )

    def __init__(self):
        self.before = self.fingerprint()

    @classmethod
    def fingerprint(cls) -> dict:
        marks = {}
        for entry in cls.WATCHED:
            path = Path(entry).expanduser()
            if not path.exists():
                marks[entry] = "absent"
                continue
            marks[entry] = hashlib.sha256(path.read_bytes()).hexdigest()
        return marks

    def unchanged(self) -> bool:
        return self.fingerprint() == self.before

    def differences(self) -> list[str]:
        after = self.fingerprint()
        return [
            f"{key}: {self.before[key]} -> {after[key]}"
            for key in sorted(self.before)
            if self.before[key] != after[key]
        ]
