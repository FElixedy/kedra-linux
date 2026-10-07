#!/usr/bin/env python3
"""Tests for preset inheritance and resolution.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    DATA_DIR,
    KEDRA_MODULES,
    TreeTestCase,
    base_preset_toml,
    derived_preset_toml,
    manifests,
    module_toml,
    resolve,
    state,
)

ResolutionError = resolve.ResolutionError


class TestResolveBasePresets(TreeTestCase):
    def test_resolves_kedra(self):
        tree = self.load(self.make_tree())
        result = resolve.resolve_preset(tree, "kedra")

        self.assertTrue(result.applies_experience_layer)
        self.assertEqual(result.preset_id, "kedra")
        self.assertEqual(result.chain, ("kedra",))
        self.assertEqual(
            {m.id: m.enabled for m in result.modules},
            KEDRA_MODULES,
        )

    def test_inherited_enabled_and_disabled_modules_keep_their_state(self):
        tree = self.load(self.make_tree())
        result = resolve.resolve_preset(tree, "kedra")
        by_id = {m.id: m for m in result.modules}

        self.assertTrue(by_id["desktop"].enabled)
        self.assertFalse(by_id["extras"].enabled)
        for module in result.modules:
            self.assertEqual(module.source, "preset:kedra")

    def test_enabled_module_ids_helper(self):
        tree = self.load(self.make_tree())
        result = resolve.resolve_preset(tree, "kedra")
        self.assertNotIn("extras", result.enabled_module_ids)
        self.assertIn("desktop", result.enabled_module_ids)

    def test_module_metadata_is_carried_through(self):
        tree = self.load(
            self.make_tree(
                modules={
                    **{mid: module_toml(mid) for mid in KEDRA_MODULES},
                    "terminal": module_toml("terminal", version="2.3.4", tier="core"),
                }
            )
        )
        result = resolve.resolve_preset(tree, "kedra")
        terminal = {m.id: m for m in result.modules}["terminal"]
        self.assertEqual(terminal.version, "2.3.4")
        self.assertEqual(terminal.tier, "core")

    def test_unknown_preset_is_rejected(self):
        tree = self.load(self.make_tree())
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "ghost")
        self.assertIn("unknown preset 'ghost'", str(ctx.exception))


class TestVanillaShortCircuit(TreeTestCase):
    def vanilla_tree(self, extra_presets: dict[str, str] | None = None):
        presets = {
            "kedra": base_preset_toml("kedra", KEDRA_MODULES),
            "vanilla": base_preset_toml("vanilla", {}, applies=False),
        }
        presets.update(extra_presets or {})
        return self.load(self.make_tree(presets=presets))

    def test_vanilla_resolves_to_no_modules(self):
        tree = self.vanilla_tree()
        result = resolve.resolve_preset(tree, "vanilla")

        self.assertFalse(result.applies_experience_layer)
        self.assertEqual(result.modules, ())
        self.assertEqual(result.enabled_module_ids, ())

    def test_vanilla_is_not_every_module_disabled(self):
        """The distinction the architecture rests on.

        Vanilla must not enumerate modules at all. A resolver that returned
        seven disabled modules would invite a caller to 'apply' each one.
        """
        tree = self.vanilla_tree()
        vanilla = resolve.resolve_preset(tree, "vanilla")
        kedra = resolve.resolve_preset(tree, "kedra")

        self.assertEqual(len(vanilla.modules), 0)
        self.assertEqual(len(kedra.modules), 7)

    def test_deriving_from_vanilla_and_enabling_the_layer_is_rejected(self):
        tree = self.vanilla_tree(
            {"hybrid": derived_preset_toml("hybrid", "vanilla", {}, applies=True)}
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "hybrid")
        message = str(ctx.exception)
        self.assertIn("may not change whether the Experience layer applies", message)

    def test_deriving_from_kedra_and_disabling_the_layer_is_rejected(self):
        tree = self.vanilla_tree(
            {"halfway": derived_preset_toml("halfway", "kedra", {}, applies=False)}
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "halfway")
        self.assertIn("may not change whether the Experience layer applies", str(ctx.exception))

    def test_overriding_modules_without_the_layer_is_rejected(self):
        """Caught at resolution even if a manifest smuggled it past validation."""
        tree = self.vanilla_tree()
        user_state = state.UserState(
            base_preset="vanilla",
            module_overrides=(state.ModuleOverride(id="desktop", enabled=True),),
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_state(tree, user_state)
        self.assertIn("does not apply the Experience layer", str(ctx.exception))


class TestDerivedPresets(TreeTestCase):
    def custom_tree(self, overrides: dict[str, bool], base: str | None = "kedra"):
        return self.load(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "custom": derived_preset_toml("custom", base, overrides),
                }
            )
        )

    def test_override_disables_an_inherited_enabled_module(self):
        tree = self.custom_tree({"terminal": False})
        result = resolve.resolve_preset(tree, "custom")
        by_id = {m.id: m for m in result.modules}

        self.assertFalse(by_id["terminal"].enabled)
        self.assertEqual(by_id["terminal"].source, "override:custom")
        # Untouched modules still come from the base.
        self.assertTrue(by_id["desktop"].enabled)
        self.assertEqual(by_id["desktop"].source, "preset:kedra")

    def test_override_enables_an_inherited_disabled_module(self):
        tree = self.custom_tree({"extras": True})
        by_id = {m.id: m for m in resolve.resolve_preset(tree, "custom").modules}

        self.assertTrue(by_id["extras"].enabled)
        self.assertEqual(by_id["extras"].source, "override:custom")

    def test_chain_is_reported(self):
        tree = self.custom_tree({"terminal": False})
        self.assertEqual(resolve.resolve_preset(tree, "custom").chain, ("custom", "kedra"))

    def test_derived_preset_inherits_full_module_set_without_cloning(self):
        """The custom preset stores two overrides but resolves to all seven."""
        tree = self.custom_tree({"terminal": False, "extensions": False})
        stored = tree.presets["custom"]
        result = resolve.resolve_preset(tree, "custom")

        self.assertEqual(len(stored["module_overrides"]), 2)
        self.assertNotIn("modules", stored)
        self.assertEqual(len(result.modules), 7)

    def test_module_added_to_base_appears_in_derived_without_touching_it(self):
        """Why inheritance beats snapshotting: the base can grow."""
        grown = dict(KEDRA_MODULES)
        grown["newcomer"] = True
        tree = self.load(
            self.make_tree(
                modules={mid: module_toml(mid) for mid in grown},
                presets={
                    "kedra": base_preset_toml("kedra", grown),
                    "custom": derived_preset_toml("custom", "kedra", {"terminal": False}),
                },
            )
        )
        result = resolve.resolve_preset(tree, "custom")
        by_id = {m.id: m for m in result.modules}

        self.assertIn("newcomer", by_id)
        self.assertTrue(by_id["newcomer"].enabled)
        self.assertEqual(by_id["newcomer"].source, "preset:kedra")

    def test_multi_level_chain_resolves_leaf_last(self):
        tree = self.load(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "middle": derived_preset_toml("middle", "kedra", {"terminal": False}),
                    "leaf": derived_preset_toml("leaf", "middle", {"terminal": True}),
                }
            )
        )
        result = resolve.resolve_preset(tree, "leaf")
        by_id = {m.id: m for m in result.modules}

        self.assertEqual(result.chain, ("leaf", "middle", "kedra"))
        self.assertTrue(by_id["terminal"].enabled)
        self.assertEqual(by_id["terminal"].source, "override:leaf")

    def test_missing_base_is_rejected(self):
        tree, _ = self.load_dirty(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "custom": derived_preset_toml("custom", "ghost", {"terminal": False}),
                }
            )
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "custom")
        self.assertIn("inherits from nonexistent preset 'ghost'", str(ctx.exception))

    def test_inheritance_cycle_is_rejected(self):
        tree, _ = self.load_dirty(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "alpha": derived_preset_toml("alpha", "beta", {}),
                    "beta": derived_preset_toml("beta", "alpha", {}),
                }
            )
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "alpha")
        self.assertIn("preset inheritance cycle", str(ctx.exception))

    def test_self_inheritance_is_rejected(self):
        tree, _ = self.load_dirty(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "loop": derived_preset_toml("loop", "loop", {}),
                }
            )
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "loop")
        self.assertIn("cycle", str(ctx.exception))

    def test_override_for_nonexistent_module_is_rejected(self):
        tree, _ = self.load_dirty(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "custom": derived_preset_toml("custom", "kedra", {"ghost": False}),
                }
            )
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "custom")
        self.assertIn("nonexistent module 'ghost'", str(ctx.exception))

    def test_override_for_module_not_declared_by_base_is_rejected(self):
        """The module exists, but the base preset never declared it."""
        modules = {mid: module_toml(mid) for mid in KEDRA_MODULES}
        modules["outsider"] = module_toml("outsider")
        tree = self.load(
            self.make_tree(
                modules=modules,
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "custom": derived_preset_toml("custom", "kedra", {"outsider": True}),
                },
            )
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_preset(tree, "custom")
        self.assertIn("does not declare", str(ctx.exception))


class TestMalformedOverrides(TreeTestCase):
    def resolve_with_overrides(self, block: str):
        preset = (
            f"schema_version = {manifests.SCHEMA_VERSION}\n\n"
            '[preset]\nid = "custom"\nname = "Custom"\ndescription = "d."\n'
            'version = "0.1.0"\nkind = "derived"\napplies_experience_layer = true\n'
            'base_preset = "kedra"\n' + block
        )
        tree, _ = self.load_dirty(
            self.make_tree(
                presets={
                    "kedra": base_preset_toml("kedra", KEDRA_MODULES),
                    "custom": preset,
                }
            )
        )
        return resolve.resolve_preset(tree, "custom")

    def test_override_without_enabled_is_rejected(self):
        with self.assertRaises(ResolutionError) as ctx:
            self.resolve_with_overrides('\n[[preset.module_overrides]]\nid = "terminal"\n')
        self.assertIn("must set 'enabled' to a boolean", str(ctx.exception))

    def test_override_with_non_boolean_enabled_is_rejected(self):
        with self.assertRaises(ResolutionError) as ctx:
            self.resolve_with_overrides(
                '\n[[preset.module_overrides]]\nid = "terminal"\nenabled = "no"\n'
            )
        self.assertIn("must set 'enabled' to a boolean", str(ctx.exception))

    def test_override_without_id_is_rejected(self):
        with self.assertRaises(ResolutionError) as ctx:
            self.resolve_with_overrides('\n[[preset.module_overrides]]\nenabled = true\n')
        self.assertIn("missing or invalid module 'id'", str(ctx.exception))

    def test_duplicate_override_is_rejected(self):
        with self.assertRaises(ResolutionError) as ctx:
            self.resolve_with_overrides(
                '\n[[preset.module_overrides]]\nid = "terminal"\nenabled = true\n'
                '\n[[preset.module_overrides]]\nid = "terminal"\nenabled = false\n'
            )
        self.assertIn("twice", str(ctx.exception))

    def test_override_with_unknown_key_is_rejected(self):
        with self.assertRaises(ResolutionError) as ctx:
            self.resolve_with_overrides(
                '\n[[preset.module_overrides]]\nid = "terminal"\nenabled = true\nnonsense = 1\n'
            )
        self.assertIn("unknown key 'nonsense'", str(ctx.exception))


class TestResolveFromUserState(TreeTestCase):
    def test_user_state_overrides_are_applied(self):
        """The worked example from the task specification."""
        tree = self.load(self.make_tree())
        user_state = state.UserState(
            base_preset="kedra",
            module_overrides=(
                state.ModuleOverride(id="terminal", enabled=False),
                state.ModuleOverride(id="extensions", enabled=False),
            ),
        )
        result = resolve.resolve_state(tree, user_state)

        self.assertEqual(
            {m.id: m.enabled for m in result.modules},
            {
                "desktop": True,
                "workflow": True,
                "appearance": True,
                "terminal": False,
                "extensions": False,
                "integration": True,
                "extras": False,
            },
        )

    def test_user_state_source_is_attributed(self):
        tree = self.load(self.make_tree())
        user_state = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
        )
        by_id = {m.id: m for m in resolve.resolve_state(tree, user_state).modules}

        self.assertEqual(by_id["terminal"].source, "user-state")
        self.assertEqual(by_id["desktop"].source, "preset:kedra")

    def test_user_state_does_not_mutate_the_tree(self):
        tree = self.load(self.make_tree())
        before = json.dumps(tree.presets, sort_keys=True)

        user_state = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="terminal", enabled=False),),
        )
        resolve.resolve_state(tree, user_state)

        self.assertEqual(json.dumps(tree.presets, sort_keys=True), before)

    def test_user_state_with_unknown_base_is_rejected(self):
        tree = self.load(self.make_tree())
        user_state = state.UserState(base_preset="ghost")
        with self.assertRaises(ResolutionError):
            resolve.resolve_state(tree, user_state)

    def test_user_state_override_for_unknown_module_is_rejected(self):
        tree = self.load(self.make_tree())
        user_state = state.UserState(
            base_preset="kedra",
            module_overrides=(state.ModuleOverride(id="ghost", enabled=True),),
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolve.resolve_state(tree, user_state)
        self.assertIn("nonexistent module 'ghost'", str(ctx.exception))


class TestDeterminism(TreeTestCase):
    def test_modules_are_sorted_by_id(self):
        tree = self.load(self.make_tree())
        ids = [m.id for m in resolve.resolve_preset(tree, "kedra").modules]
        self.assertEqual(ids, sorted(ids))

    def test_repeated_resolution_is_identical(self):
        tree = self.load(self.make_tree())
        first = resolve.resolve_preset(tree, "kedra").to_json()
        second = resolve.resolve_preset(tree, "kedra").to_json()
        self.assertEqual(first, second)

    def test_manifest_declaration_order_does_not_affect_output(self):
        """Same content, different preset ordering -> identical JSON."""
        forward = dict(KEDRA_MODULES)
        backward = dict(reversed(list(KEDRA_MODULES.items())))

        tree_a = self.load(self.make_tree(presets={"kedra": base_preset_toml("kedra", forward)}))
        tree_b = self.load(self.make_tree(presets={"kedra": base_preset_toml("kedra", backward)}))

        self.assertEqual(
            resolve.resolve_preset(tree_a, "kedra").to_json(),
            resolve.resolve_preset(tree_b, "kedra").to_json(),
        )

    def test_json_output_is_parseable_and_versioned(self):
        tree = self.load(self.make_tree())
        payload = json.loads(resolve.resolve_preset(tree, "kedra").to_json())

        self.assertEqual(
            payload["resolution_schema_version"], resolve.RESOLUTION_SCHEMA_VERSION
        )
        self.assertEqual(payload["preset_id"], "kedra")
        self.assertTrue(payload["applies_experience_layer"])
        self.assertEqual(len(payload["modules"]), 7)

    def test_resolution_is_immutable(self):
        tree = self.load(self.make_tree())
        result = resolve.resolve_preset(tree, "kedra")
        with self.assertRaises(Exception):
            result.modules[0].enabled = False


class TestShippedTreeResolves(unittest.TestCase):
    """The manifests actually shipped must resolve, not just validate."""

    def setUp(self):
        report = manifests.Report()
        self.tree = manifests.load_tree(DATA_DIR, report)
        self.assertTrue(report.ok, f"shipped data invalid: {report.errors}")

    def test_shipped_kedra_preset_resolves(self):
        result = resolve.resolve_preset(self.tree, "kedra")
        self.assertTrue(result.applies_experience_layer)
        self.assertEqual(
            set(result.enabled_module_ids),
            {"desktop", "workflow", "appearance", "terminal", "extensions", "integration"},
        )

    def test_shipped_vanilla_preset_resolves_to_nothing(self):
        result = resolve.resolve_preset(self.tree, "vanilla")
        self.assertFalse(result.applies_experience_layer)
        self.assertEqual(result.modules, ())

    def test_shipped_default_preset_resolves(self):
        resolve.resolve_preset(self.tree, self.tree.default_preset)


if __name__ == "__main__":
    unittest.main()
