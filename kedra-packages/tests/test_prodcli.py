"""The shipped `kedra-experience` command: its contract, and what it refuses.

This is the interface a future Calamares step will call, so the tests are
written as a consumer would read it -- exact key names, exact exit statuses,
exact package sets -- rather than as a tour of the implementation. If a change
here needs these edited, that change breaks an installer.

Everything runs against fixture trees or the in-repo shipped tree via an
explicit `--data-dir`. Nothing consults /usr/share/kedra/experience, nothing
reads dconf, and nothing writes anywhere.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import DATA_DIR, TreeTestCase, base_preset_toml, module_toml

from kedra_experience import prodcli
from kedra_experience.manifests import INSTALLED_DATA_DIR


def run(argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    """Invoke main() exactly as the shipped entry point does."""
    out, err = io.StringIO(), io.StringIO()
    original = prodcli.sys.stdin
    prodcli.sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            status = prodcli.main(argv)
    finally:
        prodcli.sys.stdin = original
    return status, out.getvalue(), err.getvalue()


def resolve_shipped(selection: dict) -> tuple[int, dict, str]:
    """Resolve a selection against the tree this repository ships."""
    status, out, err = run(
        ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"],
        stdin=json.dumps(selection),
    )
    return status, (json.loads(out) if out.strip() else {}), err


# ---------------------------------------------------------------------------
# The production data path
# ---------------------------------------------------------------------------


class InstalledDataPathTests(unittest.TestCase):
    """Production must read the package-owned tree and nothing else."""

    def test_the_default_data_dir_is_the_installed_tree(self):
        args = prodcli.build_parser().parse_args(["profiles"])
        self.assertEqual(Path(args.data_dir), Path("/usr/share/kedra/experience"))

    def test_the_installed_path_is_the_one_kedra_experience_packages(self):
        self.assertEqual(INSTALLED_DATA_DIR, Path("/usr/share/kedra/experience"))

    def test_the_default_does_not_point_into_a_source_checkout(self):
        # The audit's B1: `default_data_dir()` resolves relative to the source
        # tree, so a shipped command that used it would read a directory that
        # does not exist on a target -- or, worse, a developer's checkout.
        default = str(prodcli.build_parser().parse_args(["profiles"]).data_dir)
        self.assertNotIn("kedra-packages", default)
        self.assertNotIn(str(Path.home()), default)

    def test_a_missing_tree_is_reported_as_a_missing_package(self):
        status, out, err = run(["--data-dir", "/nonexistent/kedra", "profiles"])
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("kedra-experience package", err)


# ---------------------------------------------------------------------------
# The selection contract
# ---------------------------------------------------------------------------


class SelectionContractTests(unittest.TestCase):
    def test_the_documented_shape_is_accepted(self):
        state = prodcli.parse_selection(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "workflow", "enabled": False}],
            }
        )
        self.assertEqual(state.base_preset, "kedra")
        self.assertEqual([o.id for o in state.module_overrides], ["workflow"])
        self.assertEqual([o.enabled for o in state.module_overrides], [False])

    def test_a_selection_carries_no_applied_record(self):
        # A selection says what was chosen. Claiming an applied state would be
        # a lie the ownership layer would later act on.
        self.assertIsNone(prodcli.parse_selection({"base_preset": "kedra"}).applied)

    def test_the_keys_are_the_state_files_selection_table(self):
        # The reason no second format was invented: what an installer sends
        # here is what a later task writes into [selection] verbatim.
        from kedra_experience.state import SELECTION_KNOWN_KEYS

        self.assertEqual(
            SELECTION_KNOWN_KEYS,
            prodcli.SELECTION_KNOWN_KEYS - {"selection_schema_version"},
        )

    def test_overrides_are_normalised_to_sorted_order(self):
        state = prodcli.parse_selection(
            {
                "base_preset": "kedra",
                "module_overrides": [
                    {"id": "workflow", "enabled": False},
                    {"id": "extras", "enabled": True},
                ],
            }
        )
        self.assertEqual([o.id for o in state.module_overrides], ["extras", "workflow"])

    def test_module_overrides_may_be_omitted(self):
        self.assertEqual(prodcli.parse_selection({"base_preset": "kedra"}).module_overrides, ())

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(prodcli.UsageError) as caught:
            prodcli.parse_selection({"base_preset": "kedra", "preset": "kedra"})
        self.assertIn("unknown key", str(caught.exception))

    def test_an_unknown_override_key_is_refused(self):
        with self.assertRaises(prodcli.UsageError):
            prodcli.parse_selection(
                {"base_preset": "kedra", "module_overrides": [
                    {"id": "workflow", "enabled": False, "presentation": "x"}
                ]}
            )

    def test_a_non_boolean_enabled_is_refused(self):
        with self.assertRaises(prodcli.UsageError) as caught:
            prodcli.parse_selection(
                {"base_preset": "kedra", "module_overrides": [{"id": "w", "enabled": "no"}]}
            )
        self.assertIn("true or false", str(caught.exception))

    def test_a_duplicated_override_is_refused(self):
        with self.assertRaises(prodcli.UsageError) as caught:
            prodcli.parse_selection(
                {"base_preset": "kedra", "module_overrides": [
                    {"id": "workflow", "enabled": False},
                    {"id": "workflow", "enabled": True},
                ]}
            )
        self.assertIn("twice", str(caught.exception))

    def test_a_missing_base_preset_is_refused(self):
        with self.assertRaises(prodcli.UsageError):
            prodcli.parse_selection({"module_overrides": []})

    def test_a_future_selection_version_is_refused_rather_than_guessed(self):
        with self.assertRaises(prodcli.UsageError) as caught:
            prodcli.parse_selection(
                {"selection_schema_version": 99, "base_preset": "kedra"}
            )
        self.assertIn("not understood", str(caught.exception))

    def test_the_current_version_may_be_stated_explicitly(self):
        state = prodcli.parse_selection(
            {"selection_schema_version": prodcli.SELECTION_SCHEMA_VERSION,
             "base_preset": "kedra"}
        )
        self.assertEqual(state.base_preset, "kedra")

    def test_a_non_object_is_refused(self):
        for document in ([], "kedra", 3, None):
            with self.subTest(document=document):
                with self.assertRaises(prodcli.UsageError):
                    prodcli.parse_selection(document)

    def test_malformed_json_produces_no_document(self):
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"], stdin="{oops"
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("not valid JSON", err)

    def test_an_empty_stdin_is_refused(self):
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"], stdin="   \n"
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("no selection document", err)


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


class ProfilesDocumentTests(unittest.TestCase):
    def setUp(self):
        status, out, _ = run(["--data-dir", str(DATA_DIR), "profiles"])
        self.assertEqual(status, prodcli.EXIT_OK)
        self.document = json.loads(out)

    def test_the_envelope_is_versioned(self):
        self.assertEqual(self.document["profiles_schema_version"], 1)
        self.assertIn("experience_schema_version", self.document)
        self.assertIn("selection_schema_version", self.document)

    def test_both_shipped_presets_are_listed(self):
        self.assertEqual(
            sorted(p["id"] for p in self.document["presets"]), ["kedra", "vanilla"]
        )

    def test_the_default_preset_is_vanilla(self):
        self.assertEqual(self.document["default_preset"], "vanilla")

    def test_each_preset_says_whether_it_may_be_offered(self):
        for preset in self.document["presets"]:
            with self.subTest(preset=preset["id"]):
                self.assertIsInstance(preset["user_selectable"], bool)

    def test_vanilla_applies_no_layer_and_lists_no_modules(self):
        vanilla = next(p for p in self.document["presets"] if p["id"] == "vanilla")
        self.assertFalse(vanilla["applies_experience_layer"])
        self.assertEqual(vanilla["modules"], [])

    def test_kedra_lists_every_module_with_its_activation(self):
        kedra = next(p for p in self.document["presets"] if p["id"] == "kedra")
        self.assertTrue(kedra["applies_experience_layer"])
        states = {m["id"]: m["enabled"] for m in kedra["modules"]}
        self.assertEqual(
            states,
            {
                "appearance": True,
                "desktop": True,
                "extensions": True,
                "extras": False,
                "integration": True,
                "terminal": True,
                "workflow": True,
            },
        )

    def test_non_selectable_modules_are_flagged_so_a_ui_can_hide_them(self):
        kedra = next(p for p in self.document["presets"] if p["id"] == "kedra")
        selectable = {m["id"]: m["user_selectable"] for m in kedra["modules"]}
        # `integration` is structural -- the Experience layer's own delivery.
        self.assertFalse(selectable["integration"])
        # `desktop` is core but selectable. Tier and selectability are separate
        # axes, and this pair is the case that proves a UI must read the flag
        # rather than infer it from the tier.
        self.assertTrue(selectable["desktop"])
        self.assertEqual(
            {m["id"] for m in kedra["modules"] if not m["user_selectable"]},
            {"integration"},
        )
        self.assertTrue(selectable["workflow"])

    def test_modules_carry_the_strings_a_ui_needs(self):
        kedra = next(p for p in self.document["presets"] if p["id"] == "kedra")
        workflow = next(m for m in kedra["modules"] if m["id"] == "workflow")
        self.assertEqual(workflow["name"], "Kedra Workflow")
        self.assertTrue(workflow["description"])

    def test_output_is_deterministic(self):
        _, first, _ = run(["--data-dir", str(DATA_DIR), "profiles"])
        _, second, _ = run(["--data-dir", str(DATA_DIR), "profiles"])
        self.assertEqual(first, second)

    def test_no_python_repr_reaches_the_output(self):
        _, out, _ = run(["--data-dir", str(DATA_DIR), "profiles"])
        for marker in ("<", "object at 0x", "PosixPath(", "Resolution("):
            self.assertNotIn(marker, out)


# ---------------------------------------------------------------------------
# resolve -- the three products
# ---------------------------------------------------------------------------


class VanillaResolutionTests(unittest.TestCase):
    """Vanilla receives no Experience behaviour. Stated as assertions."""

    def setUp(self):
        self.status, self.document, _ = resolve_shipped({"base_preset": "vanilla"})

    def test_it_resolves_cleanly(self):
        self.assertEqual(self.status, prodcli.EXIT_OK)
        self.assertTrue(self.document["diagnostics"]["ok"])

    def test_the_experience_layer_does_not_apply(self):
        self.assertFalse(self.document["applies_experience_layer"])

    def test_no_module_is_resolved_at_all(self):
        # Not "every module is off" -- no module is consulted.
        self.assertEqual(self.document["modules"], [])
        self.assertEqual(self.document["enabled_modules"], [])

    def test_it_requires_no_package_whatsoever(self):
        packages = self.document["packages"]
        self.assertEqual(packages["required"], [])
        self.assertEqual(packages["recommended"], [])
        self.assertEqual(packages["optional"], [])
        self.assertEqual(packages["conditional"], [])
        self.assertEqual(packages["unresolved"], [])
        self.assertEqual(packages["requirements"], [])

    def test_it_requires_no_experience_metadata_package(self):
        self.assertNotIn("kedra-experience", self.document["packages"]["required"])

    def test_it_requires_no_experience_runtime_package(self):
        self.assertNotIn("kedra-experience-runtime", self.document["packages"]["required"])

    def test_it_requires_no_desktop_defaults_package(self):
        self.assertNotIn("kedra-experience-desktop", self.document["packages"]["required"])

    def test_it_requires_no_workflow_defaults_package(self):
        self.assertNotIn("kedra-experience-workflow", self.document["packages"]["required"])

    def test_no_kedra_package_appears_under_any_relation(self):
        every = [
            name
            for relation in ("required", "recommended", "optional", "conditional")
            for name in self.document["packages"][relation]
        ]
        self.assertEqual([n for n in every if n.startswith("kedra-")], [])

    def test_it_exposes_no_effective_terminal_contract(self):
        # The declaration exists in the tree; Vanilla enables no module, so it
        # contributes nothing. Conflating declared with effective is exactly how
        # a Vanilla system would acquire a Kedra dependency.
        self.assertEqual(self.document["applications"], [])

    def test_nothing_in_the_document_describes_a_change_to_make(self):
        # A resolve document is an answer, not a plan: no setting values, no
        # shortcuts, no operations.
        for forbidden in ("settings", "shortcuts", "plan", "operations", "managed_values"):
            self.assertNotIn(forbidden, self.document)

    def test_overriding_a_module_on_vanilla_is_refused(self):
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"],
            stdin=json.dumps(
                {"base_preset": "vanilla",
                 "module_overrides": [{"id": "workflow", "enabled": True}]}
            ),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("does not apply the Experience layer", err)


class KedraResolutionTests(unittest.TestCase):
    def setUp(self):
        self.status, self.document, _ = resolve_shipped({"base_preset": "kedra"})

    def test_it_resolves_cleanly(self):
        self.assertEqual(self.status, prodcli.EXIT_OK)
        self.assertTrue(self.document["diagnostics"]["ok"])

    def test_the_experience_layer_applies(self):
        self.assertTrue(self.document["applies_experience_layer"])

    def test_the_required_set_is_exactly_this(self):
        self.assertEqual(
            self.document["packages"]["required"],
            [
                "gnome-console",
                "gnome-shell",
                "kedra-experience",
                "kedra-experience-desktop",
                "kedra-experience-runtime",
                "kedra-experience-workflow",
            ],
        )

    def test_the_metadata_and_runtime_packages_are_required(self):
        # The audit's S1. Without these the target has Kedra's GSettings
        # defaults and no way to answer which profile it is running.
        required = self.document["packages"]["required"]
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)

    def test_the_integration_module_is_what_asks_for_them(self):
        asked = {
            r["name"]: r["module_id"]
            for r in self.document["packages"]["requirements"]
            if r["name"].startswith("kedra-experience")
        }
        self.assertEqual(asked["kedra-experience"], "integration")
        self.assertEqual(asked["kedra-experience-runtime"], "integration")

    def test_every_requirement_states_a_reason(self):
        for requirement in self.document["packages"]["requirements"]:
            with self.subTest(package=requirement["name"]):
                self.assertTrue(requirement["reason"])

    def test_recommended_and_optional_are_separate_from_required(self):
        packages = self.document["packages"]
        self.assertEqual(packages["recommended"], ["gnome-control-center", "gnome-themes-extra"])
        self.assertEqual(packages["optional"], ["gnome-shell-extension-manager", "kedra-branding"])
        for name in packages["recommended"] + packages["optional"]:
            self.assertNotIn(name, packages["required"])

    def test_nothing_is_left_unresolved_in_the_shipped_tree(self):
        self.assertEqual(self.document["packages"]["unresolved"], [])

    def test_the_terminal_contract_is_effective(self):
        self.assertEqual(len(self.document["applications"]), 1)
        contract = self.document["applications"][0]
        self.assertEqual(contract["role"], "terminal")
        self.assertEqual(contract["desktop_id"], "org.gnome.Console.desktop")
        self.assertEqual(contract["package"], "gnome-console")
        self.assertEqual(contract["launch_command"], "gapplication launch org.gnome.Console")

    def test_the_selection_is_echoed_back_normalised(self):
        self.assertEqual(
            self.document["selection"],
            {
                "selection_schema_version": 1,
                "base_preset": "kedra",
                "module_overrides": [],
            },
        )

    def test_the_preset_chain_is_reported(self):
        self.assertEqual(self.document["preset"]["id"], "kedra")
        self.assertEqual(self.document["preset"]["chain"], ["kedra"])

    def test_output_is_deterministic(self):
        _, first, _ = resolve_shipped({"base_preset": "kedra"})
        _, second, _ = resolve_shipped({"base_preset": "kedra"})
        self.assertEqual(first, second)


class CustomResolutionTests(unittest.TestCase):
    """Custom Kedra: a base pointer plus deltas, never a cloned preset."""

    def setUp(self):
        self.selection = {
            "base_preset": "kedra",
            "module_overrides": [{"id": "workflow", "enabled": False}],
        }
        self.status, self.document, _ = resolve_shipped(self.selection)

    def test_it_resolves_cleanly(self):
        self.assertEqual(self.status, prodcli.EXIT_OK)

    def test_the_workflow_defaults_package_is_dropped(self):
        self.assertNotIn("kedra-experience-workflow", self.document["packages"]["required"])

    def test_the_desktop_defaults_package_is_kept(self):
        self.assertIn("kedra-experience-desktop", self.document["packages"]["required"])

    def test_the_experience_core_and_runtime_are_kept(self):
        required = self.document["packages"]["required"]
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)

    def test_the_required_set_is_exactly_this(self):
        self.assertEqual(
            self.document["packages"]["required"],
            [
                "gnome-console",
                "gnome-shell",
                "kedra-experience",
                "kedra-experience-desktop",
                "kedra-experience-runtime",
            ],
        )

    def test_the_override_is_attributed_to_the_user_not_the_preset(self):
        workflow = next(m for m in self.document["modules"] if m["id"] == "workflow")
        self.assertFalse(workflow["enabled"])
        self.assertEqual(workflow["source"], "user-state")

    def test_unchanged_modules_still_come_from_the_base_preset(self):
        desktop = next(m for m in self.document["modules"] if m["id"] == "desktop")
        self.assertTrue(desktop["enabled"])
        self.assertEqual(desktop["source"], "preset:kedra")

    def test_only_the_deltas_are_echoed_back(self):
        # Nothing is cloned: a future Kedra release that adds a module is
        # inherited, because there is no snapshot to go stale.
        self.assertEqual(
            self.document["selection"]["module_overrides"],
            [{"id": "workflow", "enabled": False}],
        )

    def test_disabling_terminal_is_also_valid(self):
        status, document, _ = resolve_shipped(
            {"base_preset": "kedra", "module_overrides": [{"id": "terminal", "enabled": False}]}
        )
        self.assertEqual(status, prodcli.EXIT_OK)
        self.assertTrue(document["diagnostics"]["ok"])
        # The terminal application and its package both go with the module.
        self.assertNotIn("gnome-console", document["packages"]["required"])
        self.assertEqual(document["applications"], [])
        # ...and the Experience layer's own delivery does not.
        self.assertIn("kedra-experience", document["packages"]["required"])
        self.assertIn("kedra-experience-runtime", document["packages"]["required"])


class RefusedOverrideTests(unittest.TestCase):
    """A selection may not switch off a module that is not the user's to switch.

    The gap this closes: `integration` is `user_selectable = false` and carries
    kedra-experience and kedra-experience-runtime, so an accepted override of it
    removed the Experience layer's own delivery from `packages.required`. Before
    the resolver enforced this, the only thing standing between a malformed
    selection and that outcome was an installer UI honouring a flag -- a
    convention, in the one place a convention must not be load-bearing.
    """

    def refuse(self, module_id: str) -> tuple[int, dict]:
        status, document, err = resolve_shipped(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": module_id, "enabled": False}],
            }
        )
        self.assertEqual(err, "", "a refusal is reported in the document, not on stderr")
        return status, document

    # -- the two shipped core modules ------------------------------------

    def test_integration_off_is_refused(self):
        status, document = self.refuse("integration")
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        self.assertFalse(document["diagnostics"]["ok"])

    def test_integration_off_does_not_lose_the_experience_packages(self):
        # The concrete harm, asserted directly.
        _, document = self.refuse("integration")
        required = document["packages"]["required"]
        self.assertIn("kedra-experience", required)
        self.assertIn("kedra-experience-runtime", required)

    def test_desktop_off_is_a_valid_custom_kedra(self):
        # The product contract, and the one case this class exists to keep
        # separate from `integration`: `desktop` is core but user-selectable, so
        # switching the fixed workspace model off is an ordinary Custom Kedra.
        status, document, _ = resolve_shipped(
            {"base_preset": "kedra",
             "module_overrides": [{"id": "desktop", "enabled": False}]}
        )
        self.assertEqual(status, prodcli.EXIT_OK)
        self.assertTrue(document["diagnostics"]["ok"])
        self.assertEqual(document["diagnostics"]["refused_overrides"], [])
        # The workspace model goes...
        self.assertNotIn("kedra-experience-desktop", document["packages"]["required"])
        # ...the keymap stays, and is told what it costs.
        self.assertIn("kedra-experience-workflow", document["packages"]["required"])
        self.assertEqual(
            [(a["module"], a["related_module"])
             for a in document["diagnostics"]["advisories"]],
            [("workflow", "desktop")],
        )

    # -- what the refusal says -------------------------------------------

    def test_the_refusal_is_structured_not_prose(self):
        _, document = self.refuse("integration")
        self.assertEqual(
            document["diagnostics"]["refused_overrides"],
            [
                {
                    "code": "module-not-user-selectable",
                    "module": "integration",
                    "requested_enabled": False,
                    "effective_enabled": True,
                    "message": document["diagnostics"]["refused_overrides"][0]["message"],
                }
            ],
        )

    def test_the_message_names_the_module_and_says_why(self):
        _, document = self.refuse("integration")
        message = document["diagnostics"]["refused_overrides"][0]["message"]
        self.assertIn("integration", message)
        self.assertIn("not user-selectable", message)

    def test_a_refusal_is_not_reported_as_a_requirement_violation(self):
        # Different things, different arrays: a violation is about the resolved
        # profile, a refusal is about the input that produced it.
        _, document = self.refuse("integration")
        self.assertEqual(document["diagnostics"]["violations"], [])
        self.assertEqual(document["diagnostics"]["advisories"], [])

    # -- the resolution itself is not half-applied -----------------------

    def test_the_module_keeps_the_presets_answer(self):
        _, document = self.refuse("integration")
        module = next(m for m in document["modules"] if m["id"] == "integration")
        self.assertTrue(module["enabled"])
        self.assertEqual(module["source"], "preset:kedra")

    def test_the_document_is_otherwise_identical_to_an_unmodified_kedra(self):
        # "Do not partially resolve as though the override succeeded": the only
        # difference from a plain `kedra` resolution is the echoed selection and
        # the diagnostics, never the modules or the packages.
        _, refused = self.refuse("integration")
        _, plain, _ = resolve_shipped({"base_preset": "kedra"})
        for key in ("modules", "enabled_modules", "packages", "applications", "preset"):
            with self.subTest(key=key):
                self.assertEqual(refused[key], plain[key])

    def test_the_selection_is_echoed_as_given(self):
        # What was asked for is reported back unchanged; what happened to it is
        # in the diagnostics. Dropping the override from the echo would be the
        # silent handling this exists to prevent.
        _, document = self.refuse("integration")
        self.assertEqual(
            document["selection"]["module_overrides"],
            [{"id": "integration", "enabled": False}],
        )

    # -- mixed and repeated -----------------------------------------------

    def test_a_valid_override_alongside_a_refused_one_still_applies(self):
        status, document, _ = resolve_shipped(
            {
                "base_preset": "kedra",
                "module_overrides": [
                    {"id": "workflow", "enabled": False},
                    {"id": "integration", "enabled": False},
                ],
            }
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        required = document["packages"]["required"]
        # workflow was the user's to switch, so it took effect...
        self.assertNotIn("kedra-experience-workflow", required)
        # ...and integration was not.
        self.assertIn("kedra-experience", required)
        self.assertEqual(
            [r["module"] for r in document["diagnostics"]["refused_overrides"]],
            ["integration"],
        )

    def test_only_the_structural_module_is_refused_in_a_mixed_selection(self):
        # `desktop` and `integration` are both `tier = "core"`; only one of them
        # is the user's to switch. This is the test that would fail if the two
        # concepts were ever collapsed back together.
        status, document, _ = resolve_shipped(
            {
                "base_preset": "kedra",
                "module_overrides": [
                    {"id": "integration", "enabled": False},
                    {"id": "desktop", "enabled": False},
                ],
            }
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        self.assertEqual(
            [r["module"] for r in document["diagnostics"]["refused_overrides"]],
            ["integration"],
        )
        desktop = next(m for m in document["modules"] if m["id"] == "desktop")
        self.assertFalse(desktop["enabled"])
        integration = next(m for m in document["modules"] if m["id"] == "integration")
        self.assertTrue(integration["enabled"])

    def test_enabling_a_non_user_selectable_module_is_refused_too(self):
        # The rule is about who may decide, not about which way. `extras` is off
        # in the Kedra preset and user-selectable; `integration` is on and not.
        status, document, _ = resolve_shipped(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "integration", "enabled": True}],
            }
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        refusal = document["diagnostics"]["refused_overrides"][0]
        self.assertTrue(refusal["requested_enabled"])
        self.assertTrue(refusal["effective_enabled"])

    # -- the envelope stayed stable ---------------------------------------

    def test_the_document_shape_is_unchanged_apart_from_the_new_array(self):
        _, document = self.refuse("integration")
        self.assertEqual(document["resolve_schema_version"], 2)
        self.assertEqual(
            sorted(document["diagnostics"]),
            ["advisories", "ok", "refused_overrides", "violations"],
        )

    def test_a_valid_selection_reports_an_empty_refusal_array(self):
        # Present and empty rather than absent, so a consumer reads one shape.
        _, document, _ = resolve_shipped({"base_preset": "kedra"})
        self.assertEqual(document["diagnostics"]["refused_overrides"], [])

    def test_vanilla_reports_an_empty_refusal_array(self):
        _, document, _ = resolve_shipped({"base_preset": "vanilla"})
        self.assertEqual(document["diagnostics"]["refused_overrides"], [])

    def test_output_is_deterministic(self):
        first = resolve_shipped(
            {"base_preset": "kedra",
             "module_overrides": [{"id": "integration", "enabled": False}]}
        )
        second = resolve_shipped(
            {"base_preset": "kedra",
             "module_overrides": [{"id": "integration", "enabled": False}]}
        )
        self.assertEqual(first, second)

    def test_the_text_renderer_says_so_on_stderr(self):
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-", "--format", "text"],
            stdin=json.dumps(
                {"base_preset": "kedra",
                 "module_overrides": [{"id": "integration", "enabled": False}]}
            ),
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        self.assertIn("REFUSED", err)
        self.assertIn("integration", err)


class VanillaOverrideStillRejectedTests(unittest.TestCase):
    """Vanilla's own rule is unchanged: no override of any kind, ever."""

    def test_a_user_selectable_module_is_still_a_hard_error(self):
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"],
            stdin=json.dumps(
                {"base_preset": "vanilla",
                 "module_overrides": [{"id": "workflow", "enabled": True}]}
            ),
        )
        # Exit 1, not 2: there is no coherent document to hand back, because
        # Vanilla resolves no module set to attach the override to.
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("does not apply the Experience layer", err)

    def test_a_non_user_selectable_module_is_the_same_hard_error(self):
        # Vanilla's rule fires first, so this never reaches the refusal path --
        # which is correct: on Vanilla the module is not merely unselectable,
        # it is not part of the profile at all.
        status, out, err = run(
            ["--data-dir", str(DATA_DIR), "resolve", "--selection", "-"],
            stdin=json.dumps(
                {"base_preset": "vanilla",
                 "module_overrides": [{"id": "integration", "enabled": False}]}
            ),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("does not apply the Experience layer", err)


# ---------------------------------------------------------------------------
# Exit status
# ---------------------------------------------------------------------------


class ExitStatusTests(TreeTestCase):
    def test_a_valid_profile_exits_zero(self):
        status, _, _ = resolve_shipped({"base_preset": "kedra"})
        self.assertEqual(status, 0)

    def test_an_unresolvable_selection_exits_one_with_empty_stdout(self):
        status, out, _ = resolve_shipped({"base_preset": "no-such-preset"})
        self.assertEqual(status, 1)
        self.assertEqual(out, {})

    def test_a_violated_hard_requirement_exits_two_and_still_prints(self):
        # A profile that resolves and is wrong is a different situation from one
        # that cannot be resolved, and an installer must tell them apart: the
        # first can be shown to the user, the second cannot.
        root = self.make_tree(
            modules={
                "alpha": module_toml("alpha", requires=("beta",)),
                "beta": module_toml("beta"),
            },
            presets={"kedra": base_preset_toml("kedra", {"alpha": True, "beta": True})},
        )
        status, out, err = run(
            ["--data-dir", str(root), "resolve", "--selection", "-"],
            stdin=json.dumps(
                {"base_preset": "kedra", "module_overrides": [{"id": "beta", "enabled": False}]}
            ),
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        document = json.loads(out)
        self.assertFalse(document["diagnostics"]["ok"])
        self.assertEqual(
            [v["module"] for v in document["diagnostics"]["violations"]], ["alpha"]
        )
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# install-state -- the one command that writes
# ---------------------------------------------------------------------------


class InstallStateTests(unittest.TestCase):
    """Persisting a selection into a target root.

    Every test builds a throwaway target: an /etc/passwd and a home directory,
    exactly what Calamares' `users` step leaves behind. Ownership is asserted
    against the invoking uid because these run unprivileged; the real installer
    runs as root and the code path is identical.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kedra-target-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.uid, self.gid = os.getuid(), os.getgid()
        (self.root / "etc").mkdir()
        (self.root / "home" / "alice").mkdir(parents=True)
        (self.root / "etc" / "passwd").write_text(
            "root:x:0:0::/root:/bin/bash\n"
            f"alice:x:{self.uid}:{self.gid}::/home/alice:/bin/bash\n",
            encoding="utf-8",
        )
        self.state_path = (
            self.root / "home/alice/.local/state/kedra/experience-state.toml"
        )

    def install(self, selection: dict, *extra: str) -> tuple[int, dict, str]:
        status, out, err = run(
            [
                "--data-dir", str(DATA_DIR),
                "install-state",
                "--selection", "-",
                "--root", str(self.root),
                "--user", "alice",
                *extra,
            ],
            stdin=json.dumps(selection),
        )
        return status, (json.loads(out) if out.strip() else {}), err

    def written_state(self) -> str:
        return self.state_path.read_text(encoding="utf-8")

    # -- Kedra ------------------------------------------------------------

    def test_kedra_writes_the_selection(self):
        status, document, err = self.install({"base_preset": "kedra"})
        self.assertEqual(status, prodcli.EXIT_OK, err)
        self.assertTrue(document["written"])
        self.assertEqual(document["path"], str(self.state_path))
        self.assertTrue(self.state_path.is_file())

    def test_the_file_is_the_documented_state_format(self):
        self.install({"base_preset": "kedra"})
        text = self.written_state()
        self.assertIn("state_schema_version = 3", text)
        self.assertIn("[selection]", text)
        self.assertIn('base_preset = "kedra"', text)

    def test_it_is_loadable_by_the_state_model_it_claims_to_use(self):
        # The real check that no second format was invented.
        from kedra_experience.state import UserState

        self.install({"base_preset": "kedra"})
        state = UserState.load(self.state_path)
        self.assertEqual(state.base_preset, "kedra")
        self.assertEqual(state.module_overrides, ())

    def test_no_applied_state_is_recorded(self):
        # An installer applies nothing, so it must claim nothing.
        from kedra_experience.state import UserState

        _, document, _ = self.install({"base_preset": "kedra"})
        self.assertFalse(document["applied_recorded"])
        self.assertNotIn("[applied]", self.written_state())
        self.assertIsNone(UserState.load(self.state_path).applied)

    def test_no_gnome_value_is_written_anywhere(self):
        # One file, and nothing that resembles a setting.
        self.install({"base_preset": "kedra"})
        text = self.written_state()
        for marker in ("org.gnome", "gsettings", "dconf", "binding", "Super"):
            self.assertNotIn(marker, text)

    def test_exactly_one_file_is_created(self):
        self.install({"base_preset": "kedra"})
        files = [p for p in (self.root / "home/alice").rglob("*") if p.is_file()]
        self.assertEqual(files, [self.state_path])

    def test_ownership_and_modes(self):
        self.install({"base_preset": "kedra"})
        self.assertEqual(self.state_path.stat().st_uid, self.uid)
        self.assertEqual(self.state_path.stat().st_gid, self.gid)
        self.assertEqual(oct(self.state_path.stat().st_mode & 0o777), "0o644")
        for directory in (
            self.root / "home/alice/.local",
            self.root / "home/alice/.local/state",
            self.root / "home/alice/.local/state/kedra",
        ):
            with self.subTest(directory=directory.name):
                self.assertEqual(oct(directory.stat().st_mode & 0o777), "0o700")
                self.assertEqual(directory.stat().st_uid, self.uid)

    # -- Custom -----------------------------------------------------------

    def test_custom_persists_only_the_deltas(self):
        from kedra_experience.state import UserState

        self.install(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "workflow", "enabled": False}],
            }
        )
        state = UserState.load(self.state_path)
        self.assertEqual(state.base_preset, "kedra")
        self.assertEqual(
            [(o.id, o.enabled) for o in state.module_overrides], [("workflow", False)]
        )

    def test_custom_does_not_copy_the_resolved_module_list(self):
        # Seven modules resolve; one override is stored.
        self.install(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "desktop", "enabled": False}],
            }
        )
        text = self.written_state()
        self.assertEqual(text.count("[[selection.module_overrides]]"), 1)
        for module in ("terminal", "extensions", "appearance", "integration"):
            self.assertNotIn(module, text)

    # -- Vanilla ----------------------------------------------------------

    def test_vanilla_writes_no_file_at_all(self):
        status, document, err = self.install({"base_preset": "vanilla"})
        self.assertEqual(status, prodcli.EXIT_OK, err)
        self.assertFalse(document["written"])
        self.assertIsNone(document["path"])
        self.assertFalse((self.root / "home/alice/.local").exists())

    def test_vanilla_leaves_the_home_completely_untouched(self):
        self.install({"base_preset": "vanilla"})
        self.assertEqual(list((self.root / "home/alice").rglob("*")), [])

    # -- the resolver stays authoritative ---------------------------------

    def test_an_invalid_selection_is_not_persisted(self):
        status, _, err = self.install(
            {
                "base_preset": "kedra",
                "module_overrides": [{"id": "integration", "enabled": False}],
            }
        )
        self.assertEqual(status, prodcli.EXIT_INVALID_PROFILE)
        self.assertIn("refusing to persist", err)
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.root / "home/alice/.local").exists())

    def test_an_unknown_preset_is_not_persisted(self):
        status, _, err = self.install({"base_preset": "nope"})
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertFalse((self.root / "home/alice/.local").exists())

    def test_a_malformed_selection_is_not_persisted(self):
        status, out, err = run(
            [
                "--data-dir", str(DATA_DIR), "install-state", "--selection", "-",
                "--root", str(self.root), "--user", "alice",
            ],
            stdin="{not json",
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertEqual(out, "")
        self.assertFalse((self.root / "home/alice/.local").exists())

    # -- target preconditions ---------------------------------------------

    def test_a_missing_account_is_refused(self):
        status, _, err = run(
            [
                "--data-dir", str(DATA_DIR), "install-state", "--selection", "-",
                "--root", str(self.root), "--user", "nobody-here",
            ],
            stdin=json.dumps({"base_preset": "kedra"}),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("not in", err)
        self.assertIn("after the installer's user step", err)

    def test_a_missing_home_is_refused(self):
        (self.root / "etc" / "passwd").write_text(
            f"ghost:x:{self.uid}:{self.gid}::/home/ghost:/bin/bash\n", encoding="utf-8"
        )
        status, _, err = run(
            [
                "--data-dir", str(DATA_DIR), "install-state", "--selection", "-",
                "--root", str(self.root), "--user", "ghost",
            ],
            stdin=json.dumps({"base_preset": "kedra"}),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("must have been created already", err)

    def test_a_missing_root_is_refused(self):
        status, _, err = run(
            [
                "--data-dir", str(DATA_DIR), "install-state", "--selection", "-",
                "--root", "/nonexistent/target", "--user", "alice",
            ],
            stdin=json.dumps({"base_preset": "kedra"}),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("does not exist", err)

    def test_a_username_that_could_escape_the_root_is_refused(self):
        # `-rf` and friends are argparse's to reject, and it does, with its own
        # exit. These are the ones that reach the validator: a name is used as a
        # path component, so anything that could climb out is refused rather
        # than sanitised.
        for name in ("../root", "a/b", ".."):
            with self.subTest(user=name):
                status, _, err = run(
                    [
                        "--data-dir", str(DATA_DIR), "install-state",
                        "--selection", "-", "--root", str(self.root), "--user", name,
                    ],
                    stdin=json.dumps({"base_preset": "kedra"}),
                )
                self.assertEqual(status, prodcli.EXIT_ERROR)
                self.assertIn("invalid target user name", err)

    def test_a_home_outside_the_root_is_refused(self):
        (self.root / "etc" / "passwd").write_text(
            f"esc:x:{self.uid}:{self.gid}::/../../etc:/bin/bash\n", encoding="utf-8"
        )
        status, _, err = run(
            [
                "--data-dir", str(DATA_DIR), "install-state", "--selection", "-",
                "--root", str(self.root), "--user", "esc",
            ],
            stdin=json.dumps({"base_preset": "kedra"}),
        )
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("outside the root", err)

    def test_it_refuses_to_overwrite_existing_state(self):
        self.install({"base_preset": "kedra"})
        status, _, err = self.install({"base_preset": "kedra"})
        self.assertEqual(status, prodcli.EXIT_ERROR)
        self.assertIn("already exists", err)

    def test_uid_and_gid_may_be_given_explicitly(self):
        # For a target whose passwd the installer has not written yet.
        (self.root / "etc" / "passwd").unlink()
        status, document, err = self.install(
            {"base_preset": "kedra"},
            "--uid", str(self.uid), "--gid", str(self.gid), "--home", "/home/alice",
        )
        self.assertEqual(status, prodcli.EXIT_OK, err)
        self.assertEqual(document["uid"], self.uid)
        self.assertTrue(self.state_path.is_file())
