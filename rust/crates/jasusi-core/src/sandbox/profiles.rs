use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SandboxProfile {
    Strict,
    File,
    Search,
    Web,
    None,
}

impl From<i32> for SandboxProfile {
    fn from(v: i32) -> Self {
        match v {
            0 => Self::Strict,
            1 => Self::File,
            2 => Self::Search,
            3 => Self::Web,
            _ => Self::None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AllowedPaths {
    pub read_only: Vec<std::path::PathBuf>,
    pub read_write: Vec<std::path::PathBuf>,
}

impl AllowedPaths {
    pub fn for_profile(profile: SandboxProfile, project_root: &std::path::Path) -> Self {
        match profile {
            SandboxProfile::Strict => Self {
                read_only: vec![project_root.to_path_buf()],
                read_write: vec![std::env::temp_dir()],
            },
            SandboxProfile::File => Self {
                read_only: vec![project_root.to_path_buf()],
                read_write: vec![project_root.to_path_buf(), std::env::temp_dir()],
            },
            SandboxProfile::Search => Self {
                read_only: vec![project_root.to_path_buf()],
                read_write: vec![],
            },
            SandboxProfile::Web => Self {
                read_only: vec![],
                read_write: vec![std::env::temp_dir()],
            },
            SandboxProfile::None => Self {
                read_only: vec![],
                read_write: vec![],
            },
        }
    }
}
