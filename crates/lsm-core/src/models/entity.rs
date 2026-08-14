//! References to the things a change is *about*.
//!
//! An `EntityRef` is what lets the UI turn a change into a link ("bluetooth.service"
//! in a timeline entry navigates to the service page) and what lets the
//! correlation engine notice that two events from different collectors concern
//! the same object.

use std::fmt;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    Package,
    FlatpakRef,
    Unit,
    KernelPackage,
    KernelRelease,
    KernelModule,
    DkmsModule,
    ConfigFile,
    Device,
    Boot,
    Operation,
    Snapshot,
}

impl EntityKind {
    pub fn slug(&self) -> &'static str {
        match self {
            EntityKind::Package => "package",
            EntityKind::FlatpakRef => "flatpak",
            EntityKind::Unit => "unit",
            EntityKind::KernelPackage => "kernel_package",
            EntityKind::KernelRelease => "kernel_release",
            EntityKind::KernelModule => "kernel_module",
            EntityKind::DkmsModule => "dkms_module",
            EntityKind::ConfigFile => "config_file",
            EntityKind::Device => "device",
            EntityKind::Boot => "boot",
            EntityKind::Operation => "operation",
            EntityKind::Snapshot => "snapshot",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Some(match s {
            "package" => EntityKind::Package,
            "flatpak" => EntityKind::FlatpakRef,
            "unit" => EntityKind::Unit,
            "kernel_package" => EntityKind::KernelPackage,
            "kernel_release" => EntityKind::KernelRelease,
            "kernel_module" => EntityKind::KernelModule,
            "dkms_module" => EntityKind::DkmsModule,
            "config_file" => EntityKind::ConfigFile,
            "device" => EntityKind::Device,
            "boot" => EntityKind::Boot,
            "operation" => EntityKind::Operation,
            "snapshot" => EntityKind::Snapshot,
            _ => return None,
        })
    }
}

/// A stable reference to a system object.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct EntityRef {
    pub kind: EntityKind,
    /// Stable identifier, e.g. a package name or unit name. Case-sensitive as
    /// the system reports it.
    pub id: String,
}

impl EntityRef {
    pub fn new(kind: EntityKind, id: impl Into<String>) -> Self {
        EntityRef {
            kind,
            id: id.into(),
        }
    }

    pub fn package(name: impl Into<String>) -> Self {
        Self::new(EntityKind::Package, name)
    }

    pub fn unit(name: impl Into<String>) -> Self {
        Self::new(EntityKind::Unit, name)
    }

    pub fn config_file(path: impl Into<String>) -> Self {
        Self::new(EntityKind::ConfigFile, path)
    }

    pub fn kernel_release(rel: impl Into<String>) -> Self {
        Self::new(EntityKind::KernelRelease, rel)
    }

    pub fn dkms_module(name: impl Into<String>) -> Self {
        Self::new(EntityKind::DkmsModule, name)
    }

    pub fn boot(id: impl Into<String>) -> Self {
        Self::new(EntityKind::Boot, id)
    }
}

impl fmt::Display for EntityRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.kind.slug(), self.id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entity_kind_slugs_round_trip() {
        for kind in [
            EntityKind::Package,
            EntityKind::Unit,
            EntityKind::ConfigFile,
            EntityKind::DkmsModule,
            EntityKind::Boot,
            EntityKind::Snapshot,
        ] {
            assert_eq!(EntityKind::from_slug(kind.slug()), Some(kind));
        }
    }

    #[test]
    fn same_object_from_two_collectors_compares_equal() {
        assert_eq!(
            EntityRef::package("mesa"),
            EntityRef::new(EntityKind::Package, "mesa")
        );
        assert_ne!(EntityRef::package("mesa"), EntityRef::unit("mesa"));
    }
}
