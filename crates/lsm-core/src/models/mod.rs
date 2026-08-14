//! Shared domain models.
//!
//! Everything in this module is plain data: no I/O, no GTK, no distribution
//! specifics. Backends produce these types and the UI renders them, which is
//! what keeps pacman logic out of the UI and lets a second distribution be
//! added without touching either.

pub mod boot;
pub mod change;
pub mod config;
pub mod dkms;
pub mod entity;
pub mod hardware;
pub mod journal;
pub mod kernel;
pub mod package;
pub mod process;
pub mod service;
pub mod snapshot;
pub mod storage;
pub mod subsystem;
pub mod system;
pub mod version;

pub use change::{
    ChangeCategory, ChangeEvent, ChangeEventId, ChangeMetadata, ChangeRelevance, ChangeSource,
    NewChangeEvent, PackageAction, PackageOrigin,
};
pub use entity::{EntityKind, EntityRef};
pub use subsystem::{Affinity, Subsystem, SubsystemTag, SubsystemTagger};
pub use version::{Version, VersionDelta};
