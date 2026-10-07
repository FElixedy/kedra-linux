#!/usr/bin/env python3
"""Tests for atomic state persistence and state locking.

Every path used here is inside a temporary directory. `TestNoRealPathsTouched`
exists to prove the lock and save paths never resolve into the invoking user's
home, since that is the one property the rest of the suite cannot show by
passing.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import TreeTestCase, state, typed  # noqa: E402
from kedra_experience import locking  # noqa: E402

X = typed("as", ["<Super>x"])
O = typed("as", ["<Super>o"])
TARGET_A = "gsettings:org.example.test:key-a"


def sample_state(base_preset="kedra", generation=1):
    return state.UserState(
        base_preset=base_preset,
        applied=state.AppliedRecord(
            generation=generation,
            preset_id="kedra",
            preset_version="0.1.0",
            experience_schema_version=3,
            applied_at="2026-08-14T12:00:00Z",
            managed_values=(
                state.ManagedValue(
                    setting_id="example-a",
                    backend="gsettings",
                    target=TARGET_A,
                    module_id="workflow",
                    module_version="0.2.0",
                    policy="managed",
                    generation=generation,
                    last_applied=X,
                    original=O,
                    original_present=True,
                ),
            ),
        ),
    )


class TestAtomicSave(TreeTestCase):
    def test_successful_save_writes_the_state(self):
        path = self.temp_dir() / "experience-state.toml"
        sample_state().save(path)

        self.assertTrue(path.is_file())
        self.assertEqual(state.UserState.load(path), sample_state())

    def test_successful_save_replaces_prior_state(self):
        path = self.temp_dir() / "experience-state.toml"
        sample_state(generation=1).save(path)
        sample_state(generation=2).save(path)

        self.assertEqual(state.UserState.load(path).applied.generation, 2)

    def test_no_temporary_file_leaks_after_success(self):
        directory = self.temp_dir()
        path = directory / "experience-state.toml"
        sample_state().save(path)

        self.assertEqual([p.name for p in directory.iterdir()], ["experience-state.toml"])

    def test_failure_before_replace_leaves_prior_state_intact(self):
        directory = self.temp_dir()
        path = directory / "experience-state.toml"
        sample_state(generation=1).save(path)
        before = path.read_text()

        with mock.patch("os.replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                sample_state(generation=2).save(path)

        self.assertEqual(path.read_text(), before)
        self.assertEqual(state.UserState.load(path).applied.generation, 1)

    def test_failed_replace_leaves_no_temporary_behind(self):
        directory = self.temp_dir()
        path = directory / "experience-state.toml"
        sample_state().save(path)

        with mock.patch("os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                sample_state(generation=2).save(path)

        self.assertEqual([p.name for p in directory.iterdir()], ["experience-state.toml"])

    def test_serialization_failure_never_touches_the_filesystem(self):
        directory = self.temp_dir()
        path = directory / "experience-state.toml"
        sample_state(generation=1).save(path)
        before = path.read_text()

        broken = sample_state(generation=2)
        with mock.patch.object(
            state.UserState, "to_toml", side_effect=state.StateError("boom")
        ):
            with self.assertRaises(state.StateError):
                broken.save(path)

        self.assertEqual(path.read_text(), before)
        self.assertEqual([p.name for p in directory.iterdir()], ["experience-state.toml"])

    def test_temporary_file_is_written_in_the_same_directory(self):
        """A cross-filesystem rename would not be atomic."""
        directory = self.temp_dir()
        path = directory / "experience-state.toml"
        seen: list[Path] = []

        real_replace = os.replace

        def record(src, dst):
            seen.append(Path(src).parent)
            return real_replace(src, dst)

        with mock.patch("os.replace", side_effect=record):
            sample_state().save(path)

        self.assertEqual(seen, [directory])

    def test_save_creates_missing_parent_directories(self):
        path = self.temp_dir() / "nested" / "deeper" / "experience-state.toml"
        sample_state().save(path)
        self.assertTrue(path.is_file())

    def test_default_permissions_are_sane(self):
        path = self.temp_dir() / "experience-state.toml"
        sample_state().save(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_existing_permissions_are_preserved(self):
        path = self.temp_dir() / "experience-state.toml"
        sample_state().save(path)
        os.chmod(path, 0o600)

        sample_state(generation=2).save(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_file_contents_are_fsynced_before_replace(self):
        path = self.temp_dir() / "experience-state.toml"
        with mock.patch("os.fsync", wraps=os.fsync) as fsync:
            sample_state().save(path)
        # At least the file, and ideally the directory too.
        self.assertGreaterEqual(fsync.call_count, 1)


class TestStateLock(TreeTestCase):
    def state_path(self) -> Path:
        return self.temp_dir() / "experience-state.toml"

    def test_lock_is_acquired_and_released(self):
        path = self.state_path()
        with locking.state_lock(path) as lock_path:
            self.assertTrue(lock_path.exists())
        # Released: a second acquisition succeeds immediately.
        with locking.state_lock(path):
            pass

    def test_concurrent_writer_is_refused(self):
        path = self.state_path()
        with locking.state_lock(path):
            with self.assertRaises(locking.StateLocked):
                with locking.state_lock(path):
                    pass

    def test_lock_is_released_on_exception(self):
        path = self.state_path()

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with locking.state_lock(path):
                raise Boom()

        # If the lock had leaked, this would raise StateLocked.
        with locking.state_lock(path):
            pass

    def test_timeout_gives_up_rather_than_hanging(self):
        path = self.state_path()
        with locking.state_lock(path):
            with self.assertRaises(locking.StateLocked):
                with locking.state_lock(path, timeout=0.1, poll=0.01):
                    pass

    def test_lock_file_is_a_sibling_not_the_state_file(self):
        """The state file is replaced by rename; a lock on it would be moot."""
        path = self.temp_dir() / "experience-state.toml"
        lock = locking.lock_path_for(path)

        self.assertNotEqual(lock, path)
        self.assertEqual(lock.parent, path.parent)
        self.assertEqual(lock.name, "experience-state.lock")

    def test_lock_path_for_an_arbitrary_state_file(self):
        path = self.temp_dir() / "custom.toml"
        self.assertEqual(locking.lock_path_for(path).name, "custom.toml.lock")

    def test_default_lock_path_is_under_xdg_state(self):
        lock = locking.default_lock_path({"XDG_STATE_HOME": "/somewhere/state"})
        self.assertEqual(lock, Path("/somewhere/state/kedra/experience-state.lock"))

    def test_default_lock_path_creates_nothing(self):
        directory = self.temp_dir()
        locking.default_lock_path({"XDG_STATE_HOME": str(directory)})
        self.assertEqual(list(directory.iterdir()), [])

    def test_lock_does_not_target_dconf_or_gnome(self):
        lock = str(locking.default_lock_path({"XDG_STATE_HOME": "/somewhere/state"}))
        for foreign in ("dconf", "gnome", "/usr/share", ".config"):
            self.assertNotIn(foreign, lock)

    def test_save_under_lock_round_trips(self):
        path = self.state_path()
        with locking.state_lock(path):
            sample_state().save(path)
        self.assertEqual(state.UserState.load(path), sample_state())

    def test_lock_survives_the_state_file_being_replaced(self):
        """Holding the lock across a save must still exclude a second writer."""
        path = self.state_path()
        with locking.state_lock(path):
            sample_state(generation=1).save(path)
            sample_state(generation=2).save(path)
            with self.assertRaises(locking.StateLocked):
                with locking.state_lock(path):
                    pass


class TestNoRealPathsTouched(unittest.TestCase):
    """Neither the lock nor the save path may resolve into the real home."""

    def test_lock_path_honours_an_injected_environment(self):
        directory = Path("/tmp/kedra-test-does-not-exist")
        lock = locking.default_lock_path({"XDG_STATE_HOME": str(directory)})
        self.assertTrue(str(lock).startswith(str(directory)))
        self.assertFalse(lock.exists())

    def test_state_lock_requires_an_explicit_path(self):
        with self.assertRaises(TypeError):
            with locking.state_lock():
                pass

    def test_real_user_state_directory_is_untouched(self):
        real = Path.home() / ".local" / "state" / "kedra"
        self.assertFalse(
            real.exists(), f"tests must never create {real}"
        )


if __name__ == "__main__":
    unittest.main()
