//! Subsystem tagging.
//!
//! When a user says "Bluetooth broke", we need to decide which of the 300
//! packages that changed last night could plausibly be involved. That decision
//! is made here, once, as data — a rule table — rather than being scattered
//! through the UI or hardcoded as an endless list of package names.
//!
//! Rules match on *patterns* (prefix/suffix/substring) so that one rule covers
//! `mesa`, `lib32-mesa`, `mesa-utils` and `mesa-vdpau` at once. Each rule
//! carries an [`Affinity`] describing how strongly a match implicates the
//! subsystem, which the correlation engine turns into a ranking.

use std::fmt;

use serde::{Deserialize, Serialize};

/// A functional area of the system a user can point at when something breaks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Subsystem {
    Graphics,
    Networking,
    Bluetooth,
    Audio,
    Boot,
    Storage,
    Input,
    Printing,
    Power,
    Virtualization,
    Security,
    Display,
}

impl Subsystem {
    pub const ALL: &'static [Subsystem] = &[
        Subsystem::Graphics,
        Subsystem::Networking,
        Subsystem::Bluetooth,
        Subsystem::Audio,
        Subsystem::Boot,
        Subsystem::Storage,
        Subsystem::Input,
        Subsystem::Printing,
        Subsystem::Power,
        Subsystem::Virtualization,
        Subsystem::Security,
        Subsystem::Display,
    ];

    pub fn label(&self) -> &'static str {
        match self {
            Subsystem::Graphics => "Graphics",
            Subsystem::Networking => "Networking",
            Subsystem::Bluetooth => "Bluetooth",
            Subsystem::Audio => "Audio",
            Subsystem::Boot => "Boot",
            Subsystem::Storage => "Storage",
            Subsystem::Input => "Input devices",
            Subsystem::Printing => "Printing",
            Subsystem::Power => "Power management",
            Subsystem::Virtualization => "Virtualization",
            Subsystem::Security => "Security",
            Subsystem::Display => "Desktop session",
        }
    }

    pub fn slug(&self) -> &'static str {
        match self {
            Subsystem::Graphics => "graphics",
            Subsystem::Networking => "networking",
            Subsystem::Bluetooth => "bluetooth",
            Subsystem::Audio => "audio",
            Subsystem::Boot => "boot",
            Subsystem::Storage => "storage",
            Subsystem::Input => "input",
            Subsystem::Printing => "printing",
            Subsystem::Power => "power",
            Subsystem::Virtualization => "virtualization",
            Subsystem::Security => "security",
            Subsystem::Display => "display",
        }
    }

    pub fn from_slug(s: &str) -> Option<Self> {
        Self::ALL.iter().copied().find(|x| x.slug() == s)
    }

    pub fn icon_name(&self) -> &'static str {
        match self {
            Subsystem::Graphics => "video-display-symbolic",
            Subsystem::Networking => "network-wired-symbolic",
            Subsystem::Bluetooth => "bluetooth-symbolic",
            Subsystem::Audio => "audio-speakers-symbolic",
            Subsystem::Boot => "system-reboot-symbolic",
            Subsystem::Storage => "drive-harddisk-symbolic",
            Subsystem::Input => "input-keyboard-symbolic",
            Subsystem::Printing => "printer-symbolic",
            Subsystem::Power => "battery-symbolic",
            Subsystem::Virtualization => "computer-symbolic",
            Subsystem::Security => "security-high-symbolic",
            Subsystem::Display => "preferences-desktop-display-symbolic",
        }
    }
}

impl fmt::Display for Subsystem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.label())
    }
}

/// How strongly a match implicates the subsystem.
///
/// The distinction matters: a kernel update is *relevant* to a Bluetooth
/// problem, but a BlueZ update is *core* to it, and should rank above.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Affinity {
    /// Broad component that can affect many subsystems (e.g. the kernel).
    Contextual = 1,
    /// Supports the subsystem but is not the primary implementation.
    Supporting = 2,
    /// The subsystem's primary implementation.
    Core = 3,
}

/// What a rule inspects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Target {
    Package,
    Unit,
    ConfigPath,
    KernelModule,
}

/// How a rule matches its target's name.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pattern {
    Exact(&'static str),
    Prefix(&'static str),
    Suffix(&'static str),
    Contains(&'static str),
}

impl Pattern {
    fn matches(&self, name: &str) -> bool {
        match self {
            Pattern::Exact(p) => name == *p,
            Pattern::Prefix(p) => name.starts_with(p),
            Pattern::Suffix(p) => name.ends_with(p),
            Pattern::Contains(p) => name.contains(p),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Rule {
    pub subsystem: Subsystem,
    pub target: Target,
    pub pattern: Pattern,
    pub affinity: Affinity,
}

const fn rule(subsystem: Subsystem, target: Target, pattern: Pattern, affinity: Affinity) -> Rule {
    Rule {
        subsystem,
        target,
        pattern,
        affinity,
    }
}

/// A tag produced by the rule engine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubsystemTag {
    pub subsystem: Subsystem,
    pub affinity: Affinity,
}

/// The tagging engine. Construct with [`SubsystemTagger::with_defaults`] and
/// extend with [`SubsystemTagger::add_rule`] — nothing here is baked into the UI.
#[derive(Debug, Clone)]
pub struct SubsystemTagger {
    rules: Vec<Rule>,
}

impl Default for SubsystemTagger {
    fn default() -> Self {
        Self::with_defaults()
    }
}

impl SubsystemTagger {
    pub fn empty() -> Self {
        SubsystemTagger { rules: Vec::new() }
    }

    pub fn with_defaults() -> Self {
        SubsystemTagger {
            rules: DEFAULT_RULES.to_vec(),
        }
    }

    pub fn add_rule(&mut self, r: Rule) {
        self.rules.push(r);
    }

    pub fn rules(&self) -> &[Rule] {
        &self.rules
    }

    fn tag(&self, target: Target, name: &str) -> Vec<SubsystemTag> {
        let name = name.to_ascii_lowercase();
        let mut out: Vec<SubsystemTag> = Vec::new();
        for r in self.rules.iter().filter(|r| r.target == target) {
            if !r.pattern.matches(&name) {
                continue;
            }
            // Keep the strongest affinity per subsystem rather than duplicating.
            match out.iter_mut().find(|t| t.subsystem == r.subsystem) {
                Some(existing) => {
                    if r.affinity > existing.affinity {
                        existing.affinity = r.affinity;
                    }
                }
                None => out.push(SubsystemTag {
                    subsystem: r.subsystem,
                    affinity: r.affinity,
                }),
            }
        }
        out.sort_by(|a, b| {
            b.affinity
                .cmp(&a.affinity)
                .then(a.subsystem.cmp(&b.subsystem))
        });
        out
    }

    /// Tag a package name. Strips common architecture/variant decorations first
    /// so `lib32-mesa` and `mesa-git` both reach the `mesa` rules.
    pub fn tag_package(&self, name: &str) -> Vec<SubsystemTag> {
        let normalized = normalize_package_name(name);
        let mut tags = self.tag(Target::Package, &normalized);
        if normalized != name {
            for t in self.tag(Target::Package, name) {
                if !tags.iter().any(|x| x.subsystem == t.subsystem) {
                    tags.push(t);
                }
            }
        }
        tags
    }

    pub fn tag_unit(&self, unit: &str) -> Vec<SubsystemTag> {
        self.tag(Target::Unit, unit)
    }

    pub fn tag_config_path(&self, path: &str) -> Vec<SubsystemTag> {
        self.tag(Target::ConfigPath, path)
    }

    pub fn tag_kernel_module(&self, module: &str) -> Vec<SubsystemTag> {
        self.tag(Target::KernelModule, module)
    }
}

/// Strip decorations that would otherwise hide a package from its rules:
/// the `lib32-` multilib prefix and the `-git`/`-bin`/`-dkms` build suffixes.
pub fn normalize_package_name(name: &str) -> String {
    let mut n = name.to_ascii_lowercase();
    for p in ["lib32-", "mingw-w64-"] {
        if let Some(rest) = n.strip_prefix(p) {
            n = rest.to_string();
        }
    }
    for s in ["-git", "-bin", "-devel"] {
        if let Some(rest) = n.strip_suffix(s) {
            // Keep the stem only when something is left to match on.
            if !rest.is_empty() {
                n = rest.to_string();
            }
        }
    }
    n
}

use Affinity::{Core, Supporting};
use Pattern::{Contains, Exact, Prefix, Suffix};
use Subsystem::*;
use Target::{ConfigPath, KernelModule, Package, Unit};

/// The default rule table.
///
/// Ordering is irrelevant; the engine keeps the strongest affinity per
/// subsystem. Prefer patterns over exact names so that new packages in a family
/// are covered automatically.
static DEFAULT_RULES: &[Rule] = &[
    // ---- Graphics -------------------------------------------------------
    rule(Graphics, Package, Prefix("mesa"), Core),
    rule(Graphics, Package, Suffix("-mesa"), Core),
    rule(Graphics, Package, Contains("vulkan"), Core),
    rule(Graphics, Package, Prefix("nvidia"), Core),
    rule(Graphics, Package, Contains("-nvidia"), Core),
    rule(Graphics, Package, Prefix("xf86-video-"), Core),
    rule(Graphics, Package, Prefix("libva"), Supporting),
    rule(Graphics, Package, Prefix("libdrm"), Supporting),
    rule(Graphics, Package, Prefix("vdpau"), Supporting),
    rule(Graphics, Package, Prefix("libglvnd"), Supporting),
    rule(Graphics, Package, Contains("opencl"), Supporting),
    rule(Graphics, Package, Prefix("egl-"), Supporting),
    rule(Graphics, Package, Prefix("wayland"), Supporting),
    rule(Graphics, Package, Prefix("xorg-server"), Core),
    rule(Graphics, Package, Prefix("amdvlk"), Core),
    rule(Graphics, Package, Prefix("intel-media"), Core),
    rule(Graphics, Package, Prefix("vpl-gpu-rt"), Supporting),
    rule(Graphics, KernelModule, Exact("amdgpu"), Core),
    rule(Graphics, KernelModule, Exact("nvidia"), Core),
    rule(Graphics, KernelModule, Prefix("nvidia_"), Core),
    rule(Graphics, KernelModule, Exact("i915"), Core),
    rule(Graphics, KernelModule, Exact("xe"), Core),
    rule(Graphics, KernelModule, Exact("radeon"), Core),
    rule(Graphics, KernelModule, Exact("nouveau"), Core),
    rule(Graphics, ConfigPath, Contains("/x11/"), Supporting),
    rule(Graphics, ConfigPath, Contains("drirc"), Supporting),
    // ---- Display / session ---------------------------------------------
    rule(Display, Package, Prefix("gnome-shell"), Core),
    rule(Display, Package, Prefix("mutter"), Core),
    rule(Display, Package, Prefix("kwin"), Core),
    rule(Display, Package, Prefix("plasma"), Core),
    rule(Display, Package, Prefix("hyprland"), Core),
    rule(Display, Package, Prefix("sway"), Core),
    rule(Display, Package, Prefix("xdg-desktop-portal"), Supporting),
    rule(Display, Package, Prefix("gtk"), Supporting),
    rule(Display, Package, Prefix("qt6-"), Supporting),
    rule(Display, Unit, Prefix("gdm"), Core),
    rule(Display, Unit, Prefix("sddm"), Core),
    rule(Display, Unit, Prefix("lightdm"), Core),
    rule(Display, Unit, Prefix("display-manager"), Core),
    // ---- Networking -----------------------------------------------------
    rule(Networking, Package, Prefix("networkmanager"), Core),
    rule(Networking, Package, Prefix("iwd"), Core),
    rule(Networking, Package, Prefix("wpa_supplicant"), Core),
    rule(Networking, Package, Prefix("dhcpcd"), Core),
    rule(Networking, Package, Contains("systemd-resolvconf"), Core),
    rule(Networking, Package, Prefix("openresolv"), Supporting),
    rule(Networking, Package, Prefix("iproute2"), Supporting),
    rule(Networking, Package, Prefix("net-tools"), Supporting),
    rule(Networking, Package, Contains("wireguard"), Supporting),
    rule(Networking, Package, Prefix("openvpn"), Supporting),
    rule(Networking, Package, Prefix("modemmanager"), Supporting),
    rule(Networking, Unit, Prefix("networkmanager"), Core),
    rule(Networking, Unit, Prefix("systemd-networkd"), Core),
    rule(Networking, Unit, Prefix("systemd-resolved"), Core),
    rule(Networking, Unit, Prefix("iwd"), Core),
    rule(Networking, Unit, Prefix("dhcpcd"), Core),
    rule(Networking, Unit, Prefix("wpa_supplicant"), Core),
    rule(Networking, ConfigPath, Contains("/networkmanager/"), Core),
    rule(Networking, ConfigPath, Contains("/systemd/network"), Core),
    rule(Networking, ConfigPath, Contains("resolv.conf"), Core),
    rule(Networking, ConfigPath, Contains("/hosts"), Supporting),
    rule(Networking, KernelModule, Prefix("iwlwifi"), Core),
    rule(Networking, KernelModule, Prefix("ath"), Core),
    rule(Networking, KernelModule, Prefix("rtw"), Core),
    rule(Networking, KernelModule, Prefix("r8"), Supporting),
    rule(Networking, KernelModule, Prefix("e1000"), Supporting),
    // ---- Bluetooth ------------------------------------------------------
    rule(Bluetooth, Package, Prefix("bluez"), Core),
    rule(Bluetooth, Package, Contains("bluetooth"), Core),
    rule(Bluetooth, Package, Prefix("blueman"), Supporting),
    rule(Bluetooth, Unit, Prefix("bluetooth"), Core),
    rule(Bluetooth, KernelModule, Exact("btusb"), Core),
    rule(Bluetooth, KernelModule, Prefix("bt"), Supporting),
    rule(Bluetooth, KernelModule, Exact("bluetooth"), Core),
    rule(Bluetooth, ConfigPath, Contains("/bluetooth"), Core),
    // ---- Audio ----------------------------------------------------------
    rule(Audio, Package, Prefix("pipewire"), Core),
    rule(Audio, Package, Prefix("wireplumber"), Core),
    rule(Audio, Package, Prefix("pulseaudio"), Core),
    rule(Audio, Package, Prefix("alsa-"), Core),
    rule(Audio, Package, Prefix("sof-firmware"), Core),
    rule(Audio, Package, Prefix("jack"), Supporting),
    rule(Audio, Package, Prefix("libpulse"), Supporting),
    rule(Audio, Unit, Prefix("pipewire"), Core),
    rule(Audio, Unit, Prefix("wireplumber"), Core),
    rule(Audio, Unit, Prefix("pulseaudio"), Core),
    rule(Audio, KernelModule, Prefix("snd_"), Core),
    rule(Audio, KernelModule, Exact("snd"), Core),
    rule(Audio, ConfigPath, Contains("/pipewire/"), Core),
    rule(Audio, ConfigPath, Contains("/wireplumber/"), Core),
    rule(Audio, ConfigPath, Contains("/asound"), Core),
    // ---- Boot -----------------------------------------------------------
    rule(Boot, Package, Prefix("grub"), Core),
    rule(Boot, Package, Prefix("systemd-boot"), Core),
    rule(Boot, Package, Prefix("limine"), Core),
    rule(Boot, Package, Prefix("refind"), Core),
    rule(Boot, Package, Prefix("mkinitcpio"), Core),
    rule(Boot, Package, Prefix("dracut"), Core),
    rule(Boot, Package, Prefix("booster"), Core),
    rule(Boot, Package, Prefix("efibootmgr"), Supporting),
    rule(Boot, Package, Prefix("plymouth"), Supporting),
    rule(Boot, ConfigPath, Contains("/mkinitcpio"), Core),
    rule(Boot, ConfigPath, Contains("/default/grub"), Core),
    rule(Boot, ConfigPath, Contains("/dracut"), Core),
    rule(Boot, ConfigPath, Contains("/cmdline"), Core),
    // ---- Storage --------------------------------------------------------
    rule(Storage, Package, Prefix("btrfs-progs"), Core),
    rule(Storage, Package, Prefix("e2fsprogs"), Core),
    rule(Storage, Package, Prefix("xfsprogs"), Core),
    rule(Storage, Package, Prefix("f2fs-tools"), Core),
    rule(Storage, Package, Prefix("dosfstools"), Supporting),
    rule(Storage, Package, Prefix("lvm2"), Core),
    rule(Storage, Package, Prefix("cryptsetup"), Core),
    rule(Storage, Package, Prefix("nvme-cli"), Supporting),
    rule(Storage, Package, Prefix("smartmontools"), Supporting),
    rule(Storage, Package, Prefix("udisks"), Supporting),
    rule(Storage, Unit, Prefix("udisks"), Supporting),
    rule(Storage, Unit, Suffix(".mount"), Supporting),
    rule(Storage, ConfigPath, Contains("/fstab"), Core),
    rule(Storage, ConfigPath, Contains("/crypttab"), Core),
    // ---- Input ----------------------------------------------------------
    rule(Input, Package, Prefix("libinput"), Core),
    rule(Input, Package, Prefix("xf86-input-"), Core),
    rule(Input, Package, Prefix("xkeyboard-config"), Supporting),
    rule(Input, Package, Prefix("ibus"), Supporting),
    rule(Input, Package, Prefix("fcitx"), Supporting),
    rule(Input, KernelModule, Prefix("hid"), Core),
    rule(Input, KernelModule, Prefix("usbhid"), Core),
    rule(Input, ConfigPath, Contains("/x11/xorg.conf.d"), Supporting),
    // ---- Printing -------------------------------------------------------
    rule(Printing, Package, Prefix("cups"), Core),
    rule(Printing, Package, Prefix("ghostscript"), Supporting),
    rule(Printing, Package, Prefix("sane"), Supporting),
    rule(Printing, Unit, Prefix("cups"), Core),
    rule(Printing, ConfigPath, Contains("/cups/"), Core),
    // ---- Power ----------------------------------------------------------
    rule(Power, Package, Prefix("tlp"), Core),
    rule(Power, Package, Prefix("power-profiles-daemon"), Core),
    rule(Power, Package, Prefix("upower"), Core),
    rule(Power, Package, Prefix("thermald"), Supporting),
    rule(Power, Unit, Prefix("tlp"), Core),
    rule(Power, Unit, Prefix("upower"), Core),
    rule(Power, Unit, Prefix("power-profiles-daemon"), Core),
    rule(Power, Unit, Prefix("systemd-logind"), Supporting),
    // ---- Virtualization -------------------------------------------------
    rule(Virtualization, Package, Prefix("qemu"), Core),
    rule(Virtualization, Package, Prefix("libvirt"), Core),
    rule(Virtualization, Package, Prefix("virtualbox"), Core),
    rule(Virtualization, Package, Prefix("docker"), Core),
    rule(Virtualization, Package, Prefix("podman"), Core),
    rule(Virtualization, Package, Prefix("containerd"), Supporting),
    rule(Virtualization, Unit, Prefix("docker"), Core),
    rule(Virtualization, Unit, Prefix("libvirtd"), Core),
    rule(Virtualization, Unit, Prefix("containerd"), Supporting),
    rule(Virtualization, KernelModule, Prefix("kvm"), Core),
    rule(Virtualization, KernelModule, Prefix("vbox"), Core),
    // ---- Security -------------------------------------------------------
    rule(Security, Package, Prefix("polkit"), Core),
    rule(Security, Package, Prefix("sudo"), Core),
    rule(Security, Package, Prefix("apparmor"), Core),
    rule(Security, Package, Prefix("firewalld"), Core),
    rule(Security, Package, Prefix("ufw"), Core),
    rule(Security, Package, Prefix("nftables"), Core),
    rule(Security, Package, Prefix("iptables"), Core),
    rule(Security, Package, Prefix("openssh"), Supporting),
    rule(Security, Package, Prefix("pam"), Core),
    rule(Security, Unit, Prefix("firewalld"), Core),
    rule(Security, Unit, Prefix("ufw"), Core),
    rule(Security, Unit, Prefix("nftables"), Core),
    rule(Security, Unit, Prefix("sshd"), Supporting),
    rule(Security, ConfigPath, Contains("/pam.d/"), Core),
    rule(Security, ConfigPath, Contains("/sudoers"), Core),
    rule(Security, ConfigPath, Contains("/ssh/"), Supporting),
];

/// Packages that are broad enough to be contextually relevant to *any*
/// subsystem — the kernel, firmware and systemd. Tagged separately so they
/// never outrank a subsystem's own components.
pub fn universal_context_patterns() -> &'static [Pattern] {
    &[
        Pattern::Prefix("linux"),
        Pattern::Exact("systemd"),
        Pattern::Prefix("systemd-"),
        Pattern::Prefix("linux-firmware"),
        Pattern::Exact("glibc"),
        Pattern::Prefix("dkms"),
    ]
}

/// True when a package is a broad platform component whose change is worth
/// mentioning for any reported problem.
pub fn is_universal_context(name: &str) -> bool {
    let n = normalize_package_name(name);
    universal_context_patterns().iter().any(|p| p.matches(&n))
}

impl SubsystemTagger {
    /// Tag a package, folding in universal context so that a kernel change is
    /// surfaced for every subsystem at [`Affinity::Contextual`].
    pub fn tag_package_for_troubleshooting(&self, name: &str) -> Vec<SubsystemTag> {
        let mut tags = self.tag_package(name);
        if is_universal_context(name) {
            for s in Subsystem::ALL {
                if !tags.iter().any(|t| t.subsystem == *s) {
                    tags.push(SubsystemTag {
                        subsystem: *s,
                        affinity: Affinity::Contextual,
                    });
                }
            }
        }
        tags
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn subs(tags: &[SubsystemTag]) -> Vec<Subsystem> {
        tags.iter().map(|t| t.subsystem).collect()
    }

    #[test]
    fn one_pattern_covers_a_whole_package_family() {
        let t = SubsystemTagger::with_defaults();
        for pkg in ["mesa", "lib32-mesa", "mesa-utils", "mesa-vdpau"] {
            assert!(
                subs(&t.tag_package(pkg)).contains(&Subsystem::Graphics),
                "{pkg} should be tagged Graphics"
            );
        }
    }

    #[test]
    fn bluetooth_stack_is_core_but_kernel_is_only_contextual() {
        let t = SubsystemTagger::with_defaults();
        let bluez = t.tag_package("bluez");
        let bt = bluez
            .iter()
            .find(|x| x.subsystem == Subsystem::Bluetooth)
            .unwrap();
        assert_eq!(bt.affinity, Affinity::Core);

        let kernel = t.tag_package_for_troubleshooting("linux-cachyos");
        let bt_ctx = kernel
            .iter()
            .find(|x| x.subsystem == Subsystem::Bluetooth)
            .unwrap();
        assert_eq!(
            bt_ctx.affinity,
            Affinity::Contextual,
            "the kernel is relevant to bluetooth but must not outrank bluez"
        );
        assert!(bt.affinity > bt_ctx.affinity);
    }

    #[test]
    fn multilib_and_build_suffixes_do_not_hide_a_package() {
        let t = SubsystemTagger::with_defaults();
        assert!(subs(&t.tag_package("lib32-nvidia-utils")).contains(&Subsystem::Graphics));
        assert!(subs(&t.tag_package("mesa-git")).contains(&Subsystem::Graphics));
        assert_eq!(normalize_package_name("lib32-mesa"), "mesa");
        assert_eq!(normalize_package_name("paru-bin"), "paru");
    }

    #[test]
    fn a_package_can_belong_to_several_subsystems() {
        let t = SubsystemTagger::with_defaults();
        // linux-firmware is universal context; it should reach wifi and graphics alike.
        let tags = t.tag_package_for_troubleshooting("linux-firmware");
        let s = subs(&tags);
        assert!(s.contains(&Subsystem::Graphics));
        assert!(s.contains(&Subsystem::Networking));
        assert!(s.contains(&Subsystem::Bluetooth));
    }

    #[test]
    fn units_config_paths_and_modules_are_tagged_too() {
        let t = SubsystemTagger::with_defaults();
        assert!(subs(&t.tag_unit("bluetooth.service")).contains(&Subsystem::Bluetooth));
        assert!(
            subs(&t.tag_unit("NetworkManager.service".to_ascii_lowercase().as_str()))
                .contains(&Subsystem::Networking)
        );
        assert!(subs(&t.tag_config_path("/etc/modprobe.d/bluetooth.conf"))
            .contains(&Subsystem::Bluetooth));
        assert!(subs(&t.tag_kernel_module("amdgpu")).contains(&Subsystem::Graphics));
        assert!(subs(&t.tag_kernel_module("btusb")).contains(&Subsystem::Bluetooth));
    }

    #[test]
    fn strongest_affinity_wins_and_no_subsystem_is_duplicated() {
        let mut t = SubsystemTagger::empty();
        t.add_rule(rule(
            Subsystem::Audio,
            Target::Package,
            Pattern::Prefix("pipe"),
            Affinity::Supporting,
        ));
        t.add_rule(rule(
            Subsystem::Audio,
            Target::Package,
            Pattern::Exact("pipewire"),
            Affinity::Core,
        ));
        let tags = t.tag_package("pipewire");
        assert_eq!(tags.len(), 1);
        assert_eq!(tags[0].affinity, Affinity::Core);
    }

    #[test]
    fn unrelated_packages_get_no_tags() {
        let t = SubsystemTagger::with_defaults();
        assert!(t.tag_package("firefox").is_empty());
        assert!(t.tag_package("imagemagick").is_empty());
    }

    #[test]
    fn rules_are_extensible_without_touching_the_table() {
        let mut t = SubsystemTagger::with_defaults();
        assert!(t.tag_package("my-custom-driver").is_empty());
        t.add_rule(rule(
            Subsystem::Graphics,
            Target::Package,
            Pattern::Contains("custom-driver"),
            Affinity::Core,
        ));
        assert!(subs(&t.tag_package("my-custom-driver")).contains(&Subsystem::Graphics));
    }
}
