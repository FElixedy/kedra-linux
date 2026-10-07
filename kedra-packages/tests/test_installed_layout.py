"""The shipped command, run from the installed layout with no checkout present.

Every other test in this suite imports `kedra_experience` from `tools/`. That
proves the logic and proves nothing about the package: the audit's B1 finding
was precisely that production code resolved its data directory relative to a
source tree, which works perfectly on the machine that wrote it and not at all
on a target.

So this builds the real thing. It extracts the built `kedra-experience` and
`kedra-experience-runtime` packages into a root, then runs
/usr/bin/kedra-experience inside a sandbox where:

  * /usr/lib/kedra and /usr/share/kedra come from the extracted packages,
  * /home is not mounted, so the checkout does not exist at any path,
  * nothing is writable outside a throwaway tmpfs.

`ExtractedPackagesTests` additionally checks the extracted trees themselves --
install paths, permissions, and that no mutating module was packaged -- and
those run without a sandbox.

Both groups skip when the packages have not been built (`bwrap` likewise), so a
plain `python3 -m unittest discover` on a checkout still passes. They do NOT
skip inside `makepkg`, where the packages exist: see tools/kedra-packages-build,
which builds first and re-runs this module against the artifacts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from support import REPO_ROOT

# Where tools/kedra-packages-build leaves finished packages. Overridable so the
# suite can be pointed at any staging directory.
PKGDEST = Path(os.environ.get("KEDRA_PKGDEST", Path.home() / "Build" / "kedra-staging"))

REQUIRED_PACKAGES = ("kedra-experience", "kedra-experience-runtime")

INSTALLED_CLI = "/usr/bin/kedra-experience"
INSTALLED_DATA = "/usr/share/kedra/experience"
INSTALLED_LIB = "/usr/lib/kedra/experience"


def _newest_package(name: str) -> Path | None:
    """The most recent build of `name`, by mtime.

    Matched on the `<name>-<version>-<rel>-<arch>.pkg.tar.*` shape rather than a
    bare prefix, so `kedra-experience-*` cannot pick up `kedra-experience-runtime`.
    """
    candidates = [
        path
        for path in PKGDEST.glob(f"{name}-*.pkg.tar.*")
        if not path.name.endswith(".sig")
        and path.name[len(name) + 1 :].split("-")[0][:1].isdigit()
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _extract(package: Path, root: Path) -> None:
    with tarfile.open(package) as archive:
        members = [m for m in archive.getmembers() if not m.name.startswith(".")]
        archive.extractall(root, members=members, filter="tar")


class InstalledLayoutMixin:
    """Extracts the built packages into one root shared by the test class."""

    root: Path
    _tmp: tempfile.TemporaryDirectory

    @classmethod
    def setUpClass(cls):
        missing = [name for name in REQUIRED_PACKAGES if _newest_package(name) is None]
        if missing:
            raise unittest.SkipTest(
                f"not built: {', '.join(missing)} (looked in {PKGDEST}; "
                f"run tools/kedra-packages-build)"
            )
        cls._tmp = tempfile.TemporaryDirectory(prefix="kedra-installed-")
        cls.root = Path(cls._tmp.name)
        for name in REQUIRED_PACKAGES:
            _extract(_newest_package(name), cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class ExtractedPackagesTests(InstalledLayoutMixin, unittest.TestCase):
    """What the packages actually put on disk."""

    def test_the_manifest_tree_lands_where_production_reads_it(self):
        data = self.root / INSTALLED_DATA.lstrip("/")
        self.assertTrue((data / "experience.toml").is_file())
        self.assertTrue((data / "presets" / "kedra.toml").is_file())
        self.assertTrue((data / "presets" / "vanilla.toml").is_file())
        self.assertTrue((data / "modules" / "integration.toml").is_file())

    def test_the_installed_path_constant_names_that_directory(self):
        # Keeps manifests.INSTALLED_DATA_DIR and kedra-experience's package()
        # from drifting apart -- the drift would only show up on a target.
        from kedra_experience.manifests import INSTALLED_DATA_DIR

        self.assertTrue((self.root / str(INSTALLED_DATA_DIR).lstrip("/")).is_dir())

    def test_the_library_lands_on_the_private_path(self):
        lib = self.root / INSTALLED_LIB.lstrip("/") / "kedra_experience"
        self.assertTrue((lib / "prodcli.py").is_file())
        self.assertTrue((lib / "__init__.py").is_file())

    def test_the_library_is_not_installed_into_site_packages(self):
        # It is Kedra-internal. `import kedra_experience` from an unrelated
        # program must keep failing.
        self.assertEqual(list((self.root / "usr" / "lib").glob("python*")), [])

    def test_exactly_the_declared_modules_are_shipped(self):
        from kedra_experience import RUNTIME_MODULES

        lib = self.root / INSTALLED_LIB.lstrip("/") / "kedra_experience"
        shipped = sorted(p.stem for p in lib.glob("*.py"))
        self.assertEqual(shipped, sorted(RUNTIME_MODULES))

    def test_no_unshipped_module_reached_the_package(self):
        from kedra_experience import UNSHIPPED_MODULES

        lib = self.root / INSTALLED_LIB.lstrip("/") / "kedra_experience"
        for name in UNSHIPPED_MODULES:
            with self.subTest(module=name):
                self.assertFalse(
                    (lib / f"{name}.py").exists(),
                    f"{name}.py must not be on a target",
                )

    def test_no_bytecode_cache_is_shipped(self):
        # A .pyc built here would carry $pkgdir paths in its header.
        lib = self.root / INSTALLED_LIB.lstrip("/")
        self.assertEqual(list(lib.rglob("__pycache__")), [])

    def test_the_entry_point_is_executable(self):
        cli = self.root / INSTALLED_CLI.lstrip("/")
        self.assertTrue(cli.is_file())
        self.assertTrue(os.access(cli, os.X_OK))

    def test_the_entry_point_hardcodes_the_private_library_path(self):
        # Not generated and not substituted at build time: what runs on a Kedra
        # system must not depend on where the package was built.
        text = (self.root / INSTALLED_CLI.lstrip("/")).read_text(encoding="utf-8")
        self.assertIn(INSTALLED_LIB, text)
        self.assertNotIn("@", text.split('"""')[-1])  # no unsubstituted placeholder

    def test_the_entry_point_names_no_build_path(self):
        text = (self.root / INSTALLED_CLI.lstrip("/")).read_text(encoding="utf-8")
        for leak in (str(REPO_ROOT), str(Path.home()), "startdir", "pkgdir", "srcdir"):
            self.assertNotIn(leak, text)

    def test_the_packages_own_nothing_unexpected(self):
        owned = sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() or p.is_symlink()
        )
        allowed_prefixes = (
            "usr/lib/kedra/",
            "usr/share/kedra/",
            "usr/share/doc/",
            # The first-login carrier. User-scoped, because the work is only
            # possible inside the user's own graphical session.
            "usr/lib/systemd/user/",
        )
        for path in owned:
            with self.subTest(path=path):
                self.assertTrue(
                    path.startswith(allowed_prefixes) or path == "usr/bin/kedra-experience",
                    f"unexpected packaged file: {path}",
                )

    def test_the_activation_unit_is_installed_and_enabled(self):
        unit = self.root / "usr/lib/systemd/user/kedra-experience-activate.service"
        wants = (
            self.root
            / "usr/lib/systemd/user/graphical-session.target.wants"
            / "kedra-experience-activate.service"
        )
        self.assertTrue(unit.is_file())
        # A package-owned wants symlink rather than an install scriptlet: a
        # scriptlet cannot enable a *user* unit for users who do not exist yet,
        # and `systemctl --global enable` would leave a dangling symlink in /etc.
        self.assertTrue(wants.is_symlink())

    def test_the_unit_runs_only_the_safe_verb(self):
        unit = (
            self.root / "usr/lib/systemd/user/kedra-experience-activate.service"
        ).read_text(encoding="utf-8")
        exec_lines = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
        self.assertEqual(exec_lines, ["ExecStart=/usr/bin/kedra-experience apply --format json"])
        for forbidden in ("--operation", "unmanage", "restore", "--force"):
            self.assertNotIn(forbidden, " ".join(exec_lines))

    def test_the_unit_does_not_run_without_a_profile(self):
        unit = (
            self.root / "usr/lib/systemd/user/kedra-experience-activate.service"
        ).read_text(encoding="utf-8")
        # %S is $XDG_STATE_HOME. A user who never chose a profile never runs it.
        self.assertIn("ConditionPathExists=%S/kedra/experience-state.toml", unit)

    def test_a_blocked_shortcut_is_not_a_failed_unit(self):
        unit = (
            self.root / "usr/lib/systemd/user/kedra-experience-activate.service"
        ).read_text(encoding="utf-8")
        self.assertIn("SuccessExitStatus=3", unit)

    def test_the_unit_is_user_scoped(self):
        # Never a system unit: a root service writing somebody's dconf is the
        # architecture this deliberately avoids.
        self.assertEqual(list((self.root / "usr/lib/systemd").glob("system*")), [])


@unittest.skipIf(shutil.which("bwrap") is None, "bubblewrap is not installed")
class SandboxedInstalledLayoutTests(InstalledLayoutMixin, unittest.TestCase):
    """The installed command, with the source checkout genuinely unreachable."""

    def sandbox(self, *command: str) -> list[str]:
        """The bwrap invocation everything in this class runs inside.

        One definition, used by the CLI runs and by the probes that check the
        sandbox is what it claims to be. Duplicating it once already produced a
        probe that failed for its own reasons and read as a real finding.

        The host's /usr supplies the interpreter; the extracted package's /usr
        is layered over it read-only, so /usr/bin/kedra-experience,
        /usr/lib/kedra and /usr/share/kedra are the packaged ones. /home is
        never bound, which is what makes "does this secretly need the source
        tree?" an answerable question rather than an assurance.
        """
        return [
            "bwrap",
            "--overlay-src", "/usr",
            "--overlay-src", str(self.root / "usr"),
            "--ro-overlay", "/usr",
            "--ro-bind", "/etc", "/etc",
            # All four are needed: without /lib64 the dynamic loader is not
            # found and execvp fails before anything under test runs.
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/bin", "/sbin",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--chdir", "/tmp",
            "--unshare-all",
            "--die-with-parent",
            "--clearenv",
            "--setenv", "PATH", "/usr/bin",
            *command,
        ]

    def run_sandboxed(self, *command: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            self.sandbox(*command),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def run_cli(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        """Run the packaged /usr/bin/kedra-experience.

        No --data-dir is passed anywhere in this class. The command must find
        /usr/share/kedra/experience by itself or fail.
        """
        return self.run_sandboxed(INSTALLED_CLI, *args, stdin=stdin)

    def assert_absent_in_sandbox(self, path: str) -> None:
        """Assert `path` does not exist inside the sandbox."""
        probe = self.run_sandboxed(
            "/usr/bin/python3",
            "-c",
            "import pathlib,sys; sys.exit(0 if not pathlib.Path(sys.argv[1]).exists() else 1)",
            path,
        )
        self.assertEqual(
            probe.returncode,
            0,
            f"{path} is reachable inside the sandbox (stderr: {probe.stderr.strip()})",
        )

    def resolve(self, selection: dict) -> tuple[int, dict]:
        result = self.run_cli("resolve", "--selection", "-", stdin=json.dumps(selection))
        self.assertNotEqual(result.returncode, 1, f"resolve failed: {result.stderr}")
        return result.returncode, json.loads(result.stdout)

    # -- the sandbox is what it claims to be ------------------------------

    def test_the_sandbox_can_run_the_installed_command_at_all(self):
        # If this fails, every "not reachable" assertion below would pass for
        # the wrong reason.
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kedra-experience", result.stdout)

    def test_the_source_checkout_is_not_reachable(self):
        self.assert_absent_in_sandbox(str(REPO_ROOT))

    def test_the_repository_tooling_is_not_installed(self):
        # kedra-experience-dev is repository infrastructure and must never end
        # up on a target.
        self.assert_absent_in_sandbox("/usr/bin/kedra-experience-dev")

    def test_the_developer_library_path_is_not_present(self):
        self.assert_absent_in_sandbox(str(REPO_ROOT / "tools" / "kedra_experience"))

    # -- profiles ---------------------------------------------------------

    def test_profiles_works_from_the_installed_tree_alone(self):
        result = self.run_cli("profiles", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["profiles_schema_version"], 1)
        self.assertEqual(document["data_dir"], INSTALLED_DATA)
        self.assertEqual(
            sorted(p["id"] for p in document["presets"]), ["kedra", "vanilla"]
        )

    def test_profiles_reports_the_default_preset(self):
        document = json.loads(self.run_cli("profiles").stdout)
        self.assertEqual(document["default_preset"], "vanilla")

    def test_json_is_the_default_format(self):
        # `profiles` with no --format must still be machine-readable, because a
        # caller that forgets the flag should not silently get prose.
        json.loads(self.run_cli("profiles").stdout)

    # -- resolve: the three products --------------------------------------

    def test_resolve_vanilla(self):
        status, document = self.resolve({"base_preset": "vanilla"})
        self.assertEqual(status, 0)
        self.assertFalse(document["applies_experience_layer"])
        self.assertEqual(document["packages"]["required"], [])
        self.assertEqual(document["modules"], [])
        self.assertEqual(document["applications"], [])

    def test_resolve_kedra(self):
        status, document = self.resolve({"base_preset": "kedra"})
        self.assertEqual(status, 0)
        self.assertTrue(document["applies_experience_layer"])
        self.assertEqual(
            document["packages"]["required"],
            [
                "gnome-console",
                "gnome-shell",
                "kedra-experience",
                "kedra-experience-desktop",
                "kedra-experience-runtime",
                "kedra-experience-workflow",
            ],
        )

    def test_resolve_custom_with_workflow_disabled(self):
        status, document = self.resolve(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "workflow", "enabled": False}],
            }
        )
        self.assertEqual(status, 0)
        required = document["packages"]["required"]
        self.assertNotIn("kedra-experience-workflow", required)
        self.assertIn("kedra-experience-desktop", required)
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)

    def test_resolve_reads_the_installed_tree_not_some_other_one(self):
        _, document = self.resolve({"base_preset": "kedra"})
        self.assertEqual(document["data_dir"], INSTALLED_DATA)

    # -- failure modes ----------------------------------------------------

    def test_a_bad_selection_produces_no_document(self):
        result = self.run_cli("resolve", "--selection", "-", stdin='{"nope": 1}')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_no_force_or_restore_verb_exists_on_the_installed_command(self):
        # `apply` and `unmanage` are the two lifecycle operations a session
        # needs. Restoring defaults reclaims a resource over a user's own
        # divergence, and there is no switch that smuggles it in either.
        for verb in ("restore", "restore-defaults", "force", "execute", "plan"):
            with self.subTest(verb=verb):
                result = self.run_cli(verb)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid choice", result.stderr)

    def test_apply_on_a_system_with_no_profile_is_a_successful_no_op(self):
        # The Vanilla guarantee, from the packaged command, in a sandbox with no
        # state file anywhere.
        result = self.run_cli("apply", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "no-state")

    def test_it_works_with_pygobject_unimportable(self):
        """The `depends` line claims PyGObject is not needed. This checks it.

        Inspection already says so -- no shipped module imports `gi` at module
        scope, and the one lazy `import gi` in a shipped module
        (`shortcuts.launchability_problems`) is called from tests only. But the
        sandbox borrows the host's /usr, which has PyGObject installed, so every
        other test here would pass either way.

        So `gi` is made to raise on import, and both commands must still work.
        A shim on PYTHONPATH shadows the real module: the entry point inserts
        the private library path at sys.path[0] and `gi` is not there, so the
        shim is what the next lookup finds.
        """
        shim = Path(self._tmp.name) / "no-gi"
        shim.mkdir(exist_ok=True)
        (shim / "gi.py").write_text(
            'raise ImportError("PyGObject is deliberately unavailable in this test")\n',
            encoding="utf-8",
        )

        command = self.sandbox(
            "--ro-bind", str(shim), "/opt/no-gi",
            "--setenv", "PYTHONPATH", "/opt/no-gi",
            INSTALLED_CLI, "resolve", "--selection", "-",
        )
        result = subprocess.run(
            command,
            input=json.dumps({"base_preset": "kedra"}),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertIn("kedra-experience-runtime", document["packages"]["required"])
        # The terminal contract's launch command is derived by string
        # manipulation, so it must survive too.
        self.assertEqual(
            document["applications"][0]["launch_command"],
            "gapplication launch org.gnome.Console",
        )

    def test_the_gi_shim_would_actually_bite(self):
        # Guards the guard: if the shim were never imported, the test above
        # would prove nothing.
        shim = Path(self._tmp.name) / "no-gi"
        shim.mkdir(exist_ok=True)
        (shim / "gi.py").write_text('raise ImportError("boom")\n', encoding="utf-8")
        command = self.sandbox(
            "--ro-bind", str(shim), "/opt/no-gi",
            "--setenv", "PYTHONPATH", "/opt/no-gi",
            "/usr/bin/python3", "-c", "import gi",
        )
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("boom", result.stderr)

    def test_the_installed_command_writes_nothing(self):
        # /usr is a read-only overlay in the sandbox, so any write attempt would
        # surface as a failure rather than as a silent success.
        before = sorted(p.name for p in (self.root / "usr" / "share" / "kedra").rglob("*"))
        self.resolve({"base_preset": "kedra"})
        self.run_cli("profiles")
        after = sorted(p.name for p in (self.root / "usr" / "share" / "kedra").rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
