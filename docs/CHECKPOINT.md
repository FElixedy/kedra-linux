# Checkpoint — Phase 1 (partial)

Session ended mid-Phase 1 by request. Everything below is verified: the
workspace compiles clean, `cargo clippy --all-targets` is silent, and **123
tests pass**.

## Completed

**Domain layer (`lsm-core::models`)** — the shared vocabulary every subsystem
speaks. Plain data: no I/O, no GTK, no distribution specifics.

| Module | Contents |
|---|---|
| `version` | `Version` + full pacman `vercmp` (epoch, release, `~` pre-release ordering) |
| `change` | `ChangeEvent`, `NewChangeEvent` + builder, sources, categories, relevance, typed `ChangeMetadata` payloads |
| `subsystem` | Rule-table subsystem tagger with `Core`/`Supporting`/`Contextual` affinity |
| `entity` | `EntityRef` / `EntityKind` — cross-collector object identity |
| `package` | Installed/remote packages, updates, `TransactionPreview`, cleanup targets, AUR helpers |
| `service` | Normalized `ActiveState` / `EnablementState`, service details, timers, autostart |
| `kernel` | Pattern-based kernel package detection, running-kernel parsing |
| `boot` | `BootTiming` from systemd monotonic timestamps, boot records, comparison |
| `config` | Tracked file state, `WatchSet`, pacnew/pacsave parsing |
| `dkms` | Entries, per-kernel grouping, running-kernel failure distinction |
| `snapshot` | `SystemSnapshot` + `diff_snapshots` (meaningful differences only) |
| `system` | Distro info, vitals, `RebootRequirement` with reasons |
| `journal`, `hardware`, `storage`, `process` | Supporting models |

**Execution (`lsm-core::exec`)** — `CommandRunner` trait, `RealRunner`
(tokio-process, streaming, timeouts), `FakeRunner` (fixture table + call
recording for argument-vector assertions).

**Storage (`lsm-core::db`)** — async `Db` wrapper (blocking pool + serialized
writes, WAL), append-only migration infrastructure, and the complete v1 schema:
operations, operation log, change events with provenance/entities/subsystems,
snapshots and their five child tables, live config state, boot records, boot
unit times, collector cursors. Indexed for the time-range queries What Changed?
actually makes.

**Errors** — structured `Error` enum covering every case in the spec, with
`user_message()` / `technical_detail()` split so raw panics never reach the UI.

## Partially implemented

- **Phase 1 itself.** Foundation types are done; `ChangeStore`, distro
  detection, the operation engine and the GTK shell are not written.
- **SMART health** — `SmartHealth` type and the `Disk::smart` field exist so the
  storage page and snapshot format will not change shape later. No collection.
- **`RebootRequirement`** — model and reason types complete; no detector.

## Not started

Phases 2–10 in full: package backend, privileged helper + polkit policy,
operation engine, systemd/journal integration, kernel/driver/DKMS collection,
config tracker, boot analyzer, correlation engine, reports, AI layer, and the
entire GTK4 front end. `lsm-helper` and `lsm-gui` are intentionally absent from
the workspace rather than present as empty stubs.

## Architectural decisions worth carrying forward

1. **Three crates, not one.** `lsm-core` (no GTK, no privilege) / `lsm-helper`
   (privileged, typed actions only) / `lsm-gui`. The privilege boundary is a
   compilation boundary, so the GUI *cannot* call a privileged path directly.

2. **Boot timing from D-Bus, not `systemd-analyze`.** `--json` is unsupported
   for `time`/`blame` on current systemd and the text format is not a contract.
   `BootTiming::from_monotonic` derives phases from the manager's monotonic
   timestamp properties and reproduces `systemd-analyze time` exactly — verified
   in a test against real values from this machine (51.731s total).

3. **Dedup keys carry no timestamp.** The first design bucketed time into fixed
   60-second windows; a test caught that two sightings 30s apart split into two
   events when they straddled a boundary. The key is now pure semantic identity,
   and `ChangeStore` will match candidates sharing a key within a sliding
   `DEDUP_WINDOW_SECS` (300s). **This is a contract the store must honour** —
   the schema's `UNIQUE(dedup_key)` alone is not sufficient.

4. **Subsystem correlation is a rule table, not code.** `SubsystemTagger` maps
   packages/units/config paths/modules to subsystems via prefix/suffix/contains
   patterns, so one rule covers `mesa`, `lib32-mesa`, `mesa-git`. Broad platform
   components (kernel, firmware, systemd) tag as `Contextual` for *every*
   subsystem so they surface without outranking e.g. BlueZ for a Bluetooth
   fault.

5. **`PackageAction` is derived from versions, not from pacman's wording.**
   pacman logs a downgrade as `upgraded`; `PackageAction::from_versions` uses
   `vercmp` so downgrades — what users do to recover — are labelled correctly.

6. **Privacy is structural.** `WatchSet` has a `sensitive_fragments` list
   (`/etc/shadow`, host keys, NM connection profiles) whose contents are never
   hashed, while permissions stay tracked. Scope is `/etc` only.

7. **No `Command::from_string`.** Enforced by the type, tested by asserting a
   semicolon-bearing argument stays a single opaque argv entry.

## Recommended next task

**Implement `ChangeStore` in `lsm-core::changes::store`.** It is the keystone —
every collector in Phases 2–7 writes through it, so its interface should settle
before any collector exists.

Required behaviour:

- `insert(NewChangeEvent) -> ChangeEventId`, applying sliding-window dedup:
  match on `dedup_key` **and** `|timestamp - existing| <= DEDUP_WINDOW_SECS`.
- On a dedup hit, merge rather than duplicate: append to
  `change_event_sources`, and let the higher `provenance_rank()` source win for
  the primary `source`/`title`/`metadata` fields.
- Persist entities and precomputed subsystem tags to their child tables.
- `query(ChangeQuery)` with time range, category, relevance floor, subsystem and
  entity filters — served by the existing indexes.
- Collector cursor get/set against `collector_state`.

Test first against the merge semantics: insert the *same* kernel upgrade as
`Pacman` then as `OperationEngine`, and assert one row with two provenance
entries and the operation-engine metadata retained.

After that, Phase 1 finishes with distro detection, the operation engine, and
the GTK shell.
