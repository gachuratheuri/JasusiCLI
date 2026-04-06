#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LandlockStatus {
    Applied { abi_version: u32 },
    Unavailable,
}

#[derive(Debug)]
pub struct LandlockError(pub String);

impl std::fmt::Display for LandlockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Landlock error: {}", self.0)
    }
}

impl std::error::Error for LandlockError {}

pub fn apply(
    allowed: &crate::sandbox::profiles::AllowedPaths,
) -> Result<LandlockStatus, LandlockError> {
    #[cfg(target_os = "linux")]
    {
        return apply_linux(allowed);
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = allowed;
        Ok(LandlockStatus::Unavailable)
    }
}

#[cfg(target_os = "linux")]
fn apply_linux(
    allowed: &crate::sandbox::profiles::AllowedPaths,
) -> Result<LandlockStatus, LandlockError> {
    use landlock::{
        Access, AccessFs, PathBeneath, PathFd, Ruleset, RulesetAttr, RulesetCreatedAttr, ABI,
    };

    let abi = ABI::V3;
    let mut ruleset = Ruleset::default()
        .handle_access(AccessFs::from_all(abi))
        .map_err(|e| LandlockError(e.to_string()))?
        .create()
        .map_err(|e| LandlockError(e.to_string()))?;

    for path in &allowed.read_only {
        if !path.exists() {
            continue;
        }
        let fd = PathFd::new(path).map_err(|e| LandlockError(e.to_string()))?;
        ruleset = ruleset
            .add_rule(PathBeneath::new(fd, AccessFs::from_read(abi)))
            .map_err(|e| LandlockError(e.to_string()))?;
    }

    for path in &allowed.read_write {
        if !path.exists() {
            continue;
        }
        let fd = PathFd::new(path).map_err(|e| LandlockError(e.to_string()))?;
        ruleset = ruleset
            .add_rule(PathBeneath::new(fd, AccessFs::from_all(abi)))
            .map_err(|e| LandlockError(e.to_string()))?;
    }

    ruleset
        .restrict_self()
        .map_err(|e| LandlockError(e.to_string()))?;
    Ok(LandlockStatus::Applied {
        abi_version: abi as u32,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sandbox::profiles::{AllowedPaths, SandboxProfile};

    #[test]
    fn test_landlock_unavailable_on_non_linux() {
        #[cfg(not(target_os = "linux"))]
        {
            let paths =
                AllowedPaths::for_profile(SandboxProfile::Strict, std::path::Path::new("/tmp"));
            assert_eq!(apply(&paths).unwrap(), LandlockStatus::Unavailable);
        }
        #[cfg(target_os = "linux")]
        {
            let paths =
                AllowedPaths::for_profile(SandboxProfile::None, std::path::Path::new("/tmp"));
            let _ = apply(&paths);
        }
    }
}
