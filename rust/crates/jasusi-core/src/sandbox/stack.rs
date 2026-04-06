use crate::sandbox::{
    ebpf::{self, EbpfStatus},
    landlock::{self, LandlockStatus},
    profiles::{AllowedPaths, SandboxProfile},
    seccomp::{self, SeccompStatus},
};

#[derive(Debug)]
pub struct SandboxStack {
    pub profile: SandboxProfile,
    pub project_root: std::path::PathBuf,
}

#[derive(Debug)]
pub struct SandboxResult {
    pub landlock: LandlockStatus,
    pub seccomp: SeccompStatus,
    pub ebpf: EbpfStatus,
}

#[derive(Debug)]
pub struct SandboxError(pub String);

impl std::fmt::Display for SandboxError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Sandbox error: {}", self.0)
    }
}

impl std::error::Error for SandboxError {}

impl SandboxStack {
    pub fn new(profile: SandboxProfile, project_root: std::path::PathBuf) -> Self {
        Self {
            profile,
            project_root,
        }
    }

    pub fn apply(&self) -> Result<SandboxResult, SandboxError> {
        let paths = AllowedPaths::for_profile(self.profile, &self.project_root);

        // Layer 1: Landlock — RULE 4: Unavailable is always Ok, never Err
        let landlock_status = landlock::apply(&paths).unwrap_or(LandlockStatus::Unavailable);

        // Layer 2: seccomp — RULE 3: KillProcess only, Strict profile only
        let seccomp_status = if self.profile == SandboxProfile::Strict {
            seccomp::apply_strict().map_err(|e| SandboxError(e.to_string()))?
        } else {
            SeccompStatus::Unavailable
        };

        // Layer 3: eBPF — Phase 4 stub
        let ebpf_status = ebpf::attach_tc_filter();

        tracing::info!(
            profile = ?self.profile,
            landlock = ?landlock_status,
            seccomp = ?seccomp_status,
            ebpf = ?ebpf_status,
            "SandboxStack applied"
        );

        Ok(SandboxResult {
            landlock: landlock_status,
            seccomp: seccomp_status,
            ebpf: ebpf_status,
        })
    }

    pub fn is_active(&self) -> bool {
        self.profile != SandboxProfile::None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_stack(profile: SandboxProfile) -> SandboxStack {
        SandboxStack::new(profile, std::env::temp_dir())
    }

    #[test]
    fn test_none_profile_is_inactive() {
        assert!(!make_stack(SandboxProfile::None).is_active());
    }

    #[test]
    fn test_strict_profile_is_active() {
        assert!(make_stack(SandboxProfile::Strict).is_active());
    }

    #[test]
    fn test_apply_never_errors_on_windows() {
        let result = make_stack(SandboxProfile::Strict).apply();
        assert!(
            result.is_ok(),
            "SandboxStack::apply() must not Err on Windows"
        );
    }

    #[test]
    fn test_file_profile_seccomp_unavailable() {
        let result = make_stack(SandboxProfile::File).apply().unwrap();
        assert_eq!(result.seccomp, SeccompStatus::Unavailable);
    }

    #[test]
    fn test_search_profile_seccomp_and_ebpf_unavailable() {
        let result = make_stack(SandboxProfile::Search).apply().unwrap();
        assert_eq!(result.seccomp, SeccompStatus::Unavailable);
        assert_eq!(result.ebpf, EbpfStatus::Unavailable);
    }

    #[test]
    fn test_strict_non_linux_all_unavailable() {
        #[cfg(not(target_os = "linux"))]
        {
            let result = make_stack(SandboxProfile::Strict).apply().unwrap();
            assert_eq!(result.landlock, LandlockStatus::Unavailable);
            assert_eq!(result.seccomp, SeccompStatus::Unavailable);
            assert_eq!(result.ebpf, EbpfStatus::Unavailable);
        }
    }

    #[test]
    fn test_allowed_paths_strict_read_only() {
        let root = std::path::Path::new("/tmp/test-project");
        let paths = AllowedPaths::for_profile(SandboxProfile::Strict, root);
        assert!(paths.read_only.contains(&root.to_path_buf()));
        assert!(!paths.read_write.contains(&root.to_path_buf()));
    }

    #[test]
    fn test_allowed_paths_file_read_write() {
        let root = std::path::Path::new("/tmp/test-project");
        let paths = AllowedPaths::for_profile(SandboxProfile::File, root);
        assert!(paths.read_write.contains(&root.to_path_buf()));
    }
}
