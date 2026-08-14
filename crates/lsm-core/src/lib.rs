//! Core of Linux System Manager.
//!
//! This crate holds everything that is not GTK and not privileged: the domain
//! models, the system backends, the SQLite history store and the change
//! tracking that powers **What Changed?**.
//!
//! # Layering
//!
//! ```text
//!   lsm-gui      GTK4/libadwaita. Renders models. No system parsing.
//!      │
//!   lsm-core     Domain models, backends, change tracking.   ← this crate
//!      │
//!   lsm-helper   Privileged actions only, behind a typed enum.
//! ```
//!
//! # Design invariants
//!
//! * **One history.** Every subsystem writes normalized `ChangeEvent`s to a
//!   single store. There is no private per-feature history to reconcile later.
//! * **No shell.** External programs are run through [`exec::CommandRunner`]
//!   with an argument vector; a command cannot be built from a string.
//! * **SQLite caches history, never current state.** The running kernel,
//!   installed packages and unit states are always read from the system.
//! * **Metadata, not contents.** The configuration tracker stores hashes and
//!   permissions; file contents are read only on explicit user request.

pub mod db;
pub mod error;
pub mod exec;
pub mod models;

pub use error::{Error, Result};
pub use models::{
    ChangeCategory, ChangeEvent, ChangeRelevance, ChangeSource, EntityRef, NewChangeEvent,
    Subsystem, SubsystemTagger, Version,
};
