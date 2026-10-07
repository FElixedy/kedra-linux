"""Test harness for the Kedra Experience installer modules.

Two things are faked and one is emphatically not.

Faked: `libcalamares`, because these modules are Python job modules that
Calamares imports into its own interpreter, and GlobalStorage, which is a C++
object. The stub below is the whole of the API they use.

NOT faked: the resolver. Every test drives the real `kedra-experience` command
from the kedra-packages checkout, against the real shipped manifests. That is
deliberate -- a fake resolver would let this suite agree with itself about
package sets while the actual product disagreed, which is exactly the class of
drift the installer's no-duplication rule exists to prevent. The one thing the
wrapper adds is `--data-dir`, because the manifests are in a checkout here
rather than installed at /usr/share/kedra/experience.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

CALAMARES_ROOT = Path(__file__).resolve().parent.parent.parent
MODULES_DIR = CALAMARES_ROOT / "src" / "modules"

# The Experience sources. Overridable so CI can put the checkout elsewhere.
KEDRA_PACKAGES = Path(
    os.environ.get("KEDRA_PACKAGES", CALAMARES_ROOT.parent.parent / "kedra-packages")
)
EXPERIENCE_DATA = KEDRA_PACKAGES / "kedra-experience" / "data" / "experience"
EXPERIENCE_CLI = KEDRA_PACKAGES / "tools" / "kedra-experience"


def experience_available() -> bool:
    return EXPERIENCE_CLI.is_file() and EXPERIENCE_DATA.is_dir()


# ---------------------------------------------------------------------------
# The libcalamares stub
# ---------------------------------------------------------------------------


class FakeGlobalStorage:
    """The four methods the modules use, over a plain dict."""

    def __init__(self, initial: dict | None = None):
        self._data = dict(initial or {})

    def contains(self, key):
        return key in self._data

    def value(self, key):
        return self._data.get(key)

    def insert(self, key, value):
        self._data[key] = value

    def remove(self, key):
        self._data.pop(key, None)

    def keys(self):
        return list(self._data)


class FakeJob:
    def __init__(self, configuration: dict | None = None):
        self.configuration = dict(configuration or {})


def install_libcalamares_stub() -> types.ModuleType:
    """Put a `libcalamares` into sys.modules, and return it."""
    module = types.ModuleType("libcalamares")
    utils = types.ModuleType("libcalamares.utils")

    module.messages = []

    def _record(level):
        def log(text):
            module.messages.append((level, str(text)))

        return log

    utils.debug = _record("debug")
    utils.warning = _record("warning")
    utils.error = _record("error")
    utils.gettext_path = lambda: None
    utils.gettext_languages = lambda: ["en"]

    module.utils = utils
    module.globalstorage = FakeGlobalStorage()
    module.job = FakeJob()

    sys.modules["libcalamares"] = module
    sys.modules["libcalamares.utils"] = utils
    return module


def load_module(name: str):
    """Import a Calamares Python job module's main.py under a unique name."""
    path = MODULES_DIR / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"kedratest_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Resolver wrappers
# ---------------------------------------------------------------------------


def write_resolver_wrapper(directory: Path) -> Path:
    """A `kedra-experience` that reads the in-checkout manifests.

    Byte-for-byte the real command; only --data-dir is supplied, which a target
    never needs because the manifests are installed there.
    """
    wrapper = directory / "kedra-experience"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'exec {sys.executable} "{EXPERIENCE_CLI}" --data-dir "{EXPERIENCE_DATA}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def write_fake_resolver(directory: Path, *, stdout: str, status: int = 0) -> Path:
    """A stand-in that answers with fixed text, for the failure-mode tests."""
    fake = directory / "fake-kedra-experience"
    fake.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        + "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in stdout.splitlines())
        + f"exit {status}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


@unittest.skipUnless(experience_available(), f"kedra-packages checkout not at {KEDRA_PACKAGES}")
class InstallerModuleTestCase(unittest.TestCase):
    """Base: a stubbed libcalamares, a real resolver, and a scratch directory."""

    module_name: str = ""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kedra-installer-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        self.libcalamares = install_libcalamares_stub()
        self.gs = self.libcalamares.globalstorage
        self.resolver = write_resolver_wrapper(self.tmp)
        self.libcalamares.job = FakeJob({"resolver": str(self.resolver)})

        if self.module_name:
            self.module = load_module(self.module_name)

    # -- helpers ---------------------------------------------------------

    def set_selection(self, key: str, selection: dict) -> None:
        self.gs.insert(key, json.dumps(selection))

    def package_operations(self) -> list:
        return self.gs.value("packageOperations") or []

    def injected_packages(self) -> list:
        """Names this installer's Experience module added, and only those."""
        for operation in self.package_operations():
            if operation.get("source") == "kedraexperiencepackages":
                return list(operation.get("install", []))
        return []

    def resolve_directly(self, selection: dict) -> dict:
        """What the resolver says, independently of the module under test.

        The tests compare the module's behaviour against this rather than
        against a list written out here, so a change to the shipped manifests
        moves both sides together and cannot be missed.
        """
        completed = subprocess.run(
            [str(self.resolver), "resolve", "--selection", "-"],
            input=json.dumps(selection),
            capture_output=True,
            text=True,
            check=False,
        )
        return json.loads(completed.stdout)
