# Kedra Linux — Installer Polish IP0 Audit

Audit-only document. Read-only inspection of `kedra-calamares` at the frozen M1 baseline.
No production file was modified; this file is the only artefact created (untracked).

Evidence labels used throughout:

- **Observed** — actually executed here (resolver runs, test suite) or rendered in the M1 acceptance runs whose logs/screenshots are on this machine (`~/Build/kedra-accept/{a,b,c}` from the 260829 ISO, and `kedra-iso/tests/install-kedra/` from the final 260904 ISO). The view/branding code is byte-identical between those two builds (see §2), so screenshots from either are valid for UI claims.
- **Source-confirmed** — established directly from repository code/configuration.
- **Inferred** — reasoned but not runtime-verified.

---

## 1. Executive Verdict

The installer is **functionally correct and architecturally clean with respect to M1**, and **product-unfinished as a user experience**. Nothing found in this audit violates the frozen Experience contract: `kedraexperience` consumes `kedra-experience profiles`, honours `user_selectable` from the data, stores only `base_preset` + explicit deltas, and both exec-time jobs fail closed with exact-type validation (129/129 tests pass against the real resolver; all 64 UI-reachable Custom combinations resolve OK). No P0 finding.

The largest real gaps are product-level, not architectural:

1. **The three-page desktop story is incoherent.** The user picks a desktop (`Desktop`, 18 options), then the Kedra Experience (`Desktop Experience`, shown unconditionally even for Plasma/“No Desktop”), then sees every desktop again as checkboxes on `Packages` with internal IDs (`GNOME-Desktop`, `LXQT-Desktop`) and a visible “CachyOS performance and system defaults” group. Choices can contradict each other and nothing reconciles them. (P1)
2. **The Summary/confirmation does not mention the Experience, the desktop, the bootloader, the packages or the user account.** Only Location, Keyboard and Partitions are summarised (source-confirmed: only those three view steps implement `prettyStatus()`/`createSummaryWidget()`). The user commits a disk erase without seeing their Experience choice restated. (P1)
3. **The Experience page is a bare M1.1 widget.** No heading, preset/module descriptions exist in the resolver data but are rendered only as tooltips, the Custom group is titled “Modules”, the Custom tooltip is inaccurate (“parts switched off” — `Kedra Extras` can be switched *on*), there is no “Recommended” marker, and resolver advisories are never shown. Custom reads as a bare checklist rather than “Kedra with explicit changes”. (P1 for the mental-model risk; individual copy items P2)
4. **Progress UX exposes raw shell/pacman text** as the primary status line (“initialize pacman ... ranking mirrors ...”, streamed pacstrap lines, “pacman: …”) and the bar's percentage is uniform-weighted: in the final M1 run `pacstrap`+`packages` took ~71% of wall-clock time while owning ~5% of the bar. (P2)
5. **Visual identity is placeholder by design** (text-only slideshow, generated wordmark, generic upstream package icon filling the chooser frames). These are Branding Integration dependencies; Installer Polish owns the layout around them.

One documentation contradiction to resolve: `README.kedra.md` claims “no Kedra package name appears anywhere in the installer except the resolver path”, but `src/scripts/kedra-verify-target` hard-codes `kedra-experience-desktop`, `kedra-experience-workflow`, the state-file path, the GSettings override glob and the activation unit path. It is an assertion script, not an authority, so it is classified *architecture smell / requires follow-up*, not a boundary violation.

---

## 2. Audit Baseline

| Item | Value |
|---|---|
| Repository | `/home/uras/Projects/kedra/kedra-calamares` |
| Branch | `kedra` |
| HEAD | `3957a075080f065dae9bf1105eae0ec2f2d39bb7` — “kedra-calamares: reconcile the Experience operation on every resolve” (2026-08-30) |
| Tag | `m1` (exact match: `git describe --tags --exact-match` → `m1`) |
| `git status --short` at start | *(empty — clean)* |
| Stash | none |
| Package version | `pkgver=3.4.2`, `pkgrel=5` → **`kedra-calamares 3.4.2-5`** (`packaging/PKGBUILD:27,36`) |
| Upstream remote | `upstream` → `https://github.com/CachyOS/cachyos-calamares.git`; baseline tag `upstream-baseline` = `ce54421cf…` |
| Operative settings file | `settings_online.conf` — `PKGBUILD:158` installs it as `/usr/share/calamares/settings.conf`; the ISO's `calamares-online.sh` also copies `settings_online.conf` → `/etc/calamares/settings.conf` and runs `calamares -D6` (**Observed** in `kedra-iso/archiso/airootfs/usr/local/bin/calamares-online.sh`). `settings.conf`/`settings_offline.conf` are not the shipped flow. |

**Final M1 baseline confirmation.** `kedra-iso/baseline/m1-baseline.json` (`schema: kedra.baseline/1`, `tag: m1`, generated 2026-09-04) records `sources.kedra-calamares.commit = 3957a075…` on branch `kedra` — identical to local HEAD; `packages[]` lists `kedra-calamares 3.4.2-5`, `kedra-experience 0.9.0-1`, `kedra-experience-runtime 0.4.0-2`, `kedra-experience-desktop 0.4.0-1`, `kedra-experience-workflow 0.3.0-2`; `iso.filename = kedra-desktop-linux-260904.iso`, `iso.sha256 = c34a46d9…d353`. **The repository matches the final frozen M1 baseline exactly.** The obsolete `260829` / `3.4.2-4` baseline was not used for any conclusion; the only material from that era used here is UI screenshots, valid because `git diff 5ce28c567 HEAD -- src/modules/kedraexperience src/branding src/modules/*/*.conf settings_online.conf` is empty (only `kedraexperiencepackages/main.py`, `kedraexperiencestate/main.py`, tests and the PKGBUILD changed after 08-26).

Related checkouts used read-only: `kedra-packages` at `5667fbc6…` (= manifest), `kedra-iso` at `930a77e` (= `m1` + the commit that records the manifest; consistent with the manifest's own provenance note).

---

## 3. Exact Current Installer Flow

Source: `settings_online.conf:63-126` (`show` / `exec` / `show`). Sidebar labels **Observed** in `install-kedra/screen-05-experience.png`: *Welcome, Location, Keyboard, Bootloader, Partitions, Desktop, Desktop Experience, Packages, Users, Summary, Install, Finish*.

| # | Instance | Config path | Implementation | Sidebar label (source) | Visible heading / lead text | Primary action | Provenance |
|---|---|---|---|---|---|---|---|
| 1 | `welcome@online` | `src/modules/welcome/welcome_online.conf` | `src/modules/welcome/WelcomePage.cpp`, `Config.cpp` | “Welcome” (`WelcomeViewStep.cpp:46`) | “Welcome to the Kedra Linux installer” (`Config.cpp:281`, `welcomeStyleCalamares: false`) / “This program will ask you some questions and set up Kedra Linux on your computer.” (`Config.cpp:80`) | pick language; requirement check | Upstream page; CachyOS config (`requiredStorage 8.0`, `requiredRam 2.5`, `internetCheckUrl wiki.cachyos.org`, GeoIP `geoip.kde.org`); Kedra strings via branding |
| 2 | `locale` | `src/modules/locale/locale.conf` | `src/modules/locale/` | “Location” (`LocaleViewStep.cpp:71`) | timezone map | pick region/zone | Upstream (GeoIP `geoip.kde.org/v1/calamares`, default America/New_York) |
| 3 | `keyboard` | `src/modules/keyboard/keyboard.conf` | `src/modules/keyboard/` | “Keyboard” (`KeyboardViewStep.cpp:41`) | model/layout/variant lists | pick layout | Upstream |
| 4 | `packagechooser@bootloader` | `src/modules/packagechooser/packagechooser_bootloader.conf` | `PackageChooserPage.cpp`, `page_package.ui` | “Bootloader” (conf `labels.step`) | none — list + name + placeholder image + description | single-select, `mode: required`, `method: legacy`, default `grub` | CachyOS downstream config; Kedra removed `screenshot:` keys and set default GRUB |
| 5 | `partition` | `src/modules/partition/partition.conf` | `src/modules/partition/` | “Partitions” (`PartitionViewStep.cpp:122`) | device selector; “Erase disk / This will delete all data…” / “Manual partitioning” | choose partitioning | CachyOS config (btrfs default, `bootloaderOverrides`), unchanged by Kedra |
| 6 | `packagechooser@desktop` | `src/modules/packagechooser/packagechooser_desktop.conf` | as #4 | “Desktop” (conf `labels.step`) | none — list of 18 desktops + description | single-select, `mode: required`, `method: netinstall-select`, default `GNOME-Desktop` | CachyOS config; Kedra preselects GNOME, removed screenshots |
| 7 | `kedraexperience` | `src/modules/kedraexperience/kedraexperience.conf` | `KedraExperiencePage.cpp`, `KedraExperienceViewStep.cpp` | “Desktop Experience” (`KedraExperienceViewStep.cpp:50`) | “Choose how much of the Kedra desktop experience to install.” (`KedraExperiencePage.cpp:192`) | radio: Kedra Experience / Vanilla GNOME / Custom Kedra Experience; Custom reveals “Modules” checkboxes | **Kedra Experience integration** |
| 8 | `netinstall` | `src/modules/netinstall/netinstall.conf` + `netinstall.yaml` | `src/modules/netinstall/` | “Packages” (conf `label.sidebar`) | “Additional packages” (conf `label.title`) | tree of groups with checkboxes | CachyOS config/data; Kedra removed remote `groupsUrl`, edited groups |
| 9 | `users` | `src/modules/users/users.conf` | `src/modules/users/` | “Users” (`UsersViewStep.cpp:48`) | “What is your name?” … | fill account/hostname/passwords | Upstream page; CachyOS config; Kedra hostname template `kedra-${cpu}` |
| 10 | `summary` | `src/modules/summary/summary.conf` | `SummaryPage.cpp`, `SummaryModel.cpp`, `Config.cpp` | “Summary” (`Config.cpp:35`) | “This is an overview of what will happen once you start the install procedure.” (`Config.cpp:44`) | review; Next is labelled “Install” → confirm dialog | Upstream |
| 11 | *(exec phase)* | `settings_online.conf:78-124` | `src/libcalamaresui/viewpages/ExecutionViewStep.cpp`; slideshow `src/branding/kedra/show.qml` | “Install” (`ExecutionViewStep.cpp:117`) | slideshow + progress bar + status label | none (Back/Next hidden, Cancel enabled) | Upstream view; Kedra slideshow |
| 12 | `finished` | `src/modules/finished/finished.conf` | `FinishedPage.cpp` | “Finish” (`FinishedViewStep.cpp:46`) | “All done.” / “Kedra Linux has been installed on your computer. You may now restart into your new system, or continue using the Kedra Linux Live environment.” | “Restart now” checkbox (unchecked), “Done” | Upstream page; CachyOS config (`restartNowMode: user-unchecked`, `systemctl -i reboot`) |

Per-page navigation/validation details are in §14. Conditional visibility: **none of the 12 pages is conditional** — the Experience page is shown regardless of the desktop chosen on page 6 (source-confirmed: `KedraExperienceViewStep` has no visibility logic; the sequence is static).

Exec phase (jobs, in order, from the final M1 log `kedra-iso/tests/install-kedra/kedra-install-kedra.log:514-565`, **Observed**): 50 jobs, total weight 37. Kedra-specific jobs: #14 “Preparing your system for the installation of Kedra Linux” (`shellprocess@before-online`), #27 `kedraexperiencepackages`, #44 `kedraexperiencestate`, #48 “Verifying the installed Kedra system”.

---

## 4. Welcome / First Impression

**Observed** (`kedra-accept/a/screen-07-english.png`, `install-kedra/screen-03-english.png`): window title “Kedra Linux Installer”; heading “Welcome to the Kedra Linux installer”; body sentence; the generated wordmark (`welcome.png`, 900×516, “Kedra Linux / rolling release”); language combo “American English”; Back (disabled) / Next / Cancel; bottom sidebar with a “K” logo and 12 step labels. No requirement warnings were visible (all satisfied). No Support / Known-issues buttons are visible.

| Element | Source | Value | Classification |
|---|---|---|---|
| Window title | `src/calamares/CalamaresWindow.cpp:431-432` `tr("%1 Installer")` + `productName` | “Kedra Linux Installer” | C valid upstream |
| Heading | `src/modules/welcome/Config.cpp:281` with `versionedName` (“Kedra Linux”, `branding.desc:38`) | “Welcome to the Kedra Linux installer” | C |
| Body | `Config.cpp:80` | “This program will ask you some questions and set up Kedra Linux on your computer.” | C (generic; Installer Polish may add product copy later — P3) |
| Wordmark | `branding.desc:54` `productWelcome: welcome.png` (generated by `packaging/kedra-branding-images`) | placeholder wordmark | **B Branding Integration** (asset); layout slot is Installer Polish |
| Sidebar logo | `branding.desc:52` `productLogo: logo.png` (64×64), rendered by `src/branding/kedra/calamares-sidebar.qml:32-42` at 30×30 | “K” glyph | B |
| Support / Known issues | `welcome_online.conf:17-18` `show…Url: true` but `branding.desc` defines **no** `supportUrl`/`knownIssuesUrl`; `WelcomePage.cpp:154-157` hides a button whose URL is empty | hidden | C now; A/E later (needs a Kedra URL — README: “No Kedra domain has been chosen”) |
| Requirements | `welcome_online.conf:54-70`: check storage/ram/power/internet/root/screen; required ram + internet | strings in `checker/GeneralRequirements.cpp:160-218` (“is connected to the Internet”, …) | C |
| Internet probe | `welcome_online.conf:47` `https://wiki.cachyos.org` | never displayed | C (documented rationale) |
| GeoIP language | `welcome_online.conf:93-96` → `geoip.kde.org/v1/ubiquity` | **Observed**: auto-selected `tr_TR` on first launch (`kedra-install-kedra.log:218-220`, `kedra-accept/a/screen-02-installer-welcome.png` shows Turkish UI with English sidebar labels) | **uncertain / ownership decision** — conflicts with “English-first”; third-party lookup at startup (P2) |
| Language combo icon | upstream `language-icon-*.png` | standard | C |
| CachyOS naming | none on this page | — | — |
| Generic Calamares naming | `calamares.desktop` (“Install System / Calamares — System Installer”), `io.calamares.calamares.appdata.xml` — **not shipped**: `PKGBUILD:186` deletes `usr/share/applications` | not reachable | C |
| Stale Kedra wording | none on Welcome; slideshow slide 4 “Early days … development build” is intentional (§12) | — | A (product copy decision) |
| Mixed identity | English sidebar under Turkish UI (**Observed**) — Kedra step name `tr("Desktop Experience")` has no translation and no `CALAMARES_RETRANSLATE` in the page | mixed only when a non-English UI language is active | A, P3 (English-first) |

---

## 5. Experience Architecture Trace

```
kedra-experience-runtime (ISO)          kedra-calamares                                   target
────────────────────────────            ───────────────────────────────────────────       ──────────────
/usr/bin/kedra-experience               [view] kedraexperience
  profiles --format json  ───JSON──▶    KedraExperienceViewStep::loadProfiles()
  (profiles_schema_version 1)           ├─ exists? timeout 60s? exit 0? JSON object? version==1?
                                        └─ KedraExperiencePage::setProfiles(profiles, basePreset="kedra")
                                             ├─ 1 QRadioButton per preset with user_selectable==true
                                             │    (resolver order: kedra, vanilla)
                                             ├─ Custom radio (installer framing) + 1 QCheckBox per
                                             │    basePreset module with user_selectable==true
                                             │    (checked = module.enabled)
                                             └─ preselect basePreset radio
                                        onLeave() → GlobalStorage["kedraExperienceSelection"] =
                                             '{"base_preset": <id>, "module_overrides": [deltas]}'
                                                                     │
  resolve --selection -  ◀──stdin JSON──┘  [job] kedraexperiencepackages/main.py::run()
  (resolve_schema_version 2)  ──stdout──▶   _check_schema, _check_valid (diagnostics.ok, refused,
                                             violations), _required_packages, _applies_experience_layer,
                                             _resolved_selection, _preset_id  (exact JSON types)
                                            ├─ GS["kedraExperienceResolvedSelection"] = json(selection)
                                            ├─ GS["kedraExperienceAppliesLayer"]     = bool
                                            └─ GS["packageOperations"] ⟵ remove ops with source==
                                               "kedraexperiencepackages", then append
                                               {"install": packages.required, "source": …}
                                                                     │
                                           [job] packages@online ────┘ installs packageOperations
                                                                     │
  install-state --selection - --root R --user U ◀── [job] kedraexperiencestate/main.py::run()
  (install_state_schema_version 1) ──stdout──▶  written/path/applied_recorded checks; written must
                                                  equal GS["kedraExperienceAppliesLayer"]
                                                                     └──▶ R/home/U/.local/state/kedra/
                                                                          experience-state.toml
  [job] shellprocess@kedra_verify → src/scripts/kedra-verify-target (assertions only)
```

Answers to the 18 required questions (all **Source-confirmed** unless noted):

1. **Where metadata is obtained**: `KedraExperienceViewStep::loadProfiles()` (`KedraExperienceViewStep.cpp:68-136`) runs `<resolver> profiles --format json` with `QProcess`, resolver path from `kedraexperience.conf:8` (`/usr/bin/kedra-experience`). It is called from `widget()` (`:53-66`), which `ViewManager::insertViewStep()` calls at module load (`src/libcalamaresui/ViewManager.cpp:134-149`) — i.e. **once, synchronously, at installer start-up**, not on page entry.
2. **Who executes the resolver**: view step (`profiles`), `kedraexperiencepackages/main.py:_run_resolver` (`resolve`, `:149-221`), `kedraexperiencestate/main.py:run` (`install-state`, `:180-198`).
3. **Boundary format**: JSON documents on stdout; selection JSON on stdin; version-pinned: `profiles_schema_version == 1` (`KedraExperienceViewStep.cpp:119-132`), `resolve_schema_version ∈ [2]` (`kedraexperiencepackages.conf:14`, `main.py:224-243`), `install_state_schema_version ∈ [1]` (`kedraexperiencestate.conf:6`). Resolver exit codes 0/1/2 are part of the contract (`main.py:100-103`). **Observed** real documents: §5 tables below.
4. **Internal representation**: `QList<QPair<QRadioButton*, QString presetId>> m_productPresets`, `QRadioButton* m_customButton`, `QList<QPair<QCheckBox*, KedraModule{id,name,description,defaultEnabled}>> m_moduleBoxes` (`KedraExperiencePage.h:29-35, 84-85`). Published form is the selection document (`KedraExperiencePage::selection()`, `:206-247`).
5. **Vanilla**: the resolver preset `vanilla` (`user_selectable: true`, `applies_experience_layer: false`, `modules: []`) → radio “Vanilla GNOME”; selection `{"base_preset":"vanilla","module_overrides":[]}`; resolve gives `packages.required = []` → `_reconcile_package_operations([])` removes any earlier Kedra op and adds none (`main.py:344-384`); `install-state` writes nothing (`written == false == applies`).
6. **Kedra**: preset `kedra` → radio “Kedra Experience”; selection `{"base_preset":"kedra","module_overrides":[]}`; **Observed** `packages.required = [gnome-console, gnome-shell, kedra-experience, kedra-experience-desktop, kedra-experience-runtime, kedra-experience-workflow]`; state file written (final M1 log `:3679`, `:7298`).
7. **Custom**: installer's own framing (`KedraExperiencePage.cpp:126-128, 169-177`): radio “Custom Kedra Experience”; selection `{"base_preset": m_basePreset, "module_overrides": [ {id, enabled} for each checkbox ≠ defaultEnabled ]}` (`:215-235`).
8. **`base_preset = kedra`**: from `kedraexperience.conf:13` `basePreset: "kedra"` → `m_basePreset` (`KedraExperienceViewStep.cpp:202-204`) → `setProfiles(profiles, basePreset)`; Kedra radio carries the same id because the resolver lists it; a `basePreset` absent from the offered presets is a hard error (`KedraExperiencePage.cpp:108-124`). `tests/kedra-experience/test_config.py` asserts the config value exists in the metadata, is selectable, applies the layer, and offers ≥1 selectable module.
9. **`user_selectable` consumption**: preset level `KedraExperiencePage.cpp:75-78`; module level `:136-139`. `integration` (`user_selectable: false`) is therefore never rendered (**Observed** screenshot `a/screen-15-custom-modules.png`: 6 boxes, no “Kedra Integration”).
10. **Custom control set generation**: `:129-156` iterates `presets[basePreset].modules` in resolver order, creating `QCheckBox(name)` with `toolTip = description`, `checked = enabled`.
11. **Display names**: preset `name` (`:89-90`, fallback id), module `name` (`:146`, fallback id). Only the Custom radio label (“Custom Kedra Experience”) and the group title (“Modules”) are installer strings.
12. **Descriptions**: preset `description` → radio tooltip (`:91`); module `description` → checkbox tooltip (`:151`). **Not rendered as visible text.**
13. **Initial/default state**: radio = `basePreset` (`:179-189`, deliberately not the resolver's `default_preset`, which is `vanilla` in the **Observed** document); checkboxes = `module.enabled` from the resolver (`:148, :152`).
14. **Inherited vs explicit**: inherited = checkbox equals `defaultEnabled`; explicit override = differs. Only differences are serialised (`:224-227`). A Kedra/Vanilla radio ignores the checkboxes entirely (`:237-245`).
15. **Override recording**: `module_overrides: [{"id","enabled"}]` in `GS["kedraExperienceSelection"]` (`KedraExperienceViewStep.cpp:181-184`); after resolve, the resolver's echoed `selection` is stored in `GS["kedraExperienceResolvedSelection"]` (`main.py:426`) and that — not the UI value — is what `install-state` persists (`kedraexperiencestate/main.py:71-74, 148`). **Observed** `tests/…/test_state.py::CustomStateTests` confirm only deltas are stored.
16. **Hand-off to execution**: `GS["packageOperations"]` append (`main.py:377`), consumed by `packages@online` (`settings_online.conf:95-96` ordering comment). `GS["username"]`, `GS["rootMountPoint"]` consumed by the state job.
17. **Failure handling**: view — missing/timeout/non-zero/invalid JSON/wrong version/no presets/none selectable/basePreset missing/no selectable modules → `setError()` hides all controls, shows the message in a plain-text `QLabel`, `m_ready=false`, `isNextEnabled()==false` (`KedraExperienceViewStep.cpp:138-145`); no retry, no re-run on re-entry. Job — every path returns `(title, message)` → Calamares aborts the run (`JobQueue.cpp:427-431` skips non-emergency jobs) and shows the error dialog + “Installation Failed”. GlobalStorage is untouched on a failed re-run (`main.py:403-418`; `test_rerun.py::FailedRerunTests`).
18. **Duplicated semantics**: none in the three modules. The only Experience literals in the installer are the resolver path, the schema-version pins, `basePreset: "kedra"` (a pointer), `"Custom Kedra Experience"`/`"Modules"` (presentation), and the GlobalStorage key names. See §19 for `kedra-verify-target`.

**Observed resolver documents (frozen `kedra-packages` `5667fbc`)** — `profiles`: `default_preset: "vanilla"`, `experience_schema_version: 6`, presets in order `kedra` (7 modules: appearance ✔, desktop ✔, extensions ✔, extras ✘, integration ✔ *not selectable*, terminal ✔, workflow ✔ — each with `name`, `description`, `tier` ∈ {core, recommended, optional}, `version`) then `vanilla`. `resolve` also returns `packages.recommended/optional/conditional` and `diagnostics.advisories`; the installer consumes only `packages.required` and validates-but-discards advisories (`main.py:263-266`).

**M1 boundary check**: no violation found. Two notes for follow-up (not violations): (a) the page's preselected product is the config `basePreset`, while the resolver's document says `default_preset: vanilla` — a presentation decision, documented in `kedraexperience.conf:10-11`, but two different “defaults” exist; (b) `tier` and `advisories` are contract data the installer could present and currently does not.

---

## 6. Vanilla / Kedra / Custom Presentation

**Observed** (`install-kedra/screen-05-experience.png`, `a/screen-15-custom-modules.png`): a single lead sentence, three plain `QRadioButton`s in the order **Kedra Experience → Vanilla GNOME → Custom Kedra Experience**, Kedra preselected, and (Custom only) a `QGroupBox` titled “Modules” with six `QCheckBox`es. The rest of the page (~70% of its height at 1280×800) is empty.

| | Vanilla GNOME | Kedra Experience | Custom Kedra Experience |
|---|---|---|---|
| Displayed name (source) | resolver `presets[vanilla].name` | resolver `presets[kedra].name` | `KedraExperiencePage.cpp:169` `tr("Custom Kedra Experience")` |
| Description / help | tooltip only: “Upstream GNOME as GNOME ships it. The Kedra user experience layer is not applied.” (resolver) | tooltip only: “Kedra's curated GNOME desktop experience. Opinionated defaults, every module user-overridable.” (resolver) | tooltip only: “The Kedra Experience, with individual parts switched off.” (`:171`) |
| Widget | `QRadioButton` in `QButtonGroup` (`:89-94`) | same | same (`:169-177`) |
| Default / Recommended | not default; no marker | **preselected** (`:182-189`); **no “Recommended” label or any visual emphasis** | not default |
| Selected state | native radio indicator (not colour-only) | same | same + the “Modules” group becomes visible (`rebuildModuleVisibility`, `:199-204`) |
| Focus | native Qt focus rect; arrow keys move within the group; Tab enters the group at the checked radio (standard `QAbstractButton` exclusive-group behaviour — **Inferred**, needs runtime validation) | same | same; Tab then visits each checkbox |
| Interactive surface | the radio + its text label only (no card/container) | same | same |
| Switching | only `m_moduleArea` visibility toggles; checkbox state is **never reset** | same | leaving Custom hides the boxes; returning shows them with the previous state (`:199-204`; no reset code exists) |
| Semantic accuracy | name/description are accurate but the description is invisible | accurate; “Recommended” not communicated | tooltip is **inaccurate**: `Kedra Extras` defaults to off and can be switched **on**; “Custom” also carries no visible statement that it *is* Kedra plus changes |

Mental-model evaluation (**Observed** rendering + source):

- *Vanilla GNOME* — clear enough by name; nothing tells the user what “the Kedra Experience layer” is, so the contrast is not explained on-screen.
- *Kedra Experience* — presented as one of three equals; the recommended/curated nature is not shown.
- *Custom Kedra Experience* — because the group is titled “Modules”, the boxes are bare names (“Kedra Desktop”, “Kedra Workflow”) with hidden descriptions, and nothing says “starts from Kedra Experience; change what you need”, Custom **can read as** (a) a build-your-own list — mitigated only by the boxes being pre-checked, (b) a package checklist — the names look like package groups and the previous page was a package chooser, (c) an advanced/debug surface — “Modules” is implementation vocabulary. It does *not* read as a separate independent preset (it is visibly under the same lead sentence). Flag: **P1 (mental model)**; individual copy items P2.

Ordering note: the frozen contract lists Vanilla → Kedra → Custom; the page renders resolver order then Custom (Kedra → Vanilla → Custom). This is not a semantic problem, but the recommended option sitting first with no marker is what makes it look arbitrary.

---

## 7. Custom Module Selection

| Aspect | Evidence |
|---|---|
| Component | `src/modules/kedraexperience/KedraExperiencePage.cpp:129-167` (creation), `:199-204` (visibility), `:215-235` (serialisation) |
| Enumeration source | `presets[basePreset].modules` from the `profiles` document; **6 user-selectable items on the final baseline** (**Observed**: appearance, desktop, extensions, extras, terminal, workflow; `integration` excluded) |
| `user_selectable` source | resolver module field (`:136`) |
| Labels / descriptions | resolver `name` / `description` (`:146-147`); description → tooltip only |
| Control type | `QCheckBox` |
| Ordering | resolver document order (alphabetical by id in the **Observed** document); no installer sort |
| Grouping | one `QGroupBox("Modules")`; `tier` (core/recommended/optional) **not used** |
| Default/inherited representation | checked = `module.enabled`; no visual distinction between “inherited default” and “explicitly changed” |
| Explicit override representation | none on screen; only in the serialised document |
| Reset/default | **none** — no “reset to Kedra defaults”; switching to Kedra and back does not reset |
| Scrolling | **no `QScrollArea`** on the page; the page is a plain `QVBoxLayout` with `addStretch(1)` (`:22-42`) |
| Dynamic content | fully dynamic; nothing assumes a count; layout grows with the list (see §15 for the overflow risk) |
| Empty state | 0 selectable modules → `setError(...)` “Installer configuration error: the "%1" profile offers no user-selectable modules…” and Next blocked (`:158-167`) |
| Metadata error | any parse/version/shape problem → error label, Next blocked (§13) |
| Disabled state | none — controls are hidden, never disabled |
| Fixed-count assumptions | none in code |

**Prohibited-duplication search** (`git grep` over the three Kedra modules, scripts, shellprocess and chooser configs for module ids, package names, GSettings/dconf, `user_selectable`, dependency rules): **no hard-coded module arrays, package maps, dependency rules, allowlists, GNOME settings or resolver defaults** exist in `kedraexperience*`. Presentation constants present and harmless: `"Custom Kedra Experience"`, `"Modules"`, the lead sentence, the error strings. The one place Experience vocabulary is hard-coded is `src/scripts/kedra-verify-target:178-273` (assertions; §19).

**Advisory gap (Observed)**: resolving `desktop=off, workflow=on` yields `diagnostics.advisories[0].message = "module 'workflow' works best with 'desktop', which is disabled; …"`. The page never calls `resolve`, and the job validates advisories' shape but does not surface them; the user is never told.

---

## 8. Package / Desktop Selection Relationship

**Pages involved**: #4 `packagechooser@bootloader`, #6 `packagechooser@desktop`, #7 `kedraexperience`, #8 `netinstall`.

**Desktop chooser** (`packagechooser_desktop.conf`): `mode: required`, `method: netinstall-select`, `default: GNOME-Desktop`; 19 items (“No Desktop”, Plasma Desktop, GNOME, Cosmic, Niri, Cinnamon, Budgie, Mate, Xfce4, LXQT, LXDE, Hyprland, MangoWM, Sway, Wayfire, i3 Window Manager, Qtile, bspwm, Openbox — `:41-277`). Selecting an item puts its id into `GS["netinstallSelect"]` (`packagechooser/Config.cpp:169-198`), which pre-checks the same-named netinstall group. Page has no heading; the right pane shows the generic placeholder `:/images/no-selection.png` for every item because every `screenshot:` key was removed (`PackageChooserPage.cpp:76-79`; **Observed** `a/screen-13-desktop.png`).

**Netinstall page** (“Additional packages”, `netinstall.yaml`) shows, **Observed** (`a/screen-17-packages.png`):

- “System tuning — **CachyOS performance and system defaults. Recommended.**” (checked, critical; `netinstall.yaml:43-44`)
- “Shell configuration — Preconfigured fish and zsh setups” (checked)
- “Base-devel + Common packages — Recommended. Don't change unless you know what you're doing (generic).” (checked, critical) with subgroups Network/firewall/bluetooth/… ; package leaves are shown by raw name when expanded (`PackageTreeItem.cpp:135`)
- **all 18 desktop groups again**, named by internal id (`KDE-Desktop`, `GNOME-Desktop` ✔, `Cosmic`, … `LXQT-Desktop`, `LXDE-Desktop`, `i3-Window-Manager`, …), each uncheckable/checkable
- “MangoWM — … with CachyOS-provided dotfiles.” (`:680`)
- “Firefox and language package”, “Printing-Support”, “Support for HP Printer/Scanner”, “Accessibility Tools”
- hidden (not shown): “Kedra required (hidden)”, “CachyOS kernels (hidden)”, “CachyOS kernels (hidden/handheld)”; “CachyOS Handhelds” is `platform: handheld` only.

Findings:

- **Duplicated/contradictory choice (P1, Installer Polish presentation)**: the desktop is chosen on page 6 and re-offered as checkboxes on page 8. The user can uncheck `GNOME-Desktop`, or add `KDE-Desktop`, after the Experience page has already assumed GNOME. Nothing reconciles the three pages.
- **Experience shown for any desktop (P1)**: page 7 is unconditional. Choosing Plasma/No Desktop then “Kedra Experience” installs `gnome-shell`, `gnome-console` and the GSettings override packages beside (or without) the chosen desktop, and records a per-user profile. The resolver is correct to require GNOME packages; it does not know the chooser's answer. Whether to gate/hide/explain the page is an installer-flow decision, needing Experience/Resolver sign-off on the rule's wording (`user_selectable`/availability must stay with the resolver — a page-visibility rule keyed on the chooser value is installer flow, not module availability; flagged for ownership decision).
- **Internal package names exposed**: yes — netinstall leaves (`cachyos-settings`, `cachyos-micro-settings`, `linux-cachyos` is hidden) and group ids as group names.
- **CachyOS-specific groups remain**: “System tuning” (visible description names CachyOS), “Shell configuration” (`cachyos-fish-config`/`-zsh-config`), hidden kernel groups, `CachyOS Handhelds`. Content is base-system policy (out of scope); the *visible wording* is Installer Polish.
- **Gaming/performance assumptions**: no gaming group in the shipped `netinstall.yaml`; “CachyOS performance and system defaults” is the inherited performance framing.
- **Overlap with Experience modules**: technical — `GNOME-Desktop` group carries `gnome-shell`, `gnome-console`, `gnome-control-center` which also appear in the resolver's `packages.required`; pacman de-duplicates and the job deliberately does not filter (`main.py:359-362`) — harmless. Conceptual — “GNOME Apps” subgroup vs `Kedra Extras` module (both are “more GNOME software”) and “Shell configuration” vs `Kedra Terminal` (different scope, similar-sounding) can confuse.
- **Selected packages → Experience**: none. **Experience → packages**: only via `packageOperations` at exec time. Ordering (Desktop → Desktop Experience → Packages) does not make this relationship visible; the user sees three unrelated package-ish pages.

Package *content* decisions (which groups exist, what is in `System tuning`) are **outside Installer Polish** (base-system ownership); their **names, descriptions, ordering and whether the desktop groups are re-shown** are Installer Polish.

---

## 9. Terminology and User-Visible Copy

Inventory of important rendered strings (internal-only strings excluded; `tr()`/`_()` contexts checked for actual rendering paths).

| Surface | Exact text | Source | Issue |
|---|---|---|---|
| Sidebar step | “Desktop Experience” | `KedraExperienceViewStep.cpp:50` | adjacent to “Desktop” and “Packages”; only two-line label in the bar (**Observed**) — IA ambiguity |
| Experience lead | “Choose how much of the Kedra desktop experience to install.” | `KedraExperiencePage.cpp:192` | “how much” frames it as quantity; no heading |
| Group title | “Modules” | `KedraExperiencePage.cpp:36` | implementation jargon |
| Custom tooltip | “The Kedra Experience, with individual parts switched off.” | `:171` | inaccurate (Extras switches on); hidden in tooltip |
| Preset/module descriptions | resolver strings incl. “GSettings”-free but technical phrasing (“Baseline Kedra GNOME shell behaviour: window management, workspaces and panel behaviour.”, “Distribution integration points: branding provider hand-off…” — the latter is never shown) | resolver (`kedra-packages`) | tooltip-only; wording owned by Experience |
| Page errors | “The Kedra Experience resolver was not found at %1. This program is installed by the kedra-experience-runtime package; the installation medium appears to be incomplete.” | `KedraExperienceViewStep.cpp:78-82` | “resolver”, package name in primary text (acceptable for a broken medium; jargon) |
| | “The Kedra Experience metadata is version %1, which this installer does not understand (it expects %2).” | `:127-130` | jargon |
| | “Installer configuration error: basePreset is set to "%1", which the Kedra Experience metadata does not define. … Correct basePreset in kedraexperience.conf.” | `KedraExperiencePage.cpp:118-121` | config-key names in UI (developer-facing by design) |
| | “The available desktop profiles could not be listed.\n\n%1” (raw stderr) | `KedraExperienceViewStep.cpp:101-103` | raw stderr in primary text |
| Job titles | “Kedra Experience resolver failed”, “Kedra Experience profile could not be resolved”, “Kedra Experience profile is not valid”, “Kedra Experience version mismatch”, “Kedra Experience profile not recorded” | `kedraexperiencepackages/main.py:144,155,193,202,237,281`; `kedraexperiencestate/main.py:151,159,200,217,243,252,259,275` | “resolver”, “profile” vocabulary |
| Job bodies | “The resolver's answer is malformed: diagnostics.ok is not a boolean.” etc.; `<pre>{stderr}</pre>` | `main.py:141-146, 258-262, 318, 326, 334-337` | internal field names and raw output (fail-closed by design; jargon) |
| Job progress | “Resolving the Kedra Experience profile.” / “Recording the Kedra Experience profile.” | `main.py:108-109`, `kedraexperiencestate/main.py:84-85` | mild jargon |
| Exec status | “initialize pacman ... ranking mirrors ... copy pacman mirrorlist and keyring to target ...” (**Observed** on screen) | `shellprocess_initialize_pacman.conf:36` | jargon, lowercase, ellipses |
| | “modify 90-mkinitcpio-install.hook on host and chroot” / “reset 90-mkinitcpio-install.hook” | `shellprocess_modify_mk_hook.conf:22`, `shellprocess_reset_mk_hook.conf:14` | jargon |
| | “Enable the UFW Firewall as default and allow outgoing but deny incoming” | `shellprocess_enable_ufw.conf:14` | jargon |
| | “Configuring noctalia-greeter...” (runs even on GNOME) | `shellprocess_greetd.conf:19` | irrelevant/jargon |
| | streamed pacman/pacstrap lines; “pacman: <line>” | `pacstrap/main.py:49-53`, `packages/main.py:438-439` | raw output as primary status |
| Packages page | “Additional packages” | `netinstall.conf:40` | fine; but content contradicts “additional” (desktops) |
| | “CachyOS performance and system defaults. Recommended.” | `netinstall.yaml:44` | CachyOS identity |
| | “Recommended. Don't change unless you know what you're doing (generic).” | `:77` | user-hostile |
| | `GNOME-Desktop`, `KDE-Desktop`, `LXQT-Desktop`, `i3-Window-Manager`, … | `:223,290,579,741` | internal ids as labels |
| | “MangoWM is a Wayland compositor using the Noctalia shell, with CachyOS-provided dotfiles.” | `:680` | CachyOS |
| Desktop chooser | “No Desktop … your system will start up in text-only mode …” | `packagechooser_desktop.conf:47` | fine (upstream) |
| Summary | “This is an overview of what will happen once you start the install procedure.” | `summary/Config.cpp:44` | fine; content incomplete |
| Partition summary | “**Erase** disk **/dev/vda** (vda) and install Kedra” | `PartitionViewStep.cpp:204` (`shortProductName`) | fine |
| Confirm dialog | “Continue with Installation?” / “The Kedra installer is about to make changes to your disk in order to install Kedra. **You will not be able to undo these changes.**” / “Install Now” / “Go Back” | `ViewManager.cpp:341-360` | fine; no restatement of choices |
| Progress | “%p%” bar format; “Install” step | `ExecutionViewStep.cpp:77,117` | precision (§12) |
| Failure | “Installation Failed” dialog; “Kedra Linux has not been installed on your computer. The error message was: %2.” | `ViewManager.cpp:161-162`, `FinishedPage.cpp:120-125` | fine |
| Finish | “All done.” / “Kedra Linux has been installed on your computer. You may now restart into your new system, or continue using the Kedra Linux Live environment.” / “Restart now” / “Done” | `FinishedPage.cpp:91-96`, `FinishedPage.ui:102`, `ViewManager.cpp:435` | fine |
| Slideshow | “Welcome to Kedra Linux”, “Built on CachyOS”, “Rolling, not versioned”, “Early days … what you see here is placeholder.” | `show.qml:52-85` | intentional placeholder; product-copy decision |
| Cancel | “Cancel Installation? Do you really want to cancel the current install process?…” | `ViewManager.cpp:544-547` | upstream |

Term search results (user-facing occurrences only): **resolver** — page/job error strings above; **module** — “Modules” group title, job strings (“combination of Kedra Experience modules”); **module graph** — none; **preset** — “basePreset … kedraexperience.conf” config-error string only; **vendor override** — none rendered; **GSettings/schema** — none rendered by the installer (`kedra-verify-target` prints “Kedra GSettings vendor override” into the *log*/verbose output, which surfaces only in the failure dialog's details); **packageOperations** — none rendered; **manifest** — none; **package identifiers** — netinstall leaves and group ids; **serialized overrides** — none rendered (log only, `cDebug`).

---

## 10. CachyOS / Upstream Remnant Inventory

| Path | Exact text / asset | Visible surface | Reachable? | Owner | Priority |
|---|---|---|---|---|---|
| `src/modules/netinstall/netinstall.yaml:44` | “CachyOS performance and system defaults. Recommended.” | Packages page, first row | **Yes, every install** (Observed) | A (wording) / base-system (content) | P1 (with §8) |
| `netinstall.yaml:680-691` | “…with CachyOS-provided dotfiles.” (+11 translations) | Packages page | Yes | A | P2 |
| `netinstall.yaml:223…841` | group names = ids (`GNOME-Desktop`, …) | Packages page | Yes | A | P2 |
| `netinstall.yaml:1-42, 280-289` | “Kedra required (hidden)”, “CachyOS kernels (hidden)”, “CachyOS Handhelds” | hidden / handheld-only | No (desktop) | C | — |
| `netinstall.yaml:52-53, 74-75` | `cachyos-settings`, `cachyos-fish-config`… leaves | Packages page when expanded | Yes | C (correct technical names) | P3 |
| `src/branding/kedra/show.qml:54,61-65` | “Built on CachyOS” slide | Install slideshow (Observed `a/screen-23-installing.png`) | Yes | A (copy) + B (artwork replaces slideshow) | P2 |
| `src/scripts/btrfs-installation-snapshot:13,16-17` | `--description "Fresh CachyOS Installation"`; writes `/etc/cachyos/installation-snapshot` | installed system (snapper list / boot menu snapshot entries) | Yes, post-install | **D/E** — installer-owned script, visible outside the installer; cleanup candidate (IP5) with base-system confirmation | P2 |
| `src/scripts/bootloader-post-setup:7` | `TARGET_OS_NAME="CachyOS"` for limine-snapper | limine path only | Not on default GRUB path | C/D | P3 |
| `src/modules/bootloader/main.py:812-829` | `# CachyOS Limine theme`, `/+CachyOS` menu group | limine boot menu | Not on default path (documented) | E (upstream module; documented in README) | P3 |
| `src/modules/bootloader/bootloader.conf:92` | `limineSplashLogo: /usr/share/wallpapers/cachyos-wallpapers/limine-splash.png` (package not installed) | limine | No (missing file → no splash) | D | P3 |
| `src/modules/packagechooser/images/no-selection.png` | generic upstream package icon | Bootloader & Desktop chooser frames (Observed) | Yes | C asset; A layout; B for real screenshots | P2 |
| `src/modules/welcome/welcome_online.conf:47` | `internetCheckUrl: https://wiki.cachyos.org` | never rendered | No | C (documented) | — |
| `src/modules/zfs/zfs.conf:9` | `poolName: zpcachyos` | ZFS path only | Not default | C (documented) | — |
| `src/libcalamaresui/utils/TestPaste.cpp` | `paste.cachyos.org` | unit test only | No | C | — |
| `src/branding/kedra/calamares-sidebar.qml:1-13` | “Sample of QML progress tree … This example emulates…” | comment only | No | D (stale sample header) | P3 |
| `src/branding/kedra/lang/calamares-default_*.ts` | “This is a second Slide element.” | unused catalog for the old sample slideshow | No | D | P3 |
| `src/branding/kedra/stylesheet.qss` | upstream sample stylesheet, mostly commented | applied (font 13px, button rules) | Yes (visual) | A/B | P3 |
| `calamares.desktop`, `io.calamares.calamares.appdata.xml`, `io.calamares.calamares.policy` | “Install System / Calamares — System Installer” | not shipped (`PKGBUILD:186`) | No | C | — |
| `src/modules/shellprocess/shellprocess_greetd.conf:19` | “Configuring noctalia-greeter...” | exec status, every install (Observed job #46) | Yes | A (wording) | P2 |
| `src/modules/shellprocess/shellprocess_initialize_pacman.conf:36`, `*_mk_hook.conf`, `*_enable_ufw.conf` | jargon job names | exec status | Yes | A | P2 |
| `packaging/PKGBUILD:141-147` `SKIP_MODULES` | upstream | build only | No | C | — |
| Kedra-branded: `branding.desc`, `welcome.png`, `logo.png`, `icon.png` | generated wordmarks | Welcome, sidebar, window icon | Yes | **B** | P2 (dependency) |
| Installed system: GDM CachyOS logo (`cachyos-settings` override), Plymouth Arch watermark | documented in `README.kedra.md` | post-install | Yes | **B/E** (not installer) | out of scope |

---

## 11. Summary / Review Page

- **Paths**: `src/modules/summary/SummaryPage.cpp`, `SummaryModel.cpp:55-74`, `Config.cpp:33-60`; content is pulled from each `ViewStep::prettyStatus()` / `createSummaryWidget()`.
- **Sections shown (Observed `a/screen-21-summary.png`, `install-kedra/screen-09-summary.png`)**, in sidebar order: **Location** (“Set timezone to Europe/Istanbul.”, “The system language will be set to American English (United States).”, “The numbers and dates locale will be set to Türkçe (Türkiye).”), **Keyboard** (“Keyboard model has been set to Generic 105-key PC.”, “Keyboard layout has been set to English (US)/Default.”), **Partitions** (“**Erase** disk **/dev/vda** (vda) and install Kedra”, current/after bars, job list “Creating new GPT partition table…”, “Install Kedra on new btrfs system partition”).
- **Not shown**: Bootloader choice, Desktop choice, **Desktop Experience**, Packages, Users/hostname. Source-confirmed: only `LocaleViewStep`, `KeyboardViewStep`, `PartitionViewStep` override `prettyStatus`/`createSummaryWidget` (`grep` of `src/modules/*/*ViewStep.h`); `PackageChooserViewStep`, `NetInstallViewStep`, `UsersViewStep`, `KedraExperienceViewStep` do not. (`packagechooser/Config::prettyStatus()` exists — “Install option: **%1**” with the *id*, `Config.cpp:260` — but the view step never forwards it.)
- **Internal-ID leakage**: none currently (nothing Experience-related is shown).
- **Disk-destructive representation**: bold “Erase”, red/orange bars, explicit job list (good, upstream).
- **Confirmation**: Next is relabelled “Install” (`ViewManager.cpp:408`); clicking it opens “Continue with Installation?” (`:341-360`, **Observed** `a/screen-22-confirm.png`), “Install Now” starts execution. The dialog restates nothing but the disk warning.
- **Missing information the installer already knows**: `GS["kedraExperienceSelection"]` (base preset + deltas), the chooser selections (`GS["packagechooser_bootloader"]`, `GS["netinstallSelect"]`), the netinstall group set, `GS["username"]`/hostname/autologin.
- **Feasibility without duplicating resolver semantics**: an Experience summary can be built from data the page already holds — the chosen preset's resolver `name`, and for Custom the list of `KedraModule.name` with their override direction — by implementing `KedraExperienceViewStep::prettyStatus()`. No module list, dependency or package knowledge is needed. Showing resolved packages would require a pre-flight `resolve` call (consumption, not duplication) and is optional.

---

## 12. Installation Progress and Completion

**Progress (paths)**: `src/libcalamaresui/viewpages/ExecutionViewStep.cpp` (tab widget: slideshow | log; `QProgressBar` max 10000, format “%p%”; status `QLabel`; “Toggle log” tool button), `src/libcalamares/JobQueue.cpp:337-521` (weights), `src/branding/kedra/show.qml`.

- **Indicator**: percentage = cumulative job weight (`JobQueue.cpp:489-493`). Every job has weight 1 except partition/user sub-jobs (fractional) — no `weight:` in any online module's `module.desc` (only `unpackfs`, unused). **Observed** (final M1 log): 50 jobs / weight 37; `initialize pacman` 46 s, `pacstrap` 55 s, `packages` 74 s of a ~183 s run — i.e. ≈96 % of wall time between 13 % and 40 % of the bar; both acceptance screenshots show “10%” under the “initialize pacman…” line (`a/screen-23-installing.png`, `c/screen-c09-installing.png`). `pacstrap` additionally re-emits `setprogress(0)` every 0.5 s (`pacstrap/main.py:52-54`), so the bar is pinned at the job's start value for its whole duration. **The percentage suggests precision the backend does not provide.**
- **Primary text**: job `prettyStatusMessage()`; for `pacstrap` and `packages` this is raw command output (`pacstrap/main.py:49`, `packages/main.py:438-439`). Shellprocess jobs show their `i18n.name` (§9). Kedra jobs show “Resolving/Recording the Kedra Experience profile.”
- **Secondary/log**: hidden log tab toggled by an unlabelled icon button (`ExecutionViewStep.cpp:97-104`; action text “Toggle log” not translated).
- **Slideshow**: 4 text slides, 20 s timer, dark `#1D2126` background, title `#5AA9E6`, Cantarell (`show.qml:25-49`). Strings untranslated by design. **Observed**.
- **Artwork**: none (deliberate). CachyOS remnants: slide 2 text (§10). Kedra wording: slides 1, 3, 4.
- **Frozen appearance risk**: yes — during `pacstrap` the bar does not move and the status line changes only as pacman prints; during mirror ranking (46 s) the line is static. Cancel stays enabled (`disable-cancel-during-exec: false`, `settings_online.conf:138`; **Observed** button present).
- **Failure transition**: first failing job → remaining non-emergency jobs skipped → `ErrorDialog` (“Installation Failed”, message, Details, optional paste — disabled: no `uploadServer`) → Calamares **exits when the dialog closes** (`ViewManager.cpp:171-186`) — the user never reaches the Finished page in the failure case in practice (**Inferred** from code; the Finished page's failure text exists at `FinishedPage.cpp:120-125` but is preceded by the dialog whose close quits).

Ownership: progress hierarchy, status copy, job names, weights (settable per `module.desc`/config downstream) — **Installer Polish**; slideshow artwork/palette — **Branding Integration**; slideshow *copy* — Installer Polish until artwork replaces it.

**Completion (Observed `a/screen-24-finish.png`)**: “All done.” heading, two-line body naming Kedra Linux twice, unchecked “Restart now” (`finished.conf:2` `user-unchecked`), “Done” button; Back/Next disabled. Reboot: `systemctl -i reboot` when checked. No shutdown/close variant. No help/support reference. No CachyOS text. Success is unmistakable; the page is otherwise empty (no “what's next”, no summary of what was installed). Owner: A (copy/next-steps), B (artwork). P3.

---

## 13. Error and Validation Presentation

| Path | Trigger | Source | User-visible text (representative) | Navigation | Fail-closed | Recovery | Technical detail | Jargon leak |
|---|---|---|---|---|---|---|---|---|
| Resolver missing | `/usr/bin/kedra-experience` absent at start-up | `KedraExperienceViewStep.cpp:75-84` | “The Kedra Experience resolver was not found at /usr/bin/kedra-experience. This program is installed by the kedra-experience-runtime package; the installation medium appears to be incomplete.” | Next disabled on page 7; user discovers it **after pages 1–6** | yes | none (restart installer) | log only | yes (resolver, package name) |
| Resolver timeout / non-zero | 60 s / exit≠0 | `:86-105` | “…did not respond…” / “The available desktop profiles could not be listed.\n\n<stderr>” | same | yes | none | stderr inline | raw stderr |
| Invalid JSON / wrong version / no presets / none selectable / basePreset missing / no selectable modules | metadata shape | `:107-132`, `KedraExperiencePage.cpp:59-65, 101-106, 116-124, 158-167` | see §9 | same | yes | none | — | config-key names |
| Experience page validation | none possible: a radio is always checked once ready; `isNextEnabled()` = loaded && ready && non-empty selection | `KedraExperienceViewStep.cpp:138-145` | — | Next enabled whenever ready | n/a | n/a | — | — |
| Invalid Custom combination | resolver exit 2 / `diagnostics.ok=false` at **exec time** (after partitioning + pacstrap) | `kedraexperiencepackages/main.py:276-287` | “Kedra Experience profile is not valid — The selected combination of Kedra Experience modules was rejected and the installation cannot continue.<br/><br/>{messages}” | run aborted; error dialog; app exits | yes | none | dialog Details | mild |
| Resolver failure at exec | missing/timeout/exit 1/bad JSON/schema/malformed | `main.py:149-337` | titles in §9; bodies include `<pre>stderr</pre>` or “diagnostics.ok is not a boolean” | aborted | yes | none | inline | yes |
| State recording failure | `install-state` failure or `written ≠ applies` | `kedraexperiencestate/main.py:157-281` | “Kedra Experience profile not recorded — Recording the chosen profile for **user** failed … <pre>stderr</pre>” | aborted (before `kedra_verify`) | yes | none | inline | mild |
| Target verification failure | any `bad` in `kedra-verify-target` | `shellprocess_kedra_verify.conf` (`verbose: true`) | “External command finished with errors. — Command <i>/etc/calamares/scripts/kedra-verify-target</i> finished with exit code 1.\nOutput:\n…” (`System.cpp:317-355`) | aborted | yes | none | full script output | yes (raw) |
| Package manager failure | pacman error | `packages/main.py:779-820` | “Package Manager error — The package manager could not make changes to the installed system. The command <pre>…</pre> returned error code N.” | aborted | yes | none | inline | raw |
| Welcome requirements | RAM < 2.5 GiB or no internet | `checker/`, `welcome/Config.cpp:55-70` | “This computer does not satisfy the minimum requirements for installing Kedra. Installation cannot continue.” + list | Next disabled; auto-recheck (`ResultsListWidget.cpp:47`) | yes | re-check | — | no |
| Users validation | short password etc. | upstream (**Observed** “Password is too short”) | inline icon + text | Next disabled | yes | fix field | — | no |
| Generic Calamares init failure | module load failure | `ViewManager.cpp:190-215` | “Calamares Initialization Failed — Kedra Linux can not be installed. Calamares was unable to load all of the configured modules…” | blank step | yes | none | list | “Calamares” naming (valid upstream) |

Frozen fail-closed semantics are intact everywhere. UX quality issues: the start-up-time resolver failure is surfaced late (page 7) with no retry; exec-time Experience failures happen after the disk is erased (unavoidable for resolver-side failures, but UI-reachable invalid combinations are **none** on the frozen baseline — **Observed** 64/64 OK); primary error text carries raw implementation language throughout.

---

## 14. Back / Next Navigation and State

Standard Calamares semantics (`ViewManager::next()/back()`, `ViewManager.cpp:322-401, 460-494`): `onLeave()` is called when leaving a page in **either** direction; `onActivate()` on entry; Back is disabled only on page 1; Next is driven by each step's `isNextEnabled()` plus `nextStatusChanged`.

| Page | Back | Next | Validation moment | Explains a block? | State survives Back/Next |
|---|---|---|---|---|---|
| Welcome | disabled | mandatory requirements met | on entry, re-checked | yes (list) | yes |
| Location / Keyboard | yes | yes | — | — | yes (upstream) |
| Bootloader / Desktop | yes | `mode: required` → an item must be selected (default present) | on change | implicit | yes; GS rewritten on `onLeave` (`packagechooser/Config.cpp:169-198` prunes previous selection) |
| Partitions | yes | valid partition plan | on change | upstream messages | yes |
| Desktop Experience | yes (`isBackEnabled` true) | `m_loaded && ready && !selection.isEmpty()` | at start-up (metadata) | error label only — no message near Next | **yes** — widget persists, no `onActivate` override |
| Packages | yes | `required: true` → ok | — | — | yes; `packageOperations` rewritten on leave |
| Users | yes | all fields valid | on edit | inline | yes |
| Summary | yes | always (“Install”) | — | — | regenerated on activate |
| Install | hidden | hidden | — | — | — |
| Finish | disabled | disabled (“Done”) | — | — | — |

Experience scenarios (**static-analysis evidence**; the M1 rerun checks `kedra-iso/tests/kedra-installer-rerun-checks.sh` — “PASS (22 checks)” in the manifest — cover the job-level re-run, not the widget):

1. *Vanilla → Next → Back*: `onLeave` publishes `{"vanilla",[]}`; Back from Packages runs netinstall's `onLeave`, then our default `onActivate` (no-op); radio still Vanilla; Next enabled. ✔
2. *Kedra → Next → Back*: same with `{"kedra",[]}`. ✔ (also re-publishes on the Back-leave of page 7 itself)
3. *Custom + changes → Next → Back*: deltas published; checkboxes retained; group visible again. ✔ No visual indication that overrides are pending.
4. *Custom → Kedra → Custom*: only `m_moduleArea` visibility changes; previous checkbox state returns intact (`KedraExperiencePage.cpp:199-204`; no reset path). ✔ (design question: silent retention vs reset)
5. *Custom → Vanilla → Custom*: identical to 4. ✔
6. *Resolver failure entering/re-entering*: profiles are loaded once at start-up; failure state is permanent for the process; re-entry does not retry (`loadProfiles` is only called from `widget()` first construction). Fail-closed ✔, recoverability ✘.
7. *Invalid state on Next*: not reachable at the page (always one radio checked when ready). Exec-time invalidity → abort (§13).

No unexpected state is recreated on activation. The page does not use `ensureSize`. Downstream pages use standard Calamares navigation.

---

## 15. Layout / Responsive Risks

Geometry facts (source-confirmed): `branding.desc:25` `windowSize: 1100px,520px`, `windowExpanding: normal`, `sidebar: qml,bottom` (38 px, `calamares-sidebar.qml:25`), `navigation: widget`; `Gui.h:95-98` minimum 800×520, preferred 1024×520; `CalamaresWindow.cpp:447-458` clamps the window to `[min, available]`. **Observed** (final M1 log `:71-72`): available 1280×768 → minimum 1024×520, proposed 1100×520; the Summary page grew the window taller via `ensureSize` (**Observed** `a/screen-21-summary.png` vs `a/screen-14`).

| Resolution | Evidence | Assessment |
|---|---|---|
| 1920×1080 | Inferred from clamps | window 1100×520 centred; Experience page shows three radios in the top-left of a mostly empty 1100×~400 area; chooser frames scale up (`FixedAspectRatioLabel`); no clipping. Hierarchy problem, not a layout failure. |
| 1280×720 | closest Observed case (1280×800) | window 1100×520 fits (available ≈ 1280×688 with GNOME top bar); all pages rendered without clipping in the acceptance runs. |
| 1024×768 | Inferred | minimum 1024×520; branding width 1100 is clamped to 1024; the QML sidebar's 12 labels at ~85 px each already wrap “Desktop Experience” at 1100 px (**Observed**) and will wrap more at 1024; Welcome's requirement list + wordmark fit (wordmark scales, `welcomeExpandingLogo: true`). Partition and netinstall pages scroll internally (upstream). |

Code-level risks:

- **Experience page has no scroll area** (`KedraExperiencePage.cpp:22-42`); the page's minimum height grows linearly with the module count (~24 px per box). With 6 modules it is ~300 px — fine at 520. At roughly 12+ selectable modules on a 520 px-high window the `QStackedWidget`'s minimum size would exceed the window, and Qt would enlarge the main window beyond the screen, pushing the navigation footer off-screen — **code-level risk, not currently reachable** (6 items on the frozen baseline).
- **Error text**: `m_status` is word-wrapped plain text; long resolver stderr expands the label height with the same no-scroll consequence — code-level risk for very long stderr.
- Chooser pages: the placeholder image dominates ~55% of the width; the description label wraps below (Observed); fine at all three sizes.
- Netinstall: `QTreeView` scrolls; descriptions elide at the column edge (Observed “…”).
- Nested scroll areas: none on Kedra pages.
- Navigation footer: `QDialogButtonBox`-style widget navigation; never hidden except during exec (`hide-back-and-next-during-exec: true`).

No screenshot/UI test infrastructure exists in this repository (only the Python job suite); the ISO repo's VM tooling (`kedra-iso/tests/kedra-vmctl`) captures screendumps but is not a UI assertion framework. Not built during IP0.

---

## 16. Accessibility Risks

All items below are **Source-confirmed** from widget choice plus Qt default behaviour; **runtime keyboard/screen-reader validation is still required** (nothing in the repository tests it).

- **Widgets are native Qt** (`QRadioButton`, `QCheckBox`, `QGroupBox`, `QLabel`): radio/checkbox semantics, Space activation, arrow-key movement within the exclusive group, native focus rectangles and accessible names (= button text) come for free. No custom clickable containers exist on the Kedra page.
- **Descriptions are tooltips only**: unreachable by keyboard for sighted users; exposed to AT only as `QAccessible::Description` (Qt maps `toolTip` there — **Inferred** from `QAccessibleWidget`), not as visible text.
- **Recommended/selected state**: selected = native indicator (not colour-only) ✔; “recommended” is not conveyed at all ✘.
- **Error association**: the error label is not focusable and not programmatically associated with the disabled Next; a screen-reader user landing on the page hears nothing unless they navigate to the label.
- **Focus on page entry**: `QStackedWidget` moves focus into the new page's tab chain when the old page had focus (**Inferred**); the Welcome page explicitly focuses the language combo (`WelcomePage.cpp:174-182`). No explicit focus management on the Kedra page.
- **Disabled controls**: none on the Kedra page; Next is disabled without an adjacent reason.
- **Sidebar**: the QML bottom bar is purely visual (`Rectangle`/`Text`, no `Accessible` properties, no `MouseArea`); it is neither focusable nor clickable — acceptable as a progress indicator (upstream/branding choice), but AT users get no step announcement from it.
- **Scrollable Custom list**: there is no scroll area, so keyboard scrolling is moot today; if one is added later it must keep focus-follows-scroll.
- **Progress page**: the log toggle is an icon-only `QToolButton` with action text “Toggle log” (accessible name present, no visible label, untranslated).
- **Chooser pages**: `QListView` single selection — arrow keys work; the description label is not associated with the list item.
- **Netinstall**: `QTreeView` with checkable items — keyboard-usable; tooltips carry descriptions in addition to the column.

---

## 17. Visual Hierarchy

Structural observations only (no colour/typography proposals):

- **Headings**: Welcome and Finish have `<h1>` headings; Bootloader, Partitions, Desktop, Desktop Experience, Users and Summary have **no page heading** — the only orientation is the sidebar highlight. The Experience page's lead sentence is set in body size.
- **Primary vs secondary**: the three products are peers; the recommended one is not distinguished; Custom's disclosure is a group box appearing below the third radio (progressive disclosure exists but with a jargon title and no explanatory line).
- **Density/spacing**: Experience page uses default layout margins; content occupies the top ~30 % of the page (Observed); chooser pages give ~55 % width to an empty image frame; netinstall is dense but consistent (upstream).
- **Grouping/separators**: only the `QGroupBox` frame on the Experience page; no cards.
- **Validation/warning hierarchy**: page errors replace the whole content (good), but with no title/emphasis distinction.
- **Main content ↔ navigation**: footer buttons are consistently bottom-right; the bottom step bar competes with them visually at 1100 px because of two-line wrapping.
- **Summary hierarchy**: section titles (step names) + indented body; consistent, but incomplete content (§11).
- **Progress hierarchy**: slideshow dominates; the bar and one status line sit beneath; the status line is the technical text rather than a human phase name — the hierarchy is inverted relative to what a user needs (phase → detail).

---

## 18. Branding Integration Boundary

| Surface | Current source | Current state | Installer Polish responsibility | Branding Integration dependency |
|---|---|---|---|---|
| Window/app icon | `branding.desc:53` `productIcon: icon.png` (128², generated) | placeholder | none | final icon |
| Sidebar logo | `branding.desc:52` `logo.png` 64², drawn 30×30 by `calamares-sidebar.qml:32-42` | placeholder “K” | slot size, position, sidebar layout | final logo variant for dark bar |
| Welcome image | `branding.desc:54` `welcome.png` 900×516; `welcomeExpandingLogo: true` | wordmark | slot dimensions, page layout | final artwork |
| Product strings | `branding.desc:33-49` | final names (“Kedra Linux”, “Kedra”, `bootloaderEntryName: Kedra`) | wording that surrounds them | none |
| Sidebar palette | `branding.desc:56-60` (`#1D2126`, `#5AA9E6`, …) | ad-hoc placeholder colours | which tokens are consumed where | official palette/tokens |
| Stylesheet | `src/branding/kedra/stylesheet.qss` (upstream sample; font 13 px) | placeholder | structural rules (spacing, widget classes) | colours/typography assets |
| Slideshow | `show.qml` text-only, 4 slides | placeholder | slide copy, timing, fallback behaviour when no artwork | slide artwork |
| Desktop chooser images | `packagechooser_desktop.conf` (`screenshot:` removed); fallback `:/images/no-selection.png` | generic placeholder | layout without images; whether to show a frame at all | Kedra desktop screenshots |
| Bootloader chooser images | `packagechooser_bootloader.conf` (removed) | generic placeholder | same | boot-menu screenshots (or none) |
| Progress bar / log button | `ExecutionViewStep.cpp` | Qt defaults | hierarchy, copy, weights | palette |
| Finish page | `FinishedPage.cpp` | text only | copy, next-steps content | artwork if any |
| Installed-system identity (GDM logo, Plymouth watermark, GRUB) | documented in `README.kedra.md`; `bootloaderEntryName` | CachyOS logo / Arch watermark | none | system-level assets (out of installer scope) |

No temporary/fake assets were created.

---

## 19. M1 / Resolver Architecture Compliance

| Component | Consumes resolver metadata | Presents only | Stores permitted choices | Duplicates authority | Hard-codes semantics | Classification |
|---|---|---|---|---|---|---|
| `kedraexperience` view (`KedraExperiencePage.cpp`, `KedraExperienceViewStep.cpp`) | yes (`profiles`, version-pinned) | yes (names, descriptions, `enabled`, `user_selectable`) | yes (`base_preset` + deltas) | no | `basePreset` pointer, `"Custom Kedra Experience"`, `"Modules"` | **compliant** (presentation constants harmless) |
| `kedraexperience.conf` | — | — | — | no | resolver path, `basePreset`, schema pin | compliant |
| `kedraexperiencepackages` job | yes (`resolve`) | — | writes `packageOperations`, resolved selection, layer flag | no (installs `packages.required` verbatim; no filtering) | none | **compliant** |
| `kedraexperiencestate` job | yes (`install-state`) | — | delegates persistence | no | none (format/path owned by runtime) | **compliant** |
| `settings_online.conf` sequence | — | — | — | no | ordering comments only | compliant |
| `netinstall.yaml` GNOME group | no | — | — | package overlap with `packages.required` (gnome-shell, gnome-console, gnome-control-center) | package policy (base-system) | presentation/package-level duplication, **harmless** (documented in `main.py:359-362`) |
| `packagechooser_desktop.conf` | no | — | — | no | GNOME default (product policy) | compliant |
| `src/scripts/kedra-verify-target:178-273` | no | — | — | asserts expected shape: package names `kedra-experience-desktop/-workflow/-runtime/kedra-experience`, `~/.local/state/kedra/experience-state.toml`, `95_kedra-experience-*` GSettings glob, activation unit path, `base_preset` TOML key | yes (assertions) | **architecture smell / unclear — requires follow-up**: not an authority for installation, but a second description of Experience artefacts that can drift; contradicts `README.kedra.md`'s “no Kedra package name appears” claim |
| `README.kedra.md` | — | — | — | — | documentation | inconsistency with the script above |
| Page preselection (`basePreset`) vs resolver `default_preset: vanilla` | — | presentation default | — | arguably a second “default” | — | presentation-only, **harmless**, but document/decide |

No M1 boundary violation found. Nothing was refactored.

---

## 20. Existing Tests

Test locations (all in `tests/kedra-experience/`; runner `run-tests.sh`; harness `support.py` fakes `libcalamares` only and drives the **real** resolver from `kedra-packages` with `--data-dir`):

| Concern | Covered by | Notes |
|---|---|---|
| Experience metadata ingestion | `test_config.py` (resolver path, schema versions, basePreset present/selectable/applies layer/≥1 selectable module; non-selectable never offered) | config-level, not the C++ page |
| Profile selection Vanilla/Kedra/Custom semantics | `test_packages.py::VanillaTests/KedraTests/CustomTests` | job-level, against real resolver |
| `base_preset` | `test_config.py`, `test_state.py::CustomStateTests` | — |
| Explicit override persistence (deltas only) | `test_packages.py::CustomTests::test_the_deltas_are_recorded_for_persistence`, `test_state.py::CustomStateTests` | — |
| `user_selectable` | `test_config.py::test_non_selectable_modules_are_never_offered` (data-level only; the page's filtering code is untested) | — |
| Malformed metadata / resolver failure / fail-closed | `test_packages.py::ResolverFailureTests/InvalidSelectionTests`, `test_validation.py` (exact JSON types), `test_state.py::StateFailureTests` | thorough |
| Re-run reconciliation | `test_rerun.py` | thorough |
| Package selection interaction | `test_packages.py::test_an_existing_gnome_group_is_left_untouched`, `…appends_rather_than_replacing…` | — |
| Summary generation | **none** | — |
| Page navigation / state restoration | **none** | — |
| UI behaviour (page build, error states, selection document from widget state) | **none** (no C++ test for `kedraexperience`; upstream `Tests.cpp` exist for other modules but need a build) | — |
| Accessibility / layout / resolution | **none** | — |
| Installation execution integration | `kedra-iso` acceptance (VM) — recorded in the manifest, not in this repo | — |

Execution:

| Command | Working directory | Result | Tests | Failures/skips | Repo state modified? |
|---|---|---|---|---|---|
| `./run-tests.sh` (→ `python3 -m unittest discover -s . -p 'test_*.py'`, `KEDRA_PACKAGES=/home/uras/Projects/kedra-packages`) | `/home/uras/Projects/kedra/kedra-calamares/tests/kedra-experience` | **OK** in 9.8 s | **129** | 0 / 0 | No (`git status --short` empty afterwards; `__pycache__` is git-ignored) |

Matches the manifest's `kedra_calamares_experience_suite: PASS (129 tests)`. Upstream C++/ctest suites were not run (require a full build; not present on this host). No tests were added or changed.

**Important UX behaviour lacking automated coverage**: page construction from a `profiles` document (order, filtering, defaults), the selection document produced from widget state (Custom deltas), every `setError` branch, Next-enable logic, state retention across mode switches, Summary content, keyboard reachability, and layout at the three target resolutions.

---

## 21. Consolidated Findings

| ID | Priority | Surface | Finding | Evidence | Owner |
|---|---|---|---|---|---|
| F-01 | P1 | Flow (pages 6–8) | Desktop chosen twice (chooser + netinstall checkboxes with internal ids); can contradict the Experience choice; visible “CachyOS performance and system defaults” group; “don't change unless you know…” copy | Observed `a/screen-17-packages.png`; `netinstall.yaml:44,77,223-841`; `packagechooser/Config.cpp:169-198` | Installer Polish (presentation/ordering); base-system (group content) |
| F-02 | P1 | Experience page visibility | Page shown for every desktop incl. Plasma/No Desktop; Kedra Experience selectable there → GNOME packages + profile on a non-GNOME target | `settings_online.conf:70-74` static sequence; `KedraExperienceViewStep` has no gating | Installer Polish (flow) — rule wording needs Experience/Resolver decision |
| F-03 | P1 | Summary / confirm | No Experience, desktop, bootloader, packages or user rows; confirm dialog restates nothing | Observed `a/screen-21`, `a/screen-22`; `SummaryModel.cpp:61-72`; only locale/keyboard/partition override `prettyStatus` | Installer Polish (IP3) |
| F-04 | P1 | Experience page | Custom mental model unclear: “Modules” title, descriptions tooltip-only, no “Recommended”, inaccurate Custom tooltip, no visible “Kedra + changes” statement | `KedraExperiencePage.cpp:36,91,151,169-171,192`; Observed `a/screen-15` | Installer Polish (IP2) |
| F-05 | P2 | Progress | Raw pacman/shell text as primary status; uniform weights → bar misrepresents progress (≈96 % of time within 13–40 %); pinned during pacstrap; log toggle unlabeled | `pacstrap/main.py:49-54`; `packages/main.py:438`; `JobQueue.cpp:489-493`; final M1 log `:514-565` timings; Observed screenshots “10%” | Installer Polish (copy/weights/hierarchy) |
| F-06 | P2 | Experience page errors | Resolver failure detected at start-up but surfaced only on page 7; no retry; Next blocked without adjacent reason | `ViewManager.cpp:134-149`; `KedraExperienceViewStep.cpp:53-66,68-136` | Installer Polish (IP2/IP3) |
| F-07 | P2 | Experience page | Resolver advisories (e.g. workflow-without-desktop) never shown; `tier` unused | Observed resolve output; `main.py:263-266` | Installer Polish (consume, don't duplicate) |
| F-08 | P2 | Experience page | Custom state silently retained across mode switches; no reset-to-Kedra-defaults; no indication of pending overrides | `KedraExperiencePage.cpp:199-204` | Installer Polish (IP2) |
| F-09 | P2 | Chooser pages | Bootloader/Desktop pages have no heading; generic placeholder icon fills ~55 % of the page for every item | `PackageChooserPage.cpp:76-79`; Observed `a/screen-10`, `a/screen-13` | Installer Polish (layout) + Branding (assets) |
| F-10 | P2 | Welcome | GeoIP auto-selects a non-English UI (Observed `tr_TR`) despite English-first; third-party lookup; English sidebar under Turkish UI | `welcome_online.conf:93-96`; log `:218-220` | Uncertain / ownership decision (Installer Polish config vs product policy) |
| F-11 | P2 | Exec status copy | Jargon job names (“initialize pacman … ranking mirrors …”, “modify 90-mkinitcpio-install.hook…”, “Enable the UFW Firewall…”, “Configuring noctalia-greeter...”) | `shellprocess_*.conf` i18n names | Installer Polish (IP1) |
| F-12 | P2 | Slideshow | “Built on CachyOS” slide and “Early days/placeholder” slide during install | `show.qml:52-85`; Observed | Installer Polish (copy) + Branding (artwork) |
| F-13 | P2 | Installed system | Snapper snapshot described “Fresh CachyOS Installation”; `/etc/cachyos/installation-snapshot` | `src/scripts/btrfs-installation-snapshot:13-17` | Dead/obsolete downstream (IP5) with base-system confirmation |
| F-14 | P2 | Exec | Cancel enabled during execution (inherited config) | `settings_online.conf:138`; Observed | Installer Polish / product decision (upstream-valid) |
| F-15 | P2 | Architecture hygiene | `kedra-verify-target` hard-codes Experience package names, state path, override glob, unit path; README claims otherwise | `kedra-verify-target:178-273`; `README.kedra.md` “Grep for kedra-experience-workflow…” | Unclear / requires follow-up (Experience–installer integration, not Installer Polish) |
| F-16 | P2 | IA | Step names “Desktop” / “Desktop Experience” / “Packages” do not express their relationship; “Desktop Experience” wraps | `KedraExperienceViewStep.cpp:50`; conf labels; Observed | Installer Polish (IP1) |
| F-17 | P3 | Layout | No scroll area on the Experience page; overflow risk if module count grows (not reachable with 6) | `KedraExperiencePage.cpp:22-42` | Installer Polish (IP4) |
| F-18 | P3 | Error copy | Field names/raw stderr in primary error text (`diagnostics.ok is not a boolean`, `<pre>`) | `main.py:141-146,258-262` etc. | Installer Polish (IP1, wording only; keep semantics) |
| F-19 | P3 | Finish | Empty page beyond “All done.”; Restart unchecked; no next steps | `FinishedPage.cpp:91-96`; `finished.conf` | Installer Polish (IP3) |
| F-20 | P3 | i18n | Kedra page/step strings untranslated; slideshow deliberately untranslated | no `lang/*.ts` entries | Installer Polish (deferred; English-first) |
| F-21 | P3 | Dead material | sample header in `calamares-sidebar.qml`, unused `lang/calamares-default_*.ts`, limine `cachyos-wallpapers` splash path, `bootloader-post-setup` CachyOS name (limine only) | §10 | Downstream cleanup (IP5) |
| F-22 | P3 | Defaults | Page preselects `basePreset` while resolver says `default_preset: vanilla` — two notions of “default” | `kedraexperience.conf:10-13`; Observed profiles doc | Document / decide (Experience + Installer Polish) |
| F-23 | P3 | Tests | No automated coverage of the page, summary, navigation, a11y or layout | §20 | Installer Polish (IP6/IP8) |

---

## 22. Installer Polish Ownership Map

- **Installer Polish**: page headings and step names; all installer-authored copy (Experience page, job names, error wording — without weakening semantics); Experience page structure (visible descriptions, recommended marker, Custom disclosure, reset, advisories display, error/retry presentation); Summary rows for Experience/desktop/bootloader/packages/users; confirm-dialog content; netinstall *display* names/descriptions and whether desktop groups are re-shown; chooser page layout when no images exist; progress hierarchy, status copy and job weights; slideshow copy until artwork lands; Finish page copy; layout/scroll/responsive behaviour; accessibility of installer-owned widgets; GeoIP/language policy *as installer configuration* (decision needed).
- **Branding Integration**: logo/icon/welcome image, palette/tokens, stylesheet colours/typography, slideshow artwork, chooser screenshots, installed-system identity (GDM/Plymouth/GRUB assets).
- **Experience / Resolver** (`kedra-packages`): preset/module names, descriptions, `user_selectable`, `enabled` defaults, tiers, advisories, package requirements, GSettings, application contracts, state-file format, `default_preset`.
- **Appearance / Extensions**: module semantics behind “Kedra Appearance” / “GNOME Extensions” (resolver-owned modules; nothing for the installer).
- **Package / base-system**: contents of `netinstall.yaml` groups, `pacstrap.conf`, GNOME minimum set, kernels, CachyOS layer, snapshot/snapper behaviour (F-13 content decision).
- **Upstream Calamares retained**: Welcome/Locale/Keyboard/Partition/Users/Summary/Finished pages, navigation semantics, error dialog, requirements checker, progress mechanics, generic strings.

---

## 23. Recommended Staged Milestone Plan

Derived from the findings; ordering kept as planned except that IP1 is scoped to *strings and configuration only* so it can land without touching widget structure, and IP2/IP3 are explicitly sequenced after the two ownership decisions they depend on (F-02 gating rule, F-01 netinstall desktop re-listing).

**IP1 — Information Architecture & Product Copy**
- Objective: every installer-authored string a normal user sees is product language; step names express the flow.
- Scope: sidebar step names (`KedraExperienceViewStep::prettyName`, `packagechooser_*.conf labels.step`, `netinstall.conf label.*`); Experience lead sentence, group title, Custom tooltip text; shellprocess `i18n.name` values (F-11); netinstall visible group `name`/`description` display wording (F-01 wording part; no package changes); slideshow copy (F-12 text); Finish copy (F-19); error-string wording (F-18) without changing conditions.
- Likely files: `src/modules/kedraexperience/*.cpp`, `src/modules/shellprocess/*.conf`, `src/modules/netinstall/netinstall.{conf,yaml}` (names/descriptions only), `src/modules/packagechooser/*.conf` (labels), `src/branding/kedra/show.qml`.
- Dependencies: none.
- Non-goals: widget changes, visibility rules, package policy, assets, translations.
- Acceptance: string inventory (§9) re-audited with zero jargon/CachyOS items on the normal path except deliberate provenance copy; 129 tests still pass; `git diff` touches no `.py` logic and no resolver interaction.

**IP2 — Experience Selection UX**
- Objective: Vanilla / Kedra / Custom read as the frozen mental models.
- Scope: page heading; visible preset descriptions (from resolver data); “Recommended” marker on the `basePreset` option; Custom disclosure with an explanatory line and visible module descriptions; inherited vs changed indication; “Reset to Kedra defaults”; optional `tier` grouping; pre-flight `resolve` to surface advisories (consumption only); retry affordance and adjacent “why Next is disabled” text for metadata failures (F-04, F-06, F-07, F-08).
- Likely files: `KedraExperiencePage.{h,cpp}`, `KedraExperienceViewStep.{h,cpp}`, `kedraexperience.conf` (presentation keys only).
- Dependencies: IP1 copy; decision on F-22 wording; any pre-flight resolve must reuse the job's validation rules or be presentation-only.
- Non-goals: any module/package/dependency knowledge in Calamares; changing the selection document; changing fail-closed behaviour.
- Acceptance: selection document unchanged for all 64 combinations (add a C++/Qt test for the page); descriptions visible without hover; keyboard walkthrough recorded.

**IP3 — Installer Flow & Review Coherence**
- Objective: the three package-ish pages and the Summary tell one story.
- Scope: `prettyStatus()`/summary widgets for Experience, desktop, bootloader, packages, users (F-03); confirm-dialog content; Experience page gating/explanation for non-GNOME desktops (F-02, after ownership decision); netinstall presentation of desktop groups (F-01, after decision); Cancel-during-exec policy (F-14).
- Likely files: `KedraExperienceViewStep.cpp`, `PackageChooserViewStep.cpp`, `NetInstallViewStep.cpp`, `UsersViewStep.cpp` (summary hooks), `settings_online.conf`, `netinstall.yaml` (hidden/selected flags only if agreed).
- Dependencies: decisions F-01/F-02 (Experience/Resolver + base-system); IP2 for Experience summary naming.
- Non-goals: package policy, resolver semantics duplication (summary uses names the page already holds).
- Acceptance: Summary shows every user choice; a Plasma+Kedra combination is either prevented or explicitly explained; VM acceptance re-run.

**IP4 — Layout & Visual Hierarchy**
- Objective: consistent structure at 1024×768 / 1280×720 / 1920×1080.
- Scope: headings on all pages; Experience page scroll container and spacing (F-17); chooser layout without images (F-09); progress hierarchy (phase line + detail line), per-module weights (F-05); sidebar label wrapping (F-16).
- Likely files: Kedra page, `page_package.ui`/`PackageChooserPage.cpp`, `module.desc` weights, `branding.desc` sizes, `stylesheet.qss` structural rules.
- Dependencies: IP2/IP3 content.
- Non-goals: colours, fonts, artwork.
- Acceptance: screenshots at the three resolutions, no clipped controls, footer always reachable.

**IP5 — Downstream Cleanup**
- Objective: remove dead/obsolete downstream material.
- Scope: F-13 (with base-system sign-off), F-21 items, README inconsistency (F-15 documentation side), unused branding `.ts` files, sample comments.
- Dependencies: none.
- Non-goals: anything user-visible that IP1–IP4 own; resolver/verify-script semantics.
- Acceptance: `git grep -i cachy` on the installer-visible surface returns only documented technical names.

**IP6 — Accessibility & Responsive Acceptance**
- Objective: manual + automated validation matrix.
- Scope: keyboard-only walkthrough, Orca spot-check, focus restoration, three resolutions; add page-level Qt tests.
- Dependencies: IP2–IP4.
- Acceptance: recorded checklist; no critical control unreachable.

**IP7 — Branding Integration Consumption**
- Objective: drop-in of official assets.
- Scope: swap `logo.png/icon.png/welcome.png`, slideshow artwork, chooser screenshots, palette tokens in `branding.desc`/`stylesheet.qss`.
- Dependencies: Branding Integration deliverables; IP4 slot constraints.
- Non-goals: creating assets here.

**IP8 — Full Installer Acceptance & Freeze**
- Objective: end-to-end VM acceptance for Vanilla/Kedra/Custom, re-run scenarios, screenshot set, tag.
- Dependencies: all above.

---

## 24. Recommended First Implementation Milestone

**IP1 — Information Architecture & Product Copy, strings-and-configuration only.** It is the smallest coherent step: it removes the visible jargon and CachyOS wording on the normal path (F-11, F-12 text, F-16, F-18 wording, part of F-01/F-04), touches no widget structure, no sequence, no package policy and no resolver interaction, is fully verifiable by re-running the §9 inventory plus the existing 129-test suite, and produces the vocabulary IP2/IP3 need. Nothing is implemented here.

---

## 25. Repository Integrity

Final state of `/home/uras/Projects/kedra/kedra-calamares` after the audit:

- Branch `kedra`, HEAD `3957a075080f065dae9bf1105eae0ec2f2d39bb7`, tag `m1` — unchanged.
- `git status --short` → only `?? IP0-installer-polish-audit.md` (this untracked report); `git diff` and `git diff --cached` are empty; no commits, tags, stashes, branch switches, pulls, resets or rebases were performed.
- Commands run were read-only (`git log/diff/status/grep/ls-files/describe`, `cat/sed/grep`, `python3 tools/kedra-experience profiles|resolve` from the `kedra-packages` checkout, and `tests/kedra-experience/run-tests.sh`, which writes only to temporary directories and the git-ignored `__pycache__`).
- Related checkouts (`kedra-iso`, `kedra-packages`) were only read.

IP0 changed nothing other than creating this optional audit report at `/home/uras/Projects/kedra/kedra-calamares/IP0-installer-polish-audit.md`.
