#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SeccompStatus {
    Applied { syscall_count: usize },
    Unavailable,
}

#[derive(Debug)]
pub struct SeccompError(pub String);

impl std::fmt::Display for SeccompError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Seccomp error: {}", self.0)
    }
}

impl std::error::Error for SeccompError {}

pub fn apply_strict() -> Result<SeccompStatus, SeccompError> {
    #[cfg(target_os = "linux")]
    {
        return apply_linux_strict();
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(SeccompStatus::Unavailable)
    }
}

#[cfg(target_os = "linux")]
fn apply_linux_strict() -> Result<SeccompStatus, SeccompError> {
    use seccompiler::{BpfProgram, SeccompAction, SeccompFilter, SeccompRule};
    use std::collections::BTreeMap;

    let allowed: Vec<i64> = vec![
        libc::SYS_read,
        libc::SYS_write,
        libc::SYS_openat,
        libc::SYS_close,
        libc::SYS_fstat,
        libc::SYS_mmap,
        libc::SYS_mprotect,
        libc::SYS_munmap,
        libc::SYS_brk,
        libc::SYS_rt_sigaction,
        libc::SYS_rt_sigprocmask,
        libc::SYS_exit,
        libc::SYS_exit_group,
        libc::SYS_futex,
        libc::SYS_clone,
        libc::SYS_wait4,
        libc::SYS_execve,
        libc::SYS_getcwd,
        libc::SYS_getdents64,
        libc::SYS_lseek,
        libc::SYS_dup,
        libc::SYS_dup2,
        libc::SYS_pipe,
        libc::SYS_pipe2,
    ];
    let syscall_count = allowed.len();
    let mut rules: BTreeMap<i64, Vec<SeccompRule>> = BTreeMap::new();
    for syscall in &allowed {
        rules.insert(*syscall, vec![]);
    }

    let filter = SeccompFilter::new(
        rules,
        SeccompAction::KillProcess,
        SeccompAction::Allow,
        std::env::consts::ARCH
            .try_into()
            .map_err(|e| SeccompError(format!("{e:?}")))?,
    )
    .map_err(|e| SeccompError(e.to_string()))?;

    let program: BpfProgram = filter
        .try_into()
        .map_err(|e: seccompiler::Error| SeccompError(e.to_string()))?;
    seccompiler::apply_filter(&program).map_err(|e| SeccompError(e.to_string()))?;
    Ok(SeccompStatus::Applied { syscall_count })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_seccomp_unavailable_on_non_linux() {
        #[cfg(not(target_os = "linux"))]
        {
            assert_eq!(apply_strict().unwrap(), SeccompStatus::Unavailable);
        }
    }
}
